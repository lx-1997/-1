import asyncio
import json

import pytest
from starlette.requests import Request

from deepfocus_api import cost_quotas as quotas, main, research_stream
from deepfocus_api.schemas import ResearchDeepDraftRequest, ResearchDeepDraftResponse


QUICK = {"one_liner": "Useful preview", "quick": True, "provider": "test-quick"}


@pytest.fixture(autouse=True)
def isolated_dependencies(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPFOCUS_QUOTA_DB_PATH", str(tmp_path / "quotas.sqlite3"))
    monkeypatch.setattr(main, "deep_draft_cache_key", lambda request: "quick-test")
    monkeypatch.setattr(main, "legacy_deep_draft_cache_key", lambda request: "")
    monkeypatch.setattr(main, "metrics_get_ai_cache", lambda key: None)
    for name in ("metrics_set_ai_cache", "metrics_incr", "metrics_incr_ai_ref"):
        monkeypatch.setattr(main, name, lambda *args: None)
    monkeypatch.setattr(main, "_check_ai_quota", lambda *args, **kwargs: quotas.reserve("quick-test", 1))
    monkeypatch.setattr(main, "_complete_cost_quota", quotas.complete)

    async def resolve(request):
        return []

    async def quick(request, documents):
        return QUICK

    async def deep(request, documents, progress):
        return ResearchDeepDraftResponse(title="Deep result", provider="test-deep")

    monkeypatch.setattr(research_stream, "resolve_source_documents", resolve)
    monkeypatch.setattr(research_stream, "generate_deep_quick", quick)
    monkeypatch.setattr(research_stream, "generate_deep_draft", deep)


async def _response():
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""})
    return await research_stream.api_research_deep_draft_stream(ResearchDeepDraftRequest(), request, None)


def _event(chunk):
    return json.loads(chunk.removeprefix("data: ").strip())


@pytest.mark.asyncio
async def test_quick_and_deep_success_consume_one_quota():
    response = await _response()
    events = [_event(chunk) async for chunk in response.body_iterator]
    assert [event["type"] for event in events if event["type"] in {"quick", "done"}] == ["quick", "done"]
    assert quotas.used("quick-test") == 1
    assert response._quota_scope.heartbeat.done()


@pytest.mark.asyncio
@pytest.mark.parametrize("cached", [False, True])
async def test_disconnect_after_delivered_quick_keeps_one_quota_without_deep_execution(monkeypatch, cached):
    if cached:
        monkeypatch.setattr(main, "metrics_get_ai_cache", lambda key: QUICK if key == "quick:quick-test" else None)
    deep_started = False

    async def deep(request, documents, progress):
        nonlocal deep_started
        deep_started = True
        await asyncio.Event().wait()

    monkeypatch.setattr(research_stream, "generate_deep_draft", deep)
    prior = set(asyncio.all_tasks())
    response = await _response()
    async for chunk in response.body_iterator:
        if _event(chunk)["type"] == "quick":
            break
    await response.body_iterator.aclose()
    assert quotas.used("quick-test") == 1
    assert not deep_started
    assert not [task for task in asyncio.all_tasks() - prior if not task.done()]


@pytest.mark.asyncio
async def test_disconnect_during_quick_cancels_execution_and_refunds_quota(monkeypatch):
    started, closed = asyncio.Event(), asyncio.Event()

    async def quick(request, documents):
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            closed.set()

    monkeypatch.setattr(research_stream, "generate_deep_quick", quick)
    prior = set(asyncio.all_tasks())
    response = await _response()
    async for chunk in response.body_iterator:
        if _event(chunk).get("stage") == "quick":
            break
    next_event = asyncio.create_task(response.body_iterator.__anext__())
    await asyncio.wait_for(started.wait(), timeout=1)
    next_event.cancel()
    with pytest.raises(asyncio.CancelledError):
        await next_event
    assert closed.is_set()
    assert quotas.used("quick-test") == 0
    assert not [task for task in asyncio.all_tasks() - prior if not task.done()]


@pytest.mark.asyncio
async def test_failed_deep_draft_after_delivered_quick_keeps_one_quota(monkeypatch):
    async def deep(request, documents, progress):
        raise RuntimeError("Deep provider failed")

    monkeypatch.setattr(research_stream, "generate_deep_draft", deep)
    response = await _response()
    events = [_event(chunk) async for chunk in response.body_iterator]
    assert any(event["type"] == "quick" for event in events)
    assert events[-1]["type"] == "error"
    assert quotas.used("quick-test") == 1
