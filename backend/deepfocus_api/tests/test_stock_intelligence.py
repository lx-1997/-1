"""Protect real-data scenario math, provenance and the platform visibility gate."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from deepfocus_api import stock_intelligence as intelligence

NOW = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)


def snapshot(**overrides):
    inputs = dict(
        name="测试公司", quote={"price": 100, "currency": "CNY", "provider": "eastmoney", "market_time": "2026-10-13"},
        valuation={"pe_ratio": 20, "pe_dynamic": 40, "currency": "CNY"},
        financials={"report_date": "2026-06-30", "eps": 2, "profit_yoy": 20, "net_income": 100},
        forecasts={"year": 2026, "currency": "CNY", "eps_median": 6, "eps_low": 5, "eps_high": 7,
                   "broker_count": 2, "as_of": "2026-09-30", "oldest_report": "2026-08-20", "reports": []},
        history=[(f"2026-09-{i:02d}", 100 - i / 10) for i in range(1, 31)] + [("2026-10-13", 96.8)],
        flow={"main_flow_5d": -2e8, "flow_days": 5, "as_of": "2026-10-13"}, now=NOW,
    )
    inputs.update(overrides)
    return intelligence.build_stock_intelligence("600519", "CN", **inputs)


def test_scenarios_use_annual_forecast_and_ttm_not_cumulative_or_dynamic_pe():
    result = snapshot()
    valuation = result["valuation"]
    assert valuation["status"] == "ready"
    prices = {s["key"]: s["price"] for s in valuation["scenarios"]}
    assert prices == {"optimistic": 165.6, "base": 120, "pessimistic": 81.6}
    assert valuation["anchor"]["forecast_eps"] == 6
    assert valuation["anchor"]["pe_ttm"] == 20
    assert "固定压力假设" in valuation["note"]
    assert "概率" in valuation["note"]
    assert result["summary"]["stance"] == "证据存在分歧"


def test_missing_forecast_is_explicit_sensitivity_without_fair_value_claim():
    result = snapshot(forecasts=None)
    assert result["valuation"]["status"] == "sensitivity"
    assert result["valuation"]["anchor"]["forecast_eps"] is None
    assert result["valuation"]["scenarios"][1]["price"] == 100
    assert "当前股价" in result["valuation"]["formula"]
    assert any("年度 EPS" in item for item in result["data_gaps"])


@pytest.mark.parametrize("pe", [None, -4, 0, "NaN", "inf"])
def test_nonpositive_or_missing_ttm_cannot_use_dynamic_pe_to_generate_price(pe):
    result = snapshot(valuation={"pe_ratio": pe, "pe_dynamic": 30, "currency": "CNY"})
    assert result["valuation"]["status"] == "insufficient"
    assert all(s["price"] is None and s["upside_pct"] is None for s in result["valuation"]["scenarios"])


def test_hk_unverified_reporting_currency_is_not_used_for_price():
    result = intelligence.build_stock_intelligence(
        "00700", "HK", quote={"price": 400, "currency": "HKD"},
        valuation={"pe_ratio": 20, "currency": "HKD"}, financials={"eps": 12.639, "currency": "HKD"},
        forecasts={"year": 2026, "currency": "CNY", "eps_median": 30, "broker_count": 3, "as_of": "2026-10-01"}, now=NOW,
    )
    assert result["valuation"]["status"] == "sensitivity"
    assert result["valuation"]["anchor"]["forecast_eps"] is None
    assert result["valuation"]["scenarios"][1]["price"] == 400
    metrics = next(d for d in result["dimensions"] if d["key"] == "fundamentals")["metrics"]
    assert next(m for m in metrics if "EPS" in m["label"])["unit"] == "财报币种未核验"


def test_stale_or_single_forecast_downgrades_and_stale_quote_suppresses_prices():
    for overrides in [{"as_of": "2026-01-01"}, {"broker_count": 1}, {"year": 2025}]:
        forecast = {"year": 2026, "currency": "CNY", "eps_median": 6, "broker_count": 2, "as_of": "2026-10-01", **overrides}
        assert snapshot(forecasts=forecast)["valuation"]["status"] == "sensitivity"
    assert snapshot(quote={"price": 100, "currency": "CNY", "market_time": "2026-01-01"})["valuation"]["status"] == "insufficient"


def test_forecasts_validate_year_symbol_latest_broker_report_and_no_target_pe():
    def row(org, date, eps, symbol="600519"):
        return {"orgCode": org, "publishDate": date, "predictThisYearEps": eps, "predictThisYearPe": 999,
                "stockCode": symbol, "title": "盈利预测", "infoCode": "AP202610011234"}
    payload = {"currentYear": 2026, "data": [
        row("a", "2026-10-01", "6"), row("a", "2026-09-01", "60"), row("b", "2026-09-20", "8"),
        row("c", "2025-12-25", "50"), row("d", "2026-10-01", "40", symbol="000001"),
        row("e", "2026-10-10", None), row("e", "2026-09-30", "50"), row("f", "2026-10-15", "60"),
    ]}
    parsed = intelligence.parse_annual_forecasts(payload, "600519", now=NOW)
    assert parsed["broker_count"] == 2 and parsed["eps_median"] == 7
    assert parsed["eps_low"] == 6 and parsed["eps_high"] == 8
    assert "pe" not in parsed
    assert intelligence.parse_annual_forecasts({**payload, "currentYear": 2025}, "600519", now=NOW) is None


def test_cashflow_period_mismatch_not_compared_and_all_refs_resolve():
    result = snapshot(cashflow={"report_date": "2025-12-31", "ocf": 10})
    fundamentals = next(d for d in result["dimensions"] if d["key"] == "fundamentals")
    assert not any("现金流/归母" in m["label"] for m in fundamentals["metrics"])
    assert any("不一致" in gap for gap in result["data_gaps"])
    ids = {s["id"] for s in result["sources"]}
    for item in result["dimensions"] + result["valuation"]["scenarios"] + result["catalysts"]:
        assert set(item["source_ids"]) <= ids


def test_technical_range_is_historical_and_flat_rsi_is_fifty():
    hist = [(f"2026-09-{i:02d}", 100) for i in range(1, 31)] + [("2026-10-13", 100)]
    result = snapshot(history=hist)
    assert result["technical"]["range"]["lower"] == result["technical"]["range"]["upper"] == 100
    assert "不是未来" in result["technical"]["range"]["method"]
    metrics = next(d for d in result["dimensions"] if d["key"] == "technical")["metrics"]
    assert next(m for m in metrics if "RSI14" in m["label"])["value"] == 50


def test_history_series_validates_dates_prices_and_deduplicates_in_order():
    history = [
        ("2026-10-12", 109), ("2026-10-11", 108), {"d": "2026-10-12", "c": 110},
        ("2026-10-12", float("nan")), ("2026-10-14", 500), ("2026-02-30", 100),
        ("2026-2-01", 100), ("not-a-date", 100), ("2026-10-10", 0),
        ("2026-10-09", -1), ("2026-10-08", float("inf")), ("2026-10-07", True),
        None, 42, "invalid-row", [], {},
    ]
    result = snapshot(history=history)
    technical = result["technical"]
    assert technical["series"] == [{"date": "2026-10-11", "close": 108}, {"date": "2026-10-12", "close": 110}]
    assert technical["as_of"] == "2026-10-12"
    assert technical["series_currency"] == "CNY"
    assert technical["source_ids"] == ["history"]
    assert any("已剔除" in gap and "未补造" in gap for gap in result["data_gaps"])


def test_history_series_keeps_only_latest_280_actual_observations():
    history = [((NOW - timedelta(days=i)).date().isoformat(), 100 + i) for i in range(310)]
    series = snapshot(history=history)["technical"]["series"]
    assert len(series) == 280
    assert series[0] == {"date": (NOW - timedelta(days=279)).date().isoformat(), "close": 379}
    assert series[-1] == {"date": NOW.date().isoformat(), "close": 100}
    assert [row["date"] for row in series] == sorted(row["date"] for row in series)


def test_stale_valid_history_is_visible_as_history_without_current_trend_claim():
    history = [((datetime(2024, 1, 31) - timedelta(days=i)).date().isoformat(), 100 + i) for i in range(25)]
    result = snapshot(history=history)
    assert len(result["technical"]["series"]) == 25
    assert result["technical"]["as_of"] == "2024-01-31"
    assert next(s for s in result["sources"] if s["id"] == "history")["status"] == "stale"
    assert next(d for d in result["dimensions"] if d["key"] == "technical")["signal"] == "insufficient"


@pytest.mark.parametrize("history", [None, [], [None, ("2026-02-30", 12), ("2026-10-14", 13), ("2026-10-13", 0)]])
def test_no_valid_history_returns_empty_series_without_generated_prices(history):
    result = snapshot(history=history)
    assert result["technical"]["series"] == []
    assert result["technical"]["as_of"] is None
    assert result["technical"]["source_ids"] == []
    assert next(s for s in result["sources"] if s["id"] == "history")["status"] == "missing"


def test_series_currency_is_kept_separate_from_different_quote_currency():
    result = snapshot(quote={"price": 100, "currency": "USD", "provider": "public", "market_time": "2026-10-13"})
    assert result["currency"] == "USD"
    assert result["technical"]["series_currency"] == "CNY"
    assert any("当前报价币种 USD" in gap and "历史日线币种 CNY" in gap and "不能叠加" in gap for gap in result["data_gaps"])
    technical = next(d for d in result["dimensions"] if d["key"] == "technical")
    assert next(m for m in technical["metrics"] if m["label"] == "MA20")["unit"] == "CNY"


def test_unverified_history_currency_is_null_with_clear_gap():
    result = snapshot(history_provider="unknown-public-source", history_currency="")
    assert result["technical"]["series"]
    assert result["technical"]["series_currency"] is None
    assert any("历史日线币种尚未核验" in gap for gap in result["data_gaps"])


def test_synthetic_history_cannot_become_a_price_chart():
    result = snapshot(history_provider="demo")
    assert result["technical"]["series"] == []
    assert result["technical"]["as_of"] is None
    assert any("模拟数据" in gap for gap in result["data_gaps"])


def flow_line(date, main=10, close=100):
    return f"{date},{main},-3,-7,12,-2,1,-1,-1,1,-1,{close},0"


def test_flow_history_uses_actual_date_net_categories_and_unadjusted_close():
    payload = {"data": {"code": "600519", "klines": [
        flow_line("2026-10-12", 8, 99), flow_line("2026-10-11", -8, 101),
        flow_line("2026-10-12", 10, 100), flow_line("2026-10-14", 99, 900),
        flow_line("2026-02-30", 77, 700), "2026-10-10,-,-,-,-,-,-,-,-,-,-,-,0",
    ]}}
    series = intelligence.parse_fund_flow_history(payload, "600519", now=NOW)
    assert [row["date"] for row in series] == ["2026-10-11", "2026-10-12"]
    assert series[-1] == {"date": "2026-10-12", "main_net": 10, "small_net": -3, "medium_net": -7, "large_net": 12, "super_large_net": -2, "close": 100}
    assert series[0]["main_net"] == -8
    assert intelligence.parse_fund_flow_history(payload, "000001", now=NOW) == []


def test_expanded_flow_history_keeps_original_five_day_sum(monkeypatch):
    from deepfocus_api import eastmoney_data
    async def baseline(*args, **kwargs):
        return {"main_flow_5d": 999, "lhb_net": 123, "as_of": "2026-10-13"}
    calls = []
    async def public(key, url, **kwargs):
        calls.append(url)
        return {"_intelligence_fetched_at": "2026-10-13T10:00:00+08:00", "data": {"code": "600519", "klines": [flow_line(f"2026-10-{i:02d}", i, 100 + i) for i in range(1, 11)]}}
    monkeypatch.setattr(eastmoney_data, "fetch_fund_flow", baseline)
    monkeypatch.setattr(intelligence, "_public_json", public)
    flow = asyncio.run(intelligence.fetch_verified_fund_flow("600519", "CN"))
    assert len(flow["series"]) == 10
    assert flow["main_flow_5d"] == sum(range(6, 11)) == 40
    assert flow["flow_days"] == 5
    assert flow["flow_start_date"] == "2026-10-06" and flow["flow_end_date"] == "2026-10-10"
    assert flow["currency"] == flow["price_currency"] == "CNY" and flow["lhb_net"] == 123
    assert "lmt=280" in calls[0] and "f62" in calls[0]
    result = snapshot(flow=flow)
    assert result["capital"]["period"] == {"from": "2026-10-01", "to": "2026-10-10"}
    assert result["capital"]["amount_unit"] == "元"
    assert result["capital"]["price_adjustment"] == "未复权（资金源收盘）"
    assert result["capital"]["series"][-1]["main_net"] == 10


def test_unverified_baseline_flow_does_not_create_daily_history(monkeypatch):
    from deepfocus_api import eastmoney_data
    async def baseline(*args, **kwargs):
        return {"main_flow_5d": 5e8, "as_of": "2026-10-13"}
    async def failure(*args, **kwargs):
        raise RuntimeError("public daily source unavailable")
    monkeypatch.setattr(eastmoney_data, "fetch_fund_flow", baseline)
    monkeypatch.setattr(intelligence, "_public_json", failure)
    flow = asyncio.run(intelligence.fetch_verified_fund_flow("600519", "CN"))
    result = snapshot(flow=flow)
    assert result["capital"]["status"] == "insufficient" and result["capital"]["series"] == []
    assert flow["main_flow_5d"] == 5e8


def test_broker_ratings_dedupe_latest_dates_independently_of_eps():
    def row(org, date, rating, eps=None, code="600519"):
        return {"stockCode": code, "orgCode": org, "orgName": org, "publishDate": date, "emRatingName": rating,
                "predictThisYearEps": eps, "infoCode": "AP202610011234", "title": "评级原文"}
    payload = {"currentYear": 2026, "data": [
        row("a", "2026-10-01", "持有"), row("a", "2026-09-01", "买入", 9),
        row("b", "2026-10-02", "关注"), row("c", "2026-10-03", "增持"),
        row("d", "2026-02-30", "买入"), row("e", "2026-10-14", "买入"),
        row("f", "2025-01-01", "买入"), row("g", "2026-10-01", "买入", code="000001"),
    ]}
    ratings = intelligence.parse_broker_ratings(payload, "600519", "CN", now=NOW)
    assert ratings["sample_count"] == ratings["broker_count"] == 3
    assert sum(item["count"] for item in ratings["distribution"]) == 3
    assert {item["key"]: item["count"] for item in ratings["distribution"]}["neutral"] == 1
    assert {item["key"]: item["count"] for item in ratings["distribution"]}["other"] == 1
    assert next(item for item in ratings["samples"] if item["broker_id"] == "a")["rating"] == "持有"
    annual = intelligence.parse_annual_forecasts(payload, "600519", now=NOW)
    assert annual["eps_median"] is None and annual["broker_count"] == 0
    result = snapshot(forecasts=annual)
    assert result["valuation"]["status"] == "sensitivity"
    assert result["broker_ratings"]["status"] == "available"
    assert result["broker_ratings"]["sample_count"] == len(result["broker_ratings"]["samples"]) == 3


def test_hk_rating_samples_keep_real_broker_publication_and_rating():
    payload = {"result": {"data": [
        {"SECURITY_CODE": "00700", "ORG_CODE": "1", "ORG_NAME": "券商一", "PUBLISH_DATE": "2026-08-25 00:00:00", "RATING_NAME_NEW": "买入"},
        {"SECURITY_CODE": "00700", "ORG_CODE": "1", "ORG_NAME": "券商一", "PUBLISH_DATE": "2026-08-01 00:00:00", "RATING_NAME_NEW": "中性"},
        {"SECURITY_CODE": "00700", "ORG_CODE": "2", "ORG_NAME": "券商二", "PUBLISH_DATE": "2026-08-19 00:00:00", "RATING_NAME_NEW": "减持"},
    ]}}
    ratings = intelligence.parse_broker_ratings(payload, "00700", "HK", now=NOW)
    assert ratings["sample_count"] == 2
    result = intelligence.build_stock_intelligence("00700", "HK", broker_ratings=ratings, now=NOW)
    assert result["broker_ratings"]["period"] == {"from": "2026-08-19", "to": "2026-08-25"}
    assert result["broker_ratings"]["samples"][0]["rating"] == "买入"
    assert result["broker_ratings"]["samples"][0]["url"] is None
    assert "RPT_HKF10_INFO_ORGRATING" in next(s for s in result["sources"] if s["id"] == "broker-ratings")["url"]
    assert result["capital"]["series"] == []


def test_aggregate_consensus_cannot_fabricate_a_dated_rating_distribution():
    result = snapshot(forecasts=None, consensus={"rating": "Buy", "rating_summary": {"买入": 30}, "analyst_count": 30})
    assert result["broker_ratings"]["status"] == "insufficient"
    assert result["broker_ratings"]["samples"] == []
    assert result["broker_ratings"]["sample_count"] == 0
    assert sum(item["count"] for item in result["broker_ratings"]["distribution"]) == 0


def test_platform_visibility_gate_runs_before_excerpt(monkeypatch):
    from deepfocus_api import realtime_messages, symbol_linker
    class Record:
        def __init__(self, content):
            self.content = content
        def model_dump(self, **kwargs):
            return {"id": "visible", "title": "会员文章", "content": self.content, "topic": "文章"}
    originals = {"hidden": Record("来源禁止内容"), "member": Record("允许导语" + "会员隐私段落" * 100)}
    monkeypatch.setattr(symbol_linker, "messages_by_symbol", lambda *a, **k: [{"id": key} for key in originals])
    monkeypatch.setattr(realtime_messages, "get_realtime_message", lambda key: originals[key])
    seen = []
    def view(record):
        seen.append(record.content)
        return None if record is originals["hidden"] else Record("允许导语…会员文章已锁定")
    records = intelligence.load_visible_platform_messages("600519", view)
    result = snapshot(platform_messages=records)
    assert len(seen) == 2
    assert result["evidence"][0]["summary"] == "允许导语…会员文章已锁定"
    assert "隐私段落" not in str(result) and "来源禁止内容" not in str(result)


@pytest.mark.parametrize("symbol,market,expected", [("600519.SH", "", ("600519", "CN")), ("700", "HK", ("00700", "HK")), ("BRK.B", "US", ("BRK.B", "US")), ("AB12", "US", ("AB12", "US"))])
def test_market_identity(symbol, market, expected):
    assert intelligence.normalize_instrument(symbol, market) == expected


@pytest.fixture
def client(tmp_path, monkeypatch):
    from deepfocus_api import auth, storage
    asyncio.set_event_loop(asyncio.new_event_loop())
    monkeypatch.setenv("DEEPFOCUS_JWT_SECRET", "stock-intelligence-test-secret")
    monkeypatch.setenv("DEEPFOCUS_DATABASE_URL", f"sqlite:///{tmp_path / 'auth.sqlite3'}")
    monkeypatch.delenv("DEEPFOCUS_AUTH_REQUIRED", raising=False)
    storage.reset_engine_for_tests()
    storage.Base.metadata.create_all(storage.get_engine())
    from deepfocus_api.main import app
    user = auth.create_user("intelligence@test.local", "intelligence-test", "password1", role="analyst")
    token = auth.create_access_token(user, auth.rotate_session(user.id))
    yield TestClient(app), {"Authorization": f"Bearer {token}"}
    storage.reset_engine_for_tests()


def test_endpoint_auth_symbol_and_response_contract(client, monkeypatch):
    cli, headers = client
    called = []
    async def gather(symbol, **kwargs):
        called.append((symbol, kwargs))
        assert callable(kwargs["message_view"])
        return snapshot()
    monkeypatch.setattr(intelligence, "gather_stock_intelligence", gather)
    assert cli.get("/api/stock/intelligence?symbol=600519").status_code in (401, 403)
    assert not called
    assert cli.get("/api/stock/intelligence?symbol=600519&market=US", headers=headers).status_code == 400
    response = cli.get("/api/stock/intelligence?symbol=600519.SH&name=茅台&refresh=true", headers=headers)
    assert response.status_code == 200
    assert response.json()["data"]["symbol"] == "600519"
    assert called[0][0] == "600519" and called[0][1]["refresh"] is True


def test_all_upstreams_fail_without_invented_prices(monkeypatch):
    from deepfocus_api import (cn_consensus, consensus_source, eastmoney_data, market_data,
                               nasdaq_data, stock_news, valuation_source)
    async def failure(*args, **kwargs):
        raise RuntimeError("test upstream unavailable")
    for module, funcs in [
        (eastmoney_data, ["fetch_eastmoney_earnings", "fetch_eastmoney_kline", "fetch_sina_ohlc", "fetch_eastmoney_cashflow", "fetch_eastmoney_balance", "fetch_fund_flow"]),
        (market_data, ["fetch_market_quotes"]), (stock_news, ["fetch_stock_news"]), (valuation_source, ["fetch_valuation"]),
        (cn_consensus, ["fetch_cn_consensus"]), (consensus_source, ["fetch_analyst_consensus"]), (nasdaq_data, ["fetch_nasdaq_earnings"]),
        (intelligence, ["fetch_company_profile", "fetch_annual_forecasts", "fetch_verified_fund_flow"]),
    ]:
        for func in funcs:
            monkeypatch.setattr(module, func, failure)
    monkeypatch.setattr(intelligence, "load_visible_platform_messages", lambda *a, **k: [])
    result = asyncio.run(intelligence.gather_stock_intelligence("600519", message_view=lambda x: x))
    assert result["quote"]["price"] is None
    assert all(s["price"] is None for s in result["valuation"]["scenarios"])
    assert result["summary"]["coverage"] == {"available": 0, "total": 6}


@pytest.mark.parametrize("supports_force_fresh", [False, True])
def test_refresh_history_supports_production_legacy_signature_and_retains_real_daily_close(monkeypatch, supports_force_fresh):
    from deepfocus_api import cn_consensus, eastmoney_data, market_data, stock_news, valuation_source
    async def empty(*args, **kwargs):
        return None
    for module, funcs in [
        (eastmoney_data, ["fetch_eastmoney_earnings", "fetch_sina_ohlc", "fetch_eastmoney_cashflow", "fetch_eastmoney_balance"]),
        (market_data, ["fetch_market_quotes"]), (stock_news, ["fetch_stock_news"]),
        (valuation_source, ["fetch_valuation"]), (cn_consensus, ["fetch_cn_consensus"]),
        (intelligence, ["fetch_company_profile", "fetch_annual_forecasts", "fetch_verified_fund_flow"]),
    ]:
        for function in funcs:
            monkeypatch.setattr(module, function, empty)
    today = datetime.now(intelligence._BJ).date().isoformat()
    calls = []
    async def legacy_history(secid: str, points: int = 160):
        calls.append({"secid": secid, "points": points})
        return [(today, 123.45)]
    async def current_history(secid: str, points: int = 160, *, force_fresh: bool = False):
        calls.append({"secid": secid, "points": points, "force_fresh": force_fresh})
        return [(today, 123.45)]
    monkeypatch.setattr(eastmoney_data, "fetch_eastmoney_kline", current_history if supports_force_fresh else legacy_history)
    monkeypatch.setattr(intelligence, "load_visible_platform_messages", lambda *args: [])
    result = asyncio.run(intelligence.gather_stock_intelligence("600519", refresh=True, message_view=lambda message: message))
    assert len(calls) == 1 and calls[0]["secid"] == "1.600519" and calls[0]["points"] == 280
    assert calls[0].get("force_fresh") is (True if supports_force_fresh else None)
    assert result["technical"]["series"] == [{"date": today, "close": 123.45}]
    assert result["quote"]["price"] == 123.45 and result["quote"]["as_of"] == today
    assert "非实时" in result["quote"]["source"]


@pytest.fixture
def public_history_stubs(monkeypatch):
    from deepfocus_api import cn_consensus, eastmoney_data, market_data, stock_news, valuation_source
    async def empty(*args, **kwargs):
        return None
    for module, functions in [
        (eastmoney_data, ["fetch_eastmoney_earnings", "fetch_eastmoney_kline", "fetch_sina_ohlc", "fetch_eastmoney_cashflow", "fetch_eastmoney_balance"]),
        (market_data, ["fetch_market_quotes"]), (stock_news, ["fetch_stock_news"]),
        (valuation_source, ["fetch_valuation"]), (cn_consensus, ["fetch_cn_consensus"]),
        (intelligence, ["fetch_company_profile", "fetch_annual_forecasts", "fetch_verified_fund_flow", "fetch_dated_broker_ratings"]),
    ]:
        for function in functions:
            monkeypatch.setattr(module, function, empty)
    monkeypatch.setattr(intelligence, "load_visible_platform_messages", lambda *args: [])
    return eastmoney_data


def test_cn_history_prefers_available_sina_with_truthful_source_date_currency_and_no_mixing(public_history_stubs, monkeypatch):
    provider = public_history_stubs
    today = datetime.now(intelligence._BJ).date()
    rows = [{"d": (today - timedelta(days=30 - i)).isoformat(), "c": 100 + i, "o": 100 + i, "h": 102 + i, "l": 99 + i, "v": 500} for i in range(30)]
    calls = []
    async def sina(symbol, points=160):
        calls.append((symbol, points))
        return rows
    async def forbidden_eastmoney(*args, **kwargs):
        pytest.fail("可用新浪主源不应再请求生产断连的东财日线，也不能拼接另一复权序列")
    monkeypatch.setattr(provider, "fetch_sina_ohlc", sina)
    monkeypatch.setattr(provider, "fetch_eastmoney_kline", forbidden_eastmoney)
    result = asyncio.run(intelligence.gather_stock_intelligence("600519", refresh=True, message_view=lambda message: message))
    assert calls == [("600519", 280)]
    assert result["technical"]["series"] == [{"date": row["d"], "close": row["c"]} for row in rows]
    assert result["technical"]["series_currency"] == "CNY"
    assert result["technical"]["as_of"] == rows[-1]["d"]
    assert "未声明复权" in result["technical"]["adjustment"] and "前复权" not in result["technical"]["adjustment"]
    source = next(source for source in result["sources"] if source["id"] == "history")
    assert "新浪财经" in source["label"] and source["as_of"] == rows[-1]["d"]
    assert source["url"] == "https://finance.sina.com.cn/realstock/company/sh600519/nc.shtml"
    assert result["quote"]["price"] == rows[-1]["c"] and "新浪财经" in result["quote"]["source"] and "非实时" in result["quote"]["source"]
    assert any("日线源未声明复权" in gap for gap in result["data_gaps"])
    assert result["capital"]["series"] == [] and result["capital"]["status"] == "insufficient"


@pytest.mark.parametrize("sina_result", [[], [{"d": "2035-01-01", "c": 999}, {"d": "invalid", "c": 300}], "raises"])
def test_cn_history_falls_back_to_eastmoney_when_sina_has_no_valid_rows(public_history_stubs, monkeypatch, sina_result):
    provider = public_history_stubs
    date = (datetime.now(intelligence._BJ).date() - timedelta(days=1)).isoformat()
    calls = []
    async def sina(*args, **kwargs):
        calls.append("sina")
        if sina_result == "raises":
            raise RuntimeError("test public source unavailable")
        return sina_result
    async def eastmoney(secid, points=160):
        calls.append("eastmoney")
        return [(date, 123.45)]
    monkeypatch.setattr(provider, "fetch_sina_ohlc", sina)
    monkeypatch.setattr(provider, "fetch_eastmoney_kline", eastmoney)
    result = asyncio.run(intelligence.gather_stock_intelligence("600519", message_view=lambda message: message))
    assert calls == ["sina", "eastmoney"]
    assert result["technical"]["series"] == [{"date": date, "close": 123.45}]
    assert result["technical"]["adjustment"] == "前复权" and result["technical"]["series_currency"] == "CNY"
    assert "东方财富" in next(source for source in result["sources"] if source["id"] == "history")["label"]
    assert "东方财富" in result["quote"]["source"]


def test_hk_history_keeps_eastmoney_currency_and_adjustment_without_sina(public_history_stubs, monkeypatch):
    provider = public_history_stubs
    date = (datetime.now(intelligence._BJ).date() - timedelta(days=1)).isoformat()
    async def forbidden_sina(*args, **kwargs):
        pytest.fail("港股不能请求A股新浪接口")
    async def eastmoney(secid, points=160):
        assert secid == "116.00700"
        return [(date, 400)]
    monkeypatch.setattr(provider, "fetch_sina_ohlc", forbidden_sina)
    monkeypatch.setattr(provider, "fetch_eastmoney_kline", eastmoney)
    result = asyncio.run(intelligence.gather_stock_intelligence("00700", market="HK", message_view=lambda message: message))
    assert result["technical"]["series_currency"] == "HKD" and result["technical"]["adjustment"] == "前复权"
    assert result["technical"]["series"] == [{"date": date, "close": 400}]
    assert result["currency"] == "HKD" and "东方财富" in result["quote"]["source"]


def test_negative_forecasts_are_retained_without_positive_sample_bias():
    payload = {"currentYear": 2026, "data": [{"orgCode": str(i), "stockCode": "600519", "publishDate": "2026-10-01", "predictThisYearEps": eps} for i, eps in enumerate([-1, -1, -1, 1, 2])]}
    parsed = intelligence.parse_annual_forecasts(payload, "600519", now=NOW)
    assert parsed["broker_count"] == 5 and parsed["eps_median"] == -1
    result = snapshot(forecasts=parsed)
    assert result["valuation"]["status"] == "sensitivity"
    assert any("非正" in gap for gap in result["data_gaps"])


@pytest.mark.parametrize("provider", ["mock", "demo-public", "synthetic", "sample"])
def test_synthetic_prices_never_enter_scenarios(provider):
    result = snapshot(quote={"price": 100, "provider": provider, "currency": "CNY"})
    assert result["valuation"]["status"] == "insufficient"
    assert result["quote"]["price"] is None
    assert all(s["price"] is None for s in result["valuation"]["scenarios"])


def test_stale_financials_flow_and_news_do_not_determine_current_direction():
    result = snapshot(
        financials={"report_date": "2024-06-30", "profit_yoy": 50}, history=[],
        flow={"main_flow_5d": 1e8, "flow_end_date": "2024-06-30", "as_of": "2026-10-13"},
        platform_messages=[{"id": "old", "title": "2023 年回购已完成", "content": "公告旧事实", "created_at": "2023-01-01"}],
    )
    dim = {d["key"]: d for d in result["dimensions"]}
    assert dim["fundamentals"]["signal"] == dim["capital"]["signal"] == dim["news"]["signal"] == "insufficient"
    assert result["summary"]["stance"] == "信息不足"
    assert not any("回购" in c["event"] for c in result["catalysts"])
    assert result["sources"][5]["as_of"] == "2024-06-30"


def test_endpoint_uses_real_member_teaser_and_source_policy(client, monkeypatch):
    from deepfocus_api.schemas import RealtimeMessageRecord
    cli, headers = client
    visible = RealtimeMessageRecord(id="article", title="会员文章", content="可见导语" * 30 + "会员秘密正文", topic="文章", severity="info", created_at="2026-10-13")
    blocked = visible.model_copy(update={"id": "blocked", "title": "隐藏内容", "source_name": "TradeAlpha", "content": "禁止来源秘密"})
    monkeypatch.setenv("DEEPFOCUS_BLOCK_TRADEALPHA_SOURCES", "1")
    async def gather(symbol, **kwargs):
        views = [kwargs["message_view"](record) for record in [visible, blocked]]
        return snapshot(platform_messages=[record.model_dump(mode="json") for record in views if record is not None])
    monkeypatch.setattr(intelligence, "gather_stock_intelligence", gather)
    response = cli.get("/api/stock/intelligence?symbol=600519", headers=headers)
    assert response.status_code == 200
    data = response.json()["data"]
    assert "会员秘密正文" not in str(data) and "禁止来源秘密" not in str(data)
    assert len(data["evidence"]) == 1
    assert "解锁" in data["evidence"][0]["summary"] or "会员" in data["evidence"][0]["summary"]
