from datetime import date, timedelta

import pytest

from deepfocus_api import backtest_executor as executor


def bars(prices):
    return [{'date': (date(2026, 1, 1) + timedelta(days=i)).isoformat(),
             'close': price, 'high': price, 'low': price, 'volume': 100 if i < 2 else 500}
            for i, price in enumerate(prices)]


@pytest.mark.asyncio
@pytest.mark.parametrize('runner,prices,params', [
    ('momentum', [100, 100, 110, 110], {'lookback': 1, 'holding_period': 1}),
    ('mean_reversion', [100, 100, 50, 50, 100, 100], {'window': 2, 'entry_z': 1, 'holding_period': 1}),
    ('trend_following', [100, 101, 104, 109, 116, 125, 136], {'fast_ma': 1, 'slow_ma': 3, 'signal_ma': 2}),
    ('breakout', [100, 100, 110, 110, 110], {'window': 2, 'holding_period': 10, 'volume_mult': 1.5}),
])
async def test_all_strategies_balance_cash_and_closed_pnl(runner, prices, params):
    result = await executor.STRATEGY_RUNNERS[runner](bars(prices), params, initial_capital=1000, commission=.01, slippage=0)
    buys = [trade for trade in result['trades'] if trade['action'] == 'buy']
    sells = [trade for trade in result['trades'] if trade['action'].startswith('sell')]
    assert buys and len(buys) == len(sells)
    assert all(trade['commission'] > 0 for trade in result['trades'])
    assert sum(trade['pnl'] for trade in sells) == pytest.approx(result['final_equity'] - 1000, abs=.02)
    assert len(result['equity_curve']) == len(result['equity_dates']) == len(prices) + 1
    assert result['equity_curve'][-1] == result['final_equity']
    for buy in buys:
        assert buy['value'] + buy['commission'] <= 1000.02 or len(buys) > 1


@pytest.mark.asyncio
async def test_flat_position_loses_both_entry_and_exit_commissions():
    result = await executor._execute_momentum_strategy(bars([100, 110, 110]), {'lookback': 1, 'holding_period': 1}, 1000, .01, 0)
    expected = 1000 * .99 / 1.01
    assert result['final_equity'] == pytest.approx(expected, abs=.005)
    assert result['trades'][-1]['pnl'] == pytest.approx(expected - 1000, abs=.005)
    assert result['trades'][-1]['action'] == 'sell_exit'


def test_portfolio_curve_uses_dates_and_carries_forward_cash():
    results = {
        'A': {'equity_dates': ['', '2026-01-01', '2026-01-03'], 'equity_curve': [500, 400, 700]},
        'B': {'equity_dates': ['', '2026-01-02', '2026-01-03'], 'equity_curve': [500, 600, 550]},
    }
    dates, curve = executor._combine_equity_curves(results, 1000, 2)
    assert dates == ['', '2026-01-01', '2026-01-02', '2026-01-03']
    assert curve == [1000, 900, 1000, 1250]
    assert executor._combine_equity_curves({'A': results['A']}, 1000, 2)[1][-1] == 1200


@pytest.mark.asyncio
async def test_duplicate_run_cannot_reset_the_first_claim(monkeypatch, tmp_path):
    from deepfocus_api import backtest_engine
    monkeypatch.setattr(backtest_engine, 'DB_PATH', tmp_path / 'backtests.sqlite3')
    bt = backtest_engine.create_backtest(name='test', symbols=['AAPL'], start_date='2026-01-01', end_date='2026-01-31')
    first = executor.run_backtest(bt['id'])
    assert 'bt_start' in await first.__anext__()
    second = [event async for event in executor.run_backtest(bt['id'])]
    assert 'already running' in second[0]
    assert backtest_engine.get_backtest(bt['id'])['status'] == 'running'
    await first.aclose()
    assert backtest_engine.get_backtest(bt['id'])['status'] == 'failed'


@pytest.mark.asyncio
async def test_completed_backtest_reuses_persisted_result(monkeypatch, tmp_path):
    from deepfocus_api import backtest_engine
    monkeypatch.setattr(backtest_engine, 'DB_PATH', tmp_path / 'backtests.sqlite3')
    bt = backtest_engine.create_backtest(name='test', symbols=['AAPL'])
    backtest_engine.update_backtest(bt['id'], status='completed', result={'metrics': {'total_return_pct': 12}})
    events = [event async for event in executor.run_backtest(bt['id'])]
    assert '"total_return_pct": 12' in events[0]
    assert '"cached": true' in events[1]
