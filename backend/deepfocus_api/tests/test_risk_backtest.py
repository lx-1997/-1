import pytest

from deepfocus_api.risk_backtest import run_risk_backtest
from deepfocus_api.schemas import RiskBacktestRequest, RiskBacktestRuleSet


def _bars(prices):
    return [
        {
            "date": f"2026-01-0{idx + 1}",
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": 1_000_000,
        }
        for idx, price in enumerate(prices)
    ]


@pytest.mark.asyncio
async def test_risk_backtest_triggers_stop_loss(monkeypatch):
    async def fake_fetch(symbol, start_date, end_date, interval="1d"):
        data = {
            "AAA": _bars([100, 94, 90, 88, 86]),
            "SPY": _bars([100, 101, 102, 103, 104]),
        }
        return {"symbol": symbol, "bars": data[symbol], "source": "mock", "total_bars": len(data[symbol]), "interval": interval}

    monkeypatch.setattr("deepfocus_api.risk_backtest.fetch_historical_ohlcv", fake_fetch)

    request = RiskBacktestRequest(
        name="stop-loss",
        market="US",
        symbols=["AAA"],
        start_date="2026-01-01",
        end_date="2026-01-05",
        initial_capital=100000,
        benchmark="SPY",
        rules=RiskBacktestRuleSet(
            max_position_size_pct=100,
            max_total_exposure_pct=100,
            max_sector_exposure_pct=100,
            max_drawdown_pct=50,
            daily_loss_limit_pct=50,
            stop_loss_pct=5,
            take_profit_pct=50,
            cooldown_days=0,
            allow_reentry=False,
        ),
        sector_map={"AAA": "Tech"},
    )

    result = await run_risk_backtest(request)
    assert result["metrics"]["rule_hits"]["stop_loss"] >= 1
    assert result["metrics"]["risk"]["max_drawdown_pct"] <= result["metrics"]["baseline"]["max_drawdown_pct"]
    assert len(result["equity_curve"]) == len(result["dates"])


@pytest.mark.asyncio
async def test_risk_backtest_circuit_breaker_on_daily_loss(monkeypatch):
    async def fake_fetch(symbol, start_date, end_date, interval="1d"):
        data = {
            "AAA": _bars([100, 84, 82, 81, 80]),
            "SPY": _bars([100, 99, 98, 97, 96]),
        }
        return {"symbol": symbol, "bars": data[symbol], "source": "mock", "total_bars": len(data[symbol]), "interval": interval}

    monkeypatch.setattr("deepfocus_api.risk_backtest.fetch_historical_ohlcv", fake_fetch)

    request = RiskBacktestRequest(
        name="daily-loss",
        market="US",
        symbols=["AAA"],
        start_date="2026-01-01",
        end_date="2026-01-05",
        initial_capital=100000,
        benchmark="SPY",
        rules=RiskBacktestRuleSet(
            max_position_size_pct=100,
            max_total_exposure_pct=100,
            max_sector_exposure_pct=100,
            max_drawdown_pct=50,
            daily_loss_limit_pct=5,
            stop_loss_pct=50,
            take_profit_pct=50,
            cooldown_days=0,
            allow_reentry=False,
        ),
        sector_map={"AAA": "Tech"},
    )

    result = await run_risk_backtest(request)
    assert result["metrics"]["rule_hits"]["daily_loss"] >= 1
    assert any(event["type"] == "circuit_breaker" for event in result["events"])
    assert result["metrics"]["risk"]["total_return_pct"] >= result["metrics"]["baseline"]["total_return_pct"]
