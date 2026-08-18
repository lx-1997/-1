from __future__ import annotations

import asyncio

import pytest

from deepfocus_api import research_vision as rv
from deepfocus_api.research_prewarm_policy import research_prewarm_download_cap


def test_prewarm_budget_is_released_across_the_day():
    expected = {
        0: 10,
        6: 10,
        7: 20,
        9: 20,
        10: 30,
        13: 30,
        14: 35,
        17: 35,
        18: 40,
        23: 40,
    }
    assert {hour: research_prewarm_download_cap(40, hour) for hour in expected} == expected
    assert research_prewarm_download_cap(0, 12) == 0


def test_auto_does_not_turn_model_failure_into_slow_vision_retry(monkeypatch):
    vision_calls = []

    async def failed_text(*args, **kwargs):
        raise RuntimeError("云模型 75 秒内未返回")

    async def vision(*args, **kwargs):
        vision_calls.append(True)
        return {"unexpected": True}

    monkeypatch.setattr(rv, "analyze_pdf_text", failed_text)
    monkeypatch.setattr(rv, "analyze_pdf_vision", vision)
    with pytest.raises(RuntimeError, match="75 秒"):
        asyncio.run(rv.analyze_pdf_auto(b"pdf"))
    assert vision_calls == []


def test_auto_falls_back_when_pdf_really_has_no_text_layer(monkeypatch):
    async def no_text(*args, **kwargs):
        raise rv.PdfTextUnavailable("文本层过少")

    async def vision(*args, **kwargs):
        return {"path": "vision"}

    monkeypatch.setattr(rv, "analyze_pdf_text", no_text)
    monkeypatch.setattr(rv, "analyze_pdf_vision", vision)
    assert asyncio.run(rv.analyze_pdf_auto(b"pdf")) == {"path": "vision"}


def test_text_extraction_failure_is_a_valid_vision_fallback(monkeypatch):
    def broken_extract(*args, **kwargs):
        raise ValueError("damaged pdf")

    monkeypatch.setattr(rv, "extract_pdf_text", broken_extract)
    with pytest.raises(rv.PdfTextUnavailable, match="读取 PDF 文本层"):
        asyncio.run(rv.analyze_pdf_text(b"bad-pdf"))
