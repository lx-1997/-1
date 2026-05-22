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
