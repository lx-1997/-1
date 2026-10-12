from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from deepfocus_api import main
from deepfocus_api.analysis_limiter import AnalysisBusy
from deepfocus_api.async_singleflight import AsyncSingleFlight
from deepfocus_api.schemas import NewsAnalyzeRequest, ResearchVisionAnalyzeRequest


def request(path):
    return Request({"type": "http", "method": "POST", "path": path, "headers": [], "client": ("127.0.0.1", 1), "query_string": b""})


@pytest.mark.asyncio
async def test_visual_degradation_is_not_written_to_outer_or_alias_cache(monkeypatch):
    writes = []

    async def source(*args):
        return b"%PDF-test"

    async def analyze(*args, **kwargs):
        return {
            "one_liner": "文本抽取结果", "summary": "待恢复视觉能力", "provider": "text-only-fallback",
            "vision_unavailable": True, "confidence": 0,
            "logic_lines": [{"title": str(n), "evidence": "原文文本"} for n in range(4)],
        }

    class Core:
        async def run_adapter(self, req, runner, **kwargs):
            return SimpleNamespace(raw=await runner())

    monkeypatch.setattr(main, "_resolve_research_pdf_bytes", source)
    monkeypatch.setattr(main, "analyze_pdf_adaptive", analyze)
    monkeypatch.setattr(main, "core_agent", Core())
    monkeypatch.setattr(main, "_RESEARCH_AI_SINGLEFLIGHT", AsyncSingleFlight())
    monkeypatch.setattr(main, "metrics_get_ai_cache", lambda key: None)
    monkeypatch.setattr(main, "metrics_set_ai_cache", lambda *args: writes.append(args))
    result = await main._generate_research_ai_result(
        ResearchVisionAnalyzeRequest(file_id="test-degraded"), title="报告", cache_key="test-degraded",
    )
    assert result["vision_unavailable"]
    assert writes == []


@pytest.mark.asyncio
async def test_news_queue_busy_is_503_and_never_starts_model_or_cache(monkeypatch):
    calls = []

    class Busy:
        async def __aenter__(self):
            raise AnalysisBusy("解读队列已满")

        async def __aexit__(self, *args):
            pass

    async def forbidden(*args, **kwargs):
        calls.append("model")
        raise AssertionError("busy must be terminal")

    monkeypatch.setattr(main, "_AI_ANALYZE_SEM", Busy())
    monkeypatch.setattr(main, "analyze_news", forbidden)
    monkeypatch.setattr(main, "metrics_get_ai_cache", lambda key: None)
    monkeypatch.setattr(main, "metrics_set_ai_cache", lambda *args: calls.append("cache"))
    monkeypatch.setattr(main, "metrics_incr", lambda *args: None)
    monkeypatch.setattr(main, "metrics_incr_news_heat", lambda *args: None)
    monkeypatch.setattr(main, "_check_ai_quota", lambda *args, **kwargs: None)
    with pytest.raises(HTTPException) as caught:
        await main.api_news_ai_analyze(NewsAnalyzeRequest(title="测试文章", content="足够长的文章正文" * 30), request("/api/news/ai-analyze"), _user={"id": "test"})
    assert caught.value.status_code == 503
    assert calls == []
