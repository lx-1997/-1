"""agent 工具 assess_long_term_bull：用《价值投资之长线牛股》方法论给个股做长线体检，
只用 ground-truth、缺数据诚实降级、永不抛断 agent 循环。"""
import asyncio

from deepfocus_api import agent_tools


def _run(coro):
    return asyncio.run(coro)


async def _earn(symbol, market=None):
    return {"roe": 22.0, "revenue_yoy": 28.0, "profit_yoy": 35.0, "eps": 2.1}


async def _val(symbol, market=None):
    return {"pe_ratio": 30.0, "pb_ratio": 6.5, "peg": 0.8}


def _fake_msgs(**kwargs):
    return [type("M", (), {"title": "公司中标12亿元大订单"})()]


async def _stmt(symbol, market=None):
    # 现金奶牛样本：(+,-,-) 类型4 + 含金量足 + 好生意
    from deepfocus_api import financial_statements as fs
    return {"ocf": 2.69e10, "icf": -5e9, "fcf_financing": -1e10, "capex": 6e8, "fcf": 2.63e10,
            "roe": 22.0, "gross_margin": 89.7, "revenue_yoy": 28.0, "profit_yoy": 35.0,
            "provider": "eastmoney", "report_date": "2026-03-31",
            **fs.assess_quality({"ocf": 2.69e10, "icf": -5e9, "fcf_financing": -1e10, "fcf": 2.63e10,
                                 "eps": 21.5, "ocf_per_share": 21.4, "deduct_eps": 21.4,
                                 "roe": 22.0, "gross_margin": 89.7})}


def test_assess_full_path(monkeypatch):
    monkeypatch.setattr(agent_tools, "fetch_eastmoney_earnings", _earn)
    monkeypatch.setattr(agent_tools, "fetch_valuation", _val)
    monkeypatch.setattr(agent_tools, "list_realtime_messages", _fake_msgs)
    monkeypatch.setattr(agent_tools.financial_statements, "fetch_statements", _stmt)
    out = _run(agent_tools.execute_tool("assess_long_term_bull", {"symbol": "600519", "market": "CN"}))
    assert out["ok"] is True
    d = out["data"]
    assert d["roe_stage"]["stage"] == "成长期"          # ROE 上行 + 双增 → 成长期(第4.7)
    assert d["target_type"]["label"]                    # 投资对象五型已归类(第5章)
    assert d["cashflow"]["type"] == 4                    # 现金流八类型已计入(第9.5)
    assert d["cashflow"]["free_cash_flow"] is not None
    assert d["bull_gene"]["score"] is not None          # 牛股基因综合分(第3.7)
    assert "大订单" in (d["catalysts"]["bull_types"] or [])  # 催化剂按第2章关键字分类
    assert any("PE" in n for n in d["valuation"]["notes"])
    assert "不构成投资建议" in d["disclaimer"]


def test_get_financial_statements_tool(monkeypatch):
    monkeypatch.setattr(agent_tools.financial_statements, "fetch_statements", _stmt)
    out = _run(agent_tools.execute_tool("get_financial_statements", {"symbol": "600519", "market": "CN"}))
    assert out["ok"] is True
    assert out["data"]["cashflow"]["type"] == 4 and out["data"]["free_cash_flow"] is not None


def test_assess_degrades_without_data(monkeypatch):
    async def _none(symbol, market=None):
        return None
    monkeypatch.setattr(agent_tools, "fetch_eastmoney_earnings", _none)
    monkeypatch.setattr(agent_tools, "fetch_valuation", _none)
    monkeypatch.setattr(agent_tools, "list_realtime_messages", lambda **k: [])
    monkeypatch.setattr(agent_tools.financial_statements, "fetch_statements", _none)
    out = _run(agent_tools.execute_tool("assess_long_term_bull", {"symbol": "AAPL"}))
    assert out["ok"] is True                            # 永不抛断
    assert out["data"]["data_gaps"]                     # 诚实标注缺口
    assert out["data"]["roe_stage"]["stage"] == "—"


def test_tool_registered():
    assert "assess_long_term_bull" in agent_tools.TOOL_REGISTRY
    spec = agent_tools.TOOL_REGISTRY["assess_long_term_bull"].openai_spec()
    assert spec["function"]["name"] == "assess_long_term_bull"
