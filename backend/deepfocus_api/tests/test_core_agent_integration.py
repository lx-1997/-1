"""Integration guards for the CoreAgent boundary at user-facing routes."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from deepfocus_api import main
from deepfocus_api.schemas import (
    CnEarningsDiagnosisRequest,
    CnEarningsDiagnosisResponse,
    CnEarningsRecord,
    GeneralChatRequest,
    GeneralChatResponse,
    OrchestratorChatRequest,
    ProfessionalEvalRunRequest,
    ProfessionalReportAnalysisResponse,
    PersonProfile,
    PersonVoiceItem,
    ShareholderChangeInterpretRequest,
    ShareholderChangeInterpretResponse,
    ShareholderChangeRecord,
    StockAnalysisRequest,
    StockAnalysisResponse,
    StockSnapshot,
)


@pytest.mark.asyncio
async def test_context_hint_is_kept_out_of_the_current_question(monkeypatch):
    """Context/previous turns must reach the model without changing routing text."""
    captured: dict[str, object] = {}

    async def fake_route(request, _ifind, **kwargs):
        captured["message"] = request.message
        captured["context_prefix"] = kwargs.get("context_prefix")
        return main.OrchestratorChatResponse(
            provider="test",
            model="test",
            generated_at=datetime.now(timezone.utc),
            title="测试",
            content="已回答",
        )

    # Exercise the HTTP body parsing path, which is where context_hint is
    # separated from message.  The small fake Request avoids TestClient setup.
    from starlette.requests import Request
    import json

    payload = json.dumps({
        "message": "总结下近期研报",
        "context_hint": "【当前模块上下文】\n上一轮提到减持和财报",
    }, ensure_ascii=False).encode()
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.request", "body": b"", "more_body": False}
        delivered = True
        return {"type": "http.request", "body": payload, "more_body": False}

    req = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/agents/tool-research",
            "headers": [(b"content-type", b"application/json")],
            "client": ("127.0.0.1", 1),
        },
        receive,
    )
    monkeypatch.setattr(main, "_route_orchestrator_chat", fake_route)
    monkeypatch.setattr(main, "_check_agent_quota", lambda *_a, **_k: None)
    monkeypatch.setattr(main, "ifind_enhance_enabled", lambda *_a, **_k: False)

    result = await main.tool_research(req, _user={"username": "tester"})
    assert result["ok"] is True
    assert captured["message"] == "总结下近期研报"
    assert "减持和财报" in str(captured["context_prefix"])


@pytest.mark.asyncio
async def test_stock_analysis_is_projected_through_core_agent(monkeypatch):
    stock = StockSnapshot(symbol="600519", name="贵州茅台")

    async def fake_analyze(_request):
        return StockAnalysisResponse(
            provider="test",
            model="test-model",
            generated_at=datetime.now(timezone.utc),
            executive_summary="结构化测试结果",
            sentiment_label="neutral",
            sentiment_score=0,
            risk_level="medium",
            catalysts=[],
            risks=[],
            watch_items=[],
            suggested_questions=[],
        )

    monkeypatch.setattr(main.llm, "analyze_stock", fake_analyze)
    result = await main.stock_analysis(StockAnalysisRequest(stock=stock))

    assert result.executive_summary == "结构化测试结果"
    assert result.core_agent_run_id
    assert result.core_agent_protocol_version == "core-agent-v1"
    assert result.core_agent_route == "stock-analysis"


@pytest.mark.asyncio
async def test_general_chat_adapter_keeps_legacy_response_shape(monkeypatch):
    async def fake_general(_request):
        return GeneralChatResponse(
            provider="test",
            model="test-model",
            generated_at=datetime.now(timezone.utc),
            content="普通回答",
        )

    monkeypatch.setattr(main.llm, "general_chat", fake_general)
    async def no_fresh_evidence(*_args, **_kwargs):
        return 0

    monkeypatch.setattr(main, "_acquire_fresh_evidence_if_thin", no_fresh_evidence)
    request = GeneralChatRequest(message="你好")
    result = await main.general_chat(request)

    assert result.content == "普通回答"
    assert result.core_agent_run_id
    assert result.core_agent_route == "general-chat"


@pytest.mark.asyncio
async def test_professional_chat_skill_is_inside_core_agent(monkeypatch):
    """The early professional-chat branch must not bypass the shared envelope."""
    monkeypatch.setattr(main, "_select_professional_chat_report", lambda _request: None)
    result = await main._maybe_professional_research_chat(
        OrchestratorChatRequest(message="总结一下专业财报库")
    )
    assert result is not None
    assert result.core_agent_run_id
    assert result.core_agent_route == "professional-catalog"


@pytest.mark.asyncio
async def test_professional_report_http_errors_are_not_rewritten(monkeypatch):
    async def missing_report(*_args, **_kwargs):
        raise HTTPException(status_code=404, detail="Professional report not found")

    monkeypatch.setattr(main, "analyze_professional_report", missing_report)
    with pytest.raises(HTTPException) as caught:
        await main.api_analyze_professional_report("missing-report")
    assert caught.value.status_code == 404


@pytest.mark.asyncio
async def test_professional_report_adapter_keeps_response_shape(monkeypatch):
    async def fake_analyze(*_args, **_kwargs):
        return ProfessionalReportAnalysisResponse(
            report={
                "id": "r1",
                "title": "测试报告",
                "symbol": "600519",
                "parser": "plain",
                "char_count": 10,
                "created_at": "2026-08-30T00:00:00Z",
                "updated_at": "2026-08-30T00:00:00Z",
            },
            summary="结构化财报结论",
            confidence=0.75,
        )

    monkeypatch.setattr(main, "analyze_professional_report", fake_analyze)
    result = await main.api_analyze_professional_report("r1")
    assert result.summary == "结构化财报结论"
    assert result.core_agent_run_id
    assert result.core_agent_route == "professional-report-analysis"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "fetch_name", "route"),
    [
        (main.market_dashboard_analyze, "fetch_market_dashboard", "market-dashboard-analysis"),
        (main.ashare_dashboard_analyze, "fetch_ashare_dashboard", "market-dashboard-ashare-analysis"),
    ],
)
async def test_market_dashboard_analysis_uses_core_agent(monkeypatch, endpoint, fetch_name, route):
    async def fake_dashboard():
        return {
            "overall_signal": "neutral",
            "overall_score": 50,
            "categories": [{"indicators": [{
                "name": "测试指标",
                "value": 1,
                "unit": "点",
                "signal": "neutral",
                "status": "ok",
            }]}],
        }

    async def fake_analyze(**_kwargs):
        return {"summary": "仪表盘解读", "confidence": 0.7}

    monkeypatch.setattr(main, fetch_name, fake_dashboard)
    monkeypatch.setattr(main.llm, "analyze_market_dashboard", fake_analyze)
    result = await endpoint()

    assert result.summary == "仪表盘解读"
    assert result.core_agent_run_id
    assert result.core_agent_protocol_version == "core-agent-v1"
    assert result.core_agent_route == route


@pytest.mark.asyncio
async def test_stock_screen_intent_parser_uses_core_agent(monkeypatch):
    class FakeScreenLLM:
        provider = "test"
        provider_name = "test"
        model = "test-model"

        async def parse_screen_query(self, _query):
            return None

    # Keep the route on the process-wide adapter shared by CoreAgent; a new
    # CloudResearchLLM here would create an independent model-pool cursor.
    monkeypatch.setattr(main, "llm", FakeScreenLLM())
    result = await main.stock_screen("筛选近期动量强的股票")

    assert result.provider == "rule-template"
    assert result.core_agent_run_id
    assert result.core_agent_protocol_version == "core-agent-v1"
    assert result.core_agent_route == "stock-screen-query"


@pytest.mark.asyncio
async def test_skill_interpretation_endpoints_use_core_agent_without_shape_changes(monkeypatch):
    seen_routes: list[str] = []

    class FakeCoreAgent:
        async def run_adapter(self, _request, runner, *, route):
            seen_routes.append(route)
            return type("Result", (), {
                "raw": await runner(),
                "run_id": f"test-{route}",
                "protocol_version": "core-agent-v1",
                "route": route,
            })()

    async def fake_shareholder(_request, _llm):
        return ShareholderChangeInterpretResponse(
            provider="test",
            model="test-model",
            generated_at=datetime.now(timezone.utc),
            symbol="600000",
            name="浦发银行",
            title="增持公告",
            verdict="观察",
            summary="测试股东变动解读",
        )

    async def fake_earnings(_request, _llm):
        return CnEarningsDiagnosisResponse(
            provider="test",
            model="test-model",
            generated_at=datetime.now(timezone.utc),
            symbol="600177",
            name="雅戈尔",
            title="年度报告",
            report_type="annual",
            summary="测试财报诊断",
            verdict="中性",
        )

    record = ShareholderChangeRecord(
        symbol="600000",
        name="浦发银行",
        announcement_date="2026-05-21",
        direction="increase",
        status="plan",
        title="增持公告",
        url="https://example.com/shareholder.pdf",
    )
    earnings_record = CnEarningsRecord(
        symbol="600177",
        name="雅戈尔",
        announcement_date="2026-04-28",
        report_type="annual",
        title="年度报告",
        url="https://example.com/earnings.pdf",
    )
    monkeypatch.setattr(main, "core_agent", FakeCoreAgent())
    monkeypatch.setattr(main, "interpret_shareholder_change", fake_shareholder)
    monkeypatch.setattr(main, "diagnose_cn_earnings", fake_earnings)

    shareholder = await main.shareholder_change_interpret(
        ShareholderChangeInterpretRequest(record=record)
    )
    earnings = await main.cn_earnings_diagnose(
        CnEarningsDiagnosisRequest(record=earnings_record)
    )

    assert shareholder.summary == "测试股东变动解读"
    assert earnings.summary == "测试财报诊断"
    assert shareholder.core_agent_run_id == "test-shareholder-change-interpret"
    assert earnings.core_agent_run_id == "test-cn-earnings-diagnose"
    assert seen_routes == ["shareholder-change-interpret", "cn-earnings-diagnose"]


@pytest.mark.asyncio
async def test_professional_eval_endpoint_uses_core_agent_without_shape_changes(monkeypatch):
    seen: dict[str, str] = {}

    class FakeCoreAgent:
        async def run_adapter(self, _request, runner, *, route):
            seen["route"] = route
            return type("Result", (), {
                "raw": await runner(),
                "run_id": "test-professional-eval",
                "protocol_version": "core-agent-v1",
                "route": route,
            })()

    async def fake_eval(_request):
        return {"total": 0, "passed": 0, "pass_rate": 0, "citation_rate": 0,
                "answer_match_rate": 0, "refusal_guard_rate": 0, "cases": []}

    monkeypatch.setattr(main, "core_agent", FakeCoreAgent())
    monkeypatch.setattr(main, "run_professional_eval", fake_eval)
    result = await main.api_run_professional_eval(ProfessionalEvalRunRequest())

    assert result["total"] == 0
    assert result["core_agent_run_id"] == "test-professional-eval"
    assert seen["route"] == "professional-eval"


@pytest.mark.asyncio
async def test_people_digest_model_call_uses_core_agent_without_shape_changes(monkeypatch):
    seen: dict[str, str] = {}

    class FakeCoreAgent:
        async def run_adapter(self, _request, runner, *, route):
            seen["route"] = route
            return type("Result", (), {"raw": await runner()})()

    class FakeLLM:
        provider = "test"
        provider_name = "test-provider"

        async def complete_json(self, _prompt, **_kwargs):
            return {"digest": "测试人物近期关注算力与产业链。"}

    profile = PersonProfile(
        id="test-person",
        name="测试人物",
        role="分析师",
        org="测试机构",
        item_count=1,
        items=[PersonVoiceItem(
            id="item-1",
            title="测试人物谈算力产业链",
            url="https://example.com/item-1",
            source_name="测试来源",
        )],
    )
    monkeypatch.setattr(main, "core_agent", FakeCoreAgent())
    monkeypatch.setattr(main, "CloudResearchLLM", FakeLLM)
    main._digest_cache.clear()

    digest, provider = await main._synthesize_person_digest(profile, refresh=True)

    assert digest == "测试人物近期关注算力与产业链。"
    assert provider == "test-provider"
    assert seen["route"] == "people-digest"
