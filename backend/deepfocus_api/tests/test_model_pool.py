from __future__ import annotations

from types import SimpleNamespace

import pytest

from deepfocus_api import llm as llm_mod
from deepfocus_api import model_config


class _Completions:
    def __init__(self, label: str, calls: list[tuple[str, dict]], *, fail: Exception | None = None):
        self.label = label
        self.calls = calls
        self.fail = fail

    async def create(self, **payload):
        self.calls.append((self.label, payload))
        if self.fail:
            raise self.fail
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))])


class _EmptyReasoningCompletions(_Completions):
    async def create(self, **payload):
        self.calls.append((self.label, payload))
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='', reasoning_content='hidden reasoning'))])


class _Client:
    def __init__(self, label: str, calls: list[tuple[str, dict]], *, fail: Exception | None = None):
        self.chat = SimpleNamespace(completions=_Completions(label, calls, fail=fail))


@pytest.mark.asyncio
async def test_pool_prefers_first_slot_on_each_healthy_request(monkeypatch):
    config = {
        "provider": "minimax",
        "model": "MiniMax-M3",
        "base_url": "https://api.minimaxi.com/v1",
        "api_key": "key-a",
        "model_pool": [
            {"provider": "minimax", "model": "MiniMax-M3", "base_url": "https://api.minimaxi.com/v1", "api_key": "key-a", "label": "a"},
            {"provider": "minimax", "model": "MiniMax-M3", "base_url": "https://api.minimaxi.com/v1", "api_key": "key-b", "label": "b"},
        ],
        "temperature": 0.2,
    }
    monkeypatch.setattr(llm_mod, "load_model_config", lambda: config)
    calls: list[tuple[str, dict]] = []
    llm = llm_mod.CloudResearchLLM()
    monkeypatch.setattr(llm, "_client_for_config", lambda item: _Client(item["label"], calls))

    for _ in range(4):
        await llm._completion({"model": "MiniMax-M3", "messages": []}, timeout_seconds=1)

    assert [label for label, _ in calls] == ["a", "a", "a", "a"]
    assert all(payload["model"] == "MiniMax-M3" for _, payload in calls)


@pytest.mark.asyncio
async def test_pool_uses_next_slot_for_retryable_failure(monkeypatch):
    config = {
        "provider": "minimax",
        "model": "MiniMax-M3",
        "api_key": "key-a",
        "model_pool": [
            {"provider": "minimax", "model": "MiniMax-M3", "api_key": "key-a", "label": "a"},
            {"provider": "minimax", "model": "MiniMax-M3", "api_key": "key-b", "label": "b"},
        ],
    }
    monkeypatch.setattr(llm_mod, "load_model_config", lambda: config)
    calls: list[tuple[str, dict]] = []

    quota_error = RuntimeError("429 Token Plan 用量上限")
    quota_error.status_code = 429

    def make_client(item):
        return _Client(item["label"], calls, fail=quota_error if item["label"] == "a" else None)

    llm = llm_mod.CloudResearchLLM()
    monkeypatch.setattr(llm, "_client_for_config", make_client)
    response = await llm._completion({"model": "MiniMax-M3", "messages": []}, timeout_seconds=1)

    assert response.choices[0].message.content == '{"ok":true}'
    assert [label for label, _ in calls] == ["a", "b"]


@pytest.mark.asyncio
async def test_pool_fails_over_on_upstream_balance_402(monkeypatch):
    """Providers commonly use 402 when the primary account has no balance."""
    config = {
        "provider": "openai-compatible", "model": "qwen3.6-flash", "api_key": "key-a",
        "model_pool": [
            {"provider": "openai-compatible", "model": "qwen3.6-flash", "api_key": "key-a", "label": "qwen"},
            {"provider": "openai-compatible", "model": "glm-5.3-flash", "api_key": "key-b", "label": "glm"},
        ],
    }
    monkeypatch.setattr(llm_mod, "load_model_config", lambda: config)
    calls: list[tuple[str, dict]] = []
    balance_error = RuntimeError("Insufficient Balance")
    balance_error.status_code = 402

    def make_client(item):
        return _Client(item["label"], calls, fail=balance_error if item["label"] == "qwen" else None)

    llm = llm_mod.CloudResearchLLM()
    monkeypatch.setattr(llm, "_client_for_config", make_client)
    response = await llm._completion({"model": "qwen3.6-flash", "messages": []}, timeout_seconds=1)

    assert response.choices[0].message.content == '{"ok":true}'
    assert [label for label, _ in calls] == ["qwen", "glm"]


@pytest.mark.asyncio
async def test_pool_fails_over_on_empty_public_answer(monkeypatch):
    config = {
        "provider": "openai-compatible", "model": "qwen3.6-flash", "api_key": "key-a",
        "model_pool": [
            {"provider": "openai-compatible", "model": "qwen3.6-flash", "api_key": "key-a", "label": "qwen"},
            {"provider": "openai-compatible", "model": "glm-5.3-flash", "api_key": "key-b", "label": "glm"},
        ],
    }
    monkeypatch.setattr(llm_mod, "load_model_config", lambda: config)
    calls: list[tuple[str, dict]] = []

    class EmptyClient:
        def __init__(self):
            self.chat = SimpleNamespace(completions=_EmptyReasoningCompletions("qwen", calls))

    def make_client(item):
        return EmptyClient() if item["label"] == "qwen" else _Client("glm", calls)

    llm = llm_mod.CloudResearchLLM()
    monkeypatch.setattr(llm, "_client_for_config", make_client)
    response = await llm._completion({"model": "qwen3.6-flash", "messages": []}, timeout_seconds=1)
    assert response.choices[0].message.content == '{"ok":true}'
    assert [label for label, _ in calls] == ["qwen", "glm"]


@pytest.mark.asyncio
async def test_pool_uses_next_slot_after_async_timeout(monkeypatch):
    """wait_for raises an empty-string TimeoutError; it must still fail over."""
    config = {
        "provider": "minimax",
        "model": "MiniMax-M3",
        "api_key": "key-a",
        "model_pool": [
            {"provider": "minimax", "model": "MiniMax-M3", "api_key": "key-a", "label": "a"},
            {"provider": "minimax", "model": "MiniMax-M3", "api_key": "key-b", "label": "b"},
        ],
    }
    monkeypatch.setattr(llm_mod, "load_model_config", lambda: config)
    calls: list[tuple[str, dict]] = []

    timeout_error = TimeoutError()

    def make_client(item):
        return _Client(item["label"], calls, fail=timeout_error if item["label"] == "a" else None)

    llm = llm_mod.CloudResearchLLM()
    monkeypatch.setattr(llm, "_client_for_config", make_client)
    response = await llm._completion({"model": "MiniMax-M3", "messages": []}, timeout_seconds=1)

    assert response.choices[0].message.content == '{"ok":true}'
    assert [label for label, _ in calls] == ["a", "b"]


@pytest.mark.asyncio
async def test_pool_temporarily_skips_auth_failed_slot(monkeypatch):
    config = {
        "provider": "minimax",
        "model": "MiniMax-M3",
        "api_key": "key-a",
        "model_pool": [
            {"provider": "minimax", "model": "MiniMax-M3", "api_key": "key-a", "label": "a"},
            {"provider": "minimax", "model": "MiniMax-M3", "api_key": "key-b", "label": "b"},
        ],
    }
    monkeypatch.setattr(llm_mod, "load_model_config", lambda: config)
    calls: list[tuple[str, dict]] = []
    auth_error = RuntimeError("unauthorized")
    auth_error.status_code = 401

    def make_client(item):
        return _Client(item["label"], calls, fail=auth_error if item["label"] == "a" else None)

    llm = llm_mod.CloudResearchLLM()
    monkeypatch.setattr(llm, "_client_for_config", make_client)
    await llm._completion({"model": "MiniMax-M3", "messages": []}, timeout_seconds=1)
    await llm._completion({"model": "MiniMax-M3", "messages": []}, timeout_seconds=1)

    assert [label for label, _ in calls] == ["a", "b", "b"]


def test_model_mapping_is_normalized_without_leaking_pool_keys(monkeypatch):
    monkeypatch.setenv(
        model_config.MODEL_POOL_ENV,
        '{"modelMapping":{"Moss":[{"baseUrl":"https://api.minimaxi.com/v1","apiKey":"secret-a","model":"MiniMax-M3"},{"baseUrl":"https://api.minimaxi.com/v1","apiKey":"secret-b","model":"MiniMax-M3"}]}}',
    )
    normalized = model_config._normalize_config(model_config._default_config())
    assert normalized["load_balance"] is True
    assert len(normalized["model_pool"]) == 2
    public = model_config.public_model_config(normalized)
    assert public.pool_size == 2
    assert public.load_balancing is True
    assert "secret-a" not in public.model_dump_json()
    assert "secret-b" not in public.model_dump_json()
