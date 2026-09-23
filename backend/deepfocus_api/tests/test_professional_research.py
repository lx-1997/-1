from __future__ import annotations

from pathlib import Path

import pytest

from deepfocus_api import professional_research as pr
from deepfocus_api.schemas import (
    ProfessionalEvalRunRequest,
    ProfessionalRagQueryRequest,
    ProfessionalReportAnalysisRequest,
)


SAMPLE_REPORT = """
[Page 1]
2025年年度报告
公司实现营业收入 123.45 亿元，同比增长 18.6%；归母净利润 12.30 亿元，同比增长 22.1%。扣非净利润 10.80 亿元。
毛利率 36.5%，加权平均净资产收益率 ROE 14.2%。

[Page 2]
经营活动产生的现金流量净额 8.20 亿元，资本开支 3.10 亿元。
主要风险：应收账款增加，若下游需求放缓，公司现金流可能承压。
"""


@pytest.fixture(autouse=True)
def isolated_professional_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(pr, "DB_PATH", tmp_path / "professional.sqlite3")
    pr.init_professional_research_db()


def _ingest_sample():
    return pr.ingest_professional_report_text(
        text=SAMPLE_REPORT,
        title="测试公司2025年年报",
        symbol="TEST",
        report_type="annual",
    )


def test_ingest_report_extracts_structured_metrics():
    report = _ingest_sample()

    metrics = {metric.metric_key: metric for metric in pr.list_professional_metrics(report_id=report.id)}

    assert report.metrics_count >= 8
    assert report.chunks_count == 2
    assert report.period == "2025年度"
    assert metrics["revenue"].raw_value == "123.45 亿元"
    assert metrics["net_profit"].raw_value == "12.30 亿元"
    assert metrics["adjusted_net_profit"].raw_value == "10.80 亿元"
    assert metrics["net_profit_yoy"].raw_value == "同比增长 22.1%"
    assert metrics["capex"].raw_value == "3.10 亿元"


def test_period_aliases_are_compared_without_cross_period_matches():
    assert pr._period_matches("2025年度", "2025年报")
    assert pr._period_matches("2025年度", "FY25")
    assert pr._period_matches("2025年第一季度", "2025Q1")
    assert pr._period_matches("2025年第一季度", "2025 1Q")
    assert pr._period_matches("2025年半年度", "2025H1")
    assert pr._period_matches("2025年前三季度", "2025年1-9月")
    assert pr._period_matches("2025年前三季度", "2025 9M")
    assert pr._period_matches("2025年度", "2025")
    assert not pr._period_matches("2024年度", "2025年度")
    assert not pr._period_matches("2025年半年度", "2025年度")
    assert not pr._period_matches("2025年前三季度", "2025Q3")
    assert not pr._period_matches("2024年度", "FY25")


def test_short_fy_alias_is_not_dropped_by_sql_prefilter():
    with pr._connect() as conn:
        conn.execute(
            """
            INSERT INTO professional_financial_metrics (
                id, report_id, symbol, period, metric_key, metric_label, value,
                normalized_value, unit, raw_value, source_page, source_excerpt,
                confidence, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "metric-fy25",
                "report-fy25",
                "TEST",
                "FY25",
                "revenue",
                "营业收入",
                100.0,
                100.0,
                "亿元",
                "100 亿元",
                1,
                "FY25 revenue",
                1.0,
                "{}",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        conn.commit()

    metrics = pr.list_professional_metrics(period="FY25", limit=5)

    assert [metric.period for metric in metrics] == ["FY25"]


def test_period_marker_filter_keeps_requested_slice_beyond_default_window():
    """Localized period aliases must be filtered after, not before, the SQL window."""
    with pr._connect() as conn:
        for index in range(120):
            conn.execute(
                """
                INSERT INTO professional_financial_metrics (
                    id, report_id, symbol, period, metric_key, metric_label, value,
                    normalized_value, unit, raw_value, source_page, source_excerpt,
                    confidence, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"metric-{index}",
                    "report-period-window",
                    "TEST",
                    "2025Q1" if index == 0 else "2025Q2",
                    "revenue",
                    "营业收入",
                    float(index),
                    float(index),
                    "亿元",
                    f"{index} 亿元",
                    1,
                    f"2025 quarter metric {index}",
                    1.0,
                    "{}",
                    f"2026-01-{index // 24 + 1:02d}T{index % 24:02d}:00:00+00:00",
                ),
            )
        conn.commit()

    metrics = pr.list_professional_metrics(period="2025Q1", limit=5)

    assert len(metrics) == 1
    assert metrics[0].period == "2025Q1"


@pytest.mark.asyncio
async def test_period_constrained_rag_does_not_mix_same_symbol_reports():
    annual_2024 = pr.ingest_professional_report_text(
        text=(
            "[Page 1]\n2024年度\n营业收入 80 亿元。\n"
            "主要风险：2024 年需求放缓，现金流承压。"
        ),
        title="测试公司2024年年报",
        symbol="TEST",
        report_type="annual",
        period="2024年度",
    )
    annual_2025 = pr.ingest_professional_report_text(
        text=(
            "[Page 1]\n2025年度\n营业收入 100 亿元。\n"
            "主要风险：2025 年海外客户集中度较高。"
        ),
        title="测试公司2025年年报",
        symbol="TEST",
        report_type="annual",
        period="2025年度",
    )

    result = await pr.query_professional_rag(
        ProfessionalRagQueryRequest(
            question="营业收入是多少？",
            symbol="TEST",
            period="2025年报",
            top_k=8,
            use_cloud_model=False,
        )
    )

    assert result.citations
    assert {citation.report_id for citation in result.citations} == {annual_2025.id}
    assert "100 亿元" in result.answer
    assert "80 亿元" not in result.answer
    assert annual_2024.id not in {citation.report_id for citation in result.citations}

    risk_result = await pr.query_professional_rag(
        ProfessionalRagQueryRequest(
            question="主要风险是什么？",
            symbol="TEST",
            period="2025年报",
            top_k=8,
            use_cloud_model=False,
        )
    )
    assert risk_result.citations
    assert {citation.report_id for citation in risk_result.citations} == {annual_2025.id}
    assert any("2025 年海外客户集中度较高" in citation.text for citation in risk_result.citations)
    assert all("2024 年需求放缓" not in citation.text for citation in risk_result.citations)


@pytest.mark.asyncio
async def test_cited_rag_answers_with_sources_and_refuses_missing_facts():
    report = _ingest_sample()

    answer = await pr.query_professional_rag(
        ProfessionalRagQueryRequest(
            report_id=report.id,
            question="这份报告披露的营业收入是多少？",
            use_cloud_model=False,
        )
    )
    assert "123.45 亿元" in answer.answer
    assert "[M" in answer.answer
    assert answer.citations

    missing = await pr.query_professional_rag(
        ProfessionalRagQueryRequest(
            report_id=report.id,
            question="这份报告披露的董事会秘书联系电话是多少？",
            use_cloud_model=False,
        )
    )
    assert "不知道" in missing.answer
    assert not missing.citations


@pytest.mark.asyncio
async def test_report_analysis_and_eval_suite_are_reproducible():
    report = _ingest_sample()

    analysis = await pr.analyze_professional_report(
        report.id,
        ProfessionalReportAnalysisRequest(use_cloud_model=False),
    )
    assert analysis.key_metrics
    assert analysis.citations
    assert "解析出" in analysis.summary

    eval_run = await pr.run_professional_eval(ProfessionalEvalRunRequest(report_id=report.id))
    assert eval_run.total >= 5
    assert eval_run.pass_rate == 1.0
    assert eval_run.citation_rate >= 0.8
