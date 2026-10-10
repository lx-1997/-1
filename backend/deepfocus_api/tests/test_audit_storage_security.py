"""Regression evidence for revoked sessions, object ownership, task leases and cursors."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from deepfocus_api import agent_runtime as ar, auth, backtest_engine as bt
from deepfocus_api import dulus_runtime as memory, realtime_messages as rm
from deepfocus_api import risk_management as risk, storage
from deepfocus_api.ownership import bind_owner, current_owner_id
from deepfocus_api.schemas import DulusMemoryCreateRequest, DulusRoundtableRequest, InvestmentTaskCreateRequest, RealtimeMessageCreateRequest


@pytest.fixture(autouse=True)
def isolated_stores(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPFOCUS_DATABASE_URL", f"sqlite:///{tmp_path / 'auth.sqlite3'}")
    monkeypatch.setenv("DEEPFOCUS_AUTH_REQUIRED", "true")
    monkeypatch.setenv("DEEPFOCUS_JWT_SECRET", "audit-test-secret-not-a-production-secret")
    for module, name in ((ar, "tasks"), (bt, "backtests"), (memory, "memory"), (rm, "messages"), (risk, "risk")):
        monkeypatch.setattr(module, "DB_PATH", tmp_path / f"{name}.sqlite3")
    storage.reset_engine_for_tests()
    storage.Base.metadata.create_all(storage.get_engine())
    yield
    ar._worker_leases.clear()
    storage.reset_engine_for_tests()


def _account(username="alice", role="analyst"):
    return auth.create_user(f"{username}@audit.example", username, "password-123", role=role)


def _headers(user):
    token = auth.create_access_token(user, auth.rotate_session(user.id))
    return {"Authorization": f"Bearer {token}"}


def _auth_app():
    app = FastAPI()
    app.add_middleware(auth.AuthMiddleware)

    @app.get("/api/risk/positions")
    async def positions(request: Request):
        return request.state.auth_claims

    @app.post("/api/mcp/servers")
    async def admin_only():
        return {"ok": True}

    return TestClient(app)  # No lifespan or background workers.


def test_revoked_token_rejected_by_global_middleware_and_role_refreshed():
    user = _account(role="admin")
    headers = _headers(user)
    client = _auth_app()
    assert client.post("/api/mcp/servers", headers=headers).status_code == 200
    with storage.session_scope() as session:
        session.get(auth.User, user.id).role = "analyst"
    assert client.post("/api/mcp/servers", headers=headers).status_code == 403
    assert client.get("/api/risk/positions", headers=headers).json()["role"] == "analyst"
    auth.reset_password(user.username, "changed-password")
    assert client.get("/api/risk/positions", headers=headers).status_code == 401


def test_deleted_or_disabled_account_and_missing_sid_rejected():
    user = _account()
    headers = _headers(user)
    no_sid = {"Authorization": f"Bearer {auth.create_access_token(user)}"}
    client = _auth_app()
    assert client.get("/api/risk/positions", headers=no_sid).status_code == 401
    with storage.session_scope() as session:
        session.get(auth.User, user.id).is_active = False
    assert client.get("/api/risk/positions", headers=headers).status_code == 401
    with storage.session_scope() as session:
        session.delete(session.get(auth.User, user.id))
    assert client.get("/api/risk/positions", headers=headers).status_code == 401


def test_account_check_database_error_fails_closed(monkeypatch):
    user = _account()
    headers = _headers(user)

    def failed_session():
        raise RuntimeError("temporary database outage")

    monkeypatch.setattr(auth, "session_scope", failed_session)
    assert _auth_app().get("/api/risk/positions", headers=headers).status_code == 401


def test_owner_context_applies_to_public_routes_and_resets_after_request():
    user = _account()
    app = FastAPI()
    app.add_middleware(auth.AuthMiddleware)

    @app.get("/api/realtime/messages")
    async def public_route():
        return {"owner": current_owner_id()}

    client = TestClient(app)
    assert client.get("/api/realtime/messages", headers=_headers(user)).json() == {"owner": user.id}
    assert client.get("/api/realtime/messages").json() == {"owner": None}
    assert current_owner_id() is None
    with bind_owner("outer"):
        with pytest.raises(RuntimeError), bind_owner("inner"):
            assert current_owner_id() == "inner"
            raise RuntimeError("intentional")
        assert current_owner_id() == "outer"
    assert current_owner_id() is None


def test_real_http_private_objects_and_revoked_jwt():
    """真实路由/签名 JWT；不进入 lifespan，不启动 worker 或调用行情/AI。"""
    from deepfocus_api import main

    alice, bob = _account("alice"), _account("bob")
    a_headers, b_headers = _headers(alice), _headers(bob)
    client = TestClient(main.app)
    created = [
        client.post("/api/risk/positions", headers=a_headers, json={"symbol": "NVDA", "entry_price": 100, "quantity": 1}),
        client.post("/api/backtest", headers=a_headers, json={"name": "private"}),
        client.post("/api/agents/tasks", headers=a_headers, json={"title": "private", "task_type": "investment_research"}),
    ]
    for response in created:
        assert response.status_code == 200, response.text
        assert response.json()["owner_user_id"] == alice.id
    position_id, backtest_id, task_id = [response.json()["id"] for response in created]
    for path, key in (("/api/risk/positions", "positions"), ("/api/backtest", "backtests"), ("/api/agents/tasks", "tasks")):
        assert client.get(path, headers=b_headers).json()[key] == []
        assert len(client.get(path, headers=a_headers).json()[key]) == 1

    forbidden_requests = [
        ("get", f"/api/risk/positions/{position_id}", {}),
        ("put", f"/api/risk/positions/{position_id}", {"json": {"notes": "stolen"}}),
        ("delete", f"/api/risk/positions/{position_id}", {}),
        ("post", f"/api/risk/positions/{position_id}/close", {"json": {"exit_price": 110}}),
        ("get", f"/api/backtest/{backtest_id}", {}),
        ("delete", f"/api/backtest/{backtest_id}", {}),
        ("post", f"/api/backtest/{backtest_id}/run", {}),
        ("get", f"/api/agents/tasks/{task_id}", {}),
        ("post", f"/api/agents/tasks/{task_id}/retry", {}),
        ("post", f"/api/agents/tasks/{task_id}/cancel", {}),
        ("get", f"/api/agents/tasks/{task_id}/events", {}),
    ]
    for method, path, kwargs in forbidden_requests:
        response = getattr(client, method)(path, headers=b_headers, **kwargs)
        assert response.status_code == 404, (method, path, response.text)
    assert risk.get_position(position_id)["notes"] == ""
    assert bt.get_backtest(backtest_id)["status"] == "pending"
    assert ar.get_investment_task(task_id).status == "pending"

    response = client.post("/api/dulus/memory", headers=a_headers, json={
        "scope": "user", "hall": "ai_chat", "title": "private", "content": "alice only", "tags": ["owner:bob"],
    })
    assert response.status_code == 200, response.text
    assert response.json()["owner_user_id"] == alice.id
    assert client.get("/api/dulus/memory?scope=user", headers=b_headers).json()["memories"] == []
    assert client.get("/api/dulus/memory?scope=user:alice", headers=b_headers).status_code == 403
    auth.reset_password("alice", "changed-password")
    for path in ("/api/risk/positions", "/api/backtest", "/api/agents/tasks", "/api/dulus/memory?scope=user"):
        assert client.get(path, headers=a_headers).status_code == 401, path


def test_worker_inherits_task_owner_context_and_clears_it(monkeypatch):
    task = ar.create_investment_task(InvestmentTaskCreateRequest(title="private", task_type="investment_research"), owner_user_id="alice")
    stop = asyncio.Event()

    async def process(record):
        assert record.id == task.id
        assert current_owner_id() == "alice"
        ar._update_task(record.id, status="completed")
        stop.set()

    monkeypatch.setattr(ar, "_process_task", process)
    asyncio.run(ar._worker_loop(stop))
    assert current_owner_id() is None
    assert ar.get_investment_task(task.id).status == "completed"


def test_concurrent_session_rotation_keeps_device_limit():
    user = _account()
    with ThreadPoolExecutor(max_workers=4) as pool:
        issued = list(pool.map(lambda _: auth.rotate_session(user.id), range(4)))
    with storage.session_scope() as session:
        retained = session.get(auth.User, user.id).session_id.split(",")
    assert len(retained) == auth._MAX_DEVICES
    assert len(set(retained)) == auth._MAX_DEVICES
    assert set(retained) <= set(issued)


def test_position_owner_limits_every_read_write_and_legacy_access():
    owned = risk.create_position("NVDA", entry_price=100, quantity=1, owner_user_id="alice")
    legacy = risk.create_position("AAPL", entry_price=100, quantity=1)
    assert risk.list_positions(owner_user_id="bob") == []
    assert risk.get_position(owned["id"], owner_user_id="bob") is None
    assert risk.update_position(owned["id"], owner_user_id="bob", notes="stolen") is None
    assert not risk.delete_position(owned["id"], owner_user_id="bob")
    assert risk.close_position(owned["id"], 110, owner_user_id="bob") is None
    assert risk.get_position(legacy["id"], owner_user_id="alice") is None
    assert risk.get_position(legacy["id"], owner_user_id="admin", is_admin=True)
    assert risk.update_position(owned["id"], owner_user_id="alice", tags=["updated"])["tags"] == ["updated"]
    risk.close_position(owned["id"], 110, owner_user_id="alice")
    assert len(risk.list_pnl_records(owner_user_id="alice")) == 1
    assert risk.list_pnl_records(owner_user_id="bob") == []
    assert risk.get_pnl_summary(owner_user_id="bob")["total_trades"] == 0
    assert risk.get_risk_summary(owner_user_id="bob")["closed_positions_count"] == 0


def test_concurrent_close_records_profit_once():
    position = risk.create_position("NVDA", entry_price=100, quantity=1, owner_user_id="alice")

    def close(_):
        try:
            risk.close_position(position["id"], 110, owner_user_id="alice")
            return "closed"
        except risk.PositionAlreadyClosedError:
            return "already_closed"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(close, range(2)))
    assert sorted(outcomes) == ["already_closed", "closed"]
    assert len(risk.list_pnl_records(owner_user_id="alice")) == 1


def test_close_uses_current_cost_when_concurrent_edit_wins_write_lock(monkeypatch):
    position = risk.create_position("NVDA", entry_price=100, quantity=1, owner_user_id="alice")
    original_connect = risk._connect
    edited = False

    class Connection:
        def __init__(self):
            self.conn = original_connect()

        def __enter__(self):
            self.conn.__enter__()
            return self

        def __exit__(self, *args):
            return self.conn.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.conn, name)

        def execute(self, sql, *args):
            nonlocal edited
            if sql == "BEGIN IMMEDIATE" and not edited:
                # 修改请求先获得写锁；平仓请求随后必须使用新的成本入账。
                edited = True
                with original_connect() as other:
                    other.execute("UPDATE positions SET entry_price=200 WHERE id=?", (position["id"],))
                    other.commit()
            return self.conn.execute(sql, *args)

    monkeypatch.setattr(risk, "init_risk_db", lambda: None)
    monkeypatch.setattr(risk, "_connect", Connection)
    risk.close_position(position["id"], 110, owner_user_id="alice")
    pnl = risk.list_pnl_records(owner_user_id="alice")[0]
    assert pnl["entry_price"] == 200
    assert pnl["realized_pnl"] == -90


def test_backtest_owner_and_atomic_claim():
    owned = bt.create_backtest("private", owner_user_id="alice")
    legacy = bt.create_backtest("legacy")
    assert bt.list_backtests(owner_user_id="bob") == []
    assert bt.get_backtest(owned["id"], owner_user_id="bob") is None
    assert bt.update_backtest(owned["id"], owner_user_id="bob", name="stolen") is None
    assert not bt.delete_backtest(owned["id"], owner_user_id="bob")
    assert not bt.claim_backtest(owned["id"], owner_user_id="bob")
    assert bt.get_backtest(legacy["id"], owner_user_id="alice") is None
    with ThreadPoolExecutor(max_workers=4) as pool:
        claimed = list(pool.map(lambda _: bt.claim_backtest(owned["id"], owner_user_id="alice"), range(4)))
    assert sum(claimed) == 1
    bt.update_backtest(owned["id"], status="completed")
    assert not bt.claim_backtest(owned["id"], owner_user_id="alice")


def test_task_owner_atomic_claim_and_stale_lease_cannot_finish_new_run():
    task = ar.create_investment_task(InvestmentTaskCreateRequest(title="private", task_type="investment_research"), owner_user_id="alice")
    assert ar.get_investment_task(task.id, owner_user_id="bob") is None
    assert ar.list_investment_tasks(owner_user_id="bob") == []
    assert ar.cancel_investment_task(task.id, owner_user_id="bob") is None
    assert ar.retry_investment_task(task.id, owner_user_id="bob") is None
    with ThreadPoolExecutor(max_workers=4) as pool:
        claimed = list(pool.map(lambda _: ar._claim_next_task(), range(4)))
    assert sum(item is not None for item in claimed) == 1
    with ar._connect() as conn:
        old_lease = conn.execute("SELECT lease_token FROM agent_tasks WHERE id=?", (task.id,)).fetchone()["lease_token"]
    ar.cancel_investment_task(task.id, owner_user_id="alice")
    ar.retry_investment_task(task.id, owner_user_id="alice")
    ar._claim_next_task()
    context = ar._active_lease.set((task.id, old_lease))
    try:
        assert not ar._update_task(task.id, status="completed", result_json='{"stale":true}')
    finally:
        ar._active_lease.reset(context)
    assert ar.get_investment_task(task.id).status == "running"


def test_worker_heartbeat_keeps_long_task_alive_and_cancel_stops_execution(monkeypatch):
    monkeypatch.setattr(ar, "RUNNING_TASK_STALE_SECONDS", 0.3)
    task = ar.create_investment_task(InvestmentTaskCreateRequest(title="long", task_type="investment_research"))
    ar._claim_next_task()
    lease = ar._worker_leases.pop(task.id)

    async def run():
        execution = asyncio.create_task(asyncio.sleep(2))
        heartbeat = asyncio.create_task(ar._maintain_task_lease(task.id, lease, execution))
        try:
            await asyncio.sleep(0.6)
            assert ar.recover_stale_running_tasks() == 0
            assert ar.get_investment_task(task.id).status == "running"
            ar.cancel_investment_task(task.id)
            await asyncio.sleep(0.2)
            assert execution.cancelled()
        finally:
            execution.cancel()
            heartbeat.cancel()
            await asyncio.gather(execution, heartbeat, return_exceptions=True)

    asyncio.run(run())


def test_lease_database_failure_stops_execution(monkeypatch):
    monkeypatch.setattr(ar, "RUNNING_TASK_STALE_SECONDS", 0.3)

    def unavailable():
        raise sqlite3.OperationalError("temporary database outage")

    monkeypatch.setattr(ar, "_connect", unavailable)

    async def run():
        execution = asyncio.create_task(asyncio.sleep(10))
        await ar._maintain_task_lease("task", "lease", execution)
        await asyncio.gather(execution, return_exceptions=True)
        assert execution.cancelled()

    asyncio.run(run())


def test_private_memory_scope_uses_owner_column_not_forged_tags():
    a = memory.create_dulus_memory(DulusMemoryCreateRequest(scope="user", hall="ai_chat", title="same", content="alice", tags=["owner:bob"]), owner_user_id="alice")
    b = memory.create_dulus_memory(DulusMemoryCreateRequest(scope="user", hall="ai_chat", title="same", content="bob"), owner_user_id="bob")
    for scope in ("user", "user:bob", "user:%"):
        assert [m.id for m in memory.list_dulus_memories(scope=scope, owner_user_id="alice").memories] == [a.id]
    assert [m.id for m in memory.list_dulus_memories(scope="user", owner_user_id="bob").memories] == [b.id]


def test_memory_migration_only_assigns_exact_existing_owner():
    alice = _account()
    with sqlite3.connect(memory.DB_PATH) as conn:
        conn.execute("CREATE TABLE dulus_memory(id TEXT PRIMARY KEY,scope TEXT,hall TEXT,title TEXT,content TEXT,tags_json TEXT,source TEXT,created_at TEXT)")
        conn.executemany("INSERT INTO dulus_memory VALUES(?,?,?,?,?,?,?,?)", [
            ("known", "user", "ai_chat", "known", "private", json.dumps(["owner:alice"]), "ai_chat", "2020-01-01"),
            ("unknown", "user", "ai_chat", "unknown", "private", json.dumps(["owner:nobody"]), "ai_chat", "2020-01-01"),
        ])
    memory.init_dulus_runtime_db()
    assert [m.id for m in memory.list_dulus_memories(scope="user", owner_user_id=alice.id).memories] == ["known"]
    with sqlite3.connect(memory.DB_PATH) as conn:
        assert conn.execute("SELECT owner_user_id FROM dulus_memory WHERE id='unknown'").fetchone()[0] is None


def test_roundtable_memory_is_private_and_legacy_summary_is_admin_only():
    memory.init_dulus_runtime_db()
    legacy = memory._save_memory(scope="session", hall="events", title="legacy private objective", content="private summary", tags=["roundtable"], source="roundtable")
    with bind_owner("alice"):
        memory._save_roundtable_memory(DulusRoundtableRequest(objective="private question"), "private answer", [], "research_more", 0.5)
    memory._save_roundtable_memory(DulusRoundtableRequest(objective="anonymous question"), "anonymous answer", [], "research_more", 0.5)
    own = memory.list_dulus_memories(scope="user", owner_user_id="alice").memories
    assert len(own) == 1 and own[0].source == "roundtable"
    assert own[0].owner_user_id == "alice"
    assert "private answer" in own[0].content
    assert memory.list_dulus_memories(scope="user", owner_user_id="bob").memories == []
    assert all(item.source != "roundtable" for item in memory.list_dulus_memories(scope="__shared__").memories)
    admin = memory.list_dulus_memories(scope="user", owner_user_id="admin", is_admin=True).memories
    assert {item.id for item in admin} == {legacy.id, own[0].id}


def test_cursor_drains_large_equal_timestamp_burst_without_loss():
    rm.init_realtime_message_db()
    stamp = "2026-01-01T00:00:00+00:00"
    with rm._connect() as conn:
        for index in range(450):
            conn.execute("INSERT INTO realtime_messages(id,title,content,topic,severity,tags_json,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?)", (f"m{index:04}", "news", "body", "快讯", "info", "[]", "{}", stamp))
        conn.commit()
    seen = []
    cursor = {}
    while True:
        page = rm.list_realtime_messages(order="asc", limit=200, **cursor)
        if not page:
            break
        seen.extend(message.id for message in page)
        cursor = {"after_created_at": page[-1].created_at, "after_id": page[-1].id}
    assert seen == [f"m{index:04}" for index in range(450)]
    assert len(set(seen)) == 450
