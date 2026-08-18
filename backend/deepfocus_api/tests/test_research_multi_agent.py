from __future__ import annotations

import asyncio
import json

import pytest

from deepfocus_api import research_multi_agent as ma


@pytest.fixture(autouse=True)
def _enable_multi_agent(monkeypatch):
    monkeypatch.setattr(ma, "_ENABLED", True)


def test_main_cache_key_is_versioned_only_when_enabled(monkeypatch):
    monkeypatch.setattr(ma, "_ENABLED", True)
    assert ma.analysis_cache_key("fid-1") == "research:agents-v2:fid-1"
    monkeypatch.setattr(ma, "_ENABLED", False)
    assert ma.analysis_cache_key("fid-1") == "fid-1"


def test_disabled_mode_delegates_to_established_single_agent(monkeypatch):
    expected = {"summary": "single-agent"}
    calls = 0

    async def single(*args, **kwargs):
        nonlocal calls
        calls += 1
        return expected

    monkeypatch.setattr(ma, "_ENABLED", False)
    monkeypatch.setattr(ma.rv, "analyze_pdf_auto", single)
    result = asyncio.run(ma.analyze_pdf_adaptive(b"pdf", title="report", max_pages=4))
    assert result is expected
    assert calls == 1


def test_merge_results_obeys_field_ownership_and_deduplicates():
    result = ma._merge_results(
        {
            "facts": {
                "subject": "英伟达 NVDA", "instruments": ["NVDA"], "market": "美股",
                "rating": "增持", "target_price": "US$180", "confidence": 0.9,
                "bearish": ["不应采纳的越权字段"],
            },
            "thesis": {
                "one_liner": "偏多：数据中心需求强", "summary": "盈利预期上修。",
                "core_logic": "需求增长推动收入和利润。", "bullish": ["收入上修", "收入上修", "新品催化"],
                "instruments": ["NVDA", "台积电"], "confidence": 0.8,
            },
            "risk": {
                "bearish": ["需求不及预期", "估值收缩"], "df_take": "跟踪订单和毛利率。",
                "instruments": ["NVDA"], "confidence": 0.7,
            },
        },
        provider="fake · 3 agents",
        pages=4,
    )
    assert result["subject"] == "英伟达 NVDA"
    assert result["bullish"] == ["收入上修", "新品催化"]
    assert result["bearish"] == ["需求不及预期", "估值收缩"]
    assert result["instruments"] == ["NVDA", "台积电"]
    assert result["confidence"] == pytest.approx(0.8)
    assert result["pages_analyzed"] == 4


def test_text_report_keeps_fast_single_agent_path(monkeypatch):
    expected = {"summary": "fast text"}
    calls = {"text": 0, "render": 0}

    async def fake_text(*args, **kwargs):
        calls["text"] += 1
        return expected

    def fake_render(*args, **kwargs):
        calls["render"] += 1
        return [b"image"] * 4

    monkeypatch.setattr(ma.rv, "analyze_pdf_text", fake_text)
    monkeypatch.setattr(ma.rv, "render_pdf_to_pngs", fake_render)
    result = asyncio.run(ma.analyze_pdf_adaptive(b"pdf", title="text"))
    assert result is expected
    assert calls == {"text": 1, "render": 0}


def test_image_report_runs_three_agents_concurrently_and_merges(monkeypatch):
    active = 0
    max_active = 0

    class FakeLLM:
        provider = "minimax"
        model = "fake-model"

        async def complete_vision(self, prompt, images, **kwargs):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.02)
            active -= 1
            if "事实与估值" in prompt:
                return json.dumps({
                    "subject": "贵州茅台", "instruments": ["贵州茅台"], "market": "A股",
                    "rating": "买入", "target_price": "1800元", "confidence": 0.9,
                }, ensure_ascii=False)
            if "核心逻辑 Agent" in prompt:
                return json.dumps({
                    "one_liner": "偏多：盈利预期上修", "summary": "需求保持稳健。",
                    "core_logic": "量价驱动利润。", "bullish": ["利润上修"], "confidence": 0.8,
                }, ensure_ascii=False)
            return json.dumps({
                "bearish": ["需求不及预期"], "df_take": "跟踪批价和库存。", "confidence": 0.7,
            }, ensure_ascii=False)

    async def no_text(*args, **kwargs):
        raise ma.rv.PdfTextUnavailable("scan")

    monkeypatch.setattr(ma, "CloudResearchLLM", FakeLLM)
    monkeypatch.setattr(ma.rv, "analyze_pdf_text", no_text)
    monkeypatch.setattr(ma, "_pdf_page_count", lambda pdf: 4)
    monkeypatch.setattr(ma.rv, "render_pdf_to_pngs", lambda *args, **kwargs: [b"p1", b"p2", b"p3", b"p4"])
    monkeypatch.setattr(ma, "_cache_get", lambda key: None)
    monkeypatch.setattr(ma, "_cache_put", lambda key, value: None)

    result = asyncio.run(ma.analyze_pdf_adaptive(b"scan-pdf", title="贵州茅台", max_pages=4))
    assert max_active == 3
    assert result["agent_count"] == 3
    assert result["analysis_mode"] == "multi_agent_vision"
    assert result["one_liner"] == "偏多：盈利预期上修"
    assert result["bearish"] == ["需求不及预期"]
    assert result["target_price"] == "1800元"


def test_partial_agent_failure_falls_back_without_caching_partial(monkeypatch):
    class FakeLLM:
        provider = "minimax"
        model = "fake-model"

        async def complete_vision(self, prompt, images, **kwargs):
            if "风险反证" in prompt:
                raise RuntimeError("risk unavailable")
            if "事实与估值" in prompt:
                return '{"subject":"腾讯", "instruments":["腾讯"], "market":"港股"}'
            return '{"one_liner":"中性偏多：核心业务稳健", "bullish":["游戏增长"]}'

    async def no_text(*args, **kwargs):
        raise ma.rv.PdfTextUnavailable("scan")

    monkeypatch.setattr(ma, "CloudResearchLLM", FakeLLM)
    monkeypatch.setattr(ma.rv, "analyze_pdf_text", no_text)
    monkeypatch.setattr(ma, "_pdf_page_count", lambda pdf: 4)
    monkeypatch.setattr(ma.rv, "render_pdf_to_pngs", lambda *args, **kwargs: [b"1", b"2", b"3", b"4"])
    monkeypatch.setattr(ma, "_cache_get", lambda key: None)
    cache_writes = []
    monkeypatch.setattr(ma, "_cache_put", lambda key, value: cache_writes.append((key, value)))
    expected = {"summary": "single fallback", "bullish": ["完整结论"], "bearish": ["完整风险"]}

    async def single_fallback(*args, **kwargs):
        return expected

    monkeypatch.setattr(ma.rv, "analyze_pdf_vision", single_fallback)

    result = asyncio.run(ma.analyze_pdf_adaptive(b"partial"))
    assert result["summary"] == "single fallback"
    assert result["analysis_mode"] == "single_agent_fallback"
    assert "风险反证 Agent" in result["multi_agent_warning"]
    assert cache_writes == []


def test_all_agents_failure_falls_back_to_single_agent(monkeypatch):
    class FakeLLM:
        provider = "minimax"
        model = "fake-model"

        async def complete_vision(self, prompt, images, **kwargs):
            raise RuntimeError("provider unavailable")

    async def no_text(*args, **kwargs):
        raise ma.rv.PdfTextUnavailable("scan")

    single_calls = 0

    async def single_fallback(*args, **kwargs):
        nonlocal single_calls
        single_calls += 1
        return {"summary": "single-agent recovered"}

    monkeypatch.setattr(ma, "CloudResearchLLM", FakeLLM)
    monkeypatch.setattr(ma.rv, "analyze_pdf_text", no_text)
    monkeypatch.setattr(ma.rv, "analyze_pdf_vision", single_fallback)
    monkeypatch.setattr(ma, "_pdf_page_count", lambda pdf: 4)
    monkeypatch.setattr(ma.rv, "render_pdf_to_pngs", lambda *args, **kwargs: [b"1", b"2", b"3", b"4"])
    monkeypatch.setattr(ma, "_cache_get", lambda key: None)
    monkeypatch.setattr(ma, "_cache_put", lambda key, value: None)

    result = asyncio.run(ma.analyze_pdf_adaptive(b"all-fail"))
    assert result["summary"] == "single-agent recovered"
    assert result["analysis_mode"] == "single_agent_fallback"
    assert single_calls == 1


def test_short_scan_uses_single_agent_without_pre_render(monkeypatch):
    async def no_text(*args, **kwargs):
        raise ma.rv.PdfTextUnavailable("scan")

    expected = {"summary": "single"}
    renders = 0

    def unexpected_render(*args, **kwargs):
        nonlocal renders
        renders += 1
        return [b"image"]

    async def single(*args, **kwargs):
        return expected

    monkeypatch.setattr(ma.rv, "analyze_pdf_text", no_text)
    monkeypatch.setattr(ma, "_pdf_page_count", lambda pdf: 2)
    monkeypatch.setattr(ma.rv, "render_pdf_to_pngs", unexpected_render)
    monkeypatch.setattr(ma.rv, "analyze_pdf_vision", single)
    monkeypatch.setattr(ma, "_cache_get", lambda key: None)

    result = asyncio.run(ma.analyze_pdf_adaptive(b"short"))
    assert result is expected
    assert renders == 0


def test_cancelling_outer_analysis_cancels_all_agent_tasks(monkeypatch):
    active = 0

    class SlowLLM:
        provider = "minimax"
        model = "fake-model"

        async def complete_vision(self, prompt, images, **kwargs):
            nonlocal active
            active += 1
            try:
                await asyncio.sleep(60)
            finally:
                active -= 1

    async def no_text(*args, **kwargs):
        raise ma.rv.PdfTextUnavailable("scan")

    async def scenario():
        monkeypatch.setattr(ma, "CloudResearchLLM", SlowLLM)
        monkeypatch.setattr(ma.rv, "analyze_pdf_text", no_text)
        monkeypatch.setattr(ma, "_pdf_page_count", lambda pdf: 4)
        monkeypatch.setattr(ma.rv, "render_pdf_to_pngs", lambda *args, **kwargs: [b"1", b"2", b"3", b"4"])
        monkeypatch.setattr(ma, "_cache_get", lambda key: None)
        task = asyncio.create_task(ma.analyze_pdf_adaptive(b"cancel"))
        for _ in range(100):
            if active == 3:
                break
            await asyncio.sleep(0.01)
        assert active == 3
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)
        assert active == 0

    asyncio.run(scenario())


def test_batch_rechecks_cache_after_waiting(monkeypatch):
    expected = {"summary": "filled while queued"}
    reads = 0

    def cache_get(key):
        nonlocal reads
        reads += 1
        return None if reads == 1 else expected

    monkeypatch.setattr(ma, "_cache_get", cache_get)
    monkeypatch.setattr(ma, "_pdf_page_count", lambda pdf: 4)
    monkeypatch.setattr(
        ma.rv, "render_pdf_to_pngs",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("后到请求不应重复渲染")),
    )
    result = asyncio.run(ma._analyze_images_parallel(
        b"late-cache", title=None, symbol=None, max_pages=4,
    ))
    assert result is expected
    assert reads == 2


def test_background_batch_yields_when_interactive_batch_is_busy(monkeypatch):
    monkeypatch.setattr(ma, "_cache_get", lambda key: None)
    monkeypatch.setattr(ma, "_pdf_page_count", lambda pdf: 4)

    async def scenario():
        async with ma._batch_semaphore():
            with pytest.raises(ma.ResearchMultiAgentBusy):
                await ma._analyze_images_parallel(
                    b"background", title=None, symbol=None, max_pages=4, background=True,
                )

    asyncio.run(scenario())
