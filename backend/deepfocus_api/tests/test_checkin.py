"""连续看复盘签到：幂等、连续天数、里程碑发奖去重、状态查询。"""
from datetime import datetime, timedelta

import pytest

from deepfocus_api import checkin


@pytest.fixture
def iso(monkeypatch, tmp_path):
    monkeypatch.setattr(checkin, "_db_path", lambda: tmp_path / "checkin.sqlite3")
    grants = []
    monkeypatch.setattr(checkin, "grant_membership",
                        lambda username, days, source="admin": grants.append((username, days, source)) or {"tier": "premium"})
    return grants


def _set_today(monkeypatch, day: str):
    monkeypatch.setattr(checkin, "_bj_today", lambda: day)


def test_idempotent_same_day(iso, monkeypatch):
    _set_today(monkeypatch, "2026-06-13")
    r1 = checkin.record_checkin("u1", "user1")
    r2 = checkin.record_checkin("u1", "user1")
    assert r1["first_today"] is True and r2["first_today"] is False
    assert r1["streak"] == 1 and r2["streak"] == 1
    assert r2["total"] == 1  # 同日只记一条


def test_consecutive_streak_and_break(iso, monkeypatch):
    for d in ("2026-06-10", "2026-06-11", "2026-06-12"):
        _set_today(monkeypatch, d)
        r = checkin.record_checkin("u1", "user1")
    assert r["streak"] == 3 and r["total"] == 3 and r["longest"] == 3
    # 断一天（跳过 13），14 号再签 → 连续重置为 1，最长仍 3
    _set_today(monkeypatch, "2026-06-14")
    r = checkin.record_checkin("u1", "user1")
    assert r["streak"] == 1 and r["total"] == 4 and r["longest"] == 3


def test_day2_milestone_hooks_return(iso, monkeypatch):
    """⭐ D1→D2 即时钩子：连续 2 天当场发 1 天会员（专治次日流失）。"""
    grants = iso
    base = datetime(2026, 6, 1)
    _set_today(monkeypatch, base.strftime("%Y-%m-%d"))
    r1 = checkin.record_checkin("u1", "user1")
    assert r1["milestone"] is None and grants == []  # 第 1 天还没到任何档
    _set_today(monkeypatch, (base + timedelta(days=1)).strftime("%Y-%m-%d"))
    r2 = checkin.record_checkin("u1", "user1")
    assert r2["streak"] == 2
    assert r2["milestone"] == {"days": 2, "reward_days": 1, "membership": {"tier": "premium"}}
    assert grants == [("user1", 1, "checkin")]  # 即时发 1 天，source=checkin 不计 paid


def test_milestone_grants_once(iso, monkeypatch):
    grants = iso
    base = datetime(2026, 6, 1)
    for i in range(7):  # 连续 7 天 → 依次命中 2 天(1天)、7 天(3天)里程碑
        _set_today(monkeypatch, (base + timedelta(days=i)).strftime("%Y-%m-%d"))
        r = checkin.record_checkin("u1", "user1")
    assert r["milestone"] == {"days": 7, "reward_days": 3, "membership": {"tier": "premium"}}
    # D2 钩子先发 1 天，7 天档再发 3 天，各只发一次
    assert grants == [("user1", 1, "checkin"), ("user1", 3, "checkin")]
    # 第 8 天继续签：不重复发 7 天档
    _set_today(monkeypatch, (base + timedelta(days=7)).strftime("%Y-%m-%d"))
    r = checkin.record_checkin("u1", "user1")
    assert r["milestone"] is None
    assert len(grants) == 2


def test_three_day_badge_no_reward(iso, monkeypatch):
    grants = iso
    base = datetime(2026, 6, 1)
    for i in range(3):
        _set_today(monkeypatch, (base + timedelta(days=i)).strftime("%Y-%m-%d"))
        r = checkin.record_checkin("u1", "user1")
    assert r["milestone"] == {"days": 3, "reward_days": 0, "membership": None}
    # 第 3 天档是纯徽章、不调 grant_membership；grants 里只有第 2 天的 D2 钩子（1 天）
    assert grants == [("user1", 1, "checkin")]


def test_status(iso, monkeypatch):
    base = datetime(2026, 6, 1)
    for i in range(3):
        _set_today(monkeypatch, (base + timedelta(days=i)).strftime("%Y-%m-%d"))
        checkin.record_checkin("u1", "user1")
    _set_today(monkeypatch, "2026-06-03")
    s = checkin.checkin_status("u1")
    assert s["checked_today"] is True and s["streak"] == 3
    assert s["next_milestone"] == 7 and s["days_to_next"] == 4 and s["next_reward_days"] == 3
    # 新用户：今日未签；下一个里程碑现在是 2 天（D2 钩子）
    s2 = checkin.checkin_status("u2")
    assert s2["checked_today"] is False and s2["streak"] == 0 and s2["next_milestone"] == 2
