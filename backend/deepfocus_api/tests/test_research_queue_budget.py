from __future__ import annotations

import asyncio
import subprocess
import time
from types import SimpleNamespace

import fitz
import pytest

from deepfocus_api import research_multi_agent as ma, research_vision as rv


@pytest.fixture(autouse=True)
def isolated_batches(monkeypatch):
    monkeypatch.setattr(ma, "_ENABLED", True)
    monkeypatch.setattr(ma, "_PAGE_SUMMARY_ENABLED", False)
    monkeypatch.setattr(ma, "_cache_get", lambda key: None)
    monkeypatch.setattr(ma, "_cache_put", lambda *args: None)
    monkeypatch.setattr(ma.rv, "extract_pdf_text", lambda *args, **kwargs: "正文事实 " * 200)


@pytest.mark.parametrize("whole_timeout", [False, True])
def test_waiting_batch_is_bounded_and_does_not_start_fallback(monkeypatch, whole_timeout):
    calls = []

    async def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError("Timed-out queue must not start a fresh fallback")

    monkeypatch.setattr(ma, "_QUEUE_TIMEOUT_SECONDS", 1.0 if whole_timeout else 0.02)
    monkeypatch.setattr(ma, "_ANALYSIS_TIMEOUT_SECONDS", 0.04 if whole_timeout else 1.0)
    monkeypatch.setattr(ma.rv, "analyze_pdf_text", forbidden)
    monkeypatch.setattr(ma.rv, "analyze_pdf_vision", forbidden)

    async def scenario():
        sem = ma._batch_semaphore()
        started = asyncio.get_running_loop().time()
        async with sem:
            error = ma.ResearchAnalysisTimeout if whole_timeout else ma.ResearchMultiAgentBusy
            with pytest.raises(error):
                await ma.analyze_pdf_adaptive(b"queued")
            assert asyncio.get_running_loop().time() - started < 0.5
            assert ma._batch_foreground_waiters == 0
        assert not sem.locked()
        assert calls == []

    asyncio.run(scenario())


def test_foreground_cancels_background_siblings_before_using_batch(monkeypatch):
    active_background = 0
    cancelled = 0
    instances = 0
    writes = []
    monkeypatch.setattr(ma, "_cache_put", lambda key, value: writes.append(value))

    class LLM:
        provider = "test"
        model = "test"

        def __init__(self):
            nonlocal instances
            instances += 1
            self.background = instances == 1

        async def complete_json(self, prompt, **kwargs):
            nonlocal active_background, cancelled
            if self.background:
                active_background += 1
                try:
                    await asyncio.Event().wait()
                finally:
                    active_background -= 1
                    cancelled += 1
            assert active_background == 0
            return {"summary": "原文显示收入增长", "one_liner": "收入增长", "confidence": 0.8}

    monkeypatch.setattr(ma, "CloudResearchLLM", LLM)

    async def scenario():
        prior = set(asyncio.all_tasks())
        background = asyncio.create_task(ma.analyze_pdf_adaptive(b"background", background=True))
        for _ in range(100):
            if active_background == 3:
                break
            await asyncio.sleep(0.005)
        assert active_background == 3
        result = await ma.analyze_pdf_adaptive(b"foreground")
        with pytest.raises(ma.ResearchMultiAgentBusy):
            await background
        assert cancelled == 3
        assert result["analysis_mode"] == "multi_agent_text"
        assert len(writes) == 1
        assert not ma._batch_semaphore().locked()
        assert not [task for task in asyncio.all_tasks() - prior if not task.done()]

    asyncio.run(scenario())


def test_overall_deadline_cancels_running_fallback(monkeypatch):
    closed = False
    started = False

    class FailedLLM:
        provider = "test"
        model = "test"

        async def complete_json(self, *args, **kwargs):
            raise RuntimeError("provider temporary failure")

    async def fallback(*args, **kwargs):
        nonlocal closed, started
        started = True
        try:
            await asyncio.Event().wait()
        finally:
            closed = True

    monkeypatch.setattr(ma, "CloudResearchLLM", FailedLLM)
    monkeypatch.setattr(ma, "_ANALYSIS_TIMEOUT_SECONDS", 0.04)
    monkeypatch.setattr(ma.rv, "analyze_pdf_text", fallback)

    async def scenario():
        with pytest.raises(ma.ResearchAnalysisTimeout):
            await ma.analyze_pdf_adaptive(b"fallback")
        assert started and closed
        assert not ma._batch_semaphore().locked()

    asyncio.run(scenario())


def test_concurrent_cache_misses_wait_for_write_and_reuse_one_batch(monkeypatch):
    cache = {}
    calls = 0

    class LLM:
        provider = "test"
        model = "test"

        async def complete_json(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.02)
            return {"summary": "原文收入增长", "one_liner": "收入增长", "confidence": 0.8}

    def delayed_write(key, value):
        # A write moved to a worker thread must remain part of the lease;
        # otherwise the follower can acquire and issue three duplicate calls.
        time.sleep(0.03)
        cache[key] = value

    monkeypatch.setattr(ma, "CloudResearchLLM", LLM)
    monkeypatch.setattr(ma, "_cache_get", cache.get)
    monkeypatch.setattr(ma, "_cache_put", delayed_write)

    async def scenario():
        first, second = await asyncio.gather(
            ma.analyze_pdf_adaptive(b"same-pdf"), ma.analyze_pdf_adaptive(b"same-pdf"),
        )
        assert calls == 3
        assert first == second

    asyncio.run(scenario())


def test_no_vision_provider_uses_text_extraction_without_rendering(monkeypatch):
    class TextOnlyLLM:
        provider = "glm"
        model = "glm-text"

        def has_vision_provider(self):
            return False

        async def complete_vision(self, *args, **kwargs):
            raise AssertionError("Text model must never receive images")

    monkeypatch.setattr(ma, "CloudResearchLLM", TextOnlyLLM)
    monkeypatch.setattr(rv, "CloudResearchLLM", TextOnlyLLM)
    monkeypatch.setattr(rv, "extract_pdf_text", lambda *args, **kwargs: "")
    monkeypatch.setattr(rv, "extract_pdf_ocr_text", lambda *args, **kwargs: "")
    monkeypatch.setattr(ma, "_pdf_page_count", lambda *args: 4)

    def render(*args, **kwargs):
        raise AssertionError("Unavailable vision route must not render pages")

    monkeypatch.setattr(rv, "render_pdf_to_pngs", render)
    result = asyncio.run(ma.analyze_pdf_adaptive(b"scan"))
    assert result["vision_unavailable"] is True
    assert result["provider"] == "text-only-fallback"
    assert result["confidence"] == 0.0
    assert "未进行图片解读" in result["summary"]


def test_ocr_keeps_successful_pages_after_a_page_timeout(monkeypatch):
    doc = fitz.open()
    doc.new_page()
    doc.new_page()
    pdf = doc.tobytes()
    doc.close()
    calls = 0

    def ocr(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise subprocess.TimeoutExpired("tesseract", 6)
        return SimpleNamespace(returncode=0, stdout=b"readable second page")

    monkeypatch.setattr(rv.shutil, "which", lambda command: "/test/tesseract")
    monkeypatch.setattr(rv.subprocess, "run", ocr)
    text = rv.extract_pdf_ocr_text(pdf)
    assert calls == 2
    assert "readable second page" in text


def test_missing_ocr_binary_skips_pdf_rendering(monkeypatch):
    monkeypatch.setattr(rv.shutil, "which", lambda command: None)
    assert rv.extract_pdf_ocr_text(b"not even a PDF") == ""


@pytest.mark.parametrize("marker", [
    {"vision_unavailable": True},
    {"provider": "text-only-fallback"},
    {"provider": "local-fallback"},
    {"analysis_mode": "text_extraction_fallback"},
])
def test_four_logic_lines_cannot_make_a_degraded_report_cacheable(marker):
    report = {
        "summary": "已整理正文重点",
        "one_liner": "收入增长",
        "logic_lines": [{"title": f"逻辑 {index}", "evidence": f"原文事实 {index}"} for index in range(4)],
    }
    assert not rv.report_depth_needs_refresh(report)
    report.update(marker)
    assert rv.report_depth_needs_refresh(report)
    assert rv.report_is_degraded(report)


def test_vision_route_bypasses_degraded_secondary_cache(monkeypatch):
    from deepfocus_api import data_store

    cached = {
        "summary": "old unavailable shell", "vision_unavailable": True,
        "logic_lines": [{"title": f"逻辑 {index}", "evidence": f"原文事实 {index}"} for index in range(4)],
    }
    refreshed = {"summary": "fresh text extraction"}

    class LLM:
        provider = "test"

        def has_vision_provider(self):
            return False

    async def extract(*args, **kwargs):
        return refreshed

    monkeypatch.setattr(data_store, "latest", lambda *args, **kwargs: cached)
    monkeypatch.setattr(rv, "CloudResearchLLM", LLM)
    monkeypatch.setattr(rv, "analyze_pdf_without_vision", extract)
    assert asyncio.run(rv.analyze_pdf_vision(b"cached-scan")) is refreshed


@pytest.mark.parametrize("route", ["single", "adaptive", "agent_failure"])
def test_failed_ocr_is_reused_only_within_the_same_request(monkeypatch, route):
    from deepfocus_api import data_store

    ocr_calls = []

    class LLM:
        provider = "glm"
        model = "text-only"

        def has_vision_provider(self):
            return False

    def ocr(*args, **kwargs):
        ocr_calls.append(kwargs["max_pages"])
        return ""

    async def failed_agents(*args, **kwargs):
        raise RuntimeError("temporary agent failure")

    monkeypatch.setattr(ma, "_ENABLED", route != "single")
    monkeypatch.setattr(ma, "CloudResearchLLM", LLM)
    monkeypatch.setattr(rv, "CloudResearchLLM", LLM)
    monkeypatch.setattr(rv, "extract_pdf_text", lambda *args, **kwargs: "")
    monkeypatch.setattr(rv, "extract_pdf_ocr_text", ocr)
    monkeypatch.setattr(ma, "_pdf_page_count", lambda *args: 4)
    monkeypatch.setattr(data_store, "latest", lambda *args, **kwargs: None)
    if route == "agent_failure":
        monkeypatch.setattr(ma, "_analyze_text_parallel", failed_agents)

    async def scenario():
        first = await ma.analyze_pdf_adaptive(b"same-scan", max_pages=14)
        assert first["vision_unavailable"]
        assert ocr_calls == [14]
        # The failure is not persisted globally: the next request retries once.
        await ma.analyze_pdf_adaptive(b"same-scan", max_pages=14)
        assert ocr_calls == [14, 14]

    asyncio.run(scenario())


def test_runtime_vision_unavailability_does_not_repeat_failed_ocr(monkeypatch):
    from deepfocus_api import data_store

    ocr_calls = []

    class LLM:
        provider = "test"
        model = "vision"

        def has_vision_provider(self):
            return True

        async def complete_vision(self, *args, **kwargs):
            raise rv.VisionUnavailableError("image capability unavailable")

    def ocr(*args, **kwargs):
        ocr_calls.append(kwargs["max_pages"])
        return ""

    monkeypatch.setattr(rv, "CloudResearchLLM", LLM)
    monkeypatch.setattr(rv, "extract_pdf_text", lambda *args, **kwargs: "")
    monkeypatch.setattr(rv, "extract_pdf_ocr_text", ocr)
    monkeypatch.setattr(rv, "render_pdf_to_pngs", lambda *args, **kwargs: [b"image"])
    monkeypatch.setattr(data_store, "latest", lambda *args, **kwargs: None)
    result = asyncio.run(rv.analyze_pdf_auto(b"scan", max_pages=14))
    assert result["vision_unavailable"]
    assert ocr_calls == [14]
