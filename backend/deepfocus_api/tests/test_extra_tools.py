"""新增 agent 工具（价格走势/宏观快照/期权）的回归守卫。

桩掉底层取数函数（yahoo_finance / github_data / nasdaq_data），只测工具的紧凑映射 + 注册，不触网。
在任何 asyncio.run 之前于模块顶部 import main（注册工具 + 规避 customs_hs_detail 的模块级 asyncio.Lock）。
"""
from __future__ import annotations

import asyncio

from deepfocus_api import agent_tools, github_data, nasdaq_data, yahoo_finance
from deepfocus_api import main as _main  # noqa: F401  —— import 触发工具注册


def test_new_tools_registered():
    names = [t.name for t in agent_tools.list_tools()]
    for n in ("get_price_history", "get_macro_environment", "get_options_signal"):
        assert n in names


def test_price_history_summary(monkeypatch):
    async def fake(symbol, market=None):
        return [("2026-01-01", 100.0), ("2026-03-01", 90.0), ("2026-06-01", 120.0)]

    monkeypatch.setattr(yahoo_finance, "fetch_yahoo_history", fake)
    out = asyncio.run(agent_tools.execute_tool("get_price_history", {"symbol": "AAPL"}))

    assert out["ok"] is True
    d = out["data"]
    assert d["first"] == 100.0 and d["last"] == 120.0
    assert d["high"] == 120.0 and d["low"] == 90.0
    assert d["change_pct"] == 20.0 and d["points"] == 3


def test_price_history_empty_degrades(monkeypatch):
    async def fake(symbol, market=None):
        return []

    monkeypatch.setattr(yahoo_finance, "fetch_yahoo_history", fake)
    out = asyncio.run(agent_tools.execute_tool("get_price_history", {"symbol": "ZZZZ"}))
    assert out["ok"] is True and out["data"] is None  # 优雅降级


def test_macro_snapshot_takes_latest(monkeypatch):
    async def y(months=13):
        return [("2026-05", 4.1), ("2026-06", 4.3)]

    async def o(points=20):
        return [("2026-06", 78.5)]

    async def g(months=13):
        return [("2026-06", 2300.0)]

    async def s(months=13):
        return [("2026-06", 5400.0)]

    monkeypatch.setattr(github_data, "fetch_us10y_history", y)
    monkeypatch.setattr(github_data, "fetch_oil_history", o)
    monkeypatch.setattr(github_data, "fetch_gold_history", g)
    monkeypatch.setattr(github_data, "fetch_sp500_index_history", s)

    out = asyncio.run(agent_tools.execute_tool("get_macro_environment", {}))
    assert out["ok"] is True
    d = out["data"]
    assert d["us10y_yield"]["value"] == 4.3  # 取最新（末条）
    assert d["wti_oil"]["value"] == 78.5
    assert d["gold"]["value"] == 2300.0
    assert d["sp500"]["value"] == 5400.0


def test_macro_one_source_failing_still_returns(monkeypatch):
    async def ok(*a, **k):
        return [("2026-06", 4.3)]

    async def boom(*a, **k):
        raise RuntimeError("source down")

    monkeypatch.setattr(github_data, "fetch_us10y_history", ok)
    monkeypatch.setattr(github_data, "fetch_oil_history", boom)  # 单源失败
    monkeypatch.setattr(github_data, "fetch_gold_history", ok)
    monkeypatch.setattr(github_data, "fetch_sp500_index_history", ok)

    out = asyncio.run(agent_tools.execute_tool("get_macro_environment", {}))
    assert out["ok"] is True
    assert out["data"]["us10y_yield"]["value"] == 4.3
    assert out["data"]["wti_oil"] is None  # 失败的源 → None，不拖垮整体


def test_options_signal_passthrough(monkeypatch):
    async def fake(symbol):
        return {"put_call_ratio": 0.85, "sentiment": "neutral"}

    monkeypatch.setattr(nasdaq_data, "fetch_nasdaq_options", fake)
    out = asyncio.run(agent_tools.execute_tool("get_options_signal", {"symbol": "AAPL"}))
    assert out["ok"] is True and out["data"]["put_call_ratio"] == 0.85


def test_verdict_tool_read_cache(monkeypatch, tmp_path):
    # 短 TTL 读缓存：同一标的第二次调用走缓存（不重建），标 cached=True。
    from datetime import datetime, timezone

    from deepfocus_api import data_store
    from deepfocus_api.schemas import TearSheetResponse

    monkeypatch.setattr(data_store, "DB_PATH", tmp_path / "ds.sqlite3")
    data_store.init_data_store()

    calls = {"n": 0}
    fake_ts = TearSheetResponse(
        symbol="AAPL", name="Apple", generated_at=datetime.now(timezone.utc),
        overall_verdict="重点跟踪", overall_score=42, confidence=0.7,
    )

    async def fake_core(symbol, name="", market_cap=None, market=""):
        calls["n"] += 1
        return fake_ts

    monkeypatch.setattr(_main, "_build_stock_tear_sheet_core", fake_core)

    out1 = asyncio.run(agent_tools.execute_tool("get_stock_verdict", {"symbol": "AAPL"}))
    out2 = asyncio.run(agent_tools.execute_tool("get_stock_verdict", {"symbol": "AAPL"}))

    assert calls["n"] == 1  # 第二次命中缓存，未重建
    assert "cached" not in out1["data"]  # 首次非缓存
    assert out2["data"]["cached"] is True  # 二次缓存命中
    assert out2["data"]["overall_verdict"] == "重点跟踪"


def test_verdict_tool_cache_expired_rebuilds(monkeypatch, tmp_path):
    # max_age 到期 → 不命中 → 重建。用极短 TTL 模拟。
    from datetime import datetime, timezone

    from deepfocus_api import data_store
    from deepfocus_api.schemas import TearSheetResponse

    monkeypatch.setattr(data_store, "DB_PATH", tmp_path / "ds.sqlite3")
    monkeypatch.setattr(_main, "_VERDICT_TOOL_CACHE_TTL", -1.0)  # 恒过期
    data_store.init_data_store()

    calls = {"n": 0}

    async def fake_core(symbol, name="", market_cap=None, market=""):
        calls["n"] += 1
        return TearSheetResponse(
            symbol="AAPL", name="Apple", generated_at=datetime.now(timezone.utc),
            overall_verdict="重点跟踪", overall_score=42, confidence=0.7,
        )

    monkeypatch.setattr(_main, "_build_stock_tear_sheet_core", fake_core)

    asyncio.run(agent_tools.execute_tool("get_stock_verdict", {"symbol": "AAPL"}))
    asyncio.run(agent_tools.execute_tool("get_stock_verdict", {"symbol": "AAPL"}))
    assert calls["n"] == 2  # TTL 恒过期 → 每次都重建
