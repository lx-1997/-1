from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from deepfocus_api import agent_runtime as ar
from deepfocus_api.schemas import (
    InvestmentTaskCreateRequest,
    InvestmentTaskRecord,
    OptionsSignal,
    OptionsSignalResponse,
    ProfessionalCitation,
    ProfessionalRagQueryResponse,
    ProfessionalReportAnalysisResponse,
    ProfessionalReportRecord,
)


def _task(symbol: str = "TSLA", name: str = "Tesla") -> InvestmentTaskRecord:
    return InvestmentTaskRecord(
        id="task-quality",
        title=f"审查 {symbol} 的主要风险",
        symbol=symbol,
        asset_name=name,
        task_type="investment_research",
        engine="deepfocus",
        status="running",
        priority=3,
        assigned_agent="ReportAgent",
        progress=90,
        created_at="2026-05-16T00:00:00+00:00",
        updated_at="2026-05-16T00:02:00+00:00",
        input={
            "symbol": symbol,
            "asset_name": name,
            "objective": f"审查 {symbol} 的主要风险",
            "context": "这是一段很长的任务上下文。" * 30,
        },
        logs=[],
    )


def test_mock_result_does_not_call_long_context_sufficient_evidence() -> None:
    result = ar._mock_investment_result(_task(), evidence=[], payload_override=_task().input)

    evidence_lines = result["agent_findings"]["evidence"]

    assert result["confidence"] <= 0.35
    assert result["evidence"] == []
    assert any("未命中强相关" in item for item in evidence_lines)
    assert all("资料较充分" not in item for item in evidence_lines)


def test_tsla_fallback_profile_is_specific() -> None:
    result = ar._mock_investment_result(_task(), evidence=[], payload_override=_task().input)
    research_text = "\n".join(result["agent_findings"]["research"])
    action_text = "\n".join(result["action_plan"])

    assert "Optimus" in research_text
    assert "FSD" in research_text
    assert "10-Q" in action_text
    assert "需要从商业模式、盈利质量" not in research_text


def test_guardrails_cap_confidence_and_strip_unbacked_price_claims() -> None:
    task = _task()
    payload = {
        **task.input,
        "market_quote": {},
        "evidence": [
            {
                "source": "雪球关键词抓取",
                "source_type": "agent_crawl",
                "source_category": "sentiment",
                "title": "TSLA Optimus 万亿市场讨论",
                "text": "TSLA Optimus 社区热帖，未包含官方链接。",
                "credibility_score": 0.46,
                "tags": ["TSLA", "Optimus"],
                "url": "https://xueqiu.com/statuses/1",
            }
        ],
    }
    result = ar._apply_report_guardrails(
        {
            "investor_summary": "特斯拉当前股价422.24美元，单日跌4.75%。Optimus 叙事很热。",
            "plain_language_takeaway": "股价刚跌近5%，但可以继续看 Optimus。",
            "decision": "candidate",
            "confidence": 0.9,
            "agent_findings": {"evidence": ["资料较充分"]},
            "risk_controls": [],
            "action_plan": [],
            "evidence": [{"title": "模型虚构证据"}],
        },
        task,
        payload,
    )

    assert result["confidence"] <= 0.45
    assert result["decision"] == "research_more"
    assert "422.24" not in result["investor_summary"]
    assert "资料较充分" not in result["agent_findings"]["evidence"]
    assert "社区/媒体/自媒体" in result["agent_findings"]["evidence"][0]
    assert result["evidence"][0]["source"] == "雪球关键词抓取"


def test_cjk_context_token_estimate_is_conservative() -> None:
    english = "a" * 80
    chinese = "中" * 80

    assert ar._estimate_context_tokens(chinese) > ar._estimate_context_tokens(english) * 4


def test_tool_result_compression_preserves_middle_error_signal() -> None:
    long_log = "\n".join(
        [
            *[f"ok build step {index}" for index in range(140)],
            "Traceback (most recent call last):",
            "  File \"/workspace/agent_loop.py\", line 88, in run",
            "RuntimeError: critical middle failure from tool result",
            *[f"ok cleanup step {index}" for index in range(140)],
        ]
    )

    compressed = ar._compress_tool_result_text(long_log, max_tokens=180)

    assert "context-gc" in compressed
    assert "RuntimeError: critical middle failure" in compressed
    assert "/workspace/agent_loop.py" in compressed


def test_compact_cloud_payload_adds_checkpoint_and_fits_budget(monkeypatch) -> None:
    monkeypatch.setattr(ar, "AGENT_CONTEXT_WINDOW_TOKENS", 9000)
    monkeypatch.setattr(ar, "AGENT_CONTEXT_RESERVED_TOKENS", 1000)
    monkeypatch.setattr(ar, "AGENT_COMPACT_EVIDENCE_LIMIT", 8)

    noisy_text = "\n".join(
        [
            *[f"normal tool output line {index}" for index in range(250)],
            "ERROR: option chain provider timed out in the middle of the log",
            "  at /tmp/deepfocus/options_signal.py:55",
            *[f"normal tail line {index}" for index in range(250)],
        ]
    )
    payload = {
        "title": "TSLA 上下文压缩测试",
        "symbol": "TSLA",
        "asset_name": "特斯拉",
        "task_type": "investment_research",
        "horizon": "1-4周",
        "investor_profile": "稳健",
        "objective": "分析 TSLA 期权链、财报证据和中期风险",
        "context": "中文上下文需要更保守估算。" * 800,
        "market_quote": {"symbol": "TSLA", "price": None, "warning": "quote timeout"},
        "evidence": [
            {
                "source": "SEC EDGAR",
                "source_type": "filing",
                "source_category": "filing",
                "title": f"TSLA 10-Q filing {index}",
                "text": noisy_text,
                "credibility_score": 0.94,
                "url": f"https://www.sec.gov/example/{index}",
                "tags": ["SEC", "10-Q", "TSLA"],
            }
            for index in range(6)
        ],
    }

    compact = ar._compact_cloud_report_payload(payload, output_token_budget=1000)

    assert "context_checkpoint" in compact
    assert compact["context_checkpoint"]["compression"]["cjk_aware"] is True
    assert compact["context_gc"]["estimated_tokens_after_fit"] <= compact["context_gc"]["budget_tokens"]
    assert any("ERROR: option chain provider" in item["takeaway"] for item in compact["evidence"])


def test_external_research_filter_requires_target_relevance() -> None:
    payload = {"symbol": "TSLA", "asset_name": "特斯拉"}

    assert ar._external_item_matches_target(
        "特斯拉 2026 年基本面研报",
        "汽车毛利率、FSD 和 Robotaxi 情景分析。",
        payload,
    )
    assert not ar._external_item_matches_target(
        "缠论A股主升浪筛选",
        "摘要：财报披露前提前分析业绩预期。特斯拉 TSLA 在华销量被一句带过。",
        payload,
    )


def _professional_report(
    report_id: str,
    title: str,
    *,
    symbol: str | None = None,
) -> ProfessionalReportRecord:
    return ProfessionalReportRecord(
        id=report_id,
        source_item_id=None,
        title=title,
        symbol=symbol,
        report_type="research",
        period="2026Q1",
        parser="pdf",
        char_count=1000,
        metadata={"filename": f"{title}.pdf"},
        metrics_count=2,
        chunks_count=5,
        created_at="2026-05-01T00:00:00+00:00",
        updated_at="2026-05-01T00:00:00+00:00",
    )


def test_professional_report_lookup_falls_back_to_asset_name(monkeypatch) -> None:
    reports = [
        _professional_report("r-other", "通用机器人产业链综述"),
        _professional_report("r-tsla", "特斯拉 FSD 与 Robotaxi 深度研报"),
    ]

    def fake_list_professional_reports(*, symbol=None, limit=50):
        if symbol:
            return []
        return reports[:limit]

    monkeypatch.setattr(ar, "list_professional_reports", fake_list_professional_reports)

    selected = ar._find_professional_reports_for_payload({"asset_name": "特斯拉"}, report_limit=2)

    assert [report.id for report in selected] == ["r-tsla"]


def test_professional_evidence_collects_name_matched_report(monkeypatch) -> None:
    report = _professional_report("r-tsla", "Tesla 2026Q1 业绩点评", symbol="TSLA")
    rag_symbols: list[str | None] = []

    def fake_list_professional_reports(*, symbol=None, limit=50):
        if symbol:
            return []
        return [report]

    async def fake_analyze_professional_report(report_id, request):
        return ProfessionalReportAnalysisResponse(
            report=report,
            summary="特斯拉收入增长，毛利率仍需跟踪。",
            key_metrics=[],
            quality_flags=[],
            risks=["需求波动"],
            follow_up_questions=[],
            citations=[],
            confidence=0.82,
        )

    async def fake_query_professional_rag(request):
        rag_symbols.append(request.symbol)
        return ProfessionalRagQueryResponse(
            answer="引用显示 FSD 与车型周期是核心变量。",
            citations=[
                ProfessionalCitation(
                    citation_id="C1",
                    kind="chunk",
                    source_id="chunk-1",
                    report_id=report.id,
                    report_title=report.title,
                    title="管理层讨论",
                    text="FSD take-rate and Robotaxi timeline remain key assumptions.",
                    score=0.9,
                )
            ],
            metrics=[],
            confidence=0.8,
        )

    monkeypatch.setattr(ar, "list_professional_reports", fake_list_professional_reports)
    monkeypatch.setattr(ar, "analyze_professional_report", fake_analyze_professional_report)
    monkeypatch.setattr(ar, "query_professional_rag", fake_query_professional_rag)

    evidence = asyncio.run(
        ar._collect_professional_research_evidence({"asset_name": "特斯拉"}, report_limit=2)
    )

    assert {item["source_type"] for item in evidence} == {"professional_report_analysis", "professional_rag"}
    assert all(item["symbol"] == "TSLA" for item in evidence)
    assert rag_symbols == ["TSLA"]


def test_options_symbol_scope_only_uses_us_tickers() -> None:
    assert ar._is_us_optionable_symbol("TSLA")
    assert not ar._is_us_optionable_symbol("300750.SZ")


def test_recover_stale_running_tasks_marks_failed(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ar, "DB_PATH", tmp_path / "agent_tasks.sqlite3")
    monkeypatch.setattr(ar, "RUNNING_TASK_STALE_SECONDS", 60)
    terminated: list[tuple[int, str]] = []

    def fake_terminate(pid, kind):
        terminated.append((pid, kind))
        return True

    monkeypatch.setattr(ar, "_terminate_registered_runtime", fake_terminate)

    task = ar.create_investment_task(
        InvestmentTaskCreateRequest(
            title="TradingAgents stale task",
            symbol="00148.HK",
            asset_name="建滔集团",
            engine="tradingagents",
            objective="验证长任务心跳恢复",
        )
    )
    stale_updated_at = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
    with ar._connect() as conn:
        conn.execute(
            """
            UPDATE agent_tasks
            SET status = 'running',
                progress = 85,
                assigned_agent = 'TradingAgents.Runner',
                runtime_pid = 12345,
                runtime_kind = 'tradingagents_external',
                updated_at = ?
            WHERE id = ?
            """,
            (stale_updated_at, task.id),
        )
        conn.commit()

    recovered = ar.recover_stale_running_tasks()
    recovered_task = ar.get_investment_task(task.id)

    assert recovered == 1
    assert recovered_task is not None
    assert recovered_task.status == "failed"
    assert recovered_task.progress == 85
    assert recovered_task.error
    assert "心跳超过 60 秒" in recovered_task.error
    assert "已终止失联的外部运行进程" in recovered_task.error
    assert recovered_task.logs[-1].agent == "TaskCenter"
    assert terminated == [(12345, "tradingagents_external")]
    with ar._connect() as conn:
        row = conn.execute(
            "SELECT runtime_pid, runtime_kind FROM agent_tasks WHERE id = ?",
            (task.id,),
        ).fetchone()
    assert row["runtime_pid"] is None
    assert row["runtime_kind"] is None


def test_unavailable_options_chain_is_still_injected_as_evidence(monkeypatch) -> None:
    async def fake_fetch_options_signals(*args, **kwargs) -> OptionsSignalResponse:
        return OptionsSignalResponse(
            generated_at=datetime.now(timezone.utc),
            horizon_days=45,
            provider="none",
            signals=[
                OptionsSignal(
                    symbol="TSLA",
                    provider="none",
                    provider_name="No free option chain source",
                    source_status="unavailable",
                    fetched_at=datetime.now(timezone.utc),
                    expiration_count=0,
                    contract_count=0,
                    data_quality=0,
                    direction="不可判定",
                    score=50,
                    conviction="低",
                    summary="免费期权链源暂未返回可用合约。",
                    risk_flags=["Nasdaq timeout", "Yahoo 403"],
                    delay_note="未返回可用期权链。",
                )
            ],
            warnings=["Nasdaq public option chain failed for TSLA: ConnectTimeout."],
        )

    monkeypatch.setattr(ar, "fetch_options_signals", fake_fetch_options_signals)

    evidence = asyncio.run(ar._collect_options_signal_evidence({"symbol": "TSLA"}))

    assert evidence
    assert evidence[0]["source_type"] == "options_signal_unavailable"
    assert "Nasdaq" in evidence[0]["text"]


def test_toolchain_evidence_ranks_ahead_of_social_repetition() -> None:
    social = [
        {
            "source": "雪球关键词抓取",
            "source_type": "agent_crawl",
            "source_category": "sentiment",
            "title": f"TSLA 特斯拉 Optimus 社区讨论 {index}",
            "text": "TSLA 特斯拉 Optimus FSD 马斯克 " * 8,
            "credibility_score": 0.46,
        }
        for index in range(8)
    ]
    options = {
        "source": "期权雷达",
        "source_type": "options_signal",
        "source_category": "market",
        "title": "TSLA 期权链信号",
        "text": "Put/Call、隐含波动、关键行权价。",
        "credibility_score": 0.62,
    }

    ranked = ar._rank_relevant_evidence(
        [*social, options],
        symbol="TSLA",
        name="特斯拉",
        objective="分析特斯拉最近买卖点、基本面、研报证据和期权链信号",
    )

    assert ranked[0]["source_type"] == "options_signal"


def test_sec_submission_parser_creates_official_filing_evidence() -> None:
    evidence = ar._sec_filing_evidence_from_submissions(
        symbol="TSLA",
        asset_name="特斯拉",
        cik="1318605",
        limit=2,
        submissions={
            "name": "Tesla, Inc.",
            "filings": {
                "recent": {
                    "form": ["4", "10-Q", "8-K"],
                    "filingDate": ["2026-05-10", "2026-04-24", "2026-04-22"],
                    "reportDate": ["2026-05-08", "2026-03-31", "2026-04-22"],
                    "accessionNumber": [
                        "0000000000-26-000001",
                        "0001628280-26-012345",
                        "0001628280-26-012346",
                    ],
                    "primaryDocument": ["xslF345X05/doc4.xml", "tsla-20260331.htm", "tsla-8k.htm"],
                    "acceptanceDateTime": [
                        "2026-05-10T12:00:00.000Z",
                        "2026-04-24T12:00:00.000Z",
                        "2026-04-22T12:00:00.000Z",
                    ],
                }
            },
        },
    )

    assert len(evidence) == 2
    assert evidence[0]["source"] == "SEC EDGAR"
    assert evidence[0]["source_type"] == "filing"
    assert "10-Q" in evidence[0]["title"]
    assert evidence[0]["url"].endswith("/000162828026012345/tsla-20260331.htm")
