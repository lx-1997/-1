from __future__ import annotations

import asyncio
import json

import fitz
import pytest

from deepfocus_api import research_vision as rv


def _pdf_bytes(pages: int = 3) -> bytes:
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"page {i + 1}")
    return doc.tobytes()


class _FakeLLM:
    def __init__(self, *, replies=None, raises_then=None):
        self.provider = "minimax"
        self.model = "MiniMax-M3"
        self._replies = list(replies or [])
        self._raises_then = list(raises_then or [])
        self.calls = []

    async def complete_vision(self, prompt, image_pngs, **kwargs):
        self.calls.append(len(image_pngs))
        if self._raises_then:
            exc = self._raises_then.pop(0)
            if exc is not None:
                raise exc
        return self._replies.pop(0)


# --- pure helpers ----------------------------------------------------------

def test_render_pdf_to_pngs_caps_pages():
    pngs = rv.render_pdf_to_pngs(_pdf_bytes(5), max_pages=3)
    assert len(pngs) == 3
    assert all(p[:8] == b"\x89PNG\r\n\x1a\n" for p in pngs)  # PNG magic


def test_render_caps_at_max_vision_pages():
    pngs = rv.render_pdf_to_pngs(_pdf_bytes(12), max_pages=999)
    assert len(pngs) == rv.MAX_VISION_PAGES


def test_clamp_confidence_and_str_list():
    assert rv._clamp_confidence("0.8") == 0.8
    assert rv._clamp_confidence(2) == 1.0
    assert rv._clamp_confidence("x") == 0.5
    assert rv._as_str_list(["a", "", "b"], 5) == ["a", "b"]
    assert rv._as_str_list("solo", 5) == ["solo"]


# --- analyze_pdf_vision ----------------------------------------------------

def test_analyze_pdf_vision_parses_json(monkeypatch):
    reply = json.dumps({
        "summary": "标的看多", "key_points": ["要点1", "要点2"],
        "risks": ["风险1"], "rating": "买入", "target_price": "$415", "confidence": 0.82,
    })
    fake = _FakeLLM(replies=[reply])
    monkeypatch.setattr(rv, "CloudResearchLLM", lambda: fake)

    result = asyncio.run(rv.analyze_pdf_vision(_pdf_bytes(2), title="特斯拉", symbol="TSLA"))
    assert result["summary"] == "标的看多"
    assert result["key_points"] == ["要点1", "要点2"]
    assert result["rating"] == "买入"
    assert result["target_price"] == "$415"
    assert result["confidence"] == 0.82
    assert result["pages_analyzed"] == 2
    assert result["provider"] == "MiniMax-M3"


def test_analyze_pdf_vision_drops_sensitive_page_and_retries(monkeypatch):
    sensitive = RuntimeError(
        "视觉模型调用失败：Error code: 422 - messages[0]'s content[2] image is sensitive (1026)"
    )
    ok = json.dumps({"summary": "ok", "key_points": ["k"], "risks": [], "confidence": 0.5})
    fake = _FakeLLM(replies=[ok], raises_then=[sensitive, None])
    monkeypatch.setattr(rv, "CloudResearchLLM", lambda: fake)

    result = asyncio.run(rv.analyze_pdf_vision(_pdf_bytes(3), title="x"))
    # 3 张图 → 第一次 content[2](第2张)被判 sensitive 丢弃 → 第二次用 2 张成功
    assert fake.calls == [3, 2]
    assert result["pages_analyzed"] == 2
    assert result["summary"] == "ok"


def test_analyze_pdf_vision_rejects_mock_provider(monkeypatch):
    class _Mock:
        provider = "mock"
        model = "mock"
    monkeypatch.setattr(rv, "CloudResearchLLM", lambda: _Mock())
    with pytest.raises(RuntimeError):
        asyncio.run(rv.analyze_pdf_vision(_pdf_bytes(1)))


def test_analyze_pdf_vision_empty_reply_raises(monkeypatch):
    fake = _FakeLLM(replies=[json.dumps({"summary": "", "key_points": []})])
    monkeypatch.setattr(rv, "CloudResearchLLM", lambda: fake)
    with pytest.raises(RuntimeError):
        asyncio.run(rv.analyze_pdf_vision(_pdf_bytes(1)))
