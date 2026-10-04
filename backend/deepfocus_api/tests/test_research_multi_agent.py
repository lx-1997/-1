from __future__ import annotations

import asyncio
import json

import pytest

from deepfocus_api import research_multi_agent as ma


@pytest.fixture(autouse=True)
def _enable_multi_agent(monkeypatch):
    monkeypatch.setattr(ma, "_ENABLED", True)
    # Legacy role-wide tests stay deterministic; the dedicated page test below
    # turns the new rollout on explicitly.
    monkeypatch.setattr(ma, "_PAGE_SUMMARY_ENABLED", False)


def test_main_cache_key_is_versioned_only_when_enabled(monkeypatch):
    monkeypatch.setattr(ma, "_ENABLED", True)
    assert ma.analysis_cache_key("fid-1") == "research:agents-v4:fid-1"
    monkeypatch.setattr(ma, "_ENABLED", False)
    assert ma.analysis_cache_key("fid-1") == "fid-1"


def test_agent_prompts_only_extract_source_material():
    for role in ma._ROLES:
        prompt = ma._build_text_prompt(role, "某公司", "TEST", "正文")
        assert "原文未提及" in prompt
        assert "不得根据常识推断" in prompt
    assert "买方风控视角" not in ma._ROLES[-1].instruction


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
                "industry_context": "AI 算力景气度仍高。", "comparables": "对照台积电资本开支。",
                "bearish": ["不应采纳的越权字段"],
            },
            "thesis": {
                "one_liner": "偏多：数据中心需求强", "summary": "盈利预期上修。",
                "core_logic": "需求增长推动收入和利润。", "bullish": ["收入上修", "收入上修", "新品催化"],
                "premises": "数据中心资本开支延续。", "sensitivity": "毛利率对产品组合最敏感。",
                "instruments": ["NVDA", "台积电"], "confidence": 0.8,
            },
            "risk": {
                "bearish": ["需求不及预期", "估值收缩"],
                "contrary": "客户自研芯片可能分流需求。", "tracking": "跟踪订单和毛利率。",
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
    assert result["df_take"] == ""  # 六维「综合判断与边界」已下线
    assert result["confidence"] == pytest.approx(0.8)
    assert result["pages_analyzed"] == 4


def test_text_report_runs_three_agents_concurrently_and_merges(monkeypatch):
    active = 0
    max_active = 0

    class FakeLLM:
        provider = "minimax"
        model = "fake-model"

        async def complete_json(self, prompt, **kwargs):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.02)
            active -= 1
            if "事实与估值" in prompt:
                return {
                    "subject": "贵州茅台", "instruments": ["贵州茅台"], "market": "A股",
                    "rating": "买入", "target_price": "1800元", "industry_context": "白酒调整期。",
                    "comparables": "对照五粮液批价。", "confidence": 0.9,
                }
            if "核心逻辑 Agent" in prompt:
                return {
                    "one_liner": "偏多：盈利预期上修", "summary": "需求保持稳健。",
                    "core_logic": "量价驱动利润。", "bullish": ["利润上修"],
                    "premises": "批价稳定。", "sensitivity": "渠道库存变化。", "confidence": 0.8,
                }
            return {
                "bearish": ["需求不及预期"], "contrary": "终端动销可能弱于报表。",
                "tracking": "跟踪批价和库存。", "confidence": 0.7,
            }

    monkeypatch.setattr(ma, "CloudResearchLLM", FakeLLM)
    monkeypatch.setattr(ma.rv, "extract_pdf_text", lambda *args, **kwargs: "正文 " * 300)
    monkeypatch.setattr(ma, "_cache_get", lambda key: None)
    monkeypatch.setattr(ma, "_cache_put", lambda key, value: None)

    result = asyncio.run(ma.analyze_pdf_adaptive(b"pdf", title="贵州茅台", max_pages=14))
    assert max_active == 3
    assert result["agent_count"] == 3
    assert result["analysis_mode"] == "multi_agent_text"
    assert result["one_liner"] == "偏多：盈利预期上修"
    assert result["bearish"] == ["需求不及预期"]
    assert result["target_price"] == "1800元"


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
                    "rating": "买入", "target_price": "1800元", "industry_context": "白酒调整期。",
                    "comparables": "对照五粮液批价。", "confidence": 0.9,
                }, ensure_ascii=False)
            if "核心逻辑 Agent" in prompt:
                return json.dumps({
                    "one_liner": "偏多：盈利预期上修", "summary": "需求保持稳健。",
                    "core_logic": "量价驱动利润。", "bullish": ["利润上修"],
                    "premises": "批价稳定。", "sensitivity": "渠道库存变化。", "confidence": 0.8,
                }, ensure_ascii=False)
            return json.dumps({
                "bearish": ["需求不及预期"], "contrary": "终端动销可能弱于报表。",
                "tracking": "跟踪批价和库存。", "confidence": 0.7,
            }, ensure_ascii=False)

    def no_text(*args, **kwargs):
        raise ma.rv.PdfTextUnavailable("scan")

    monkeypatch.setattr(ma, "CloudResearchLLM", FakeLLM)
    monkeypatch.setattr(ma.rv, "extract_pdf_text", no_text)
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
    assert result["df_take"] == ""  # 六维「综合判断与边界」已下线


def test_image_report_can_read_pages_concurrently_then_synthesize(monkeypatch):
    active = 0
    max_active = 0
    synthesis_calls = 0

    class FakeLLM:
        provider = "minimax"
        model = "MiniMax-M3"

        async def complete_vision(self, prompt, images, **kwargs):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            try:
                await asyncio.sleep(0.01)
                page = int(prompt.split("页码：", 1)[1].split("。", 1)[0])
                return json.dumps({
                    "summary": f"第{page}页明确写出收入增长。",
                    "facts": [f"第{page}页收入增长"],
                    "bullish": ["需求延续"],
                    "bearish": [],
                    "confidence": 0.8,
                }, ensure_ascii=False)
            finally:
                active -= 1

        async def complete_json(self, prompt, **kwargs):
            nonlocal synthesis_calls
            synthesis_calls += 1
            return {
                "subject": "贵州茅台",
                "summary": "多页共同显示收入增长。",
                "one_liner": "收入增长，报告观点偏积极",
                "core_logic": "需求增长推动收入。",
                "bullish": ["需求延续"],
                "bearish": [],
                "confidence": 0.8,
            }

    def no_text(*args, **kwargs):
        raise ma.rv.PdfTextUnavailable("scan")

    monkeypatch.setattr(ma, "_PAGE_SUMMARY_ENABLED", True)
    monkeypatch.setattr(ma, "_PAGE_CONCURRENCY", 2)
    monkeypatch.setattr(ma, "_page_sem", None)
    monkeypatch.setattr(ma, "CloudResearchLLM", FakeLLM)
    monkeypatch.setattr(ma.rv, "extract_pdf_text", no_text)
    monkeypatch.setattr(ma.rv, "render_pdf_to_pngs", lambda *args, **kwargs: [b"1", b"2", b"3", b"4"])
    monkeypatch.setattr(ma, "_cache_get", lambda key: None)
    monkeypatch.setattr(ma, "_cache_put", lambda key, value: None)

    result = asyncio.run(ma.analyze_pdf_adaptive(b"scan-pages", title="贵州茅台", max_pages=4))
    assert max_active <= 2
    assert synthesis_calls == 1
    assert result["analysis_mode"] == "page_parallel_synthesis"
    assert result["pages_analyzed"] == 4
    assert result["summary"] == "多页共同显示收入增长。"


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

    def no_text(*args, **kwargs):
        raise ma.rv.PdfTextUnavailable("scan")

    monkeypatch.setattr(ma, "CloudResearchLLM", FakeLLM)
    monkeypatch.setattr(ma.rv, "extract_pdf_text", no_text)
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

    def no_text(*args, **kwargs):
        raise ma.rv.PdfTextUnavailable("scan")

    single_calls = 0

    async def single_fallback(*args, **kwargs):
        nonlocal single_calls
        single_calls += 1
        return {"summary": "single-agent recovered"}

    monkeypatch.setattr(ma, "CloudResearchLLM", FakeLLM)
    monkeypatch.setattr(ma.rv, "extract_pdf_text", no_text)
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
    def no_text(*args, **kwargs):
        raise ma.rv.PdfTextUnavailable("scan")

    expected = {"summary": "single"}
    renders = 0

    def unexpected_render(*args, **kwargs):
        nonlocal renders
        renders += 1
        return [b"image"]

    async def single(*args, **kwargs):
        return expected

    monkeypatch.setattr(ma.rv, "extract_pdf_text", no_text)
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

    def no_text(*args, **kwargs):
        raise ma.rv.PdfTextUnavailable("scan")

    async def scenario():
        monkeypatch.setattr(ma, "CloudResearchLLM", SlowLLM)
        monkeypatch.setattr(ma.rv, "extract_pdf_text", no_text)
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
