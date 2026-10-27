from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from . import db

"""AI 模拟盘·互动层（v2 转化钩子）：冠军竞猜 + 关注 agent + 调仓时间线。

设计要点：
- 数据与赛马同库（.ai_fund.sqlite3），结算直接读 aif_nav，不碰 ai_fund 引擎代码。
- 结算规则：交易日 D 收盘（最后一个 aif_nav 点）净值第一名 = 当日冠军；
  前两名净值差 < 0.05% 视为「无胜负」（hit=NULL，不影响连胜）——防 v2 首日全员贴地时误判。
- 竞猜窗口：交易日 00:00–15:00（北京）；15:00 后与休市日拒绝下注（防「看牌下注」）。
- 每人每日一注（可改，末次为准）；用户画像零 PII：只存 user_id(账号 sub)。
"""

_BJ = timezone(timedelta(hours=8))
_SETTLE_MIN_INTERVAL = 60.0
_SETTLE_STATE = {"ts": 0.0}
_MAX_FOLLOWS = 3
_NO_CONTEST_MARGIN_PCT = 0.05   # 前两名净值差小于此(百分点)→无胜负


def _db_path() -> str:
    return str(db.data_path(".ai_fund.sqlite3", "DEEPFOCUS_AIFUND_DB_PATH"))


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS aif_guess (
        day TEXT NOT NULL, user_id TEXT NOT NULL, fund_id TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT,
        settled INTEGER NOT NULL DEFAULT 0, hit INTEGER,
        PRIMARY KEY (day, user_id));
    CREATE TABLE IF NOT EXISTS aif_guess_stats (
        user_id TEXT PRIMARY KEY, streak INTEGER NOT NULL DEFAULT 0,
        best INTEGER NOT NULL DEFAULT 0, hits INTEGER NOT NULL DEFAULT 0,
        total INTEGER NOT NULL DEFAULT 0, updated_at TEXT);
    CREATE TABLE IF NOT EXISTS aif_follow (
        user_id TEXT NOT NULL, fund_id TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY (user_id, fund_id));
    """)


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def bj_now() -> datetime:
    return datetime.now(_BJ)


def bj_today(now: Optional[datetime] = None) -> str:
    return (now or bj_now()).strftime("%Y-%m-%d")


def is_trading_day(now: Optional[datetime] = None, holidays: Optional[set] = None) -> bool:
    from .ai_fund import CN_MARKET_HOLIDAYS
    d = now or bj_now()
    if d.weekday() >= 5:
        return False
    return d.strftime("%Y-%m-%d") not in (holidays if holidays is not None else CN_MARKET_HOLIDAYS)


def guess_window_open(now: Optional[datetime] = None, holidays: Optional[set] = None) -> tuple[bool, str]:
    """竞猜窗口：交易日 00:00–15:00。返回 (是否可猜, 原因文案)。"""
    d = now or bj_now()
    if not is_trading_day(d, holidays):
        return False, "休市日不竞猜，交易日见"
    hm = d.hour * 60 + d.minute
    if hm >= 900:
        return False, "今日已收盘，竞猜通道关闭（明日 09:30 前可再下注）"
    return True, ""


# ---------------- 竞猜 ----------------

def set_guess(conn: sqlite3.Connection, user_id: str, fund_id: str,
              now: Optional[datetime] = None, holidays: Optional[set] = None) -> dict[str, Any]:
    from .ai_fund import ROSTER_BY_ID
    ensure_db(conn)
    if fund_id not in ROSTER_BY_ID:
        raise ValueError("没有这个选手")
    ok, why = guess_window_open(now, holidays)
    if not ok:
        raise ValueError(why)
    day = bj_today(now)
    row = conn.execute("SELECT settled FROM aif_guess WHERE day=? AND user_id=?", (day, user_id)).fetchone()
    if row and row["settled"]:
        raise ValueError("该日竞猜已结算")
    ts = _now_utc()
    conn.execute(
        "INSERT INTO aif_guess (day,user_id,fund_id,created_at,updated_at) VALUES (?,?,?,?,?) "
        "ON CONFLICT(day,user_id) DO UPDATE SET fund_id=excluded.fund_id, updated_at=excluded.updated_at, settled=0, hit=NULL",
        (day, user_id, fund_id, ts, ts))
    conn.commit()
    return {"ok": True, "day": day, "fund_id": fund_id, "name": ROSTER_BY_ID[fund_id].name}


def _day_close_navs(conn: sqlite3.Connection, day: str, fund_ids: list[str]) -> dict[str, float]:
    """交易日 D 各账户收盘净值：取 BJ 日期==D 的最后一个 aif_nav 点。"""
    lo = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    hi = (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=2)).strftime("%Y-%m-%d")
    out: dict[str, float] = {}
    for fid in fund_ids:
        rows = conn.execute("SELECT ts,nav FROM aif_nav WHERE fund_id=? AND ts>=? AND ts<? ORDER BY ts ASC",
                            (fid, lo, hi)).fetchall()
        last = None
        for r in rows:
            try:
                bj = datetime.fromisoformat(str(r["ts"]).replace("Z", "+00:00")).astimezone(_BJ).strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                continue
            if bj == day:
                last = float(r["nav"])
        if last is not None:
            out[fid] = last
    return out


def settle_guesses(conn: sqlite3.Connection, now: Optional[datetime] = None,
                   force: bool = False) -> list[dict[str, Any]]:
    """懒结算所有已到期(日<今日)的未结算竞猜。幂等；60s 节流（force 绕过，供测试）。
    返回本次结算的 [{day, champion, no_contest, users}]。绝不抛出。"""
    from .ai_fund import ROSTER, ROSTER_BY_ID
    ensure_db(conn)
    t0 = time.monotonic()
    if not force and (t0 - _SETTLE_STATE["ts"]) < _SETTLE_MIN_INTERVAL:
        return []
    _SETTLE_STATE["ts"] = t0
    today = bj_today(now)
    fund_ids = [c.fund_id for c in ROSTER]
    results: list[dict[str, Any]] = []
    try:
        days = [r["day"] for r in conn.execute(
            "SELECT DISTINCT day FROM aif_guess WHERE settled=0 AND day<? ORDER BY day ASC", (today,)).fetchall()]
        for day in days:
            navs = _day_close_navs(conn, day, fund_ids)
            if len(navs) < 2:
                continue  # 当日尚无净值快照（异常日），留给下次
            ordered = sorted(navs.items(), key=lambda kv: kv[1], reverse=True)
            champion, top = ordered[0]
            margin_pct = (top - ordered[1][1]) * 100.0
            no_contest = margin_pct < _NO_CONTEST_MARGIN_PCT
            rows = conn.execute("SELECT user_id,fund_id FROM aif_guess WHERE day=? AND settled=0", (day,)).fetchall()
            users = 0
            for r in rows:
                hit = None if no_contest else (1 if r["fund_id"] == champion else 0)
                conn.execute("UPDATE aif_guess SET settled=1, hit=? WHERE day=? AND user_id=?",
                             (hit, day, r["user_id"]))
                if hit is not None:
                    uid = r["user_id"]
                    st = conn.execute("SELECT streak,best,hits,total FROM aif_guess_stats WHERE user_id=?", (uid,)).fetchone()
                    streak = int(st["streak"]) if st else 0
                    best = int(st["best"]) if st else 0
                    hits = int(st["hits"]) if st else 0
                    total = int(st["total"]) if st else 0
                    streak = streak + 1 if hit == 1 else 0
                    best = max(best, streak)
                    conn.execute(
                        "INSERT INTO aif_guess_stats (user_id,streak,best,hits,total,updated_at) VALUES (?,?,?,?,?,?) "
                        "ON CONFLICT(user_id) DO UPDATE SET streak=?, best=?, hits=?, total=?, updated_at=?",
                        (uid, streak, best, hits + (1 if hit else 0), total + 1, _now_utc(),
                         streak, best, hits + (1 if hit else 0), total + 1, _now_utc()))
                users += 1
            conn.execute("UPDATE aif_guess SET settled=1 WHERE day=? AND settled=0", (day,))
            conn.commit()
            results.append({"day": day, "champion": ROSTER_BY_ID[champion].name if champion in ROSTER_BY_ID else champion,
                            "no_contest": no_contest, "users": users})
    except Exception:  # noqa: BLE001 — 结算失败不拦主流程，下轮再试
        conn.rollback()
    return results


def guess_summary(conn: sqlite3.Connection, user_id: str) -> dict[str, Any]:
    from .ai_fund import ROSTER_BY_ID
    ensure_db(conn)
    day = bj_today()
    pick = conn.execute("SELECT fund_id FROM aif_guess WHERE day=? AND user_id=?", (day, user_id)).fetchone()
    distribution = {r["fund_id"]: r["n"] for r in conn.execute(
        "SELECT fund_id, COUNT(*) AS n FROM aif_guess WHERE day=? GROUP BY fund_id", (day,)).fetchall()}
    guessers = sum(distribution.values())
    recent = []
    for r in conn.execute(
            "SELECT day,fund_id,hit FROM aif_guess WHERE user_id=? AND settled=1 ORDER BY day DESC LIMIT 8",
            (user_id,)).fetchall():
        cfg = ROSTER_BY_ID.get(r["fund_id"])
        recent.append({"day": r["day"], "fund_id": r["fund_id"],
                       "name": cfg.name if cfg else r["fund_id"],
                       "emoji": cfg.emoji if cfg else "",
                       "hit": None if r["hit"] is None else bool(r["hit"])})
    st = conn.execute("SELECT streak,best,hits,total FROM aif_guess_stats WHERE user_id=?", (user_id,)).fetchone()
    window_open, why = guess_window_open()
    return {
        "day": day,
        "window_open": window_open, "window_note": why,
        "my_pick": pick["fund_id"] if pick else None,
        "my_pick_name": (ROSTER_BY_ID[pick["fund_id"]].name if pick and pick["fund_id"] in ROSTER_BY_ID else None),
        "distribution": distribution, "guessers": guessers,
        "recent": recent,
        "streak": int(st["streak"]) if st else 0,
        "best": int(st["best"]) if st else 0,
        "hits": int(st["hits"]) if st else 0,
        "total": int(st["total"]) if st else 0,
    }


# ---------------- 关注 ----------------

def list_follows(conn: sqlite3.Connection, user_id: str) -> list[dict[str, Any]]:
    from .ai_fund import ROSTER_BY_ID
    ensure_db(conn)
    rows = conn.execute("SELECT fund_id,created_at FROM aif_follow WHERE user_id=? ORDER BY created_at ASC",
                        (user_id,)).fetchall()
    out = []
    for r in rows:
        cfg = ROSTER_BY_ID.get(r["fund_id"])
        if cfg:
            out.append({"fund_id": cfg.fund_id, "name": cfg.name, "emoji": cfg.emoji, "color": None})
    return out


def set_follow(conn: sqlite3.Connection, user_id: str, fund_id: str, on: bool) -> dict[str, Any]:
    from .ai_fund import ROSTER_BY_ID
    ensure_db(conn)
    if fund_id not in ROSTER_BY_ID:
        raise ValueError("没有这个选手")
    if on:
        n = conn.execute("SELECT COUNT(*) AS c FROM aif_follow WHERE user_id=?", (user_id,)).fetchone()["c"]
        if n >= _MAX_FOLLOWS:
            raise ValueError(f"最多关注 {_MAX_FOLLOWS} 位")
        conn.execute("INSERT OR IGNORE INTO aif_follow (user_id,fund_id,created_at) VALUES (?,?,?)",
                     (user_id, fund_id, _now_utc()))
    else:
        conn.execute("DELETE FROM aif_follow WHERE user_id=? AND fund_id=?", (user_id, fund_id))
    conn.commit()
    return {"ok": True, "follows": [f["fund_id"] for f in list_follows(conn, user_id)]}


def follow_feed(conn: sqlite3.Connection, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
    from .ai_fund import ROSTER_BY_ID
    ensure_db(conn)
    limit = max(1, min(int(limit or 20), 50))
    rows = conn.execute(
        "SELECT t.fund_id,t.side,t.name,t.symbol,t.pnl_pct,t.ts FROM aif_trade t "
        "JOIN aif_follow f ON f.fund_id=t.fund_id WHERE f.user_id=? ORDER BY t.ts DESC LIMIT ?",
        (user_id, limit)).fetchall()
    out = []
    for r in rows:
        cfg = ROSTER_BY_ID.get(r["fund_id"])
        out.append({"fund_id": r["fund_id"], "agent": cfg.name if cfg else r["fund_id"],
                    "emoji": cfg.emoji if cfg else "", "side": r["side"], "name": r["name"] or r["symbol"],
                    "symbol": r["symbol"], "pnl_pct": r["pnl_pct"], "ts": r["ts"]})
    return out
