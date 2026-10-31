import asyncio
import json

import pytest
from starlette.requests import Request

from deepfocus_api import cost_quotas as quotas, main, research_stream
from deepfocus_api.analysis_limiter import AnalysisBusy, AnalysisLimiter
from deepfocus_api.schemas import ResearchDeepDraftRequest, ResearchDeepDraftResponse


QUICK = {"one_liner": "Useful preview", "quick": True, "provider": "test-quick"}
DEEP = ResearchDeepDraftResponse(title="Deep result", provider="test-deep", sections=[{"id": "s1", "title": "Source facts", "paragraphs": ["Full model-generated report with readable evidence."]}])


@pytest.fixture(autouse=True)
def isolated_dependencies(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPFOCUS_QUOTA_DB_PATH", str(tmp_path / "quotas.sqlite3"))
    monkeypatch.setattr(main, "deep_draft_cache_key", lambda request: "quick-test")
    monkeypatch.setattr(main, "legacy_deep_draft_cache_key", lambda request: "")
    monkeypatch.setattr(main, "metrics_get_ai_cache", lambda key: None)
    for name in ("metrics_set_ai_cache", "metrics_incr", "metrics_incr_ai_ref"):
        monkeypatch.setattr(main, name, lambda *args: None)
    monkeypatch.setattr(main, "_AI_ANALYZE_SEM", AnalysisLimiter(2))
    monkeypatch.setattr(main, "_check_ai_quota", lambda *args, **kwargs: quotas.reserve("quick-test", 1))
    monkeypatch.setattr(main, "_complete_cost_quota", quotas.complete)
    monkeypatch.setattr(research_stream, "_STREAM_HEARTBEAT_SECONDS", 0.01)

    async def resolve(request):
        return []

    async def quick(request, documents, **kwargs):
        return None

    async def deep(request, documents, progress):
        return DEEP

    monkeypatch.setattr(research_stream, "resolve_source_documents", resolve)
    monkeypatch.setattr(research_stream, "generate_deep_quick", quick)
    monkeypatch.setattr(research_stream, "generate_deep_draft", deep)


async def _response():
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "query_string": b""})
    return await research_stream.api_research_deep_draft_stream(ResearchDeepDraftRequest(), request, None)


def _event(chunk):
    return json.loads(chunk.removeprefix("data: ").strip())


async def _collect(response):
    try:
        return [_event(chunk) async for chunk in response.body_iterator]
    finally:
        await response.body_iterator.aclose()


def _no_owned_tasks(prior):
    assert not [task for task in asyncio.all_tasks() - prior if not task.done()]


@pytest.mark.asyncio
async def test_deep_starts_before_slow_quick_finishes_and_cancels_loser(monkeypatch):
    quick_started, deep_started, quick_closed = (asyncio.Event() for _ in range(3))

    async def quick(*args, **kwargs):
        try:
            quick_started.set()
            await asyncio.Event().wait()
        finally:
            quick_closed.set()

    async def deep(*args, **kwargs):
        await quick_started.wait()
        deep_started.set()
        return DEEP

    monkeypatch.setattr(research_stream, "generate_deep_quick", quick)
    monkeypatch.setattr(research_stream, "generate_deep_draft", deep)
    prior = set(asyncio.all_tasks())
    response = await _response()
    events = await asyncio.wait_for(_collect(response), 1)
    assert deep_started.is_set() and quick_closed.is_set()
    assert events[-1]["type"] == "done"
    assert not any(event["type"] == "quick" for event in events)
    assert quotas.used("quick-test") == 1
    assert main._AI_ANALYZE_SEM._foreground == main._AI_ANALYZE_SEM._background == 0
    _no_owned_tasks(prior)


@pytest.mark.asyncio
@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("persist_failure", [False, True])
async def test_early_preview_is_cached_and_quota_completes_once_in_iterator_scope(monkeypatch, cached, persist_failure):
    finish = asyncio.Event()
    writes, completions = [], []
    owner = asyncio.current_task()
    if cached:
        monkeypatch.setattr(main, "metrics_get_ai_cache", lambda key: QUICK if key == "quick:quick-test" else None)

    async def quick(*args, **kwargs):
        return QUICK

    async def deep(*args, **kwargs):
        await finish.wait()
        return DEEP

    def complete(lease):
        assert asyncio.current_task() is owner
        assert quotas._request_leases.get() is response._quota_scope.leases
        assert response._quota_scope.leases == [lease]
        completions.append(lease.lease_id)
        return quotas.complete(lease)

    def persist(key, value):
        writes.append((key, value))
        if persist_failure and key.startswith("quick:"):
            raise RuntimeError("Optional preview persistence failed")

    monkeypatch.setattr(main, "metrics_set_ai_cache", persist)
    monkeypatch.setattr(main, "_complete_cost_quota", complete)
    monkeypatch.setattr(research_stream, "generate_deep_quick", quick)
    monkeypatch.setattr(research_stream, "generate_deep_draft", deep)
    prior = set(asyncio.all_tasks())
    response = await _response()
    try:
        events = []
        async for chunk in response.body_iterator:
            events.append(_event(chunk))
            if events[-1]["type"] == "quick":
                finish.set()
        assert [event["type"] for event in events if event["type"] in {"quick", "done"}] == ["quick", "done"]
    finally:
        await response.body_iterator.aclose()
    assert len(completions) == 1
    assert quotas.used("quick-test") == 1
    assert any(key == "quick:quick-test" for key, _ in writes) is (not cached)
    assert any(key == "quick-test" for key, _ in writes)
    _no_owned_tasks(prior)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [AnalysisBusy("preview busy"), RuntimeError("preview failed")])
async def test_quick_failure_does_not_fail_deep(monkeypatch, error):
    attempted = asyncio.Event()

    async def quick(*args, **kwargs):
        attempted.set()
        raise error

    async def deep(*args, **kwargs):
        await attempted.wait()
        return DEEP

    monkeypatch.setattr(research_stream, "generate_deep_quick", quick)
    monkeypatch.setattr(research_stream, "generate_deep_draft", deep)
    response = await _response()
    events = await asyncio.wait_for(_collect(response), 1)
    assert events[-1]["type"] == "done"
    assert not any(event["type"] in {"error", "quick"} for event in events)
    assert quotas.used("quick-test") == 1


@pytest.mark.asyncio
async def test_quick_cache_failure_does_not_fail_deep(monkeypatch):
    def lookup(key):
        if key.startswith("quick:"):
            raise RuntimeError("preview cache failed")
        return None

    monkeypatch.setattr(main, "metrics_get_ai_cache", lookup)
    events = await _collect(await _response())
    assert events[-1]["type"] == "done"
    assert quotas.used("quick-test") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("cached", [False, True])
async def test_optional_quick_cannot_occupy_foreground_queue(monkeypatch, cached):
    limiter = main._AI_ANALYZE_SEM
    if cached:
        monkeypatch.setattr(main, "metrics_get_ai_cache", lambda key: QUICK if key == "quick:quick-test" else None)

    async def forbidden(*args, **kwargs):
        raise AssertionError("No optional model work while another interactive request owns capacity")

    finish = asyncio.Event()

    async def deep(*args, **kwargs):
        if cached:
            await finish.wait()
        return DEEP

    monkeypatch.setattr(research_stream, "generate_deep_quick", forbidden)
    monkeypatch.setattr(research_stream, "generate_deep_draft", deep)
    async with limiter:
        response = await _response()
        try:
            events = []
            async for chunk in response.body_iterator:
                events.append(_event(chunk))
                if events[-1]["type"] == "quick":
                    finish.set()
        finally:
            await response.body_iterator.aclose()
        assert events[-1]["type"] == "done"
        assert any(event["type"] == "quick" for event in events) is cached
        assert limiter._background == limiter._waiting == 0
        assert limiter._foreground == 1


@pytest.mark.asyncio
async def test_disconnect_cancels_and_awaits_both_model_tasks_and_queue_wait(monkeypatch):
    started = [asyncio.Event(), asyncio.Event()]
    closed = [asyncio.Event(), asyncio.Event()]

    async def quick(*args, **kwargs):
        try:
            started[0].set()
            await asyncio.Event().wait()
        finally:
            closed[0].set()

    async def deep(*args, **kwargs):
        try:
            started[1].set()
            await asyncio.Event().wait()
        finally:
            closed[1].set()

    monkeypatch.setattr(research_stream, "generate_deep_quick", quick)
    monkeypatch.setattr(research_stream, "generate_deep_draft", deep)
    prior = set(asyncio.all_tasks())
    response = await _response()
    try:
        async for chunk in response.body_iterator:
            event = _event(chunk)
            if event["type"] == "tick" and all(e.is_set() for e in started):
                break
    finally:
        await response.body_iterator.aclose()
    assert all(e.is_set() for e in started + closed)
    assert quotas.used("quick-test") == 0
    assert response._quota_scope.heartbeat.done()
    assert main._AI_ANALYZE_SEM._foreground == main._AI_ANALYZE_SEM._background == 0
    _no_owned_tasks(prior)


@pytest.mark.asyncio
async def test_close_at_generate_frame_cleans_even_unstarted_tasks():
    prior = set(asyncio.all_tasks())
    response = await _response()
    try:
        async for chunk in response.body_iterator:
            if _event(chunk).get("stage") == "generate":
                break
    finally:
        await response.body_iterator.aclose()
    assert quotas.used("quick-test") == 0
    _no_owned_tasks(prior)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["disconnect", "provider", "timeout"])
async def test_failure_after_delivered_quick_keeps_exactly_one_charge(monkeypatch, failure):
    finish, closed = asyncio.Event(), asyncio.Event()
    monkeypatch.setattr(research_stream, "_STREAM_TOTAL_TIMEOUT_SECONDS", 0.08)
    completions = []

    def complete(lease):
        completions.append(lease.lease_id)
        return quotas.complete(lease)

    async def quick(*args, **kwargs):
        return QUICK

    async def deep(*args, **kwargs):
        try:
            await finish.wait()
            raise RuntimeError("deep provider failure")
        finally:
            closed.set()

    monkeypatch.setattr(main, "_complete_cost_quota", complete)
    monkeypatch.setattr(research_stream, "generate_deep_quick", quick)
    monkeypatch.setattr(research_stream, "generate_deep_draft", deep)
    prior = set(asyncio.all_tasks())
    response = await _response()
    try:
        events = []
        async for chunk in response.body_iterator:
            events.append(_event(chunk))
            if events[-1]["type"] == "quick":
                if failure == "disconnect":
                    break
                if failure == "provider":
                    finish.set()
    finally:
        await response.body_iterator.aclose()
    assert any(e["type"] == "quick" for e in events)
    assert closed.is_set()
    assert len(completions) == quotas.used("quick-test") == 1
    if failure != "disconnect":
        assert events[-1]["type"] == "error"
    if failure == "timeout":
        assert events[-1]["status"] == 504
    _no_owned_tasks(prior)


@pytest.mark.asyncio
async def test_total_deadline_covers_foreground_queue_and_does_not_start_models(monkeypatch):
    limiter = AnalysisLimiter(1, foreground_wait_seconds=1)
    monkeypatch.setattr(main, "_AI_ANALYZE_SEM", limiter)
    monkeypatch.setattr(research_stream, "_STREAM_TOTAL_TIMEOUT_SECONDS", 0.04)
    calls = []

    async def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError("Queued request must not start models after its deadline")

    monkeypatch.setattr(research_stream, "generate_deep_quick", forbidden)
    monkeypatch.setattr(research_stream, "generate_deep_draft", forbidden)
    prior = set(asyncio.all_tasks())
    async with limiter:
        events = await _collect(await _response())
    assert events[-1]["type"] == "error" and events[-1]["status"] == 504
    assert any(e["type"] == "tick" for e in events)
    assert calls == []
    assert quotas.used("quick-test") == 0
    _no_owned_tasks(prior)


@pytest.mark.asyncio
async def test_total_deadline_and_heartbeat_cover_source_resolution(monkeypatch):
    closed = asyncio.Event()
    monkeypatch.setattr(research_stream, "_STREAM_TOTAL_TIMEOUT_SECONDS", 0.04)

    async def resolve(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    monkeypatch.setattr(research_stream, "resolve_source_documents", resolve)
    prior = set(asyncio.all_tasks())
    events = await _collect(await _response())
    assert events[-1]["type"] == "error" and events[-1]["status"] == 504
    assert any(e["type"] == "tick" for e in events)
    assert closed.is_set()
    assert quotas.used("quick-test") == 0
    _no_owned_tasks(prior)


@pytest.mark.asyncio
async def test_full_draft_cache_skips_all_generation_and_completes_one_lease(monkeypatch):
    cached = DEEP.model_dump(mode="json")
    monkeypatch.setattr(main, "metrics_get_ai_cache", lambda key: cached if key == "quick-test" else None)
    calls = []

    async def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError("Cached response must not resolve or generate")

    monkeypatch.setattr(research_stream, "resolve_source_documents", forbidden)
    monkeypatch.setattr(research_stream, "generate_deep_quick", forbidden)
    monkeypatch.setattr(research_stream, "generate_deep_draft", forbidden)
    events = await _collect(await _response())
    assert events[-1]["type"] == "done"
    assert calls == []
    assert quotas.used("quick-test") == 1


@pytest.mark.asyncio
async def test_local_fallback_draft_is_not_cached(monkeypatch):
    writes = []

    async def fallback(*args, **kwargs):
        return ResearchDeepDraftResponse(title="原文摘录", provider="local-fallback")

    monkeypatch.setattr(research_stream, "generate_deep_draft", fallback)
    monkeypatch.setattr(main, "metrics_set_ai_cache", lambda key, value: writes.append(key))
    events = await _collect(await _response())
    assert events[-1]["type"] == "done"
    assert "quick-test" not in writes


@pytest.mark.asyncio
async def test_cache_lookup_wait_emits_elapsed_heartbeats(monkeypatch):
    import threading
    entered, release = threading.Event(), threading.Event()
    first = True

    def lookup(key):
        nonlocal first
        if first:
            first = False
            entered.set()
            release.wait(1)
        return None

    monkeypatch.setattr(main, "metrics_get_ai_cache", lookup)
    response = await _response()
    try:
        events = []
        async for chunk in response.body_iterator:
            events.append(_event(chunk))
            if events[-1]["type"] == "tick" and entered.is_set():
                release.set()
        assert any(e["type"] == "tick" for e in events)
        assert events[-1]["type"] == "done"
    finally:
        release.set()
        await response.body_iterator.aclose()


@pytest.mark.asyncio
async def test_overflowing_model_delta_queue_does_not_fail_deep(monkeypatch):
    finish = asyncio.Event()
    writes = []
    monkeypatch.setattr(main, "metrics_set_ai_cache", lambda *args: writes.append(args))

    async def deep(request, documents, progress):
        progress("llm_delta", '{"sections":[{"paragraphs":["')
        for _ in range(1000):
            progress("llm_delta", "x")
        progress("llm_delta", "latest fragment")
        progress("llm_delta", '"]}]}')
        await finish.wait()
        return DEEP

    monkeypatch.setattr(research_stream, "generate_deep_draft", deep)
    prior = set(asyncio.all_tasks())
    response = await _response()
    events = []
    async def consume():
        try:
            async for chunk in response.body_iterator:
                events.append(_event(chunk))
                if events[-1]["type"] == "body":
                    assert not finish.is_set() and writes == []
                    text = events[-1]["data"]["sections"][0]["paragraphs"][0]
                    assert text == "x" * 1000 + "latest fragment"
                    finish.set()
        finally:
            await response.body_iterator.aclose()
    await asyncio.wait_for(consume(), 2)
    assert events[-1]["type"] == "done" and finish.is_set()
    assert not any(e["type"] == "thought" for e in events)
    assert quotas.used("quick-test") == 1
    _no_owned_tasks(prior)


@pytest.mark.asyncio
async def test_unstructured_overflow_is_not_exposed_as_prose_or_thought(monkeypatch):
    started, finish = asyncio.Event(), asyncio.Event()
    async def deep(request, documents, progress):
        started.set()
        for _ in range(1000): progress("llm_delta", "RAW_NON_JSON_FRAGMENT")
        await finish.wait()
        return DEEP
    monkeypatch.setattr(research_stream, "generate_deep_draft", deep)
    prior = set(asyncio.all_tasks()); response = await _response(); events = []
    async def consume():
        try:
            async for chunk in response.body_iterator:
                events.append(_event(chunk))
                if events[-1]["type"] == "tick" and started.is_set(): finish.set()
        finally:
            await response.body_iterator.aclose()
    await asyncio.wait_for(consume(), 2)
    assert finish.is_set() and events[-1]["type"] == "done"
    assert not any(e["type"] in {"thought", "body"} for e in events)
    assert "RAW_NON_JSON_FRAGMENT" not in json.dumps(events)
    assert quotas.used("quick-test") == 1
    _no_owned_tasks(prior)


@pytest.mark.asyncio
@pytest.mark.parametrize("quick_delivered", [False, True])
async def test_asgi_disconnect_shields_cleanup_and_releases_capacity(monkeypatch, quick_delivered):
    quick_started, deep_started, quick_closed, deep_closed, disconnect = (asyncio.Event() for _ in range(5))

    async def quick(*args, **kwargs):
        try:
            quick_started.set()
            if quick_delivered:
                return QUICK
            await asyncio.Event().wait()
        finally:
            quick_closed.set()

    async def deep(*args, **kwargs):
        try:
            deep_started.set()
            await asyncio.Event().wait()
        finally:
            deep_closed.set()

    monkeypatch.setattr(research_stream, "generate_deep_quick", quick)
    monkeypatch.setattr(research_stream, "generate_deep_draft", deep)
    prior = set(asyncio.all_tasks())
    response = await _response()

    async def receive():
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        body = message.get("body")
        if body:
            event = _event(body.decode() if isinstance(body, bytes) else body)
            if event["type"] == ("quick" if quick_delivered else "tick") and quick_started.is_set() and deep_started.is_set():
                disconnect.set()

    await asyncio.wait_for(response({"type": "http", "asgi": {"version": "3.0"}}, receive, send), 1)
    assert quick_closed.is_set() and deep_closed.is_set()
    assert quotas.used("quick-test") == int(quick_delivered)
    assert main._AI_ANALYZE_SEM._foreground == main._AI_ANALYZE_SEM._background == 0
    assert response._quota_scope.closed and response._quota_scope.heartbeat.done()
    _no_owned_tasks(prior)


@pytest.mark.asyncio
@pytest.mark.parametrize("has_partial_text", [False, True])
async def test_parallel_stream_leaves_real_exclusive_vision_batch_to_deep(monkeypatch, has_partial_text):
    from deepfocus_api import research_digest, research_multi_agent, research_quick

    preview_ready = asyncio.Event()
    batch_calls, writes = [], []
    doc = research_digest.SourceDocument(
        "scan-source", "Scanned report", pdf_bytes=b"stubbed-pdf-extraction", total_pages=3,
        page_texts=[(1, "Short extracted text, insufficient for a full deep text draft.")] if has_partial_text else [],
    )

    class LLM:
        provider, model = "test", "test-model"

        async def complete_json(self, *args, **kwargs):
            raise RuntimeError("Preview text provider failed")

    # Keep the actual public adaptive deadline and exclusive semaphore lease;
    # replace only extraction/model work. Before the fix, a one-page preview
    # acquires this real batch and the deep report's bounded lease times out.
    async def adaptive_impl(pdf_bytes, *, max_pages, **kwargs):
        async with research_multi_agent._batch_lease():
            batch_calls.append(max_pages)
            if max_pages == 1:
                preview_ready.set()
                await asyncio.Event().wait()
            return {
                "one_liner": "Full report visual conclusion", "summary": "Full visual evidence",
                "bullish": ["Verified visual fact"], "pages_analyzed": 3,
                "provider": "test-vision", "confidence": 0.5,
            }

    async def resolve(*args, **kwargs):
        return [doc]

    async def tracked_quick(*args, **kwargs):
        try:
            return await research_quick.generate_deep_quick(*args, **kwargs)
        finally:
            preview_ready.set()

    async def real_deep(*args, **kwargs):
        await preview_ready.wait()
        return await research_digest.generate_deep_draft(*args, **kwargs)

    monkeypatch.setattr(research_quick, "CloudResearchLLM", LLM)
    monkeypatch.setattr(research_multi_agent, "_QUEUE_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(research_multi_agent, "_analyze_pdf_adaptive_impl", adaptive_impl)
    monkeypatch.setattr(research_stream, "resolve_source_documents", resolve)
    monkeypatch.setattr(research_stream, "generate_deep_quick", tracked_quick)
    monkeypatch.setattr(research_stream, "generate_deep_draft", real_deep)
    monkeypatch.setattr(main, "metrics_set_ai_cache", lambda key, value: writes.append((key, value)))
    prior = set(asyncio.all_tasks())
    events = await asyncio.wait_for(_collect(await _response()), 1)
    assert events[-1]["type"] == "done"
    assert events[-1]["data"]["one_liner"] == "Full report visual conclusion"
    assert not any(e.get("stage") == "fallback" for e in events)
    assert len(batch_calls) == 1 and batch_calls[0] > 1
    assert not research_multi_agent._batch_semaphore().locked()
    assert research_multi_agent._batch_foreground_waiters == 0
    # Visual compact fallback is readable live, but is never a reusable deep article.
    assert not any(key == "quick-test" for key, value in writes)
    assert quotas.used("quick-test") == 1
    _no_owned_tasks(prior)


def test_draft_prefix_requires_closed_top_level_summary_and_preserves_escapes():
    # An incomplete string cannot become a preview, and nested keys do not count.
    assert research_stream._draft_prefix_preview('{"nested":{"one_liner":"wrong","plain_language_summary":"wrong"}}') == {}
    assert research_stream._draft_prefix_preview('{"one_liner":"20%","plain_language_summary":"unfinished') == {}
    source = json.dumps({"one_liner": 'Revenue "quoted" 20%', "plain_language_summary": "2026年收入增速20%，并非利润增速。"}, ensure_ascii=False)
    preview = research_stream._draft_prefix_preview(source[:-1])
    assert preview["one_liner"] == 'Revenue "quoted" 20%'
    assert preview["summary"] == "2026年收入增速20%，并非利润增速。"
    assert research_stream._draft_prefix_preview('```json\n' + source) == {}


@pytest.mark.asyncio
async def test_draft_prefix_delivers_before_full_result_without_caching_partial(monkeypatch):
    release_deep = asyncio.Event()
    optional_closed = asyncio.Event()
    writes = []
    monkeypatch.setattr(main, "metrics_set_ai_cache", lambda *args: writes.append(args))

    async def quick(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            optional_closed.set()

    async def deep(request, documents, progress):
        prefix = json.dumps({"subject": "Company", "one_liner": "Revenue grew 20%", "plain_language_summary": "Revenue growth is not earnings growth."})[:-1] + ',"sections":['
        # Exceed the display queue capacity; exact prefix assembly must survive.
        for char in prefix:
            progress("llm_delta", char)
        await release_deep.wait()
        return DEEP

    monkeypatch.setattr(research_stream, "generate_deep_quick", quick)
    monkeypatch.setattr(research_stream, "generate_deep_draft", deep)
    prior = set(asyncio.all_tasks())
    response = await _response()
    events = []
    async def consume():
        try:
            async for chunk in response.body_iterator:
                event = _event(chunk)
                events.append(event)
                if event["type"] == "quick":
                    assert not release_deep.is_set()
                    assert event["data"]["summary"] == "Revenue growth is not earnings growth."
                    assert "正文生成中" in event["data"]["source_note"]
                    assert writes == []
                    release_deep.set()
        finally:
            await response.body_iterator.aclose()
    await asyncio.wait_for(consume(), 2)
    assert events[-1]["type"] == "done"
    assert sum(event["type"] == "quick" for event in events) == 1
    assert optional_closed.is_set()
    assert quotas.used("quick-test") == 1
    _no_owned_tasks(prior)


@pytest.mark.asyncio
async def test_full_model_failure_keeps_preview_instead_of_replacing_it_with_excerpts(monkeypatch):
    async def deep(request, documents, progress):
        progress("llm_delta", '{"one_liner":"Useful conclusion","plain_language_summary":"Readable preliminary summary",')
        await asyncio.sleep(0.02)
        return ResearchDeepDraftResponse(title="Excerpts", provider="local-fallback")
    monkeypatch.setattr(research_stream, "generate_deep_draft", deep)
    events = await asyncio.wait_for(_collect(await _response()), 1)
    assert any(event["type"] == "quick" for event in events)
    assert events[-1]["type"] == "error"
    assert "已保留初步速览" in events[-1]["detail"]
    assert not any(event["type"] == "done" for event in events)
    assert quotas.used("quick-test") == 1
