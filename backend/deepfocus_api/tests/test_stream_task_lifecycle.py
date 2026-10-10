import asyncio
import json

import pytest
from starlette.requests import Request

from deepfocus_api import cost_quotas as quotas, main, portfolio_api, research_stream
from deepfocus_api.schemas import ResearchDeepDraftRequest, ResearchDeepDraftResponse


@pytest.fixture(autouse=True)
def isolated_dependencies(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPFOCUS_QUOTA_DB_PATH", str(tmp_path / "quotas.sqlite3"))
    monkeypatch.setattr(main, "deep_draft_cache_key", lambda request: "draft-test")
    monkeypatch.setattr(main, "legacy_deep_draft_cache_key", lambda request: "")
    monkeypatch.setattr(main, "metrics_get_ai_cache", lambda key: None)
    monkeypatch.setattr(main, "metrics_set_ai_cache", lambda *args: None)
    monkeypatch.setattr(main, "metrics_incr", lambda *args: None)
    monkeypatch.setattr(main, "metrics_incr_ai_ref", lambda *args: None)
    monkeypatch.setattr(main, "_check_ai_quota", lambda *args, **kwargs: quotas.reserve("draft-test", 1))
    monkeypatch.setattr(main, "_complete_cost_quota", quotas.complete)
    async def resolve(request):
        return []
    monkeypatch.setattr(research_stream, "resolve_source_documents", resolve)


def _request():
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""})


@pytest.mark.asyncio
async def test_research_stream_delivers_first_model_fragment(monkeypatch):
    async def generate(request, documents, progress):
        progress("llm_delta", "first fragment")
        await asyncio.sleep(0.01)
        return ResearchDeepDraftResponse(title="test", provider="test")
    monkeypatch.setattr(research_stream, "generate_deep_draft", generate)
    response = await research_stream.api_research_deep_draft_stream(ResearchDeepDraftRequest(), _request(), None)
    events = [json.loads(chunk.removeprefix("data: ").strip()) async for chunk in response.body_iterator]
    assert any(event.get("delta") == "first fragment" for event in events)
    assert events[-1]["type"] == "done"
    assert quotas.used("draft-test") == 1


@pytest.mark.asyncio
async def test_research_stream_close_awaits_generation_and_queue_waiters(monkeypatch):
    original_wait = asyncio.wait
    async def fast_wait(tasks, *, timeout, return_when):
        return await original_wait(tasks, timeout=0.002, return_when=return_when)
    monkeypatch.setattr(research_stream.asyncio, "wait", fast_wait)
    execution_closed = asyncio.Event()
    async def generate(request, documents, progress):
        try:
            await asyncio.Event().wait()
        finally:
            execution_closed.set()
    monkeypatch.setattr(research_stream, "generate_deep_draft", generate)
    prior = set(asyncio.all_tasks())
    response = await research_stream.api_research_deep_draft_stream(ResearchDeepDraftRequest(), _request(), None)
    ticks = 0
    async for chunk in response.body_iterator:
        event = json.loads(chunk.removeprefix("data: ").strip())
        if event["type"] == "tick":
            ticks += 1
            if ticks == 3:
                break
    await response.body_iterator.aclose()
    assert execution_closed.is_set()
    assert quotas.used("draft-test") == 0
    pending = [task for task in asyncio.all_tasks() - prior if not task.done()]
    assert pending == []


@pytest.mark.asyncio
@pytest.mark.parametrize("disconnect_before_first_yield", [False, True])
async def test_backtest_stream_close_closes_owned_execution(monkeypatch, disconnect_before_first_yield):
    execution_closed = asyncio.Event()
    monkeypatch.setattr(portfolio_api, "get_backtest", lambda *args, **kwargs: {"status": "pending"})
    async def run(backtest_id, request):
        try:
            yield "first event"
            yield "second event"
        finally:
            execution_closed.set()
    monkeypatch.setattr(portfolio_api, "run_backtest", run)
    class Client:
        async def is_disconnected(self):
            return disconnect_before_first_yield
    response = await portfolio_api.backtest_run("bt-1", Client(), {"sub": "user-a"})
    if disconnect_before_first_yield:
        assert [chunk async for chunk in response.body_iterator] == []
    else:
        assert await response.body_iterator.__anext__() == "first event"
        await response.body_iterator.aclose()
    assert execution_closed.is_set()
