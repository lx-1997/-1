from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from deepfocus_api import article_original_text as article
from deepfocus_api import data_store
from deepfocus_api.bounded_cache import ByteBoundedTTLCache
from deepfocus_api.schemas import RealtimeMessageRecord


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _cache(monkeypatch, *, max_bytes=32 * 1024 * 1024, max_item_bytes=2 * 1024 * 1024, max_entries=512, sizeof=None):
    clock = Clock()
    cache = ByteBoundedTTLCache(max_entries=max_entries, max_bytes=max_bytes,
                               max_item_bytes=max_item_bytes, ttl_seconds=article._CACHE_SECONDS,
                               clock=clock, sizeof=sizeof)
    monkeypatch.setattr(article, "_ARTICLE_TEXT_CACHE", cache)
    monkeypatch.setattr(article, "time", SimpleNamespace(monotonic=clock))
    return cache, clock


def _message():
    return RealtimeMessageRecord(id="cache-memory-article", title="原文完整性", content="已收录导语。", topic="文章", severity="info", url="https://files.example.invalid/article.png", created_at="2026-10-12T00:00:00Z")


def test_fallback_expires_at_15_minutes_without_extending_on_hits(monkeypatch):
    cache, clock = _cache(monkeypatch)
    normal = article.ArticleOriginalText(["完整正文。"], "html")
    fallback = article.ArticleOriginalText(["只是导语。"], "stored-fallback", truncated=True)
    article._put_cached("normal", normal)
    article._put_cached("fallback", fallback)
    initial_bytes = cache.stats()["bytes"]
    clock.now = article._FALLBACK_CACHE_SECONDS - 1
    assert article._get_cached("fallback") == fallback
    assert article._get_cached("normal") == normal
    clock.now = article._FALLBACK_CACHE_SECONDS
    assert article._get_cached("fallback") is None
    assert article._get_cached("normal") == normal
    assert cache.stats()["bytes"] < initial_bytes
    clock.now = article._CACHE_SECONDS
    assert article._get_cached("normal") is None
    assert cache.stats()["bytes"] == 0


def test_cached_paragraph_lists_cannot_be_mutated_by_a_reader(monkeypatch):
    cache, _ = _cache(monkeypatch)
    result = article.ArticleOriginalText(["第一段。", "第二段。"], "html")
    article._put_cached("article", result)
    counted_bytes = cache.stats()["bytes"]
    result.paragraphs.append("生产者后来修改的第三段。")
    first_reader = article._get_cached("article")
    first_reader.paragraphs.append("读者修改。")
    assert article._get_cached("article").paragraphs == ["第一段。", "第二段。"]
    assert cache.stats()["bytes"] == counted_bytes


def test_total_retained_body_budget_evicts_whole_articles(monkeypatch):
    cache, _ = _cache(monkeypatch, max_bytes=7000, max_item_bytes=4000, max_entries=512)
    last = None
    for index in range(8):
        last = article.ArticleOriginalText([str(index) + "a" * 2000], "html")
        article._put_cached(str(index), last)
    assert cache.stats()["bytes"] <= 7000
    assert cache.stats()["evictions"] > 0
    assert article._get_cached("7") == last
    assert len(article._get_cached("7").paragraphs[0]) == 2001


@pytest.mark.asyncio
async def test_overlarge_article_is_returned_in_full_and_stays_process_only(monkeypatch):
    cache, _ = _cache(monkeypatch)
    paragraphs = ["z" * (3 * 1024 * 1024), "完整结尾。"]
    async def extract(url):
        return article.ArticleOriginalText(paragraphs, "vision-ocr")
    monkeypatch.setattr(article, "_source_urls", lambda message: [message.url])
    monkeypatch.setattr(article, "_extract_remote_source", extract)
    monkeypatch.setattr(data_store, "record", lambda *args, **kwargs: pytest.fail("private article text must not be persisted to the public data store"))
    result = await article.extract_article_original_text(_message())
    assert result.paragraphs == paragraphs
    assert result.truncated is False
    assert cache.stats()["entries"] == 0 and cache.stats()["rejected"] == 1


@pytest.mark.asyncio
async def test_failed_cache_size_estimate_does_not_fail_successful_article(monkeypatch):
    def fail_size(value):
        raise ValueError("test estimator failure")
    cache, _ = _cache(monkeypatch, sizeof=fail_size)
    async def extract(url):
        return article.ArticleOriginalText(["完整正文及结尾。"], "html")
    monkeypatch.setattr(article, "_source_urls", lambda message: [message.url])
    monkeypatch.setattr(article, "_extract_remote_source", extract)
    result = await article.extract_article_original_text(_message())
    assert result.paragraphs == ["完整正文及结尾。"]
    assert cache.stats()["entries"] == 0 and cache.stats()["rejected"] == 1


@pytest.mark.asyncio
async def test_same_article_keeps_original_singleflight_and_cached_body(monkeypatch):
    cache, _ = _cache(monkeypatch)
    entered, finish = asyncio.Event(), asyncio.Event()
    calls = []
    async def extract(url):
        calls.append(url)
        entered.set()
        await finish.wait()
        return article.ArticleOriginalText(["提取一次的完整正文。"], "html")
    monkeypatch.setattr(article, "_source_urls", lambda message: [message.url])
    monkeypatch.setattr(article, "_extract_remote_source", extract)
    first = asyncio.create_task(article.extract_article_original_text(_message()))
    await entered.wait()
    second = asyncio.create_task(article.extract_article_original_text(_message()))
    await asyncio.sleep(0)
    finish.set()
    one, two = await asyncio.gather(first, second)
    third = await article.extract_article_original_text(_message())
    assert one == two == third
    assert calls == [_message().url] and cache.stats()["entries"] == 1
