"""Contract tests for the publication-style research deep-draft API.

These tests deliberately exercise only the public schema/route surface.  The
deep-draft generator is backed by a cloud model in production, so the tests do
not make network calls or assert a particular model's prose.  They instead
protect the invariants that the article renderer relies on: bounded request
size, a stable nested evidence contract, valid timestamps/quantities, and an
explicit route registration.
"""
from __future__ import annotations

from datetime import datetime, timezone
import asyncio

import fitz
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from deepfocus_api import research_digest as rd
from deepfocus_api.schemas import (
    ResearchDeepDraftCoverage,
    ResearchDeepDraftEvidence,
    ResearchDeepDraftRequest,
    ResearchDeepDraftResponse,
    ResearchDeepDraftRisk,
    ResearchDeepDraftSection,
    ResearchDeepDraftSource,
    ResearchDeepDraftTable,
    ResearchDeepDraftWatchItem,
)


def _pdf_bytes(*paragraphs: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox((60, 60, 530, 760), "\n\n".join(paragraphs))
    return doc.tobytes()


def test_request_defaults_and_page_source_bounds():
    request = ResearchDeepDraftRequest()
    assert request.title == "研报深度解读"
    assert request.max_pages == 32
    assert request.source_ids == []

    assert ResearchDeepDraftRequest(max_pages=1).max_pages == 1
    assert ResearchDeepDraftRequest(max_pages=60).max_pages == 60
    with pytest.raises(ValidationError):
        ResearchDeepDraftRequest(max_pages=0)
    with pytest.raises(ValidationError):
        ResearchDeepDraftRequest(max_pages=61)
    with pytest.raises(ValidationError):
        ResearchDeepDraftRequest(source_ids=[str(i) for i in range(21)])


def test_nested_draft_round_trip_keeps_evidence_and_article_fields():
    evidence = ResearchDeepDraftEvidence(
        page=7,
        pages="7–8",
        excerpt="订单能见度延伸至 2027 年",
        label="订单",
        source_id="report-a",
        kind="pdf",
    )
    table = ResearchDeepDraftTable(
        title="板块信号总览",
        columns=["板块", "需求信号", "验证指标"],
        rows=[["液冷", "高", "服务器液冷渗透率"]],
        note="仅整理原文披露信息",
    )
    section = ResearchDeepDraftSection(
        id="section-1",
        title="需求从芯片向下游扩散",
        summary="高规格零部件成为瓶颈。",
        paragraphs=["第一段。", "第二段。"],
        bullets=["订单能见度改善"],
        evidence=[evidence],
        tables=[table],
    )
    watch = ResearchDeepDraftWatchItem(
        title="跟踪订单能见度",
        item="订单能见度",
        signal="订单继续上修",
        window="未来 4–6 个季度",
        metric="订单/出货",
        trigger="连续两个季度上修",
        why="验证需求是否持续",
        evidence=[evidence],
    )
    source = ResearchDeepDraftSource(
        title="亚洲 AI 供应链调研",
        label="原文第 7–8 页",
        page=7,
        pages="7–8",
        url="https://example.invalid/report.pdf",
        excerpt=evidence.excerpt,
        source_id="report-a",
    )
    response = ResearchDeepDraftResponse(
        title="亚洲 AI 供应链：深度解读",
        subtitle="需求、瓶颈与验证路径",
        subject="AI 服务器供应链",
        symbol="NVDA",
        generated_at=datetime.now(timezone.utc),
        read_time_minutes=8,
        source_count=2,
        confidence=0.82,
        one_liner="高规格零部件的供给约束仍是主线。",
        executive_summary="先给结论，再给证据。",
        core_conclusion="订单能见度与规格升级共同支撑景气。",
        thesis="需求扩散→规格升级→有效产能约束。",
        sections=[section],
        tables=[table],
        watchlist=[watch],
        risks=[ResearchDeepDraftRisk(title="需求不及预期", detail="订单可能延后", evidence=[evidence]), "原文未披露估值"],
        instruments=["NVDA"],
        sources=[source],
        disclaimer="AI 生成，仅供研究参考。",
        pages_analyzed=8,
        provider="DeepFocus",
        source_coverage=ResearchDeepDraftCoverage(
            pages_read=8,
            chars_read=12000,
            cited_claims=5,
            verified_claims=3,
            source_count=2,
            total_pages=20,
        ),
    )

    payload = response.model_dump(mode="json")
    assert payload["mode"] == "deep_draft"
    assert payload["generated_at"].endswith("Z") or "+" in payload["generated_at"]
    assert payload["sections"][0]["evidence"][0]["page"] == 7
    assert payload["tables"][0]["rows"][0][0] == "液冷"
    assert payload["watchlist"][0]["trigger"] == "连续两个季度上修"
    assert payload["source_coverage"]["cited_claims"] == 5
    assert isinstance(payload["risks"][0], dict)
    assert payload["risks"][1] == "原文未披露估值"


def test_response_rejects_invalid_article_quantities():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValidationError):
        ResearchDeepDraftResponse(generated_at=now, read_time_minutes=0)
    with pytest.raises(ValidationError):
        ResearchDeepDraftResponse(generated_at=now, confidence=1.01)
    with pytest.raises(ValidationError):
        ResearchDeepDraftResponse(generated_at=now, source_coverage={"pages_read": -1})


def test_deep_draft_fallback_is_readable_without_a_cloud_model(monkeypatch):
    class _MockLLM:
        provider = "mock"

    monkeypatch.setattr(rd, "CloudResearchLLM", lambda: _MockLLM())
    document = rd._make_document(
        _pdf_bytes(
            "Revenue grew 20% year over year. Orders are visible through 2027.",
            "The report does not provide a valuation target. Treat missing fields as unknown.",
        ),
        source_id="source-a",
        title="测试研报",
    )
    response = asyncio.run(
        rd.generate_deep_draft(
            ResearchDeepDraftRequest(title="测试深度稿", symbol="TEST"),
            documents=[document],
        )
    )
    assert response.mode == "deep_draft"
    assert response.provider == "local-fallback"
    assert response.sections
    assert response.sections[0].evidence
    assert response.source_count == 1
    assert response.source_coverage.pages_read == 1
    assert "不构成投资建议" in response.disclaimer


def test_source_loader_preserves_order_and_deduplicates_ids():
    async def loader(source_id: str):
        return _pdf_bytes(f"{source_id} has a source-bound paragraph with enough text.")

    request = ResearchDeepDraftRequest(
        title="多来源",
        source_ids=["a", "a", "b"],
    )
    docs = asyncio.run(rd.resolve_source_documents(request, loader=loader))
    assert [doc.source_id for doc in docs] == ["a", "b"]
    assert all(doc.pages_read == 1 for doc in docs)


def test_pdf_url_allowlist_and_workbench_path_guard():
    assert rd._allowed_pdf_url("https://pdf.dfcfw.com/pdf/H3_A_1.pdf")
    assert not rd._allowed_pdf_url("http://169.254.169.254/latest/meta-data/")
    assert not rd._allowed_pdf_url("file:///etc/passwd")
    with pytest.raises(HTTPException) as exc:
        rd._safe_workbench_path("downloads/海外投行报告", "../secrets.pdf")
    assert exc.value.status_code == 400


def test_model_cannot_overclaim_source_coverage_or_cite_impossible_page():
    document = rd._make_document(
        _pdf_bytes("A source paragraph with a measurable fact and a page reference."),
        source_id="source-a",
        title="测试研报",
    )
    response = rd.normalise_deep_draft(
        ResearchDeepDraftRequest(title="覆盖率测试"),
        [document],
        data={
            "sections": [{
                "title": "事实",
                "paragraphs": ["原文事实。"],
                "evidence": [{"page": 999, "source_id": "source-a", "excerpt": "不存在的页码"}],
            }],
            "sources": [{"title": "测试研报", "page": 999, "source_id": "source-a"}],
            "source_coverage": {"cited_claims": 99999, "verified_claims": 99999},
        },
    )
    # Counts and page references must be bounded by what the server actually
    # read; model-supplied metadata is not evidence by itself.
    assert response.source_coverage.cited_claims <= 2
    assert response.source_coverage.verified_claims <= response.source_coverage.cited_claims
    assert response.sections[0].evidence[0].page is None
    assert response.sources[0].page is None


def test_model_cannot_introduce_an_unread_source_id():
    document = rd._make_document(
        _pdf_bytes("Only the supplied report can be cited in this draft."),
        source_id="source-a",
        title="测试研报",
    )
    response = rd.normalise_deep_draft(
        ResearchDeepDraftRequest(title="来源边界测试"),
        [document],
        data={
            "sections": [{
                "title": "未经核验的来源",
                "paragraphs": ["正文仍可显示，但外部来源不能伪装成当前研报证据。"],
                "evidence": [{
                    "page": 1,
                    "source_id": "model-invented-source",
                    "excerpt": "这段内容并未随请求提供。",
                }],
            }],
            "sources": [{
                "title": "模型虚构的外部报告",
                "source_id": "model-invented-source",
                "page": 1,
            }],
        },
    )
    assert response.sections[0].evidence == []
    assert all(item.source_id != "model-invented-source" for item in response.sources)


def test_compliance_guard_neutralises_generated_prose_but_keeps_short_source_quote():
    document = rd._make_document(
        _pdf_bytes("机构原文：建议买入，订单将在下季度披露。"),
        source_id="source-a",
        title="机构研报",
    )
    response = rd.normalise_deep_draft(
        ResearchDeepDraftRequest(title="合规测试"),
        [document],
        data={
            "one_liner": "建议买入，确定性较高。",
            "sections": [{
                "title": "机构观点",
                "paragraphs": ["建议买入属于原作者观点。"],
                "evidence": [{
                    "page": 1,
                    "source_id": "source-a",
                    "excerpt": "机构原文：建议买入，订单将在下季度披露。" * 3,
                }],
            }],
        },
    )
    assert "建议买入" not in response.one_liner
    assert "建议买入" not in response.sections[0].paragraphs[0]
    quote = response.sections[0].evidence[0].excerpt
    assert "建议买入" in quote  # quoted source wording is not silently rewritten
    assert len(quote) <= 80


def test_deep_draft_route_is_explicitly_registered():
    # Importing the app is intentionally kept to one assertion: this catches
    # the common integration failure where schemas/module exist but the
    # FastAPI route was never mounted.  No endpoint is called, so no auth or
    # model configuration is required.
    from deepfocus_api import main

    routes = {
        (getattr(route, "path", ""), tuple(sorted(getattr(route, "methods", set()) or set())))
        for route in main.app.routes
    }
    assert any(path == "/api/research/deep-draft" and "POST" in methods for path, methods in routes)


def test_deep_draft_auth_is_handler_gated_and_not_prefix_wildcarded():
    # The auth middleware must let the handler see anonymous requests so it
    # can return the product's 403/402 quota responses; a broad
    # ``/api/research`` prefix must still not expose private source files.
    from deepfocus_api.auth import is_public_path

    assert is_public_path("/api/research/deep-draft") is True
    assert is_public_path("/api/research/deep-draft/anything") is False
    assert is_public_path("/api/research/wire-file") is False


def test_main_route_serializes_json_cached_draft(monkeypatch):
    """A cached ISO timestamp must survive FastAPI/Pydantic response validation."""
    from starlette.requests import Request

    from deepfocus_api import main

    cached = {
        "title": "缓存深度稿",
        "generated_at": "2026-08-30T04:00:00+00:00",
        "mode": "deep_draft",
        "read_time_minutes": 1,
        "confidence": 0.4,
        "sections": [{"id": "s1", "title": "原文概览", "paragraphs": ["原文。"]}],
        "watchlist": [],
        "risks": [],
        "sources": [],
        "source_coverage": {
            "pages_read": 1,
            "chars_read": 3,
            "cited_claims": 0,
            "verified_claims": 0,
            "source_count": 0,
            "total_pages": 1,
        },
    }
    monkeypatch.setattr(main, "metrics_get_ai_cache", lambda _key: cached)
    monkeypatch.setattr(main, "_check_ai_quota", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "metrics_incr", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "metrics_incr_ai_ref", lambda *args, **kwargs: None)
    request = Request({"type": "http", "method": "POST", "path": "/api/research/deep-draft", "headers": [], "client": ("127.0.0.1", 1234), "query_string": b""})
    response = asyncio.run(
        main.api_research_deep_draft(
            ResearchDeepDraftRequest(title="缓存深度稿"), request, None,
        )
    )
    assert isinstance(response, ResearchDeepDraftResponse)
    assert response.generated_at.year == 2026
    # The concrete model is an internal diagnostic; the API exposes the
    # product brand consistently with the compact research endpoint.
    assert response.provider == main._AI_BRAND
    assert response.mode == "deep_draft"
