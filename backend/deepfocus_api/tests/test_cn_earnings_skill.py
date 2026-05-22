from datetime import datetime, timezone

import pytest

from deepfocus_api.cn_earnings_skill import (
    diagnose_cn_earnings,
    _dedupe_key,
    _extract_report_detail_from_text,
    _record_from_cninfo_row,
    _record_preference,
    detect_cn_earnings_request,
    format_cn_earnings_skill_response,
)
from deepfocus_api.schemas import (
    CnEarningsRecord,
    CnEarningsDiagnosisRequest,
    CnEarningsScanResponse,
    OrchestratorChatRequest,
)


def test_detect_cn_earnings_request_for_all_a_month():
    request = detect_cn_earnings_request("查看 A 股近一个月所有财报公告")

    assert request is not None
    assert request.market == "A"
    assert request.days == 30
    assert request.limit == 120


def test_detect_cn_earnings_request_for_quarter_limit():
    request = detect_cn_earnings_request("列出全A最近10天一季报前20")

    assert request is not None
    assert request.days == 10
    assert request.report_types == ["q1"]
    assert request.limit == 20


def test_record_from_cninfo_row_normalizes_financial_report():
    record = _record_from_cninfo_row(
        {
            "secCode": "688981",
            "secName": "中芯国际",
            "announcementId": "1225307101",
            "announcementTitle": "中芯国际2026年<em>第一季度报告</em>",
            "announcementTime": 1778774400000,
            "adjunctUrl": "finalpage/2026-05-15/1225307101.PDF",
        }
    )

    assert record is not None
    assert record.symbol == "688981"
    assert record.report_type == "q1"
    assert record.fiscal_year == "2026"
    assert record.fiscal_period == "第一季度"
    assert record.url.endswith("/finalpage/2026-05-15/1225307101.PDF")


def test_visual_annual_report_variant_loses_to_parseable_plain_pdf():
    visual = _record_from_cninfo_row(
        {
            "secCode": "600177",
            "secName": "雅戈尔",
            "announcementId": "1225310476",
            "announcementTitle": "雅戈尔时尚股份有限公司2025年年度报告（图文版）",
            "announcementTime": 1778860800000,
            "adjunctUrl": "finalpage/2026-05-16/1225310476.PDF",
            "adjunctSize": 32271,
        }
    )
    plain = _record_from_cninfo_row(
        {
            "secCode": "600177",
            "secName": "雅戈尔",
            "announcementId": "1225220816",
            "announcementTitle": "雅戈尔时尚股份有限公司2025年年度报告",
            "announcementTime": 1777305600000,
            "adjunctUrl": "finalpage/2026-04-28/1225220816.PDF",
            "adjunctSize": 1631,
        }
    )

    assert visual is not None
    assert plain is not None
    assert _dedupe_key(visual) == _dedupe_key(plain)
    assert _record_preference(plain) > _record_preference(visual)


def test_record_from_cninfo_row_skips_correction_notice_without_report_body():
    record = _record_from_cninfo_row(
        {
            "secCode": "600038",
            "secName": "中直股份",
            "announcementId": "1225309463",
            "announcementTitle": "中航直升机股份有限公司关于2025年年度报告的更正公告",
            "announcementTime": 1778860800000,
            "adjunctUrl": "finalpage/2026-05-16/1225309463.PDF",
        }
    )

    assert record is None


def test_pdf_text_detail_extraction_for_quarter_report():
    record = CnEarningsRecord(
        symbol="688981",
        name="中芯国际",
        announcement_date="2026-05-15",
        report_type="q1",
        fiscal_year="2026",
        fiscal_period="第一季度",
        title="中芯国际2026年第一季度报告",
        url="https://static.cninfo.com.cn/finalpage/2026-05-15/1225307101.PDF",
        announcement_id="1225307101",
    )
    detail = _extract_report_detail_from_text(
        """
        主要会计数据和财务指标
        营业收入 17,617,218 16,301,085 8.1
        归属于上市公司股东的净利润 1,361,209 1,356,374 0.4
        归属于上市公司股东的扣除非经常性损益的净利润 1,232,279 1,169,998 5.3
        经营活动产生的现金流量净额 5,131,729 -1,171,520 不适用
        基本每股收益（元/股） 0.17 0.17 -
        加权平均净资产收益率（%） 0.9 0.9 -
        毛利率 为20.1%
        总资产 380,545,855 367,718,196 3.5
        """,
        record,
    )

    assert detail["revenue"] == "17,617,218"
    assert detail["revenue_yoy"] == "8.1%"
    assert detail["net_profit"] == "1,361,209"
    assert detail["net_profit_yoy"] == "0.4%"
    assert detail["deducted_net_profit"] == "1,232,279"
    assert detail["operating_cash_flow"] == "5,131,729"
    assert detail["eps"] == "0.17"
    assert detail["roe"] == "0.9"
    assert detail["gross_margin"] == "20.1%"
    assert detail["detail_quality"] == "full"


def test_pdf_text_detail_extraction_handles_parenthesized_units():
    record = CnEarningsRecord(
        symbol="001395",
        name="亚联机械",
        announcement_date="2026-05-16",
        report_type="correction",
        fiscal_year="2025",
        fiscal_period="更正/修正",
        title="2025年年度报告（更正后）",
        url="https://static.cninfo.com.cn/finalpage/2026-05-16/1225309665.PDF",
    )
    detail = _extract_report_detail_from_text(
        """
        主要会计数据和财务指标
        2025年 2024年 本年比上年增减 2023年
        营业收入（元） 800,670,782.85 864,841,087.37 -7.42% 647,061,705.78
        归属于上市公司股 东的净利润（元） 187,028,414.27 154,652,488.37 20.93% 103,341,000.00
        经营活动产生的现金流量净额（元） 96,398,721.10 80,118,002.00 20.32% 61,000,000.00
        基本每股收益（元/股） 2.19 1.88 16.49% 1.30
        加权平均净资产收益率（%） 16.60 14.20 增加2.40个百分点 11.10
        """,
        record,
    )

    assert detail["revenue"] == "800,670,782.85"
    assert detail["revenue_yoy"] == "-7.42%"
    assert detail["net_profit"] == "187,028,414.27"
    assert detail["net_profit_yoy"] == "20.93%"
    assert detail["operating_cash_flow"] == "96,398,721.10"
    assert detail["detail_quality"] == "full"


def test_formatter_surfaces_financial_metrics_inline():
    response = CnEarningsScanResponse(
        generated_at=datetime.now(timezone.utc),
        start_date="2026-05-11",
        end_date="2026-05-17",
        total_found=1,
        returned_count=1,
        detail_attempted_count=1,
        detail_success_count=1,
        summary="命中 1 条 A 股财报公告。",
        records=[
            CnEarningsRecord(
                symbol="688981",
                name="中芯国际",
                announcement_date="2026-05-15",
                report_type="q1",
                fiscal_year="2026",
                fiscal_period="第一季度",
                title="中芯国际2026年第一季度报告",
                url="https://static.cninfo.com.cn/finalpage/2026-05-15/1225307101.PDF",
                revenue="17,617,218",
                revenue_yoy="8.1%",
                net_profit="1,361,209",
                net_profit_yoy="0.4%",
                eps="0.17",
                roe="0.9",
                operating_cash_flow="5,131,729",
                detail_source="pdf",
                detail_quality="full",
            )
        ],
    )

    content = format_cn_earnings_skill_response(response)

    assert "财报明细（前 1 条，已解析前 1 条 PDF 正文，1 条抽取到可用明细。）" in content
    assert "营收/同比：17,617,218 / 8.1%" in content
    assert "归母净利/同比：1,361,209 / 0.4%" in content
    assert "EPS：0.17；ROE：0.9" in content


@pytest.mark.asyncio
async def test_orchestrator_semantic_route_invokes_cn_earnings_skill(monkeypatch):
    from deepfocus_api import main

    async def fake_scan(skill_request):
        assert skill_request.market == "A"
        return CnEarningsScanResponse(
            generated_at=datetime.now(timezone.utc),
            start_date="2026-05-11",
            end_date="2026-05-17",
            total_found=1,
            returned_count=1,
            summary="命中 1 条 A 股财报公告。",
            records=[
                CnEarningsRecord(
                    symbol="688981",
                    name="中芯国际",
                    announcement_date="2026-05-15",
                    report_type="q1",
                    fiscal_year="2026",
                    fiscal_period="第一季度",
                    title="中芯国际2026年第一季度报告",
                    url="https://static.cninfo.com.cn/finalpage/2026-05-15/1225307101.PDF",
                )
            ],
        )

    monkeypatch.setattr(main, "scan_cn_earnings", fake_scan)
    response = await main._maybe_cn_earnings_skill_chat(
        OrchestratorChatRequest(message="查看 A 股近一周所有财报公告")
    )

    assert response is not None
    assert response.handled_inline is True
    assert response.should_create_task is False
    assert "cn.earnings.scan" in response.chips
    assert "A股财报" in response.chips
    assert response.reasoning_trace[1].title == "cn.earnings.scan"


@pytest.mark.asyncio
async def test_cn_earnings_diagnosis_returns_agent_steps_in_mock_mode():
    class MockLLM:
        provider = "mock"
        provider_name = "mock"
        model = "mock-research-analyst"

    response = await diagnose_cn_earnings(
        CnEarningsDiagnosisRequest(
            record=CnEarningsRecord(
                symbol="600177",
                name="雅戈尔",
                announcement_date="2026-04-28",
                report_type="annual",
                fiscal_year="2025",
                fiscal_period="年度",
                title="雅戈尔时尚股份有限公司2025年年度报告",
                url="https://static.cninfo.com.cn/finalpage/2026-04-28/1225220816.PDF",
                revenue="11,581,812,280.44",
                net_profit="2,447,339,231.76",
                operating_cash_flow="1,000,000,000.00",
                detail_source="pdf",
                detail_quality="full",
            )
        ),
        MockLLM(),
    )

    assert response.skill == "cn.earnings.diagnose"
    assert response.symbol == "600177"
    assert response.agent_steps
    assert {step.agent for step in response.agent_steps} >= {"EarningsReviewer", "QualityAgent"}
