import asyncio
from datetime import date, timedelta

import pytest

from deepfocus_api import backtest_data, research_data


def run(tool, data):
    return asyncio.run(research_data.resolve_platform_history(tool, data))


def query(**changes):
    return {"symbols": ["600519.SH", "000001.SZ"], "currency": "CNY",
            "start_date": "2024-01-01", "end_date": "2024-12-31", **changes}


def public_result(**changes):
    return {"quality": "verified_real", "is_synthetic": False, "adjustment": "qfq",
            "source_name": "public provider", "end_date": "2024-12-31", "fetched_at": "2025-01-01",
            "bars": [{"date": "2024-01-02", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100}], **changes}


def test_resolves_real_sources_and_preserves_weights(monkeypatch):
    fetched = []
    async def fetch(symbol, start, end):
        fetched.append((symbol, start, end))
        return public_result()
    monkeypatch.setattr(backtest_data, "fetch_historical_ohlcv", fetch)
    data, sources, warnings = run("portfolio", query(weights=[{"symbol": "600519.sh", "weight": .6}]))
    assert len(fetched) == len(data["prices"]) == len(sources) == 2
    assert data["weights"][0]["symbol"] == "600519.SH"
    assert all(s["currency"] == "CNY" and "qfq" in s["coverage"] for s in sources)
    assert all(p["adjustment"] == "qfq" and p["currency"] == "CNY" for p in data["prices"])
    assert not warnings and "symbols" not in data and "start_date" not in data


@pytest.mark.parametrize("change", [
    {"prices": [{"close": 1}]}, {"file_name": "prices.csv"}, {"symbols": ["AAPL", "600519.SH"]},
    {"symbols": ["AAPL"], "currency": "CNY"}, {"symbols": ["AAPL", "AAPL.US"], "currency": "USD"},
    {"symbols": ["600519.SH", "600519.SH"]}, {"symbols": ["bad/url"]},
    {"start_date": "bad"}, {"start_date": "2030-01-01"}, {"start_date": "2000-01-01"},
])
def test_invalid_query_fails_before_provider(monkeypatch, change):
    async def forbidden(*args):
        pytest.fail("Invalid input must not contact provider")
    monkeypatch.setattr(backtest_data, "fetch_historical_ohlcv", forbidden)
    with pytest.raises(ValueError):
        run("factors", query(**change))


@pytest.mark.parametrize("changes", [{"is_synthetic": True}, {"quality": "unavailable"}, {"bars": []}])
def test_unavailable_or_synthetic_feed_rejected(monkeypatch, changes):
    async def fetch(*args):
        return public_result(**changes)
    monkeypatch.setattr(backtest_data, "fetch_historical_ohlcv", fetch)
    with pytest.raises(RuntimeError, match="真实历史行情"):
        run("portfolio", query())


def test_unclosed_day_removed(monkeypatch):
    ends = []
    async def fetch(symbol, start, end):
        ends.append(end)
        return public_result()
    monkeypatch.setattr(backtest_data, "fetch_historical_ohlcv", fetch)
    _, _, warnings = run("portfolio", query(end_date="2030-01-01"))
    assert set(ends) == {(date.today() - timedelta(days=1)).isoformat()}
    assert any("未收盘" in w for w in warnings)


@pytest.mark.parametrize("adjustment", [None, "none", "raw", "unknown", "provider_reported"])
def test_real_but_unverified_adjustment_is_rejected(monkeypatch, adjustment):
    async def fetch(*args):
        return public_result(adjustment=adjustment)
    monkeypatch.setattr(backtest_data, "fetch_historical_ohlcv", fetch)
    with pytest.raises(ValueError, match="已复权"):
        run("portfolio", query())


def test_different_verified_adjustment_methods_cannot_mix(monkeypatch):
    async def fetch(symbol, *args):
        return public_result(adjustment="qfq" if symbol == "600519.SH" else "hfq")
    monkeypatch.setattr(backtest_data, "fetch_historical_ohlcv", fetch)
    with pytest.raises(ValueError, match="同口径"):
        run("factors", query())


def test_provider_concurrency_is_bounded(monkeypatch):
    active, maximum = 0, 0
    async def fetch(*args):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(.01)
        active -= 1
        return public_result()
    monkeypatch.setattr(backtest_data, "fetch_historical_ohlcv", fetch)
    run("factors", query(symbols=["600519.SH", "000001.SZ", "000002.SZ", "600036.SH", "601318.SH"]))
    assert maximum == 2


def test_other_tools_do_not_contact_history_provider():
    input_data = {"symbols": ["AAPL"]}
    assert run("financials", input_data) == (input_data, [], [])
