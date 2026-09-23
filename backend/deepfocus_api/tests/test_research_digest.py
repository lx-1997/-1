from __future__ import annotations

import asyncio

import fitz
from fastapi import FastAPI
from fastapi.testclient import TestClient

from deepfocus_api import research_digest as digest
from deepfocus_api.schemas import ResearchDeepDraftRequest


def _pdf(text: str = "Revenue grew 20 percent. Track orders next quarter.") -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    return doc.tobytes()


def test_request_defaults_and_page_cap():
    request = ResearchDeepDraftRequest(title="测试")
    assert request.max_pages == 32
    assert ResearchDeepDraftRequest(title="测试", max_pages=60).max_pages == 60


def test_fallback_preserves_source_evidence_and_marks_missing_fields():
    document = digest._make_document(_pdf(), source_id="source-a", title="样例研报")
    result = asyncio.run(
        digest.generate_deep_draft(
            ResearchDeepDraftRequest(title="样例研报", symbol="TEST"),
            documents=[document],
        )
    )
    assert result.mode == "deep_draft"
    assert result.provider == "local-fallback"
    assert result.source_count == 1
    assert result.source_coverage.pages_read == 1
    assert result.sections
    assert result.sections[0].evidence[0].page == 1
    assert "原文未提供" in result.disclaimer


def test_model_result_is_normalised_to_article_contract(monkeypatch):
    class FakeLLM:
        provider = "openai"
        model = "fake-model"

        async def complete_json(self, prompt, **kwargs):
            assert "sections" in prompt
            return {
                "subtitle": "供需变化",
                "one_liner": "中性：等待订单验证",
                "plain_language_summary": "简单说，订单还在增长，但需要继续观察能否兑现。",
                "executive_summary": "报告给出订单数据。",
                "core_conclusion": "订单是核心变量。",
                "thesis": "需求到订单再到盈利。",
                "decision_implication": "后续重点验证订单披露。",
                "metrics": [{"label": "订单增速", "value": "20%", "change": "+3pct", "context": "较上期改善"}],
                "glossary": [{"term": "NET", "meaning": "增加意愿减去减少意愿"}],
                "sections": [
                    {
                        "id": "s1",
                        "title": "订单",
                        "summary": "订单变化",
                        "paragraphs": ["订单增加。"],
                        "evidence": [{"page": 1, "excerpt": "订单增加", "source_id": "source-a"}],
                    }
                ],
                "tables": [{"title": "数据", "columns": ["指标"], "rows": [["20%"]]}],
                "watchlist": [{"item": "订单", "window": "下季度", "trigger": "订单披露"}],
                "risks": [{"title": "需求", "detail": "需求不及预期"}],
                "instruments": ["TEST"],
                "confidence": 0.8,
            }

    monkeypatch.setattr(digest, "CloudResearchLLM", lambda: FakeLLM())
    document = digest._make_document(_pdf(), source_id="source-a", title="样例研报")
    result = asyncio.run(
        digest.generate_deep_draft(
            ResearchDeepDraftRequest(title="样例研报", symbol="TEST"),
            documents=[document],
        )
    )
    assert result.provider == "fake-model"
    assert result.sections[0].title == "订单"
    assert result.tables[0].rows == [["20%"]]
    assert result.watchlist[0].trigger == "订单披露"
    assert result.source_coverage.cited_claims >= 1
    assert result.plain_language_summary.startswith("简单说")
    assert result.metrics[0].value == "20%"
    assert result.glossary[0]["term"] == "NET"


def test_standalone_router_returns_safe_shape_without_source():
    app = FastAPI()
    app.include_router(digest.router)
    response = TestClient(app).post("/api/research/deep-draft", json={"title": "无来源"})
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "deep_draft"
    assert body["sections"]
    assert body["provider"] == "local-fallback"
