from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from deepfocus_api import llm as llm_mod
from deepfocus_api import llm_runtime as runtime


def _slot(name="a", model="qwen3.6-flash", key=None):
    return {"provider": "openai-compatible", "base_url": "https://example.invalid/v1", "api_key": key or f"runtime-{name}", "model": model, "label": name}


def _configure(monkeypatch, slots):
    monkeypatch.setattr(llm_mod, "load_model_config", lambda: {**slots[0], "temperature": .2, "model_pool": slots})
    return llm_mod.CloudResearchLLM()


def _error(status, message, **kwargs):
    error = RuntimeError(message)
    error.status_code = status
    for key, value in kwargs.items():
        setattr(error, key, value)
    return error


def _response(content='{"summary":"有事实依据"}', **kwargs):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, **kwargs))])


def _client(create):
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


@pytest.mark.asyncio
async def test_cache_reuses_transport_by_loop_endpoint_and_credential():
    created = []

    class Client:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.closed = False
            created.append(self)

        async def close(self):
            self.closed = True

    config = _slot()
    first = runtime.get_cached_client(config, Client)
    assert runtime.get_cached_client({**config, "model": "glm-5.3-flash", "label": "alias"}, Client) is first
    assert first.kwargs["max_retries"] == 0
    other_key = runtime.get_cached_client({**config, "api_key": "runtime-new-key"}, Client)
    other_endpoint = runtime.get_cached_client({**config, "base_url": "https://other.invalid/v1"}, Client)
    assert first is not other_key and first is not other_endpoint
    assert config["api_key"] not in repr(list(runtime._CLIENTS))
    await runtime.close_llm_clients()
    assert all(client.closed for client in created)


def test_clients_are_never_shared_across_event_loops():
    clients = []

    class Client:
        def __init__(self, **kwargs):
            clients.append(self)

        async def close(self):
            pass

    async def use():
        client = runtime.get_cached_client(_slot(), Client)
        await runtime.close_llm_clients()
        return client

    assert asyncio.run(use()) is not asyncio.run(use())


@pytest.mark.asyncio
async def test_cache_eviction_waits_until_active_request_releases(monkeypatch):
    monkeypatch.setattr(runtime, "CLIENT_CACHE_MAX", 2)
    clients = []

    class Client:
        def __init__(self, **kwargs):
            self.closed = False
            clients.append(self)

        async def close(self):
            self.closed = True

    first = runtime.get_cached_client(_slot("a"), Client)
    runtime.borrow_client(first)
    runtime.get_cached_client(_slot("b"), Client)
    runtime.get_cached_client(_slot("c"), Client)
    await asyncio.sleep(.001)
    assert len(runtime._CLIENTS) == 2
    assert not first.closed
    runtime.release_client(first)
    await asyncio.sleep(.001)
    assert first.closed
    await runtime.close_llm_clients()
    assert all(client.closed for client in clients)


@pytest.mark.asyncio
async def test_idle_client_is_closed_and_replaced():
    class Client:
        def __init__(self, **kwargs):
            self.closed = False

        async def close(self):
            self.closed = True

    first = runtime.get_cached_client(_slot(), Client)
    runtime._CLIENT_ENTRIES[id(first)].last_used -= runtime.CLIENT_IDLE_SECONDS + 1
    second = runtime.get_cached_client(_slot(), Client)
    await asyncio.sleep(.001)
    assert first is not second and first.closed
    await runtime.close_llm_clients()


@pytest.mark.asyncio
async def test_quota_circuit_is_shared_across_instances_and_not_reopened(monkeypatch):
    primary, backup = _slot("quota"), _slot("healthy", "glm-5.3-flash")
    first = _configure(monkeypatch, [primary, backup])
    calls = []

    def make_client(config):
        async def create(**payload):
            calls.append(config["label"])
            if config is primary:
                raise _error(429, "insufficient_quota")
            return _response()
        return _client(create)

    monkeypatch.setattr(llm_mod.CloudResearchLLM, "_client_for_config", staticmethod(make_client))
    await first._completion({"messages": []}, timeout_seconds=1)
    second = llm_mod.CloudResearchLLM()
    await second._completion({"messages": []}, timeout_seconds=1)
    assert calls == ["quota", "healthy", "healthy"]
    assert runtime._COOLDOWNS[runtime.pool_slot_key(primary)] - time.monotonic() > 5 * 3600
    # An in-flight success predating the failure cannot reopen the quota slot.
    runtime.recover_slot(primary)
    assert not runtime.slot_available(primary)
    runtime.cooldown_slot(backup, _error(429, "rate limit exceeded"))
    with pytest.raises(runtime.ModelPoolUnavailableError):
        await second._completion({"messages": []}, timeout_seconds=1)
    assert calls == ["quota", "healthy", "healthy"]


def test_quota_reset_and_rate_limit_have_different_cooldowns():
    quota = _error(429, "insufficient_quota")
    assert runtime.cooldown_seconds(_slot(), quota) == runtime.QUOTA_COOLDOWN_SECONDS
    quota.response = SimpleNamespace(headers={"retry-after": "1"})
    assert runtime.cooldown_seconds(_slot(), quota) == runtime.QUOTA_COOLDOWN_SECONDS
    reset_at = time.time() + 3 * 86400
    quota.body = {"error": {"code": "insufficient_quota", "reset_at": reset_at}}
    assert 3 * 86400 - 1 < runtime.cooldown_seconds(_slot(), quota) <= 3 * 86400
    rate = _error(429, "rate limit exceeded", response=SimpleNamespace(headers={"retry-after": "45"}))
    assert runtime.cooldown_seconds(_slot(), rate) == 45
    rate.response.headers = {"x-ratelimit-reset-requests": "1m30s"}
    assert runtime.cooldown_seconds(_slot(), rate) == 90


def test_circuit_identity_changes_with_credentials_not_display_label():
    config = _slot()
    runtime.cooldown_slot(config, _error(429, "insufficient_quota"))
    assert not runtime.slot_available({**config, "label": "renamed"})
    assert runtime.slot_available({**config, "api_key": "runtime-replaced"})
    assert config["api_key"] not in runtime.pool_slot_key(config)


@pytest.mark.asyncio
async def test_failover_uses_remaining_budget_and_cancels_backup(monkeypatch):
    primary, backup = _slot("slow-primary"), _slot("slow-backup")
    llm = _configure(monkeypatch, [primary, backup])
    calls, cancelled = [], []

    def make_client(config):
        async def create(**payload):
            calls.append(config["label"])
            if config is primary:
                await asyncio.sleep(.04)
                raise _error(429, "rate limit exceeded")
            try:
                await asyncio.sleep(.08)
                return _response()
            except asyncio.CancelledError:
                cancelled.append(config["label"])
                raise
        return _client(create)

    monkeypatch.setattr(llm, "_client_for_config", make_client)
    started = time.monotonic()
    with pytest.raises(asyncio.TimeoutError):
        await llm._completion({"messages": []}, timeout_seconds=.08)
    assert time.monotonic() - started < .13
    assert calls == ["slow-primary", "slow-backup"]
    assert cancelled == ["slow-backup"]


@pytest.mark.asyncio
async def test_exhausted_deadline_does_not_call_or_trip_unused_backup(monkeypatch):
    primary, backup = _slot("hung"), _slot("unused")
    llm = _configure(monkeypatch, [primary, backup])
    calls = []

    def make_client(config):
        async def create(**payload):
            calls.append(config["label"])
            await asyncio.sleep(1)
            return _response()
        return _client(create)

    monkeypatch.setattr(llm, "_client_for_config", make_client)
    with pytest.raises(asyncio.TimeoutError):
        await llm._completion({"messages": []}, timeout_seconds=.02)
    assert calls == ["hung"]
    assert runtime.slot_available(backup)


@pytest.mark.asyncio
async def test_caller_cancellation_propagates_without_failover_or_circuit(monkeypatch):
    first, backup = _slot("cancel-first"), _slot("cancel-backup")
    llm = _configure(monkeypatch, [first, backup])
    started = asyncio.Event()
    calls = []

    def make_client(config):
        async def create(**payload):
            calls.append(config["label"])
            started.set()
            await asyncio.sleep(30)
        return _client(create)

    monkeypatch.setattr(llm, "_client_for_config", make_client)
    task = asyncio.create_task(llm._completion({"messages": []}, timeout_seconds=10))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls == ["cancel-first"]
    assert runtime.slot_available(first) and runtime.slot_available(backup)


@pytest.mark.asyncio
async def test_thinking_negotiation_failure_uses_real_error_for_failover(monkeypatch):
    first, backup = _slot("thinking", "glm-5.3-flash"), _slot("backup")
    llm = _configure(monkeypatch, [first, backup])
    calls = []

    def make_client(config):
        async def create(**payload):
            calls.append((config["label"], payload))
            if config is first:
                if payload["extra_body"]["thinking"]["type"] == "disabled":
                    raise _error(400, "1210 不支持关闭思考")
                raise _error(429, "insufficient_quota")
            return _response()
        return _client(create)

    monkeypatch.setattr(llm, "_client_for_config", make_client)
    assert (await llm._completion({"messages": []}, timeout_seconds=1)).choices[0].message.content
    assert [label for label, _ in calls] == ["thinking", "thinking", "backup"]
    assert not runtime.slot_available(first)


@pytest.mark.asyncio
async def test_json_repairs_share_one_budget(monkeypatch):
    llm = _configure(monkeypatch, [_slot("json")])
    calls = []

    async def create(**payload):
        calls.append(payload)
        await asyncio.sleep(.04)
        return _response("not JSON")

    monkeypatch.setattr(llm, "_client", lambda: _client(create))
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="未返回"):
        await llm.complete_json("question", timeout_seconds=.06)
    assert time.monotonic() - started < .11
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_parent_deadline_context_is_inherited_and_request_isolated(monkeypatch):
    llm = _configure(monkeypatch, [_slot("context")])

    async def create(**payload):
        await asyncio.sleep(.04)
        return _response()

    monkeypatch.setattr(llm, "_client", lambda: _client(create))
    with runtime.llm_deadline(.02):
        with pytest.raises(asyncio.TimeoutError):
            await llm._completion({"messages": []}, timeout_seconds=1)
    runtime._COOLDOWNS.clear()
    assert (await llm._completion({"messages": []}, timeout_seconds=.2)).choices[0].message.content


@pytest.mark.asyncio
async def test_known_text_model_is_skipped_without_image_request(monkeypatch):
    text, vision = _slot("text", "glm-4-flash-250414"), _slot("vision", "qwen-vl-max")
    llm = _configure(monkeypatch, [text, vision])
    calls = []

    def make_client(config):
        async def create(**payload):
            calls.append((config["label"], payload))
            return _response()
        return _client(create)

    monkeypatch.setattr(llm, "_client_for_config", make_client)
    assert llm.has_vision_provider()
    await llm.complete_vision("read", [b"png"], timeout_seconds=1)
    assert [label for label, _ in calls] == ["vision"]
    assert calls[0][1]["messages"][0]["content"][1]["type"] == "image_url"


@pytest.mark.asyncio
async def test_visual_capability_error_fails_over_and_remembers_without_hurting_text(monkeypatch):
    text, vision = _slot("unknown", "new-text-model"), _slot("vl", "qwen-vl-max")
    llm = _configure(monkeypatch, [text, vision])
    calls = []

    def make_client(config):
        async def create(**payload):
            calls.append(config["label"])
            if config is text and runtime.payload_has_images(payload):
                raise _error(400, "This model does not support image inputs")
            return _response()
        return _client(create)

    monkeypatch.setattr(llm_mod.CloudResearchLLM, "_client_for_config", staticmethod(make_client))
    await llm.complete_vision("read", [b"png"], timeout_seconds=1)
    await llm_mod.CloudResearchLLM().complete_vision("read again", [b"png"], timeout_seconds=1)
    await llm._completion({"messages": []}, timeout_seconds=1)
    assert calls == ["unknown", "vl", "vl", "unknown"]
    assert runtime.slot_available(text) and not runtime.vision_supported(text)


@pytest.mark.asyncio
async def test_no_visual_model_raises_specific_error_without_cloud_request(monkeypatch):
    llm = _configure(monkeypatch, [_slot("text-only", "glm-4-flash-250414")])
    assert not llm.has_vision_provider()
    monkeypatch.setattr(llm, "_client", lambda: pytest.fail("must not call text-only model"))
    with pytest.raises(runtime.VisionUnavailableError, match="OCR"):
        await llm.complete_vision("read", [b"png"])


@pytest.mark.asyncio
async def test_invalid_image_400_is_not_treated_as_model_capability(monkeypatch):
    first, backup = _slot("invalid"), _slot("not-called", "qwen-vl-max")
    llm = _configure(monkeypatch, [first, backup])
    calls = []

    def make_client(config):
        async def create(**payload):
            calls.append(config["label"])
            raise _error(400, "Invalid image_url: corrupt image data")
        return _client(create)

    monkeypatch.setattr(llm, "_client_for_config", make_client)
    with pytest.raises(RuntimeError, match="corrupt"):
        await llm.complete_vision("read", [b"png"])
    assert calls == ["invalid"]
    assert runtime.vision_supported(first)


@pytest.mark.asyncio
async def test_glm53_disabled_thinking_payload_is_preserved(monkeypatch):
    llm = _configure(monkeypatch, [_slot("glm", "glm-5.3-flash")])
    payloads = []

    async def create(**payload):
        payloads.append(payload)
        return _response()

    monkeypatch.setattr(llm, "_client", lambda: _client(create))
    await llm._completion({"messages": []}, timeout_seconds=1)
    assert payloads[0]["extra_body"]["thinking"] == {"type": "disabled"}


@pytest.mark.asyncio
async def test_stream_deadline_includes_reading_and_releases_transport(monkeypatch):
    llm = _configure(monkeypatch, [_slot("stream")])
    closed = []

    async def chunks():
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='{"summary":'))])
        await asyncio.sleep(1)
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='"done"}'))])

    class Stream:
        def __aiter__(self):
            return chunks()

        async def close(self):
            closed.append(True)

    async def create(**payload):
        await asyncio.sleep(.01)
        return Stream()

    monkeypatch.setattr(llm, "_client", lambda: _client(create))
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="未返回"):
        await llm._complete_text_streaming("question", max_tokens=20, timeout_seconds=.04)
    assert time.monotonic() - started < .09
    assert closed == [True]


@pytest.mark.asyncio
async def test_stream_cancellation_is_not_swallowed(monkeypatch):
    llm = _configure(monkeypatch, [_slot("stream-cancel")])
    entered = asyncio.Event()
    closed = []

    async def chunks():
        entered.set()
        await asyncio.sleep(30)
        yield None

    class Stream:
        def __aiter__(self):
            return chunks()

        async def close(self):
            closed.append(True)

    async def create(**payload):
        return Stream()

    monkeypatch.setattr(llm, "_client", lambda: _client(create))
    task = asyncio.create_task(llm._complete_text_streaming("question", max_tokens=20, timeout_seconds=10))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed == [True]


@pytest.mark.asyncio
async def test_real_sdk_does_not_retry_429_and_reuses_clients(monkeypatch):
    import httpx
    from openai import AsyncOpenAI

    primary, backup = _slot("sdk-quota"), _slot("sdk-healthy", "glm-5.3-flash")
    llm = _configure(monkeypatch, [primary, backup])
    requests, clients = [], []

    def handle(request):
        credential = request.headers["authorization"]
        requests.append(credential)
        if credential.endswith(primary["api_key"]):
            return httpx.Response(429, json={"error": {"code": "insufficient_quota", "message": "insufficient_quota"}})
        return httpx.Response(200, json={
            "id": "chat-test", "object": "chat.completion", "created": 1, "model": "glm-5.3-flash",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": '{"summary":"complete"}'}}],
        })

    def factory(**kwargs):
        client = AsyncOpenAI(**kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)))
        clients.append(client)
        return client

    monkeypatch.setattr(llm_mod, "AsyncOpenAI", factory)
    await llm._completion({"messages": [{"role": "user", "content": "question"}]}, timeout_seconds=1)
    await llm_mod.CloudResearchLLM()._completion({"messages": [{"role": "user", "content": "again"}]}, timeout_seconds=1)
    assert len(clients) == 2
    assert requests == [f"Bearer {primary['api_key']}", f"Bearer {backup['api_key']}", f"Bearer {backup['api_key']}"]
    await runtime.close_llm_clients()
    assert all(client.is_closed() for client in clients)


@pytest.mark.asyncio
async def test_stream_failure_and_empty_output_fail_over_with_shared_budget(monkeypatch):
    failed, empty, backup = _slot("broken-stream"), _slot("empty-stream"), _slot("good-stream")
    llm = _configure(monkeypatch, [failed, empty, backup])
    calls, closed = [], []

    class Stream:
        def __init__(self, config):
            self.config = config

        def __aiter__(self):
            async def chunks():
                if self.config is failed:
                    raise _error(502, "upstream connection reset")
                if self.config is backup:
                    yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='{"summary":"done"}'))])
            return chunks()

        async def close(self):
            closed.append(self.config["label"])

    def make_client(config):
        async def create(**payload):
            calls.append(config["label"])
            return Stream(config)
        return _client(create)

    monkeypatch.setattr(llm, "_client_for_config", make_client)
    text = await llm._complete_text_streaming("question", max_tokens=30, timeout_seconds=1)
    assert text == '{"summary":"done"}'
    assert calls == ["broken-stream", "empty-stream", "good-stream"]
    assert closed == calls
    assert not runtime.slot_available(failed) and not runtime.slot_available(empty)


@pytest.mark.asyncio
async def test_stream_keeps_cached_client_leased_until_consumed(monkeypatch):
    config = _slot("leased-stream")
    llm = _configure(monkeypatch, [config])
    monkeypatch.setattr(runtime, "CLIENT_CACHE_MAX", 1)
    clients = []

    class Client:
        def __init__(self, **kwargs):
            self.closed = False
            clients.append(self)
            async def create(**payload):
                async def chunks():
                    yield _response()
                return chunks()
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

        async def close(self):
            self.closed = True

    monkeypatch.setattr(llm_mod, "AsyncOpenAI", Client)
    stream = await llm._completion({"messages": [], "stream": True}, timeout_seconds=1)
    assert runtime._CLIENT_ENTRIES[id(clients[0])].active == 1
    runtime.get_cached_client(_slot("other-live"), Client)
    await asyncio.sleep(.001)
    assert not clients[0].closed
    assert len([chunk async for chunk in stream]) == 1
    await asyncio.sleep(.001)
    assert clients[0].closed
    await runtime.close_llm_clients()


@pytest.mark.asyncio
async def test_format_negotiation_retry_uses_remaining_budget(monkeypatch):
    llm = _configure(monkeypatch, [_slot("format")])
    calls = []

    async def create(**payload):
        calls.append(payload)
        if "response_format" in payload:
            await asyncio.sleep(.03)
            raise _error(400, "response_format is unsupported")
        await asyncio.sleep(.05)
        return _response()

    monkeypatch.setattr(llm, "_client", lambda: _client(create))
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="未返回"):
        await llm._complete_text("question", max_tokens=30, force_json=True, timeout_seconds=.05)
    assert time.monotonic() - started < .09
    assert len(calls) == 2 and "response_format" not in calls[1]
