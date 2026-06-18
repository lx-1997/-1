"""会员到期转化召回：候选去重、每日上限、SMTP 未配置降级、邮件文案。"""
import pytest

from deepfocus_api import expiry_reminder


@pytest.fixture
def iso(monkeypatch, tmp_path):
    monkeypatch.setattr(expiry_reminder, "RECALL_DB_PATH", tmp_path / "recall.sqlite3")
    monkeypatch.setattr(expiry_reminder, "_email_smtp_config", lambda: None)  # SMTP 未配置 → skipped
    return tmp_path


def _cand(uid, day_left=1):
    return {"id": uid, "username": f"u_{uid}", "email": f"{uid}@x.com",
            "expires_at": f"2026-06-1{day_left}T08:00:00+00:00", "source": "invite", "days_left": day_left}


def _working_smtp(monkeypatch):
    monkeypatch.setattr(expiry_reminder, "_email_smtp_config",
                        lambda: {"sender": "s@x.com", "user": "u@x.com"})
    monkeypatch.setattr(expiry_reminder, "_smtp_sendmail", lambda config, to, mime: None)


def test_smtp_unset_does_not_poison(iso, monkeypatch):
    """SMTP 未配置：本轮 skipped 但不落库，候选不被毒化（下轮仍可召回）。"""
    monkeypatch.setattr(expiry_reminder, "users_expiring_within", lambda h=48: [_cand("a")])
    r1 = expiry_reminder.run_expiry_reminder_once()  # 固件里 SMTP→None
    assert r1["candidates"] == 1 and r1["skipped"] == 1
    s = expiry_reminder.expiry_reminder_stats()
    assert s["skipped"] == 0 and s["recent"] == []  # 没落库
    r2 = expiry_reminder.run_expiry_reminder_once()
    assert r2["candidates"] == 1  # 仍是候选


def test_dedupe_per_expiry_cycle(iso, monkeypatch):
    monkeypatch.setattr(expiry_reminder, "users_expiring_within", lambda h=48: [_cand("a")])
    _working_smtp(monkeypatch)
    r1 = expiry_reminder.run_expiry_reminder_once()
    assert r1["candidates"] == 1 and r1["sent"] == 1  # 真发出并落库
    r2 = expiry_reminder.run_expiry_reminder_once()
    assert r2["candidates"] == 0  # 同一到期日不再重复


def test_daily_limit(iso, monkeypatch):
    monkeypatch.setattr(expiry_reminder, "users_expiring_within", lambda h=48: [_cand(f"u{i}") for i in range(5)])
    r = expiry_reminder.run_expiry_reminder_once(limit=2)
    assert r["candidates"] == 5 and r["deferred"] == 3
    assert r["skipped"] == 2


def test_email_copy(iso):
    s1, b1 = expiry_reminder._build_email("张三", 1)
    assert "明天到期" in s1 and "张三" in b1 and "续费" in b1
    s0, _ = expiry_reminder._build_email("李四", 0)
    assert "今天到期" in s0


def test_stats(iso, monkeypatch):
    monkeypatch.setattr(expiry_reminder, "users_expiring_within", lambda h=48: [_cand("a"), _cand("b")])
    _working_smtp(monkeypatch)
    expiry_reminder.run_expiry_reminder_once()
    s = expiry_reminder.expiry_reminder_stats()
    assert s["sent"] == 2 and len(s["recent"]) == 2
