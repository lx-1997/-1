"""Platform assistant integration with production handlers and temporary SQLite.

The watchlist/task APIRoutes come unchanged from main.py. JWTs, account/session
validation, ownership, Pydantic validation, and database stores are real. Only
the LLM boundary is scripted; lifespan/background workers never start.
"""
from __future__ import annotations

import asyncio
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from deepfocus_api import auth, platform_assistant as platform, storage, user_prefs
from deepfocus_api.platform_action_store import PlatformActionStore


def _tool(capability_id, **arguments):
    return SimpleNamespace(
        id="database_tool", type="function",
        function=SimpleNamespace(name="call_platform_capability", arguments=json.dumps({
            "capability_id": capability_id, **arguments,
        })),
    )


def _calls(*calls):
    return SimpleNamespace(content=None, tool_calls=list(calls))


def _answer():
    return SimpleNamespace(content="已收到接口回执。", tool_calls=None)


class ScriptedLLM:
    provider = "openai"
    provider_name = "isolated-script"
    model = "scripted-tools"

    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    async def _completion(self, payload, timeout_seconds):
        self.requests.append(copy.deepcopy(payload))
        assert self.script, "Unexpected extra model call"
        return SimpleNamespace(choices=[SimpleNamespace(message=self.script.pop(0))])


@pytest.fixture
def business_app(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPFOCUS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DEEPFOCUS_DATABASE_URL", f"sqlite:///{tmp_path / 'accounts.sqlite3'}")
    monkeypatch.setenv("DEEPFOCUS_JWT_SECRET", "isolated-platform-business-test-secret")
    monkeypatch.setenv("DEEPFOCUS_AUTH_REQUIRED", "true")
    monkeypatch.delenv("DEEPFOCUS_ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("DEEPFOCUS_ADMIN_PASSWORD", raising=False)
    storage.reset_engine_for_tests()
    auth.init_auth()

    # Import without lifespan: no market scanners, model calls, or task workers.
    from deepfocus_api import agent_runtime, main, weixin_bind
    monkeypatch.setattr(agent_runtime, "DB_PATH", tmp_path / "tasks.sqlite3")
    monkeypatch.setattr(weixin_bind, "get_by_user", lambda owner: None)
    agent_runtime.init_task_db()

    app = FastAPI()
    business_paths = {"/api/me/watchlist", "/api/agents/tasks",
                      "/api/agents/tasks/{task_id}", "/api/agents/tasks/{task_id}/cancel",
                      "/api/agents/tasks/{task_id}/retry"}
    app.router.routes.extend(route for route in main.app.routes if getattr(route, "path", None) in business_paths)
    assert len([route for route in app.routes if getattr(route, "path", None) in business_paths]) == 7

    async def guarded_legacy():
        raise AssertionError("Platform intent escaped to the research model path")

    for path in platform.CHAT_PATHS:
        app.add_api_route(path, guarded_legacy, methods=["POST"])
    llm = ScriptedLLM([])
    platform.install_platform_assistant(app, llm)
    action_path = str(tmp_path / "actions.sqlite3")
    app.state.platform_assistant.store = PlatformActionStore(action_path)
    app.add_middleware(auth.AuthMiddleware)
    client = TestClient(app)
    users = {name: auth.create_user(f"{name}@example.test", name, "test-password", role="analyst")
             for name in ("alice", "bob")}
    headers = {name: {"Authorization": "Bearer " + auth.create_access_token(user, sid=auth.rotate_session(user.id)),
                      "X-DF-Web": "isolated-test"}
               for name, user in users.items()}

    def script(*messages):
        replacement = ScriptedLLM(messages)
        app.state.platform_assistant.llm = replacement
        return replacement

    def chat(message, *, owner="alice", path="/api/agents/chat", request_id="test-request"):
        response = client.post(path, json={"message": message, "request_id": request_id}, headers=headers[owner])
        assert response.status_code == 200, response.text
        if not path.endswith("/stream"):
            return response.json()
        frames = []
        for block in response.text.split("\n\n"):
            for line in block.splitlines():
                if line.startswith("data: "):
                    frames.append(json.loads(line[6:]))
        finals = [frame for frame in frames if "platform_actions" in frame]
        assert finals, response.text
        return finals[-1]

    yield SimpleNamespace(app=app, client=client, users=users, headers=headers, script=script,
                          chat=chat, runtime=agent_runtime, action_path=action_path)
    client.close()
    storage.reset_engine_for_tests()


@pytest.mark.parametrize("path", sorted(platform.CHAT_PATHS))
def test_real_watchlist_add_and_request_replay_across_all_chat_transports(business_app, path):
    case = business_app
    user_prefs.set_watchlist(case.users["alice"].id, ["AAPL"], {"AAPL": "Apple"})
    llm = case.script(_calls(_tool("post:/api/me/watchlist", body={"symbols": ["MSFT"], "names": {"MSFT": "Microsoft"}})), _answer())
    result = case.chat("把 MSFT 添加到我的自选股", path=path)
    assert result["ok"] is True
    assert result["platform_actions"][0]["status"] == "completed"
    assert user_prefs.get_watchlist(case.users["alice"].id) == {
        "symbols": ["AAPL", "MSFT"], "names": {"AAPL": "Apple", "MSFT": "Microsoft"},
    }
    assert user_prefs.get_watchlist(case.users["bob"].id) is None
    request_count = len(llm.requests)
    # A new store instance models a process restart while keeping durable receipts.
    case.app.state.platform_assistant.store = PlatformActionStore(case.action_path)
    replay = case.chat("把 MSFT 添加到我的自选股", path=path)
    assert replay == result
    assert len(llm.requests) == request_count


def test_real_watchlist_removal_requires_owner_confirmation_and_replays_once(business_app):
    case = business_app
    uid = case.users["alice"].id
    user_prefs.set_watchlist(uid, ["AAPL", "MSFT"], {"AAPL": "Apple", "MSFT": "Microsoft"})
    case.script(_calls(_tool("post:/api/me/watchlist", body={"symbols": ["MSFT"], "names": {"MSFT": "Microsoft"}})), _answer())
    proposal = case.chat("从我的自选股移除 AAPL")
    action = proposal["platform_actions"][0]
    assert action["status"] == "needs_confirmation"
    assert user_prefs.get_watchlist(uid)["symbols"] == ["AAPL", "MSFT"]
    token_body = {"confirmation_token": action["confirmation_token"]}
    denied = case.client.post("/api/agents/platform-actions/confirm", json=token_body, headers=case.headers["bob"])
    assert denied.status_code == 409
    assert user_prefs.get_watchlist(uid)["symbols"] == ["AAPL", "MSFT"]
    # Another device adds a stock after the proposal; confirmation preserves it.
    user_prefs.set_watchlist(uid, ["AAPL", "MSFT", "NVDA"], {"AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "Nvidia"})
    confirmed = case.client.post("/api/agents/platform-actions/confirm", json=token_body, headers=case.headers["alice"])
    assert confirmed.status_code == 200
    assert confirmed.json()["ok"] is True
    assert user_prefs.get_watchlist(uid)["symbols"] == ["MSFT", "NVDA"]
    # Replay must not re-apply a removal to a newly re-added symbol.
    user_prefs.set_watchlist(uid, ["MSFT", "NVDA", "AAPL"], {})
    replay = case.client.post("/api/agents/platform-actions/confirm", json=token_body, headers=case.headers["alice"])
    assert replay.json() == confirmed.json()
    assert user_prefs.get_watchlist(uid)["symbols"] == ["MSFT", "NVDA", "AAPL"]


def test_real_account_revocation_blocks_pending_confirmation(business_app):
    case = business_app
    uid = case.users["alice"].id
    user_prefs.set_watchlist(uid, ["AAPL"], {})
    case.script(_calls(_tool("post:/api/me/watchlist", body={"symbols": [], "names": {}})), _answer())
    token = case.chat("清空我的自选股")["platform_actions"][0]["confirmation_token"]
    # The old JWT is still cryptographically valid, but its persisted session is revoked.
    with storage.session_scope() as session:
        session.get(auth.User, uid).session_id = "replacement-session"
    response = case.client.post("/api/agents/platform-actions/confirm", json={"confirmation_token": token}, headers=case.headers["alice"])
    assert response.status_code == 401
    assert user_prefs.get_watchlist(uid)["symbols"] == ["AAPL"]
    assert case.app.state.platform_assistant.store.claim(uid, token)[0] == "executing"


def test_real_task_create_owner_cancel_and_request_replay(business_app):
    case = business_app
    llm = case.script(_calls(_tool("post:/api/agents/tasks", body={"title": "隔离研究任务", "symbol": "MSFT"})), _answer())
    created = case.chat("创建一个 MSFT 后台研究任务", request_id="create-task")
    action = created["platform_actions"][0]
    assert action["status"] == "completed"
    task_id = action["result"]["id"]
    record = case.runtime.get_investment_task(task_id, owner_user_id=case.users["alice"].id)
    assert record.owner_user_id == case.users["alice"].id
    assert record.status == "pending"
    assert not case.runtime.is_worker_running()
    assert case.chat("创建一个 MSFT 后台研究任务", request_id="create-task") == created
    assert len(llm.requests) == 2
    assert len(case.runtime.list_investment_tasks(owner_user_id=case.users["alice"].id)) == 1
    assert case.client.get(f"/api/agents/tasks/{task_id}", headers=case.headers["bob"]).status_code == 404

    case.script(_calls(_tool("post:/api/agents/tasks/{task_id}/cancel", path_params={"task_id": task_id})), _answer())
    denied = case.chat(f"取消研究任务 {task_id}", owner="bob", request_id="bob-cancel")
    assert denied["ok"] is False
    assert denied["platform_actions"][0]["status"] == "failed"
    assert case.runtime.get_investment_task(task_id, owner_user_id=case.users["alice"].id).status == "pending"

    case.script(_calls(_tool("post:/api/agents/tasks/{task_id}/cancel", path_params={"task_id": task_id})), _answer())
    cancelled = case.chat(f"取消研究任务 {task_id}", request_id="alice-cancel")
    assert cancelled["ok"] is True
    final = case.runtime.get_investment_task(task_id, owner_user_id=case.users["alice"].id)
    assert final.status == "cancelled"
    assert final.completed_at is not None
    assert len([log for log in final.logs if log.message == "用户取消任务。"] ) == 1
    assert case.chat(f"取消研究任务 {task_id}", request_id="alice-cancel") == cancelled
    assert len(case.runtime.get_investment_task(task_id, owner_user_id=case.users["alice"].id).logs) == len(final.logs)


def test_model_request_id_extra_cannot_duplicate_one_requested_research_task(business_app):
    case = business_app
    first = {"title": "同一个隔离研究任务", "symbol": "MSFT", "request_id": "model-first"}
    second = {**first, "request_id": "model-second"}
    case.script(_calls(_tool("post:/api/agents/tasks", body=first)),
                _calls(_tool("post:/api/agents/tasks", body=second)), _answer())
    result = case.chat("创建一个 MSFT 后台研究任务")
    assert result["ok"] is True
    assert len(case.runtime.list_investment_tasks(owner_user_id=case.users["alice"].id)) == 1
    assert len(result["platform_actions"]) == 1


@pytest.mark.parametrize("body", [{"title": "invalid-priority", "priority": 7},
                                  {"title": "invalid-kind", "task_type": "send_money"},
                                  {"symbol": "MSFT"}])
def test_real_task_schema_errors_do_not_insert_rows(business_app, body):
    case = business_app
    case.script(_calls(_tool("post:/api/agents/tasks", body=body)), _answer())
    result = case.chat("创建一个后台研究任务")
    assert result["ok"] is False
    assert result["platform_actions"][0]["status"] == "failed"
    assert case.runtime.list_investment_tasks(owner_user_id=case.users["alice"].id) == []


def test_confirmation_preserves_concurrent_name_change_in_real_watchlist(business_app):
    case = business_app
    uid = case.users["alice"].id
    user_prefs.set_watchlist(uid, ["AAPL", "MSFT"], {"AAPL": "Apple", "MSFT": "Microsoft"})
    case.script(_calls(_tool("post:/api/me/watchlist", body={"symbols": ["MSFT"], "names": {"MSFT": "Microsoft"}})), _answer())
    token = case.chat("从我的自选股移除 AAPL")["platform_actions"][0]["confirmation_token"]
    user_prefs.set_watchlist(uid, ["AAPL", "MSFT"], {"AAPL": "Apple", "MSFT": "用户最新名称"})
    receipt = case.client.post("/api/agents/platform-actions/confirm", json={"confirmation_token": token}, headers=case.headers["alice"])
    assert receipt.status_code == 200
    assert receipt.json()["ok"] is True
    current = user_prefs.get_watchlist(uid)
    assert current["symbols"] == ["MSFT"]
    assert current["names"]["MSFT"] == "用户最新名称"


def _concurrent_adds(case, monkeypatch):
    """Pause only the SDK boundary after two real GETs to force a write race."""
    original_execute = platform.execute_capability

    async def experiment():
        reads = 0
        both_read = asyncio.Event()

        async def barrier_execute(*args, **kwargs):
            nonlocal reads
            result = await original_execute(*args, **kwargs)
            if args[3] == "get:/api/me/watchlist" and kwargs.get("internal_data"):
                reads += 1
                if reads == 2:
                    both_read.set()
                await asyncio.wait_for(both_read.wait(), timeout=3)
            return result

        monkeypatch.setattr(platform, "execute_capability", barrier_execute)
        request = Request({"type": "http", "method": "POST", "path": "/api/agents/chat", "query_string": b"",
                           "headers": [(b"authorization", case.headers["alice"]["Authorization"].encode())],
                           "client": ("127.0.0.1", 50000), "server": ("test", 80), "scheme": "http", "app": case.app})
        claims = auth.decode_token(case.headers["alice"]["Authorization"][7:])
        runs = []
        for symbol in ("MSFT", "NVDA"):
            llm = ScriptedLLM([_calls(_tool("post:/api/me/watchlist", body={"symbols": [symbol], "names": {}})), _answer()])
            assistant = platform.PlatformAssistant(case.app, llm, PlatformActionStore(case.action_path))
            runs.append(assistant.run(request, claims, {"message": f"把 {symbol} 添加到我的自选股"}))
        return await asyncio.gather(*runs)

    return asyncio.run(experiment())


def test_concurrent_assistant_adds_preserve_both_real_watchlist_updates(business_app, monkeypatch):
    case = business_app
    uid = case.users["alice"].id
    user_prefs.set_watchlist(uid, ["AAPL"], {"AAPL": "Apple"})
    results = _concurrent_adds(case, monkeypatch)
    assert all(result["ok"] and result["platform_actions"][0]["status"] == "completed" for result in results)
    assert set(user_prefs.get_watchlist(uid)["symbols"]) == {"AAPL", "MSFT", "NVDA"}


def test_concurrent_adds_at_capacity_have_one_success_and_one_failure_receipt(business_app, monkeypatch):
    case = business_app
    uid = case.users["alice"].id
    existing = [f"STOCK{i:03d}" for i in range(99)]
    user_prefs.set_watchlist(uid, existing, {})
    results = _concurrent_adds(case, monkeypatch)
    assert sum(result["ok"] for result in results) == 1
    assert sorted(result["platform_actions"][0]["status"] for result in results) == ["completed", "failed"]
    final = user_prefs.get_watchlist(uid)["symbols"]
    assert len(final) == 100
    assert set(existing).issubset(final)
    assert len(set(final) & {"MSFT", "NVDA"}) == 1


def test_model_supplied_delta_cannot_remove_unrequested_watchlist_symbols(business_app):
    case = business_app
    uid = case.users["alice"].id
    user_prefs.set_watchlist(uid, ["AAPL"], {"AAPL": "Apple"})
    case.script(_calls(_tool("post:/api/me/watchlist", body={
        "symbols": ["MSFT"], "names": {}, "delta": {"added": ["NVDA"], "removed": ["AAPL"], "names": {}},
    })), _answer())
    result = case.chat("把 MSFT 添加到我的自选股")
    assert result["ok"] is True
    assert user_prefs.get_watchlist(uid)["symbols"] == ["AAPL", "MSFT"]


@pytest.mark.parametrize("phrase", ["取消任务 {task_id}", "帮我取消刚才的任务"])
def test_natural_task_cancel_message_reaches_real_task_endpoint(business_app, phrase):
    case = business_app
    response = case.client.post("/api/agents/tasks", json={"title": "即将取消的隔离任务"}, headers=case.headers["alice"])
    assert response.status_code == 200
    task_id = response.json()["id"]
    case.script(_calls(_tool("post:/api/agents/tasks/{task_id}/cancel", path_params={"task_id": task_id})), _answer())
    result = case.chat(phrase.format(task_id=task_id))
    assert result["ok"] is True
    assert result["platform_actions"][0]["status"] == "completed"
    assert case.runtime.get_investment_task(task_id, owner_user_id=case.users["alice"].id).status == "cancelled"


@pytest.mark.parametrize("seed_exists", [False, True])
def test_real_delta_endpoint_merges_threaded_writers_and_keeps_accounts_separate(business_app, seed_exists):
    case = business_app
    if seed_exists:
        for owner in case.users:
            user_prefs.set_watchlist(case.users[owner].id, ["AAPL"], {"AAPL": "Apple"})

    writes = [(owner, f"{owner.upper()}{number:02d}") for owner in case.users for number in range(12)]

    def add_symbol(write):
        owner, symbol = write
        response = case.client.post("/api/me/watchlist", json={"delta": {
            "added": [symbol], "removed": [], "names": {symbol: f"name-{symbol}"},
        }}, headers=case.headers[owner])
        return owner, symbol, response.status_code, response.text

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(add_symbol, writes))
    assert all(status == 200 for _, _, status, _ in results), results
    for owner, user in case.users.items():
        expected = {symbol for writer, symbol in writes if writer == owner}
        if seed_exists:
            expected.add("AAPL")
        final = user_prefs.get_watchlist(user.id)
        assert set(final["symbols"]) == expected
        assert all(final["names"][symbol] == f"name-{symbol}" for writer, symbol in writes if writer == owner)


def test_real_delta_endpoint_over_capacity_rejects_the_whole_change(business_app):
    case = business_app
    uid = case.users["alice"].id
    existing = [f"STOCK{i:03d}" for i in range(100)]
    before = user_prefs.set_watchlist(uid, existing, {"STOCK000": "Original"})
    response = case.client.post("/api/me/watchlist", json={"delta": {
        "added": ["MSFT"], "removed": [], "names": {"STOCK000": "Uncommitted rename"},
    }}, headers=case.headers["alice"])
    assert response.status_code == 409
    assert user_prefs.get_watchlist(uid) == before


def test_real_delta_replay_is_idempotent_and_full_payload_remains_compatible(business_app):
    case = business_app
    uid = case.users["alice"].id
    initial = case.client.post("/api/me/watchlist", json={"symbols": ["AAPL", "MSFT"],
                              "names": {"AAPL": "Apple", "MSFT": "Microsoft"}}, headers=case.headers["alice"])
    assert initial.status_code == 200
    # A client-supplied full preview in the same payload must not overwrite the
    # authoritative delta, which is merged against the latest database row.
    operation = {"symbols": ["MSFT"], "names": {}, "delta": {
        "added": ["NVDA", "NVDA"], "removed": ["AAPL"], "names": {"NVDA": "Nvidia"},
    }}
    first = case.client.post("/api/me/watchlist", json=operation, headers=case.headers["alice"])
    assert first.status_code == 200
    replay = case.client.post("/api/me/watchlist", json=operation, headers=case.headers["alice"])
    assert replay.json() == first.json()
    assert user_prefs.get_watchlist(uid)["symbols"] == ["MSFT", "NVDA"]
    # Existing page callers still intentionally replace a full list.
    replacement = case.client.post("/api/me/watchlist", json={"symbols": ["TSLA"], "names": {"TSLA": "Tesla"}}, headers=case.headers["alice"])
    assert replacement.status_code == 200
    assert user_prefs.get_watchlist(uid) == {"symbols": ["TSLA"], "names": {"TSLA": "Tesla"}}
    assert user_prefs.get_watchlist(case.users["bob"].id) is None


@pytest.mark.parametrize("delta", [{"added": "MSFT"}, {"removed": {"symbol": "AAPL"}},
                                    {"added": [f"S{i}" for i in range(101)]}])
def test_invalid_delta_shape_or_size_cannot_modify_real_watchlist(business_app, delta):
    case = business_app
    uid = case.users["alice"].id
    before = user_prefs.set_watchlist(uid, ["AAPL"], {"AAPL": "Apple"})
    response = case.client.post("/api/me/watchlist", json={"delta": delta}, headers=case.headers["alice"])
    assert response.status_code == 422
    assert user_prefs.get_watchlist(uid) == before
