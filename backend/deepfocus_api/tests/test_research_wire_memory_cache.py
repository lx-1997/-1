from __future__ import annotations

from types import SimpleNamespace
import asyncio
import inspect

import pytest
import pytest_asyncio

from deepfocus_api import research_wire as wire
from deepfocus_api.bounded_cache import ByteBoundedTTLCache


@pytest_asyncio.fixture(autouse=True)
async def isolated_refresh_tasks(monkeypatch):
    tasks = {}
    monkeypatch.setattr(wire, "_WIRE_REFRESH_TASKS", tasks)
    yield
    remaining = list(tasks.values())
    for task in remaining:
        if not task.done(): task.cancel()
    await asyncio.gather(*remaining, return_exceptions=True)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _cache(monkeypatch, *, max_entries=24, max_bytes=16 * 1024 * 1024, max_item_bytes=2 * 1024 * 1024, sizeof=None):
    clock = Clock()
    cache = ByteBoundedTTLCache(max_entries=max_entries, max_bytes=max_bytes,
                               max_item_bytes=max_item_bytes, ttl_seconds=3600,
                               clock=clock, sizeof=sizeof)
    monkeypatch.setattr(wire, "_WIRE_CACHE", cache)
    monkeypatch.setattr(wire, "_WIRE_TTL", 600)
    monkeypatch.setattr(wire, "_WIRE_STALE_TTL", 3600)
    monkeypatch.setattr(wire, "time", SimpleNamespace(monotonic=clock))
    monkeypatch.setattr(wire, "_auth_payload", lambda: {})
    # These tests isolate caching from persistent ontology/index writes.
    monkeypatch.setattr(wire, "_ANNOTATED_WIRE_IDS", {str(index) for index in range(600)})
    return cache, clock


def _rows(count=5, prefix="研报"):
    return {"items": [{"fileId": str(index), "name": f"{prefix}{index}.pdf", "createTime": "2026-10-01T08:00:00Z", "size": 100, "downloadCount": 1} for index in range(count)]}


def _client(monkeypatch, fetch):
    class Response:
        def __init__(self, data):
            self.data = data
        def raise_for_status(self):
            pass
        def json(self):
            return self.data
    class Client:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, url, **kwargs):
            value = fetch(kwargs["json"])
            return Response(await value if inspect.isawaitable(value) else value)
    monkeypatch.setattr(wire.httpx, "AsyncClient", Client)


@pytest.mark.asyncio
async def test_queries_cache_full_pool_and_return_unshared_slices(monkeypatch):
    cache, _ = _cache(monkeypatch)
    calls = []
    def fetch(payload):
        calls.append(payload)
        return _rows()
    _client(monkeypatch, fetch)
    first = await wire.fetch_research_wire_online(query="AI", limit=1)
    first["items"][0]["title"] = "调用方富化时修改标题"
    first["items"][0]["large_extra"] = "a" * 10_000
    counted_bytes = cache.stats()["bytes"]
    larger = await wire.fetch_research_wire_online(query=" AI ", limit=5)
    assert len(calls) == 1 and calls[0]["resultLimit"] == wire._WIRE_POOL
    assert len(first["items"]) == 1 and first["total"] == larger["total"] == 5
    assert len(larger["items"]) == 5 and larger["items"][0]["title"] == "研报0"
    assert "large_extra" not in larger["items"][0]
    assert cache.stats()["bytes"] == counted_bytes


@pytest.mark.asyncio
async def test_freshness_stale_grace_and_expiry_are_distinct(monkeypatch):
    cache, clock = _cache(monkeypatch)
    calls = []
    def fetch(payload):
        calls.append(payload)
        if len(calls) > 1:
            raise RuntimeError("source down")
        return _rows()
    _client(monkeypatch, fetch)
    live = await wire.fetch_research_wire_online()
    clock.now = 599
    assert (await wire.fetch_research_wire_online())["stale"] is False
    assert len(calls) == 1
    clock.now = 601
    stale = await wire.fetch_research_wire_online()
    assert stale["stale"] is True and stale["items"] == live["items"]
    await asyncio.gather(wire._WIRE_REFRESH_TASKS[""], return_exceptions=True)
    assert len(calls) == 2
    clock.now = 3599
    assert (await wire.fetch_research_wire_online())["stale"] is True
    await asyncio.gather(wire._WIRE_REFRESH_TASKS[""], return_exceptions=True)
    clock.now = 3600
    with pytest.raises(RuntimeError, match="source down"):
        await wire.fetch_research_wire_online()
    assert cache.stats()["bytes"] == 0 and cache.stats()["expired"] == 1


@pytest.mark.asyncio
async def test_forced_refresh_keeps_existing_stale_fallback_for_empty_source(monkeypatch):
    cache, clock = _cache(monkeypatch)
    calls = []
    def fetch(payload):
        calls.append(payload)
        return _rows() if len(calls) == 1 else {"items": []}
    _client(monkeypatch, fetch)
    live = await wire.fetch_research_wire_online()
    clock.now = 20
    fallback = await wire.fetch_research_wire_online(use_cache=False)
    assert fallback["stale"] is True and fallback["items"] == live["items"]
    assert len(calls) == 2
    # An empty refresh never overwrites or renews the last successful pool.
    assert cache[""][0] == 0
    assert (await wire.fetch_research_wire_online())["stale"] is False
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_successful_forced_refresh_replaces_pool_and_starts_ttl_at_completion(monkeypatch):
    cache, clock = _cache(monkeypatch)
    calls = []
    def fetch(payload):
        calls.append(payload)
        clock.now += 700  # Upstream/normalization time must not burn the new TTL.
        return _rows(prefix="旧" if len(calls) == 1 else "新")
    _client(monkeypatch, fetch)
    await wire.fetch_research_wire_online()
    assert cache[""][0] == 700
    clock.now = 701
    updated = await wire.fetch_research_wire_online(use_cache=False)
    assert updated["items"][0]["title"] == "新0" and cache[""][0] == 1401
    clock.now = 2000
    assert (await wire.fetch_research_wire_online())["items"][0]["title"] == "新0"
    assert len(calls) == 2
    clock.now = 5001
    assert cache.prune_expired() == 1


@pytest.mark.asyncio
async def test_rare_queries_do_not_evict_the_hot_latest_pool(monkeypatch):
    cache, _ = _cache(monkeypatch, max_entries=3)
    calls = []
    def fetch(payload):
        calls.append(payload.get("keyword", ""))
        return _rows()
    _client(monkeypatch, fetch)
    await wire.fetch_research_wire_online()
    for query in ("rare-a", "rare-b", "rare-c", "rare-d", "rare-e"):
        await wire.fetch_research_wire_online(query=query)
    assert "" in cache and len(cache) == 3
    before = len(calls)
    assert (await wire.fetch_research_wire_online())["total"] == 5
    assert len(calls) == before


@pytest.mark.asyncio
async def test_oversized_pool_is_uncached_but_rows_and_titles_are_complete(monkeypatch):
    cache, _ = _cache(monkeypatch, max_item_bytes=2_000)
    expected = _rows(count=20, prefix="完整报告" + "题" * 100)
    _client(monkeypatch, lambda payload: expected)
    result = await wire.fetch_research_wire_online(limit=20)
    assert len(result["items"]) == result["total"] == 20
    assert result["items"][-1]["title"].endswith("19")
    assert len(result["items"][-1]["title"]) > 100
    assert cache.stats()["entries"] == 0 and cache.stats()["rejected"] == 1


@pytest.mark.asyncio
async def test_failed_size_estimate_does_not_fail_successful_wire_result(monkeypatch):
    def fail_size(value):
        raise ValueError("test estimator failure")
    cache, _ = _cache(monkeypatch, sizeof=fail_size)
    _client(monkeypatch, lambda payload: _rows())
    result = await wire.fetch_research_wire_online(limit=5)
    assert len(result["items"]) == result["total"] == 5
    assert cache.stats()["entries"] == 0 and cache.stats()["rejected"] == 1


@pytest.mark.asyncio
async def test_old_unrelated_queries_are_physically_pruned_when_fetch_starts(monkeypatch):
    cache, clock = _cache(monkeypatch)
    _client(monkeypatch, lambda payload: _rows())
    await wire.fetch_research_wire_online(query="old")
    clock.now = 3601
    await wire.fetch_research_wire_online(query="new")
    assert "old" not in cache and "new" in cache
    assert cache.stats()["expired"] == 1


@pytest.mark.asyncio
async def test_stock_search_is_one_page_and_cannot_contaminate_full_pool_cache(monkeypatch):
    cache, _ = _cache(monkeypatch)
    calls = []
    def fetch(payload):
        calls.append(payload)
        return _rows(count=25, prefix="个股" if payload["searchPages"] == 1 else "全池")
    _client(monkeypatch, fetch)
    stock = await wire.fetch_research_wire_online(query="测试公司", limit=60, stock_scope=True)
    feed = await wire.fetch_research_wire_online(query="测试公司", limit=25)
    assert len(stock["items"]) == 20 and feed["items"][0]["title"] == "全池0"
    assert calls[0]["resultLimit"] == 20 and calls[0]["searchPages"] == 1
    assert calls[1]["resultLimit"] == wire._WIRE_POOL and calls[1]["searchPages"] == 0
    assert ("stock", "测试公司") in cache and "测试公司" in cache
    assert (await wire.fetch_research_wire_online(query="测试公司", limit=3, stock_scope=True))["items"][0]["title"] == "个股0"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_stock_failure_does_not_borrow_unbounded_query_cache(monkeypatch):
    _cache(monkeypatch)
    def fetch(payload):
        if payload["searchPages"] == 1:
            raise RuntimeError("stock query unavailable")
        return _rows()
    _client(monkeypatch, fetch)
    await wire.fetch_research_wire_online(query="测试公司")
    with pytest.raises(RuntimeError, match="stock query unavailable"):
        await wire.fetch_research_wire_online(query="测试公司", stock_scope=True)
    # A normal keyword containing the namespace string is still independent.
    await wire.fetch_research_wire_online(query="stock:测试公司")
    with pytest.raises(RuntimeError, match="stock query unavailable"):
        await wire.fetch_research_wire_online(query="测试公司", stock_scope=True)


@pytest.mark.asyncio
async def test_forced_failure_preserves_unexpired_success_without_renewal(monkeypatch):
    cache, clock = _cache(monkeypatch); calls = []
    def fetch(payload):
        calls.append(payload)
        if len(calls)>1: raise RuntimeError("source down")
        return _rows()
    _client(monkeypatch, fetch)
    live = await wire.fetch_research_wire_online(); clock.now = 20
    result = await wire.fetch_research_wire_online(use_cache=False)
    assert result['stale'] and result['items']==live['items']
    assert cache[''][0]==0 and len(calls)==2


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_unregister_or_duplicate_shared_refresh(monkeypatch):
    _cache(monkeypatch); entered, release = asyncio.Event(), asyncio.Event(); calls = []
    async def fetch(payload):
        calls.append(payload); entered.set(); await release.wait(); return _rows()
    _client(monkeypatch, fetch)
    first = asyncio.create_task(wire.fetch_research_wire_online()); second = shared = None
    try:
        await asyncio.wait_for(entered.wait(), 1); shared = wire._WIRE_REFRESH_TASKS['']
        first.cancel(); await asyncio.gather(first, return_exceptions=True)
        assert wire._WIRE_REFRESH_TASKS.get('') is shared and not shared.cancelled()
        second = asyncio.create_task(wire.fetch_research_wire_online())
        await asyncio.sleep(0)
        assert wire._WIRE_REFRESH_TASKS[''] is shared and len(calls)==1
        release.set(); result = await asyncio.wait_for(second, 1)
        assert len(result['items'])==5 and len(calls)==1
        await asyncio.sleep(0)
        assert not wire._WIRE_REFRESH_TASKS
    finally:
        release.set()
        await asyncio.gather(*[t for t in (first, second, shared) if t is not None], return_exceptions=True)


@pytest.mark.asyncio
async def test_fallback_cannot_outlive_absolute_expiry_during_refresh(monkeypatch):
    cache, clock = _cache(monkeypatch); entered, release = asyncio.Event(), asyncio.Event(); calls = []
    async def fetch(payload):
        calls.append(payload)
        if len(calls)==1: return _rows()
        entered.set(); await release.wait(); raise RuntimeError('late source down')
    _client(monkeypatch, fetch)
    await wire.fetch_research_wire_online(); clock.now=20
    task = asyncio.create_task(wire.fetch_research_wire_online(use_cache=False))
    try:
        await asyncio.wait_for(entered.wait(), 1); clock.now=3600; release.set()
        with pytest.raises(RuntimeError, match='late source down'): await asyncio.wait_for(task, 1)
        assert cache.stats()['entries']==0
    finally:
        release.set(); await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_stale_returns_before_refresh_finishes_and_coalesces_requests(monkeypatch):
    cache, clock = _cache(monkeypatch); entered, release = asyncio.Event(), asyncio.Event(); calls = []
    async def fetch(payload):
        calls.append(payload)
        if len(calls)==1: return _rows(prefix='old')
        entered.set(); await release.wait(); return _rows(prefix='new')
    _client(monkeypatch, fetch)
    await wire.fetch_research_wire_online(); clock.now=601
    try:
        stale = await asyncio.wait_for(wire.fetch_research_wire_online(), 1)
        assert stale['stale'] and stale['items'][0]['title']=='old0' and not release.is_set()
        await asyncio.wait_for(entered.wait(), 1); task=wire._WIRE_REFRESH_TASKS['']
        assert (await wire.fetch_research_wire_online())['stale'] and len(calls)==2
        release.set(); await asyncio.wait_for(task, 1)
        result=await wire.fetch_research_wire_online()
        assert not result['stale'] and result['items'][0]['title']=='new0' and cache[''][0]==601
    finally:
        release.set()
        await asyncio.gather(*list(wire._WIRE_REFRESH_TASKS.values()), return_exceptions=True)


@pytest.mark.asyncio
async def test_small_stock_slice_cannot_truncate_later_same_page_requests(monkeypatch):
    _cache(monkeypatch); calls = []
    def fetch(payload):
        calls.append(payload); return _rows(count=payload['resultLimit'])
    _client(monkeypatch, fetch)
    first=await wire.fetch_research_wire_online(query='company', limit=1, stock_scope=True)
    later=await wire.fetch_research_wire_online(query='company', limit=20, stock_scope=True)
    assert len(first['items'])==1 and first['total']==20
    assert len(later['items'])==20 and len(calls)==1
    assert calls[0]['resultLimit']==20 and calls[0]['searchPages']==1
