"""认证 / RBAC / 存储 seam 回归测试。

覆盖：口令哈希不可逆、JWT 签发与篡改/过期、用户存储与唯一性、首用户自动 admin、
登录/注册/me/users 端点、以及 DEEPFOCUS_AUTH_REQUIRED 打开后的中间件强制 + 路径级 RBAC。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from deepfocus_api import auth, storage


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """每个用例切到独立临时 SQLite，建表，跑完丢弃 engine。"""
    db_file = tmp_path / "auth_test.sqlite3"
    monkeypatch.setenv("DEEPFOCUS_DATABASE_URL", f"sqlite:///{db_file}")
    monkeypatch.delenv("DEEPFOCUS_AUTH_REQUIRED", raising=False)
    monkeypatch.delenv("DEEPFOCUS_ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("DEEPFOCUS_ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("DEEPFOCUS_JWT_SECRET", "test-secret-key")
    storage.reset_engine_for_tests()
    auth.init_auth()
    yield
    storage.reset_engine_for_tests()


@pytest.fixture
def client(fresh_db):
    # 不用 with 上下文 → 不触发 lifespan（避免拉起 worker/网络），但中间件照常生效。
    from deepfocus_api.main import app

    return TestClient(app)


# --------------------------------------------------------------------------- #
# 口令与令牌
# --------------------------------------------------------------------------- #
def test_password_hash_is_not_plaintext_and_verifies():
    hashed = auth.hash_password("s3cret-pw")
    assert hashed != "s3cret-pw"
    assert "s3cret-pw" not in hashed
    assert auth.verify_password("s3cret-pw", hashed) is True
    assert auth.verify_password("wrong", hashed) is False


def test_verify_password_tolerates_garbage_hash():
    assert auth.verify_password("x", "not-a-real-hash") is False


def test_jwt_roundtrip_and_tamper(monkeypatch):
    monkeypatch.setenv("DEEPFOCUS_JWT_SECRET", "test-secret-key")
    user = auth.AuthUserOut(
        id="u1",
        email="a@b.com",
        username="alice",
        role=auth.ROLE_ANALYST,
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )
    token = auth.create_access_token(user)
    claims = auth.decode_token(token)
    assert claims is not None
    assert claims["sub"] == "u1"
    assert claims["role"] == auth.ROLE_ANALYST

    assert auth.decode_token(token + "tamper") is None
    # 用错误密钥签名的令牌应被拒。
    forged = jwt.encode({"sub": "u1", "role": "admin"}, "other-secret", algorithm="HS256")
    assert auth.decode_token(forged) is None


def test_expired_jwt_rejected(monkeypatch):
    monkeypatch.setenv("DEEPFOCUS_JWT_SECRET", "test-secret-key")
    expired = jwt.encode(
        {
            "sub": "u1",
            "role": "viewer",
            "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
        },
        "test-secret-key",
        algorithm="HS256",
    )
    assert auth.decode_token(expired) is None


# --------------------------------------------------------------------------- #
# 用户存储
# --------------------------------------------------------------------------- #
def test_first_user_becomes_admin_then_analyst(fresh_db):
    first = auth.create_user("boss@firm.com", "boss", "password1")
    assert first.role == auth.ROLE_ADMIN
    second = auth.create_user("ana@firm.com", "analyst1", "password1")
    assert second.role == auth.ROLE_ANALYST


def test_duplicate_email_or_username_rejected(fresh_db):
    auth.create_user("dup@firm.com", "dup", "password1")
    with pytest.raises(auth.UserExistsError):
        auth.create_user("dup@firm.com", "other", "password1")
    with pytest.raises(auth.UserExistsError):
        auth.create_user("other@firm.com", "dup", "password1")


def test_authenticate_by_email_and_username(fresh_db):
    auth.create_user("trader@firm.com", "trader", "password1")
    assert auth.authenticate("trader@firm.com", "password1") is not None
    assert auth.authenticate("trader", "password1") is not None
    assert auth.authenticate("trader", "wrong") is None
    assert auth.authenticate("ghost", "password1") is None


def test_role_hierarchy():
    assert auth.role_satisfies(auth.ROLE_ADMIN, auth.ROLE_ANALYST) is True
    assert auth.role_satisfies(auth.ROLE_ANALYST, auth.ROLE_ADMIN) is False
    assert auth.role_satisfies(auth.ROLE_VIEWER, auth.ROLE_VIEWER) is True


# --------------------------------------------------------------------------- #
# 端点（旁路态）
# --------------------------------------------------------------------------- #
def test_register_login_me_flow(client):
    reg = client.post(
        "/api/auth/register",
        json={"email": "user@firm.com", "username": "user1", "password": "password1"},
    )
    assert reg.status_code == 200, reg.text
    body = reg.json()
    token = body["access_token"]
    assert body["user"]["role"] == auth.ROLE_ADMIN  # 首个用户

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "user@firm.com"

    # 无令牌访问 /me → 401（handler 级守卫，即便旁路态）。
    assert client.get("/api/auth/me").status_code == 401


def test_register_rejects_bad_email_and_duplicates(client):
    assert (
        client.post(
            "/api/auth/register",
            json={"email": "not-an-email", "username": "abc", "password": "password1"},
        ).status_code
        == 422
    )
    client.post(
        "/api/auth/register",
        json={"email": "dupe@firm.com", "username": "dupe", "password": "password1"},
    )
    assert (
        client.post(
            "/api/auth/register",
            json={"email": "dupe@firm.com", "username": "dupe2", "password": "password1"},
        ).status_code
        == 409
    )


def test_register_phone_only_succeeds_email_optional(client):
    # 邮箱可不填——只要手机号给了就行；邮箱回 None，可用用户名登录。
    reg = client.post(
        "/api/auth/register",
        json={"username": "noemail", "password": "password1", "phone": "13800001111"},
    )
    assert reg.status_code == 200, reg.text
    assert reg.json()["user"]["email"] is None
    assert reg.json()["user"]["phone"] == "13800001111"
    assert (
        client.post(
            "/api/auth/login", json={"username": "noemail", "password": "password1"}
        ).status_code
        == 200
    )


def test_register_requires_phone_or_email(client):
    # 手机号、邮箱至少填一项：两者都空 → 422。
    assert client.post(
        "/api/auth/register", json={"username": "bothblank", "password": "password1"}
    ).status_code == 422
    # 任填其一即可。
    assert client.post(
        "/api/auth/register",
        json={"username": "haspho", "password": "password1", "phone": "13800002222"},
    ).status_code == 200
    assert client.post(
        "/api/auth/register",
        json={"username": "hasmail", "password": "password1", "email": "a@firm.com"},
    ).status_code == 200


def test_multiple_users_without_email_do_not_clash(fresh_db):
    # 多个 NULL 邮箱不应触发唯一性冲突（仅按用户名查重）。
    a = auth.create_user(None, "alpha", "password1")
    b = auth.create_user("", "beta", "password1")  # 空串也归一成 None
    assert a.email is None and b.email is None


def test_account_stats_shape(fresh_db):
    """看板账号统计：总数/角色分布/手机邮箱填写/最近列表（不含明文 PII）。"""
    auth.create_user("a@firm.com", "boss", "password1")            # 首个 → admin
    auth.create_user(None, "ana", "password1", phone="13800000001")  # 无邮箱、有手机
    stats = auth.account_stats()
    assert stats["total"] == 2
    assert stats["new_today"] == 2 and stats["new_7d"] == 2
    assert stats["with_phone"] == 1 and stats["with_email"] == 1
    assert stats["by_role"]["admin"] == 1 and stats["by_role"]["analyst"] == 1
    assert len(stats["recent"]) == 2
    # 最近列表只露用户名 + 是否填写，绝不含明文手机号/邮箱
    blob = str(stats["recent"])
    assert "13800000001" not in blob and "a@firm.com" not in blob
    assert {"username", "role", "created_at", "has_phone", "has_email"} == set(stats["recent"][0])


def test_reconcile_migrates_legacy_schema(tmp_path, monkeypatch):
    """老库（email NOT NULL、无 phone）迁移：旧账号完好可登录，新增可不带邮箱、可带手机号。"""
    from sqlalchemy import text

    db_file = tmp_path / "legacy.sqlite3"
    monkeypatch.setenv("DEEPFOCUS_DATABASE_URL", f"sqlite:///{db_file}")
    monkeypatch.setenv("DEEPFOCUS_JWT_SECRET", "test-secret-key")
    storage.reset_engine_for_tests()

    # 手工铺一张“老 schema”表 + 一个旧账号。
    eng = storage.get_engine()
    with eng.begin() as conn:
        conn.execute(text(
            "CREATE TABLE auth_users ("
            " id VARCHAR(64) NOT NULL PRIMARY KEY,"
            " email VARCHAR(320) NOT NULL,"
            " username VARCHAR(120) NOT NULL,"
            " password_hash VARCHAR(255) NOT NULL,"
            " role VARCHAR(32), is_active BOOLEAN, created_at DATETIME)"
        ))
        conn.execute(text("CREATE UNIQUE INDEX ix_auth_users_email ON auth_users (email)"))
        conn.execute(text("CREATE UNIQUE INDEX ix_auth_users_username ON auth_users (username)"))
        conn.execute(
            text(
                "INSERT INTO auth_users "
                "(id, email, username, password_hash, role, is_active, created_at) "
                "VALUES (:id, :email, :u, :pw, 'admin', 1, '2025-01-01 00:00:00')"
            ),
            {"id": "old1", "email": "old@firm.com", "u": "olduser", "pw": auth.hash_password("password1")},
        )

    # 完整升级链（与 init_auth 同序）：先补 phone/放开 email，再补邀请码列 + 给存量补码，最后补 session_id。
    auth._reconcile_auth_schema()
    auth._ensure_invite_columns()
    auth._ensure_session_column()
    auth._ensure_membership_column()
    auth._ensure_referral_columns()

    # 旧账号原封不动、仍可登录；存量账号被补发了邀请码。
    assert auth.authenticate("olduser", "password1") is not None
    overview = auth.get_invite_overview(
        auth.get_user_out_by_email("old@firm.com").id  # type: ignore[union-attr]
    )
    assert overview is not None and overview.code  # 老账号已有邀请码
    # 新增账号可不带邮箱、可带手机号（证明 email 可空、phone + invite 列都已就位）。
    with_phone = auth.create_user(None, "newbie", "password1", phone="13800000000")
    assert with_phone.phone == "13800000000"
    assert auth.create_user(None, "noemail2", "password1").email is None
    storage.reset_engine_for_tests()


def test_username_case_insensitive(fresh_db):
    """用户名大小写不敏感：变体注册被拒（防仿冒）、任意大小写可登录、显示名保留原样。"""
    auth.create_user(None, "CaseUser", "password1")
    with pytest.raises(auth.UserExistsError):
        auth.create_user(None, "caseuser", "password1")
    with pytest.raises(auth.UserExistsError):
        auth.create_user(None, "CASEUSER", "password1")
    assert auth.authenticate("caseuser", "password1") is not None
    assert auth.authenticate("CASEUSER", "password1") is not None
    # 显示名保留注册时的原始大小写。
    assert auth.authenticate("caseuser", "password1").username == "CaseUser"


def test_concurrent_duplicate_registration_no_dupes(fresh_db):
    """并发用同一用户名注册：只 1 个成功、其余抛 UserExistsError（不漏 IntegrityError），DB 绝无重复行。"""
    from concurrent.futures import ThreadPoolExecutor

    def reg(_):
        try:
            auth.create_user(None, "raceuser", "password1")
            return "ok"
        except auth.UserExistsError:
            return "dup"
        except Exception as exc:  # IntegrityError 等泄漏 = 失败
            return "ERR:" + type(exc).__name__

    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(reg, range(8)))

    assert results.count("ok") == 1, results          # 恰好 1 个成功
    assert all(r in ("ok", "dup") for r in results), results  # 无 IntegrityError/500 泄漏
    assert auth.count_users() == 1                    # DB 只 1 行，绝无重复


def test_invite_referral_flow(client):
    # 邀请人注册 → 拿到自带邀请码；被邀请人带码注册 → 建立 invited_by 关系，概览计数 +1。
    inviter = client.post(
        "/api/auth/register", json={"username": "inviter1", "password": "password1", "email": "inviter1@firm.com"}
    ).json()
    ih = {"Authorization": f"Bearer {inviter['access_token']}"}
    ov = client.get("/api/auth/invite", headers=ih).json()
    code = ov["code"]
    assert code and ov["invited_count"] == 0

    invitee = client.post(
        "/api/auth/register",
        json={"username": "invitee1", "password": "password1", "email": "invitee1@firm.com", "invite_code": code.lower()},
    )
    assert invitee.status_code == 200, invitee.text

    ov2 = client.get("/api/auth/invite", headers=ih).json()
    assert ov2["invited_count"] == 1
    assert any(u["username"] == "invitee1" for u in ov2["invited"])

    # 无效邀请码不报错、只是没有邀请人。
    assert client.post(
        "/api/auth/register",
        json={"username": "solo1", "password": "password1", "email": "solo1@firm.com", "invite_code": "ZZZZZZ"},
    ).status_code == 200


def test_register_collects_phone_and_email_optional(client):
    # 手机号/邮箱都收集、都不强制：带上则入库，可用任一登录态读回。
    reg = client.post(
        "/api/auth/register",
        json={"username": "withphone", "password": "password1", "phone": "13800138000"},
    )
    assert reg.status_code == 200, reg.text
    assert reg.json()["user"]["phone"] == "13800138000"
    # 重复手机号被拒（唯一）。
    assert (
        client.post(
            "/api/auth/register",
            json={"username": "other", "password": "password1", "phone": "138-0013-8000"},
        ).status_code
        == 409
    )
    # 明显非法手机号被拦。
    assert (
        client.post(
            "/api/auth/register",
            json={"username": "bad", "password": "password1", "phone": "abc"},
        ).status_code
        == 422
    )


def test_ai_analyze_requires_login(client):
    # AI 解读=登录网关：无令牌被拒（中间件 401 或 handler 配额 403 均为「拒绝」）；带合法令牌则越过网关。
    payload = {"title": "某利好", "content": "公司发布超预期财报。"}
    assert client.post("/api/news/ai-analyze", json=payload).status_code in (401, 403)
    assert client.post("/api/research/vision-analyze", json={"title": "x"}).status_code in (401, 403)

    token = client.post(
        "/api/auth/register",
        json={"username": "aiuser", "password": "password1", "email": "aiuser@firm.com"},
    ).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    assert client.post("/api/news/ai-analyze", json=payload, headers=h).status_code not in (401, 403)


def test_watchlist_per_account_and_guest_blocked(client):
    # 未登录读自选股 → 401（游客走前端 localStorage，不落库）。
    assert client.get("/api/me/watchlist").status_code == 401

    a = client.post("/api/auth/register", json={"username": "wl_a", "password": "password1", "email": "wl_a@firm.com"}).json()
    b = client.post("/api/auth/register", json={"username": "wl_b", "password": "password1", "email": "wl_b@firm.com"}).json()
    ha = {"Authorization": f"Bearer {a['access_token']}"}
    hb = {"Authorization": f"Bearer {b['access_token']}"}

    # 新账号无记录 → empty。
    assert client.get("/api/me/watchlist", headers=ha).json()["empty"] is True

    # A 存自己的列表（带重复，应去重）。
    saved = client.post(
        "/api/me/watchlist", headers=ha,
        json={"symbols": ["600519", "NVDA", "NVDA"], "names": {"600519": "贵州茅台"}},
    ).json()
    assert saved["symbols"] == ["600519", "NVDA"]

    # A 读回自己的；B 仍为空 —— 账号间互不影响。
    assert client.get("/api/me/watchlist", headers=ha).json()["symbols"] == ["600519", "NVDA"]
    assert client.get("/api/me/watchlist", headers=hb).json()["empty"] is True


def test_login_wrong_password(client):
    client.post(
        "/api/auth/register",
        json={"email": "u@firm.com", "username": "u", "password": "password1"},
    )
    assert (
        client.post(
            "/api/auth/login", json={"username": "u", "password": "nope"}
        ).status_code
        == 401
    )


def test_users_endpoint_requires_admin(client):
    # 注册 admin（首用户）+ analyst（次用户）。
    admin = client.post(
        "/api/auth/register",
        json={"email": "admin@firm.com", "username": "admin", "password": "password1"},
    ).json()
    analyst = client.post(
        "/api/auth/register",
        json={"email": "ana@firm.com", "username": "ana", "password": "password1"},
    ).json()

    admin_h = {"Authorization": f"Bearer {admin['access_token']}"}
    ana_h = {"Authorization": f"Bearer {analyst['access_token']}"}

    assert client.get("/api/auth/users", headers=admin_h).status_code == 200
    assert client.get("/api/auth/users", headers=ana_h).status_code == 403


# --------------------------------------------------------------------------- #
# 中间件强制（DEEPFOCUS_AUTH_REQUIRED=true）
# --------------------------------------------------------------------------- #
def test_enforced_mode_blocks_unauthenticated_api(client, monkeypatch):
    monkeypatch.setenv("DEEPFOCUS_AUTH_REQUIRED", "true")
    # 公开端点仍放行。
    assert client.get("/health").status_code == 200
    # 受保护 /api 路由无令牌 → 401。
    assert client.get("/api/auth/me").status_code == 401


def test_enforced_mode_allows_valid_token_and_enforces_rbac(client, monkeypatch):
    monkeypatch.setenv("DEEPFOCUS_AUTH_REQUIRED", "true")
    admin = client.post(
        "/api/auth/register",
        json={"email": "a@firm.com", "username": "adm", "password": "password1"},
    ).json()
    analyst = client.post(
        "/api/auth/register",
        json={"email": "b@firm.com", "username": "ana2", "password": "password1"},
    ).json()
    admin_h = {"Authorization": f"Bearer {admin['access_token']}"}
    ana_h = {"Authorization": f"Bearer {analyst['access_token']}"}

    # 合法令牌可访问。
    assert client.get("/api/auth/me", headers=admin_h).status_code == 200
    # 管理员路径：analyst 被中间件挡为 403。
    assert client.get("/api/auth/users", headers=ana_h).status_code == 403
    assert client.get("/api/auth/users", headers=admin_h).status_code == 200


# --------------------------------------------------------------------------- #
# 免费层白名单（"收回公开访问"：行情/快讯/研报列表/打点匿名可达；AI解读/原文/自选收口）
# --------------------------------------------------------------------------- #
def test_public_path_whitelist_classification():
    """免费层精确命中、收口层一律需鉴权。重点验证 wire(列表,免费) 不会前缀误放行 wire-file(原文,收口)。"""
    free = [
        "/api/market/quotes", "/api/market/search", "/api/market-dashboard",
        "/api/headlines", "/api/realtime/messages", "/api/realtime/messages/stream",
        "/api/research/wire", "/api/metrics/pageview", "/api/metrics/event",
        "/api/activity", "/api/qr",
        "/health", "/api/auth/login", "/api/auth/register",
    ]
    for path in free:
        assert auth.is_public_path(path) is True, f"{path} 应免登录可达"

    gated = [
        "/api/research/wire-file",        # 研报原文：绝不能被 /api/research/wire 误放行
        "/api/research/vision-analyze",   # AI 多模态解读
        "/api/news/ai-analyze",           # AI 快讯解读
        "/api/me/watchlist",              # 自选股（按账号）
        "/api/auth/users",                # 管理端
        "/api/agents/orchestrator-chat",  # AI 对话
    ]
    for path in gated:
        assert auth.is_public_path(path) is False, f"{path} 必须需要登录"


def test_enforced_mode_whitelist_end_to_end(client, monkeypatch):
    """中间件端到端：强制鉴权下白名单匿名可达、收口端点 401，且 wire(列表) 不放行 wire-file(原文)。"""
    monkeypatch.setenv("DEEPFOCUS_AUTH_REQUIRED", "true")
    # 免费层（快讯，本地存储、无外网）匿名可达。
    assert client.get("/api/realtime/messages", params={"limit": 1}).status_code == 200
    # 收口层匿名 → 401；wire-file 被中间件先行拦截（不触网），证明未被 /api/research/wire 白名单误放行。
    assert client.get("/api/research/wire-file", params={"file_id": "x"}).status_code == 401
    assert client.get("/api/me/watchlist").status_code == 401


def test_init_auth_refuses_dev_secret_when_enforced(tmp_path, monkeypatch):
    """开了强制鉴权却没设独立密钥（会回落源码内置开发密钥、JWT 可伪造）→ 启动期硬校验直接拒绝。"""
    monkeypatch.setenv("DEEPFOCUS_DATABASE_URL", f"sqlite:///{tmp_path / 'guard.sqlite3'}")
    monkeypatch.setenv("DEEPFOCUS_AUTH_REQUIRED", "true")
    monkeypatch.delenv("DEEPFOCUS_JWT_SECRET", raising=False)
    storage.reset_engine_for_tests()
    with pytest.raises(RuntimeError):
        auth.init_auth()
    storage.reset_engine_for_tests()
