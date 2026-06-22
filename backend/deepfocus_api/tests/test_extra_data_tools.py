"""补打通工具回归守卫:横向对比 / 投研晨报(main.py 注册)。

验证注册 + handler 精简投影 + 晨报缓存命中短路。底层 stock_compare/data_store 全部 monkeypatch。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS

from deepfocus_api import agent_tools, main, data_store


def run(tool: str, **args):
    return asyncio.run(agent_tools.execute_tool(tool, args))


def test_extra_tools_registered():
    for n in ("get_stock_comparison", "get_briefing_today"):
        assert n in agent_tools.TOOL_REGISTRY, n


def test_stock_comparison_projection(monkeypatch):
    item = NS(symbol="600519", name="贵州茅台", overall_verdict="重点跟踪", overall_score=80,
              sector="消费", market_cap=1.5e12,
              dimensions=[NS(label="估值", signal="bull"), NS(label="动量", signal="neutral")])

    async def fake_compare(symbols="", caps=""):
        return NS(items=[item])

    monkeypatch.setattr(main, "stock_compare", fake_compare)
    out = run("get_stock_comparison", symbols="600519,000858")
    assert out["ok"] is True
    d = out["data"]["items"][0]
    assert d["symbol"] == "600519" and d["score"] == 80
    assert d["dims"] == {"估值": "bull", "动量": "neutral"}  # 维度→信号灯精简矩阵


def test_briefing_cache_hit_short_circuits(monkeypatch):
    # 缓存命中→直接返回,不触发重活(build_macro/portfolio)
    monkeypatch.setattr(data_store, "latest",
                        lambda *a, **k: {"headline": "维持纪律", "macro_verdict": "中性", "portfolio_verdict": "稳健"})
    out = run("get_briefing_today")
    assert out["ok"] is True and out["data"]["macro_verdict"] == "中性"
