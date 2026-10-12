import asyncio
import time

import pytest

from deepfocus_api.core_agent import CoreAgent, CoreAgentRequest


class SlowLLM:
    provider_name = provider = "test"
    model = "test"

    def __init__(self):
        self.cancelled = False
        self.fallback_calls = 0

    async def run_tool_agent(self, **kwargs):
        try:
            await asyncio.sleep(10)
        finally:
            self.cancelled = True

    async def general_chat(self, request):
        self.fallback_calls += 1
        return {"content": "should not run after the budget is spent"}


@pytest.mark.asyncio
async def test_tool_timeout_cancels_work_and_does_not_start_fallback():
    llm = SlowLLM()
    agent = CoreAgent(llm)
    started = time.monotonic()
    result = await agent.run_chat(CoreAgentRequest(objective="研究", mode="research", timeout_seconds=0.02))
    assert result.status == "failed"
    assert "时间预算" in result.error
    assert llm.cancelled
    assert llm.fallback_calls == 0
    assert time.monotonic() - started < 0.2


@pytest.mark.asyncio
async def test_fallback_uses_remaining_turn_budget():
    class EmptySlowLLM(SlowLLM):
        async def run_tool_agent(self, **kwargs):
            await asyncio.sleep(0.025)
            return None

        async def general_chat(self, request):
            self.fallback_calls += 1
            await asyncio.sleep(10)

    llm = EmptySlowLLM()
    started = time.monotonic()
    result = await CoreAgent(llm).run_chat(CoreAgentRequest(objective="研究", mode="research", timeout_seconds=0.04))
    assert result.status == "failed"
    assert llm.fallback_calls == 1
    assert time.monotonic() - started < 0.15


@pytest.mark.asyncio
async def test_user_cancellation_propagates_without_fallback():
    llm = SlowLLM()
    task = asyncio.create_task(CoreAgent(llm).run_tool_only(CoreAgentRequest(objective="研究", mode="research")))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert llm.cancelled
    assert llm.fallback_calls == 0


@pytest.mark.asyncio
async def test_spent_startup_budget_does_not_start_a_model_call():
    class InstantLLM(SlowLLM):
        calls = 0

        async def run_tool_agent(self, **kwargs):
            self.calls += 1
            return {"answer": "too late"}

    async def slow_progress(*args):
        await asyncio.sleep(0.01)

    llm = InstantLLM()
    result = await CoreAgent(llm).run_tool_only(
        CoreAgentRequest(objective="研究", mode="research", timeout_seconds=0.005),
        emit=slow_progress,
    )
    assert result.status == "failed"
    assert llm.calls == 0
