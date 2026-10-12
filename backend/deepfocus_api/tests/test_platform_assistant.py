"""Exercise platform operations through the real ASGI and authentication stack.

No LLM/network calls or production databases: scripted tool calls hit small
FastAPI routes, while AuthMiddleware still verifies and forwards each session.
"""
from __future__ import annotations

import asyncio
import copy
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from deepfocus_api import auth
from deepfocus_api import platform_assistant as platform
from deepfocus_api.platform_action_store import PlatformActionStore


CLAIMS = {
    "alice": {"sub": "alice", "role": "analyst"},
    "bob": {"sub": "bob", "role": "analyst"},
    "admin": {"sub": "admin", "role": "admin"},
}


def _cap(cap_id, path, method="GET", effect="read", **extra):
    return {
        "id": cap_id, "title": cap_id, "group": "test", "path": path,
        "method": method, "effect": effect, "enabled": True,
        "parameters": {"parameters": []}, **extra,
    }


CAPS = {
    item["id"]: item for item in [
        _cap("get:/api/me/watchlist", "/api/me/watchlist"),
        _cap("watchlist_save", "/api/me/watchlist", "POST", "write"),
        _cap("item_read", "/api/items/{item_id}", parameters={"parameters": [{"name": "limit", "in": "query"}]}),
        _cap("item_delete", "/api/items/{item_id}", "DELETE", "confirm"),
        _cap("profile_save", "/api/me/profile", "POST", "write"),
        _cap("memory_read", "/api/dulus/memory", parameters={"parameters": [{"name": "scope", "in": "query"}]}),
        _cap("memory_save", "/api/dulus/memory", "POST", "write"),
        _cap("admin_reset", "/api/admin/reset", "POST", "confirm", required_role="admin"),
        _cap("disabled", "/api/me/watchlist", enabled=False),
        _cap("interactive", "/api/me/watchlist", frontend_interaction=True),
    ]
}


def _tool(cap_id, call_id="call1", **arguments):
    return SimpleNamespace(
        id=call_id, type="function",
        function=SimpleNamespace(name="call_platform_capability", arguments=json.dumps({"capability_id": cap_id, **arguments})),
    )


def _named_tool(name, arguments, call_id="call1"):
    return SimpleNamespace(id=call_id, type="function", function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))


def _calls(*calls):
    return SimpleNamespace(content=None, tool_calls=list(calls))


def _answer(text="操作回执已返回。"):
    return SimpleNamespace(content=text, tool_calls=None)


def _plan(tool, arguments):
    return _answer(json.dumps({"tool": tool, "arguments": arguments}, ensure_ascii=False))


def _plan_answer(text):
    return _answer(json.dumps({"answer": text}, ensure_ascii=False))


class FakeLLM:
    provider = "openai"
    provider_name = "test-provider"
    model = "test-model"

    def __init__(self, script=()):
        self.script = list(script)
        self.requests = []

    async def _completion(self, payload, timeout_seconds):
        self.requests.append(copy.deepcopy(payload))
        if not self.script:
            raise AssertionError("Unexpected extra model round")
        response = self.script.pop(0)
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(choices=[SimpleNamespace(message=response)])


@pytest.fixture
def make_app(monkeypatch, tmp_path):
    # Keep the real authentication middleware and original route dependencies;
    # only replace the database-backed token verifier with known valid sessions.
    monkeypatch.setenv("DEEPFOCUS_AUTH_REQUIRED", "true")
    monkeypatch.setattr(auth, "decode_token", lambda token: CLAIMS.get(token))
    monkeypatch.setattr(auth, "validated_claims", lambda claims: claims)
    monkeypatch.setattr(auth, "session_is_current", lambda claims: claims is not None)

    def verified_claims(request):
        claims = auth.current_claims(request)
        if not claims:
            raise HTTPException(status_code=401, detail="请登录")
        return claims

    monkeypatch.setattr(platform, "require_current_user", verified_claims)
    monkeypatch.setattr(platform, "capability_catalog", lambda app: list(CAPS.values()))
    monkeypatch.setattr(platform, "get_capability", lambda app, cap_id: CAPS.get(cap_id))
    monkeypatch.setattr(platform, "boundary_summary", lambda app: {"test": True})
    instances = []

    def factory(script=(), *, check_quota=None, complete_quota=None):
        app = FastAPI()
        seen = []
        watchlists = {"alice": {"symbols": ["AAPL"], "names": {"AAPL": "Apple"}}, "bob": {"symbols": [], "names": {}}}
        llm = FakeLLM(script)

        def watchlist_apply(owner, body):
            # Isolated API boundary follows the real endpoint's delta-first
            # contract. Concurrent storage CAS is tested with real user_prefs;
            # this synchronous in-memory merge only supplies route responses.
            delta = body.get("delta")
            if delta is None:
                result = {"symbols": body.get("symbols", []), "names": body.get("names", {})}
            else:
                current = watchlists[owner]
                symbols = [symbol for symbol in current["symbols"] if symbol not in delta["removed"]]
                result = {"symbols": list(dict.fromkeys([*symbols, *delta["added"]])),
                          "names": {**current["names"], **delta["names"]}}
            watchlists[owner] = copy.deepcopy(result)
            return result

        @app.get("/api/me/watchlist")
        async def read_watchlist(request: Request):
            claims = auth.require_current_user(request)
            seen.append(("read", claims["sub"]))
            return watchlists[claims["sub"]]

        @app.post("/api/me/watchlist")
        async def save_watchlist(request: Request):
            claims = auth.require_current_user(request)
            body = await request.json()
            seen.append(("save", claims["sub"], body))
            result = watchlist_apply(claims["sub"], body)
            return {"saved": True, **result}

        @app.get("/api/items/{item_id}")
        async def read_item(item_id: str, request: Request, limit: int = 1):
            claims = auth.require_current_user(request)
            seen.append(("item", claims["sub"], item_id, limit))
            return {"id": item_id, "owner": claims["sub"], "bearer_seen": request.headers.get("authorization"),
                    "web_seen": request.headers.get("x-df-web"), "assistant_seen": request.headers.get("x-platform-assistant"),
                    "peer": list(request.client), "nested": {"access_token": "must-never-leak", "value": 1}}

        @app.delete("/api/items/{item_id}")
        async def delete_item(item_id: str, request: Request):
            claims = auth.require_current_user(request)
            seen.append(("delete", claims["sub"], item_id))
            if item_id == "not-owned":
                raise HTTPException(status_code=403, detail="记录不属于当前账号")
            return {"deleted": item_id}

        @app.post("/api/me/profile")
        async def save_profile(request: Request):
            claims = auth.require_current_user(request)
            seen.append(("profile", claims["sub"]))
            return {"saved": True}

        @app.get("/api/dulus/memory")
        async def read_memory(request: Request, scope: str = "shared"):
            claims = auth.require_current_user(request)
            seen.append(("memory-read", claims["sub"], scope))
            return {"scope": scope}

        @app.post("/api/dulus/memory")
        async def save_memory(request: Request):
            claims = auth.require_current_user(request)
            body = await request.json()
            seen.append(("memory-write", claims["sub"], body.get("scope", "shared")))
            return {"scope": body.get("scope", "shared")}

        @app.post("/api/admin/reset")
        async def admin_reset(request: Request):
            claims = auth.require_admin(request)
            seen.append(("reset", claims["sub"]))
            return {"reset": True}

        async def legacy_chat(request: Request):
            seen.append(("legacy", request.url.path))
            return {"legacy": True, "body": (await request.body()).decode()}

        for path in platform.CHAT_PATHS:
            app.add_api_route(path, legacy_chat, methods=["POST"])
        platform.install_platform_assistant(app, llm, check_quota=check_quota, complete_quota=complete_quota)
        app.state.platform_assistant.store = PlatformActionStore(str(tmp_path / f"actions-{len(instances)}.sqlite3"))
        # Production registration also adds authentication outside interception.
        app.add_middleware(auth.AuthMiddleware)
        app.add_middleware(CORSMiddleware, allow_origins=["https://app.test"], allow_methods=["*"], allow_headers=["*"])
        instances.append(app)
        return SimpleNamespace(app=app, client=TestClient(app), seen=seen, watchlists=watchlists,
                               watchlist_apply=watchlist_apply, llm=llm)

    return factory


def _headers(owner="alice"):
    return {"Authorization": f"Bearer {owner}", "X-DF-Web": "browser-test"}


def _request(app, owner="alice"):
    return Request({"type": "http", "method": "POST", "path": "/api/agents/chat", "query_string": b"",
                    "headers": [(b"authorization", f"Bearer {owner}".encode()), (b"x-df-web", b"browser-test")],
                    "client": ("198.51.100.9", 43000), "server": ("test", 80), "scheme": "http", "app": app})


def _execute(fixture, cap_id, arguments, *, owner="alice"):
    return asyncio.run(platform.execute_capability(fixture.app, _request(fixture.app, owner), CLAIMS[owner], cap_id, arguments))


def _chat(fixture, message="我的自选股有哪些？", path="/api/agents/chat", owner="alice", **body):
    return fixture.client.post(path, headers=_headers(owner), json={"message": message, **body})


def test_capability_runs_original_auth_stack_and_forwards_request_context(make_app):
    fixture = make_app()
    result = _execute(fixture, "item_read", {"path_params": {"item_id": "i1"}, "query": {"limit": 3}})
    assert result["ok"] is True
    assert result["data"]["owner"] == "alice"
    assert result["data"]["bearer_seen"] == "Bearer alice"
    assert result["data"]["web_seen"] == "browser-test"
    assert result["data"]["assistant_seen"] == "1"
    assert result["data"]["peer"] == ["198.51.100.9", 43000]
    assert result["data"]["nested"] == {"value": 1}
    assert fixture.seen == [("item", "alice", "i1", 3)]


def test_supplied_claims_do_not_bypass_original_endpoint_auth(make_app):
    fixture = make_app()
    result = asyncio.run(platform.execute_capability(fixture.app, _request(fixture.app, "invalid"), CLAIMS["alice"], "get:/api/me/watchlist", {}))
    assert result["ok"] is False
    assert result["status"] == 401
    assert fixture.seen == []


@pytest.mark.parametrize("cap_id", ["not-registered", "disabled", "interactive"])
def test_only_registered_enabled_noninteractive_capabilities_execute(make_app, cap_id):
    fixture = make_app()
    with pytest.raises(ValueError):
        _execute(fixture, cap_id, {})
    assert fixture.seen == []


@pytest.mark.parametrize("record_id", ["../secret", "..", ".", "/api/admin/reset", "x\\y", "%2e%2e", "x?owner=bob", "x#fragment", "", "a" * 161])
def test_path_parameters_cannot_escape_capability_template(make_app, record_id):
    fixture = make_app()
    with pytest.raises(ValueError):
        _execute(fixture, "item_read", {"path_params": {"item_id": record_id}})
    assert fixture.seen == []


@pytest.mark.parametrize("arguments", [{"url": "http://evil.test"}, {"method": "DELETE"}, {"path_params": {}},
                                          {"path_params": {"item_id": "i1", "extra": "x"}},
                                          {"path_params": {"item_id": "i1"}, "query": {"unregistered": "x"}}])
def test_extraneous_execution_arguments_are_rejected(make_app, arguments):
    fixture = make_app()
    with pytest.raises(ValueError):
        _execute(fixture, "item_read", arguments)
    assert fixture.seen == []


@pytest.mark.parametrize("identity_key", ["owner_id", "owner_user_id", "is_admin", "auth_claims", "authorization", "api_key", "access_token", "password", "token"])
def test_nested_identity_and_credentials_cannot_be_spoofed(make_app, identity_key):
    fixture = make_app()
    with pytest.raises(ValueError, match="身份"):
        _execute(fixture, "watchlist_save", {"body": {"items": [{identity_key: "bob"}]}})
    assert fixture.seen == []


def test_admin_capabilities_hidden_and_rejected_for_normal_user(make_app):
    fixture = make_app()
    response = fixture.client.get("/api/agents/platform/capabilities", headers=_headers())
    assert response.status_code == 200
    assert "admin_reset" not in {cap["id"] for cap in response.json()["capabilities"]}
    with pytest.raises(HTTPException) as denied:
        _execute(fixture, "admin_reset", {})
    assert denied.value.status_code == 403
    admin_catalog = fixture.client.get("/api/agents/platform/capabilities", headers=_headers("admin")).json()
    assert "admin_reset" in {cap["id"] for cap in admin_catalog["capabilities"]}
    assert fixture.seen == []


def test_explicit_reversible_write_executes_once_even_when_model_repeats(make_app):
    # The model supplies only its new symbol. The executor preserves the
    # existing portfolio even if the model omits it from the replacement body.
    arguments = {"body": {"symbols": ["NVDA"], "names": {"NVDA": "Nvidia"}}}
    fixture = make_app([_calls(_tool("watchlist_save", **arguments)), _calls(_tool("watchlist_save", call_id="call2", **arguments)), _answer()])
    result = _chat(fixture, "添加 NVDA 到我的自选股").json()
    assert result["ok"] is True
    assert len(result["platform_actions"]) == 1
    assert result["platform_actions"][0]["status"] == "completed"
    assert len([item for item in fixture.seen if item[0] == "save"]) == 1
    assert fixture.watchlists["alice"] == {"symbols": ["AAPL", "NVDA"], "names": {"AAPL": "Apple", "NVDA": "Nvidia"}}
    assert fixture.watchlists["bob"] == {"symbols": [], "names": {}}
    assert fixture.llm.requests[-1]["messages"][-1]["role"] == "tool"
    assert json.loads(fixture.llm.requests[-1]["messages"][-1]["content"])["ok"] is True


@pytest.mark.parametrize("objective", ["我的自选股有哪些？", "如何添加股票到自选股？", "不要修改我的自选股", "如果添加 NVDA 到自选股会怎样？"])
def test_read_tutorial_negative_and_hypothetical_messages_require_confirmation_for_write(make_app, objective):
    fixture = make_app([_calls(_tool("watchlist_save", body={"symbols": ["NVDA"]})), _answer()])
    result = _chat(fixture, objective).json()
    assert result["platform_actions"][0]["status"] == "needs_confirmation"
    assert not [item for item in fixture.seen if item[0] == "save"]
    assert fixture.watchlists["alice"]["symbols"] == ["AAPL"]


def test_live_write_authorization_does_not_cover_unrelated_business_domain(make_app):
    fixture = make_app([_calls(_tool("profile_save", body={"display_name": "unexpected"})), _answer()])
    result = _chat(fixture, "添加 NVDA 到我的自选股").json()
    assert result["platform_actions"][0]["status"] == "needs_confirmation"
    assert fixture.seen == []


def test_confirmation_owner_binding_and_replays_return_receipt_without_second_write(make_app):
    fixture = make_app([_calls(_tool("item_delete", path_params={"item_id": "i1"})), _answer()])
    proposal = _chat(fixture, "删除证据库记录 i1").json()
    action = proposal["platform_actions"][0]
    assert action["status"] == "needs_confirmation"
    assert fixture.seen == []
    payload = {"confirmation_token": action["confirmation_token"]}
    denied = fixture.client.post("/api/agents/platform-actions/confirm", headers=_headers("bob"), json=payload)
    assert denied.status_code == 409
    assert fixture.seen == []
    receipt = fixture.client.post("/api/agents/platform-actions/confirm", headers=_headers(), json=payload)
    assert receipt.status_code == 200
    assert receipt.json()["platform_actions"][0]["status"] == "completed"
    replay = fixture.client.post("/api/agents/platform-actions/confirm", headers=_headers(), json=payload)
    assert replay.json() == receipt.json()
    assert fixture.seen == [("delete", "alice", "i1")]


def test_confirmation_preserves_original_endpoint_ownership_failure(make_app):
    fixture = make_app([_calls(_tool("item_delete", path_params={"item_id": "not-owned"})), _answer()])
    action = _chat(fixture, "删除证据库中的 not-owned").json()["platform_actions"][0]
    payload = {"confirmation_token": action["confirmation_token"]}
    receipt = fixture.client.post("/api/agents/platform-actions/confirm", headers=_headers(), json=payload).json()
    assert receipt["ok"] is False
    assert receipt["platform_actions"][0]["status"] == "failed"
    assert "不属于" in receipt["answer"]
    assert fixture.client.post("/api/agents/platform-actions/confirm", headers=_headers(), json=payload).json() == receipt
    assert len(fixture.seen) == 1


def test_confirmed_watchlist_change_preserves_items_added_after_proposal(make_app):
    fixture = make_app([_calls(_tool("watchlist_save", body={"symbols": []})), _answer()])
    action = _chat(fixture, "从我的自选股中移除 AAPL").json()["platform_actions"][0]
    assert action["status"] == "needs_confirmation"
    assert not [item for item in fixture.seen if item[0] == "save"]
    # A second browser changes the account while the card waits for a click.
    fixture.watchlists["alice"] = {"symbols": ["AAPL", "AMD"], "names": {"AAPL": "Apple", "AMD": "AMD"}}
    response = fixture.client.post("/api/agents/platform-actions/confirm", headers=_headers(), json={"confirmation_token": action["confirmation_token"]})
    assert response.json()["ok"] is True
    assert fixture.watchlists["alice"]["symbols"] == ["AMD"]
    assert fixture.watchlists["alice"]["names"]["AMD"] == "AMD"


def test_add_preserves_existing_watchlist_larger_than_model_preview_limit(make_app):
    fixture = make_app([_calls(_tool("watchlist_save", body={"symbols": ["NVDA"]})), _answer()])
    previous = [f"S{i:02}" for i in range(70)]
    fixture.watchlists["alice"] = {"symbols": previous, "names": {symbol: symbol for symbol in previous}}
    response = _chat(fixture, "添加 NVDA 到我的自选股")
    assert response.json()["ok"] is True
    assert fixture.watchlists["alice"]["symbols"] == [*previous, "NVDA"]


def test_parallel_claims_admit_one_executor_only(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    store = PlatformActionStore(str(tmp_path / "parallel.sqlite3"))
    token = store.propose("alice", {"operation": "delete"})

    def claim(_):
        try:
            return store.claim("alice", token)[0]
        except ValueError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(claim, range(8)))
    assert statuses.count("executing") == 1
    assert statuses.count("rejected") == 7


def test_action_store_atomic_claim_expires_and_never_retries_inflight_operation(tmp_path, monkeypatch):
    store = PlatformActionStore(str(tmp_path / "pending.sqlite3"))
    now = [1000.0]
    monkeypatch.setattr("deepfocus_api.platform_action_store.time.time", lambda: now[0])
    token = store.propose("alice", {"value": 1})
    assert store.claim("alice", token) == ("executing", {"value": 1})
    with pytest.raises(ValueError, match="已提交"):
        store.claim("alice", token)
    expired = store.propose("alice", {"value": 2})
    now[0] += 601
    with pytest.raises(ValueError, match="过期"):
        store.claim("alice", expired)
    receipt = {"ok": True, "operation": "completed"}
    store.finish("alice", token, receipt)
    assert store.claim("alice", token) == ("completed", receipt)


@pytest.mark.parametrize("path", sorted(platform.CHAT_PATHS))
def test_research_chat_passes_through_with_exact_body_and_no_llm_calls(make_app, path):
    fixture = make_app()
    raw = b'{"message":"Analyze AAPL earnings","history":[],"extra":{"x":1}}'
    response = fixture.client.post(path, headers={**_headers(), "Content-Type": "application/json"}, content=raw)
    assert response.json() == {"legacy": True, "body": raw.decode()}
    assert fixture.seen == [("legacy", path)]
    assert fixture.llm.requests == []


@pytest.mark.parametrize("raw", [b"{broken", b"[]", b"null"])
def test_invalid_or_nonobject_json_body_is_replayed_to_original_handler(make_app, raw):
    fixture = make_app()
    response = fixture.client.post("/api/agents/chat", headers=_headers(), content=raw)
    assert response.json()["body"] == raw.decode()
    assert fixture.llm.requests == []


@pytest.mark.parametrize("path", ["/api/agents/chat/stream", "/api/agents/tool-research/stream"])
def test_sse_platform_results_keep_legacy_final_projection(make_app, path):
    fixture = make_app([_calls(_tool("get:/api/me/watchlist")), _answer("你的自选包括 AAPL。")])
    response = _chat(fixture, path=path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = []
    for block in response.text.strip().split("\n\n"):
        kind = next(line.removeprefix("event: ") for line in block.splitlines() if line.startswith("event: "))
        data = json.loads(next(line.removeprefix("data: ") for line in block.splitlines() if line.startswith("data: ")))
        events.append((kind, data))
    kinds = [kind for kind, data in events]
    assert kinds[:3] == ["status", "tool_start", "tool_result"]
    final = next(data for kind, data in events if kind == "final")
    assert final["answer"] == final["content"] == "你的自选包括 AAPL。"
    assert final["provider"] == "test-provider"
    assert final["handled_inline"] is True
    if path == "/api/agents/chat/stream":
        assert "delta" in kinds and "done" in kinds
        assert next(data for kind, data in events if kind == "done")["answer"] == final["answer"]
    assert fixture.seen == [("read", "alice")]


def test_anonymous_platform_intent_and_confirm_are_rejected(make_app):
    fixture = make_app()
    assert fixture.client.post("/api/agents/chat", json={"message": "我的自选股有哪些？"}).status_code == 401
    assert fixture.client.post("/api/agents/platform-actions/confirm", json={"confirmation_token": "x"}).status_code == 401
    assert fixture.llm.requests == []


def test_query_string_message_enters_platform_assistant_for_legacy_research_clients(make_app):
    fixture = make_app([_calls(_tool("get:/api/me/watchlist")), _answer("AAPL")])
    response = fixture.client.post("/api/agents/tool-research", headers=_headers(), params={"message": "我的自选股有哪些？"}, json={})
    assert response.status_code == 200
    assert response.json()["answer"] == "AAPL"
    assert fixture.seen == [("read", "alice")]


def test_navigation_returns_configured_view_without_mutating_records(make_app):
    fixture = make_app([_calls(_named_tool("navigate_platform", {"view": "stocks", "symbol": "NVDA"})), _answer("已打开观察池。")])
    response = _chat(fixture, "打开自选股页面")
    assert response.json()["navigation"] == {"view": "stocks", "symbol": "NVDA"}
    assert response.json()["platform_actions"] == []
    assert fixture.seen == []


def test_openapi_and_cors_survive_platform_middleware_installation(make_app):
    fixture = make_app()
    schema = fixture.client.get("/openapi.json")
    assert schema.status_code == 200
    assert "/api/agents/platform-actions/confirm" in schema.json()["paths"]
    preflight = fixture.client.options("/api/agents/chat", headers={"Origin": "https://app.test", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type"})
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "https://app.test"
    response = fixture.client.get("/api/agents/platform/capabilities", headers={**_headers(), "Origin": "https://app.test"})
    assert response.headers["access-control-allow-origin"] == "https://app.test"
    assert fixture.llm.requests == []


def test_successful_inline_operation_uses_quota_callbacks_once(make_app):
    seen = []
    fixture = make_app([_calls(_tool("get:/api/me/watchlist")), _answer()], check_quota=lambda claims, request: seen.append(("reserve", claims["sub"])) or "lease", complete_quota=lambda lease: seen.append(("complete", lease)))
    assert _chat(fixture).json()["ok"] is True
    assert seen == [("reserve", "alice"), ("complete", "lease")]


def test_model_failure_after_write_keeps_receipt_and_does_not_fall_back(make_app):
    fixture = make_app([_calls(_tool("watchlist_save", body={"symbols": ["AAPL", "NVDA"]})), RuntimeError("model disconnected")])
    result = _chat(fixture, "添加 NVDA 到我的自选股").json()
    assert result["ok"] is False
    assert result["platform_actions"][0]["status"] == "completed"
    assert len([item for item in fixture.seen if item[0] == "save"]) == 1
    assert not [item for item in fixture.seen if item[0] == "legacy"]


@pytest.mark.parametrize("cap_id,arguments", [("memory_read", {}), ("memory_save", {"body": {"content": "my note"}})])
def test_memory_default_is_forced_to_personal_scope(make_app, cap_id, arguments):
    fixture = make_app()
    response = _execute(fixture, cap_id, arguments)
    assert response["ok"] is True
    assert response["data"]["scope"] == "user"
    assert fixture.seen[0][1:] == ("alice", "user")


@pytest.mark.parametrize("cap_id,arguments", [("memory_read", {"query": {"scope": "shared"}}),
                                               ("memory_read", {"query": {"scope": "session"}}),
                                               ("memory_save", {"body": {"scope": "shared", "content": "note"}}),
                                               ("memory_save", {"body": {"scope": "session", "content": "note"}})])
def test_memory_cannot_escape_personal_scope_through_tool_parameters(make_app, cap_id, arguments):
    fixture = make_app()
    with pytest.raises(ValueError, match="个人记忆"):
        _execute(fixture, cap_id, arguments)
    assert fixture.seen == []


def test_durable_request_id_replay_returns_same_write_receipt_without_calling_model(make_app):
    fixture = make_app([_calls(_tool("watchlist_save", body={"symbols": ["NVDA"]})), _answer()])
    first = _chat(fixture, "添加 NVDA 到我的自选股", request_id="retry-safe-1")
    replay = _chat(fixture, "添加 NVDA 到我的自选股", request_id="retry-safe-1")
    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    assert len(fixture.llm.requests) == 2
    assert len([item for item in fixture.seen if item[0] == "save"]) == 1


def test_reusing_request_id_with_different_operation_fails_before_second_execution(make_app):
    fixture = make_app([_calls(_tool("watchlist_save", body={"symbols": ["NVDA"]})), _answer()])
    assert _chat(fixture, "添加 NVDA 到我的自选股", request_id="bound-request").status_code == 200
    response = _chat(fixture, "添加 AMD 到我的自选股", request_id="bound-request")
    assert response.status_code == 409
    assert "不同的操作" in response.json()["detail"]
    assert len(fixture.llm.requests) == 2
    assert len([item for item in fixture.seen if item[0] == "save"]) == 1


def test_request_id_store_is_owner_bound_and_cannot_reexecute_inflight_request(tmp_path):
    store = PlatformActionStore(str(tmp_path / "request-replay.sqlite3"))
    assert store.begin_run("alice", "r1", "fingerprint") is None
    with pytest.raises(ValueError, match="已提交"):
        store.begin_run("alice", "r1", "fingerprint")
    assert store.begin_run("bob", "r1", "fingerprint") is None
    store.finish_run("alice", "r1", {"answer": "Alice receipt"})
    assert store.begin_run("alice", "r1", "fingerprint") == {"answer": "Alice receipt"}
    with pytest.raises(ValueError, match="已提交"):
        store.begin_run("bob", "r1", "fingerprint")


@pytest.mark.parametrize("path", ["/api/agents/chat/stream", "/api/agents/tool-research/stream"])
def test_request_id_sse_replay_keeps_existing_client_payload_contract(make_app, path):
    fixture = make_app([_calls(_tool("get:/api/me/watchlist")), _answer("你的自选包括 AAPL。")])
    first = _chat(fixture, path=path, request_id="stream-retry")
    replay = _chat(fixture, path=path, request_id="stream-retry")
    assert replay.status_code == 200
    assert replay.headers["content-type"].startswith("text/event-stream")
    data = [json.loads(line.removeprefix("data: ")) for line in replay.text.splitlines() if line.startswith("data: ")]
    if path == "/api/agents/chat/stream":
        assert data[0] == {"delta": "你的自选包括 AAPL。"}
        assert data[-1]["done"] is True
    else:
        assert "event: final" in replay.text
    assert data[-1]["answer"] == "你的自选包括 AAPL。"
    assert fixture.seen == [("read", "alice")]
    assert len(fixture.llm.requests) == 2


@pytest.mark.parametrize("raw", [b"{broken", b"[]", b"null"])
def test_malformed_confirmation_body_is_client_error(make_app, raw):
    fixture = make_app()
    response = fixture.client.post("/api/agents/platform-actions/confirm", headers=_headers(), content=raw)
    assert response.status_code == 400
    assert fixture.seen == []


def test_assistant_navigation_covers_all_configured_workspace_views():
    source = Path(__file__).resolve().parents[3] / "src/config/workspaces.ts"
    expected = set(re.findall(r"\bview:\s*['\"]([^'\"]+)['\"]", source.read_text()))
    assert set(platform.VIEWS) == expected


def test_native_prose_only_model_falls_back_to_json_discovery_and_real_read(make_app):
    fixture = make_app([
        _answer("你的自选股已经查好了。"),
        _plan("discover_platform_capabilities", {"group": "test", "query": "watchlist"}),
        _plan("call_platform_capability", {"capability_id": "get:/api/me/watchlist"}),
        _plan_answer("你的自选股包括 AAPL。"),
    ])
    response = _chat(fixture)
    result = response.json()
    assert response.status_code == 200
    assert result["ok"] is True
    assert result["answer"] == "你的自选股包括 AAPL。"
    assert [item["tool"] for item in result["tool_trace"]] == ["discover_platform_capabilities", "call_platform_capability"]
    assert fixture.seen == [("read", "alice")]
    assert len(fixture.llm.requests) == 4
    assert fixture.llm.requests[0]["tools"]
    for payload in fixture.llm.requests[1:]:
        assert "tools" not in payload and "tool_choice" not in payload
        assert all(message["role"] != "tool" and "tool_calls" not in message and "tool_call_id" not in message for message in payload["messages"])
    # The compatibility protocol supplies actual server results as user-role
    # messages rather than forged native tool-call records.
    final_messages = fixture.llm.requests[-1]["messages"]
    assert any(message["role"] == "user" and "AAPL" in str(message.get("content", "")) for message in final_messages)


def test_bare_native_navigation_json_falls_back_to_executable_json_plan(make_app):
    fixture = make_app([
        _answer('{"view":"backtest-center"}'),
        _plan("navigate_platform", {"view": "backtest-center"}),
        _plan_answer("已打开回测中心。"),
    ])
    result = _chat(fixture, "打开回测中心").json()
    assert result["ok"] is True
    assert result["navigation"]["view"] == "backtest-center"
    assert result["platform_actions"] == []
    assert fixture.seen == []
    assert len(fixture.llm.requests) == 3
    assert [item["tool"] for item in result["tool_trace"]] == ["navigate_platform"]


def test_json_compatibility_write_keeps_preservation_guards_and_deduplicates_model_repeats(make_app):
    arguments = {"capability_id": "watchlist_save", "body": {"symbols": ["NVDA"]}}
    fixture = make_app([
        _answer("已添加到你的自选股。"),
        _plan("call_platform_capability", arguments),
        _plan("call_platform_capability", arguments),
        _plan_answer("已添加 NVDA。"),
    ])
    first = _chat(fixture, "添加 NVDA 到我的自选股", request_id="json-write-replay")
    replay = _chat(fixture, "添加 NVDA 到我的自选股", request_id="json-write-replay")
    result = first.json()
    assert result["ok"] is True
    assert len(result["platform_actions"]) == 1
    assert result["platform_actions"][0]["status"] == "completed"
    assert fixture.watchlists["alice"]["symbols"] == ["AAPL", "NVDA"]
    assert len([item for item in fixture.seen if item[0] == "save"]) == 1
    assert replay.json() == result
    assert len(fixture.llm.requests) == 4


def test_json_compatibility_write_outside_live_authorization_still_requires_confirmation(make_app):
    fixture = make_app([
        _answer("已更新个人资料。"),
        _plan("call_platform_capability", {"capability_id": "profile_save", "body": {"display_name": "unexpected"}}),
        _plan_answer("已更新个人资料。"),
    ])
    result = _chat(fixture, "添加 NVDA 到我的自选股").json()
    assert result["platform_actions"][0]["status"] == "needs_confirmation"
    assert "等待确认" in result["answer"]
    assert fixture.seen == []


@pytest.mark.parametrize("plan", [
    '{"tool":"http_request","arguments":{"url":"http://evil.test"}}',
    '{"tool":"call_platform_capability","arguments":{"capability_id":"unregistered"}}',
    '{"tool":"call_platform_capability","arguments":[]}',
    '{"tool":"call_platform_capability","arguments":{"capability_id":"watchlist_save","body":{"symbols":["NVDA"],"owner_id":"bob"}}}',
    '{"view":"backtest-center"}',
    '[{"tool":"call_platform_capability","arguments":{"capability_id":"watchlist_save"}}]',
    '{"tool":',
])
def test_invalid_or_unregistered_json_compatibility_plans_cannot_mutate_records(make_app, plan):
    fixture = make_app([_answer("已添加到自选股。"), _answer(plan), _plan_answer("请提供准确的操作对象和参数。")])
    result = _chat(fixture, "添加 NVDA 到我的自选股").json()
    assert result["ok"] is False
    assert result["platform_actions"] == []
    assert fixture.watchlists["alice"]["symbols"] == ["AAPL"]
    assert not [item for item in fixture.seen if item[0] in {"save", "profile", "delete", "reset"}]


@pytest.mark.parametrize("completion", ["已添加 NVDA 到自选股。", '{"answer":"已添加 NVDA 到自选股。"}'])
def test_compatibility_final_completion_claim_without_receipt_is_rejected(make_app, completion):
    fixture = make_app([_answer("已完成。"), _answer(completion), _plan_answer("已完成。")])
    result = _chat(fixture, "添加 NVDA 到我的自选股").json()
    assert result["ok"] is False
    assert result["platform_actions"] == []
    assert "已添加" not in result["answer"]
    assert fixture.seen == []


@pytest.mark.parametrize("objective,claim", [("打开回测中心", "已打开回测中心。"), ("我的自选股有哪些？", "你的自选股包括 NVDA。")])
def test_compatibility_navigation_and_personal_read_claims_need_real_receipts(make_app, objective, claim):
    fixture = make_app([_answer(claim), _plan_answer(claim)])
    result = _chat(fixture, objective).json()
    assert result["ok"] is False
    assert result["answer"] != claim
    assert not result.get("navigation")
    assert result["tool_trace"] == []
    assert fixture.seen == []


def test_native_clarification_does_not_trigger_unnecessary_json_model_round(make_app):
    fixture = make_app([_answer("请提供要添加到自选股的股票代码。")])
    result = _chat(fixture, "添加股票到我的自选股").json()
    assert "请提供" in result["answer"]
    assert result["platform_actions"] == []
    assert fixture.seen == []
    assert len(fixture.llm.requests) == 1


def test_successful_native_write_does_not_reenter_compatibility_executor(make_app):
    fixture = make_app([_calls(_tool("watchlist_save", body={"symbols": ["NVDA"]})), _answer("已添加 NVDA。")])
    result = _chat(fixture, "添加 NVDA 到我的自选股").json()
    assert result["platform_actions"][0]["status"] == "completed"
    assert len([item for item in fixture.seen if item[0] == "save"]) == 1
    assert len(fixture.llm.requests) == 2


def test_switching_from_native_to_json_plan_cannot_repeat_already_performed_write(make_app):
    arguments = {"capability_id": "watchlist_save", "body": {"symbols": ["NVDA"]}}
    fixture = make_app([
        _calls(_tool("watchlist_save", body={"symbols": ["NVDA"]})),
        _plan("call_platform_capability", arguments),
        _plan_answer("已添加 NVDA。"),
    ])
    result = _chat(fixture, "添加 NVDA 到我的自选股").json()
    assert result["ok"] is True
    assert len(result["platform_actions"]) == 1
    assert len([item for item in fixture.seen if item[0] == "save"]) == 1
    assert fixture.watchlists["alice"]["symbols"] == ["AAPL", "NVDA"]
    assert len(fixture.llm.requests) == 3


@pytest.mark.parametrize("objective,path,expected", [
    ("创建我的研究任务", "/api/agents/tasks", True),
    ("取消我的研究任务", "/api/agents/tasks", False),
    ("重试我的研究任务", "/api/agents/tasks", False),
    ("取消我的研究任务", "/api/agents/tasks/{task_id}/cancel", True),
    ("创建我的研究任务", "/api/agents/tasks/{task_id}/cancel", False),
    ("重试我的研究任务", "/api/agents/tasks/{task_id}/retry", True),
    ("启动这个回测", "/api/backtest", True),
    ("取消这个回测", "/api/backtest", False),
    ("启动深度研判", "/api/agents/deep-research", True),
    ("取消深度研判", "/api/agents/deep-research", False),
])
def test_live_authorization_matches_operation_within_the_same_domain(objective, path, expected):
    assert platform._direct_write_authorized(objective, {"path": path, "method": "POST"}) is expected
