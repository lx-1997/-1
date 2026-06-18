"""iFinD 客户端：A股代码归一 + 实时行情解析(mock) + 未配置/非A股优雅降级。"""
import pytest

from deepfocus_api import ifind_api


def test_normalize_a_code():
    assert ifind_api.normalize_a_code("600519") == "600519.SH"
    assert ifind_api.normalize_a_code("300750") == "300750.SZ"
    assert ifind_api.normalize_a_code("000858") == "000858.SZ"
    assert ifind_api.normalize_a_code("688981") == "688981.SH"
    assert ifind_api.normalize_a_code("600519.SH") == "600519.SH"  # 已带后缀原样
    assert ifind_api.normalize_a_code("AAPL") is None              # 非A股 → None
    assert ifind_api.normalize_a_code("00700.HK") is None
    assert ifind_api.normalize_a_code("") is None


def test_quote_disabled_without_token(monkeypatch):
    monkeypatch.delenv("DEEPFOCUS_IFIND_REFRESH_TOKEN", raising=False)
    r = ifind_api.real_time_quote("600519")
    assert r["ok"] is False and "refresh" in r["error"].lower()


def test_quote_filters_non_a_share(monkeypatch):
    monkeypatch.setenv("DEEPFOCUS_IFIND_REFRESH_TOKEN", "rt")
    # 全是非A股 → 不发请求，直接提示
    r = ifind_api.real_time_quote("AAPL,00700.HK")
    assert r["ok"] is False and "A 股" in r["error"] and r["skipped"] == ["AAPL", "00700.HK"]


def test_quote_parses_real_shape(monkeypatch):
    monkeypatch.setenv("DEEPFOCUS_IFIND_REFRESH_TOKEN", "rt")
    monkeypatch.setattr(ifind_api, "_access_token", lambda: "tok")
    # 用实测返回结构 mock
    monkeypatch.setattr(ifind_api, "_request_quote", lambda codes, ind, tok: {
        "errorcode": 0, "errmsg": "Success!",
        "tables": [{"thscode": "600519.SH", "time": ["2026-06-15 16:00:59"],
                    "table": {"latest": [1271.1], "changeRatio": [-1.61], "pe_ttm": [19.21], "totalCapital": [1.58e12]}}],
    })
    r = ifind_api.real_time_quote("600519,AAPL")
    assert r["ok"] is True and r["skipped"] == ["AAPL"]
    row = r["rows"][0]
    assert row["code"] == "600519.SH" and row["latest"] == 1271.1 and row["pe_ttm"] == 19.21
    assert row["time"] == "2026-06-15 16:00:59"


def test_fetch_market_quotes_ifind_symbol_alignment(monkeypatch):
    """Surface B 关键正确性：iFinD 命中的 quote 必须用「原请求 symbol」作 key（裸6位 vs .SH 不能错配），
    且命中后跳过原链。ifind_user=False 时绝不触发 iFinD。"""
    import asyncio as _aio
    from deepfocus_api import market_data

    calls = {"n": 0}
    def _fake_quote(codes, indicators=None):
        calls["n"] += 1
        # iFinD 返回归一后的带后缀 code
        return {"ok": True, "skipped": [], "rows": [
            {"code": "600519.SH", "latest": 1271.1, "changeRatio": -1.61, "pe_ttm": 19.2, "pb": 5.8, "totalCapital": 1.5e12, "turnoverRatio": 0.33},
            {"code": "000001.SZ", "latest": 11.5, "changeRatio": 0.5, "pe_ttm": 5.1, "pb": 0.6, "totalCapital": 2.2e11, "turnoverRatio": 0.9},
        ]}
    # fetch_market_quotes 内是 `from . import ifind_api` 延迟导入 → patch 真实模块
    monkeypatch.setattr(ifind_api, "real_time_quote", _fake_quote)
    monkeypatch.setattr(ifind_api, "enabled", lambda: True)

    # ifind_user=True：两只都命中 → 不走原链(provider 全 ifind)，symbol 对齐到原请求
    resp = _aio.run(
        market_data.fetch_market_quotes(["600519", "000001.SZ"], ifind_user=True))
    got = {q.symbol: q for q in resp.quotes}
    assert set(got) == {"600519", "000001.SZ"}            # 用原请求 symbol，非 600519.SH
    assert got["600519"].provider == "ifind" and got["600519"].is_realtime
    assert got["600519"].pe_ttm == 19.2 and got["600519"].total_capital == 1.5e12
    assert got["000001.SZ"].price == 11.5

    # ifind_user=False：绝不调用 iFinD
    calls["n"] = 0
    monkeypatch.setattr(market_data, "QUOTE_PROVIDERS", [])  # 断网，避免真请求
    resp2 = _aio.run(
        market_data.fetch_market_quotes(["600519"], ifind_user=False))
    assert calls["n"] == 0 and resp2.provider == "none"


def test_execute_tool_ifind_grade_no_leak(monkeypatch):
    """Surface C 关键安全：_IFIND_GRADE 仅在 execute_tool 作用域内有效，调用后必复位 False（防跨请求串味）。"""
    import asyncio as _aio
    from deepfocus_api import agent_tools as AT

    seen = []
    AT.register_tool(AT.AgentTool(name="_probe_grade", description="t", parameters={"type": "object", "properties": {}},
                                  handler=lambda: AT._asyncio_probe() if hasattr(AT, "_asyncio_probe") else None))

    async def _probe():  # 直接读 ContextVar
        return {"grade": AT._IFIND_GRADE.get()}
    AT.TOOL_REGISTRY["_probe_grade"].handler = _probe

    async def go():
        assert AT._IFIND_GRADE.get() is False           # 初始
        r1 = await AT.execute_tool("_probe_grade", {}, ifind_user=True)
        assert r1["data"]["grade"] is True               # 作用域内可见
        assert AT._IFIND_GRADE.get() is False            # 调用后复位
        r2 = await AT.execute_tool("_probe_grade", {})    # 默认 False（其他调用方/ashare_review 路径）
        assert r2["data"]["grade"] is False
        assert AT._IFIND_GRADE.get() is False
    _aio.run(go())
    AT.TOOL_REGISTRY.pop("_probe_grade", None)


def test_execute_tool_resets_grade_on_exception(monkeypatch):
    """handler 抛异常也必须复位灰度位。"""
    import asyncio as _aio
    from deepfocus_api import agent_tools as AT

    async def _boom():
        raise RuntimeError("boom")
    AT.TOOL_REGISTRY["_boom"] = AT.AgentTool(name="_boom", description="t", parameters={"type": "object", "properties": {}}, handler=_boom)

    async def go():
        r = await AT.execute_tool("_boom", {}, ifind_user=True)
        assert r["ok"] is False
        assert AT._IFIND_GRADE.get() is False  # 异常后仍复位
    _aio.run(go())
    AT.TOOL_REGISTRY.pop("_boom", None)


def test_valuation_tool_ifind_branch(monkeypatch):
    """灰度态 + A股 → 估值工具用 iFinD；非灰度 → 走原 fetch_valuation。"""
    import asyncio as _aio
    from deepfocus_api import agent_tools as AT

    monkeypatch.setattr(ifind_api, "enabled", lambda: True)
    monkeypatch.setattr(ifind_api, "real_time_quote", lambda codes, indicators=None: {
        "ok": True, "rows": [{"code": "600519.SH", "pe_ttm": 19.2, "pb": 5.8, "totalCapital": 1.5e12}]})
    called = {"orig": 0}
    async def _fake_val(symbol, market=None):
        called["orig"] += 1; return {"pe_ratio": 99, "provider": "eastmoney"}
    monkeypatch.setattr(AT, "fetch_valuation", _fake_val)

    async def go():
        tok = AT._IFIND_GRADE.set(True)
        try:
            v = await AT._tool_get_valuation("600519", "CN")
        finally:
            AT._IFIND_GRADE.reset(tok)
        assert v["provider"] == "ifind" and v["pe_ratio"] == 19.2 and called["orig"] == 0  # 没回退原源
        # 非灰度 → 原源
        v2 = await AT._tool_get_valuation("600519", "CN")
        assert v2["provider"] == "eastmoney" and called["orig"] == 1
    _aio.run(go())


def test_quote_propagates_api_error(monkeypatch):
    monkeypatch.setenv("DEEPFOCUS_IFIND_REFRESH_TOKEN", "rt")
    monkeypatch.setattr(ifind_api, "_access_token", lambda: "tok")
    monkeypatch.setattr(ifind_api, "_request_quote", lambda codes, ind, tok: {
        "errorcode": -4230, "errmsg": "You currently do not have permission for real-time US stock market quotes.", "tables": []})
    r = ifind_api.real_time_quote("600519")
    assert r["ok"] is False and "permission" in r["error"].lower()
