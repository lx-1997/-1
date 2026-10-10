from __future__ import annotations

import json
import math
import time
import uuid
from datetime import datetime
from typing import Any, AsyncIterator, Optional

from fastapi import Request

from .backtest_data import extract_price_series, fetch_historical_ohlcv
from .backtest_engine import (
    calculate_backtest_metrics,
    claim_backtest,
    create_backtest,
    get_backtest,
    update_backtest,
)
from .shared_utils import safe_float, utc_now_iso, profit_factor


def _sse(event_type: str, payload: dict[str, Any], event_id: str | None = None) -> str:
    lines = [f"id: {event_id}" if event_id else None,
             f"event: {event_type}",
             f"data: {json.dumps(payload, ensure_ascii=False)}"]
    return "\n".join(l for l in lines if l) + "\n\n"


DEFAULT_COMMISSION = 0.001
DEFAULT_SLIPPAGE = 0.0005


def _apply_slippage(price: float, direction: int, slippage: float) -> float:
    return price * (1 + direction * slippage)


async def _execute_strategy(
    kind: str, bars: list[dict[str, Any]], params: dict[str, Any],
    initial_capital: float, commission: float, slippage: float,
) -> dict[str, Any]:
    """One cash ledger for every strategy; equity is marked on each bar date."""
    if initial_capital <= 0 or not 0 <= commission < 1 or not 0 <= slippage < 1:
        raise ValueError("capital must be positive; commission and slippage must be in [0, 1)")
    bars = sorted(bars, key=lambda bar: str(bar["date"]))
    closes = extract_price_series(bars)
    cash = float(initial_capital)
    shares = 0.0
    entry_cost = 0.0
    entry_index = 0
    entry_date = ""
    trades: list[dict[str, Any]] = []
    equity_curve = [cash]
    equity_dates = [""]
    lookback = max(1, int(params.get("lookback", 20)))
    window = max(2, int(params.get("window", 50 if kind == "breakout" else 20)))
    holding = max(1, int(params.get("holding_period", 5 if kind == "momentum" else 10)))
    fast_ma = max(1, int(params.get("fast_ma", 20)))
    slow_ma = max(fast_ma + 1, int(params.get("slow_ma", 60)))
    signal_ma = max(1, int(params.get("signal_ma", 9)))
    signal_line = None

    def buy(bar, reason):
        nonlocal cash, shares, entry_cost, entry_index, entry_date
        price = _apply_slippage(float(bar["close"]), 1, slippage)
        entry_cost = cash
        shares = cash / (price * (1 + commission))
        value = shares * price
        fee = value * commission
        cash = max(0.0, cash - value - fee)
        entry_index = i
        entry_date = str(bar["date"])
        trades.append({"date": entry_date, "action": "buy", "price": round(price, 2),
                       "shares": round(shares, 6), "value": round(value, 2),
                       "commission": round(fee, 2), "reason": reason})

    def sell(bar, reason, action="sell"):
        nonlocal cash, shares, entry_cost
        price = _apply_slippage(float(bar["close"]), -1, slippage)
        value = shares * price
        fee = value * commission
        proceeds = value - fee
        trades.append({"date": str(bar["date"]), "action": action, "price": round(price, 2),
                       "shares": round(shares, 6), "value": round(value, 2),
                       "commission": round(fee, 2), "pnl": round(proceeds - entry_cost, 2),
                       "reason": reason, "return_pct": round((proceeds / entry_cost - 1) * 100, 4)})
        cash += proceeds
        shares = entry_cost = 0.0

    for i, bar in enumerate(bars):
        price = float(bar["close"])
        if price <= 0 or not math.isfinite(price):
            raise ValueError("historical closes must be finite and positive")
        should_buy = should_sell = False
        buy_reason = sell_reason = ""
        if kind == "momentum" and i >= lookback and (i - lookback) % holding == 0:
            momentum = price / closes[i - lookback] - 1
            should_buy, should_sell = momentum > 0.05, momentum < -0.05
            buy_reason, sell_reason = f"动量信号 {momentum:.2%}", f"动量反转 {momentum:.2%}"
        elif kind == "mean_reversion" and i >= window:
            values = closes[i - window:i]
            mean = sum(values) / window
            std = math.sqrt(sum((v - mean) ** 2 for v in values) / window) or 1e-9
            z = (price - mean) / std
            should_buy = z < -float(params.get("entry_z", 2.0))
            held_days = (datetime.strptime(str(bar["date"]), "%Y-%m-%d") - datetime.strptime(entry_date, "%Y-%m-%d")).days if shares else 0
            should_sell = shares > 0 and held_days >= holding and (z > float(params.get("exit_z", 0.5)) or z > -0.3)
            buy_reason, sell_reason = f"超卖 Z={z:.1f}", f"回归均值 Z={z:.1f}"
        elif kind == "trend_following" and i >= slow_ma:
            macd = _ema(closes[:i + 1], fast_ma) - _ema(closes[:i + 1], slow_ma)
            signal_line = macd if signal_line is None else _ema_last(signal_line, macd, signal_ma)
            diff = macd - signal_line
            should_buy, should_sell = diff > 0 and macd > 0, diff < 0
            buy_reason, sell_reason = f"MACD金叉 diff={diff:.2f}", f"MACD死叉 diff={diff:.2f}"
        elif kind == "breakout" and i >= window:
            history = bars[i - window:i]
            high = max(float(b["high"]) for b in history)
            low = min(float(b["low"]) for b in history)
            avg_volume = sum(float(b.get("volume", 0)) for b in history) / window
            should_buy = price > high and float(bar.get("volume", 0)) > avg_volume * float(params.get("volume_mult", 1.5))
            should_sell = price < low or i - entry_index >= holding
            buy_reason = f"突破{window}日高点"
            sell_reason = "跌破支撑" if price < low else f"持仓{holding}日退出"
        if shares and should_sell:
            sell(bar, sell_reason)
        elif not shares and should_buy and cash > 0:
            buy(bar, buy_reason)
        equity_curve.append(round(cash + shares * price, 2))
        equity_dates.append(str(bar["date"]))
    if shares:
        sell(bars[-1], "回测结束平仓", "sell_exit")
        equity_curve[-1] = round(cash, 2)
    return {"trades": trades, "equity_curve": equity_curve, "equity_dates": equity_dates,
            "final_equity": round(cash, 2)}


async def _execute_momentum_strategy(bars, params, initial_capital=100000, commission=DEFAULT_COMMISSION, slippage=DEFAULT_SLIPPAGE):
    return await _execute_strategy("momentum", bars, params, initial_capital, commission, slippage)


async def _execute_mean_reversion_strategy(bars, params, initial_capital=100000, commission=DEFAULT_COMMISSION, slippage=DEFAULT_SLIPPAGE):
    return await _execute_strategy("mean_reversion", bars, params, initial_capital, commission, slippage)


async def _execute_trend_following_strategy(bars, params, initial_capital=100000, commission=DEFAULT_COMMISSION, slippage=DEFAULT_SLIPPAGE):
    return await _execute_strategy("trend_following", bars, params, initial_capital, commission, slippage)


async def _execute_breakout_strategy(bars, params, initial_capital=100000, commission=DEFAULT_COMMISSION, slippage=DEFAULT_SLIPPAGE):
    return await _execute_strategy("breakout", bars, params, initial_capital, commission, slippage)


def _combine_equity_curves(results: dict[str, dict[str, Any]], initial_capital: float, allocations: int) -> tuple[list[str], list[float]]:
    """Calendar union with carry-forward marks; missing sleeves remain cash."""
    dates = sorted({date for result in results.values() for date in result.get("equity_dates", []) if date})
    allocation = initial_capital / max(allocations, 1)
    marks = {symbol: allocation for symbol in results}
    curves = {symbol: dict(zip(result.get("equity_dates", []), result["equity_curve"])) for symbol, result in results.items()}
    output = [float(initial_capital)]
    for date in dates:
        for symbol, curve in curves.items():
            if date in curve:
                marks[symbol] = curve[date]
        output.append(round(sum(marks.values()) + allocation * max(0, allocations - len(results)), 2))
    return [""] + dates, output


def _trade_metrics(metrics, trades):
    closed = [float(t["pnl"]) for t in trades if "pnl" in t]
    gains = sum(pnl for pnl in closed if pnl > 0)
    losses = -sum(pnl for pnl in closed if pnl < 0)
    metrics.update(total_trades=len(closed), win_rate=round(100 * sum(pnl > 0 for pnl in closed) / len(closed), 1) if closed else 0,
                   profit_factor=profit_factor(gains, losses),
                   avg_trade_return=round(sum(float(t.get("return_pct", 0)) for t in trades if "pnl" in t) / len(closed), 2) if closed else 0)
    return metrics

def _ema(values: list[float], period: int) -> float:
    if not values:
        return 0
    if len(values) <= period:
        return sum(values) / len(values)
    multiplier = 2 / (period + 1)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = (v - ema) * multiplier + ema
    return ema


def _ema_last(prev_ema: float, current_val: float, period: int) -> float:
    multiplier = 2 / (period + 1)
    return (current_val - prev_ema) * multiplier + prev_ema


STRATEGY_RUNNERS = {
    "momentum": _execute_momentum_strategy,
    "mean_reversion": _execute_mean_reversion_strategy,
    "trend_following": _execute_trend_following_strategy,
    "breakout": _execute_breakout_strategy,
}


async def _run_backtest_impl(
    backtest_id: str,
    request: Optional[Request] = None,
) -> AsyncIterator[str]:
    bt = get_backtest(backtest_id)
    if not bt:
        yield _sse("bt_error", {"backtest_id": backtest_id, "error": "backtest not found"})
        return

    syms = bt.get("symbols", [])
    if not syms:
        yield _sse("bt_error", {"backtest_id": backtest_id, "error": "no symbols configured"})
        update_backtest(backtest_id, status="failed", error="no symbols", progress=0)
        return

    update_backtest(backtest_id, status="running", progress=5)
    yield _sse("bt_start", {
        "backtest_id": backtest_id, "name": bt["name"],
        "strategy": bt["strategy_type"], "symbols": syms,
        "start_date": bt["start_date"], "end_date": bt["end_date"],
        "initial_capital": bt["initial_capital"],
        "generated_at": utc_now_iso(),
    })

    params = bt.get("parameters", {})
    commission = float(params.get("commission", DEFAULT_COMMISSION))
    slippage_val = float(params.get("slippage", DEFAULT_SLIPPAGE))

    all_bars: dict[str, list[dict[str, Any]]] = {}
    _sources: set[str] = set()
    total_symbols = len(syms)
    for idx, sym in enumerate(syms):
        if request and await request.is_disconnected():
            update_backtest(backtest_id, status="failed", error="client disconnected", progress=0)
            return
        yield _sse("bt_data_fetch", {
            "backtest_id": backtest_id, "symbol": sym.upper(),
            "detail": f"获取 {sym.upper()} 历史数据...", "status": "working",
        })
        try:
            data = await fetch_historical_ohlcv(sym, bt["start_date"], bt["end_date"])
            bars = data.get("bars", [])
            if not bars:
                raise ValueError("no historical bars available")
            all_bars[sym.upper()] = bars
            _sources.add(str(data.get("source", "")))
            yield _sse("bt_data_fetch", {
                "backtest_id": backtest_id, "symbol": sym.upper(),
                "detail": f"{sym.upper()}: {len(bars)} 根K线 | 来源:{data.get('source', 'unknown')}",
                "total_bars": len(bars), "source": data.get("source"), "status": "done",
            })
        except Exception as e:
            yield _sse("bt_data_fetch", {
                "backtest_id": backtest_id, "symbol": sym.upper(),
                "detail": f"获取失败: {str(e)[:50]}", "status": "error",
            })
        progress = 5 + int(15 * (idx + 1) / max(total_symbols, 1))
        update_backtest(backtest_id, progress=progress)

    if not all_bars:
        update_backtest(backtest_id, status="failed", error="no historical data available", progress=0)
        yield _sse("bt_error", {"backtest_id": backtest_id, "error": "no historical data available"})
        return

    update_backtest(backtest_id, progress=25)
    strategy_type = bt.get("strategy_type", "momentum")
    runner = STRATEGY_RUNNERS.get(strategy_type, _execute_momentum_strategy)
    symbol_results: dict[str, dict[str, Any]] = {}
    all_trades_log: list[dict[str, Any]] = []

    for idx, (sym, bars) in enumerate(all_bars.items()):
        if request and await request.is_disconnected():
            update_backtest(backtest_id, status="failed", error="client disconnected", progress=0)
            return
        yield _sse("bt_execute", {
            "backtest_id": backtest_id, "symbol": sym,
            "detail": f"执行 {sym} {strategy_type} 策略回测...", "status": "working",
        })

        bt_start = time.time()
        try:
            result = await runner(
                bars, params,
                initial_capital=bt["initial_capital"] / max(len(all_bars), 1),
                commission=commission, slippage=slippage_val,
            )
        except Exception as e:
            yield _sse("bt_execute", {
                "backtest_id": backtest_id, "symbol": sym,
                "detail": f"执行失败: {str(e)[:50]}", "status": "error",
            })
            continue

        trades_log = result.get("trades", [])
        eq_curve = result.get("equity_curve", [bt["initial_capital"]])

        metrics = _trade_metrics(calculate_backtest_metrics(eq_curve, initial_capital=bt["initial_capital"] / max(len(all_bars), 1)), trades_log)

        symbol_results[sym] = {
            "metrics": metrics,
            "trades": len(trades_log),
            "equity_curve": eq_curve,
            "equity_dates": result.get("equity_dates", []),
            "final_equity": result.get("final_equity", 0),
        }
        all_trades_log.extend({**trade, "symbol": sym} for trade in trades_log)

        yield _sse("bt_execute", {
            "backtest_id": backtest_id, "symbol": sym,
            "detail": f"{sym}: {len(trades_log)}笔交易 收益{metrics['total_return_pct']:+.1f}% "
                      f"夏普{metrics['sharpe_ratio']:.2f} 回撤{metrics['max_drawdown_pct']:.1f}%",
            "trades": len(trades_log),
            "return_pct": metrics["total_return_pct"],
            "sharpe": metrics["sharpe_ratio"],
            "max_dd_pct": metrics["max_drawdown_pct"],
            "win_rate": metrics["win_rate"],
            "elapsed_ms": round((time.time() - bt_start) * 1000),
            "status": "done",
        })

        progress = 25 + int(65 * (idx + 1) / len(all_bars))
        update_backtest(backtest_id, progress=progress)

    combined_dates, combined_curve = _combine_equity_curves(symbol_results, bt["initial_capital"], len(all_bars))
    if not symbol_results:
        update_backtest(backtest_id, status="failed", error="all strategies failed", progress=0)
        yield _sse("bt_error", {"backtest_id": backtest_id, "error": "all strategies failed"})
        return
    final_metrics = _trade_metrics(calculate_backtest_metrics(combined_curve, initial_capital=bt["initial_capital"]), all_trades_log)
    all_trades_log.sort(key=lambda t: t.get("date", ""))

    result_payload = {
        "metrics": final_metrics,
        "total_trades": len(all_trades_log),
        "equity_curve": combined_curve,
        "equity_dates": combined_dates,
        "symbol_results": symbol_results,
        "trades_log": all_trades_log[:50],
        "commission": commission,
        "slippage": slippage_val,
        "data_sources": "yfinance" if "yfinance" in _sources else ("synthetic" if _sources else "unknown"),
    }

    update_backtest(backtest_id, status="completed", progress=100, result=result_payload)
    try:
        yield _sse("bt_result", {
            "backtest_id": backtest_id, "status": "completed",
            "metrics": final_metrics,
            "total_trades": len(all_trades_log),
            "equity_curve": combined_curve,
            "equity_dates": combined_dates,
            "symbol_results": {s: {"metrics": r["metrics"], "trades": r["trades"]} for s, r in symbol_results.items()},
        })
        yield _sse("bt_done", {"backtest_id": backtest_id, "total_trades": len(all_trades_log),
                               "symbols": list(all_bars)})
    except Exception:
        pass


async def run_backtest(backtest_id: str, request: Optional[Request] = None) -> AsyncIterator[str]:
    current = get_backtest(backtest_id)
    if current and current.get("status") == "completed" and current.get("result"):
        yield _sse("bt_result", {"backtest_id": backtest_id, "status": "completed", **current["result"]})
        yield _sse("bt_done", {"backtest_id": backtest_id, "cached": True})
        return
    if not claim_backtest(backtest_id):
        yield _sse("bt_error", {"backtest_id": backtest_id, "error": "backtest is already running or unavailable"})
        return
    try:
        async for event in _run_backtest_impl(backtest_id, request):
            yield event
    finally:
        # Only the generator which acquired the claim can terminate this run.
        current = get_backtest(backtest_id)
        if current and current.get("status") == "running":
            update_backtest(backtest_id, status="failed", error="execution interrupted", progress=0)


async def list_backtest_results(symbol: str, *, owner_user_id: Optional[str] = None, is_admin: bool = False) -> dict[str, Any]:
    from .backtest_engine import list_backtests as lb
    all_bt = lb(limit=100, owner_user_id=owner_user_id, is_admin=is_admin)
    matched = [b for b in all_bt if symbol.upper() in [s.upper() for s in b.get("symbols", [])]]
    return {"backtests": matched, "symbol": symbol.upper(), "total": len(matched)}


def _detect_data_source(all_bars: dict[str, list[dict[str, Any]]]) -> str:
    seen = set()
    for bars_list in all_bars.values():
        if bars_list and isinstance(bars_list, list):
            for bar in bars_list:
                if isinstance(bar, dict):
                    seen.add(bar.get("source", ""))
                    break
    return "yfinance" if "yfinance" in seen else "synthetic" if seen else "unknown"
