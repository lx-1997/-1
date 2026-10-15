import pytest

from deepfocus_api import research_quick
from deepfocus_api.research_digest import ResearchDeepDraftRequest, SourceDocument


@pytest.mark.asyncio
@pytest.mark.parametrize("has_text", [False, True])
async def test_parallel_preview_never_enters_vision_after_scan_or_text_failure(monkeypatch, has_text):
    text_calls, vision_calls = [], []

    class LLM:
        provider, model = "test", "test-model"

        async def complete_json(self, *args, **kwargs):
            text_calls.append(True)
            raise RuntimeError("text provider failed")

    async def vision(*args, **kwargs):
        vision_calls.append(True)
        raise AssertionError("The parallel preview must leave the vision batch to deep")

    monkeypatch.setattr(research_quick, "CloudResearchLLM", LLM)
    monkeypatch.setattr(research_quick, "analyze_pdf_adaptive", vision)
    doc = SourceDocument("source", "report", pdf_bytes=b"pdf", total_pages=3,
                         page_texts=[(1, "text extracted from report")] if has_text else [])
    result = await research_quick.generate_deep_quick(
        ResearchDeepDraftRequest(title="report"), documents=[doc], allow_vision=False,
    )
    assert result is None
    assert len(text_calls) == int(has_text)
    assert vision_calls == []


@pytest.mark.asyncio
async def test_parallel_text_preview_still_returns_useful_result(monkeypatch):
    class LLM:
        provider, model = "test", "test-model"

        async def complete_json(self, *args, **kwargs):
            return {"one_liner": "Verified text preview", "summary": "Evidence from the text"}

    async def vision(*args, **kwargs):
        raise AssertionError("Successful text preview does not need vision")

    monkeypatch.setattr(research_quick, "CloudResearchLLM", LLM)
    monkeypatch.setattr(research_quick, "analyze_pdf_adaptive", vision)
    doc = SourceDocument("source", "report", pdf_bytes=b"pdf", page_texts=[(1, "report text")])
    result = await research_quick.generate_deep_quick(
        ResearchDeepDraftRequest(title="report"), documents=[doc], allow_vision=False,
    )
    assert result["one_liner"] == "Verified text preview"
    assert result["quick"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("has_text", [False, True])
async def test_other_callers_keep_default_vision_capability(monkeypatch, has_text):
    calls = []

    class LLM:
        provider, model = "test", "test-model"

        async def complete_json(self, *args, **kwargs):
            raise RuntimeError("text provider failed")

    async def vision(*args, **kwargs):
        calls.append(kwargs["max_pages"])
        return {"one_liner": "Visual preview", "summary": "Read from image"}

    monkeypatch.setattr(research_quick, "CloudResearchLLM", LLM)
    monkeypatch.setattr(research_quick, "analyze_pdf_adaptive", vision)
    doc = SourceDocument("source", "report", pdf_bytes=b"pdf", total_pages=3,
                         page_texts=[(1, "report text")] if has_text else [])
    result = await research_quick.generate_deep_quick(ResearchDeepDraftRequest(title="report"), documents=[doc])
    assert result["one_liner"] == "Visual preview"
    assert result["quick_vision"] is True
    assert calls == [1]
