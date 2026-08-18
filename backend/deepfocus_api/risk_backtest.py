from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Optional

from .backtest_data import fetch_historical_ohlcv
from .backtest_engine import calculate_backtest_metrics
from .shared_utils import dedupe, safe_float, utc_now_iso


@dataclass
class _PositionState:
    symbol: str
    sector: str = ""
    shares: float = 0.0
    entry_price: float = 0.0
    entry_date: str = ""
    active: bool = False
    locked: bool = False
    cooldown_until: str = ""
    entry_count: int = 0


def _normalize_symbols(symbols: list[str]) -> list[str]:
    cleaned = [str(sym or "").strip().upper() for sym in symbols]
    return dedupe([sym for sym in cleaned if sym])


def _price_maps(all_bars: dict[str, list[dict[str, Any]]]) -> tuple[list[str], dict[str, dict[str, float]]]:
    dates = sorted({
        str(bar.get("date", ""))
        for bars in all_bars.values()
        for bar in bars
        if str(bar.get("date", ""))
    })
    price_maps: dict[str, dict[str, float]] = {}
    for symbol, bars in all_bars.items():
        price_maps[symbol] = {
            str(bar.get("date", "")): safe_float(bar.get("close"), 0.0)
            for bar in bars
            if str(bar.get("date", "")) and safe_float(bar.get("close"), 0.0) > 0
        }
    return dates, price_maps


def _add_days(date_str: str, days: int) -> str:
    if days <= 0 or not date_str:
        return date_str
    from datetime import datetime, timedelta

    try:
        dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
    except ValueError:
        return date_str
    return (dt + timedelta(days=days)).strftime("%Y-%m-%d")


def _simulate_portfolio(
    *,
    dates: list[str],
    price_maps: dict[str, dict[str, float]],
    symbols: list[str],
    initial_capital: float,
    rules: dict[str, Any],
    sector_map: dict[str, str],
    allow_risk_controls: bool,
) -> dict[str, Any]:
    max_position_size_pct = safe_float(rules.get("max_position_size_pct"), 20.0)
    max_total_exposure_pct = safe_float(rules.get("max_total_exposure_pct"), 100.0)
    max_sector_exposure_pct = safe_float(rules.get("max_sector_exposure_pct"), 40.0)
    max_drawdown_pct = safe_float(rules.get("max_drawdown_pct"), 15.0)
    daily_loss_limit_pct = safe_float(rules.get("daily_loss_limit_pct"), 5.0)
    stop_loss_pct = safe_float(rules.get("stop_loss_pct"), 8.0)
    take_profit_pct = safe_float(rules.get("take_profit_pct"), 15.0)
    cooldown_days = int(safe_float(rules.get("cooldown_days"), 5))
    allow_reentry = bool(rules.get("allow_reentry", False))

    positions = {
        symbol: _PositionState(symbol=symbol, sector=str(sector_map.get(symbol, "")).strip())
        for symbol in symbols
    }
    first_dates = {
        symbol: next((date for date in dates if price_maps.get(symbol, {}).get(date)), "")
        for symbol in symbols
    }

    last_prices: dict[str, float] = {}
    cash = initial_capital
    peak = initial_capital
    prev_equity = initial_capital
    circuit_breaker = False
    trades_log: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    equity_curve: list[float] = []
    active_symbols: set[str] = set()
    rule_hits = defaultdict(int)
    sector_exposure: dict[str, float] = defaultdict(float)

    total_exposure_cap = initial_capital * max_total_exposure_pct / 100.0
    per_symbol_cap = initial_capital * max_position_size_pct / 100.0
    per_symbol_target = min(
        per_symbol_cap,
        total_exposure_cap / max(len(symbols), 1),
    )

    def current_portfolio_value() -> float:
        return sum(
            positions[symbol].shares * last_prices.get(symbol, 0.0)
            for symbol in active_symbols
            if positions[symbol].active
        )

    def current_sector_value(sector: str) -> float:
        if not sector:
            return 0.0
        return sum(
            positions[symbol].shares * last_prices.get(symbol, 0.0)
            for symbol in active_symbols
            if positions[symbol].active and positions[symbol].sector == sector
        )

    def exit_position(symbol: str, date: str, reason: str) -> None:
        nonlocal cash
        state = positions[symbol]
        if not state.active:
            return
        price = last_prices.get(symbol, state.entry_price)
        if price <= 0:
            price = state.entry_price or 0.0
        proceeds = state.shares * price
        cost = state.shares * state.entry_price
        pnl = proceeds - cost
        cash += proceeds
        trades_log.append({
            "date": date,
            "symbol": symbol,
            "action": "sell",
            "price": round(price, 4),
            "shares": round(state.shares, 4),
            "value": round(proceeds, 2),
            "pnl": round(pnl, 2),
            "reason": reason,
        })
        events.append({
            "date": date,
            "symbol": symbol,
            "type": "exit",
            "message": reason,
            "value": round(pnl, 2),
        })
        state.active = False
        state.shares = 0.0
        state.locked = not allow_reentry
        state.cooldown_until = _add_days(date, cooldown_days) if allow_reentry else ""
        active_symbols.discard(symbol)

    for date in dates:
        for symbol in symbols:
            price = price_maps.get(symbol, {}).get(date)
            if price and price > 0:
                last_prices[symbol] = price

        if not circuit_breaker:
            for symbol in symbols:
                state = positions[symbol]
                if state.active or state.locked:
                    continue
                if state.cooldown_until and date < state.cooldown_until:
                    continue
                if first_dates.get(symbol) and date < first_dates[symbol]:
                    continue
                price = last_prices.get(symbol)
                if not price or price <= 0:
                    continue

                current_exposure = current_portfolio_value()
                remaining_total_budget = max(0.0, total_exposure_cap - current_exposure)
                current_sector_budget = (
                    max(0.0, initial_capital * max_sector_exposure_pct / 100.0 - current_sector_value(state.sector))
                    if state.sector and max_sector_exposure_pct > 0
                    else remaining_total_budget
                )
                allocation = min(per_symbol_target, cash, remaining_total_budget, current_sector_budget)
                if allocation <= 0:
                    rule_hits["allocation_block"] += 1
                    events.append({
                        "date": date,
                        "symbol": symbol,
                        "type": "block",
                        "message": "仓位上限或现金不足，未建立头寸",
                        "value": 0.0,
                    })
                    continue

                shares = allocation / price
                cash -= allocation
                state.active = True
                state.entry_price = price
                state.entry_date = date
                state.shares = shares
                state.entry_count += 1
                active_symbols.add(symbol)
                if state.sector:
                    sector_exposure[state.sector] += allocation
                trades_log.append({
                    "date": date,
                    "symbol": symbol,
                    "action": "buy",
                    "price": round(price, 4),
                    "shares": round(shares, 4),
                    "value": round(allocation, 2),
                    "reason": "初始建仓" if state.entry_count == 1 else "风控重入",
                })
                events.append({
                    "date": date,
                    "symbol": symbol,
                    "type": "entry",
                    "message": "建立头寸",
                    "value": round(allocation, 2),
                })

        equity_before_exit = cash + current_portfolio_value()

        if allow_risk_controls and not circuit_breaker:
            for symbol in list(active_symbols):
                state = positions[symbol]
                price = last_prices.get(symbol, 0.0)
                if price <= 0:
                    continue

                if stop_loss_pct > 0 and price <= state.entry_price * (1 - stop_loss_pct / 100.0):
                    rule_hits["stop_loss"] += 1
                    exit_position(symbol, date, f"触发止损 {stop_loss_pct:.1f}%")
                elif take_profit_pct > 0 and price >= state.entry_price * (1 + take_profit_pct / 100.0):
                    rule_hits["take_profit"] += 1
                    exit_position(symbol, date, f"触发止盈 {take_profit_pct:.1f}%")

            equity_after_position_exits = cash + current_portfolio_value()
            daily_return = (
                (equity_after_position_exits / prev_equity - 1) * 100
                if prev_equity > 0
                else 0.0
            )
            peak = max(peak, equity_after_position_exits)
            drawdown_pct = ((peak - equity_after_position_exits) / peak * 100) if peak > 0 else 0.0

            if daily_loss_limit_pct > 0 and daily_return <= -daily_loss_limit_pct and active_symbols:
                rule_hits["daily_loss"] += 1
                for symbol in list(active_symbols):
                    exit_position(symbol, date, f"触发单日亏损阈值 {daily_loss_limit_pct:.1f}%")
                circuit_breaker = True
                events.append({
                    "date": date,
                    "type": "circuit_breaker",
                    "message": "单日亏损熔断，停止继续开仓",
                    "value": round(daily_return, 2),
                })
            elif max_drawdown_pct > 0 and drawdown_pct >= max_drawdown_pct and active_symbols:
                rule_hits["drawdown"] += 1
                for symbol in list(active_symbols):
                    exit_position(symbol, date, f"触发最大回撤阈值 {max_drawdown_pct:.1f}%")
                circuit_breaker = True
                events.append({
                    "date": date,
                    "type": "circuit_breaker",
                    "message": "组合回撤超限，停止继续开仓",
                    "value": round(drawdown_pct, 2),
                })

        equity = cash + current_portfolio_value()
        peak = max(peak, equity)
        equity_curve.append(round(equity, 2))
        prev_equity = equity if equity > 0 else prev_equity

    if dates:
        final_date = dates[-1]
        for symbol in list(active_symbols):
            exit_position(symbol, final_date, "回测结束平仓")
        equity_curve[-1] = round(cash + current_portfolio_value(), 2) if equity_curve else initial_capital

    return {
        "equity_curve": equity_curve or [initial_capital, initial_capital],
        "trades_log": trades_log,
        "events": events,
        "rule_hits": dict(rule_hits),
        "final_equity": round(equity_curve[-1] if equity_curve else initial_capital, 2),
    }


async def run_risk_backtest(request: Any) -> dict[str, Any]:
    symbols = _normalize_symbols(getattr(request, "symbols", []) or [])
    rules_obj = getattr(request, "rules", {})
    rules = rules_obj.model_dump() if hasattr(rules_obj, "model_dump") else dict(rules_obj or {})
    if not symbols:
        raise ValueError("请至少输入一个回测标的")

    start_date = getattr(request, "start_date", "") or ""
    end_date = getattr(request, "end_date", "") or ""
    initial_capital = safe_float(getattr(request, "initial_capital", 100000), 100000)
    benchmark = str(getattr(request, "benchmark", "SPY") or "").strip().upper() or "SPY"
    market = str(getattr(request, "market", "US") or "US")
    sector_map = {str(k).strip().upper(): str(v).strip() for k, v in dict(getattr(request, "sector_map", {}) or {}).items()}

    if not start_date:
        start_date = "2024-01-01"
    if not end_date:
        from datetime import datetime

        end_date = datetime.utcnow().strftime("%Y-%m-%d")

    fetch_targets = {symbol: fetch_historical_ohlcv(symbol, start_date, end_date, market=market) for symbol in symbols}
    fetch_targets[benchmark] = fetch_historical_ohlcv(benchmark, start_date, end_date)
    fetched = await asyncio.gather(*fetch_targets.values(), return_exceptions=True)
    all_data: dict[str, dict[str, Any]] = {}
    for (symbol, _), result in zip(fetch_targets.items(), fetched):
        if isinstance(result, Exception):
            all_data[symbol] = {"symbol": symbol, "bars": [], "source": "unavailable", "warnings": [str(result)], "is_synthetic": False}
        else:
            all_data[symbol] = result if isinstance(result, dict) else {"symbol": symbol, "bars": [], "source": "unavailable", "is_synthetic": False}

    unavailable = []
    for symbol in dedupe(symbols + [benchmark]):
        payload = all_data.get(symbol) or {}
        bar_count = len(payload.get("bars") or [])
        if bar_count < 2:
            warning = "；".join(str(item) for item in (payload.get("warnings") or [])[-2:])
            unavailable.append(f"{symbol} 仅 {bar_count} 根真实日线" + (f"（{warning}）" if warning else ""))
    if unavailable:
        raise ValueError("真实行情校验未通过：" + "；".join(unavailable) + "。系统已停止计算，不会使用模拟数据。")

    all_bars = {symbol: list(data.get("bars", [])) for symbol, data in all_data.items()}
    dates, price_maps = _price_maps(all_bars)
    if len(dates) < 2:
        raise ValueError("真实行情可用交易日不足，无法回测")

    baseline = _simulate_portfolio(
        dates=dates,
        price_maps=price_maps,
        symbols=symbols,
        initial_capital=initial_capital,
        rules=rules,
        sector_map=sector_map,
        allow_risk_controls=False,
    )
    risk = _simulate_portfolio(
        dates=dates,
        price_maps=price_maps,
        symbols=symbols,
        initial_capital=initial_capital,
        rules=rules,
        sector_map=sector_map,
        allow_risk_controls=True,
    )

    benchmark_prices = price_maps.get(benchmark, {})
    benchmark_curve: list[float] = []
    if benchmark_prices:
        bench_last = 0.0
        bench_cash = initial_capital
        bench_shares = 0.0
        entered = False
        for date in dates:
            price = benchmark_prices.get(date)
            if price and price > 0:
                bench_last = price
            if not entered and bench_last > 0:
                bench_shares = bench_cash / bench_last
                bench_cash = 0.0
                entered = True
            benchmark_curve.append(round(bench_cash + bench_shares * bench_last, 2))
    else:
        benchmark_curve = [initial_capital for _ in dates] if dates else [initial_capital, initial_capital]

    baseline_metrics = calculate_backtest_metrics(
        baseline["equity_curve"] if baseline["equity_curve"] else [initial_capital, initial_capital],
        benchmark_curve=benchmark_curve if len(benchmark_curve) == len(baseline["equity_curve"]) else None,
        initial_capital=initial_capital,
    )
    risk_metrics = calculate_backtest_metrics(
        risk["equity_curve"] if risk["equity_curve"] else [initial_capital, initial_capital],
        benchmark_curve=benchmark_curve if len(benchmark_curve) == len(risk["equity_curve"]) else None,
        initial_capital=initial_capital,
    )
    benchmark_metrics = calculate_backtest_metrics(
        benchmark_curve if len(benchmark_curve) >= 2 else [initial_capital, initial_capital],
        initial_capital=initial_capital,
    )

    improvement = {
        "return_delta_pct": round(risk_metrics["total_return_pct"] - baseline_metrics["total_return_pct"], 2),
        "drawdown_reduction_pct": round(baseline_metrics["max_drawdown_pct"] - risk_metrics["max_drawdown_pct"], 2),
        "sharpe_delta": round(risk_metrics["sharpe_ratio"] - baseline_metrics["sharpe_ratio"], 4),
        "profit_factor_delta": round(risk_metrics["profit_factor"] - baseline_metrics["profit_factor"], 4),
    }

    rule_hits = {
        "stop_loss": risk["rule_hits"].get("stop_loss", 0),
        "take_profit": risk["rule_hits"].get("take_profit", 0),
        "daily_loss": risk["rule_hits"].get("daily_loss", 0),
        "drawdown": risk["rule_hits"].get("drawdown", 0),
        "allocation_block": risk["rule_hits"].get("allocation_block", 0),
    }
    summary = (
        f"风控后收益 {risk_metrics['total_return_pct']:+.2f}%、"
        f"最大回撤 {risk_metrics['max_drawdown_pct']:.2f}%；"
        f"相对裸持有收益变化 {improvement['return_delta_pct']:+.2f}%"
    )

    return {
        "generated_at": utc_now_iso(),
        "name": getattr(request, "name", "") or "风控回测",
        "market": market,
        "symbols": symbols,
        "benchmark": benchmark,
        "start_date": start_date,
        "end_date": end_date,
        "initial_capital": initial_capital,
        "rules": rules,
        "metrics": {
            "risk": risk_metrics,
            "baseline": baseline_metrics,
            "benchmark": benchmark_metrics,
            "improvement": improvement,
            "rule_hits": rule_hits,
            "summary": summary,
        },
        "events": risk["events"][-200:],
        "trades_log": risk["trades_log"][-200:],
        "equity_curve": risk["equity_curve"],
        "baseline_curve": baseline["equity_curve"],
        "benchmark_curve": benchmark_curve,
        "dates": dates,
        "data_sources": {symbol: str(all_data.get(symbol, {}).get("source", "")) for symbol in symbols + [benchmark]},
        "data_source_details": {
            symbol: {
                key: all_data.get(symbol, {}).get(key)
                for key in ("symbol", "normalized_symbol", "market", "exchange", "provider_symbol", "asset_class", "source", "source_name", "quality", "adjustment", "is_synthetic", "total_bars", "start_date", "end_date", "fetched_at", "cached", "warnings")
                if all_data.get(symbol, {}).get(key) is not None
            }
            for symbol in dedupe(symbols + [benchmark])
        },
        "disclaimer": "风控回测基于历史行情与本地规则引擎，仅用于风险管理研究，不构成投资建议。",
    }
