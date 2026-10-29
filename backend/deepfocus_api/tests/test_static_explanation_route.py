import json
import asyncio
from datetime import datetime, timezone

import pytest
from starlette.requests import Request

from deepfocus_api import main
from deepfocus_api.schemas import GeneralChatResponse


def request(path, message="市盈率与市净率有什么区别？请用三句话说明。", **extra):
    payload = json.dumps({"message": message, "roundtable": False, **extra}, ensure_ascii=False).encode()
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": payload, "more_body": False}

    return Request({"type": "http", "method": "POST", "path": path, "headers": [(b"content-type", b"application/json")], "client": ("127.0.0.1", 1), "query_string": b""}, receive)


@pytest.fixture
def simple_model(monkeypatch):
    calls = []

    async def general(req):
        calls.append(req)
        return GeneralChatResponse(provider="test", model="test", generated_at=datetime.now(timezone.utc), content="市盈率比较股价和每股利润，市净率比较股价和每股净资产。")

    async def forbidden(**kwargs):
        raise AssertionError("a standalone definition must not start live-data tools")

    async def general_stream(req):
        result = await general(req)
        yield result.content[:8]
        yield result.content[8:]

    monkeypatch.setattr(main.llm, "general_chat_stream", general_stream)
    monkeypatch.setattr(main.llm, "general_chat", general)
    monkeypatch.setattr(main.llm, "run_tool_agent", forbidden)
    monkeypatch.setattr(main, "_check_agent_quota", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "ifind_enhance_enabled", lambda *args, **kwargs: False)
    monkeypatch.setattr(main, "metrics_incr", lambda *args: None)
    monkeypatch.setattr(main, "_record_agent_chain", lambda **kwargs: None)
    if hasattr(main, "_start_ai_runtime_tracker"):
        monkeypatch.setattr(main, "_start_ai_runtime_tracker", lambda *args, **kwargs: lambda *a, **k: None)
    return calls


@pytest.mark.asyncio
async def test_definition_json_uses_one_tool_free_model_call(simple_model):
    result = await main.tool_research(request("/api/agents/tool-research"), _user=None)
    assert result["ok"]
    assert "每股利润" in result["answer"]
    assert result["tool_trace"] == []
    assert len(simple_model) == 1
    assert not simple_model[0].context


@pytest.mark.asyncio
async def test_definition_sse_keeps_final_contract_without_tools(simple_model):
    response = await main.tool_research_stream(request("/api/agents/tool-research/stream"), _user=None)
    chunks = [chunk async for chunk in response.body_iterator]
    text = "".join(chunk.decode() if isinstance(chunk, bytes) else chunk for chunk in chunks)
    assert "event: final" in text
    assert "每股利润" in text
    assert "event: tool_start" not in text
    assert len(simple_model) == 1

@pytest.mark.asyncio
async def test_definition_sse_delivers_body_before_the_model_finishes(simple_model,monkeypatch):
    release=asyncio.Event();finished=asyncio.Event()
    async def tokens(req):
        yield '先出现的正文。'
        await release.wait()
        yield '后出现的正文。'
        finished.set()
    monkeypatch.setattr(main.llm,'general_chat_stream',tokens)
    response=await main.tool_research_stream(request('/api/agents/tool-research/stream'),_user=None)
    frames=[]
    try:
        async for frame in response.body_iterator:
            frame=frame.decode() if isinstance(frame,bytes) else frame
            frames.append(frame)
            if 'event: answer_preview' in frame and not release.is_set():
                assert '先出现的正文' in frame and not finished.is_set()
                assert not any('event: final' in row for row in frames)
                release.set()
        assert any('event: final' in row and '后出现的正文' in row for row in frames)
    finally:
        release.set()
        await response.body_iterator.aclose()


def test_comparison_preserves_evidence_rounds_within_total_budget(monkeypatch):
    monkeypatch.delenv("DEEPFOCUS_WEB_QA_MAX_ROUNDS", raising=False)
    assert main._web_qa_round_budget("建滔集团和快手更偏向谁？") == 6
    assert main._web_qa_round_budget("贵州茅台股价多少？") == 4
    assert main._web_qa_round_budget("解释文件", has_attachment=True) == 6


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("question", ["你是谁", "你好，你是谁？", "你能做什么？", "你是什么模型？", "你好"])
async def test_assistant_info_never_calls_a_model_or_live_tool(simple_model, monkeypatch, stream, question):
    async def forbidden(*args, **kwargs):
        raise AssertionError("assistant identity must not invoke an LLM or tool")
    monkeypatch.setattr(main.llm, "general_chat", forbidden)
    monkeypatch.setattr(main.llm, "run_tool_agent", forbidden)
    monkeypatch.setattr(main, "_run_web_roundtable", forbidden)
    suffix = "/stream" if stream else ""
    req = request("/api/agents/tool-research" + suffix, question, roundtable=True,
                  history=[["贵州茅台估值", "旧行情数据不能污染本轮身份说明"]], symbol="600519")
    if stream:
        response = await main.tool_research_stream(req, _user=None)
        chunks = [chunk async for chunk in response.body_iterator]
        text = "".join(chunk.decode() if isinstance(chunk, bytes) else chunk for chunk in chunks)
        assert "event: final" in text
        assert "event: tool_start" not in text
        assert main.assistant_info_answer(question) in text
    else:
        result = await main.tool_research(req, _user=None)
        assert result["ok"]
        assert result["tool_trace"] == []
        assert main.assistant_info_answer(question) in result["answer"]
    assert not simple_model
