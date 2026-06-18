from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .auth import grant_membership
from .shared_utils import utc_now_iso

"""
连续看复盘签到：把「打开当日复盘」本身当作签到动作（零额外摩擦），
连续天数到里程碑自动赠送会员天数——把被动内容变成主动回访习惯。

- 一个用户一个北京自然日最多记一次（day 为 BJ 日期串，复合主键幂等）。
- 连续天数 = 从今天往前数不断档的天数；里程碑奖励每档每人只发一次（checkin_reward 去重）。
- 奖励走 grant_membership(source="checkin")：发真实尊享天数、自动顺延，但来源标 checkin，
  绝不冒充 paid（不计入邀请付费奖励）。失败绝不影响签到主链路。
"""

BJ_TZ = timezone(timedelta(hours=8))

# 连续天数 → 赠送会员天数。⭐ 留存第一痛点是「第 2 天不回来」(D2 留存仅 ~15%)，
# 原先首个奖励要连续 7 天才拿得到——而连续 7 天本身就是我们想引导的行为，奖励对正在
# 流失的新用户毫无作用。故加一个 (2 天 → 1 天会员) 的即时低门槛钩子：第 2 天一回来就发，
# 给「明天再来」一个具体理由。发奖循环每次至多发一档、取最高未达成档，故 D2 当天 streak=2
# 命中 (2,1)、D3 命中 (3,0) 徽章，互不冲突。成本封顶：每人一生仅 1 天，且 source=checkin 不计 paid。
# 后续力度仍克制：30/100 天的用户是付费候选，奖励"够甜但不替代付费"（平衡 DAU 与付费）。
MILESTONES: list[tuple[int, int]] = [
    (2, 1),     # ⭐连续 2 天：1 天尊享——D1→D2 即时钩子，专治次日流失
    (3, 0),     # 连续 3 天：达成徽章，培养习惯
    (7, 3),     # 连续 7 天：3 天尊享
    (14, 7),    # 连续 14 天：7 天尊享
    (30, 15),   # 连续 30 天：半月尊享
    (100, 31),  # 连续 100 天：月卡
]


def _db_path() -> Path:
    return Path(
        os.getenv(
            "DEEPFOCUS_CHECKIN_DB_PATH",
            str(Path(__file__).resolve().parents[1] / ".checkin.sqlite3"),
        )
    )


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_checkin_db() -> None:
    p = _db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checkin_log (
                user_id TEXT NOT NULL,
                day TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (user_id, day)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checkin_reward (
                user_id TEXT NOT NULL,
                milestone INTEGER NOT NULL,
                reward_days INTEGER NOT NULL,
                granted_at TEXT NOT NULL,
                PRIMARY KEY (user_id, milestone)
            )
            """
        )
        conn.commit()


def _bj_today() -> str:
    return datetime.now(BJ_TZ).strftime("%Y-%m-%d")


def _days_of(user_id: str) -> list[str]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT day FROM checkin_log WHERE user_id = ? ORDER BY day", (user_id,)
        ).fetchall()
    return [r["day"] for r in rows]


def _streak_ending_today(days: set, today: str) -> int:
    """从今天往前数连续天数（今天必须在集合里，否则为 0）。"""
    if today not in days:
        return 0
    cur = datetime.strptime(today, "%Y-%m-%d").date()
    n = 0
    while cur.strftime("%Y-%m-%d") in days:
        n += 1
        cur = cur - timedelta(days=1)
    return n


def _longest_streak(days: list[str]) -> int:
    """历史最长连续天数。"""
    if not days:
        return 0
    dates = sorted({datetime.strptime(d, "%Y-%m-%d").date() for d in days})
    best = run = 1
    for i in range(1, len(dates)):
        if (dates[i] - dates[i - 1]).days == 1:
            run += 1
        else:
            run = 1
        best = max(best, run)
    return best


def _granted_milestones(conn: sqlite3.Connection, user_id: str) -> set:
    rows = conn.execute(
        "SELECT milestone FROM checkin_reward WHERE user_id = ?", (user_id,)
    ).fetchall()
    return {int(r["milestone"]) for r in rows}


def record_checkin(user_id: str, username: str) -> dict:
    """记录一次今日签到（幂等）。返回连续/累计/最长 + 本次新达成的里程碑奖励（若有）。

    任何奖励发放失败都不影响签到本身。"""
    init_checkin_db()
    uid = (user_id or "").strip()
    if not uid:
        return {"ok": False, "reason": "empty"}
    today = _bj_today()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO checkin_log (user_id, day, created_at) VALUES (?, ?, ?)",
            (uid, today, utc_now_iso()),
        )
        conn.commit()
        first_today = cur.rowcount == 1  # 今天第一次签到
        granted = _granted_milestones(conn, uid)

    days = _days_of(uid)
    streak = _streak_ending_today(set(days), today)
    total = len(days)
    longest = _longest_streak(days)

    # 本次连续天数命中、且从未发过的最大里程碑（一次签到至多发一档，避免补签穿越多档）
    milestone: Optional[dict] = None
    for need, reward_days in sorted(MILESTONES, reverse=True):
        if streak >= need and need not in granted:
            membership = None
            if reward_days > 0:
                try:
                    membership = grant_membership(username, reward_days, source="checkin")
                except Exception:  # noqa: BLE001 —— 发奖失败不回滚签到
                    membership = None
            try:
                with _connect() as conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO checkin_reward (user_id, milestone, reward_days, granted_at)"
                        " VALUES (?, ?, ?, ?)",
                        (uid, need, reward_days, utc_now_iso()),
                    )
                    conn.commit()
            except sqlite3.Error:
                pass
            milestone = {"days": need, "reward_days": reward_days, "membership": membership}
            break

    return {
        "ok": True,
        "date": today,
        "first_today": first_today,
        "streak": streak,
        "total": total,
        "longest": longest,
        "milestone": milestone,
    }


def checkin_status(user_id: str) -> dict:
    """当前签到状态：今天是否已签 / 连续 / 累计 / 最长 / 下一个里程碑还差几天。"""
    init_checkin_db()
    uid = (user_id or "").strip()
    if not uid:
        return {"checked_today": False, "streak": 0, "total": 0, "longest": 0,
                "next_milestone": None, "days_to_next": None, "milestones": []}
    today = _bj_today()
    days = _days_of(uid)
    days_set = set(days)
    streak = _streak_ending_today(days_set, today)
    with _connect() as conn:
        granted = _granted_milestones(conn, uid)
    next_milestone = next_reward = None
    for need, reward_days in sorted(MILESTONES):
        if streak < need:
            next_milestone, next_reward = need, reward_days
            break
    return {
        "checked_today": today in days_set,
        "streak": streak,
        "total": len(days),
        "longest": _longest_streak(days),
        "next_milestone": next_milestone,
        "days_to_next": (next_milestone - streak) if next_milestone else None,
        "next_reward_days": next_reward,
        "milestones": [
            {"days": d, "reward_days": r, "reached": d in granted} for d, r in MILESTONES
        ],
    }
