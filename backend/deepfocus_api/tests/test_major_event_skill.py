from datetime import datetime, timezone

import pytest

from deepfocus_api.major_event_skill import (
    _extract_event_detail_from_text,
    _record_from_cninfo_row,
    detect_major_event_request,
    format_major_event_skill_response,
)
from deepfocus_api.schemas import (
    MajorEventRecord,
    MajorEventScanResponse,
    OrchestratorChatRequest,
)


def test_detect_major_event_request_for_a_share_week():
    request = detect_major_event_request("查看 A 股近一周重大事项风险预警")

    assert request is not None
    assert request.market == "A"
    assert request.days == 7
    assert request.limit == 120


def test_detect_major_event_request_for_buyback_limit():
    request = detect_major_event_request("列出全A最近10天回购公告前20")

    assert request is not None
    assert request.days == 10
    assert request.event_types == ["buyback"]
    assert request.limit == 20


def test_record_from_cninfo_row_normalizes_regulatory_event():
    record = _record_from_cninfo_row(
        {
            "secCode": "300001",
            "secName": "特锐德",
            "announcementId": "1225310000",
            "announcementTitle": "青岛特锐德电气股份有限公司关于收到<em>行政处罚</em>事先告知书的公告",
            "announcementTime": 1778860800000,
            "adjunctUrl": "finalpage/2026-05-16/1225310000.PDF",
            "pageColumn": "SZZB",
        }
    )

    assert record is not None
    assert record.symbol == "300001"
    assert record.event_type == "regulatory_penalty"
    assert record.status == "risk"
    assert record.risk_level == "high"
    assert record.impact == "negative"
    assert record.url.endswith("/finalpage/2026-05-16/1225310000.PDF")


def test_record_from_cninfo_row_normalizes_buyback_event():
    record = _record_from_cninfo_row(
        {
            "secCode": "000001",
            "secName": "平安银行",
            "announcementId": "1225310001",
            "announcementTitle": "关于以集中竞价交易方式<em>回购股份</em>方案的公告",
            "announcementTime": 1778860800000,
            "adjunctUrl": "finalpage/2026-05-16/1225310001.PDF",
        }
    )

    assert record is not None
    assert record.event_type == "buyback"
    assert record.impact == "positive"
    assert record.risk_level == "low"


def test_pdf_text_detail_extraction_for_restructuring():
    record = MajorEventRecord(
        symbol="600000",
        name="浦发银行",
        announcement_date="2026-05-16",
        event_type="restructuring",
        status="new",
        title="关于筹划发行股份购买资产暨重大资产重组的提示性公告",
        url="https://static.cninfo.com.cn/finalpage/2026-05-16/1225310999.PDF",
        announcement_id="1225310999",
        risk_level="medium",
    )
    detail = _extract_event_detail_from_text(
        """
        重要内容提示：公司拟通过发行股份购买资产方式收购深圳某科技有限公司51%股权。
        交易标的：深圳某科技有限公司51%股权；交易金额：人民币12.5亿元。
        股权比例：51%。
        公司已签署股权转让协议，本次交易尚需提交股东大会审议，存在不确定性。
        实施期限：自股东大会审议通过之日起12个月内。
        风险提示：本次交易尚需交易所审核和监管批复，能否实施存在不确定性。
        """,
        record,
    )

    assert detail["subject"] == "深圳某科技有限公司51%股权"
    assert detail["amount"] == "人民币12.5亿元"
    assert detail["share_ratio"] == "51%"
    assert "签署股权转让协议" in detail["progress"]
    assert detail["deadline"] == "自股东大会审议通过之日起12个月内"
    assert "不确定性风险" in detail["risk_flags"]
    assert detail["detail_quality"] == "full"


def test_formatter_surfaces_major_event_fields_inline():
    response = MajorEventScanResponse(
        generated_at=datetime.now(timezone.utc),
        start_date="2026-05-11",
        end_date="2026-05-17",
        total_found=1,
        returned_count=1,
        detail_attempted_count=1,
        detail_success_count=1,
        summary="命中 1 条 A 股重大事项公告。",
        records=[
            MajorEventRecord(
                symbol="600000",
                name="浦发银行",
                announcement_date="2026-05-16",
                event_type="restructuring",
                status="new",
                impact="mixed",
                risk_level="medium",
                title="关于筹划发行股份购买资产暨重大资产重组的提示性公告",
                url="https://static.cninfo.com.cn/finalpage/2026-05-16/1225310999.PDF",
                subject="深圳某科技有限公司51%股权",
                amount="人民币12.5亿元",
                share_ratio="51%",
                progress="公司已签署股权转让协议",
                deadline="自股东大会审议通过之日起12个月内",
                risk_flags=["不确定性风险"],
                detail_source="pdf",
                detail_quality="full",
            )
        ],
    )

    content = format_major_event_skill_response(response)

    assert "重大事项（前 1 条，已解析前 1 条 PDF 正文，1 条抽取到可用明细。）" in content
    assert "并购重组/新披露" in content
    assert "主体：深圳某科技有限公司51%股权" in content
    assert "金额/比例：人民币12.5亿元 / 51%" in content


@pytest.mark.asyncio
async def test_orchestrator_semantic_route_invokes_major_event_skill(monkeypatch):
    from deepfocus_api import main

    async def fake_scan(skill_request):
        assert skill_request.market == "A"
        return MajorEventScanResponse(
            generated_at=datetime.now(timezone.utc),
            start_date="2026-05-11",
            end_date="2026-05-17",
            total_found=1,
            returned_count=1,
            summary="命中 1 条 A 股重大事项公告。",
            records=[
                MajorEventRecord(
                    symbol="300001",
                    name="特锐德",
                    announcement_date="2026-05-16",
                    event_type="regulatory_penalty",
                    status="risk",
                    impact="negative",
                    risk_level="high",
                    title="关于收到行政处罚事先告知书的公告",
                    url="https://static.cninfo.com.cn/finalpage/2026-05-16/1225310000.PDF",
                )
            ],
        )

    monkeypatch.setattr(main, "scan_major_events", fake_scan)
    response = await main._maybe_major_event_skill_chat(
        OrchestratorChatRequest(message="查看 A 股近一周重大事项风险预警")
    )

    assert response is not None
    assert response.handled_inline is True
    assert response.should_create_task is False
    assert "cn.major_event.scan" in response.chips
    assert "A股重大事项" in response.chips
    assert response.reasoning_trace[1].title == "cn.major_event.scan"
