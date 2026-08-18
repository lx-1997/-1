"""会员码防刷回归：体验权益终身一次、真实结果审计、兑换限流。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from deepfocus_api import auth, membership_codes, storage, write_guard


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPFOCUS_DATABASE_URL", f"sqlite:///{tmp_path / 'codes.sqlite3'}")
    monkeypatch.setenv("DEEPFOCUS_JWT_SECRET", "test-secret")
    monkeypatch.delenv("DEEPFOCUS_AUTH_REQUIRED", raising=False)
    storage.reset_engine_for_tests()
    write_guard._HITS.clear()
    auth.init_auth()
    yield
    write_guard._HITS.clear()
    storage.reset_engine_for_tests()


def _user(name: str):
    return auth.create_user(None, name, "password1")


def test_trial_code_is_lifetime_once_and_second_code_stays_unused(fresh):
    user = _user("trial-code-user")
    first, second = membership_codes.generate_codes(2, "trial")

    ok = membership_codes.redeem(first, user.id, user.username)
    blocked = membership_codes.redeem(second, user.id, user.username)

    assert ok["ok"] is True and ok["kind"] == "trial" and ok["days"] == 7
    assert blocked == {"ok": False, "reason": "trial_ever"}
    rows = {row["code"]: row for row in membership_codes.list_codes()}
    assert rows[first]["used"] is True
    assert rows[second]["used"] is False
    assert auth.claim_trial(user.id)["reason"] == "claimed"


def test_login_trial_also_blocks_trial_code_without_consuming_it(fresh):
    user = _user("login-trial-user")
    assert auth.claim_trial(user.id)["ok"] is True
    code = membership_codes.generate_codes(1, "trial")[0]

    assert membership_codes.redeem(code, user.id, user.username) == {
        "ok": False,
        "reason": "trial_ever",
    }
    row = next(item for item in membership_codes.list_codes() if item["code"] == code)
    assert row["used"] is False


def test_normal_paid_codes_are_not_lifetime_limited(fresh):
    user = _user("paid-code-user")
    first, second = membership_codes.generate_codes(2, "premium", days=30)

    assert membership_codes.redeem(first, user.id, user.username)["ok"] is True
    assert membership_codes.redeem(second, user.id, user.username)["ok"] is True


def test_redeem_endpoint_logs_results_and_rate_limits(fresh, monkeypatch):
    from deepfocus_api import main as main_mod

    user = _user("endpoint-user")
    token = auth.create_access_token(user)
    headers = {"Authorization": f"Bearer {token}", "User-Agent": "pytest"}
    logged: list[dict] = []
    monkeypatch.setattr(main_mod, "metrics_log_activity", lambda **kwargs: logged.append(kwargs))
    client = TestClient(main_mod.app)

    first, second = membership_codes.generate_codes(2, "trial")
    success = client.post("/api/membership/redeem", json={"code": first}, headers=headers)
    blocked = client.post("/api/membership/redeem", json={"code": second}, headers=headers)

    assert success.status_code == 200
    assert blocked.status_code == 400
    assert "仅可领取或兑换一次" in blocked.json()["detail"]
    assert [item["action"] for item in logged[:2]] == ["redeem_success", "redeem_failed"]

    # 前两次已占用窗口；再允许 3 次，随后第 6 次在服务端被限流。
    for i in range(3):
        assert client.post(
            "/api/membership/redeem", json={"code": f"INVALID-{i}"}, headers=headers
        ).status_code == 400
    limited = client.post(
        "/api/membership/redeem", json={"code": "INVALID-LIMIT"}, headers=headers
    )
    assert limited.status_code == 429
    assert logged[-1]["action"] == "redeem_rate_limited"
