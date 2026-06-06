"""AI 原生 tool-use 闭环（CloudResearchLLM.run_tool_agent）的回归守卫。

用伪 OpenAI 客户端模拟 tool_calls，验证闭环真的：选工具→执行→把结果回灌进 messages→再推理→
正确终止；并锁定优雅降级（mock / 异常 → None，调用方回退）与结果到 Orchestrator 回复的映射。
不触网：execute_tool 被替换成记录型桩，只测「循环编排」本身。
"""
from __future__ import annotations

import asyncio

from deepfocus_api import agent_tools
from deepfocus_api import llm as llm_mod
# 在任何 asyncio.run() 之前导入 main：它会注册 get_stock_verdict 工具，且其依赖链里有
# 模块级 asyncio.Lock()，需在事件循环仍存在时（即文件顶部）导入，否则首次惰性导入会失败。
from deepfocus_api import main as main_app
from deepfocus_api.llm import CloudResearchLLM, tool_agent_to_orchestrator_response
from deepfocus_api.schemas import (
    OrchestratorChatRequest,
    TearSheetDimension,
    TearSheetResponse,
)


# --- 伪 OpenAI 客户端 -------------------------------------------------------
class _FakeFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, call_id, name, arguments):
        self.id = call_id
        self.type = "function"
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _FakeResponse:
    def __init__(self, message):
        self.choices = [type("C", (), {"message": message})()]


class _FakeCompletions:
    def __init__(self, script):
        self._script = list(script)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._script:
            raise AssertionError("伪客户端被多调用了一次（脚本耗尽）")
        return self._script.pop(0)


class _FakeClient:
    def __init__(self, script):
        self.chat = type("Chat", (), {"completions": _FakeCompletions(script)})()


async def _no_mcp_tools():
    return []


def _make_llm(monkeypatch, *, provider="openai", script=None, fake_execute=None):
    monkeypatch.setattr(
        llm_mod, "load_model_config",
        lambda: {"provider": provider, "model": "gpt-test", "api_key": "x", "base_url": None, "temperature": 0.2},
    )
    # 闭环测试默认不触 MCP（避免读 sqlite）；MCP 合并另有专门测试覆盖。
    monkeypatch.setattr(llm_mod, "discover_mcp_agent_tools", _no_mcp_tools)
    llm = CloudResearchLLM()
    if script is not None:
        client = _FakeClient(script)
        monkeypatch.setattr(llm, "_client", lambda: client)
        llm._test_client = client  # 便于断言 create 调用
    if fake_execute is not None:
        monkeypatch.setattr(llm_mod, "execute_tool", fake_execute)
    return llm


def _recording_execute(results=None):
    calls: list[tuple[str, dict]] = []
    default = {"ok": True, "data": {"price": 195.0, "change_percent": 1.2}}

    async def fake(name, arguments, extra_tools=None):
        calls.append((name, dict(arguments or {})))
        return (results or {}).get(name, default)

    fake.calls = calls
    return fake


# --- 闭环编排 ---------------------------------------------------------------
def test_single_tool_call_then_answer(monkeypatch):
    script = [
        _FakeResponse(_FakeMessage(content=None, tool_calls=[
            _FakeToolCall("c1", "get_market_quote", '{"symbol": "AAPL"}'),
        ])),
        _FakeResponse(_FakeMessage(content="AAPL 现价 195，动能偏强。", tool_calls=None)),
    ]
    execute = _recording_execute()
    llm = _make_llm(monkeypatch, script=script, fake_execute=execute)

    result = asyncio.run(llm.run_tool_agent(question="分析 AAPL"))

    assert result is not None
    assert result["answer"] == "AAPL 现价 195，动能偏强。"
    assert result["truncated"] is False
    assert result["rounds"] == 1
    # 工具被执行了一次，参数透传正确。
    assert execute.calls == [("get_market_quote", {"symbol": "AAPL"})]
    # trace 记录了这次调用。
    assert len(result["tool_trace"]) == 1
    assert result["tool_trace"][0]["tool"] == "get_market_quote"
    assert result["tool_trace"][0]["ok"] is True
    # 关键：第二次 create 的 messages 里含有把工具结果回灌的 role=tool 消息。
    second_call_messages = llm._test_client.chat.completions.calls[1]["messages"]
    roles = [m["role"] for m in second_call_messages]
    assert "tool" in roles
    tool_msg = next(m for m in second_call_messages if m["role"] == "tool")
    assert tool_msg["tool_call_id"] == "c1"
    assert "195" in tool_msg["content"]
    # 第一次 create 带了 tools 参数（真的开启了 function-calling）。
    assert llm._test_client.chat.completions.calls[0].get("tools")


def test_run_tool_agent_merges_and_dispatches_mcp_tools(monkeypatch):
    # 用真实 dispatch（不 patch execute_tool），patch discover 注入一个 MCP 工具，
    # 验证它① 进了 tools spec ② 被真实执行 ③ 进了 trace。
    called = {}

    async def mcp_handler(**kwargs):
        called["args"] = kwargs
        return {"result": "ok"}

    mcp_tool = agent_tools.AgentTool(
        name="mcp__srv__do_thing", description="[MCP·S] 干活",
        parameters={"type": "object", "properties": {"x": {"type": "integer"}}},
        handler=mcp_handler,
    )

    async def fake_discover():
        return [mcp_tool]

    script = [
        _FakeResponse(_FakeMessage(tool_calls=[_FakeToolCall("c1", "mcp__srv__do_thing", '{"x": 1}')])),
        _FakeResponse(_FakeMessage(content="完成。", tool_calls=None)),
    ]
    monkeypatch.setattr(
        llm_mod, "load_model_config",
        lambda: {"provider": "openai", "model": "m", "api_key": "x", "base_url": None, "temperature": 0.2},
    )
    monkeypatch.setattr(llm_mod, "discover_mcp_agent_tools", fake_discover)
    llm = CloudResearchLLM()
    client = _FakeClient(script)
    monkeypatch.setattr(llm, "_client", lambda: client)

    result = asyncio.run(llm.run_tool_agent(question="干活"))

    assert result["answer"] == "完成。"
    assert called["args"] == {"x": 1}  # MCP 工具被真实执行
    specs = client.chat.completions.calls[0]["tools"]
    assert any(s["function"]["name"] == "mcp__srv__do_thing" for s in specs)  # 已并入 tools
    assert result["tool_trace"][0]["tool"] == "mcp__srv__do_thing"
    assert result["tool_trace"][0]["ok"] is True


def test_no_tool_call_answers_directly(monkeypatch):
    script = [_FakeResponse(_FakeMessage(content="你好，我是投研助手。", tool_calls=None))]
    execute = _recording_execute()
    llm = _make_llm(monkeypatch, script=script, fake_execute=execute)

    result = asyncio.run(llm.run_tool_agent(question="在吗"))

    assert result["answer"] == "你好，我是投研助手。"
    assert result["rounds"] == 0
    assert result["truncated"] is False
    assert result["tool_trace"] == []
    assert execute.calls == []  # 没触发任何工具


def test_max_rounds_truncates_with_final_synthesis(monkeypatch):
    # 连续两轮都要工具 → 用满 max_rounds=2 → 去 tools 强制出最终结论（第 3 次 create）。
    script = [
        _FakeResponse(_FakeMessage(tool_calls=[_FakeToolCall("c1", "get_market_quote", '{"symbol":"AAPL"}')])),
        _FakeResponse(_FakeMessage(tool_calls=[_FakeToolCall("c2", "get_valuation", '{"symbol":"AAPL"}')])),
        _FakeResponse(_FakeMessage(content="综合：估值合理。", tool_calls=None)),
    ]
    execute = _recording_execute()
    llm = _make_llm(monkeypatch, script=script, fake_execute=execute)

    result = asyncio.run(llm.run_tool_agent(question="分析 AAPL", max_rounds=2))

    assert result["truncated"] is True
    assert result["rounds"] == 2
    assert result["answer"] == "综合：估值合理。"
    assert len(result["tool_trace"]) == 2
    assert len(llm._test_client.chat.completions.calls) == 3
    # 最后一次 create 是无 tools 的收尾合成。
    assert not llm._test_client.chat.completions.calls[2].get("tools")


def test_bad_tool_arguments_do_not_break_loop(monkeypatch):
    # 模型给了非法 JSON 参数 → 解析成 {}，仍能执行并继续。
    script = [
        _FakeResponse(_FakeMessage(tool_calls=[_FakeToolCall("c1", "get_market_quote", "not-json")])),
        _FakeResponse(_FakeMessage(content="已尽力作答。", tool_calls=None)),
    ]
    execute = _recording_execute()
    llm = _make_llm(monkeypatch, script=script, fake_execute=execute)

    result = asyncio.run(llm.run_tool_agent(question="分析"))

    assert result["answer"] == "已尽力作答。"
    assert execute.calls == [("get_market_quote", {})]  # 坏参数降级为空 dict


# --- 优雅降级（红线）-------------------------------------------------------
def test_mock_provider_returns_none(monkeypatch):
    llm = _make_llm(monkeypatch, provider="mock")
    assert asyncio.run(llm.run_tool_agent(question="分析 AAPL")) is None


def test_client_exception_returns_none(monkeypatch):
    class _BoomCompletions:
        async def create(self, **kwargs):
            raise RuntimeError("tools is not supported by this model")

    llm = _make_llm(monkeypatch)
    boom = type("Client", (), {"chat": type("Chat", (), {"completions": _BoomCompletions()})()})()
    monkeypatch.setattr(llm, "_client", lambda: boom)

    assert asyncio.run(llm.run_tool_agent(question="分析 AAPL")) is None


# --- 结果 → Orchestrator 回复映射 ------------------------------------------
def test_mapper_builds_response_with_tool_trace():
    req = OrchestratorChatRequest(message="分析 AAPL")
    result = {
        "answer": "AAPL 现价 195，估值合理。",
        "tool_trace": [
            {"tool": "get_market_quote", "ok": True, "summary": "命中字段：quotes"},
            {"tool": "get_fund_flow", "ok": False, "summary": "暂无数据（已优雅降级）"},
        ],
        "rounds": 2,
        "truncated": False,
    }
    resp = tool_agent_to_orchestrator_response(result, req, "minimax", "gpt-test")

    assert resp is not None
    assert "195" in resp.content
    assert resp.handled_inline is True
    assert resp.should_create_task is False
    # 两次工具 + 一个综合步骤 = 3 步，且失败的工具映射为 error 状态。
    assert len(resp.reasoning_trace) == 3
    assert resp.reasoning_trace[0].status == "done"
    assert resp.reasoning_trace[1].status == "error"
    assert resp.reasoning_trace[-1].phase == "synthesis"


def test_mapper_empty_answer_returns_none():
    req = OrchestratorChatRequest(message="分析 AAPL")
    assert tool_agent_to_orchestrator_response({"answer": "  ", "tool_trace": []}, req, "minimax", "m") is None


# --- 工具注册表自描述 -------------------------------------------------------
def test_tool_registry_specs_shape():
    names = [t.name for t in agent_tools.list_tools()]
    assert "get_market_quote" in names and "get_valuation" in names
    specs = agent_tools.openai_tool_specs()
    assert all(s["type"] == "function" for s in specs)
    assert all("parameters" in s["function"] and "name" in s["function"] for s in specs)


def test_execute_tool_unknown_and_bad_args_degrade_gracefully():
    assert asyncio.run(agent_tools.execute_tool("nope", {}))["ok"] is False
    assert asyncio.run(agent_tools.execute_tool("get_market_quote", {"bad": 1}))["ok"] is False


# --- 皇冠工具：get_stock_verdict（确定性速判卡 verdict）---------------------
def test_get_stock_verdict_tool_returns_ground_truth(monkeypatch):
    # main 在文件顶部已导入 → get_stock_verdict 已注册进共享 TOOL_REGISTRY。
    from datetime import datetime, timezone

    main = main_app
    assert "get_stock_verdict" in [t.name for t in agent_tools.list_tools()]

    fake = TearSheetResponse(
        symbol="AAPL", name="Apple", generated_at=datetime.now(timezone.utc),
        price=195.0, change_percent=1.2,
        overall_verdict="重点跟踪", overall_score=42, confidence=0.71,
        dimensions=[
            TearSheetDimension(key="momentum", label="动能", signal="bullish", score=40, headline="强势", confidence=0.8),
        ],
    )

    async def stub_core(symbol, market=""):
        return fake

    # handler 内按名解析 main._build_stock_tear_sheet_core，替换即生效；不触网、不触发 LLM 叙述。
    monkeypatch.setattr(main, "_build_stock_tear_sheet_core", stub_core)

    out = asyncio.run(agent_tools.execute_tool("get_stock_verdict", {"symbol": "AAPL"}))

    assert out["ok"] is True
    data = out["data"]
    # verdict/score/confidence 是确定性引擎的 ground truth，原样透传。
    assert data["overall_verdict"] == "重点跟踪"
    assert data["overall_score"] == 42
    assert data["confidence"] == 0.71
    # 维度压成紧凑信号（label/signal/score/headline/confidence）。
    assert data["dimensions"][0]["label"] == "动能"
    assert data["dimensions"][0]["signal"] == "bullish"
    # 紧凑返回不含 narrative（不把 LLM 输出回灌给 LLM）。
    assert "narrative" not in data
