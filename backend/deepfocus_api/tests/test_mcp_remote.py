"""远程 MCP 端点（/api/mcp）回归测试。

覆盖：个人接入令牌签发/校验/撤销、JSON-RPC initialize/tools_list/tools_call、
无效令牌 401、错误参数 isError、账户令牌管理端点的越权防护。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from deepfocus_api import auth, storage
from deepfocus_api import mcp_tokens


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    db_file = tmp_path / "auth_test.sqlite3"
    monkeypatch.setenv("DEEPFOCUS_DATABASE_URL", f"sqlite:///{db_file}")
    monkeypatch.setenv("DEEPFOCUS_MCP_TOKEN_DB_PATH", str(tmp_path / "mcp_tokens_test.sqlite3"))
    monkeypatch.delenv("DEEPFOCUS_AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("DEEPFOCUS_ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("DEEPFOCUS_ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("DEEPFOCUS_JWT_SECRET", "test-secret-key")
    storage.reset_engine_for_tests()
    auth.init_auth()
    mcp_tokens.init_mcp_tokens_db()
    yield
    storage.reset_engine_for_tests()


@pytest.fixture
def client(fresh_db):
    from deepfocus_api.main import app

    return TestClient(app)


def _rpc(client: TestClient, payload: dict, token: str = ""):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return client.post("/api/mcp", json=payload, headers=headers)


def _make_user_and_token(client: TestClient) -> tuple[str, str]:
    """注册一个用户（首个用户自动 admin）并签发一把 MCP 接入令牌，返回 (jwt, token)。"""
    r = client.post("/api/auth/register", json={"username": "mcpuser", "password": "pw123456", "email": "mcp@test.dev"})
    assert r.status_code == 200, r.text
    jwt_token = r.json()["access_token"]
    r = client.post(
        "/api/account/mcp-tokens",
        json={"name": "测试令牌"},
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert r.status_code == 200, r.text
    return jwt_token, r.json()["token"]


# --------------------------------------------------------------------------- #
# 令牌层
# --------------------------------------------------------------------------- #
def test_token_issue_verify_revoke_roundtrip(fresh_db):
    issued = mcp_tokens.issue_token("u1", "alice", "我的 Cursor")
    assert issued["token"].startswith("dfm_")
    rec = mcp_tokens.verify_token(issued["token"])
    assert rec is not None and rec["user_id"] == "u1"
    assert "token_hash" not in rec and rec["_token_hash"]  # 明文/摘要不外泄
    # 错误令牌 / 非法前缀
    assert mcp_tokens.verify_token(issued["token"] + "x") is None
    assert mcp_tokens.verify_token("dfk_should_not_match") is None
    # 撤销后立即失效，且他人无法撤销
    assert mcp_tokens.revoke_token("u2", issued["token_prefix"]) is False
    assert mcp_tokens.revoke_token("u1", issued["token_prefix"]) is True
    assert mcp_tokens.verify_token(issued["token"]) is None


def test_token_cap_per_user(fresh_db):
    for i in range(mcp_tokens._MAX_TOKENS_PER_USER):
        mcp_tokens.issue_token("u1", "alice", f"t{i}")
    with pytest.raises(ValueError):
        mcp_tokens.issue_token("u1", "alice", "one too many")


# --------------------------------------------------------------------------- #
# JSON-RPC 端点
# --------------------------------------------------------------------------- #
def test_rpc_requires_valid_token(client):
    body = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    r = _rpc(client, body)  # 无令牌
    assert r.status_code == 401
    r = _rpc(client, body, token="dfm_not_a_real_token")
    assert r.status_code == 401


def test_rpc_initialize_and_tools_list(client):
    _, token = _make_user_and_token(client)
    r = _rpc(client, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, token)
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["serverInfo"]["name"] == "稻草财经 MCP"
    assert result["protocolVersion"]

    r = _rpc(client, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, token)
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert {"ping", "get_review_today", "get_stock_verdict", "ask_ai"} <= names

    r = _rpc(client, {"jsonrpc": "2.0", "id": 3, "method": "no/such/method"}, token)
    assert r.json()["error"]["code"] == -32601


def test_rpc_ping_returns_account_and_membership(client):
    _, token = _make_user_and_token(client)
    r = _rpc(client, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                      "params": {"name": "ping", "arguments": {}}}, token)
    assert r.status_code == 200
    data = r.json()["result"]
    assert data["isError"] is False
    payload = data["structuredContent"]["data"]
    assert payload["ok"] is True
    assert payload["account"] == "mcpuser"
    assert payload["membership"]["tier"] in ("trial", "premium", "lifetime")


def test_rpc_tool_argument_error_is_error_result(client):
    _, token = _make_user_and_token(client)
    r = _rpc(client, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                      "params": {"name": "get_review", "arguments": {"date": "not-a-date"}}}, token)
    assert r.status_code == 200
    data = r.json()["result"]
    assert data["isError"] is True
    assert "YYYY-MM-DD" in data["content"][0]["text"]

    r = _rpc(client, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                      "params": {"name": "no_such_tool", "arguments": {}}}, token)
    assert r.json()["error"]["code"] == -32602


def test_notification_gets_202(client):
    _, token = _make_user_and_token(client)
    r = _rpc(client, {"jsonrpc": "2.0", "method": "notifications/initialized"}, token)
    assert r.status_code == 202


# --------------------------------------------------------------------------- #
# 账户令牌管理端点
# --------------------------------------------------------------------------- #
def test_account_token_endpoints_flow(client):
    r = client.post("/api/auth/register", json={"username": "tokuser", "password": "pw123456", "email": "tok@test.dev"})
    jwt_token = r.json()["access_token"]
    hdr = {"Authorization": f"Bearer {jwt_token}"}

    r = client.post("/api/account/mcp-tokens", json={"name": "cherry"}, headers=hdr)
    assert r.status_code == 200
    plain = r.json()["token"]
    prefix = r.json()["token_prefix"]

    r = client.get("/api/account/mcp-tokens", headers=hdr)
    tokens = r.json()["tokens"]
    assert len(tokens) == 1
    assert tokens[0]["token_prefix"] == prefix
    assert "token" not in tokens[0]  # 列表绝不含明文

    # 未登录管理端点被拒
    assert client.get("/api/account/mcp-tokens").status_code == 401

    # 撤销后 MCP 立即 401
    r = client.delete(f"/api/account/mcp-tokens/{prefix}", headers=hdr)
    assert r.json() == {"ok": True}
    assert client.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                       headers={"Authorization": f"Bearer {plain}"}).status_code == 401
    # 重复撤销 404
    assert client.delete(f"/api/account/mcp-tokens/{prefix}", headers=hdr).status_code == 404


def test_setup_page_public(client):
    r = client.get("/api/mcp/setup")
    assert r.status_code == 200
    assert "MCP 接入控制台" in r.text
    assert "/api/mcp" in r.text


# --------------------------------------------------------------------------- #
# OAuth 2.1 浏览器授权流
# --------------------------------------------------------------------------- #
def test_mcp_401_points_to_oauth_discovery(client):
    r = client.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401
    www = r.headers.get("WWW-Authenticate", "")
    assert "resource_metadata" in www and "/.well-known/oauth-protected-resource" in www
    assert r.headers.get("Access-Control-Allow-Origin") == "*"


def test_oauth_metadata_endpoints(client):
    rs = client.get("/.well-known/oauth-protected-resource").json()
    assert rs["resource"].endswith("/api/mcp")
    assert rs["authorization_servers"]
    asm = client.get("/.well-known/oauth-authorization-server").json()
    assert asm["authorization_endpoint"].endswith("/api/oauth/authorize")
    assert asm["token_endpoint"].endswith("/api/oauth/token")
    assert asm["code_challenge_methods_supported"] == ["S256"]


def test_oauth_authorization_code_flow(client):
    from urllib.parse import urlparse, parse_qs

    from deepfocus_api.mcp_oauth import pkce_s256

    # ① 动态注册客户端
    r = client.post("/api/oauth/register", json={
        "client_name": "测试客户端",
        "redirect_uris": ["http://localhost:9911/callback"],
    })
    assert r.status_code == 201, r.text
    cid = r.json()["client_id"]

    # ② 授权页可访问
    assert client.get("/api/oauth/authorize").status_code == 200

    # ③ 注册登录用户 → 页面以站点 JWT 审批
    reg = client.post("/api/auth/register", json={
        "username": "oauthuser", "password": "pw123456", "email": "oauth@test.dev"})
    site_jwt = reg.json()["access_token"]

    verifier = "v" * 64
    challenge = pkce_s256(verifier)
    redirect_uri = "http://localhost:9911/callback"
    r = client.post("/api/oauth/authorize/consent", json={
        "approve": True, "client_id": cid, "redirect_uri": redirect_uri,
        "state": "st123", "code_challenge": challenge,
        "code_challenge_method": "S256", "resource": "https://daocaijing.com/api/mcp",
    }, headers={"Authorization": f"Bearer {site_jwt}"})
    assert r.status_code == 200, r.text
    redirect = r.json()["redirect"]
    assert redirect.startswith(redirect_uri)
    q = parse_qs(urlparse(redirect).query)
    assert q["state"] == ["st123"]
    code = q["code"][0]

    # ④ 换 token（urlencoded 表单，模拟真实客户端）；错误 verifier 必须被拒
    bad = client.post("/api/oauth/token", data={
        "grant_type": "authorization_code", "code": code, "client_id": cid,
        "redirect_uri": redirect_uri, "code_verifier": "w" * 64})
    assert bad.status_code == 400
    r = client.post("/api/oauth/token", data={
        "grant_type": "authorization_code", "code": code, "client_id": cid,
        "redirect_uri": redirect_uri, "code_verifier": verifier})
    assert r.status_code == 200, r.text
    pair = r.json()
    access, refresh = pair["access_token"], pair["refresh_token"]
    assert pair["token_type"] == "bearer" and pair["expires_in"] > 0

    # ⑤ 授权码单次使用：重放被拒
    r = client.post("/api/oauth/token", data={
        "grant_type": "authorization_code", "code": code, "client_id": cid,
        "redirect_uri": redirect_uri, "code_verifier": verifier})
    assert r.status_code == 400

    # ⑥ 用 access token 调 MCP 工具（ping 回显账号）
    r = client.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                      "params": {"name": "ping", "arguments": {}}},
                    headers={"Authorization": f"Bearer {access}"})
    assert r.status_code == 200
    data = r.json()["result"]["structuredContent"]["data"]
    assert data["ok"] is True and data["account"] == "oauthuser"

    # ⑦ refresh 轮换：旧 refresh 用一次后即作废
    r = client.post("/api/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": refresh, "client_id": cid})
    assert r.status_code == 200
    new_refresh = r.json()["refresh_token"]
    assert client.post("/api/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": refresh, "client_id": cid}).status_code == 400
    assert client.post("/api/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": new_refresh, "client_id": "dfc_other"}).status_code == 400

    # ⑧ 未注册的 redirect_uri 被拒
    r = client.post("/api/oauth/authorize/consent", json={
        "approve": True, "client_id": cid, "redirect_uri": "https://evil.example/cb",
        "code_challenge": challenge, "code_challenge_method": "S256",
    }, headers={"Authorization": f"Bearer {site_jwt}"})
    assert r.status_code == 400


def test_oauth_consent_requires_login(client):
    r = client.post("/api/oauth/authorize/consent", json={"approve": True, "client_id": "x",
                                                          "redirect_uri": "http://a/cb"})
    assert r.status_code == 401
