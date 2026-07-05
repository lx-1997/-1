"""邀请奖励活动回归测试：卡折算数学 + 有效邀请判定(IP去重/激活/付费) + 兑换(自助/转人工)。"""
from __future__ import annotations

import pytest

from deepfocus_api import auth, referral, storage


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPFOCUS_DATABASE_URL", f"sqlite:///{tmp_path / 'ref.sqlite3'}")
    monkeypatch.setenv("DEEPFOCUS_JWT_SECRET", "test-secret")
    monkeypatch.delenv("DEEPFOCUS_AUTH_REQUIRED", raising=False)
    storage.reset_engine_for_tests()
    auth.init_auth()
    yield
    storage.reset_engine_for_tests()


def _mk(username, code=None, ip=None):
    return auth.create_user(None, username, "password1", phone=None, invite_code_used=code, registered_ip=ip)


def test_earned_math():
    # 里程碑(激活有效邀请)：1→3天 3→周 8→月 16→季 30→年(¥698)，永久档已取消；付费 2→季 4→年
    e = referral._earned
    assert e(0, 0)["month"] == 0 and e(0, 0)["year"] == 0
    assert e(8, 0)["month"] == 1 and e(8, 0)["quarter"] == 0
    assert e(16, 0)["quarter"] == 1 and e(16, 0)["year"] == 0
    assert e(30, 0)["year"] == 1 and e(30, 0)["month"] == 1 and e(30, 0)["quarter"] == 1
    assert e(99, 0)["lifetime"] == 0                            # 注册档无永久
    assert e(0, 1)["quarter"] == 0                              # 1 付费尚不解锁
    assert e(0, 2)["quarter"] == 1 and e(0, 2)["year"] == 0     # 2 付费 → 季卡
    assert e(0, 4)["year"] == 1                                 # 4 付费 → 年卡
    assert e(0, 99)["lifetime"] == 0                            # 付费档无永久


def test_reg_track_qualify_and_self_redeem(fresh, monkeypatch):
    inviter = _mk("inviterA")
    code = auth.get_invite_overview(inviter.id).code
    invitees = [_mk(f"a{i}", code=code, ip=f"10.0.0.{i}") for i in range(12)]  # 12 个不同 IP
    monkeypatch.setattr(referral, "_activity_last_seen", lambda: {u.id: "2099-01-01T00:00:00+00:00" for u in invitees})  # 全部已激活(次日回访)

    data = referral.overview(inviter.id)
    assert data["qualified_reg"] == 12
    assert data["suspicious"] is False
    assert data["earned"]["month"] == 1 and data["available"]["month"] == 1

    res = referral.redeem(inviter.id, "month")
    assert res["ok"] and res["status"] == "granted"
    assert res["membership"]["tier"] == "premium"
    # 已兑换 → 不可再兑
    assert referral.redeem(inviter.id, "month")["ok"] is False
    assert referral.overview(inviter.id)["available"]["month"] == 0


def test_paid_track(fresh, monkeypatch):
    inviter = _mk("inviterB")
    code = auth.get_invite_overview(inviter.id).code
    invitees = [_mk(f"b{i}", code=code, ip=f"10.1.0.{i}") for i in range(4)]
    monkeypatch.setattr(referral, "_activity_last_seen", lambda: {u.id: "2099-01-01T00:00:00+00:00" for u in invitees})
    for i in range(4):
        auth.grant_membership(f"b{i}", 400, source="paid")   # 4 个年费付费用户 → 触 2→季 与 4→年 双档

    data = referral.overview(inviter.id)
    assert data["qualified_paid"] == 4
    assert data["earned"]["year"] == 1 and data["earned"]["quarter"] == 1


def test_ip_dedup_and_suspicious_pending(fresh, monkeypatch):
    inviter = _mk("inviterC")
    code = auth.get_invite_overview(inviter.id).code
    distinct = [_mk(f"c{i}", code=code, ip=f"10.2.0.{i}") for i in range(10)]  # 10 个不同 IP
    cluster = [_mk(f"k{i}", code=code, ip="7.7.7.7") for i in range(3)]        # 3 个同 IP
    allu = distinct + cluster
    monkeypatch.setattr(referral, "_activity_last_seen", lambda: {u.id: "2099-01-01T00:00:00+00:00" for u in allu})

    data = referral.overview(inviter.id)
    assert data["qualified_reg"] == 11        # 10 不同 + 同IP只计首个
    assert data["suspicious"] is True         # 同 IP 聚集 ≥3 → 红旗
    assert data["available"]["month"] == 1

    res = referral.redeem(inviter.id, "month")
    assert res["ok"] and res["status"] == "pending"   # 可疑 → 转人工，不自动发
