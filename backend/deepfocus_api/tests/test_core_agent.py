"""Regression tests for the shared CoreAgent boundary."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from deepfocus_api.core_agent import (
    CORE_AGENT_PROTOCOL_VERSION,
    CoreAgent,
    CoreAgentProfile,
    CoreAgentRequest,
    _invoke,
    is_read_only_tool,
)


class FakeLLM:
    provider_name = "test-provider"
    provider = "test-provider"
    model = "test-model"

    def __init__(self, tool_result=None):
        self.tool_result = tool_result
        self.tool_calls = []
        self.chat_calls = []

    async def run_tool_agent(self, **kwargs):
        self.tool_calls.append(kwargs)
        emit = kwargs.get("emit")
        if emit:
            await emit("tool_start", {"tool": "get_market_quote", "args": {"api_key": "do-not-log"}})
            await emit("tool_result", {"tool": "get_market_quote", "ok": True, "summary": "已取得行情"})
        return self.tool_result

    async def general_chat(self, request):
        self.chat_calls.append(request)
        from deepfocus_api.schemas import GeneralChatResponse
        from datetime import datetime, timezone
        return GeneralChatResponse(
            provider="test-provider",
            model="test-model",
            generated_at=datetime.now(timezone.utc),
            content="兼容回答",
        )

    async def general_chat_stream(self, request):
        yield "流式"
        yield "回答"


@pytest.mark.asyncio
async def test_research_run_records_order_and_enforces_allowlist():
    llm = FakeLLM({"answer": "有证据的回答", "tool_trace": [{"tool": "get_market_quote", "ok": True}]})
    agent = CoreAgent(llm, max_events=64)
    result = await agent.run_chat(
        CoreAgentRequest(objective="分析 AAPL", mode="research"),
    )

    assert result.ok is True
    assert result.route == "tool-agent"
    assert result.answer == "有证据的回答"
    assert llm.tool_calls
    assert "get_market_quote" in llm.tool_calls[0]["allowed_tool_names"]
    events = agent.events(result.run_id)
    assert events[0]["type"] == "run_start"
    assert events[-1]["type"] == "run_complete"
    assert [event["id"] for event in events] == list(dict.fromkeys(event["id"] for event in events))


@pytest.mark.asyncio
async def test_tool_failure_can_return_without_second_model_call():
    llm = FakeLLM(None)
    agent = CoreAgent(llm, max_events=64)
    result = await agent.run_tool_only(CoreAgentRequest(objective="查一下行情", mode="research"))

    assert result.status == "fallback"
    assert result.answer == ""
    assert llm.chat_calls == []
    assert any(event["type"] == "fallback" for event in agent.events(result.run_id))


@pytest.mark.asyncio
async def test_chat_falls_back_to_legacy_general_chat():
    llm = FakeLLM(None)
    agent = CoreAgent(llm, max_events=64)
    result = await agent.run_chat(
        CoreAgentRequest(objective="你好", mode="chat"),
        prefer_tool_agent=False,
    )

    assert result.ok is True
    assert result.route == "general-chat"
    assert result.answer == "兼容回答"
    assert len(llm.chat_calls) == 1


@pytest.mark.asyncio
async def test_adapter_preserves_raw_response_and_timeout_contract():
    llm = FakeLLM()
    agent = CoreAgent(llm, max_events=64)

    async def runner(request):
        return {"summary": "研报结论", "confidence": 0.8, "data_issues": ["缺少历史区间"]}

    result = await agent.run_adapter(
        CoreAgentRequest(objective="解读研报", mode="document"),
        runner,
        route="report-vision",
    )
    assert result.ok is True
    assert result.route == "report-vision"
    assert result.answer == "研报结论"
    assert result.raw["confidence"] == 0.8
    assert result.gaps == ["缺少历史区间"]


@pytest.mark.asyncio
async def test_stream_chat_uses_same_lifecycle():
    agent = CoreAgent(FakeLLM(), max_events=64)
    chunks = [chunk async for chunk in agent.stream_chat(CoreAgentRequest(objective="你好", mode="chat"))]
    assert chunks == ["流式", "回答"]
    events = agent.events()
    assert events[0]["type"] == "run_start"
    assert events[-1]["type"] == "run_complete"


def test_ledger_is_append_only_bounded_and_redacts_secrets(tmp_path: Path):
    ledger = tmp_path / "core.jsonl"
    agent = CoreAgent(FakeLLM(), ledger_path=ledger, max_events=32)
    import asyncio

    asyncio.run(agent._event(
        "run-test",
        "run_start",
        phase="orchestrator",
        title="test",
        payload={"api_key": "secret", "nested": {"token": "hidden"}},
    ))
    row = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
    assert row["payload"]["api_key"] == "[REDACTED]"
    assert row["payload"]["nested"]["token"] == "[REDACTED]"
    assert row["run_id"] == "run-test"


def test_read_only_policy_is_fail_closed():
    assert is_read_only_tool("get_market_quote")
    assert not is_read_only_tool("write_order")
    assert not is_read_only_tool("get_filesystem")
    assert CORE_AGENT_PROTOCOL_VERSION == "core-agent-v1"


@pytest.mark.asyncio
async def test_custom_profile_cannot_escape_global_tool_policy():
    llm = FakeLLM({"answer": "ok", "tool_trace": []})
    agent = CoreAgent(
        llm,
        profiles={
            "chat": CoreAgentProfile("chat", "test", frozenset({"write_order", "get_market_quote"})),
            "research": CoreAgentProfile("research", "test", frozenset({"write_order", "get_market_quote"})),
        },
    )
    result = await agent.run_chat(
        CoreAgentRequest(objective="查行情", mode="research"),
        prefer_tool_agent=True,
    )
    # The custom profile is still constrained by the global finance allowlist.
    assert result.route == "tool-agent"
    assert "write_order" not in llm.tool_calls[0]["allowed_tool_names"]
    assert "write_order" not in agent.status()["profiles"]["chat"]["allow_tools"]


@pytest.mark.asyncio
async def test_legacy_tool_adapter_is_not_retried_without_policy_keyword():
    """CoreAgent must fail closed when an injected adapter has the old signature."""

    class LegacyLLM(FakeLLM):
        def __init__(self):
            super().__init__(None)
            self.legacy_calls = 0

        async def run_tool_agent(self, *, question, context_hint="", timeout_seconds=30, emit=None, ifind_user=False, max_rounds=4):
            self.legacy_calls += 1
            return {"answer": "不应被执行", "tool_trace": []}

    llm = LegacyLLM()
    agent = CoreAgent(llm, max_events=64)
    result = await agent.run_chat(
        CoreAgentRequest(objective="查一下行情", mode="research"),
        prefer_tool_agent=True,
    )

    assert llm.legacy_calls == 0
    assert result.route == "general-chat"
    assert result.answer == "兼容回答"
    assert any(event["type"] == "error" for event in agent.events(result.run_id))


@pytest.mark.asyncio
async def test_adapter_typeerror_is_not_retried_with_a_second_arity():
    """A runner's own TypeError must not cause a duplicate invocation."""
    calls = 0

    async def runner(_request):
        nonlocal calls
        calls += 1
        raise TypeError("runner bug")

    with pytest.raises(TypeError, match="runner bug"):
        await _invoke(runner, CoreAgentRequest(objective="test"))
    assert calls == 1
