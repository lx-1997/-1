from datetime import date, timedelta

import pytest

from deepfocus_api.quant_lab import run_quant_lab
from deepfocus_api.schemas import QuantLabRequest, RiskBacktestRuleSet


def _bars(start: float, daily_step: float, count: int = 90) -> list[dict]:
    first_day = date(2026, 1, 1)
    return [
        {
            "date": (first_day + timedelta(days=index)).isoformat(),
            "open": start + daily_step * index,
            "high": start + daily_step * index,
            "low": start + daily_step * index,
            "close": start + daily_step * index,
            "volume": 1_000_000 + index * 1_000,
        }
        for index in range(count)
    ]


@pytest.mark.asyncio
async def test_quant_lab_connects_signals_orders_and_risk_backtest(monkeypatch):
    market_data = {
        "AAA": _bars(100, 0.8),
        "BBB": _bars(100, -0.2),
        "SPY": _bars(100, 0.2),
    }

    async def fake_fetch(symbol, start_date, end_date, interval="1d"):
        bars = market_data[symbol]
        return {
            "symbol": symbol,
            "bars": bars,
            "source": "mock",
            "total_bars": len(bars),
            "interval": interval,
        }

    monkeypatch.setattr("deepfocus_api.quant_lab.fetch_historical_ohlcv", fake_fetch)
    monkeypatch.setattr("deepfocus_api.risk_backtest.fetch_historical_ohlcv", fake_fetch)
    monkeypatch.setattr(
        "deepfocus_api.quant_lab.get_risk_summary",
        lambda: {
            "portfolio": {"total_value": 100_000, "open_count": 1, "total_pnl_pct": 3.2},
            "open_positions": [{"symbol": "BBB", "position_size_pct": 25}],
        },
    )

    request = QuantLabRequest(
        name="QuantLab smoke",
        market="US",
        strategy_key="momentum",
        symbols=["AAA", "BBB"],
        start_date="2026-01-01",
        end_date="2026-03-31",
        benchmark="SPY",
        initial_capital=100_000,
        lookback=20,
        top_n=1,
        risk_rules=RiskBacktestRuleSet(),
    )

    result = await run_quant_lab(request)

    assert result["strategy_label"] == "动量"
    assert [signal["symbol"] for signal in result["signals"]] == ["AAA", "BBB"]
    assert result["signals"][0]["score"] > result["signals"][1]["score"]
    assert result["orders"][0]["side"] == "buy"
    assert result["orders"][1]["side"] == "sell"
    assert result["portfolio_context"]["current_open_count"] == 1
    assert result["backtest"]["metrics"]["summary"]
    assert len(result["backtest"]["dates"]) == len(result["backtest"]["equity_curve"])
    assert result["data_sources"] == {"AAA": "mock", "BBB": "mock", "SPY": "mock"}
