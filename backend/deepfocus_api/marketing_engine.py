from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from typing import Any, Optional

from .auth import list_users, users_expiring_within
from .metrics_store import DB_PATH as METRICS_DB_PATH
from .recall_subscriptions import (
    DB_PATH as RECALL_DB_PATH,
    _app_base_url,
    _email_smtp_config,
    _smtp_sendmail,
)
from .shared_utils import utc_now_iso

"""
自动营销引擎：现有孤立召回零件（t1_recall / expiry_reminder / weixin_schedule）之上补一层「编排」。

做的事 = 通用分群 → 确定性文案（复用当日复盘/权益，v1 零 LLM）→ 邮件触达（复用现成 SMTP 链路）
→ 统一频控/抑制 → 点击 + 回访归因 → 运营看板可见。

分群（本引擎主动触达，均需留邮箱）：
- d7_slipping：注册 4~14 天、回访过至少一天、但最近 72h 没再来（习惯断挡，最陡流失斜坡）
- dormant   ：注册 >14 天、历史活跃 ≥3 天、最近 14 天没来（沉睡老用户唤回）
- power_free：非会员、近 7 天活跃 ≥4 天（转化种子，最该请他升级）
只读分群（本引擎不发，由 t1_recall / expiry_reminder 承接，看板只展示规模防重复触达）：
- new_no_return（T+1 未回访，t1 窗口）/ expiring（7 天内到期，expiry 窗口）

铁律：
- 主闸 DEEPFOCUS_MARKETING_ENABLED 默认 '0' → 关时任何调用强制 dry_run（诚实空转，代码可先上生产验证无副作用）。
- 频控：suppression 名单 / 每用户 N 天冷却 / 同 campaign M 天不重发 / 每 campaign 日上限 / 全引擎日总量上限。
- SMTP 未配置：整轮 skipped 且【绝不落 sent】（学 t1_recall 血泪教训，防毒化频控）。
- 出站：品牌只用 DeepFocus、结尾免责 + 退订 footer、过 compliance.neutralize_text、严禁荐股/买卖措辞。
- 表落在 recall 库（与 t1/expiry 同库），连接带 busy_timeout（学 weixin_schedule），并发写不炸。
"""

BJ_TZ = timezone(timedelta(hours=8))

# 种子 campaign：key → (title, segment)。enabled 默认 0（部署与启用解耦，验证后再灰度开）。
_SEED_CAMPAIGNS = [
    ("d7_slipping", "习惯断挡召回", "d7_slipping"),
    ("dormant", "沉睡用户唤回", "dormant"),
    ("power_free", "高活跃免费用户转化", "power_free"),
]

_EMAIL_FOOTER = (
    "—————————————————————\n"
    "DeepFocus · 金融终端 daocaijing.com\n"
    "本邮件由系统自动发送。内容仅供研究参考，不构成投资建议；据此操作风险自负。\n"
    "如不希望再收到此类邮件，回复「退订」即可。"
)


# --------------------------------------------------------------------------- #
# 配置 / 连接
# --------------------------------------------------------------------------- #
def _master_enabled() -> bool:
    return (os.getenv("DEEPFOCUS_MARKETING_ENABLED", "0") or "0").strip().lower() in ("1", "true", "yes", "on")


def _user_cooldown_days() -> int:
    try:
        return max(0, int(os.getenv("DEEPFOCUS_MKT_USER_COOLDOWN_DAYS", "7")))
    except ValueError:
        return 7


def _daily_total_cap() -> int:
    try:
        return int(os.getenv("DEEPFOCUS_MKT_DAILY_TOTAL", "40"))
    except ValueError:
        return 40


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(RECALL_DB_PATH)
    conn.row_factory = sqlite3.Row
    # 与 t1/expiry/settle_push_log 同库并发写：给 busy_timeout（学 weixin_schedule.py），
    # 让偶发写锁竞争等待而非立刻抛 "database is locked"。
    conn.execute("PRAGMA busy_timeout=3000")
    return conn


def init_marketing_db() -> None:
    """建表 + 播种 campaign。幂等；任何失败不抛（lifespan 里调用，不能阻断启动）。"""
    try:
        RECALL_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS marketing_campaigns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT UNIQUE NOT NULL,
                    title TEXT NOT NULL,
                    segment TEXT NOT NULL,
                    channel TEXT NOT NULL DEFAULT 'email',
                    enabled INTEGER NOT NULL DEFAULT 0,
                    daily_cap INTEGER NOT NULL DEFAULT 20,
                    cooldown_days INTEGER NOT NULL DEFAULT 30,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS marketing_touches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_key TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    username TEXT,
                    address TEXT,
                    channel TEXT NOT NULL DEFAULT 'email',
                    status TEXT NOT NULL,
                    detail TEXT,
                    day TEXT NOT NULL,
                    sent_at TEXT NOT NULL,
                    clicked_at TEXT,
                    returned_at TEXT
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mkt_touch_user ON marketing_touches(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mkt_touch_camp ON marketing_touches(campaign_key, day)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS marketing_suppression (
                    user_id TEXT PRIMARY KEY,
                    reason TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            now = utc_now_iso()
            for key, title, seg in _SEED_CAMPAIGNS:
                conn.execute(
                    "INSERT OR IGNORE INTO marketing_campaigns (key, title, segment, channel, enabled,"
                    " daily_cap, cooldown_days, created_at, updated_at) VALUES (?,?,?,?,0,20,30,?,?)",
                    (key, title, seg, "email", now, now),
                )
            conn.commit()
    except sqlite3.Error:
        pass


def list_campaigns() -> list[dict[str, Any]]:
    init_marketing_db()
    try:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT key, title, segment, channel, enabled, daily_cap, cooldown_days FROM marketing_campaigns"
                " ORDER BY id"
            ).fetchall()
        return [{**dict(r), "enabled": bool(r["enabled"])} for r in rows]
    except sqlite3.Error:
        return []


def campaign_set(key: str, *, enabled: Optional[bool] = None,
                 daily_cap: Optional[int] = None, cooldown_days: Optional[int] = None) -> Optional[dict[str, Any]]:
    """改一条 campaign 的启停 / 日上限 / 冷却。白名单列，None 表示不改。"""
    init_marketing_db()
    sets, args = [], []
    if enabled is not None:
        sets.append("enabled=?"); args.append(1 if enabled else 0)
    if daily_cap is not None:
        sets.append("daily_cap=?"); args.append(max(0, int(daily_cap)))
    if cooldown_days is not None:
        sets.append("cooldown_days=?"); args.append(max(0, int(cooldown_days)))
    if not sets:
        return _get_campaign(key)
    sets.append("updated_at=?"); args.append(utc_now_iso()); args.append(key)
    try:
        with _connect() as conn:
            conn.execute(f"UPDATE marketing_campaigns SET {', '.join(sets)} WHERE key=?", args)
            conn.commit()
    except sqlite3.Error:
        return None
    return _get_campaign(key)


def _get_campaign(key: str) -> Optional[dict[str, Any]]:
    try:
        with _connect() as conn:
            r = conn.execute(
                "SELECT key, title, segment, channel, enabled, daily_cap, cooldown_days FROM marketing_campaigns"
                " WHERE key=?", (key,)
            ).fetchone()
        return {**dict(r), "enabled": bool(r["enabled"])} if r else None
    except sqlite3.Error:
        return None


def add_suppression(user_id: str, reason: str = "unsubscribe") -> None:
    if not user_id:
        return
    init_marketing_db()
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO marketing_suppression (user_id, reason, created_at) VALUES (?,?,?)",
                (user_id, reason[:80], utc_now_iso()),
            )
            conn.commit()
    except sqlite3.Error:
        pass


# --------------------------------------------------------------------------- #
# 分群：行为画像（一次扫描 activity_log 建 user_id → last_seen / 活跃天数）
# --------------------------------------------------------------------------- #
def _as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _activity_profile() -> dict[str, dict[str, Any]]:
    """user_id(去掉 u: 前缀) → {last_seen(ISO), days_total, days_7d}。

    ⚠️ activity_log 滚动只留最近约 10 万条（metrics_store），dormant(>14 天不活跃)在样本被裁剪时会
    低估活跃天数——v1 接受此界，长周期归因交给 growth_cohort_daily（本引擎不重建）。失败返回空 dict。"""
    out: dict[str, dict[str, Any]] = {}
    try:
        week_cut = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        with sqlite3.connect(METRICS_DB_PATH, timeout=8) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT actor_id,"
                " MAX(ts) AS last_seen,"
                " COUNT(DISTINCT substr(datetime(ts,'+8 hours'),1,10)) AS days_total,"
                " COUNT(DISTINCT CASE WHEN ts >= ? THEN substr(datetime(ts,'+8 hours'),1,10) END) AS days_7d"
                " FROM activity_log WHERE actor_kind='user' GROUP BY actor_id",
                (week_cut,),
            ).fetchall()
            for r in rows:
                aid = r["actor_id"] or ""
                uid = aid[2:] if aid.startswith("u:") else aid
                out[uid] = {
                    "last_seen": r["last_seen"] or "",
                    "days_total": int(r["days_total"] or 0),
                    "days_7d": int(r["days_7d"] or 0),
                }
    except sqlite3.Error:
        pass
    return out


def _hours_since(iso: str, now: datetime) -> Optional[float]:
    if not iso:
        return None
    try:
        return (now - _as_utc(datetime.fromisoformat(iso))).total_seconds() / 3600.0
    except ValueError:
        return None


def _member_tier(u: Any) -> str:
    m = getattr(u, "membership", None) or {}
    return str(m.get("tier") or "trial")


def compute_segments() -> dict[str, list[dict[str, Any]]]:
    """算三个主动分群的成员名单（含 user_id/username/email）。任何失败该分群返回空、不拖垮其余。"""
    segs: dict[str, list[dict[str, Any]]] = {"d7_slipping": [], "dormant": [], "power_free": []}
    try:
        now = datetime.now(timezone.utc)
        prof = _activity_profile()
        for u in list_users():
            email = (getattr(u, "email", "") or "").strip()
            if not email:
                continue
            uid = u.id
            created = _as_utc(u.created_at)
            reg_days = (now - created).total_seconds() / 86400.0
            p = prof.get(uid) or {}
            idle_h = _hours_since(p.get("last_seen", ""), now)
            days_total = int(p.get("days_total", 0))
            days_7d = int(p.get("days_7d", 0))
            row = {"user_id": uid, "username": u.username, "email": email}
            tier = _member_tier(u)
            # d7_slipping：注册 4~14 天、回访过(活跃≥2 天)、最近 72h 没来
            if 4 <= reg_days <= 14 and days_total >= 2 and idle_h is not None and idle_h >= 72:
                segs["d7_slipping"].append(row)
            # dormant：注册 >14 天、历史活跃≥3 天、最近 14 天没来（或无近期记录）
            elif reg_days > 14 and days_total >= 3 and (idle_h is None or idle_h >= 24 * 14):
                segs["dormant"].append(row)
            # power_free：非会员、近 7 天活跃≥4 天（与前两档互斥：还在高频用的人不进流失/沉睡）
            if tier == "trial" and days_7d >= 4:
                segs["power_free"].append(row)
    except Exception:  # noqa: BLE001
        pass
    return segs


def segment_sizes() -> dict[str, int]:
    """看板用：三主动分群规模 + 两只读分群规模（后者标注由 t1/expiry 承接，防重复触达）。"""
    out: dict[str, int] = {}
    try:
        segs = compute_segments()
        for k, v in segs.items():
            out[k] = len(v)
    except Exception:  # noqa: BLE001
        out.update({"d7_slipping": 0, "dormant": 0, "power_free": 0})
    # 只读分群（本引擎不发）
    try:
        out["expiring"] = len(users_expiring_within(7 * 24))
    except Exception:  # noqa: BLE001
        out["expiring"] = 0
    return out


# --------------------------------------------------------------------------- #
# 频控
# --------------------------------------------------------------------------- #
def _suppressed_ids() -> set[str]:
    try:
        with _connect() as conn:
            return {r["user_id"] for r in conn.execute("SELECT user_id FROM marketing_suppression").fetchall()}
    except sqlite3.Error:
        return set()


def _recent_sent_index(cooldown_days: int) -> dict[str, str]:
    """user_id → 最近一次 sent 的 sent_at（用于跨 campaign 全局冷却）。窗口取 max(用户冷却, 各 campaign 冷却)。"""
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=max(1, cooldown_days))).isoformat()
        with _connect() as conn:
            rows = conn.execute(
                "SELECT user_id, MAX(sent_at) AS last FROM marketing_touches"
                " WHERE status='sent' AND sent_at >= ? GROUP BY user_id",
                (since,),
            ).fetchall()
        return {r["user_id"]: r["last"] for r in rows}
    except sqlite3.Error:
        return {}


def _campaign_last_sent(campaign_key: str) -> dict[str, str]:
    """指定 campaign 下 user_id → 最近 sent_at（同 campaign 冷却判定）。"""
    try:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT user_id, MAX(sent_at) AS last FROM marketing_touches"
                " WHERE status='sent' AND campaign_key=? GROUP BY user_id",
                (campaign_key,),
            ).fetchall()
        return {r["user_id"]: r["last"] for r in rows}
    except sqlite3.Error:
        return {}


def _sent_today(campaign_key: str, today: str) -> int:
    try:
        with _connect() as conn:
            r = conn.execute(
                "SELECT COUNT(*) AS n FROM marketing_touches WHERE campaign_key=? AND day=? AND status='sent'",
                (campaign_key, today),
            ).fetchone()
        return int(r["n"] or 0)
    except sqlite3.Error:
        return 0


def _sent_today_total(today: str) -> int:
    try:
        with _connect() as conn:
            r = conn.execute(
                "SELECT COUNT(*) AS n FROM marketing_touches WHERE day=? AND status='sent'", (today,)
            ).fetchone()
        return int(r["n"] or 0)
    except sqlite3.Error:
        return 0


def _too_recent(iso: str, now: datetime, days: int) -> bool:
    if not iso:
        return False
    try:
        return (now - _as_utc(datetime.fromisoformat(iso))).total_seconds() < days * 86400
    except ValueError:
        return False


# --------------------------------------------------------------------------- #
# 文案（v1 确定性，复用内容钩子；不含任何荐股/买卖措辞）
# --------------------------------------------------------------------------- #
def _neutralize(text: str) -> str:
    try:
        from . import compliance
        return compliance.neutralize_text(text)
    except Exception:  # noqa: BLE001
        return text


def _review_hook() -> tuple[str, str]:
    """(one_liner, edges_line)：复用当日 A股复盘做内容钩子，取不到返回空串。"""
    try:
        from . import ashare_review
        review = ashare_review.latest_review()
        if not review:
            return "", ""
        nar = review.get("narrative") or {}
        one = (nar.get("one_liner") or "").strip()
        edges = review.get("our_edge") or []
        edge_line = (
            f"其中有 {len(edges)} 条本站提前于盘面发布的资讯与当日走势形成对照。" if edges else ""
        )
        return one, edge_line
    except Exception:  # noqa: BLE001
        return "", ""


def build_email(segment: str, username: str) -> tuple[str, str]:
    """按分群生成 (subject, body)。主链接由调用方替换 {LINK} 占位为带追踪的点击 URL。"""
    one, edge_line = _review_hook()
    if segment == "d7_slipping":
        subject = "【DeepFocus】这几天的 A股复盘，你还没看"
        lead = f"最近的 A股收盘复盘：{one}" if one else "每个交易日 15:35，DeepFocus 自动生成当日 A股收盘复盘。"
        body = (
            f"{username}，您好：\n\n"
            f"{lead}\n{edge_line}\n\n"
            "今天回来看一眼复盘，顺手就完成签到——连续签到到里程碑还能领会员：{LINK}\n\n"
            f"{_EMAIL_FOOTER}"
        )
    elif segment == "dormant":
        subject = "【DeepFocus】最近两周，A股主线换了"
        lead = f"最新一期复盘的一句话：{one}" if one else "大盘与板块主线这两周有明显变化。"
        body = (
            f"{username}，您好：\n\n"
            f"有一阵没见你了。{lead}\n{edge_line}\n\n"
            "回来看看这两周错过了什么——大盘 / 板块 / 个股主线，每个交易日都在更新：{LINK}\n\n"
            f"{_EMAIL_FOOTER}"
        )
    else:  # power_free
        subject = "【DeepFocus】你已经是这里的常客了"
        body = (
            f"{username}，您好：\n\n"
            "这段时间你几乎每个交易日都在用 DeepFocus——谢谢。\n"
            "升级尊享会员后，这些每天都会用到的能力不再受限：\n"
            "· AI 投研问答不限次（自动调行情/估值 + 检索我们的快讯·研报·复盘）\n"
            "· 资讯与研报原文、多模态 AI 解读\n"
            "· 每个交易日 15:35 的 A股收盘复盘全量内容\n\n"
            "看看会员权益：{LINK}\n\n"
            f"{_EMAIL_FOOTER}"
        )
    return subject, _neutralize(body)


# --------------------------------------------------------------------------- #
# 触达 + 归因
# --------------------------------------------------------------------------- #
def _click_url(touch_id: int, campaign_key: str) -> str:
    return f"{_app_base_url()}/api/marketing/click/{touch_id}"


def _insert_touch(campaign_key: str, u: dict[str, Any], today: str, status: str = "queued") -> Optional[int]:
    try:
        with _connect() as conn:
            cur = conn.execute(
                "INSERT INTO marketing_touches (campaign_key, user_id, username, address, channel,"
                " status, detail, day, sent_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (campaign_key, u["user_id"], u.get("username", ""), u.get("email", ""), "email",
                 status, "", today, utc_now_iso()),
            )
            conn.commit()
            return int(cur.lastrowid)
    except sqlite3.Error:
        return None


def _update_touch(touch_id: int, status: str, detail: str) -> None:
    try:
        with _connect() as conn:
            conn.execute(
                "UPDATE marketing_touches SET status=?, detail=?, sent_at=? WHERE id=?",
                (status, detail[:200], utc_now_iso(), touch_id),
            )
            conn.commit()
    except sqlite3.Error:
        pass


def mark_clicked(touch_id: int) -> Optional[str]:
    """点击追踪回填；返回落地 URL（带 utm）。无效 id 也返回首页 URL（不 404 吓用户）。"""
    base = _app_base_url()
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT campaign_key, clicked_at FROM marketing_touches WHERE id=?", (touch_id,)
            ).fetchone()
            if row is None:
                return base
            if not row["clicked_at"]:
                conn.execute(
                    "UPDATE marketing_touches SET clicked_at=? WHERE id=?", (utc_now_iso(), touch_id)
                )
                conn.commit()
            return f"{base}/?utm=mkt_{row['campaign_key']}"
    except sqlite3.Error:
        return base


def attribute_returns(window_hours: int = 72) -> int:
    """把 sent 但未 returned 的触达，与 activity_log 里发信后的活跃对齐回填 returned_at。返回回填条数。"""
    filled = 0
    try:
        now = datetime.now(timezone.utc)
        with _connect() as conn:
            touches = conn.execute(
                "SELECT id, user_id, sent_at FROM marketing_touches"
                " WHERE status='sent' AND returned_at IS NULL"
            ).fetchall()
        if not touches:
            return 0
        # 逐条查 metrics 库该用户发信后的首个活跃（触达量不大，逐条足够；失败静默）
        with sqlite3.connect(METRICS_DB_PATH, timeout=8) as mconn:
            mconn.row_factory = sqlite3.Row
            for t in touches:
                try:
                    sent = _as_utc(datetime.fromisoformat(t["sent_at"]))
                except (ValueError, TypeError):
                    continue
                if (now - sent).total_seconds() < 600:
                    continue  # 刚发不到 10 分钟，给用户点开的时间
                upper = (sent + timedelta(hours=window_hours)).isoformat()
                r = mconn.execute(
                    "SELECT MIN(ts) AS ts FROM activity_log WHERE actor_id=? AND ts > ? AND ts <= ?",
                    (f"u:{t['user_id']}", sent.isoformat(), upper),
                ).fetchone()
                if r and r["ts"]:
                    with _connect() as wconn:
                        wconn.execute(
                            "UPDATE marketing_touches SET returned_at=? WHERE id=?", (r["ts"], t["id"])
                        )
                        wconn.commit()
                    filled += 1
    except sqlite3.Error:
        pass
    return filled


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def run_marketing_once(dry_run: bool = False, limit: Optional[int] = None) -> dict[str, Any]:
    """执行一轮营销触达。dry_run=True 只算「会发给谁」不发不落库。返回结构化 summary，绝不抛出。

    主闸 DEEPFOCUS_MARKETING_ENABLED 关闭时强制 dry_run（诚实空转）。"""
    init_marketing_db()
    forced_dry = not _master_enabled()
    effective_dry = dry_run or forced_dry
    summary: dict[str, Any] = {
        "dry_run": effective_dry, "master_enabled": not forced_dry,
        "sent": 0, "skipped": 0, "errors": 0, "preview": [], "detail": [],
    }

    try:
        segs = compute_segments()
    except Exception as exc:  # noqa: BLE001
        summary["detail"].append(f"分群失败：{exc}"[:200])
        return summary

    campaigns = [c for c in list_campaigns() if c["enabled"] and c["channel"] == "email"]
    if not campaigns:
        summary["detail"].append("无启用中的 email campaign（默认全关，看板里开启后灰度）")
        return summary

    config = _email_smtp_config()
    smtp_ok = config is not None
    if not smtp_ok and not effective_dry:
        # SMTP 未配置：整轮 skipped 且不落库（学 t1_recall，防毒化频控/去重）
        summary["skipped"] = sum(len(segs.get(c["segment"], [])) for c in campaigns)
        summary["detail"].append("SMTP 未配置，本轮整体跳过（不落库，配置后自动续发）")
        return summary

    now = datetime.now(timezone.utc)
    today = datetime.now(BJ_TZ).strftime("%Y-%m-%d")
    user_cd = _user_cooldown_days()
    suppressed = _suppressed_ids()
    max_cd = max([user_cd] + [int(c["cooldown_days"]) for c in campaigns])
    recent_any = _recent_sent_index(max_cd)
    total_cap = _daily_total_cap()
    total_today = _sent_today_total(today)
    touched_this_run: set[str] = set()

    for camp in campaigns:
        seg_members = segs.get(camp["segment"], [])
        camp_last = _campaign_last_sent(camp["key"])
        camp_sent_today = _sent_today(camp["key"], today)
        camp_cap = int(camp["daily_cap"])
        for u in seg_members:
            uid = u["user_id"]
            # —— 频控闸门 ——
            if uid in suppressed:
                summary["skipped"] += 1; continue
            if uid in touched_this_run:
                summary["skipped"] += 1; continue  # 本轮已被别的 campaign 命中，一人一轮最多一条
            if _too_recent(recent_any.get(uid, ""), now, user_cd):
                summary["skipped"] += 1; continue
            if _too_recent(camp_last.get(uid, ""), now, int(camp["cooldown_days"])):
                summary["skipped"] += 1; continue
            if camp_cap > 0 and camp_sent_today >= camp_cap:
                summary["detail"].append(f"{camp['key']} 达日上限 {camp_cap}，其余留待下轮"); break
            if total_cap > 0 and total_today >= total_cap:
                summary["detail"].append(f"全引擎达日总量 {total_cap}，本轮止");
                summary["preview"] = summary["preview"][:50]
                return summary
            if limit is not None and summary["sent"] + len(summary["preview"]) >= limit:
                return summary

            subject, body_tpl = build_email(camp["segment"], u["username"])
            if effective_dry:
                summary["preview"].append({"campaign": camp["key"], "user": u["username"],
                                            "email": u["email"], "subject": subject})
                touched_this_run.add(uid)
                camp_sent_today += 1; total_today += 1
                continue

            # 真发：先落 touch 拿 id → 用 id 拼追踪链接 → 发 → 回写状态（先记后发）
            touch_id = _insert_touch(camp["key"], u, today, status="queued")
            if touch_id is None:
                summary["errors"] += 1; continue
            body = body_tpl.replace("{LINK}", _click_url(touch_id, camp["key"]))
            try:
                mime = MIMEText(body, "plain", "utf-8")
                mime["Subject"] = subject
                mime["From"] = config["sender"] or config["user"]
                mime["To"] = u["email"]
                _smtp_sendmail(config, [u["email"]], mime)
                _update_touch(touch_id, "sent", subject)
                summary["sent"] += 1
                touched_this_run.add(uid)
                camp_sent_today += 1; total_today += 1
                summary["detail"].append(f"sent[{camp['key']}] → {u['username']}")
            except Exception as exc:  # noqa: BLE001
                _update_touch(touch_id, "error", str(exc))
                summary["errors"] += 1
                summary["detail"].append(f"error[{camp['key']}] → {u['username']}: {exc}"[:200])

    return summary


def marketing_stats(days: int = 14) -> dict[str, Any]:
    """看板用：通道状态 + 分群规模 + 各 campaign 触达效果 + 最近触达。绝不抛。"""
    init_marketing_db()
    out: dict[str, Any] = {
        "master_enabled": _master_enabled(),
        "smtp_configured": _email_smtp_config() is not None,
        "weixin_channel_on": (os.getenv("DEEPFOCUS_WEIXIN_CHANNEL", "0") or "0").strip() in ("1", "true", "yes", "on"),
        "user_cooldown_days": _user_cooldown_days(),
        "daily_total_cap": _daily_total_cap(),
        "segments": {}, "campaigns": [], "recent": [],
    }
    try:
        out["segments"] = segment_sizes()
    except Exception:  # noqa: BLE001
        pass
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with _connect() as conn:
            for c in list_campaigns():
                r = conn.execute(
                    "SELECT SUM(status='sent') AS sent,"
                    " SUM(clicked_at IS NOT NULL) AS clicked,"
                    " SUM(returned_at IS NOT NULL) AS returned"
                    " FROM marketing_touches WHERE campaign_key=? AND sent_at >= ? AND status='sent'",
                    (c["key"], since),
                ).fetchone()
                sent = int((r["sent"] or 0) if r else 0)
                clicked = int((r["clicked"] or 0) if r else 0)
                returned = int((r["returned"] or 0) if r else 0)
                out["campaigns"].append({
                    **c, "sent": sent, "clicked": clicked, "returned": returned,
                    "ctr_pct": round(clicked / sent * 100, 1) if sent else None,
                    "return_pct": round(returned / sent * 100, 1) if sent else None,
                })
            out["recent"] = [
                dict(r) for r in conn.execute(
                    "SELECT campaign_key, username, status, detail, day, sent_at, clicked_at, returned_at"
                    " FROM marketing_touches ORDER BY id DESC LIMIT 20"
                ).fetchall()
            ]
    except sqlite3.Error:
        pass
    return out


def marketing_funnel(days: int = 14) -> dict[str, Any]:
    """给 growth_analytics 复用的漏斗：近 N 天 sent→clicked→returned 汇总（容错直读，缺表返回零）。"""
    out = {"sent": 0, "clicked": 0, "returned": 0, "ctr_pct": None, "return_pct": None}
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with _connect() as conn:
            r = conn.execute(
                "SELECT SUM(status='sent') AS sent, SUM(clicked_at IS NOT NULL) AS clicked,"
                " SUM(returned_at IS NOT NULL) AS returned FROM marketing_touches"
                " WHERE sent_at >= ? AND status='sent'",
                (since,),
            ).fetchone()
        if r:
            out["sent"] = int(r["sent"] or 0)
            out["clicked"] = int(r["clicked"] or 0)
            out["returned"] = int(r["returned"] or 0)
            if out["sent"]:
                out["ctr_pct"] = round(out["clicked"] / out["sent"] * 100, 1)
                out["return_pct"] = round(out["returned"] / out["sent"] * 100, 1)
    except sqlite3.Error:
        pass
    return out
