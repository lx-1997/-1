"""写接口限流中间件 + 数据源 DELETE 提权回归。

覆盖：rate_limit_hit 滑动窗口（按主体计数、窗口过期、IP 回退）、
中间件 429 集成（匿名连刷限流路径）、普通用户 DELETE /api/data-sources 被拒。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from deepfocus_api import auth


def _request(method: str = "POST", path: str = "/api/ai/stock-analysis", ip: str = "1.2.3.4"):
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [(b"x-real-ip", ip.encode())],
        "client": ("9.9.9.9", 1234),
        "query_string": b"",
    }
    return auth.Request(scope)


@pytest.fixture(autouse=True)
def _clean_windows():
    auth._RATE_WINDOWS.clear()
    yield
    auth._RATE_WINDOWS.clear()


def test_rate_limit_hit_counts_per_subject():
    claims = {"sub": "user-a"}
    for _ in range(12):
        assert auth.rate_limit_hit(_request(), claims) is None
    assert auth.rate_limit_hit(_request(), claims) == "/api/ai/"
    # 其他主体不受影响
    assert auth.rate_limit_hit(_request(), {"sub": "user-b"}) is None


def test_rate_limit_hit_anonymous_falls_back_to_ip():
    for _ in range(12):
        assert auth.rate_limit_hit(_request(), None) is None
    assert auth.rate_limit_hit(_request(), None) == "/api/ai/"
    # 不同 IP 不受牵连
    assert auth.rate_limit_hit(_request(ip="5.6.7.8"), None) is None


def test_rate_limit_hit_non_matching_method_path():
    assert auth.rate_limit_hit(_request(method="GET"), {"sub": "u"}) is None
    assert auth.rate_limit_hit(_request(path="/api/realtime/messages"), {"sub": "u"}) is None


def test_rate_limit_hit_window_expires(monkeypatch):
    monkeypatch.setattr(auth.time, "monotonic", lambda: 1000.0)
    claims = {"sub": "u"}
    for _ in range(12):
        assert auth.rate_limit_hit(_request(), claims) is None
    assert auth.rate_limit_hit(_request(), claims) is not None
    # 时间前进越过窗口 → 重新放行
    monkeypatch.setattr(auth.time, "monotonic", lambda: 1061.0)
    assert auth.rate_limit_hit(_request(), claims) is None


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "rate_limit_test.sqlite3"
    monkeypatch.setenv("DEEPFOCUS_DATABASE_URL", f"sqlite:///{db_file}")
    monkeypatch.setenv("DEEPFOCUS_AUTH_REQUIRED", "true")
    monkeypatch.setenv("DEEPFOCUS_JWT_SECRET", "test-secret-key")
    monkeypatch.setenv("DEEPFOCUS_ADMIN_EMAIL", "real-admin@example.com")
    monkeypatch.setenv("DEEPFOCUS_ADMIN_PASSWORD", "AdminPass123")
    from deepfocus_api import storage

    storage.reset_engine_for_tests()
    auth.init_auth()
    from deepfocus_api.main import app

    yield TestClient(app)
    storage.reset_engine_for_tests()


def test_middleware_returns_429_on_burst(client, monkeypatch):
    # 缩小阈值让集成测试快：仅保留 metrics 埋点规则并降到 2 次
    monkeypatch.setattr(
        auth, "_RATE_RULES", (("POST", "/api/metrics/", 2, 60),)
    )
    r1 = client.post("/api/metrics/pageview", json={"path": "/"})
    r2 = client.post("/api/metrics/pageview", json={"path": "/"})
    r3 = client.post("/api/metrics/pageview", json={"path": "/"})
    assert r1.status_code != 429 and r2.status_code != 429
    assert r3.status_code == 429
    assert "频繁" in r3.json()["detail"]


def test_delete_data_source_requires_admin(client):
    # 普通登录用户（非 admin）删全局数据源 → 403
    reg = client.post(
        "/api/auth/register",
        json={"username": "trial-user", "password": "Passw0rd123", "email": "trial@example.com"},
    )
    assert reg.status_code == 200, reg.text
    token = reg.json().get("access_token") or client.post(
        "/api/auth/login", json={"username": "trial-user", "password": "Passw0rd123"}
    ).json()["access_token"]
    resp = client.delete(
        "/api/data-sources/ds-any", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 403, resp.text
