"""微信/终端 AI 智能体「中文名→A股代码」解析的回归守卫。

根因：所有数据工具都按【代码】取数，但用户发的是【中文名】(长电科技/亿纬锂能/概伦电子)，
模型凭记忆猜 A 股代码常错 → 取数全错。修法：stock_name_index 加 resolve_to_code/search_names，
execute_tool 入口对 symbol 自动归一(中文名→代码)，并新增 resolve_symbol 工具供消歧/指代解析。
"""
from __future__ import annotations

import asyncio

import pytest

from deepfocus_api import agent_tools as at
from deepfocus_api import stock_name_index as sni


@pytest.fixture
def names(monkeypatch):
    # 注入小型 name->code 表（生产由磁盘缓存/后台刷新填全 ~5400 只）
    sni._ingest({
        "长电科技": "600584", "亿纬锂能": "300014", "概伦电子": "688206",
        "宁德时代": "300750", "思源电气": "002028", "长城汽车": "601633",
    })
    return None


def test_resolve_to_code_variants(names):
    r = sni.resolve_to_code
    assert r("长电科技") == "600584"                 # 精确名
    assert r("分析下长电科技这只票") == "600584"      # 名字裹在短语里
    assert r("600519") == "600519"                   # 已是代码
    assert r("看看 002028 思源电气") == "002028"      # 文本含代码优先取代码
    assert r("AAPL") is None                          # 美股 ticker 不强转
    assert r("00700") is None                         # 港股代码(ascii)不强转
    assert r("宁德") == "300750"                      # 唯一前缀
    assert r("长") is None                            # 歧义前缀不猜(长电/长城)
    assert r("不存在的某票") is None                  # 查不到→None
    assert r("") is None


def test_search_names_candidates(names):
    out = sni.search_names("概伦")
    assert out and out[0]["code"] == "688206" and out[0]["market"] == "CN"
    assert sni.search_names("688206")[0]["code"] == "688206"  # 代码反查
    assert sni.search_names("") == []


def test_execute_tool_coerces_chinese_symbol(names):
    seen: dict = {}

    async def _echo(symbol=None, market=None):
        seen["symbol"] = symbol
        return {"got": symbol}

    at.register_tool(at.AgentTool(name="_echo_resolve_test", description="t",
                                  parameters=at._SYMBOL_MARKET_SCHEMA, handler=_echo))
    try:
        async def run():
            r = await at.execute_tool("_echo_resolve_test", {"symbol": "亿纬锂能"})
            assert r["ok"] and seen["symbol"] == "300014"     # 中文名 → 代码后才进 handler
            await at.execute_tool("_echo_resolve_test", {"symbol": "AAPL"})
            assert seen["symbol"] == "AAPL"                   # 美股 ticker 原样透传
        asyncio.run(run())
    finally:
        at.TOOL_REGISTRY.pop("_echo_resolve_test", None)


def test_resolve_symbol_tool(names):
    async def run():
        r = await at.execute_tool("resolve_symbol", {"query": "宁德"})
        assert r["ok"]
        assert r["data"]["matches"][0]["code"] == "300750"
        miss = await at.execute_tool("resolve_symbol", {"query": "压根不存在xyz"})
        assert miss["data"]["matches"] == []
    asyncio.run(run())


def test_resolve_symbol_registered():
    assert "resolve_symbol" in at.TOOL_REGISTRY
