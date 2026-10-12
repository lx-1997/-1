from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from deepfocus_api import agent_tools, ashare_review, llm as llm_mod, llm_runtime


def _candidates():
    return [{"name": "半导体", "kind": "sector", "pct": 2.1, "signals": [
        {"title": "芯片需求新增订单证据", "topic": "快讯", "age_hours": 20},
    ]}]


def _message(content=None, tools=False):
    calls = [SimpleNamespace(id="search-1", function=SimpleNamespace(name="search_our_content", arguments='{"query":"芯片订单"}'))] if tools else []
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=calls))])


def _config(monkeypatch):
    primary = {"provider": "openai-compatible", "model": "qwen3.6-flash", "api_key": "ashare-primary-test", "base_url": "https://primary.invalid/v1"}
    backup = {"provider": "openai-compatible", "model": "glm-5.3-flash", "api_key": "ashare-backup-test", "base_url": "https://backup.invalid/v1"}
    monkeypatch.setattr(llm_mod, "load_model_config", lambda: {**primary, "model_pool": [primary, backup], "temperature": .2})
    monkeypatch.setitem(agent_tools.TOOL_REGISTRY, "search_our_content", SimpleNamespace(openai_spec=lambda: {"type": "function", "function": {"name": "search_our_content"}}))
    async def execute(name, args):
        return {"ok": True, "sources": [{"title": "芯片订单增长证据"}]}
    monkeypatch.setattr(agent_tools, "execute_tool", execute)
    return primary, backup


@pytest.mark.asyncio
async def test_tool_rounds_and_final_use_matching_backup_endpoint_and_model(monkeypatch):
    primary, backup = _config(monkeypatch)
    error = RuntimeError("insufficient_quota")
    error.status_code = 429
    llm_runtime.cooldown_slot(primary, error)
    requests = []

    def make_client(config):
        async def create(**payload):
            requests.append((config["base_url"], payload))
            if "tools" in payload:
                return _message(tools=True)
            return _message('{"keep":[{"i":0,"subs":[0],"reason":"订单催化有提前发布的内容支撑"}]}')
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    monkeypatch.setattr(llm_mod.CloudResearchLLM, "_client_for_config", staticmethod(make_client))
    result = await ashare_review._agentic_linkages(_candidates(), datetime.now(timezone.utc))
    assert len(requests) == 5  # Four evidence rounds, then the existing final judge.
    assert all(endpoint == backup["base_url"] and payload["model"] == backup["model"] for endpoint, payload in requests)
    assert all(payload["extra_body"]["thinking"] == {"type": "disabled"} for _, payload in requests)
    assert "tools" not in requests[-1][1]
    assert len([message for message in requests[-1][1]["messages"] if message["role"] == "tool"]) == 4
    assert result[0]["evidence"] == 1 and "订单催化" in result[0]["reason"]


@pytest.mark.asyncio
async def test_rounds_share_deadline_and_keep_quality_fallback(monkeypatch):
    _config(monkeypatch)
    budgets = []
    def deadline(seconds):
        budgets.append(seconds)
        return llm_runtime.llm_deadline(.04)
    monkeypatch.setattr(ashare_review, "llm_deadline", deadline)
    calls = []
    async def create(**payload):
        calls.append(payload)
        await asyncio.sleep(.025)
        return _message(tools=True)
    monkeypatch.setattr(llm_mod.CloudResearchLLM, "_client_for_config", staticmethod(lambda config: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))))
    started = time.monotonic()
    assert await ashare_review._agentic_linkages(_candidates(), datetime.now(timezone.utc)) is None
    assert time.monotonic() - started < .09
    assert budgets == [45.0] and len(calls) == 2


@pytest.mark.asyncio
async def test_agentic_cancellation_propagates(monkeypatch):
    _config(monkeypatch)
    entered = asyncio.Event()
    async def create(**payload):
        entered.set()
        await asyncio.sleep(30)
    monkeypatch.setattr(llm_mod.CloudResearchLLM, "_client_for_config", staticmethod(lambda config: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))))
    task = asyncio.create_task(ashare_review._agentic_linkages(_candidates(), datetime.now(timezone.utc)))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
