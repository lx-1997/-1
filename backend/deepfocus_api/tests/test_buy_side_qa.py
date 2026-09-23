from __future__ import annotations

import asyncio
from types import SimpleNamespace

from deepfocus_api.buy_side_qa import (
    PROTOCOL_VERSION,
    answer_protocol_issues,
    build_buy_side_mandate,
)
from deepfocus_api import llm as llm_mod
from deepfocus_api import dulus_runtime as dr
from deepfocus_api.llm import CloudResearchLLM
from deepfocus_api.schemas import DulusRoundtableRequest


def test_unspecified_single_stock_question_uses_transparent_dual_horizon() -> None:
    mandate = build_buy_side_mandate("贵州茅台现在怎么看？")

    assert mandate.is_research is True
    assert mandate.task_type == "single_stock"
    assert mandate.horizon == "dual"
    assert mandate.horizon_explicit is False
    assert "未来 1—4 个季度与 3—5 年" in mandate.horizon_label
    prompt = mandate.system_prompt()
    assert "不能擅自假定用户是长线投资者" in prompt
    assert "市场当前在定价什么" in prompt
    assert "缺少可核验的一致预期" in prompt
    assert "反证或失效条件" in prompt
    assert "5 个关键变量" in prompt


def test_explicit_short_horizon_uses_earnings_and_expectation_lens() -> None:
    mandate = build_buy_side_mandate("宁德时代财报后未来一个季度怎么看？")

    assert mandate.task_type == "earnings_event"
    assert mandate.horizon == "event"
    assert mandate.horizon_explicit is True
    assert "一致预期" in mandate.research_lens
    assert "盈利预测修正" in mandate.research_lens
    assert "与该期限对齐" in mandate.decision_context_note


def test_explicit_one_year_horizon_overrides_earnings_event_default() -> None:
    mandate = build_buy_side_mandate("宁德时代财报后未来一年怎么看？")

    assert mandate.task_type == "earnings_event"
    assert mandate.horizon == "medium"
    assert mandate.horizon_label == "6—12 个月"


def test_explicit_long_horizon_uses_business_and_management_lens() -> None:
    mandate = build_buy_side_mandate("贵州茅台未来五年是否值得长期持有？")

    assert mandate.horizon == "long"
    assert mandate.horizon_explicit is True
    assert "护城河" in mandate.research_lens
    assert "资本配置" in mandate.research_lens
    assert "管理层可信度" in mandate.research_lens


def test_management_research_turns_implicit_judgment_into_checks() -> None:
    mandate = build_buy_side_mandate("这家公司 CFO 的业绩指引可信吗？")

    assert mandate.task_type == "management_quality"
    assert mandate.horizon == "cross_cycle"
    assert "历史承诺—实际结果" in mandate.research_lens
    assert "CEO/CFO 表述一致性" in mandate.research_lens


def test_fact_lookup_stays_compact_instead_of_forcing_alpha_sections() -> None:
    mandate = build_buy_side_mandate("贵州茅台现价多少？")

    assert mandate.task_type == "fact_lookup"
    assert mandate.key_variable_target == 0
    assert mandate.required_sections == ("直接答案", "时点与口径")
    prompt = mandate.system_prompt()
    assert "不为显得深刻而硬造非共识观点" in prompt
    assert "事实型问题不强行扩写投资逻辑" in prompt


def test_deep_research_expands_human_verification_list() -> None:
    mandate = build_buy_side_mandate("给我做一份比亚迪完整投委会尽调")

    assert mandate.key_variable_target == 15
    assert "15 个关键变量" in mandate.system_prompt()
    assert "允许 1200—1800 字" in mandate.system_prompt()


def test_report_digest_is_news_research_and_lowercase_chat_is_not_ticker() -> None:
    assert build_buy_side_mandate("总结一下最近研报").task_type == "news_digest"
    assert build_buy_side_mandate("ok").is_research is False
    assert build_buy_side_mandate("AAPL 怎么看").is_research is True


def test_protocol_eval_flags_consensus_falsifier_and_horizon_gaps() -> None:
    mandate = build_buy_side_mandate("贵州茅台怎么看？")

    assert answer_protocol_issues(mandate, "结论：公司不错。") == [
        "missing_market_expectation_or_gap",
        "missing_falsifier",
        "missing_human_verification",
        "missing_transparent_horizon_assumption",
    ]
    good = (
        "短中期未来几个季度看盈利兑现，长期 3—5 年看品牌与现金流。"
        "当前缺少可核验的一致预期，预期差待确认。"
        "反证条件是关键经营指标持续走弱。关键变量与人工复核：财报口径、指引兑现。"
    )
    assert answer_protocol_issues(mandate, good) == []


def test_tool_agent_injects_mandate_and_exposes_eval_metadata(monkeypatch) -> None:
    captured: list[dict] = []

    monkeypatch.setattr(
        llm_mod,
        "load_model_config",
        lambda: {
            "provider": "openai",
            "model": "test-model",
            "api_key": "test-key",
            "base_url": None,
            "temperature": 0.2,
        },
    )

    async def no_mcp_tools():
        return []

    async def fake_completion(payload, *, timeout_seconds):
        captured.append(payload)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(
                content=(
                    "短中期看未来几个季度，长期看 3—5 年。"
                    "缺少可核验的一致预期，预期差待确认；"
                    "反证条件是盈利走弱。关键变量与人工复核：最新财报。"
                ),
                tool_calls=[],
            ))]
        )

    monkeypatch.setattr(llm_mod, "discover_mcp_agent_tools", no_mcp_tools)
    model = CloudResearchLLM()
    monkeypatch.setattr(model, "_completion", fake_completion)

    result = asyncio.run(model.run_tool_agent(question="贵州茅台现在怎么看？"))

    system = captured[0]["messages"][0]["content"]
    assert f"买方研究协议 {PROTOCOL_VERSION}" in system
    assert "先按用户的持有期限和研究任务选择方法" in system
    assert "短周期财报或事件题应围绕预期差" in system
    assert "市场当前在定价什么" in system
    assert result is not None
    assert result["research_mandate"]["horizon"] == "dual"
    assert result["protocol_issues"] == []


def test_fact_audit_receives_expectation_gap_and_human_check_rules(monkeypatch) -> None:
    captured: list[dict] = []
    scripted_messages = [
        SimpleNamespace(
            content="",
            tool_calls=[SimpleNamespace(
                id="quote-1",
                function=SimpleNamespace(
                    name="get_market_quote",
                    arguments='{"symbol":"600519"}',
                ),
            )],
        ),
        SimpleNamespace(content="草稿结论。", tool_calls=[]),
        SimpleNamespace(
            content=(
                "短中期看未来几个季度，长期看 3—5 年。"
                "缺少可核验的一致预期，预期差待确认。"
                "反证条件是后续已披露指标走弱。关键变量与人工复核：最新财报。"
            ),
            tool_calls=[],
        ),
    ]

    monkeypatch.setattr(
        llm_mod,
        "load_model_config",
        lambda: {
            "provider": "openai",
            "model": "test-model",
            "api_key": "test-key",
            "base_url": None,
            "temperature": 0.2,
        },
    )

    async def no_mcp_tools():
        return []

    async def fake_execute_tool(name, args, **kwargs):
        assert name == "get_market_quote"
        return {"ok": True, "data": {"quotes": [{"symbol": "600519", "price": 1500.0}]}}

    async def fake_completion(payload, *, timeout_seconds):
        captured.append(payload)
        message = scripted_messages[len(captured) - 1]
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    monkeypatch.setattr(llm_mod, "discover_mcp_agent_tools", no_mcp_tools)
    monkeypatch.setattr(llm_mod, "execute_tool", fake_execute_tool)
    model = CloudResearchLLM()
    monkeypatch.setattr(model, "_completion", fake_completion)

    result = asyncio.run(model.run_tool_agent(
        question="贵州茅台现在怎么看？",
        audit_final_answer=True,
    ))

    assert result is not None
    assert len(captured) == 3
    audit_request = captured[2]["messages"][1]["content"]
    assert "只有证据 JSON 明确含一致预期" in audit_request
    assert "预期差待确认" in audit_request
    assert "关键复核变量" in audit_request
    assert result["protocol_issues"] == []


def test_roundtable_prompt_maps_protocol_to_existing_four_sections() -> None:
    mandate = build_buy_side_mandate("宁德时代和比亚迪未来一年更偏向谁？")
    prompt = mandate.roundtable_prompt()

    assert mandate.task_type == "stock_comparison"
    assert mandate.horizon == "medium"
    assert "核心依据" in prompt
    assert "市场在定价什么及预期差" in prompt
    assert "下一步核验" in prompt
    assert "具体反证" in prompt


def test_roundtable_roles_and_editor_receive_the_same_buy_side_context() -> None:
    captured: list[str] = []

    class FakeLLM:
        provider = "openai"
        provider_name = "openai-compatible"
        model = "test-model"

        async def complete_json(self, prompt, **kwargs):
            captured.append(prompt)
            return {
                "content": (
                    "**结论**：暂时结论。\n\n"
                    "**核心依据**：已有证据。\n\n"
                    "**风险与反证**：等待证伪。\n\n"
                    "**下一步核验**：复核关键变量。"
                ),
                "key_points": ["已有证据"],
                "risks": ["等待证伪"],
                "actions": ["复核关键变量"],
                "confidence": 0.62,
            }

    request = DulusRoundtableRequest(
        objective="宁德时代财报后未来一个季度怎么看？",
        participants=["research"],
        enabled_tools=[],
    )
    turn = asyncio.run(dr._run_participant(FakeLLM(), request, "research", [], []))
    asyncio.run(dr._build_synthesis(FakeLLM(), request, [turn], []))

    assert len(captured) == 2
    assert all(f"买方研究口径 {PROTOCOL_VERSION}" in prompt for prompt in captured)
    assert all("期限按『1—4 周/未来 1—2 个季度』" in prompt for prompt in captured)
    assert "研究角色区分市场共识" in captured[0]
    assert "主席在『核心依据』中写清市场在定价什么及预期差" in captured[1]
