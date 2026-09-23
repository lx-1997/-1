"""比较题质量闸：报告期口径、可见核对摘要和发布前降级。"""
from __future__ import annotations

import asyncio

from deepfocus_api import agent_tools
from deepfocus_api.llm import _comparison_period_guard, _summarize_tool_result


def test_financial_period_key_is_explicit():
    assert agent_tools._financial_period_key("2026-03-31") == "2026-Q1"
    assert agent_tools._financial_period_key("2026-06-30") == "2026-H1"
    assert agent_tools._financial_period_key("not-a-date") is None


def test_compare_stocks_marks_latest_but_non_common_period(monkeypatch):
    async def quote(symbol, market=None):
        return {"quotes": [{"price": 100, "currency": "CNY"}]}

    async def valuation(symbol, market=None):
        return {"pe_ratio": 20, "currency": "CNY"}

    async def financials(symbol, market=None):
        date = "2026-06-30" if "宁德" in symbol else "2026-03-31"
        return {"name": symbol, "report_date": date, "revenue_yoy": 10, "roe": 8}

    async def consensus(symbol, market=None):
        return {"period": "截至2026-08-01", "avg_target_price": 120}

    monkeypatch.setattr(agent_tools, "_tool_get_market_quote", quote)
    monkeypatch.setattr(agent_tools, "_tool_get_valuation", valuation)
    monkeypatch.setattr(agent_tools, "_tool_get_financials", financials)
    monkeypatch.setattr(agent_tools, "_tool_get_analyst_consensus", consensus)

    payload = asyncio.run(agent_tools._tool_compare_stocks("宁德时代,比亚迪"))
    basis = payload["comparison_basis"]
    assert basis["is_strictly_comparable"] is False
    assert basis["financials"] == "latest_each_company"
    assert basis["periods_by_symbol"] == {"宁德时代": "2026-H1", "比亚迪": "2026-Q1"}
    assert payload["items"][0]["financials"]["same_period_as_peers"] is False
    assert "不得横向排名" in basis["warning"]


def test_compare_stocks_summary_exposes_mismatch_to_ui():
    summary = _summarize_tool_result({
        "ok": True,
        "data": {
            "items": [{"symbol": "300750"}, {"symbol": "002594"}],
            "comparison_basis": {
                "is_strictly_comparable": False,
                "periods_by_symbol": {"300750": "2026-H1", "002594": "2026-Q1"},
            },
        },
    })
    assert "财报期不同" in summary
    assert "2026-H1" in summary and "2026-Q1" in summary


def test_comparison_period_guard_removes_misleading_same_basis_claim():
    answer = "结论：宁德更优。\n\n同口径横向比较显示宁德 ROE 更高。"
    guarded = _comparison_period_guard(
        "宁德时代和比亚迪未来一年更偏向谁？",
        answer,
        {
            "is_strictly_comparable": False,
            "periods_by_symbol": {"宁德时代": "2026-H1", "比亚迪": "2026-Q1"},
        },
    )
    assert "同口径" not in guarded
    assert "财报期不同" in guarded
    assert "ROE" in guarded
