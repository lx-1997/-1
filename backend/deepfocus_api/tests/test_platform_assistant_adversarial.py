"""Adversarial schedules around the real platform executor and receipt store.

The SDK and application routes are isolated test boundaries; no production
database, real model, or external request is used. Barriers make races repeatable
without sleeping or replacing the platform executor with a toy state machine.
"""
from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from test_platform_assistant import (
    CLAIMS, _answer, _calls, _chat, _headers, _tool, make_app,
)


def _completion(message):
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _replace_endpoint(app, path, method, replacement):
    # Keep route validation and the real ASGI middleware stack. Only change
    # the isolated endpoint boundary, where the schedule is injected.
    route = next(route for route in app.routes
                 if getattr(route, "path", None) == path
                 and method in getattr(route, "methods", ()))
    route.dependant.call = replacement


async def _http(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="http://isolated.test")


def _body(symbol, request_id):
    return {"message": f"添加 {symbol} 到我的自选股", "request_id": request_id}


def test_concurrent_watchlist_additions_preserve_both_committed_symbols(make_app):
    fixture = make_app()

    async def scenario():
        readers = 0
        both_read = asyncio.Event()

        async def synchronized_read(request):
            nonlocal readers
            owner = CLAIMS[request.headers["authorization"].split()[1]]["sub"]
            snapshot = copy.deepcopy(fixture.watchlists[owner])
            readers += 1
            if readers == 2:
                both_read.set()
            await asyncio.wait_for(both_read.wait(), 2)
            return snapshot

        async def scripted(payload, timeout_seconds):
            fixture.llm.requests.append(copy.deepcopy(payload))
            if any(message["role"] == "tool" for message in payload["messages"]):
                return _completion(_answer())
            objective = next(message["content"] for message in payload["messages"]
                             if message["role"] == "user")
            symbol = "NVDA" if "NVDA" in objective else "AMD"
            return _completion(_calls(_tool("watchlist_save", body={"symbols": [symbol]})))

        _replace_endpoint(fixture.app, "/api/me/watchlist", "GET", synchronized_read)
        fixture.llm._completion = scripted
        async with await _http(fixture.app) as client:
            results = await asyncio.gather(*(
                client.post("/api/agents/chat", headers=_headers(), json=_body(symbol, f"parallel-{symbol}"))
                for symbol in ("NVDA", "AMD")
            ))
        assert all(response.status_code == 200 and response.json()["ok"] for response in results)
        assert len([row for row in fixture.seen if row[0] == "save"]) == 2
        # Both callers received a successful committed-operation receipt.
        # A later full-table POST must not erase the other caller's addition.
        assert set(fixture.watchlists["alice"]["symbols"]) == {"AAPL", "NVDA", "AMD"}

    asyncio.run(scenario())


@pytest.mark.parametrize("same_body", [True, False])
def test_parallel_duplicate_request_is_rejected_before_second_sdk_or_quota_call(make_app, same_body):
    quota = []
    fixture = make_app(check_quota=lambda claims, request: quota.append(claims["sub"]))

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def scripted(payload, timeout_seconds):
            fixture.llm.requests.append(copy.deepcopy(payload))
            if len(fixture.llm.requests) == 1:
                entered.set()
                await release.wait()
                return _completion(_calls(_tool("watchlist_save", body={"symbols": ["NVDA"]})))
            return _completion(_answer())

        fixture.llm._completion = scripted
        async with await _http(fixture.app) as client:
            first = asyncio.create_task(client.post("/api/agents/chat", headers=_headers(), json=_body("NVDA", "same-request")))
            await asyncio.wait_for(entered.wait(), 2)
            second = await client.post("/api/agents/chat", headers=_headers(),
                                       json=_body("NVDA" if same_body else "AMD", "same-request"))
            release.set()
            completed = await asyncio.wait_for(first, 2)
        assert completed.status_code == 200
        assert second.status_code == 409
        assert ("已提交" if same_body else "不同的操作") in second.json()["detail"]
        assert len(fixture.llm.requests) == 2
        assert len([row for row in fixture.seen if row[0] == "save"]) == 1
        assert quota == ["alice"]

    asyncio.run(scenario())


def test_concurrent_confirmation_admits_one_write_and_replays_its_receipt(make_app):
    fixture = make_app([_calls(_tool("item_delete", path_params={"item_id": "record-1"})), _answer()])
    proposal = _chat(fixture, "删除我的收藏 record-1").json()["platform_actions"][0]

    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()

        async def blocked_delete(item_id, request):
            fixture.seen.append(("delete", "alice", item_id))
            started.set()
            await release.wait()
            return {"deleted": item_id}

        _replace_endpoint(fixture.app, "/api/items/{item_id}", "DELETE", blocked_delete)
        payload = {"confirmation_token": proposal["confirmation_token"]}
        async with await _http(fixture.app) as client:
            first = asyncio.create_task(client.post("/api/agents/platform-actions/confirm", headers=_headers(), json=payload))
            await asyncio.wait_for(started.wait(), 2)
            simultaneous = await client.post("/api/agents/platform-actions/confirm", headers=_headers(), json=payload)
            foreign = await client.post("/api/agents/platform-actions/confirm", headers=_headers("bob"), json=payload)
            release.set()
            completed = await asyncio.wait_for(first, 2)
            replay = await client.post("/api/agents/platform-actions/confirm", headers=_headers(), json=payload)
        assert simultaneous.status_code == foreign.status_code == 409
        assert completed.status_code == replay.status_code == 200
        assert completed.json() == replay.json()
        assert len([row for row in fixture.seen if row[0] == "delete"]) == 1

    asyncio.run(scenario())


def test_write_response_loss_keeps_an_uncertain_action_receipt_and_never_reexecutes(make_app):
    fixture = make_app([_calls(_tool("watchlist_save", body={"symbols": ["NVDA"]}))])

    async def committed_but_response_lost(request):
        body = await request.json()
        fixture.seen.append(("save", "alice", body))
        fixture.watchlist_apply("alice", body)
        raise RuntimeError("isolated fault after commit, before HTTP response")

    _replace_endpoint(fixture.app, "/api/me/watchlist", "POST", committed_but_response_lost)
    first = _chat(fixture, "添加 NVDA 到我的自选股", request_id="lost-write-response")
    replay = _chat(fixture, "添加 NVDA 到我的自选股", request_id="lost-write-response")
    result = first.json()
    assert first.status_code == replay.status_code == 200
    assert result == replay.json()
    assert fixture.watchlists["alice"]["symbols"] == ["AAPL", "NVDA"]
    assert len([row for row in fixture.seen if row[0] == "save"]) == 1
    assert len(fixture.llm.requests) == 1
    assert result["ok"] is False
    # Losing a response cannot prove the write did not happen. The attempted
    # action must remain visible so users can inspect its real endpoint.
    assert len(result["platform_actions"]) == 1
    assert result["platform_actions"][0]["status"] == "failed"
    assert "核对" in result["platform_actions"][0]["error"]


def test_write_http_failure_is_cached_without_second_execution(make_app):
    from fastapi import HTTPException

    fixture = make_app([_calls(_tool("watchlist_save", body={"symbols": ["NVDA"]})), _answer()])

    async def reject_write(request):
        fixture.seen.append(("attempt", "alice"))
        raise HTTPException(status_code=409, detail="isolated version conflict")

    _replace_endpoint(fixture.app, "/api/me/watchlist", "POST", reject_write)
    first = _chat(fixture, "添加 NVDA 到我的自选股", request_id="write-rejected")
    replay = _chat(fixture, "添加 NVDA 到我的自选股", request_id="write-rejected")
    assert first.json() == replay.json()
    assert first.json()["platform_actions"][0]["status"] == "failed"
    assert first.json()["ok"] is False
    assert fixture.watchlists["alice"]["symbols"] == ["AAPL"]
    assert [row for row in fixture.seen if row[0] == "attempt"] == [("attempt", "alice")]


@pytest.mark.parametrize("path", ["/api/agents/chat/stream", "/api/agents/tool-research/stream"])
def test_sse_disconnect_after_committed_write_keeps_recoverable_receipt(make_app, path):
    fixture = make_app()

    async def scenario():
        second_round, canceled = asyncio.Event(), asyncio.Event()
        incoming = asyncio.Queue()
        captured = []

        async def scripted(payload, timeout_seconds):
            fixture.llm.requests.append(copy.deepcopy(payload))
            if len(fixture.llm.requests) == 1:
                return _completion(_calls(_tool("watchlist_save", body={"symbols": ["NVDA"]})))
            second_round.set()
            try:
                await asyncio.Future()
            finally:
                canceled.set()

        fixture.llm._completion = scripted
        encoded = json.dumps(_body("NVDA", "disconnect-after-write")).encode()
        await incoming.put({"type": "http.request", "body": encoded, "more_body": False})
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
                 "http_version": "1.1", "method": "POST", "path": path, "raw_path": path.encode(),
                 "query_string": b"", "headers": [(b"authorization", b"Bearer alice"), (b"content-type", b"application/json")],
                 "client": ("127.0.0.1", 32001), "server": ("isolated.test", 80), "scheme": "http"}

        async def send(message):
            captured.append(copy.deepcopy(message))

        request = asyncio.create_task(fixture.app(scope, incoming.get, send))
        await asyncio.wait_for(second_round.wait(), 2)
        assert fixture.watchlists["alice"]["symbols"] == ["AAPL", "NVDA"]
        await incoming.put({"type": "http.disconnect"})
        await asyncio.wait_for(request, 2)
        await asyncio.wait_for(canceled.wait(), 2)
        wire = b"".join(message.get("body", b"") for message in captured).decode()
        assert '"platform":true' in wire
        assert "event: final" not in wire
        assert len([row for row in fixture.seen if row[0] == "save"]) == 1

        async with await _http(fixture.app) as client:
            replay = await client.post("/api/agents/chat", headers=_headers(), json=_body("NVDA", "disconnect-after-write"))
        # A receipt already existed before the model's second round began.
        # Disconnect must cancel the model without discarding that receipt.
        assert replay.status_code == 200, replay.text
        assert replay.json()["platform_actions"][0]["status"] == "completed"
        assert len([row for row in fixture.seen if row[0] == "save"]) == 1
        assert len(fixture.llm.requests) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("path", ["/api/agents/chat/stream", "/api/agents/tool-research/stream"])
def test_sse_disconnect_before_write_response_recovers_uncertainty_without_reexecution(make_app, path):
    fixture = make_app([_calls(_tool("watchlist_save", body={"symbols": ["NVDA"]}))])

    async def scenario():
        committed, canceled = asyncio.Event(), asyncio.Event()
        incoming = asyncio.Queue()
        captured = []

        async def committed_but_response_pending(request):
            body = await request.json()
            fixture.seen.append(("save", "alice", body))
            fixture.watchlist_apply("alice", body)
            committed.set()
            try:
                await asyncio.Future()
            finally:
                canceled.set()

        _replace_endpoint(fixture.app, "/api/me/watchlist", "POST", committed_but_response_pending)
        body = _body("NVDA", "disconnect-during-write")
        await incoming.put({"type": "http.request", "body": json.dumps(body).encode(), "more_body": False})
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
                 "http_version": "1.1", "method": "POST", "path": path, "raw_path": path.encode(),
                 "query_string": b"", "headers": [(b"authorization", b"Bearer alice"), (b"content-type", b"application/json")],
                 "client": ("127.0.0.1", 32001), "server": ("isolated.test", 80), "scheme": "http"}

        async def send(message):
            captured.append(copy.deepcopy(message))

        request = asyncio.create_task(fixture.app(scope, incoming.get, send))
        await asyncio.wait_for(committed.wait(), 2)
        await incoming.put({"type": "http.disconnect"})
        await asyncio.wait_for(request, 2)
        await asyncio.wait_for(canceled.wait(), 2)
        async with await _http(fixture.app) as client:
            replay = await client.post("/api/agents/chat", headers=_headers(), json=body)
        assert replay.status_code == 200, replay.text
        assert replay.json()["ok"] is False
        assert replay.json()["platform_actions"][0]["status"] == "failed"
        assert "核对" in replay.json()["platform_actions"][0]["error"]
        assert fixture.watchlists["alice"]["symbols"] == ["AAPL", "NVDA"]
        assert len([row for row in fixture.seen if row[0] == "save"]) == 1
        assert len(fixture.llm.requests) == 1

    asyncio.run(scenario())


def test_delayed_watchlist_confirmation_preserves_concurrent_name_edits(make_app):
    fixture = make_app([_calls(_tool("watchlist_save", body={"symbols": ["AAPL"], "names": {"AAPL": "Apple"}})), _answer()])
    fixture.watchlists["alice"]["symbols"].append("NVDA")
    action = _chat(fixture, "从我的自选股移除 NVDA").json()["platform_actions"][0]
    fixture.watchlists["alice"] = {"symbols": ["AAPL", "NVDA", "AMD"],
                                    "names": {"AAPL": "Apple renamed elsewhere", "AMD": "AMD"}}
    receipt = fixture.client.post("/api/agents/platform-actions/confirm", headers=_headers(),
                                  json={"confirmation_token": action["confirmation_token"]})
    assert receipt.json()["ok"] is True
    assert fixture.watchlists["alice"] == {"symbols": ["AAPL", "AMD"],
                                           "names": {"AAPL": "Apple renamed elsewhere", "AMD": "AMD"}}


def test_predeployment_watchlist_confirmation_cannot_overwrite_newer_names(make_app):
    fixture = make_app()
    # This is the payload emitted by the previous production executor:
    # delta.names is a full old name snapshot, and arguments.body has no delta.
    arguments = {"body": {"symbols": ["AAPL"], "names": {"AAPL": "Apple"}}}
    action = {"id": "legacy-card", "title": "watchlist_save", "capability_id": "watchlist_save",
              "method": "POST", "path": "/api/me/watchlist", "arguments": arguments,
              "status": "needs_confirmation"}
    token = fixture.app.state.platform_assistant.store.propose("alice", {
        "capability_id": "watchlist_save", "arguments": arguments, "action": action,
        "watchlist_delta": {"added": [], "removed": ["NVDA"], "names": {"AAPL": "Apple"}},
    })
    fixture.watchlists["alice"] = {"symbols": ["AAPL", "NVDA", "AMD"],
                                    "names": {"AAPL": "Apple renamed elsewhere", "AMD": "AMD"}}
    response = fixture.client.post("/api/agents/platform-actions/confirm", headers=_headers(),
                                   json={"confirmation_token": token})
    assert response.status_code in {200, 409}
    assert fixture.watchlists["alice"]["names"]["AAPL"] == "Apple renamed elsewhere"
    assert "AMD" in fixture.watchlists["alice"]["symbols"]
    # Either safely migrate the legacy payload or close it with an explicit
    # request to generate a current confirmation card. Never silently apply
    # the old full name snapshot as a changed-only atomic update.
    if response.status_code == 200 and response.json()["ok"]:
        assert "NVDA" not in fixture.watchlists["alice"]["symbols"]
    else:
        assert "NVDA" in fixture.watchlists["alice"]["symbols"]
        assert "重新" in response.text


def test_confirmation_response_loss_is_cached_as_uncertain_and_never_reexecuted(make_app):
    fixture = make_app([_calls(_tool("item_delete", path_params={"item_id": "record-1"})), _answer()])
    token = _chat(fixture, "删除我的收藏 record-1").json()["platform_actions"][0]["confirmation_token"]

    async def deleted_but_response_lost(item_id, request):
        fixture.seen.append(("delete", "alice", item_id))
        raise RuntimeError("isolated post-delete response loss")

    _replace_endpoint(fixture.app, "/api/items/{item_id}", "DELETE", deleted_but_response_lost)
    first = fixture.client.post("/api/agents/platform-actions/confirm", headers=_headers(), json={"confirmation_token": token})
    replay = fixture.client.post("/api/agents/platform-actions/confirm", headers=_headers(), json={"confirmation_token": token})
    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    assert first.json()["ok"] is False
    assert first.json()["platform_actions"][0]["status"] == "failed"
    assert "不会自动重试" in first.json()["platform_actions"][0]["error"]
    assert len([row for row in fixture.seen if row[0] == "delete"]) == 1


def test_confirmation_cancel_after_commit_keeps_uncertain_receipt_without_second_write(make_app):
    fixture = make_app([_calls(_tool("item_delete", path_params={"item_id": "record-1"})), _answer()])
    token = _chat(fixture, "删除我的收藏 record-1").json()["platform_actions"][0]["confirmation_token"]

    async def scenario():
        committed, canceled = asyncio.Event(), asyncio.Event()

        async def deleted_but_response_interrupted(item_id, request):
            fixture.seen.append(("delete", "alice", item_id))
            committed.set()
            try:
                await asyncio.Future()
            finally:
                canceled.set()

        _replace_endpoint(fixture.app, "/api/items/{item_id}", "DELETE", deleted_but_response_interrupted)
        payload = {"confirmation_token": token}
        async with await _http(fixture.app) as client:
            first = asyncio.create_task(client.post("/api/agents/platform-actions/confirm", headers=_headers(), json=payload))
            await asyncio.wait_for(committed.wait(), 2)
            first.cancel()
            await asyncio.gather(first, return_exceptions=True)
            await asyncio.wait_for(canceled.wait(), 2)
            replay = await client.post("/api/agents/platform-actions/confirm", headers=_headers(), json=payload)
        assert len([row for row in fixture.seen if row[0] == "delete"]) == 1
        assert replay.status_code == 200, replay.text
        assert replay.json()["platform_actions"][0]["status"] == "failed"
        assert "核对" in replay.json()["platform_actions"][0]["error"]

    asyncio.run(scenario())


def test_quota_completion_failure_does_not_discard_committed_operation_receipt(make_app):
    def fail_quota_completion(lease):
        raise RuntimeError("isolated quota bookkeeping failure after committed write")

    fixture = make_app([
        _calls(_tool("watchlist_save", body={"symbols": ["NVDA"]})), _answer(),
    ], check_quota=lambda claims, request: "isolated-lease", complete_quota=fail_quota_completion)
    # A business write has already succeeded; auxiliary bookkeeping may fail,
    # but must not strand the durable request in an irrecoverable executing row.
    with TestClient(fixture.app, raise_server_exceptions=False) as client:
        first = client.post("/api/agents/chat", headers=_headers(), json=_body("NVDA", "quota-post-write"))
        replay = client.post("/api/agents/chat", headers=_headers(), json=_body("NVDA", "quota-post-write"))
    assert fixture.watchlists["alice"]["symbols"] == ["AAPL", "NVDA"]
    assert len([row for row in fixture.seen if row[0] == "save"]) == 1
    assert first.status_code == replay.status_code == 200, (first.text, replay.text)
    assert replay.json()["platform_actions"][0]["status"] == "completed"
