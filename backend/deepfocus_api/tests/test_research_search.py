from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from deepfocus_api import main
from deepfocus_api import eastmoney_reports as er
from deepfocus_api.schemas import MarketSymbolCandidate, MarketSymbolSearchResponse


def _search_response(candidate: MarketSymbolCandidate | None) -> MarketSymbolSearchResponse:
    return MarketSymbolSearchResponse(
        query="x",
        candidates=[candidate] if candidate else [],
        provider="eastmoney" if candidate else "none",
        fetched_at="2026-06-06T00:00:00Z",
    )


def _candidate(market: str, code: str, name: str) -> MarketSymbolCandidate:
    return MarketSymbolCandidate(
        symbol=f"{code}.SH" if market == "CN" else code,
        code=code, name=name, market=market,
        provider="eastmoney", provider_name="东方财富公共搜索",
    )


# --- pure helpers ----------------------------------------------------------

def test_report_pdf_url_uses_h3_prefix():
    assert er.eastmoney_report_pdf_url("AP123") == "https://pdf.dfcfw.com/pdf/H3_AP123_1.pdf"
    assert er.eastmoney_report_pdf_url("") == ""


def test_normalize_row_extracts_fields_and_drops_empty_rating():
    row = er._normalize_row({
        "infoCode": "AP9", "title": "深度报告", "orgSName": "某某证券",
        "publishDate": "2026-05-25 08:00:00", "sRatingName": "--",
        "stockName": "贵州茅台", "stockCode": "600519",
    })
    assert row["info_code"] == "AP9"
    assert row["date"] == "2026-05-25"
    assert row["rating"] == ""  # "--" 归一化为无评级
    assert row["pdf_url"].endswith("H3_AP9_1.pdf")


def test_normalize_row_rejects_rows_without_code_or_title():
    assert er._normalize_row({"title": "无编号"}) is None
    assert er._normalize_row({"infoCode": "AP1"}) is None


# --- /api/research/search --------------------------------------------------

def test_search_us_short_circuits_with_warning(monkeypatch):
    monkeypatch.setattr(main, "search_market_symbols",
                        lambda kw, market=None: _async(_search_response(_candidate("US", "TSLA", "特斯拉"))))

    async def _fail(**kwargs):  # 美股不应触发东财研报查询
        raise AssertionError("query_eastmoney_reports should not be called for US")
    monkeypatch.setattr(main, "query_eastmoney_reports", _fail)

    resp = asyncio.run(main.api_research_search(keyword="特斯拉"))
    assert resp.items == []
    assert resp.resolved_market == "US"
    assert resp.data_quality.level == "degraded"
    assert any("海外投行" in w for w in resp.warnings)


def test_search_cn_maps_rows_to_items(monkeypatch):
    monkeypatch.setattr(main, "search_market_symbols",
                        lambda kw, market=None: _async(_search_response(_candidate("CN", "600519", "贵州茅台"))))

    async def _rows(*, code, market, page_size):
        assert code == "600519"
        return ([{
            "info_code": "AP100", "title": "年报点评", "org": "诚通证券",
            "date": "2026-05-25", "rating": "强烈推荐", "stock_name": "贵州茅台",
            "pdf_url": "https://pdf.dfcfw.com/pdf/H3_AP100_1.pdf",
        }], [])
    monkeypatch.setattr(main, "query_eastmoney_reports", _rows)

    resp = asyncio.run(main.api_research_search(keyword="茅台"))
    assert resp.provider == "eastmoney"
    assert resp.data_quality.level == "live"
    assert len(resp.items) == 1
    item = resp.items[0]
    assert item.id == "AP100"
    assert item.symbol == "600519.SH"
    assert item.rating == "强烈推荐"
    assert item.preview_url == "/api/research/pdf/AP100"


def test_search_empty_keyword_is_degraded():
    resp = asyncio.run(main.api_research_search(keyword="  "))
    assert resp.provider == "none"
    assert resp.data_quality.level == "degraded"


def test_search_unresolved_keyword_is_degraded(monkeypatch):
    monkeypatch.setattr(main, "search_market_symbols",
                        lambda kw, market=None: _async(_search_response(None)))
    resp = asyncio.run(main.api_research_search(keyword="zzzz不存在"))
    assert resp.items == []
    assert resp.provider == "none"


# --- /api/research/pdf/{info_code} (SSRF guard) ----------------------------

def test_pdf_proxy_rejects_illegal_info_code():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.api_research_pdf("a" * 70))  # 超长 → 不匹配 [A-Za-z0-9]{1,64}
    assert exc.value.status_code == 422

    with pytest.raises(HTTPException) as exc2:
        asyncio.run(main.api_research_pdf("bad/../code"))
    assert exc2.value.status_code == 422


def _async(value):
    async def _coro():
        return value
    return _coro()
