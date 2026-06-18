"""行情源注册表（QUOTE_PROVIDERS）的回归守卫。

把 fetch_market_quotes 的 7 步硬编码回退链重构成「声明式注册表 + 单循环」后，这些测试锁定：
  1. 注册表结构合法（key 唯一、需 key 的源标了 requires_key、中国源只服务 A股/港股）；
  2. 派发语义与旧 if/elif 一致：按序补齐、已命中的符号不再下发给后面的源、
     无 key 的源跳过、scope 不匹配的源跳过、全部命中即短路 break。
"""
from __future__ import annotations

import asyncio

from deepfocus_api import market_data as md
from deepfocus_api.schemas import MarketQuote


def _quote(symbol: str, provider: str) -> MarketQuote:
    return MarketQuote(
        symbol=symbol,
        price=1.0,
        provider=provider,
        provider_name=provider,
        fetched_at="2026-06-06T00:00:00+00:00",
    )


def _install_recording_stubs(monkeypatch, fills: dict[str, str]):
    """把每个 _fetch_* 替换成记录调用的桩；fills: provider_key -> 它能补齐的符号。
    返回 calls 列表，每项是 (provider_key, 收到的符号列表)。"""
    calls: list[tuple[str, list[str]]] = []

    def make(provider_key: str, takes_key: bool):
        async def stub(client, symbols, *args):  # args 容纳 finnhub/alpha 的 key 参数
            calls.append((provider_key, list(symbols)))
            owned = fills.get(provider_key)
            quotes = [_quote(s, provider_key) for s in symbols if owned and s == owned]
            return quotes, []
        return stub

    monkeypatch.setattr(md, "_fetch_finnhub_quotes", make("finnhub", True))
    monkeypatch.setattr(md, "_fetch_alpha_vantage_quotes", make("alpha_vantage", True))
    # eastmoney 在表里出现两次（cn 优先 + 全局兜底），同一函数据 symbols 区分。
    monkeypatch.setattr(md, "_fetch_eastmoney_quotes", make("eastmoney", False))
    monkeypatch.setattr(md, "_fetch_eastmoney_us_quotes", make("eastmoney_us", False))
    monkeypatch.setattr(md, "_fetch_sina_quotes", make("sina", False))
    monkeypatch.setattr(md, "_fetch_tencent_quotes", make("tencent", False))
    monkeypatch.setattr(md, "_fetch_stooq_quotes", make("stooq", False))
    return calls


# --- 结构守卫 ---------------------------------------------------------------

def test_registry_keys_unique_and_nonempty() -> None:
    keys = [p.key for p in md.QUOTE_PROVIDERS]
    assert keys == ["finnhub", "alpha_vantage", "eastmoney_cn", "sina", "tencent", "eastmoney_us", "stooq", "eastmoney_fallback", "google_finance"]
    assert len(keys) == len(set(keys))


def test_key_gated_providers_marked() -> None:
    by_key = {p.key: p for p in md.QUOTE_PROVIDERS}
    assert by_key["finnhub"].requires_key is True
    assert by_key["alpha_vantage"].requires_key is True
    # 公共源不需要 key。
    for k in ("eastmoney_cn", "sina", "tencent", "stooq", "eastmoney_fallback"):
        assert by_key[k].requires_key is False


def test_china_providers_serve_only_cn_hk() -> None:
    by_key = {p.key: p for p in md.QUOTE_PROVIDERS}
    for k in ("eastmoney_cn", "tencent"):
        assert by_key[k].serves("600519.SH") is True
        assert by_key[k].serves("00700.HK") is True
        assert by_key[k].serves("AAPL") is False
    # sina 经 gb_ 前缀也覆盖美股（主力快源）→ 三市场全服务
    assert by_key["sina"].serves("600519.SH") is True
    assert by_key["sina"].serves("00700.HK") is True
    assert by_key["sina"].serves("AAPL") is True
    # 东财美股源只服务美股
    assert by_key["eastmoney_us"].serves("AAPL") is True
    assert by_key["eastmoney_us"].serves("600519.SH") is False
    # 全局源服务一切。
    for k in ("finnhub", "alpha_vantage", "stooq", "eastmoney_fallback"):
        assert by_key[k].serves("AAPL") is True
        assert by_key[k].serves("600519.SH") is True


# --- 派发语义守卫 -----------------------------------------------------------

def test_no_keys_us_symbol_falls_through_to_stooq(monkeypatch) -> None:
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.delenv("FINNHUB_TOKEN", raising=False)
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)
    monkeypatch.delenv("ALPHA_VANTAGE_API_KEY", raising=False)
    calls = _install_recording_stubs(monkeypatch, fills={"stooq": "AAPL", "eastmoney": "600519.SH"})

    resp = asyncio.run(md.fetch_market_quotes(["AAPL", "600519.SH"]))
    called = {c[0] for c in calls}

    # 无 key → finnhub / alpha 整段跳过。
    assert "finnhub" not in called and "alpha_vantage" not in called
    # CN 符号被 eastmoney_cn 命中，且 eastmoney 只收到中国符号（AAPL 被 scope 过滤掉）。
    em_targets = [syms for key, syms in calls if key == "eastmoney"]
    assert em_targets and all("AAPL" not in t for t in em_targets)
    # sina 现在也覆盖美股 → 会尝试 AAPL（本测试它不补齐，继续往后落）；tencent 仍只服务 CN/HK 且无缺口 → 不被调用。
    sina_targets = [syms for key, syms in calls if key == "sina"]
    assert sina_targets == [["AAPL"]]
    assert "tencent" not in called
    # AAPL 落到 stooq。
    assert ("stooq", ["AAPL"]) in calls
    assert {q.symbol for q in resp.quotes} == {"AAPL", "600519.SH"}
    assert resp.provider == "mixed"


def test_earlier_hit_short_circuits_later_providers(monkeypatch) -> None:
    # finnhub 命中 AAPL 后，后面的源不应再收到 AAPL（已补齐）。
    monkeypatch.setenv("FINNHUB_API_KEY", "demo")
    calls = _install_recording_stubs(monkeypatch, fills={"finnhub": "AAPL"})

    resp = asyncio.run(md.fetch_market_quotes(["AAPL"]))

    assert ("finnhub", ["AAPL"]) in calls
    # 全部命中 → 循环 break，stooq / eastmoney 兜底不再触发。
    later = {key for key, _ in calls if key in {"stooq", "eastmoney", "alpha_vantage"}}
    assert not later
    assert {q.symbol for q in resp.quotes} == {"AAPL"}
    assert resp.provider == "finnhub"


def test_unfilled_symbol_reaches_eastmoney_fallback(monkeypatch) -> None:
    # 没有任何源能补齐的美股符号，会一路走到末尾 eastmoney_fallback（serves=all），
    # 最终仍缺失则进 warnings。
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.delenv("FINNHUB_TOKEN", raising=False)
    calls = _install_recording_stubs(monkeypatch, fills={})  # 谁都补不齐

    resp = asyncio.run(md.fetch_market_quotes(["ZZZZ"]))
    called = [key for key, _ in calls]

    # stooq 之后，eastmoney 兜底（全局 scope）也被调用过一次，且收到了这个美股符号。
    assert "stooq" in called
    em_targets = [syms for key, syms in calls if key == "eastmoney"]
    assert any("ZZZZ" in t for t in em_targets)
    assert resp.quotes == []
    assert any("ZZZZ" in w for w in resp.warnings)


# --- 东财 suggest 候选解析：科创板/创业板/北证不再被漏掉（回归） ---
def test_candidate_parser_covers_star_chinext_bse() -> None:
    from deepfocus_api.market_data import _candidate_from_eastmoney as parse

    star = parse({"Code": "688206", "Name": "概伦电子", "Classify": "23",
                  "SecurityTypeName": "科创板", "QuoteID": "1.688206"})
    assert star and star.market == "CN" and star.symbol == "688206.SH"

    chinext = parse({"Code": "300750", "Name": "宁德时代", "Classify": "AStock",
                     "SecurityTypeName": "创业板", "QuoteID": "0.300750"})
    assert chinext and chinext.market == "CN" and chinext.symbol == "300750.SZ"

    bse = parse({"Code": "920185", "Name": "贝特瑞", "Classify": "23",
                 "SecurityTypeName": "北证", "QuoteID": "0.920185"})
    assert bse and bse.market == "CN" and bse.symbol == "920185.BJ"

    # 主板仍正常
    main = parse({"Code": "600519", "Name": "贵州茅台", "Classify": "AStock",
                  "SecurityTypeName": "沪A", "QuoteID": "1.600519"})
    assert main and main.symbol == "600519.SH"
