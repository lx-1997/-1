from datetime import datetime, timezone

import pytest
import httpx

from deepfocus_api.schemas import (
    OrchestratorChatRequest,
    ShareholderChangeInterpretRequest,
    ShareholderChangeRecord,
    ShareholderChangeScanRequest,
    ShareholderChangeScanResponse,
)
from deepfocus_api.shareholder_change_skill import (
    _apply_hk_localized_name,
    _collapse_hk_duplicate_disclosure_chains,
    _default_record_sort_key,
    _effective_us_form4_limit,
    _extract_change_detail_from_text,
    _format_hk_reason_method,
    _record_from_hkex_summary_row,
    _record_from_cninfo_row,
    _sec_retry_delay,
    _shareholder_hint,
    _title_holder_names,
    detect_shareholder_change_request,
    format_shareholder_change_skill_response,
    interpret_shareholder_change,
)


def test_detect_shareholder_change_request_for_all_a_week():
    request = detect_shareholder_change_request("查看 A 股近一周所有股东增减持信息")

    assert request is not None
    assert request.market == "A"
    assert request.days == 7
    assert request.direction == "all"


def test_detect_shareholder_change_request_for_decrease_days_limit():
    request = detect_shareholder_change_request("列出全A最近10天减持公告前20")

    assert request is not None
    assert request.days == 10
    assert request.direction == "decrease"
    assert request.limit == 20


def test_detect_shareholder_change_request_for_hk_market():
    request = detect_shareholder_change_request("查看港股近一周所有股东增减持")

    assert request is not None
    assert request.market == "HK"
    assert request.days == 7


def test_detect_shareholder_change_request_for_us_form4():
    request = detect_shareholder_change_request("扫描美股最近10天 Form 4 内部人交易前20")

    assert request is not None
    assert request.market == "US"
    assert request.days == 10
    assert request.limit == 20


def test_us_form4_effective_limit_tracks_selected_limit_with_cap():
    assert _effective_us_form4_limit(
        ShareholderChangeScanRequest(market="US", limit=120, detail_limit=120)
    ) == 120
    assert _effective_us_form4_limit(
        ShareholderChangeScanRequest(market="US", limit=200, detail_limit=200)
    ) == 120
    assert _effective_us_form4_limit(
        ShareholderChangeScanRequest(market="US", limit=40, detail_limit=12)
    ) == 12


def test_sec_retry_delay_uses_retry_after_with_bounds():
    request = httpx.Request("GET", "https://www.sec.gov/cgi-bin/browse-edgar")

    assert _sec_retry_delay(httpx.Response(429, headers={"Retry-After": "7"}, request=request), 2.0) == 7.0
    assert _sec_retry_delay(httpx.Response(429, headers={"Retry-After": "90"}, request=request), 2.0) == 30.0
    assert _sec_retry_delay(httpx.Response(429, headers={"Retry-After": "soon"}, request=request), 2.0) == 2.0


@pytest.mark.asyncio
async def test_shareholder_interpretation_calls_llm_when_configured():
    class FakeLLM:
        provider = "openai-compatible"
        provider_name = "test-provider"
        model = "test-model"

        async def complete_json(self, prompt, **kwargs):
            assert "股东增减持披露解读 Agent" in prompt
            return {
                "tone": "watch",
                "verdict": "计划需看落地",
                "summary": "增持计划偏正面，但还需要确认实际成交金额。",
                "points": ["控股股东相关主体参与", "当前仍是计划阶段"],
                "risks": ["计划不等于成交"],
                "questions": ["资金来源是否清楚？"],
                "actions": ["跟踪完成公告"],
                "evidence": ["测试公告"],
                "confidence": 0.73,
            }

    record = ShareholderChangeRecord(
        symbol="600000",
        name="浦发银行",
        announcement_date="2026-05-21",
        direction="increase",
        status="plan",
        shareholder_type="控股股东",
        title="关于控股股东增持计划的公告",
        url="https://example.com/report.pdf",
        risk_level="medium",
    )

    result = await interpret_shareholder_change(
        ShareholderChangeInterpretRequest(record=record),
        FakeLLM(),
    )

    assert result.provider == "test-provider"
    assert result.model == "test-model"
    assert result.tone == "watch"
    assert result.verdict == "计划需看落地"
    assert result.confidence == 0.73


@pytest.mark.asyncio
async def test_shareholder_interpretation_mock_is_labeled_local_rules():
    class MockLLM:
        provider = "mock"
        provider_name = "mock"
        model = "mock-model"

    record = ShareholderChangeRecord(
        symbol="00001",
        name="长和",
        announcement_date="2026-05-21",
        direction="decrease",
        status="completed",
        shareholder_type="主要股东",
        shareholder_hint="BlackRock, Inc.",
        title="BlackRock, Inc. 减持 长和 (00001)",
        url="https://di.hkex.com.hk/di/example",
        risk_level="high",
    )

    result = await interpret_shareholder_change(
        ShareholderChangeInterpretRequest(record=record),
        MockLLM(),
    )

    assert result.provider == "mock"
    assert result.tone == "risk"
    assert any("规则兜底结果" in item for item in result.risks)


def test_record_from_cninfo_row_normalizes_announcement():
    record = _record_from_cninfo_row(
        {
            "secCode": "002484",
            "secName": "江海股份",
            "announcementId": "1225310831",
            "announcementTitle": "南通江海电容器股份有限公司关于部分董事、高级管理人员<em>减持</em>股份预披露的公告",
            "announcementTime": 1778860800000,
            "adjunctUrl": "finalpage/2026-05-16/1225310831.PDF",
            "pageColumn": "SZZB",
        }
    )

    assert record is not None
    assert record.symbol == "002484"
    assert record.direction == "decrease"
    assert record.status == "plan"
    assert record.shareholder_type == "董监高"
    assert record.risk_level == "high"
    assert record.url.endswith("/finalpage/2026-05-16/1225310831.PDF")


def test_completed_status_wins_over_plan_wording():
    record = _record_from_cninfo_row(
        {
            "secCode": "688757",
            "secName": "胜科纳米",
            "announcementId": "1225309242",
            "announcementTitle": "关于股东减持计划实施完毕暨减持股份结果公告",
            "announcementTime": 1778860800000,
            "adjunctUrl": "finalpage/2026-05-16/1225309242.PDF",
        }
    )

    assert record is not None
    assert record.status == "completed"


def test_default_sort_matches_frontend_first_screen_order():
    early_symbol = ShareholderChangeRecord(
        symbol="002005",
        name="ST德豪",
        announcement_date="2026-05-16",
        direction="decrease",
        status="plan",
        shareholder_type="股东",
        title="关于公司大股东减持股份的预披露公告",
        url="https://static.cninfo.com.cn/finalpage/2026-05-16/1225310000.PDF",
    )
    late_symbol = ShareholderChangeRecord(
        symbol="688757",
        name="胜科纳米",
        announcement_date="2026-05-16",
        direction="decrease",
        status="completed",
        shareholder_type="股东",
        title="关于股东减持计划实施完毕暨减持股份结果公告",
        url="https://static.cninfo.com.cn/finalpage/2026-05-16/1225309242.PDF",
    )
    older_record = ShareholderChangeRecord(
        symbol="000001",
        name="平安银行",
        announcement_date="2026-05-15",
        direction="increase",
        status="other",
        shareholder_type="股东",
        title="关于股东增持股份的公告",
        url="https://static.cninfo.com.cn/finalpage/2026-05-15/1225300000.PDF",
    )

    records = sorted([late_symbol, older_record, early_symbol], key=_default_record_sort_key)

    assert [record.symbol for record in records] == ["002005", "688757", "000001"]


def test_hk_localized_name_updates_company_surfaces():
    record = ShareholderChangeRecord(
        symbol="00001",
        name="CK Hutchison Holdings Ltd.",
        announcement_date="2026-05-20",
        direction="increase",
        status="completed",
        shareholder_type="主要股东",
        shareholder_hint="BlackRock, Inc.",
        title="BlackRock, Inc. 增持 CK Hutchison Holdings Ltd. (00001)",
        url="https://di.hkex.com.hk/di/example",
        source="hkex-di",
        source_name="HKEX Disclosure of Interests",
        detail_summary="披露主体：BlackRock, Inc.；CK Hutchison Holdings Ltd.；股数：100",
        evidence_excerpt="BlackRock, Inc. · CK Hutchison Holdings Ltd. · 100",
    )

    _apply_hk_localized_name(record, "长和")

    assert record.name == "长和"
    assert "长和" in record.title
    assert "CK Hutchison" not in record.title
    assert record.metadata["issuer_name_en"] == "CK Hutchison Holdings Ltd."
    assert record.metadata["issuer_name_zh"] == "长和"


def test_hk_duplicate_disclosure_chain_collapses_to_one_record():
    base_kwargs = dict(
        symbol="00460",
        name="Sihuan Pharmaceutical Holdings Group Ltd.",
        announcement_date="2026-05-19",
        direction="increase",
        status="completed",
        url="https://di.hkex.com.hk/di/example",
        source="hkex-di",
        source_name="HKEX Disclosure of Interests",
        risk_level="low",
        change_shares="662,890,000",
        change_ratio="56.62% → 57.10%",
        price_range="",
        change_period="2026-05-16",
        change_method="HKEX DI reason 1113 (L)",
        holding_before="56.62%",
        holding_after="57.10%",
        detail_source="html",
        detail_quality="partial",
        metadata={"reason_code": "1113 (L)", "summary_type": "C1"},
    )
    records = [
        ShareholderChangeRecord(
            **base_kwargs,
            shareholder_type="主要股东",
            shareholder_hint="Network Victory Limited",
            shareholder_names=["Network Victory Limited"],
            announcement_id="serial-1",
            title="Network Victory Limited 增持 Sihuan Pharmaceutical Holdings Group Ltd. (00460)",
        ),
        ShareholderChangeRecord(
            **base_kwargs,
            shareholder_type="主要股东",
            shareholder_hint="Proper Process International Limited",
            shareholder_names=["Proper Process International Limited"],
            announcement_id="serial-2",
            title="Proper Process International Limited 增持 Sihuan Pharmaceutical Holdings Group Ltd. (00460)",
        ),
        ShareholderChangeRecord(
            **{**base_kwargs, "metadata": {"reason_code": "1113 (L)", "summary_type": "C2"}},
            shareholder_type="董事/高管",
            shareholder_hint="Che Fengsheng",
            shareholder_names=["Che Fengsheng"],
            announcement_id="serial-3",
            title="Che Fengsheng 增持 Sihuan Pharmaceutical Holdings Group Ltd. (00460)",
        ),
    ]

    collapsed = _collapse_hk_duplicate_disclosure_chains(records)

    assert len(collapsed) == 1
    assert collapsed[0].shareholder_names == [
        "Network Victory Limited",
        "Proper Process International Limited",
        "Che Fengsheng",
    ]
    assert collapsed[0].shareholder_type == "主要股东 / 董事 / 高管"
    assert collapsed[0].metadata["collapsed_disclosure_count"] == 3
    assert collapsed[0].metadata["collapsed_summary_types"] == ["C1", "C2"]
    assert "合并 3 条 HKEX DI 链式披露" in collapsed[0].detail_summary


def test_hk_duplicate_disclosure_chain_collapses_even_when_trade_details_differ():
    base_kwargs = dict(
        symbol="00460",
        name="Sihuan Pharmaceutical Holdings Group Ltd.",
        announcement_date="2026-05-19",
        direction="increase",
        status="completed",
        shareholder_type="主要股东",
        url="https://di.hkex.com.hk/di/example",
        source="hkex-di",
        source_name="HKEX Disclosure of Interests",
        risk_level="low",
        change_ratio="56.62% → 57.10%",
        change_period="2026-05-16",
        change_method="取得/增持权益 · 好仓 · 代码 1113",
        holding_before="5,662,000,000 股 · 56.62% · 好仓",
        holding_after="5,710,000,000 股 · 57.10% · 好仓",
        detail_source="html",
        detail_quality="partial",
        metadata={"reason_code": "1113", "summary_type": "C1"},
    )
    records = [
        ShareholderChangeRecord(
            **base_kwargs,
            shareholder_hint="Mingyao Capital Limited",
            shareholder_names=["Mingyao Capital Limited"],
            announcement_id="serial-1",
            title="Mingyao Capital Limited 增持 Sihuan Pharmaceutical Holdings Group Ltd. (00460)",
            change_shares="48,000,000 股",
            price_range="HKD 1.30",
        ),
        ShareholderChangeRecord(
            **base_kwargs,
            shareholder_hint="Proper Process International Limited",
            shareholder_names=["Proper Process International Limited"],
            announcement_id="serial-2",
            title="Proper Process International Limited 增持 Sihuan Pharmaceutical Holdings Group Ltd. (00460)",
            change_shares="52,000,000 股",
            price_range="HKD 1.31",
        ),
    ]

    collapsed = _collapse_hk_duplicate_disclosure_chains(records)

    assert len(collapsed) == 1
    assert collapsed[0].metadata["collapsed_disclosure_count"] == 2
    assert collapsed[0].shareholder_hint == "Mingyao Capital Limited、Proper Process International Limited"


def test_hk_summary_row_formats_disclosure_fields_for_display():
    cells = [
        '<a href="/di/example">17</a>',
        "BaiDe International Holdings Ltd.",
        "02668",
        "Ordinary Shares",
        "Han Weining",
        "28/04/2026",
        "1004 (L)<br>",
        "433,130,000(L)<br>",
        "HKD 0.0716",
        "0(L)",
        "0.00",
        "433,130,000(L)",
        "7.69",
    ]
    row_html = '<TR bgColor="#ffffff">' + "".join(f'<TD class="txt">{cell}</TD>' for cell in cells) + "</TR>"

    record = _record_from_hkex_summary_row(
        row_html=row_html,
        summary_date=datetime(2026, 5, 20).date(),
        summary_type="C1",
        shareholder_type="主要股东",
    )

    assert record is not None
    assert record.change_shares == "433,130,000 股"
    assert record.change_ratio == "0.00% → 7.69%"
    assert record.price_range == "HKD 0.0716"
    assert record.change_amount == "约 HKD 0.31 亿"
    assert record.change_method == "增持权益 · 好仓 · 代码 1004"
    assert "433,130,000(L)" not in record.detail_summary
    assert "|" not in record.evidence_excerpt
    assert record.metadata["reason_code"] == "1004"
    assert record.metadata["share_position"] == "好仓"


def test_hk_summary_row_uses_first_share_number_for_estimated_amount():
    cells = [
        '<a href="/di/example">18</a>',
        "Example Holdings Ltd.",
        "00999",
        "Ordinary Shares",
        "Example Holder",
        "28/04/2026",
        "1101 (L)<br>",
        "44,500,000(L) | 44,500,000(P)",
        "HKD 44.5000",
        "2,000,000(L)",
        "56.62",
        "46,500,000(L)",
        "57.10",
    ]
    row_html = '<TR bgColor="#ffffff">' + "".join(f'<TD class="txt">{cell}</TD>' for cell in cells) + "</TR>"

    record = _record_from_hkex_summary_row(
        row_html=row_html,
        summary_date=datetime(2026, 5, 20).date(),
        summary_type="C1",
        shareholder_type="主要股东",
    )

    assert record is not None
    assert record.change_shares == "44,500,000 股"
    assert record.change_amount == "约 HKD 19.80 亿"


def test_hk_reason_method_formats_common_increase_and_decrease_codes_for_display():
    assert (
        _format_hk_reason_method(reason_code="1113", direction="increase", share_position_label="好仓")
        == "取得/增持权益 · 好仓 · 代码 1113"
    )
    assert (
        _format_hk_reason_method(reason_code="1213", direction="decrease", share_position_label="淡仓")
        == "处置/减持权益 · 淡仓 · 代码 1213"
    )


def test_title_holder_names_ignore_generic_notice_wording():
    names = _title_holder_names(
        "正弦电气关于董事、高级管理人员增持公司股份计划实施完成暨增持结果公告",
        issuer_name="正弦电气",
    )

    assert names == []


def test_title_holder_names_ignore_issuer_name_as_holder():
    names = _title_holder_names("禾丰股份股东减持股份计划公告", issuer_name="禾丰股份")

    assert names == []


def test_title_holder_names_keep_institution_holder():
    names = _title_holder_names("关于国家集成电路产业投资基金股份有限公司减持股份计划的公告", issuer_name="盛科通信")

    assert names == ["国家集成电路产业投资基金股份有限公司"]


def test_title_holder_names_ignore_generic_title_fragments():
    assert _title_holder_names("关于拟增持雪峰科技股票的公告", issuer_name="广东宏大") == []
    assert (
        _title_holder_names("南通江海电容器股份有限公司关于部分董事、高级管理人员减持股份预披露的公告", issuer_name="江海股份")
        == []
    )
    assert _shareholder_hint("关于拟增持雪峰科技股票的公告", issuer_name="广东宏大") == ""
    assert _shareholder_hint("关于公司董事、高管减持计划实施完成的公告", issuer_name="翔鹭钨业") == ""
    assert _shareholder_hint("关于董事、持股5%以上股东减持股份预披露的公告", issuer_name="同花顺") == ""


def test_title_holder_names_can_extract_specific_named_controller():
    names = _title_holder_names(
        "关于控股股东宁波盈峰、实际控制人及其一致行动人减持公司股份比例触及1%整数倍的公告",
        issuer_name="盈峰环境",
    )

    assert names == ["宁波盈峰"]


def test_pdf_text_detail_extraction_for_completed_decrease():
    record = ShareholderChangeRecord(
        symbol="688757",
        name="胜科纳米",
        announcement_date="2026-05-16",
        direction="decrease",
        status="completed",
        shareholder_type="5%以上股东",
        title="关于股东减持计划实施完毕暨减持股份结果公告",
        url="https://static.cninfo.com.cn/finalpage/2026-05-16/1225309242.PDF",
        announcement_id="1225309242",
        risk_level="high",
    )
    detail = _extract_change_detail_from_text(
        """
        股东名称 深圳高捷 股东身份 直接持股5%以上股东
        持股数量 23,884,070股 持股比例 5.92%
        减持数量 4,033,000股
        减持期间 2026年4月29日～2026年5月14日
        减持方式及对应减持数量 集中竞价减持，314,500股 大宗交易减持，3,718,500股
        减持价格区间 25.73～34.21元/股
        减持总金额 112,718,420元
        减持比例 1.00%
        当前持股数量 19,851,070股 当前持股比例 4.92%
        因自身资金需求，深圳高捷计划减持。
        """,
        record,
    )

    assert detail["shareholder_names"] == ["深圳高捷"]
    assert detail["change_shares"] == "4,033,000股"
    assert detail["change_ratio"] == "1.00%"
    assert detail["change_amount"] == "112,718,420元"
    assert detail["price_range"] == "25.73～34.21元/股"
    assert detail["change_period"] == "2026年4月29日～2026年5月14日"
    assert detail["change_method"] == "集中竞价、大宗交易"
    assert detail["holding_before"] == "23,884,070股，占5.92%"
    assert detail["holding_after"] == "19,851,070股，占4.92%"
    assert detail["detail_quality"] == "full"


def test_formatter_surfaces_detail_fields_inline():
    response = ShareholderChangeScanResponse(
        generated_at=datetime.now(timezone.utc),
        direction="all",
        start_date="2026-05-11",
        end_date="2026-05-17",
        total_found=1,
        returned_count=1,
        detail_attempted_count=1,
        detail_success_count=1,
        summary="命中 1 条 A 股增减持公告。",
        records=[
            ShareholderChangeRecord(
                symbol="688757",
                name="胜科纳米",
                announcement_date="2026-05-16",
                direction="decrease",
                status="completed",
                shareholder_type="5%以上股东",
                title="关于股东减持计划实施完毕暨减持股份结果公告",
                url="https://static.cninfo.com.cn/finalpage/2026-05-16/1225309242.PDF",
                announcement_id="1225309242",
                risk_level="high",
                shareholder_names=["深圳高捷"],
                change_shares="4,033,000股",
                change_ratio="1.00%",
                change_amount="112,718,420元",
                price_range="25.73～34.21元/股",
                change_period="2026年4月29日～2026年5月14日",
                change_method="集中竞价、大宗交易",
                holding_before="23,884,070股，占5.92%",
                holding_after="19,851,070股，占4.92%",
                detail_source="pdf",
                detail_quality="full",
            )
        ],
    )

    content = format_shareholder_change_skill_response(response)

    assert "明细（前 1 条，已解析前 1 条 PDF 正文，1 条抽取到可用明细。）" in content
    assert "股东/人员：深圳高捷" in content
    assert "数量/比例：4,033,000股 / 1.00%" in content
    assert "价格/金额：25.73～34.21元/股 / 112,718,420元" in content


@pytest.mark.asyncio
async def test_orchestrator_semantic_route_invokes_shareholder_skill(monkeypatch):
    from deepfocus_api import main

    async def fake_scan(skill_request):
        assert skill_request.direction == "all"
        return ShareholderChangeScanResponse(
            generated_at=datetime.now(timezone.utc),
            direction="all",
            start_date="2026-05-11",
            end_date="2026-05-17",
            total_found=1,
            returned_count=1,
            summary="命中 1 条 A 股增减持公告。",
            records=[
                ShareholderChangeRecord(
                    symbol="002484",
                    name="江海股份",
                    announcement_date="2026-05-16",
                    direction="decrease",
                    status="plan",
                    shareholder_type="董监高",
                    title="关于部分董事、高级管理人员减持股份预披露的公告",
                    url="https://static.cninfo.com.cn/finalpage/2026-05-16/1225310831.PDF",
                    announcement_id="1225310831",
                    risk_level="high",
                )
            ],
        )

    monkeypatch.setattr(main, "scan_shareholder_changes", fake_scan)
    response = await main._maybe_shareholder_change_skill_chat(
        OrchestratorChatRequest(message="查看 A 股近一周所有股东增减持信息")
    )

    assert response is not None
    assert response.handled_inline is True
    assert response.should_create_task is False
    assert "shareholder.change.scan" in response.chips
    assert "已调用 skill" in response.content
    assert response.reasoning_trace[1].title == "shareholder.change.scan"
