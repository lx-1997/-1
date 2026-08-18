from datetime import date, timedelta

from deepfocus_api.quant_strategy_engine import run_strategy_backtest


def _bars(start: float, step: float, count: int = 100) -> list[dict]:
    first = date(2026, 1, 1)
    rows = []
    for index in range(count):
        close = max(1.0, start + step * index)
        rows.append({
            "date": (first + timedelta(days=index)).isoformat(),
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1_000_000,
        })
    return rows


def _payload(bars: list[dict], market: str = "US") -> dict:
    return {"bars": bars, "market": market, "source": "verified_test", "source_name": "test", "is_synthetic": False}


def _run(*, allow_short: bool, commission_bps: float = 3, slippage_bps: float = 5, short_borrow_bps: float = 100):
    all_data = {
        "UP": _payload(_bars(50, 0.8)),
        "DOWN": _payload(_bars(200, -1.2)),
        "SPY": _payload(_bars(100, 0.1)),
    }
    return run_strategy_backtest(
        name="engine", market="US", symbols=["UP", "DOWN"], benchmark="SPY", all_data=all_data,
        initial_capital=100_000, strategy_key="momentum", lookback=20, top_n=2,
        allow_short=allow_short,
        rules={"max_position_size_pct": 50, "max_total_exposure_pct": 100, "max_short_exposure_pct": 30, "max_sector_exposure_pct": 100, "max_drawdown_pct": 80, "daily_loss_limit_pct": 80, "stop_loss_pct": 0, "take_profit_pct": 0, "cooldown_days": 0, "allow_reentry": True},
        sector_map={}, rebalance_frequency="weekly", commission_bps=commission_bps,
        slippage_bps=slippage_bps, short_borrow_bps=short_borrow_bps, min_trade_notional=0,
    )


def test_short_toggle_changes_actual_trades_and_costs():
    long_only = _run(allow_short=False)
    long_short = _run(allow_short=True)

    assert not any(trade["action"] == "short" for trade in long_only["trades_log"])
    assert any(trade["action"] == "short" and trade["symbol"] == "DOWN" for trade in long_short["trades_log"])
    assert long_short["metrics"]["execution"]["borrow_cost"] > 0
    assert long_short["metrics"]["attribution"]["final_gross_exposure_pct"] == 0
    assert long_short["metrics"]["risk"]["total_trades"] == len(long_short["trades_log"])
    assert "positive_day_rate_pct" in long_short["metrics"]["risk"]


def test_transaction_costs_reduce_equity():
    no_cost = _run(allow_short=True, commission_bps=0, slippage_bps=0, short_borrow_bps=0)
    with_cost = _run(allow_short=True, commission_bps=10, slippage_bps=15, short_borrow_bps=300)

    assert with_cost["metrics"]["execution"]["transaction_cost"] > 0
    assert with_cost["equity_curve"][-1] < no_cost["equity_curve"][-1]


def test_signal_uses_prior_close_without_same_day_lookahead():
    bars = _bars(100, 0, 75)
    jump_index = 60
    bars[jump_index].update({"open": 200, "high": 200, "low": 200, "close": 200})
    for index in range(jump_index + 1, len(bars)):
        bars[index].update({"open": 200, "high": 200, "low": 200, "close": 200})
    all_data = {"JUMP": _payload(bars), "SPY": _payload(_bars(100, 0, 75))}
    result = run_strategy_backtest(
        name="lookahead", market="US", symbols=["JUMP"], benchmark="SPY", all_data=all_data,
        initial_capital=100_000, strategy_key="momentum", lookback=20, top_n=1, allow_short=False,
        rules={"max_position_size_pct": 100, "max_total_exposure_pct": 100, "max_short_exposure_pct": 0, "max_sector_exposure_pct": 100, "max_drawdown_pct": 100, "daily_loss_limit_pct": 100, "stop_loss_pct": 0, "take_profit_pct": 0, "cooldown_days": 0, "allow_reentry": True},
        sector_map={}, rebalance_frequency="daily", commission_bps=0, slippage_bps=0,
        short_borrow_bps=0, min_trade_notional=0,
    )

    jump_date = bars[jump_index]["date"]
    first_buy = next(trade for trade in result["trades_log"] if trade["action"] == "buy")
    assert first_buy["date"] > jump_date
