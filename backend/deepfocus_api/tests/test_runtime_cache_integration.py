import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI
from starlette.requests import Request

from deepfocus_api import auth, main
from deepfocus_api.bounded_cache import ByteBoundedTTLCache


def request(path="/api/research/wire", headers=None):
    return Request({"type": "http", "method": "GET", "path": path,
                    "headers": [(k.encode(), v.encode()) for k, v in (headers or {}).items()],
                    "query_string": b"", "client": ("127.0.0.1", 1)})


@pytest.mark.asyncio
@pytest.mark.parametrize("item_budget, expected_fetches", [(64, 2), (4096, 1)])
async def test_image_cache_budget_preserves_full_download(monkeypatch, item_budget, expected_fetches):
    content = b"image-content" * 80
    branded = content + b"complete-brand-bar"
    calls = []
    client_type = httpx.AsyncClient

    def fetch(req):
        calls.append(req.url.path)
        return httpx.Response(200, content=content, headers={"content-type": "image/png"})

    monkeypatch.setattr(main.httpx, "AsyncClient", lambda **kwargs: client_type(
        transport=httpx.MockTransport(fetch), **kwargs))
    monkeypatch.setattr(main, "_brand_zsxq_image", lambda raw, ct: (branded, ct))
    cache = ByteBoundedTTLCache(max_entries=24, max_bytes=8192,
                               max_item_bytes=item_budget, ttl_seconds=86400)
    monkeypatch.setattr(main, "_ZSXQ_WM_CACHE", cache)
    for _ in range(2):
        response = await main.api_zsxq_image(request("/api/zsxq/image"),
                                            u="https://images.zsxq.com/test.png", dl=1)
        assert response.status_code == 200
        assert response.body == branded
        assert "attachment" in response.headers["content-disposition"]
        assert response.headers["cache-control"] == "public, max-age=86400"
    assert len(calls) == expected_fetches
    assert cache.stats()["bytes"] <= cache.max_bytes


def fake_wire(monkeypatch):
    calls = []

    async def online(**kwargs):
        calls.append(kwargs)
        return {"items": [{"id": "cache-fid", "file_id": "cache-fid", "title": "完整研报标题",
                           "date": "2026-10-12", "created_at": "2026-10-12T06:00:00Z",
                           "filename": "report.pdf", "org": "测试来源"}], "total": 1}

    monkeypatch.setattr(main, "fetch_research_wire_online", online)
    monkeypatch.setattr(main.research_archive, "upsert", lambda rows: None)
    monkeypatch.setattr(main.research_archive, "query", lambda **kwargs: [])
    monkeypatch.setattr(main, "metrics_get_ai_cache_many", lambda keys: {})
    monkeypatch.setattr(main, "_enrich_research_wire_item", lambda item: item)
    monkeypatch.setattr(main, "_trigger_pdf_prewarm", lambda rows: None)
    return calls


@pytest.mark.asyncio
async def test_wire_hot_cache_keeps_etag_and_304_contract(monkeypatch):
    calls = fake_wire(monkeypatch)
    cache = ByteBoundedTTLCache(max_entries=48, max_bytes=1024 * 1024,
                               max_item_bytes=1024 * 1024, ttl_seconds=45)
    monkeypatch.setattr(main, "_WIRE_RESP_CACHE", cache)
    first = await main.api_research_wire(request(), limit=60)
    data = json.loads(first.body)
    assert data["items"][0]["title"] == "完整研报标题"
    assert data["items"][0]["preview_url"].startswith("/api/research/wire-file?")
    second = await main.api_research_wire(request(headers={"if-none-match": "W/" + first.headers["etag"]}), limit=60)
    assert second.status_code == 304
    assert second.headers["etag"] == first.headers["etag"]
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("broken_estimator", [False, True])
async def test_uncacheable_wire_result_remains_complete_live_response(monkeypatch, broken_estimator):
    calls = fake_wire(monkeypatch)

    def broken(value):
        raise ValueError("test estimator failure")

    cache = ByteBoundedTTLCache(max_entries=48, max_bytes=64,
                               sizeof=broken if broken_estimator else None, ttl_seconds=45)
    monkeypatch.setattr(main, "_WIRE_RESP_CACHE", cache)
    for _ in range(2):
        response = await main.api_research_wire(request(), limit=60)
        assert response.status_code == 200
        result = json.loads(response.body)
        assert result["items"][0]["title"] == "完整研报标题"
        assert result["source"] == "海外投行研报 · 在线检索"
    assert len(calls) == 2
    assert cache.stats()["entries"] == 0
    assert cache.stats()["rejected"] == 2


@pytest.mark.asyncio
async def test_idle_cache_maintenance_physically_releases_expired_values(monkeypatch):
    now = [0.0]
    cache = ByteBoundedTTLCache(name="test_idle_cache", max_entries=8, max_bytes=4096,
                               ttl_seconds=5, clock=lambda: now[0])
    cache["idle-key"] = "retained content"
    ticks = []

    async def tick(seconds):
        ticks.append(seconds)
        if len(ticks) > 1:
            raise asyncio.CancelledError
        now[0] = 6

    monkeypatch.setattr(main.asyncio, "sleep", tick)
    with pytest.raises(asyncio.CancelledError):
        await main.run_memory_cache_maintenance()
    assert ticks == [60.0, 60.0]
    assert cache.stats()["entries"] == 0
    assert cache.stats()["bytes"] == 0
    assert cache.stats()["expired"] == 1


@pytest.mark.asyncio
async def test_runtime_cache_stats_are_admin_only_and_never_expose_content(monkeypatch):
    app = FastAPI()
    app.add_api_route("/api/admin/runtime-cache-stats", main.runtime_cache_stats)
    monkeypatch.setattr(auth, "session_is_current", lambda claims: True)
    monkeypatch.setattr(auth, "current_claims", lambda req: (
        {"role": req.headers["x-test-role"]} if req.headers.get("x-test-role") else None))
    cache = ByteBoundedTTLCache(name="test_private_stats", max_entries=8, max_bytes=4096)
    cache["https://private.example/secret-query"] = "private-member-original-text"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/admin/runtime-cache-stats")).status_code == 401
        assert (await client.get("/api/admin/runtime-cache-stats", headers={"x-test-role": "user"})).status_code == 403
        response = await client.get("/api/admin/runtime-cache-stats", headers={"x-test-role": "admin"})
        assert response.status_code == 200
        assert "secret-query" not in response.text
        assert "private-member-original-text" not in response.text
        stats = response.json()["caches"]["test_private_stats"]
        assert stats["entries"] == 1
        assert all(isinstance(value, int) for value in stats.values())
