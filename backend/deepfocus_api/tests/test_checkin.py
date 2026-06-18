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


def test_milestone_grants_once(iso, monkeypatch):
    grants = iso
    base = datetime(2026, 6, 1)
    for i in range(7):  # 连续 7 天 → 命中 7 天里程碑（3 天会员）
        _set_today(monkeypatch, (base + timedelta(days=i)).strftime("%Y-%m-%d"))
        r = checkin.record_checkin("u1", "user1")
    assert r["milestone"] == {"days": 7, "reward_days": 3, "membership": {"tier": "premium"}}
    assert grants == [("user1", 3, "checkin")]  # 只发一次实物奖励
    # 第 8 天继续签：不重复发 7 天档
    _set_today(monkeypatch, (base + timedelta(days=7)).strftime("%Y-%m-%d"))
    r = checkin.record_checkin("u1", "user1")
    assert r["milestone"] is None
    assert len(grants) == 1


def test_three_day_badge_no_reward(iso, monkeypatch):
    grants = iso
    base = datetime(2026, 6, 1)
    for i in range(3):
        _set_today(monkeypatch, (base + timedelta(days=i)).strftime("%Y-%m-%d"))
        r = checkin.record_checkin("u1", "user1")
    assert r["milestone"] == {"days": 3, "reward_days": 0, "membership": None}
    assert grants == []  # 3 天档是纯徽章，不发会员、不调 grant_membership


def test_status(iso, monkeypatch):
    base = datetime(2026, 6, 1)
    for i in range(3):
        _set_today(monkeypatch, (base + timedelta(days=i)).strftime("%Y-%m-%d"))
        checkin.record_checkin("u1", "user1")
    _set_today(monkeypatch, "2026-06-03")
    s = checkin.checkin_status("u1")
    assert s["checked_today"] is True and s["streak"] == 3
    assert s["next_milestone"] == 7 and s["days_to_next"] == 4 and s["next_reward_days"] == 3
    # 新用户：今日未签
    s2 = checkin.checkin_status("u2")
    assert s2["checked_today"] is False and s2["streak"] == 0 and s2["next_milestone"] == 3
