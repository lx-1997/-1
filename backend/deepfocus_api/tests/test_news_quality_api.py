"""News quality gates at the public API and background prewarm boundaries."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from deepfocus_api import main
from deepfocus_api.article_original_text import ArticleOriginalText
from deepfocus_api.schemas import NewsAnalyzeRequest


TITLE = "路透社：中国因台湾问题对巴拉圭和伯利兹的部长实施制裁。"
BODY = (
    "中国自周一起对巴拉圭通信部长及伯利兹外长实施制裁，原因是这两个国家仍与台湾保持关系。"
    "相关措施包括禁止两人入境香港和澳门，并禁止中国机构和个人与其进行交易或合作。"
)
GOOD = {
    "one_liner": "两位部长被禁止入境香港和澳门，并被限制交易合作。",
    "summary": "制裁涉及巴拉圭通信部长和伯利兹外长，具体措施包括入境与交易合作限制。",
    "logic_lines": [
        {"title": "入境限制", "evidence": "禁止两人入境香港和澳门。"},
        {"title": "交易合作限制", "evidence": "禁止中国机构和个人与其交易或合作。"},
    ],
    "confidence": 0.8,
    "source_note": "基于提供的正文",
}
BAD = {"one_liner": TITLE, "summary": "具体措施和影响尚不明确。", "confidence": 0.0}


@pytest.fixture
def boundary(monkeypatch):
    state = SimpleNamespace(cached=None, generated=dict(GOOD), gates=[], writes=[], calls=[])

    class Core:
        async def run_adapter(self, request, runner, *, route):
            assert route == "news-vision"
            return SimpleNamespace(raw=await runner())

    async def analyze(title, content, *, url=None, refresh=False):
        state.calls.append((title, content, url, refresh))
        return dict(state.generated)

    monkeypatch.setattr(main, "core_agent", Core())
    monkeypatch.setattr(main, "analyze_news", analyze)
    monkeypatch.setattr(main, "metrics_get_ai_cache", lambda key: state.cached)
    monkeypatch.setattr(main, "metrics_set_ai_cache", lambda key, value: state.writes.append((key, dict(value))))
    monkeypatch.setattr(main, "_check_ai_quota", lambda user, kind, req, *, cached: state.gates.append(cached))
    monkeypatch.setattr(main, "metrics_incr", lambda *args: None)
    monkeypatch.setattr(main, "metrics_incr_news_heat", lambda *args: None)
    monkeypatch.setattr(main, "get_realtime_message", lambda key: None)
    return state


def request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/api/news/ai-analyze", "headers": [], "client": ("127.0.0.1", 1)})


@pytest.mark.asyncio
async def test_zero_confidence_cache_is_a_miss_and_new_facts_replace_it(boundary):
    boundary.cached = BAD
    response = await main.api_news_ai_analyze(NewsAnalyzeRequest(title=TITLE, content=BODY), request(), _user={"id": "member"})
    assert boundary.gates == [False]
    assert len(boundary.calls) == 1
    assert response.confidence == 0.8
    assert len(response.logic_lines) == 2
    assert "入境" in response.logic_lines[0].evidence
    assert "交易" in response.logic_lines[1].evidence
    assert boundary.writes[0][0].startswith("news:v5:")


@pytest.mark.asyncio
async def test_failed_generation_is_retryable_and_never_written(boundary):
    boundary.generated = BAD
    with pytest.raises(HTTPException) as caught:
        await main.api_news_ai_analyze(NewsAnalyzeRequest(title=TITLE, content=BODY), request(), _user={"id": "member"})
    assert caught.value.status_code == 502
    assert "重试" in caught.value.detail
    assert boundary.writes == []


@pytest.mark.asyncio
async def test_refresh_bypasses_outer_cache_and_reaches_inner_cache_flag(boundary):
    boundary.cached = GOOD
    await main.api_news_ai_analyze(NewsAnalyzeRequest(title=TITLE, content=BODY, refresh=True), request(), _user={"id": "member"})
    assert boundary.gates == [False]
    assert boundary.calls[0][3] is True


@pytest.mark.asyncio
async def test_valid_cache_still_uses_existing_quota_gate_without_a_model_call(boundary):
    boundary.cached = GOOD
    response = await main.api_news_ai_analyze(NewsAnalyzeRequest(title=TITLE, content=BODY), request(), _user=None)
    assert boundary.gates == [True]
    assert boundary.calls == []
    assert response.logic_lines[0].evidence == GOOD["logic_lines"][0]["evidence"]


@pytest.mark.asyncio
async def test_detailed_summary_needs_no_artificial_report_logic_lines(boundary):
    boundary.generated = {**GOOD, "logic_lines": []}
    response = await main.api_news_ai_analyze(NewsAnalyzeRequest(title=TITLE, content=BODY), request(), _user={"id": "member"})
    assert response.logic_lines == []
    assert "入境" in response.summary
    assert "信息边界" not in response.model_dump_json()


@pytest.mark.parametrize("summary", ["影响未知。", "信息边界", "信息不足。", "具体措施尚未明确。"])
@pytest.mark.asyncio
async def test_unknown_only_summary_never_passes_as_new_information(boundary, summary):
    boundary.generated = {"one_liner": TITLE, "summary": summary, "confidence": 0.8, "logic_lines": []}
    with pytest.raises(HTTPException) as caught:
        await main.api_news_ai_analyze(NewsAnalyzeRequest(title=TITLE, content=BODY), request(), _user={"id": "member"})
    assert caught.value.status_code == 502
    assert boundary.writes == []


@pytest.mark.asyncio
async def test_short_stored_body_survives_a_history_retry_with_no_client_body(boundary, monkeypatch):
    short_body = "措施包括禁止两名部长入境香港澳门，并限制中国机构及个人与其交易合作。"
    monkeypatch.setattr(main, "get_realtime_message", lambda key: SimpleNamespace(topic="文章"))

    async def original(_message):
        return ArticleOriginalText([short_body], "stored-text")

    monkeypatch.setattr(main, "extract_article_original_text", original)
    response = await main.api_news_ai_analyze(
        NewsAnalyzeRequest(title=TITLE, message_id="short-article", refresh=True), request(), _user={"id": "member"},
    )
    assert boundary.calls[0][1] == short_body
    assert response.source_note == "已读取已收录正文"


@pytest.mark.parametrize("parser,truncated,expected", [
    ("stored-fallback", True, "⚠ 原文提取未完成，仅使用已收录正文"),
    ("vision-ocr", True, "⚠ 原文仅提取部分内容，解读覆盖范围有限"),
    ("stored-text", False, "已读取已收录正文"),
    ("html", False, "已读取原文正文"),
])
@pytest.mark.asyncio
async def test_source_coverage_is_honest_and_clean_body_is_not_refetched(boundary, monkeypatch, parser, truncated, expected):
    message = SimpleNamespace(topic="文章")
    monkeypatch.setattr(main, "get_realtime_message", lambda key: message)

    async def original(_message):
        return ArticleOriginalText([BODY], parser, truncated)

    monkeypatch.setattr(main, "extract_article_original_text", original)
    response = await main.api_news_ai_analyze(
        NewsAnalyzeRequest(title=TITLE, content="只有导语", message_id="article", url="https://example.test/original.png"),
        request(), _user={"id": "member"},
    )
    assert boundary.calls[0][1] == BODY
    assert boundary.calls[0][2] is None
    assert response.source_note == expected
    assert "全文" not in response.source_note
    assert boundary.writes[0][1]["source_note"] == expected


@pytest.mark.asyncio
async def test_prewarm_uses_same_clean_source_version_and_coverage(boundary, monkeypatch):
    message = SimpleNamespace(title=TITLE, content=BODY, url="https://example.test/original.png")
    monkeypatch.setattr(main, "list_realtime_messages", lambda **kwargs: [message])

    async def original(_message):
        return ArticleOriginalText([BODY], "stored-fallback", True)

    async def sleep(delay):
        if delay >= 300:
            raise asyncio.CancelledError

    monkeypatch.setattr(main, "extract_article_original_text", original)
    monkeypatch.setattr(main.asyncio, "sleep", sleep)
    monkeypatch.setenv("DEEPFOCUS_NEWS_PREWARM", "1")
    monkeypatch.setenv("DEEPFOCUS_NEWS_PREWARM_CYCLE_SECONDS", "300")
    with pytest.raises(asyncio.CancelledError):
        await main.run_news_prewarm()
    assert len(boundary.calls) == 1
    assert boundary.calls[0][2] is None
    assert boundary.writes[0][0].startswith("news:v5:")
    assert boundary.writes[0][1]["source_note"].startswith("⚠ 原文提取未完成")
