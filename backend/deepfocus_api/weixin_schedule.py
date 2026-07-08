from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from .shared_utils import utc_now_iso

"""
微信定时推送计划表（群发 + 个性化）。

两类计划：
- kind='broadcast'：运营/管理员配置，到点用 mgr.quasi_push() 广播给所有近期活跃(token 热)的绑定；
  content_type ∈ text（静态文案）/ news（最新 N 条快讯摘要）。
- kind='personal'：会员在微信里一句话订阅，到点只发给他自己的绑定；
  content_type ∈ watchlist_quote（我的自选行情快照）/ watchlist_news（我的自选相关快讯）。

时区一律北京时间(hour/minute)；day_mode ∈ daily / trading（仅交易日）。一日一次去重：last_fired_date。
渲染与发送在 main.py 的 run_wechat_scheduled_push 里（那里才有行情/快讯/manager 访问），
本模块只管「存 + 判到点 + 去重」，保持纯净可单测。见 [[wechat-push-channel]] 的准推送边界。
"""

DB_PATH = Path(
    os.getenv(
        "DEEPFOCUS_WEIXIN_SCHEDULE_DB_PATH",
        str(Path(__file__).resolve().parents[1] / ".weixin_schedule.sqlite3"),
    )
)

_KINDS = ("broadcast", "personal")
_BROADCAST_TYPES = ("text", "news")
_PERSONAL_TYPES = ("watchlist_quote", "watchlist_news")
_DAY_MODES = ("daily", "trading")
# 到点后的补发窗口（分钟）：命中即发；服务器若在计划时刻后短时重启，落在窗口内仍补发一次，
# 超窗口则当日跳过（不半夜补发陈旧内容）。
_CATCHUP_MINUTES = int(os.getenv("DEEPFOCUS_WEIXIN_SCHED_CATCHUP_MIN", "15") or 15)
# 每用户个性化计划上限（单条即可满足「每天X点推我的自选」，防滥建）。
MAX_PERSONAL_PER_USER = 1


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # 该库被扫描任务(to_thread 工作线程)与入站口令处理(事件循环)并发写；给个 busy_timeout，
    # 让偶发写锁竞争等待而非立刻抛 "database is locked"（默认 busy_timeout=0）。
    conn.execute("PRAGMA busy_timeout=3000")
    return conn


def init_schedule_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS weixin_schedule (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                owner_user_id TEXT,
                title TEXT,
                content_type TEXT NOT NULL,
                content TEXT,
                hour INTEGER NOT NULL,
                minute INTEGER NOT NULL,
                day_mode TEXT NOT NULL DEFAULT 'daily',
                enabled INTEGER NOT NULL DEFAULT 1,
                last_fired_date TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_weixin_schedule_owner ON weixin_schedule(owner_user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_weixin_schedule_enabled ON weixin_schedule(enabled)")
        conn.commit()


def _row(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    d = dict(row)
    d["enabled"] = bool(d.get("enabled", 1))
    d["hour"] = int(d.get("hour") or 0)
    d["minute"] = int(d.get("minute") or 0)
    return d


def _norm_time(hour: int, minute: int) -> tuple[int, int]:
    h = max(0, min(23, int(hour)))
    m = max(0, min(59, int(minute)))
    return h, m


def create_schedule(
    kind: str,
    content_type: str,
    hour: int,
    minute: int,
    *,
    owner_user_id: Optional[str] = None,
    title: str = "",
    content: str = "",
    day_mode: str = "daily",
    enabled: bool = True,
) -> Dict[str, Any]:
    """建一条定时计划。kind/content_type/day_mode 非法值会被兜到安全默认。"""
    init_schedule_db()
    kind = kind if kind in _KINDS else "broadcast"
    valid_types = _PERSONAL_TYPES if kind == "personal" else _BROADCAST_TYPES
    if content_type not in valid_types:
        content_type = valid_types[0]
    day_mode = day_mode if day_mode in _DAY_MODES else "daily"
    h, m = _norm_time(hour, minute)
    now = utc_now_iso()
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO weixin_schedule
                (kind, owner_user_id, title, content_type, content, hour, minute, day_mode, enabled,
                 last_fired_date, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (kind, owner_user_id, title, content_type, content, h, m, day_mode,
             1 if enabled else 0, None, now, now),
        )
        conn.commit()
        sid = int(cur.lastrowid)
    return get_schedule(sid) or {}


def get_schedule(schedule_id: int) -> Optional[Dict[str, Any]]:
    init_schedule_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM weixin_schedule WHERE id=?", (schedule_id,)).fetchone()
    return _row(row)


def list_schedules(
    kind: Optional[str] = None,
    owner_user_id: Optional[str] = None,
    enabled_only: bool = False,
) -> List[Dict[str, Any]]:
    init_schedule_db()
    q = "SELECT * FROM weixin_schedule WHERE 1=1"
    args: list[Any] = []
    if kind:
        q += " AND kind=?"
        args.append(kind)
    if owner_user_id is not None:
        q += " AND owner_user_id=?"
        args.append(owner_user_id)
    if enabled_only:
        q += " AND enabled=1"
    q += " ORDER BY hour, minute, id"
    with _connect() as conn:
        rows = conn.execute(q, args).fetchall()
    return [r for r in (_row(x) for x in rows) if r]


def update_schedule(schedule_id: int, **fields: Any) -> Optional[Dict[str, Any]]:
    """改指定字段（白名单列）。hour/minute 会归一；改内容/时间即视为一次编辑。"""
    allowed = {"title", "content", "content_type", "hour", "minute", "day_mode", "enabled"}
    sets: list[str] = []
    args: list[Any] = []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k in ("hour", "minute"):
            v = _norm_time(int(v) if k == "hour" else 0, int(v) if k == "minute" else 0)[0 if k == "hour" else 1]
        if k == "enabled":
            v = 1 if v else 0
        if k == "day_mode" and v not in _DAY_MODES:
            v = "daily"
        sets.append(f"{k}=?")
        args.append(v)
    if not sets:
        return get_schedule(schedule_id)
    sets.append("updated_at=?")
    args.append(utc_now_iso())
    args.append(schedule_id)
    init_schedule_db()
    with _connect() as conn:
        conn.execute(f"UPDATE weixin_schedule SET {', '.join(sets)} WHERE id=?", args)
        conn.commit()
    return get_schedule(schedule_id)


def delete_schedule(schedule_id: int) -> bool:
    init_schedule_db()
    with _connect() as conn:
        cur = conn.execute("DELETE FROM weixin_schedule WHERE id=?", (schedule_id,))
        conn.commit()
    return cur.rowcount > 0


def mark_fired(schedule_id: int, date_str: str) -> None:
    """记一次「今日已触发」，同日再命中窗口即跳过（去重）。"""
    init_schedule_db()
    with _connect() as conn:
        conn.execute(
            "UPDATE weixin_schedule SET last_fired_date=?, updated_at=? WHERE id=?",
            (date_str, utc_now_iso(), schedule_id),
        )
        conn.commit()


def due_schedules(now_hour: int, now_minute: int, today_str: str, catchup_minutes: int = _CATCHUP_MINUTES) -> List[Dict[str, Any]]:
    """当前到点该发的 enabled 计划：目标时刻(今日)已到、落在补发窗口内、且今日未发过。

    纯函数（不查交易日/不发送）——交易日过滤与真正发送在调用方。窗口逻辑用「距目标 0~catchup 分钟」，
    刚好命中或短时重启补发；超窗口当日自然跳过。"""
    now_abs = int(now_hour) * 60 + int(now_minute)
    out: List[Dict[str, Any]] = []
    for s in list_schedules(enabled_only=True):
        if s.get("last_fired_date") == today_str:
            continue
        delta = now_abs - (int(s["hour"]) * 60 + int(s["minute"]))
        if 0 <= delta <= catchup_minutes:
            out.append(s)
    return out


def set_personal(
    owner_user_id: str,
    content_type: str,
    hour: int,
    minute: int,
    day_mode: str = "trading",
) -> Optional[Dict[str, Any]]:
    """会员一句话订阅：每用户维持「单条」个性化计划——已有则更新(换内容/时间/重新启用)，无则新建。
    保证不会因反复订阅堆出多条重复推送。"""
    if not owner_user_id:
        return None
    if content_type not in _PERSONAL_TYPES:
        content_type = _PERSONAL_TYPES[0]
    existing = list_schedules(kind="personal", owner_user_id=owner_user_id)
    if existing:
        row = existing[0]
        # 多余的历史个性化计划清理掉，只留一条（收敛到 MAX_PERSONAL_PER_USER=1）
        for extra in existing[1:]:
            delete_schedule(int(extra["id"]))
        return update_schedule(
            int(row["id"]),
            content_type=content_type,
            hour=hour,
            minute=minute,
            day_mode=day_mode,
            enabled=True,
        )
    title = "自选行情" if content_type == "watchlist_quote" else "自选快讯"
    return create_schedule(
        "personal",
        content_type,
        hour,
        minute,
        owner_user_id=owner_user_id,
        title=title,
        day_mode=day_mode,
    )


def disable_personal(owner_user_id: str) -> int:
    """关掉某用户所有个性化计划（enabled=0，保留记录便于「随时恢复」）。返回受影响条数。"""
    if not owner_user_id:
        return 0
    rows = list_schedules(kind="personal", owner_user_id=owner_user_id, enabled_only=True)
    for r in rows:
        update_schedule(int(r["id"]), enabled=False)
    return len(rows)


def list_personal(owner_user_id: str) -> List[Dict[str, Any]]:
    return list_schedules(kind="personal", owner_user_id=owner_user_id)
