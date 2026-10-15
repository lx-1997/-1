import asyncio
import copy
import json

import pytest
from starlette.requests import Request

from deepfocus_api.analysis_limiter import AnalysisBusy, AnalysisLimiter
from deepfocus_api.research_cache import (
    deep_draft_cache_key,
    is_valid_deep_draft_cache,
    legacy_deep_draft_cache_key,
)
from deepfocus_api.research_prewarm_runtime import background_deep_prewarm, deep_to_compact
from deepfocus_api.schemas import ResearchDeepDraftRequest


def draft():
    return {
        "mode": "deep_draft", "title": "行业研究", "subject": "行业",
        "one_liner": "原文核心结论", "executive_summary": "原文摘要",
        "core_conclusion": "原文结论", "confidence": 0.7, "pages_analyzed": 18,
        "provider": "test-model", "instruments": ["600000.SH"],
        "sections": [{"id": "s1", "title": "需求", "summary": "原文需求摘要", "bullets": ["原文要点"], "evidence": [{"page": 3, "excerpt": "原文证据"}]}],
        "source_coverage": {"pages_read": 18, "chars_read": 1234, "source_count": 1},
        "risks": [{"title": "需求风险", "detail": "原文风险"}],
    }


def report_request(**overrides):
    return ResearchDeepDraftRequest(**{
        "file_id": "fid-1", "title": "行业研究", "filename": "industry.pdf",
        "max_pages": 18, **overrides,
    })


@pytest.mark.parametrize("change", [
    {"mode": "compact"}, {"sections": None}, {"source_coverage": None},
    {"provider": "local-fallback"}, {"provider": "text-only-fallback"},
    {"vision_unavailable": True}, {"confidence": -1},
])
def test_shared_validator_rejects_torn_and_fallback_results(change):
    assert not is_valid_deep_draft_cache({**draft(), **change})
    assert not is_valid_deep_draft_cache({})
    assert not is_valid_deep_draft_cache(None)
    assert is_valid_deep_draft_cache(draft())


@pytest.mark.asyncio
async def test_generation_uses_same_task_background_lease_and_frontend_cache_keys():
    store, calls = {}, []
    limiter = AnalysisLimiter(2)
    owner = asyncio.current_task()
    request = report_request()

    async def resolve(payload):
        assert asyncio.current_task() is owner
        assert limiter._background == 1 and limiter._foreground == 0
        calls.append(("resolve", payload.max_pages))
        return ["source"]

    async def generate(payload, *, documents, background):
        assert asyncio.current_task() is owner
        assert documents == ["source"] and background is True
        calls.append(("generate", payload.max_pages))
        return draft()

    result = await background_deep_prewarm(
        request, get_cache=store.get, set_cache=store.__setitem__, limiter=limiter,
        resolve=resolve, generate=generate,
    )
    assert result == draft()
    assert calls == [("resolve", 18), ("generate", 18)]
    assert store[deep_draft_cache_key(request)] == result
    assert store[legacy_deep_draft_cache_key(request)] == result
    assert limiter._background == limiter._foreground == 0


@pytest.mark.asyncio
async def test_valid_cache_hit_ignores_display_metadata_and_needs_no_capacity():
    source = report_request()
    changed = report_request(title="其他显示标题", filename="other.pdf", symbol="NVDA", max_pages=60)
    store = {deep_draft_cache_key(source): draft()}

    class NoCapacity:
        def background(self):
            raise AssertionError("cached result must not need model capacity")

    assert await background_deep_prewarm(
        changed, get_cache=store.get, set_cache=store.__setitem__, limiter=NoCapacity(),
    ) == draft()


@pytest.mark.asyncio
async def test_v2_cache_migrates_without_model_and_optional_legacy_write_can_be_disabled():
    request = report_request()
    store = {legacy_deep_draft_cache_key(request): draft()}
    result = await background_deep_prewarm(
        request, get_cache=store.get, set_cache=store.__setitem__, limiter=AnalysisLimiter(1),
    )
    assert result == store[deep_draft_cache_key(request)]

    store.clear()

    async def generate(*args, **kwargs):
        return draft()

    await background_deep_prewarm(
        request, get_cache=store.get, set_cache=store.__setitem__, limiter=AnalysisLimiter(2),
        resolve=lambda _: [], generate=generate, write_legacy=False,
    )
    assert set(store) == {deep_draft_cache_key(request)}


@pytest.mark.asyncio
async def test_late_hit_during_background_admission_skips_resolution():
    request, store = report_request(), {}
    reads = 0

    async def get_cache(key):
        nonlocal reads
        if key == deep_draft_cache_key(request):
            reads += 1
            return draft() if reads == 2 else None
        return None

    async def should_not_resolve(*args):
        raise AssertionError("late cache hit must not resolve")

    result = await background_deep_prewarm(
        request, get_cache=get_cache, set_cache=store.__setitem__, limiter=AnalysisLimiter(2),
        resolve=should_not_resolve,
    )
    assert result == draft() and reads == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [{"provider": "local-fallback"}, {"provider": "text-only-fallback"}, {"vision_unavailable": True}, {"mode": "compact"}])
async def test_failed_generation_never_poisons_cache_and_is_retryable(bad):
    store, attempts = {}, 0

    async def generate(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        return {**draft(), **bad} if attempts == 1 else draft()

    params = dict(get_cache=store.get, set_cache=store.__setitem__, limiter=AnalysisLimiter(2), resolve=lambda _: [], generate=generate)
    assert await background_deep_prewarm(report_request(), **params) is None
    assert not store
    assert await background_deep_prewarm(report_request(), **params) == draft()
    assert attempts == 2


@pytest.mark.asyncio
async def test_busy_background_never_resolves_or_queues():
    limiter = AnalysisLimiter(2)
    store = {}

    async def should_not_resolve(*args):
        raise AssertionError("foreground work reserves the background slot")

    async with limiter:
        with pytest.raises(AnalysisBusy):
            await background_deep_prewarm(
                report_request(), get_cache=store.get, set_cache=store.__setitem__,
                limiter=limiter, resolve=should_not_resolve,
            )
        assert limiter._waiting == limiter._background == 0
    assert not store


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_timeout_and_external_cancel_await_cleanup_and_release_capacity(cancel):
    limiter, store = AnalysisLimiter(2), {}
    started, closed = asyncio.Event(), asyncio.Event()
    baseline = set(asyncio.all_tasks())

    async def generate(*args, **kwargs):
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            closed.set()

    task = asyncio.create_task(background_deep_prewarm(
        report_request(), get_cache=store.get, set_cache=store.__setitem__, limiter=limiter,
        resolve=lambda _: [], generate=generate, timeout_seconds=1.0 if cancel else 0.03,
    ))
    await started.wait()
    if cancel:
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else asyncio.TimeoutError):
        await task
    assert closed.is_set()
    assert limiter._background == limiter._foreground == limiter._waiting == 0
    assert not store
    assert not [task for task in asyncio.all_tasks() - baseline if not task.done()]


def test_projection_uses_real_content_without_padding_or_mutating_deep_result():
    result = draft()
    result["sections"].append({"id": "s2", "title": "空章节"})
    prior = copy.deepcopy(result)
    compact = deep_to_compact(result)
    assert result == prior
    assert compact["summary"] == "原文摘要"
    assert compact["logic_lines"] == [{"title": "需求", "evidence": "原文证据", "chain": "原文需求摘要", "impact": "", "watch": ""}]
    assert compact["bullish"] == ["原文要点"]
    assert compact["risks"] == ["需求风险：原文风险"]
    assert compact["instruments"] == ["600000.SH"]
    with pytest.raises(ValueError):
        deep_to_compact({**result, "provider": "local-fallback"})


@pytest.mark.asyncio
async def test_prewarmed_canonical_payload_is_a_zero_model_hit_for_original_post_and_sse(monkeypatch):
    from deepfocus_api import main, research_stream

    store = {}

    async def generate(*args, **kwargs):
        return draft()

    await background_deep_prewarm(
        report_request(), get_cache=store.get, set_cache=store.__setitem__,
        limiter=AnalysisLimiter(2), resolve=lambda _: [], generate=generate,
    )
    seen = []

    def quota(*args, **kwargs):
        seen.append(kwargs["cached"])

    async def should_not_generate(*args, **kwargs):
        raise AssertionError("prewarmed source must not fetch PDF or invoke model")

    monkeypatch.setattr(main, "metrics_get_ai_cache", store.get)
    monkeypatch.setattr(main, "metrics_set_ai_cache", store.__setitem__)
    monkeypatch.setattr(main, "_check_ai_quota", quota)
    monkeypatch.setattr(main, "metrics_incr", lambda *args: None)
    monkeypatch.setattr(main, "metrics_incr_ai_ref", lambda *args: None)
    monkeypatch.setattr(main, "resolve_source_documents", should_not_generate)
    monkeypatch.setattr(research_stream, "resolve_source_documents", should_not_generate)
    monkeypatch.setattr(main, "generate_deep_draft", should_not_generate)
    monkeypatch.setattr(research_stream, "generate_deep_draft", should_not_generate)
    changed = report_request(title="前台标题", symbol="NVDA", filename="display.pdf", max_pages=18)
    http = Request({"type": "http", "method": "POST", "path": "/api/research/deep-draft", "headers": [], "client": ("127.0.0.1", 1234), "query_string": b""})
    post = await main.api_research_deep_draft(changed, http, None)
    response = await research_stream.api_research_deep_draft_stream(changed, http, None)
    events = [json.loads(chunk.removeprefix("data: ").strip()) async for chunk in response.body_iterator]
    assert post.executive_summary == "原文摘要"
    assert events[-1]["type"] == "done"
    assert events[-1]["data"]["executive_summary"] == post.executive_summary
    assert seen == [True, True]
