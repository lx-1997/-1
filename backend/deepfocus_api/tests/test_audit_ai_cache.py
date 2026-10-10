"""同一问题可能聚合私人持仓：工具闭环与流式研究缓存必须按账号隔离。"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from deepfocus_api import agent_loop, llm as llm_module, risk_management as risk
from deepfocus_api.cross_module_aggregator import gather_risk_context
from deepfocus_api.ownership import bind_owner, current_owner_id


@pytest.fixture(autouse=True)
def private_positions(tmp_path, monkeypatch):
    monkeypatch.setattr(risk, "DB_PATH", tmp_path / "risk.sqlite3")
    monkeypatch.setattr(agent_loop, "_LOOP_CACHE", {})
    monkeypatch.setattr(llm_module, "_TOOL_ANSWER_CACHE", {})
    risk.create_position("NVDA", entry_price=111, quantity=1, owner_user_id="alice")
    risk.create_position("NVDA", entry_price=222, quantity=1, owner_user_id="bob")


def _private_marker(prices):
    if prices == [111.0]:
        return "ALICE_PRIVATE"
    if prices == [222.0]:
        return "BOB_PRIVATE"
    assert prices == [], "private data from multiple accounts entered the same answer"
    return "PUBLIC_EMPTY"


def test_tool_answer_cache_reuses_own_result_without_leaking_to_other_account(monkeypatch):
    calls = []

    class Completions:
        async def create(self, **kwargs):
            messages = [item for item in kwargs["messages"] if item["role"] == "tool"]
            if messages:
                evidence = json.loads(messages[-1]["content"])["data"]
                answer = _private_marker([item["entry_price"] for item in evidence["positions"]])
                message = SimpleNamespace(content=answer, tool_calls=None)
            else:
                tool = SimpleNamespace(id="risk-evidence", type="function", function=SimpleNamespace(name="get_stock_snapshot", arguments='{"symbol":"NVDA"}'))
                message = SimpleNamespace(content=None, tool_calls=[tool])
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    async def execute(name, arguments, **kwargs):
        assert name == "get_stock_snapshot"
        calls.append(current_owner_id())
        return {"ok": True, "data": await gather_risk_context([arguments["symbol"]])}

    async def no_mcp_tools():
        return []

    monkeypatch.setattr(llm_module, "load_model_config", lambda: {"provider": "openai", "model": "test-model", "api_key": "fake-test-key", "temperature": 0.2})
    monkeypatch.setattr(llm_module, "discover_mcp_agent_tools", no_mcp_tools)
    monkeypatch.setattr(llm_module, "execute_tool", execute)
    llm = llm_module.CloudResearchLLM()
    monkeypatch.setattr(llm, "_client", lambda: SimpleNamespace(chat=SimpleNamespace(completions=Completions())))

    async def ask(owner):
        with bind_owner(owner):
            return await llm.run_tool_agent(question="分析NVDA基本面")

    a = asyncio.run(ask("alice"))
    b = asyncio.run(ask("bob"))
    a_again = asyncio.run(ask("alice"))
    public = asyncio.run(ask(None))
    assert a["answer"] == a_again["answer"] == "ALICE_PRIVATE"
    assert a_again["cached"] is True
    assert b["answer"] == "BOB_PRIVATE" and not b.get("cached")
    assert public["answer"] == "PUBLIC_EMPTY"
    assert calls == ["alice", "bob", None]  # A 重复提问没有重复取数。


def test_research_loop_cache_reads_real_owner_scoped_risk_and_replays_own_answer(monkeypatch):
    monkeypatch.setattr(agent_loop, "AVAILABLE_MODULES", ["risk"])

    class LLM:
        def __init__(self):
            self.calls = []

        async def complete_json(self, prompt, **kwargs):
            self.calls.append(current_owner_id())
            if "modules_order" in prompt:
                return {"modules_order": ["risk"], "data_budget": 1}
            has_a, has_b = "@111.0" in prompt, "@222.0" in prompt
            assert not (has_a and has_b), "both users' private costs reached the same prompt"
            marker = "ALICE_PRIVATE" if has_a else "BOB_PRIVATE" if has_b else "PUBLIC_EMPTY"
            if "momentum_signal" in prompt:
                return {"key_insight": marker, "need_another_round": False, "confidence_contribution": 0.2}
            return {"executive_summary": marker, "recommendation": "hold", "confidence": 0.5}

    llm = LLM()

    async def ask(owner):
        with bind_owner(owner):
            events = [event async for event in agent_loop.run_agent_research_loop(llm, "NVDA", "分析NVDA基本面")]
            decoded = [json.loads(next(line[6:] for line in event.splitlines() if line.startswith("data: "))) for event in events]
            return next(item for item in decoded if "executive_summary" in item)

    a = asyncio.run(ask("alice"))
    b = asyncio.run(ask("bob"))
    a_again = asyncio.run(ask("alice"))
    public = asyncio.run(ask(None))
    assert a["executive_summary"] == a_again["executive_summary"] == "ALICE_PRIVATE"
    assert a_again["cached"] is True
    assert b["executive_summary"] == "BOB_PRIVATE" and not b.get("cached")
    assert public["executive_summary"] == "PUBLIC_EMPTY"
    assert llm.calls == ["alice"] * 3 + ["bob"] * 3 + [None] * 3
