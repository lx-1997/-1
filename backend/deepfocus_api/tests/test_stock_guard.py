"""标的防幻觉护栏：解析只采信「候选名原样出现在问题里」；工具 symbol 参数确定性改写。"""
import asyncio

from deepfocus_api import stock_guard as sg
from deepfocus_api.market_data import MarketSymbolCandidate, MarketSymbolSearchResponse


def _resp(*pairs):
    cands = [MarketSymbolCandidate(symbol=s, code=c, name=n, market=m, exchange="港股",
                                   security_type="港股", quote_id=f"116.{c}", provider="test",
                                   provider_name="测试")
             for s, c, n, m in pairs]
    return MarketSymbolSearchResponse(query="q", market=None, candidates=cands,
                                      provider="test", fetched_at="2026-10-26T00:00:00Z", warnings=[])


class _FakeSearch:
    """按查询词返回预设候选，记录调用序列。"""
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    async def __call__(self, query, market=None):
        self.calls.append(query)
        return self.mapping.get(query, _resp())


def _install(monkeypatch, mapping):
    fake = _FakeSearch(mapping)
    monkeypatch.setattr(sg, "_CACHE", {})
    import deepfocus_api.market_data as md
    monkeypatch.setattr(md, "search_market_symbols", fake)
    return fake


def test_resolve_accepts_only_contained_name(monkeypatch):
    fake = _install(monkeypatch, {"建滔集团": _resp(("00148.HK", "00148", "建滔集团", "HK"))})
    r = asyncio.run(sg.resolve_question_stock("建滔集团近况"))
    assert r["primary"]["symbol"] == "00148.HK" and len(r["all"]) == 1
    # 噪声词「近况」被切分，直接用「建滔集团」探一次即命中
    assert fake.calls == ["建滔集团"]


def test_resolve_rejects_uncontained_candidate(monkeypatch):
    # 搜索返回了名字不在问题里的公司（如汇丰控股）——绝不采信
    _install(monkeypatch, {"建滔集团": _resp(("0005.HK", "0005", "汇丰控股", "HK"))})
    assert asyncio.run(sg.resolve_question_stock("建滔集团近况")) is None


def test_resolve_skips_when_question_has_code_or_no_cjk(monkeypatch):
    fake = _install(monkeypatch, {})
    assert asyncio.run(sg.resolve_question_stock("0005.HK 近况如何")) is None
    assert asyncio.run(sg.resolve_question_stock("What about NVDA?")) is None
    assert asyncio.run(sg.resolve_question_stock("")) is None
    assert fake.calls == []


def test_resolve_multi_hits_report_all(monkeypatch):
    _install(monkeypatch, {
        "建滔集团": _resp(("00148.HK", "00148", "建滔集团", "HK")),
        "腾讯控股": _resp(("00700.HK", "00700", "腾讯控股", "HK")),
    })
    r = asyncio.run(sg.resolve_question_stock("建滔集团和腾讯控股哪个好"))
    assert len(r["all"]) == 2 and r["primary"]["name"] in ("建滔集团", "腾讯控股")


def test_resolve_caches(monkeypatch):
    fake = _install(monkeypatch, {"建滔集团": _resp(("00148.HK", "00148", "建滔集团", "HK"))})
    asyncio.run(sg.resolve_question_stock("建滔集团近况"))
    asyncio.run(sg.resolve_question_stock("建滔集团近况"))
    assert len(fake.calls) == 1  # 进程缓存命中后不再搜索
    assert sg._CACHE["建滔集团近况"][1]["primary"]["symbol"] == "00148.HK"


def test_guard_rewrites_hallucinated_symbol():
    resolved = {"primary": {"symbol": "00148.HK", "name": "建滔集团"}, "all": [
        {"symbol": "00148.HK", "name": "建滔集团"}]}
    assert sg.guard_symbol_args("get_stock_research", {"symbol": "600545"}, resolved)["symbol"] == "00148.HK"
    assert sg.guard_symbol_args("get_stock_snapshot", {"symbol": "0005.HK"}, resolved)["symbol"] == "00148.HK"
    # 规范格式不同也算幻觉，统一归一
    assert sg.guard_symbol_args("get_market_quote", {"symbol": "0148"}, resolved)["symbol"] == "00148.HK"
    # 其余参数原样保留
    out = sg.guard_symbol_args("get_stock_snapshot", {"symbol": "0005", "market": "HK"}, resolved)
    assert out["market"] == "HK"


def test_guard_never_rewrites_multi_or_protected():
    resolved = {"primary": {"symbol": "00148.HK", "name": "建滔集团"}, "all": [
        {"symbol": "00148.HK", "name": "建滔集团"},
        {"symbol": "00700.HK", "name": "腾讯控股"}]}
    args = {"symbol": "600545"}
    assert sg.guard_symbol_args("compare_stocks", args, resolved) is args
    assert sg.guard_symbol_args("resolve_symbol", args, resolved) is args
    assert sg.guard_symbol_args("get_market_data", args, resolved) is args
    assert sg.guard_symbol_args("get_stock_research", args, None) is args
    assert sg.guard_symbol_args("get_stock_research", {"symbol": ""}, resolved)["symbol"] == ""


def test_guard_rewrites_company_level_news_and_announcements():
    # 公司新闻/公告/预告也是单标的工具——猜错代码会把别家新闻冒充问题标的（实测踩过）
    resolved = {"primary": {"symbol": "00148.HK", "name": "建滔集团"}, "all": [
        {"symbol": "00148.HK", "name": "建滔集团"}]}
    assert sg.guard_symbol_args("get_stock_news", {"symbol": "600545"}, resolved)["symbol"] == "00148.HK"
    assert sg.guard_symbol_args("get_stock_announcements", {"symbol": "600545"}, resolved)["symbol"] == "00148.HK"
    assert sg.guard_symbol_args("get_next_disclosure", {"symbol": "600545"}, resolved)["symbol"] == "00148.HK"


def test_guard_passthrough_when_symbol_absent():
    resolved = {"primary": {"symbol": "00148.HK", "name": "建滔集团"}, "all": [
        {"symbol": "00148.HK", "name": "建滔集团"}]}
    args = {"query": "建滔集团"}
    assert sg.guard_symbol_args("get_market_data", args, resolved) is args
