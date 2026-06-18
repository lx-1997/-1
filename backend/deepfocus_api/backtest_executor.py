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
    create_backtest,
    get_backtest,
    update_backtest,
)
from .shared_utils import safe_float, utc_now_iso


def _sse(event_type: str, payload: dict[str, Any], event_id: str | None = None) -> str:
    lines = [f"id: {event_id}" if event_id else None,
             f"event: {event_type}",
             f"data: {json.dumps(payload, ensure_ascii=False)}"]
    return "\n".join(l for l in lines if l) + "\n\n"


DEFAULT_COMMISSION = 0.001
DEFAULT_SLIPPAGE = 0.0005


def _apply_slippage(price: float, direction: int, slippage: float) -> float:
    return price * (1 + direction * slippage)


async def _execute_momentum_strategy(
    bars: list[dict[str, Any]],
    params: dict[str, Any],
    initial_capital: float = 100000,
    commission: float = DEFAULT_COMMISSION,
    slippage: float = DEFAULT_SLIPPAGE,
) -> dict[str, Any]:
    lookback = int(params.get("lookback", 20))
    top_n = int(params.get("top_n", 1))
    holding_period = int(params.get("holding_period", 5))

    if len(bars) < lookback + holding_period:
        return {"trades": [], "equity_curve": [initial_capital], "error": "insufficient data"}

    equity = initial_capital
    positions: float = 0
    equity_curve = [equity]
    trades: list[dict[str, Any]] = []
    cash = initial_capital

    for i in range(lookback, len(bars) - holding_period, holding_period):
        momentum = bars[i]["close"] / bars[i - lookback]["close"] - 1
        entry_price = bars[i]["close"] * (1 + slippage)

        should_buy = momentum > 0.05
        should_sell = momentum < -0.05

        if should_buy and cash > 0:
            positions = cash / entry_price
            cash = 0
            trades.append({
                "date": bars[i]["date"], "action": "buy", "price": round(entry_price, 2),
                "shares": round(positions, 2), "value": round(positions * entry_price, 2),
                "commission": round(positions * entry_price * commission, 2), "reason": f"动量信号 {momentum:.2%}",
            })

        elif should_sell and positions > 0:
            exit_price = bars[i]["close"] * (1 - slippage)
            cash = positions * exit_price * (1 - commission)
            pnl = cash - equity
            trades.append({
                "date": bars[i]["date"], "action": "sell", "price": round(exit_price, 2),
                "shares": round(positions, 2), "value": round(positions * exit_price, 2),
                "commission": round(positions * exit_price * commission, 2), "pnl": round(pnl, 2),
                "reason": f"动量反转 {momentum:.2%}",
            })
            positions = 0

        equity = cash + positions * bars[i]["close"]
        equity_curve.append(round(equity, 2))

    if positions > 0:
        last_price = bars[-1]["close"] * (1 - slippage)
        cash = positions * last_price * (1 - commission)
        trades.append({
            "date": bars[-1]["date"], "action": "sell_exit", "price": round(last_price, 2),
            "shares": round(positions, 2), "pnl": round(cash + equity_curve[0] - equity_curve[-1], 2),
        })
        equity_curve.append(round(cash, 2))

    return {"trades": trades, "equity_curve": equity_curve, "final_equity": round(equity_curve[-1], 2)}


async def _execute_mean_reversion_strategy(
    bars: list[dict[str, Any]],
    params: dict[str, Any],
    initial_capital: float = 100000,
    commission: float = DEFAULT_COMMISSION,
    slippage: float = DEFAULT_SLIPPAGE,
) -> dict[str, Any]:
    window = int(params.get("window", 20))
    entry_z = float(params.get("entry_z", 2.0))
    exit_z = float(params.get("exit_z", 0.5))
    holding_period = int(params.get("holding_period", 10))

    if len(bars) < window + 10:
        return {"trades": [], "equity_curve": [initial_capital], "error": "insufficient data"}

    closes = extract_price_series(bars)
    equity = initial_capital
    positions: float = 0
    equity_curve = [equity]
    trades: list[dict[str, Any]] = []
    cash = initial_capital
    in_position = False
    entry_date = ""

    for i in range(window, len(bars)):
        window_closes = closes[i - window:i]
        mean = sum(window_closes) / window
        std = math.sqrt(sum((c - mean) ** 2 for c in window_closes) / window) or 1e-9
        z = (closes[i] - mean) / std
        current_price = bars[i]["close"]

        if not in_position and z < -entry_z:
            entry_price = current_price * (1 + slippage)
            positions = cash / entry_price
            cash = 0
            entry_date = bars[i]["date"]
            in_position = True
            trades.append({
                "date": entry_date, "action": "buy", "price": round(entry_price, 2),
                "shares": round(positions, 2), "value": round(positions * entry_price, 2),
                "commission": round(positions * entry_price * commission, 2),
                "reason": f"超卖 Z={z:.1f}",
            })

        elif in_position and (z > exit_z or z > -0.3):
            exit_date_dt = datetime.strptime(bars[i]["date"], "%Y-%m-%d")
            entry_date_dt = datetime.strptime(entry_date, "%Y-%m-%d")
            if (exit_date_dt - entry_date_dt).days < holding_period:
                equity_val = cash + positions * current_price
                equity_curve.append(round(equity_val, 2))
                continue

            exit_price = current_price * (1 - slippage)
            cash = positions * exit_price * (1 - commission)
            pnl = cash - equity
            trades.append({
                "date": bars[i]["date"], "action": "sell", "price": round(exit_price, 2),
                "shares": round(positions, 2), "value": round(positions * exit_price, 2),
                "commission": round(positions * exit_price * commission, 2),
                "pnl": round(pnl, 2), "reason": f"回归均值 Z={z:.1f}",
            })
            positions = 0
            in_position = False

        equity = cash + positions * current_price
        equity_curve.append(round(equity, 2))

    if in_position:
        last_price = bars[-1]["close"] * (1 - slippage)
        cash = positions * last_price * (1 - commission)
        equity_curve.append(round(cash, 2))

    return {"trades": trades, "equity_curve": equity_curve, "final_equity": round(equity_curve[-1], 2)}


async def _execute_trend_following_strategy(
    bars: list[dict[str, Any]],
    params: dict[str, Any],
    initial_capital: float = 100000,
    commission: float = DEFAULT_COMMISSION,
    slippage: float = DEFAULT_SLIPPAGE,
) -> dict[str, Any]:
    fast_ma = int(params.get("fast_ma", 20))
    slow_ma = int(params.get("slow_ma", 60))
    signal_ma = int(params.get("signal_ma", 9))

    if len(bars) < slow_ma + 10:
        return {"trades": [], "equity_curve": [initial_capital], "error": "insufficient data"}

    closes = extract_price_series(bars)
    cash = initial_capital
    positions: float = 0
    equity_curve = [initial_capital]
    trades: list[dict[str, Any]] = []
    in_position = False
    prev_macd = 0

    for i in range(slow_ma, len(bars)):
        ema_fast = _ema(closes[:i + 1], fast_ma)
        ema_slow = _ema(closes[:i + 1], slow_ma)
        macd = ema_fast - ema_slow
        signal = macd if i == slow_ma else _ema_last(prev_macd, macd, signal_ma)
        prev_macd = macd
        macd_diff = macd - signal
        price = bars[i]["close"]

        if not in_position and macd_diff > 0 and macd > 0:
            ep = price * (1 + slippage)
            positions = cash / ep
            cash = 0
            in_position = True
            trades.append({
                "date": bars[i]["date"], "action": "buy", "price": round(ep, 2),
                "shares": round(positions, 2), "value": round(positions * ep, 2),
                "commission": round(positions * ep * commission, 2),
                "reason": f"MACD金叉 diff={macd_diff:.2f}",
            })

        elif in_position and (macd_diff < 0):
            ep = price * (1 - slippage)
            cash_val = positions * ep * (1 - commission)
            pnl = cash_val - (equity_curve[-1] if equity_curve else initial_capital)
            trades.append({
                "date": bars[i]["date"], "action": "sell", "price": round(ep, 2),
                "shares": round(positions, 2), "value": round(positions * ep, 2),
                "commission": round(positions * ep * commission, 2),
                "pnl": round(pnl, 2), "reason": f"MACD死叉 diff={macd_diff:.2f}",
            })
            cash = cash_val
            positions = 0
            in_position = False

        eq = cash + positions * price
        equity_curve.append(round(eq, 2))

    if in_position:
        lp = bars[-1]["close"] * (1 - slippage)
        cash = positions * lp * (1 - commission)
        equity_curve.append(round(cash, 2))

    return {"trades": trades, "equity_curve": equity_curve, "final_equity": round(equity_curve[-1], 2)}


async def _execute_breakout_strategy(
    bars: list[dict[str, Any]],
    params: dict[str, Any],
    initial_capital: float = 100000,
    commission: float = DEFAULT_COMMISSION,
    slippage: float = DEFAULT_SLIPPAGE,
) -> dict[str, Any]:
    window = int(params.get("window", 50))
    volume_mult = float(params.get("volume_mult", 1.5))
    holding_period = int(params.get("holding_period", 10))

    if len(bars) < window + 10:
        return {"trades": [], "equity_curve": [initial_capital], "error": "insufficient data"}

    cash = initial_capital
    positions: float = 0
    equity_curve = [initial_capital]
    trades: list[dict[str, Any]] = []
    in_position = False
    entry_hold = 0
    entry_date = ""

    for i in range(window, len(bars)):
        window_high = max(b["high"] for b in bars[i - window:i])
        window_low = min(b["low"] for b in bars[i - window:i])
        avg_vol = sum(b["volume"] for b in bars[i - window:i]) / window
        price = bars[i]["close"]
        vol = bars[i]["volume"]

        if not in_position and price > window_high and vol > avg_vol * volume_mult:
            ep = price * (1 + slippage)
            positions = cash / ep
            cash = 0
            in_position = True
            entry_hold = 0
            entry_date = bars[i]["date"]
            trades.append({
                "date": entry_date, "action": "buy", "price": round(ep, 2),
                "shares": round(positions, 2), "value": round(positions * ep, 2),
                "commission": round(positions * ep * commission, 2),
                "reason": f"突破{window}日高点 vol={vol/avg_vol:.1f}x",
            })

        elif in_position:
            entry_hold += 1
            should_exit = (price < window_low or entry_hold >= holding_period)

            if should_exit:
                ep = price * (1 - slippage)
                cash_val = positions * ep * (1 - commission)
                pnl = cash_val - (equity_curve[-1] if equity_curve else initial_capital)
                trades.append({
                    "date": bars[i]["date"], "action": "sell", "price": round(ep, 2),
                    "shares": round(positions, 2), "value": round(positions * ep, 2),
                    "commission": round(positions * ep * commission, 2),
                    "pnl": round(pnl, 2),
                    "reason": "跌破支撑" if price < window_low else f"持仓{holding_period}日止盈",
                })
                cash = cash_val
                positions = 0
                in_position = False

        eq = cash + positions * price
        equity_curve.append(round(eq, 2))

    if in_position:
        lp = bars[-1]["close"] * (1 - slippage)
        cash = positions * lp * (1 - commission)
        equity_curve.append(round(cash, 2))

    return {"trades": trades, "equity_curve": equity_curve, "final_equity": round(equity_curve[-1], 2)}


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


async def run_backtest(
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
            break
        yield _sse("bt_data_fetch", {
            "backtest_id": backtest_id, "symbol": sym.upper(),
            "detail": f"获取 {sym.upper()} 历史数据...", "status": "working",
        })
        try:
            data = await fetch_historical_ohlcv(sym, bt["start_date"], bt["end_date"])
            bars = data.get("bars", [])
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
    combined_equity = bt["initial_capital"]
    combined_curve: list[float] = [combined_equity]
    all_trades_log: list[dict[str, Any]] = []

    for idx, (sym, bars) in enumerate(all_bars.items()):
        if request and await request.is_disconnected():
            break
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

        metrics = calculate_backtest_metrics(eq_curve, initial_capital=bt["initial_capital"] / max(len(all_bars), 1))

        symbol_results[sym] = {
            "metrics": metrics,
            "trades": len(trades_log),
            "equity_curve": eq_curve,
            "final_equity": result.get("final_equity", 0),
        }
        combined_equity += result.get("final_equity", 0) - bt["initial_capital"] / max(len(all_bars), 1)
        combined_curve.append(round(combined_equity, 2))
        all_trades_log.extend(trades_log)

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

    if len(combined_curve) < 2:
        combined_curve = [bt["initial_capital"], bt["initial_capital"]]

    final_metrics = calculate_backtest_metrics(combined_curve, initial_capital=bt["initial_capital"])
    all_trades_log.sort(key=lambda t: t.get("date", ""))

    result_payload = {
        "metrics": final_metrics,
        "total_trades": len(all_trades_log),
        "equity_curve": combined_curve,
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
            "symbol_results": {s: {"metrics": r["metrics"], "trades": r["trades"]} for s, r in symbol_results.items()},
        })
        yield _sse("bt_done", {"backtest_id": backtest_id, "total_trades": len(all_trades_log),
                               "symbols": list(all_bars)})
    except Exception:
        pass


async def list_backtest_results(symbol: str) -> dict[str, Any]:
    from .backtest_engine import list_backtests as lb
    all_bt = lb(limit=100)
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
