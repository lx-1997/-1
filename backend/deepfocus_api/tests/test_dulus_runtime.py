from __future__ import annotations

import pytest

from deepfocus_api import dulus_runtime as dr
from deepfocus_api.dulus_runtime import (
    build_dulus_runtime_status,
    create_dulus_memory,
    inspect_authorized_webbridge,
    list_dulus_memories,
    run_dulus_roundtable,
)
from deepfocus_api.schemas import (
    DulusMemoryCreateRequest,
    DulusRoundtableRequest,
    DulusWebBridgeInspectRequest,
    StockSnapshot,
)


class MockLLM:
    provider = "mock"
    provider_name = "mock"
    model = "deepfocus-mock"


def test_dulus_status_runs_in_compliant_mode() -> None:
    status = build_dulus_runtime_status(MockLLM())

    assert status.compliant_mode is True
    assert any(provider.mode == "webbridge_disabled" for provider in status.providers)
    assert any(tool.id == "webbridge_browser_capture" and not tool.enabled for tool in status.tools)
    assert "白名单" in status.webbridge_policy
    assert any(tool.id == "authorized_webbridge_inspect" and tool.enabled for tool in status.tools)


def test_dulus_memory_persists_to_sqlite(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(dr, "DB_PATH", tmp_path / "dulus_memory.sqlite3")

    record = create_dulus_memory(
        DulusMemoryCreateRequest(
            scope="project",
            hall="facts",
            title="授权 WebBridge 规则",
            content="只允许本机或白名单域名。",
            tags=["webbridge", "policy"],
        )
    )
    memories = list_dulus_memories(limit=10).memories

    assert record.title == "授权 WebBridge 规则"
    assert any(memory.id == record.id for memory in memories)
    assert any(memory.source == "palace_init" for memory in memories)


def test_authorized_webbridge_blocks_unlisted_hosts() -> None:
    result = inspect_authorized_webbridge(DulusWebBridgeInspectRequest(url="https://gemini.google.com/"))

    assert result.allowed is False
    assert "不在白名单" in result.policy


def test_professional_synthesis_content_has_stable_sections() -> None:
    content = dr._professional_synthesis_content(
        "估值处于合理偏贵区间，暂不支持确定性结论。",
        ["PE 与盈利增速需要一起看", "现金流质量尚可"],
        ["盈利下修会压缩估值容错率"],
        ["复核下一期财报和关键催化"],
        fallback="fallback",
    )

    assert content.index("**结论**") < content.index("**核心依据**")
    assert content.index("**核心依据**") < content.index("**风险与反证**")
    assert content.index("**风险与反证**") < content.index("**下一步核验**")
    assert "PE 与盈利增速需要一起看" in content


def test_professional_synthesis_dedupes_headings_and_trims_at_sentence_boundary() -> None:
    noisy = (
        "**结论**：先给结论。**核心依据**：依据一。**核心依据**：重复标题不应再次出现。"
        "**风险与反证**：风险一。**下一步核验**：动作一。" + "补充说明，" * 500
    )
    content = dr._professional_synthesis_content(noisy, [], [], [], fallback="fallback")
    for heading in ("结论", "核心依据", "风险与反证", "下一步核验"):
        assert content.count(f"**{heading}**") == 1
    assert len(content) <= 1800
    assert content.endswith(("。", "！", "？", "；", "…"))


def test_comparison_contract_marks_period_mismatch_and_removes_same_basis() -> None:
    bundle = {
        "items": [
            {"name": "宁德时代", "symbol": "300750", "financials": {"period_label": "2026 H1"}},
            {"name": "比亚迪", "symbol": "002594", "financials": {"period_label": "2026 Q1"}},
        ],
        "comparison_basis": {
            "is_strictly_comparable": False,
            "periods_by_symbol": {"300750": "2026-H1", "002594": "2026-Q1"},
        },
    }
    context = "数据模块：" + __import__("json").dumps({"compare_stocks": __import__("json").dumps(bundle, ensure_ascii=False)})
    request = DulusRoundtableRequest(objective="宁德时代和比亚迪更偏向谁？", context=context)
    answer = dr._enforce_comparison_contract(
        request,
        "**结论**：同口径比较显示宁德时代 ROE 更高。\n\n**核心依据**：比亚迪资料。\n\n**风险与反证**：暂无。\n\n**下一步核验**：补财报。",
    )
    assert "同口径" not in answer
    assert "报告期不同" in answer
    assert "仅分别展示" in answer


def test_comparison_fallback_keeps_names_when_data_packet_is_empty() -> None:
    request = DulusRoundtableRequest(
        objective="宁德时代和比亚迪更偏向谁？",
        # 模拟 compare_stocks 和其余数据源同时失败；页面仍可能携带旧的当前标的。
        context="当前标的：国金证券（600109）",
        stock=StockSnapshot(symbol="600109", name="国金证券", market="CN"),
    )

    answer = dr._fallback_synthesis_content(request, [])

    assert "宁德时代" in answer and "比亚迪" in answer
    assert "国金证券" not in answer
    assert "暂不偏向任何一方" in answer


def test_method_question_never_keeps_company_specific_claims() -> None:
    assert dr._is_method_question("只讲方法，不引用实时数据，解释 PE/PB/PEG") is True
    answer = dr._method_safety_content("**结论**：宁德时代收购某电池厂形成商誉。")
    assert "宁德时代" not in answer
    assert "PEG" in answer


@pytest.mark.asyncio
async def test_generic_stock_pick_does_not_research_page_stock() -> None:
    response = await run_dulus_roundtable(
        MockLLM(),
        DulusRoundtableRequest(
            objective="你推荐买哪些股票",
            context="当前标的：国金证券（600109）\n上一轮问题：AI算力选股",
            stock=StockSnapshot(symbol="600109", name="国金证券", market="CN"),
            mode="deep_research",
        ),
    )

    assert response.decision == "research_more"
    assert response.turns == []
    assert response.tool_traces == []
    assert response.sources == []
    assert "市场、持有周期和风险偏好" in response.synthesis
    assert "国金证券" not in response.synthesis
    assert "AI算力" not in response.synthesis
    assert "未执行研究取数" in response.disclaimer


@pytest.mark.asyncio
async def test_dulus_roundtable_blocks_webbridge_and_returns_synthesis() -> None:
    response = await run_dulus_roundtable(
        MockLLM(),
        DulusRoundtableRequest(
            objective="研究 TSLA 的一到四周风险收益",
            context="需要结合财报、行情和社区分歧，但当前只有简短上下文。",
            stock=StockSnapshot(
                symbol="TSLA",
                name="Tesla",
                market="US",
                sector="EV",
                currentPrice=420.0,
                changePercent=-1.2,
                description="Tesla Inc.",
                focusLevel="high",
                communityScore=82,
            ),
            participants=["evidence", "research", "risk"],
            enabled_tools=["market_snapshot", "webbridge_browser_capture", "risk_review"],
        ),
    )

    assert response.turns
    assert response.synthesis
    assert response.decision == "blocked"
    assert any(trace.status == "blocked" for trace in response.tool_traces)
    assert any("WebBridge" in warning for warning in response.warnings)
    assert [step["label"] for step in response.research_steps] == [
        "数据接入", "证据筛选", "交叉验证", "多空评估", "结论输出"
    ]
