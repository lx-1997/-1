from __future__ import annotations

import asyncio
from contextvars import ContextVar
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from deepfocus_api import main
from deepfocus_api.analysis_limiter import AnalysisBusy
from deepfocus_api.article_original_text import ArticleOriginalText, article_original_text_source_version
from deepfocus_api.news_analysis_cache import NewsAnalysisCoordinator
from deepfocus_api.schemas import NewsAnalyzeRequest, RealtimeMessageRecord


TITLE = "两位部长受到入境及交易限制"
BODY = "措施从周一开始生效，包括禁止两名部长入境香港澳门，并限制中国机构及个人与其交易合作。" * 4
GOOD = {"one_liner": "周一起禁止两位部长入境香港澳门，并限制交易合作。", "summary": BODY, "confidence": 0.8, "source_note": "基于提供的正文"}
BAD = {"one_liner": TITLE, "confidence": 0.0}


def http_request():
    return Request({"type": "http", "method": "POST", "path": "/api/news/ai-analyze", "headers": [], "client": ("127.0.0.1", 1)})


@pytest.fixture
def boundary(monkeypatch):
    message = RealtimeMessageRecord(id="article-1", title=TITLE, content="已收录导语", topic="文章", severity="info", url="https://example.test/source.png", created_at="2026-10-15T00:00:00Z")
    state = SimpleNamespace(cache={}, now=0.0, calls=[], extractions=0, gates=[], result=GOOD, source=message, deny=False, model_gate=None, model_started=None, both_gated=None)

    def get_cache(key, max_age_seconds=None):
        item = state.cache.get(key)
        if item is None or (max_age_seconds is not None and state.now - item[0] > max_age_seconds):
            return None
        return dict(item[1])

    def set_cache(key, value):
        state.cache[key] = (state.now, dict(value))

    def check(user, kind, req, *, cached):
        state.gates.append(cached)
        if state.both_gated is not None and len(state.gates) >= 2:
            state.both_gated.set()
        if state.deny:
            raise HTTPException(status_code=402, detail="会员功能")

    async def extract(message):
        state.extractions += 1
        return ArticleOriginalText([BODY], "vision-ocr")

    async def analyze(title, content, *, url=None, refresh=False):
        state.calls.append((title, content, url, refresh))
        if state.model_started is not None:
            state.model_started.set()
        if state.model_gate is not None:
            await state.model_gate.wait()
        return dict(state.result)

    class Core:
        async def run_adapter(self, request, runner, *, route):
            return SimpleNamespace(raw=await runner())

    class Limiter:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def background(self):
            return self

    monkeypatch.setattr(main, "_NEWS_AI_COORDINATOR", NewsAnalysisCoordinator())
    monkeypatch.setattr(main, "_AI_ANALYZE_SEM", Limiter())
    monkeypatch.setattr(main, "get_realtime_message", lambda key: state.source)
    monkeypatch.setattr(main, "metrics_get_ai_cache", get_cache)
    monkeypatch.setattr(main, "metrics_set_ai_cache", set_cache)
    monkeypatch.setattr(main, "metrics_incr", lambda *args: None)
    monkeypatch.setattr(main, "metrics_incr_news_heat", lambda *args: None)
    monkeypatch.setattr(main, "_check_ai_quota", check)
    monkeypatch.setattr(main, "extract_article_original_text", extract)
    monkeypatch.setattr(main, "analyze_news", analyze)
    monkeypatch.setattr(main, "core_agent", Core())
    return state


async def call(*, refresh=False):
    return await main.api_news_ai_analyze(NewsAnalyzeRequest(title=TITLE, content="导语", message_id="article-1", refresh=refresh), http_request(), _user={"sub": "member"})


@pytest.mark.asyncio
async def test_repeated_open_uses_short_alias_before_ocr_without_changing_quota_semantics(boundary):
    first = await call()
    alias = next(key for key in boundary.cache if key.startswith("news-source:v5:"))
    pointer_time, pointer = boundary.cache[alias]
    assert list(pointer) == ["canonical_key"]
    assert pointer["canonical_key"].startswith("news:v5:")
    boundary.now = 100.0
    second = await call()
    assert boundary.extractions == 1
    assert len(boundary.calls) == 1
    assert boundary.gates == [False, True]
    assert first.source_note == second.source_note == "已读取原文正文"
    assert boundary.cache[alias][0] == pointer_time  # Absolute, not sliding TTL.


@pytest.mark.asyncio
async def test_alias_expires_after_600_seconds_and_rechecks_original_source(boundary):
    await call()
    boundary.now = 601.0
    await call()
    assert boundary.extractions == 2
    assert len(boundary.calls) == 1  # Canonical result still has its own retention.


@pytest.mark.asyncio
async def test_source_change_and_explicit_refresh_do_not_skip_extraction(boundary):
    await call()
    boundary.source = boundary.source.model_copy(update={"url": "https://example.test/revised.png"})
    await call()
    await call(refresh=True)
    assert boundary.extractions == 3
    assert len(boundary.calls) == 2
    assert boundary.calls[-1][-1] is True


@pytest.mark.asyncio
async def test_alias_cannot_revive_bad_or_missing_canonical_result(boundary):
    await call()
    key = next(key for key in boundary.cache if key.startswith("news:v5:"))
    boundary.cache[key] = (0.0, BAD)
    await call()
    assert boundary.extractions == 2
    assert len(boundary.calls) == 2
    assert boundary.gates == [False, False]


@pytest.mark.asyncio
async def test_valid_alias_remains_behind_existing_permission_gate(boundary):
    await call()
    boundary.deny = True
    with pytest.raises(HTTPException) as caught:
        await call()
    assert caught.value.status_code == 402
    assert boundary.gates == [False, True]
    assert len(boundary.calls) == 1
    assert boundary.extractions == 1


@pytest.mark.asyncio
async def test_concurrent_clicks_generate_once_and_both_pass_individual_gate(boundary):
    boundary.model_gate = asyncio.Event()
    boundary.model_started = asyncio.Event()
    boundary.both_gated = asyncio.Event()
    first = asyncio.create_task(call())
    await boundary.model_started.wait()
    second = asyncio.create_task(call())
    await asyncio.wait_for(boundary.both_gated.wait(), timeout=3)
    boundary.model_gate.set()
    responses = await asyncio.gather(first, second)
    assert len(boundary.calls) == 1
    assert len(responses) == 2
    assert boundary.gates == [False, False]
    assert main._NEWS_AI_COORDINATOR.in_flight_count == 0


@pytest.mark.asyncio
async def test_prewarm_and_click_share_generation(boundary, monkeypatch):
    boundary.model_gate = asyncio.Event()
    boundary.model_started = asyncio.Event()
    key = "news:v5:shared-prewarm"
    background = asyncio.create_task(main._generate_news_ai_result(title=TITLE, content=BODY, model_url=None, cache_key=key, background=True, original_source_note="已读取原文正文"))
    await boundary.model_started.wait()
    foreground = asyncio.create_task(main._generate_news_ai_result(title=TITLE, content=BODY, model_url=None, cache_key=key, original_source_note="已读取原文正文"))
    await asyncio.sleep(0.01)
    boundary.model_gate.set()
    results = await asyncio.gather(background, foreground)
    assert len(boundary.calls) == 1
    assert all(result[0]["source_note"] == "已读取原文正文" for result in results)


@pytest.mark.asyncio
async def test_failed_generation_never_persists_result_or_alias(boundary):
    boundary.result = BAD
    with pytest.raises(HTTPException) as caught:
        await call()
    assert caught.value.status_code == 502
    assert boundary.cache == {}


@pytest.mark.asyncio
async def test_generation_and_cancellation_remain_inside_real_quota_scope(boundary, monkeypatch, tmp_path):
    from deepfocus_api import cost_quotas as quotas
    monkeypatch.setenv("DEEPFOCUS_QUOTA_DB_PATH", str(tmp_path / "quotas.sqlite3"))
    monkeypatch.setattr(main, "_check_ai_quota", lambda *args, **kwargs: quotas.reserve("news-member", 4))
    await call()
    assert quotas.used("news-member") == 1
    boundary.model_gate = asyncio.Event()
    boundary.model_started = asyncio.Event()
    task = asyncio.create_task(call(refresh=True))
    await boundary.model_started.wait()
    assert quotas.used("news-member") == 2
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert quotas.used("news-member") == 1
    assert main._NEWS_AI_COORDINATOR.in_flight_count == 0


@pytest.mark.asyncio
async def test_lock_wait_is_inside_budget_and_cleans_cancelled_waiter():
    coordinator = NewsAnalysisCoordinator()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def work():
        entered.set()
        await release.wait()
        return "ok"

    owner = asyncio.create_task(coordinator.run("key", work))
    await entered.wait()
    with pytest.raises(asyncio.TimeoutError):
        await coordinator.run("key", work, timeout_seconds=0.01)
    assert coordinator.in_flight_count == 1
    release.set()
    assert await owner == "ok"
    assert coordinator.in_flight_count == 0


@pytest.mark.asyncio
async def test_cancelled_owner_stops_model_and_follower_uses_own_context():
    coordinator = NewsAnalysisCoordinator()
    leases = ContextVar("leases")
    entered = asyncio.Event()
    calls = []
    cancelled = asyncio.Event()

    async def work():
        calls.append(leases.get())
        if len(calls) == 1:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        return "done"

    leases.set(["first request"])
    first = asyncio.create_task(coordinator.run("key", work))
    await entered.wait()
    leases.set(["second request"])
    second = asyncio.create_task(coordinator.run("key", work))
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert cancelled.is_set()
    assert await second == "done"
    assert calls == [["first request"], ["second request"]]
    assert coordinator.in_flight_count == 0


@pytest.mark.asyncio
async def test_refresh_is_independent_of_old_flight_and_capacity_is_bounded():
    coordinator = NewsAnalysisCoordinator(max_keys=1)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow():
        entered.set()
        await release.wait()
        return "old"

    async def fresh():
        return "fresh"

    owner = asyncio.create_task(coordinator.run("key", slow))
    await entered.wait()
    assert await coordinator.run("key", fresh, refresh=True) == "fresh"
    with pytest.raises(AnalysisBusy):
        await coordinator.run("another", fresh)
    release.set()
    assert await owner == "old"
    assert coordinator.in_flight_count == 0


def test_source_version_includes_recovered_screenshot(monkeypatch, boundary):
    from deepfocus_api import article_original_text as original
    media = ["https://example.test/capture-1.png"]
    monkeypatch.setattr(original, "_local_article_media_urls", lambda message: list(media))
    first = article_original_text_source_version(boundary.source)
    media[0] = "https://example.test/capture-2.png"
    second = article_original_text_source_version(boundary.source)
    assert first != second
