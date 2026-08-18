import pytest

from deepfocus_api import backtest_data


def _bars():
    return [
        {"date": "2026-01-02", "open": 100, "high": 102, "low": 99, "close": 101, "volume": 1000},
        {"date": "2026-01-05", "open": 101, "high": 103, "low": 100, "close": 102, "volume": 1200},
    ]


def test_normalize_multi_market_symbols():
    assert backtest_data.normalize_market_symbol("600519")["provider_symbol"] == "sh600519"
    assert backtest_data.normalize_market_symbol("000001.SZ")["provider_symbol"] == "sz000001"
    assert backtest_data.normalize_market_symbol("700")["provider_symbol"] == "hk00700"
    assert backtest_data.normalize_market_symbol("AAPL")["market"] == "US"
    assert backtest_data.normalize_market_symbol("HSI")["market"] == "HK"


@pytest.mark.asyncio
async def test_fetch_uses_verified_provider_and_success_cache(monkeypatch):
    calls = 0

    async def fake_provider(meta, start_date, end_date):
        nonlocal calls
        calls += 1
        return {"bars": _bars(), "source": "verified_test", "source_name": "test", "adjustment": "qfq", "is_synthetic": False}

    backtest_data._cache.clear()
    monkeypatch.setattr(backtest_data, "_fetch_nasdaq_us", fake_provider)
    first = await backtest_data.fetch_historical_ohlcv("AAPL", "2026-01-01", "2026-01-10")
    second = await backtest_data.fetch_historical_ohlcv("AAPL", "2026-01-01", "2026-01-10")

    assert first["source"] == "verified_test"
    assert first["quality"] == "verified_real"
    assert first["is_synthetic"] is False
    assert second["cached"] is True
    assert calls == 1


@pytest.mark.asyncio
async def test_fetch_never_fabricates_when_provider_fails(monkeypatch):
    async def failed_provider(meta, start_date, end_date):
        raise TimeoutError("数据源超时")

    backtest_data._cache.clear()
    monkeypatch.setattr(backtest_data, "_fetch_nasdaq_us", failed_provider)
    result = await backtest_data.fetch_historical_ohlcv("AAPL", "2026-01-01", "2026-01-10")

    assert result["source"] == "unavailable"
    assert result["bars"] == []
    assert result["is_synthetic"] is False
    assert "TimeoutError" in result["error"]
