"""数据打通工具回归守卫：把 AI 模拟盘 / 热度榜 包装成 agent 工具。

验证：①新工具已注册并出现在 openai_tool_specs ②各 handler 做了精简投影(剔除大字段、运营数据)
③execute_tool 正确包装 {ok,data}。底层数据函数全部 monkeypatch，不触真实 I/O。
（焦点人物工具因 prod 在中国机房连不上 Google News，已撤除，见 [[ai-native-tool-agent]]）
"""
from __future__ import annotations

import asyncio

from deepfocus_api import agent_tools, ai_fund, data_store


def run(tool: str, **args):
    return asyncio.run(agent_tools.execute_tool(tool, args))


def test_new_tools_registered():
    specs = {s["function"]["name"] for s in agent_tools.openai_tool_specs()}
    for n in ("get_ai_fund_snapshot", "get_ai_fund_arena", "get_hot_stocks"):
        assert n in agent_tools.TOOL_REGISTRY, n
        assert n in specs, n
    assert "get_people_spotlight" not in agent_tools.TOOL_REGISTRY  # 已撤除(Google News 在 prod 不可达)


def test_ai_fund_snapshot_projection(monkeypatch):
    fake = {
        "persona": {"name": "阿尔法", "style": "balanced", "tag": "均衡"},
        "strategy": {"model_label": "多因子", "market_stance": "进攻"},
        "stats": {"win_rate": 0.6},
        "nav_pct": 12.3, "alpha_pct": 4.5, "benchmark_name": "沪深300",
        "cash": 100.0, "position_count": 3, "max_positions": 6, "days_running": 30,
        "mood": "乐观", "commentary": "今天加仓了",
        "positions": [{"name": "茅台", "symbol": "600519", "pnl_pct": 5, "weight": 0.2,
                       "float_pnl": 1000, "kline": [1, 2, 3], "events": [{"x": 1}]}],
        "trades": [{"side": "buy", "name": "茅台", "price": 1500, "pnl_pct": None,
                    "catalyst": "放量", "ts": "t", "narrative": "long blob", "thinking": [1, 2]}],
        "data_quality": {"label": "实时"},
    }
    monkeypatch.setattr(ai_fund, "get_snapshot", lambda fid=ai_fund.FUND_ID: fake)
    out = run("get_ai_fund_snapshot", strategy="main")
    assert out["ok"] is True
    d = out["data"]
    assert d["fund"] == "阿尔法" and d["nav_pct"] == 12.3 and d["alpha_pct"] == 4.5
    assert d["win_rate"] == 0.6 and d["data_quality"] == "实时"
    # 精简投影：大字段(kline/events/thinking/narrative)被剔除
    assert d["positions"][0]["symbol"] == "600519" and "kline" not in d["positions"][0]
    assert "narrative" not in d["recent_trades"][0] and "thinking" not in d["recent_trades"][0]
    assert "disclaimer" in d


def test_ai_fund_arena_projection(monkeypatch):
    fake = {
        "strategies": [{"rank": 1, "name": "猛犸", "style": "aggressive", "nav_pct": 20.0,
                        "alpha_pct": 8.0, "win_rate": 0.7, "max_drawdown_pct": -12.0,
                        "position_count": 5, "model_label": "激进", "last_action": {"side": "buy"},
                        "history": [1, 2, 3], "commentary": "blob"}],
        "champion": "mammoth", "spread": 15.0,
        "benchmark": {"name": "沪深300", "nav_pct": 3.0},
        "consensus": [], "divergence": [], "disclaimer": "演示",
    }
    monkeypatch.setattr(ai_fund, "get_arena", lambda: fake)
    out = run("get_ai_fund_arena")
    assert out["ok"] is True
    d = out["data"]
    assert d["champion"] == "mammoth" and d["spread"] == 15.0
    s = d["strategies"][0]
    assert s["name"] == "猛犸" and s["model"] == "激进" and s["rank"] == 1
    assert "history" not in s and "commentary" not in s  # 精简


def test_hot_stocks_projection(monkeypatch):
    monkeypatch.setattr(data_store, "hot_symbols",
                        lambda kind="verdict", **kw: [{"symbol": "AAPL", "market": "US", "count": 9}])
    out = run("get_hot_stocks", days=7, limit=5)
    assert out["ok"] is True
    top = out["data"]["hot"][0]
    assert out["data"]["window_days"] == 7 and top["symbol"] == "AAPL" and top["rank"] == 1
    assert "count" not in top  # 研判次数(运营数据)不外泄,只回 rank
