from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import time
from collections import deque
from pathlib import Path
from typing import Optional

from .shared_utils import utc_now_iso

"""
合作方 / 开发者 API 的密钥与配额层。

安全设计：
- 密钥**只存 SHA-256 摘要**（key_hash），明文仅在签发时返回一次；DB 泄露也无法直接复用密钥。
- 仅保留 key_prefix（前 12 位）供管理端识别/展示，不可反推完整密钥。
- 校验按摘要查库（高熵密钥，无时序泄露面），并校验 active + 未过期。
- 配额：按密钥的内存滑动窗口限流（单进程；多 worker 需 Redis）。
- 防暴破：对「无效密钥」按来源 IP 单独限速，防止穷举/打认证层 DoS。
- 计量流水只记 key_prefix，不落完整密钥/摘要。
"""

KEY_PREFIX = "dfk_"  # DeepFocus Key
_PREFIX_SHOW = 12     # 展示/计量用的前缀长度

TIER_RATE = {"trial": 30, "basic": 60, "pro": 300}

# 内存滑动窗口：{key_hash: deque[float]}，仅保留最近 60s
_WINDOWS: dict = {}
# 无效密钥的来源 IP 失败计数：{ip: deque[float]}，防暴破
_AUTH_FAILS: dict = {}
_AUTH_FAIL_WINDOW = 60.0
_AUTH_FAIL_MAX = int(os.getenv("DEEPFOCUS_PARTNER_AUTHFAIL_MAX", "20"))  # 每 IP 每分钟最多 20 次无效尝试


def _db_path() -> Path:
    return Path(
        os.getenv(
            "DEEPFOCUS_PARTNER_API_DB_PATH",
            str(Path(__file__).resolve().parents[1] / ".partner_api.sqlite3"),
        )
    )


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _has_column(conn: sqlite3.Connection, table: str, col: str) -> bool:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(r["name"] == col for r in rows)
    except sqlite3.Error:
        return False


def init_partner_db() -> None:
    p = _db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        # 迁移：旧版本用明文 key 作主键且无 key_hash 列；旧表只含已吊销的测试 key，安全重建。
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "partner_keys" in names and not _has_column(conn, "partner_keys", "key_hash"):
            conn.execute("DROP TABLE partner_keys")
        if "partner_usage" in names and _has_column(conn, "partner_usage", "key"):
            conn.execute("DROP TABLE partner_usage")  # 旧版存完整 key，重建为只存 prefix
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS partner_keys (
                key_hash TEXT PRIMARY KEY,
                key_prefix TEXT NOT NULL,
                name TEXT NOT NULL,
                tier TEXT NOT NULL DEFAULT 'basic',
                rate_per_min INTEGER NOT NULL DEFAULT 60,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                expires_at TEXT,
                last_used_at TEXT,
                call_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS partner_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key_prefix TEXT NOT NULL,
                path TEXT NOT NULL,
                status INTEGER NOT NULL,
                ip TEXT,
                ts TEXT NOT NULL
            )
            """
        )
        # 配额列（additive 迁移，0=不限）：max_calls 总调用上限、daily_quota 每日上限
        if not _has_column(conn, "partner_keys", "max_calls"):
            conn.execute("ALTER TABLE partner_keys ADD COLUMN max_calls INTEGER NOT NULL DEFAULT 0")
        if not _has_column(conn, "partner_keys", "daily_quota"):
            conn.execute("ALTER TABLE partner_keys ADD COLUMN daily_quota INTEGER NOT NULL DEFAULT 0")
        # 计费列（additive，人工收款对账用；price 用「分」存严禁浮点）
        for col, ddl in (
            ("price_cents", "INTEGER NOT NULL DEFAULT 0"),       # 本周期约定价(分)；0=免费/未定价
            ("billing_period", "TEXT NOT NULL DEFAULT ''"),       # monthly|yearly|oneoff|free
            ("billing_status", "TEXT NOT NULL DEFAULT 'unpaid'"), # unpaid|paid|overdue|comp(赠送)
            ("paid_at", "TEXT"),                                   # 最近人工确认到账时间
            ("billing_note", "TEXT NOT NULL DEFAULT ''"),          # 收款方式/流水号/对接人/合同号
            ("auto_renew", "INTEGER NOT NULL DEFAULT 0"),          # 1=到期前进续费告警池
        ):
            if not _has_column(conn, "partner_keys", col):
                conn.execute(f"ALTER TABLE partner_keys ADD COLUMN {col} {ddl}")
        # ⭐非有损的「每密钥·每日成功数」计数表：日配额与按周期对账的唯一真相源，**永不裁剪**
        # （partner_usage 明细表会滚动裁剪、仅供活动展示，不能作配额/计费依据）。键于 key_hash，避免 prefix 串扰。
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS partner_usage_daily (
                key_hash TEXT NOT NULL,
                day TEXT NOT NULL,
                success INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (key_hash, day)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_partner_usage_pfx_ts ON partner_usage(key_prefix, ts)")
        conn.commit()


USAGE_MAX = int(os.getenv("DEEPFOCUS_PARTNER_USAGE_MAX", "50000"))


def _norm_quota(v) -> int:
    """配额值规整为非负整数；0/空/非法 = 不限。"""
    try:
        n = int(v)
        return n if n > 0 else 0
    except (TypeError, ValueError):
        return 0


# 计费周期 → 续期/到期推算用的天数（人工对账，系统不扣费）
BILLING_PERIODS = {"monthly": 31, "yearly": 366, "oneoff": 0, "free": 0}


def generate_key(name: str, tier: str = "basic", rate_per_min: Optional[int] = None,
                 expires_in_days: Optional[int] = None, max_calls: Optional[int] = None,
                 daily_quota: Optional[int] = None, price_cents: Optional[int] = None,
                 billing_period: str = "", billing_status: str = "", billing_note: str = "",
                 auto_renew: Optional[int] = None) -> dict:
    """签发新密钥（管理员）。只此一次返回明文；库内只存摘要。
    expires_in_days=有效期天数；max_calls=总次数上限；daily_quota=每日上限（0/空=不限）。
    price_cents=本周期约定价(分)；billing_period/status/note=人工对账信息；auto_renew=是否进续费告警池。"""
    init_partner_db()
    name = (name or "").strip()[:80] or "未命名合作方"
    tier = tier if tier in TIER_RATE else "basic"
    rate = int(rate_per_min) if rate_per_min else TIER_RATE[tier]
    key = KEY_PREFIX + secrets.token_urlsafe(24)
    key_hash = _hash_key(key)
    prefix = key[:_PREFIX_SHOW]
    mc, dq = _norm_quota(max_calls), _norm_quota(daily_quota)
    price = _norm_quota(price_cents)
    period = billing_period if billing_period in BILLING_PERIODS else ("free" if price == 0 else "")
    status = billing_status if billing_status in ("unpaid", "paid", "overdue", "comp") else ("comp" if price == 0 else "unpaid")
    ar = 1 if (auto_renew if auto_renew is not None else (period in ("monthly", "yearly"))) else 0
    expires_at = None
    if expires_in_days and int(expires_in_days) > 0:
        from datetime import datetime, timedelta, timezone
        expires_at = (datetime.now(timezone.utc) + timedelta(days=int(expires_in_days))).isoformat()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO partner_keys (key_hash, key_prefix, name, tier, rate_per_min, active, created_at, expires_at,"
            " max_calls, daily_quota, price_cents, billing_period, billing_status, billing_note, auto_renew)"
            " VALUES (?,?,?,?,?,1,?,?,?,?,?,?,?,?,?)",
            (key_hash, prefix, name, tier, rate, utc_now_iso(), expires_at, mc, dq,
             price, period, status, (billing_note or "").strip()[:200], ar),
        )
        conn.commit()
    return {"key": key, "key_prefix": prefix, "name": name, "tier": tier, "rate_per_min": rate,
            "expires_at": expires_at, "max_calls": mc, "daily_quota": dq,
            "price_cents": price, "billing_period": period, "billing_status": status, "auto_renew": ar}


def verify_key(key: str) -> Optional[dict]:
    """按摘要校验密钥：存在 + active + 未过期。返回记录（不含完整密钥）或 None。"""
    if not key or not key.startswith(KEY_PREFIX):
        return None
    init_partner_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM partner_keys WHERE key_hash = ? AND active = 1", (_hash_key(key),)
        ).fetchone()
    if not row:
        return None
    exp = row["expires_at"]
    if exp:
        try:
            from datetime import datetime, timezone
            if datetime.fromisoformat(exp) <= datetime.now(timezone.utc):
                return None  # 已过期
        except ValueError:
            pass
    rec = dict(row)
    rec["_key_hash"] = rec.pop("key_hash")  # 内部用于限流/计量键，不外泄
    return rec


def register_auth_fail(ip: str) -> bool:
    """记一次无效密钥尝试。返回 True=该 IP 已超阈值（应直接 429 拒绝）。"""
    now = time.time()
    dq = _AUTH_FAILS.setdefault(ip or "?", deque())
    cutoff = now - _AUTH_FAIL_WINDOW
    while dq and dq[0] < cutoff:
        dq.popleft()
    dq.append(now)
    return len(dq) > _AUTH_FAIL_MAX


def auth_fail_blocked(ip: str) -> bool:
    """该 IP 当前是否已因无效尝试过多被临时挡住（用于在校验前先拦）。"""
    now = time.time()
    dq = _AUTH_FAILS.get(ip or "?")
    if not dq:
        return False
    cutoff = now - _AUTH_FAIL_WINDOW
    while dq and dq[0] < cutoff:
        dq.popleft()
    return len(dq) > _AUTH_FAIL_MAX


def check_rate(key_hash: str, rate_per_min: int) -> bool:
    """内存滑动窗口限流（按密钥摘要）。True=放行。"""
    now = time.time()
    win = _WINDOWS.setdefault(key_hash, deque())
    cutoff = now - 60.0
    while win and win[0] < cutoff:
        win.popleft()
    if len(win) >= max(1, int(rate_per_min)):
        return False
    win.append(now)
    return True


def log_usage(key_prefix: str, path: str, status: int, ip: str = "") -> None:
    """记一条**明细**流水（滚动裁剪，仅供活动展示）。不增计数、不作配额/计费依据。失败不影响主链路。"""
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO partner_usage (key_prefix, path, status, ip, ts) VALUES (?,?,?,?,?)",
                (key_prefix[:_PREFIX_SHOW], path[:200], int(status), ip[:64], utc_now_iso()),
            )
            conn.execute(
                "DELETE FROM partner_usage WHERE id NOT IN (SELECT id FROM partner_usage ORDER BY id DESC LIMIT ?)",
                (USAGE_MAX,),
            )
            conn.commit()
    except sqlite3.Error:
        pass


def record_success(key_hash: str, key_prefix: str, path: str, ip: str = "") -> None:
    """记一次**真实成功(200)**调用：非有损日计数 +1（配额/对账真相源）+ call_count +1 + 明细行。
    必须在 handler 成功返回后调用——5xx 不应到这里，从而不耗配额、不计费。"""
    today = utc_now_iso()[:10]
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO partner_usage_daily (key_hash, day, success) VALUES (?,?,1)"
                " ON CONFLICT(key_hash, day) DO UPDATE SET success = success + 1",
                (key_hash, today),
            )
            conn.execute(
                "UPDATE partner_keys SET call_count = call_count + 1, last_used_at = ? WHERE key_hash = ?",
                (utc_now_iso(), key_hash),
            )
            conn.commit()
    except sqlite3.Error:
        pass
    log_usage(key_prefix, path, 200, ip)


def today_count(key_hash: str) -> int:
    """该密钥今日(UTC)成功调用次数——日配额判定（读非有损计数表）。"""
    today = utc_now_iso()[:10]
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT success FROM partner_usage_daily WHERE key_hash = ? AND day = ?", (key_hash, today)
            ).fetchone()
            return int(row["success"]) if row else 0
    except sqlite3.Error:
        return 0


def month_count(key_hash: str, month: str = "") -> int:
    """该密钥某月(YYYY-MM，默认本月 UTC)成功调用次数——按周期对账用。"""
    month = month or utc_now_iso()[:7]
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(success),0) FROM partner_usage_daily WHERE key_hash = ? AND substr(day,1,7) = ?",
                (key_hash, month),
            ).fetchone()
            return int(row[0])
    except sqlite3.Error:
        return 0


def list_keys() -> list:
    """密钥列表（只含 prefix，无法反推完整密钥）。附今日成功数(today)便于看板展示日配额占比。"""
    init_partner_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT key_hash, key_prefix, name, tier, rate_per_min, active, created_at, expires_at, last_used_at,"
            " call_count, max_calls, daily_quota, price_cents, billing_period, billing_status, paid_at, billing_note, auto_renew"
            " FROM partner_keys ORDER BY created_at DESC"
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            kh = d.pop("key_hash")
            d["today"] = today_count(kh)  # 今日成功数（非有损）
            out.append(d)
    return out


def _find_hash_by_prefix(conn, prefix: str) -> Optional[str]:
    row = conn.execute("SELECT key_hash FROM partner_keys WHERE key_prefix = ?", (prefix,)).fetchone()
    return row["key_hash"] if row else None


def set_billing(prefix: str, **fields) -> bool:
    """改某密钥的计费字段（按 prefix，管理员看板用）。仅允许白名单列。"""
    init_partner_db()
    allowed = {"price_cents", "billing_period", "billing_status", "paid_at", "billing_note", "auto_renew"}
    sets, vals = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "price_cents":
            v = _norm_quota(v)
        elif k == "billing_note":
            v = (str(v) or "").strip()[:200]
        elif k == "auto_renew":
            v = 1 if v else 0
        sets.append(f"{k} = ?"); vals.append(v)
    if not sets:
        return False
    vals.append(prefix)
    with _connect() as conn:
        cur = conn.execute(f"UPDATE partner_keys SET {', '.join(sets)} WHERE key_prefix = ?", vals)
        conn.commit()
    return cur.rowcount > 0


def mark_paid(prefix: str, note: str = "") -> bool:
    """标记已收款：billing_status=paid + paid_at=now（+可写备注）。"""
    fields = {"billing_status": "paid", "paid_at": utc_now_iso()}
    if note:
        fields["billing_note"] = note
    return set_billing(prefix, **fields)


def extend_expiry(prefix: str, days: int = 0, reset_period: str = "") -> Optional[str]:
    """续期：在原 expires_at（或现在）基础上延长 days 天，**保留同一密钥**（合作方无需改配置）。
    可选 reset_period 改计费周期。返回新的 expires_at 或 None。"""
    init_partner_db()
    from datetime import datetime, timedelta, timezone
    days = int(days or 0)
    if reset_period and reset_period in BILLING_PERIODS and not days:
        days = BILLING_PERIODS[reset_period]
    if days <= 0:
        return None
    with _connect() as conn:
        row = conn.execute("SELECT expires_at FROM partner_keys WHERE key_prefix = ?", (prefix,)).fetchone()
        if not row:
            return None
        now = datetime.now(timezone.utc)
        base = now
        if row["expires_at"]:
            try:
                cur = datetime.fromisoformat(row["expires_at"])
                base = cur if cur > now else now  # 未过期顺延，已过期从现在起
            except ValueError:
                base = now
        new_exp = (base + timedelta(days=days)).isoformat()
        sets = "expires_at = ?, billing_status = 'paid', paid_at = ?"
        vals = [new_exp, utc_now_iso()]
        if reset_period and reset_period in BILLING_PERIODS:
            sets += ", billing_period = ?"; vals.append(reset_period)
        vals.append(prefix)
        conn.execute(f"UPDATE partner_keys SET {sets} WHERE key_prefix = ?", vals)
        conn.commit()
    return new_exp


def billing_summary() -> dict:
    """账面收入汇总（人工对账，不涉扣费）：本月已收 / 年度已收 / 待收款。金额单位分。"""
    init_partner_db()
    out = {"paid_month_cents": 0, "paid_year_cents": 0, "unpaid_cents": 0, "paid_keys": 0, "unpaid_keys": 0}
    ym, yr = utc_now_iso()[:7], utc_now_iso()[:4]
    try:
        with _connect() as conn:
            # 已收按 paid_at 落期聚合（避免上月签发本月到账归错月）
            out["paid_month_cents"] = int(conn.execute(
                "SELECT COALESCE(SUM(price_cents),0) FROM partner_keys WHERE billing_status='paid' AND substr(paid_at,1,7)=?", (ym,)
            ).fetchone()[0])
            out["paid_year_cents"] = int(conn.execute(
                "SELECT COALESCE(SUM(price_cents),0) FROM partner_keys WHERE billing_status='paid' AND substr(paid_at,1,4)=?", (yr,)
            ).fetchone()[0])
            out["unpaid_cents"] = int(conn.execute(
                "SELECT COALESCE(SUM(price_cents),0) FROM partner_keys WHERE billing_status='unpaid' AND active=1"
            ).fetchone()[0])
            out["paid_keys"] = int(conn.execute("SELECT COUNT(*) FROM partner_keys WHERE billing_status='paid'").fetchone()[0])
            out["unpaid_keys"] = int(conn.execute("SELECT COUNT(*) FROM partner_keys WHERE billing_status='unpaid' AND active=1").fetchone()[0])
    except sqlite3.Error:
        pass
    return out


def compute_alerts() -> dict:
    """续费/对账告警（单一真相源，供看板与邮件复用）。四类：近配额 / 近到期 / 已到期未续 / 待收款超期。"""
    init_partner_db()
    from datetime import datetime, timezone
    near_pct = int(os.getenv("DEEPFOCUS_PARTNER_NEAR_QUOTA_PCT", "80"))
    warn_days = int(os.getenv("DEEPFOCUS_PARTNER_EXPIRY_WARN_DAYS", "7"))
    now = datetime.now(timezone.utc)
    res = {"near_quota": [], "near_expiry": [], "expired": [], "unpaid_overdue": []}
    for k in list_keys():
        name, pfx = k.get("name"), k.get("key_prefix")
        active, status = k.get("active"), (k.get("billing_status") or "")
        # 近配额：免费/comp 不算（联调钥不刷屏）
        if active and status != "comp":
            mc, dq, cc, td = k.get("max_calls") or 0, k.get("daily_quota") or 0, k.get("call_count") or 0, k.get("today") or 0
            pct = 0
            if mc > 0:
                pct = max(pct, round(cc * 100 / mc))
            if dq > 0:
                pct = max(pct, round(td * 100 / dq))
            if pct >= near_pct:
                res["near_quota"].append({"name": name, "key_prefix": pfx, "pct": pct,
                                          "kind": ("总次数" if mc > 0 and cc * 100 / mc >= near_pct else "每日")})
        exp = k.get("expires_at")
        if exp and status != "comp" and k.get("auto_renew"):
            try:
                ed = datetime.fromisoformat(exp)
                days_left = (ed - now).days
                if ed <= now:
                    if status in ("paid", "unpaid"):
                        res["expired"].append({"name": name, "key_prefix": pfx, "expires_at": exp[:10]})
                elif days_left <= warn_days:
                    res["near_expiry"].append({"name": name, "key_prefix": pfx, "days_left": days_left, "expires_at": exp[:10],
                                               "price_cents": k.get("price_cents") or 0})
            except ValueError:
                pass
        # 待收款超期：unpaid 且签发 >7 天
        if active and status == "unpaid":
            try:
                cd = datetime.fromisoformat(k.get("created_at"))
                if (now - cd).days > 7:
                    res["unpaid_overdue"].append({"name": name, "key_prefix": pfx, "days": (now - cd).days,
                                                  "price_cents": k.get("price_cents") or 0})
            except (ValueError, TypeError):
                pass
    res["counts"] = {kk: len(vv) for kk, vv in res.items() if isinstance(vv, list)}
    return res


def revoke_key(key_or_prefix: str) -> bool:
    """吊销密钥：支持传完整密钥（按摘要）或 key_prefix（管理端列表里看到的）。"""
    init_partner_db()
    s = (key_or_prefix or "").strip()
    with _connect() as conn:
        if s.startswith(KEY_PREFIX) and len(s) > _PREFIX_SHOW:
            cur = conn.execute("UPDATE partner_keys SET active = 0 WHERE key_hash = ?", (_hash_key(s),))
        else:
            cur = conn.execute("UPDATE partner_keys SET active = 0 WHERE key_prefix = ?", (s,))
        conn.commit()
    return cur.rowcount > 0


def usage_stats(key_prefix: str = "") -> dict:
    init_partner_db()
    out: dict = {"total": 0, "today": 0, "by_key": []}
    today = utc_now_iso()[:10]
    try:
        with _connect() as conn:
            where = "WHERE key_prefix = ?" if key_prefix else ""
            params = (key_prefix,) if key_prefix else ()
            out["total"] = int(conn.execute(f"SELECT COUNT(*) FROM partner_usage {where}", params).fetchone()[0])
            out["today"] = int(conn.execute(
                f"SELECT COUNT(*) FROM partner_usage {'WHERE key_prefix = ? AND' if key_prefix else 'WHERE'} substr(ts,1,10) = ?",
                (params + (today,)) if key_prefix else (today,),
            ).fetchone()[0])
            out["by_key"] = [dict(r) for r in conn.execute(
                "SELECT key_prefix, name, tier, call_count, last_used_at, active FROM partner_keys ORDER BY call_count DESC LIMIT 50"
            ).fetchall()]
    except sqlite3.Error:
        pass
    return out
