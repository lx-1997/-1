from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import mean
from typing import Any

from .backtest_engine import calculate_backtest_metrics
from .shared_utils import clamp, safe_float, utc_now_iso


STRATEGY_LABELS = {
    "momentum": "动量",
    "mean_reversion": "均值回归",
    "trend_following": "趋势跟踪",
    "breakout": "突破",
    "defensive": "防守",
}


def _series(values: list[float], lookback: int) -> list[float]:
    return values[-lookback:] if lookback > 0 and len(values) > lookback else values[:]


def _stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    average = mean(values)
    return math.sqrt(sum((value - average) ** 2 for value in values) / (len(values) - 1))


def _pct_change(new: float, old: float) -> float:
    return (new / old - 1) * 100.0 if old > 0 else 0.0


def _drawdown_pct(values: list[float]) -> float:
    peak = values[0] if values else 0.0
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak * 100.0)
    return round(worst, 2)


def build_metrics(closes: list[float], benchmark: list[float], volumes: list[float], lookback: int) -> dict[str, float]:
    if len(closes) < 2:
        return {key: 0.0 for key in ("ret_5", "ret_20", "ret_60", "trend_gap", "volatility", "drawdown", "zscore", "volume_ratio", "relative_strength", "breakout_gap")}
    base = _series(closes, max(lookback, 20))
    latest = base[-1]
    ma_20 = mean(_series(closes, min(len(closes), 20)))
    ma_60 = mean(_series(closes, min(len(closes), 60)))
    std_20 = _stdev(_series(closes, min(len(closes), 20))) or 1e-9
    ret_5 = _pct_change(latest, closes[-6] if len(closes) >= 6 else closes[0])
    ret_20 = _pct_change(latest, closes[-21] if len(closes) >= 21 else closes[0])
    ret_60 = _pct_change(latest, closes[-61] if len(closes) >= 61 else closes[0])
    daily_returns = [_pct_change(base[index], base[index - 1]) for index in range(1, len(base)) if base[index - 1] > 0]
    volume_base = _series(volumes, min(len(volumes), 20))
    benchmark_ret = _pct_change(benchmark[-1], benchmark[-21] if len(benchmark) >= 21 else benchmark[0]) if benchmark else 0.0
    return {
        "ret_5": round(ret_5, 2),
        "ret_20": round(ret_20, 2),
        "ret_60": round(ret_60, 2),
        "trend_gap": round(_pct_change(ma_20, ma_60) if ma_60 > 0 else 0.0, 2),
        "volatility": round(_stdev(daily_returns) * math.sqrt(252) if daily_returns else 0.0, 2),
        "drawdown": _drawdown_pct(base),
        "zscore": round((latest - ma_20) / std_20, 2),
        "volume_ratio": round((volumes[-1] / mean(volume_base)) if volumes and volume_base and mean(volume_base) > 0 else 0.0, 2),
        "relative_strength": round(ret_20 - benchmark_ret, 2),
        "breakout_gap": round(_pct_change(latest, max(_series(closes[:-1] or closes, min(len(closes), 20)))), 2),
    }


def score_signal(strategy_key: str, metrics: dict[str, float]) -> tuple[float, list[str], list[str]]:
    ret_5, ret_20, ret_60 = metrics["ret_5"], metrics["ret_20"], metrics["ret_60"]
    trend_gap, volatility, drawdown = metrics["trend_gap"], metrics["volatility"], metrics["drawdown"]
    zscore, volume_ratio = metrics["zscore"], metrics["volume_ratio"]
    relative_strength, breakout_gap = metrics["relative_strength"], metrics["breakout_gap"]
    if strategy_key == "mean_reversion":
        score = (-12 * zscore) - (0.25 * ret_5) - (0.20 * relative_strength) - (0.15 * drawdown) + ((1 - volume_ratio) * 6)
    elif strategy_key == "trend_following":
        score = (0.95 * trend_gap) + (0.50 * ret_20) + (0.35 * relative_strength) - (0.22 * volatility) - (0.16 * drawdown)
    elif strategy_key == "breakout":
        score = (0.90 * breakout_gap) + ((volume_ratio - 1) * 9) + (0.25 * ret_20) + (0.20 * relative_strength) - (0.15 * drawdown)
    elif strategy_key == "defensive":
        score = (-0.50 * volatility) - (0.65 * drawdown) - (0.60 * abs(zscore)) + (0.15 * relative_strength) + 20
    else:
        score = (0.55 * ret_20) + (0.35 * ret_60) + (0.45 * relative_strength) + (0.60 * trend_gap) + ((volume_ratio - 1) * 5) - (0.25 * volatility) - (0.18 * drawdown)
    score = clamp(score, -100.0, 100.0)
    reasons = [f"20日收益 {ret_20:+.2f}%", f"60日收益 {ret_60:+.2f}%", f"相对基准 {relative_strength:+.2f}%", f"趋势差 {trend_gap:+.2f}%"]
    risks = []
    if volatility >= 35:
        risks.append(f"波动率偏高 {volatility:.1f}%")
    if drawdown >= 10:
        risks.append(f"近期回撤 {drawdown:.1f}%")
    if abs(zscore) >= 2.5:
        risks.append(f"价格偏离均值 {zscore:+.2f}σ")
    if volume_ratio and volume_ratio < 0.7:
        risks.append(f"成交量偏弱 {volume_ratio:.2f}x")
    return round(score, 2), reasons, risks


def action_from_score(score: float) -> str:
    if score >= 18:
        return "buy"
    if score <= -18:
        return "sell"
    return "hold"


def estimate_target_weights(
    signals: list[dict[str, Any]],
    *,
    max_total_exposure_pct: float,
    max_position_size_pct: float,
    max_short_exposure_pct: float,
    max_sector_exposure_pct: float,
    top_n: int,
    allow_short: bool,
) -> dict[str, float]:
    longs = sorted((row for row in signals if row["score"] >= 18), key=lambda row: row["score"], reverse=True)[:max(1, top_n)]
    shorts = sorted((row for row in signals if row["score"] <= -18), key=lambda row: row["score"])[:max(1, top_n)] if allow_short else []
    total_cap = max(0.0, min(100.0, max_total_exposure_pct))
    short_cap = min(total_cap, max(0.0, max_short_exposure_pct))
    long_cap = max(0.0, total_cap - short_cap if shorts else total_cap)

    def allocate(rows: list[dict[str, Any]], budget: float, sign: float) -> dict[str, float]:
        strengths = {row["symbol"]: max(1.0, abs(row["score"])) / max(8.0, safe_float(row["metrics"].get("volatility"), 20.0)) for row in rows}
        denominator = sum(strengths.values()) or 1.0
        return {row["symbol"]: sign * min(max_position_size_pct, budget * strengths[row["symbol"]] / denominator) for row in rows}

    weights = {row["symbol"]: 0.0 for row in signals}
    weights.update(allocate(longs, long_cap, 1.0))
    weights.update(allocate(shorts, short_cap, -1.0))
    if max_sector_exposure_pct > 0:
        by_sector: dict[str, list[str]] = defaultdict(list)
        for row in signals:
            if row["symbol"] in weights and row.get("sector"):
                by_sector[str(row["sector"])].append(row["symbol"])
        for symbols in by_sector.values():
            gross = sum(abs(weights.get(symbol, 0.0)) for symbol in symbols)
            if gross > max_sector_exposure_pct:
                scale = max_sector_exposure_pct / gross
                for symbol in symbols:
                    weights[symbol] *= scale
    gross = sum(abs(value) for value in weights.values())
    if gross > total_cap and gross > 0:
        weights = {symbol: value * total_cap / gross for symbol, value in weights.items()}
    return {symbol: round(value, 2) for symbol, value in weights.items()}


def minimum_history(strategy_key: str, lookback: int) -> int:
    return max(20 if strategy_key in {"mean_reversion", "breakout", "defensive"} else 60, min(lookback, 252))


def _rebalance_key(date: str, frequency: str) -> str:
    parsed = datetime.strptime(date, "%Y-%m-%d")
    if frequency == "daily":
        return date
    if frequency == "monthly":
        return parsed.strftime("%Y-%m")
    iso = parsed.isocalendar()
    return f"{iso.year}-{iso.week:02d}"


def _add_days(date: str, days: int) -> str:
    return (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=max(0, days))).strftime("%Y-%m-%d")


def run_strategy_backtest(
    *,
    name: str,
    market: str,
    symbols: list[str],
    benchmark: str,
    all_data: dict[str, dict[str, Any]],
    initial_capital: float,
    strategy_key: str,
    lookback: int,
    top_n: int,
    allow_short: bool,
    rules: dict[str, Any],
    sector_map: dict[str, str],
    rebalance_frequency: str,
    commission_bps: float,
    slippage_bps: float,
    short_borrow_bps: float,
    min_trade_notional: float,
) -> dict[str, Any]:
    symbol_bars = {symbol: list((all_data.get(symbol) or {}).get("bars") or []) for symbol in symbols}
    benchmark_bars = list((all_data.get(benchmark) or {}).get("bars") or [])
    dates = sorted({str(bar.get("date") or "") for bars in symbol_bars.values() for bar in bars if bar.get("date")})
    if len(dates) < 2:
        raise ValueError("真实行情不足，无法执行策略回测")
    bar_maps = {symbol: {str(bar["date"]): bar for bar in bars} for symbol, bars in symbol_bars.items()}
    benchmark_map = {str(bar["date"]): bar for bar in benchmark_bars}
    histories = {symbol: {"closes": [], "volumes": []} for symbol in symbols}
    benchmark_history: list[float] = []
    last_prices: dict[str, float] = {}
    shares = {symbol: 0.0 for symbol in symbols}
    entry_prices = {symbol: 0.0 for symbol in symbols}
    cash = float(initial_capital)
    peak = float(initial_capital)
    previous_equity = float(initial_capital)
    pause_until = ""
    halted = False
    blocked_symbols: set[str] = set()
    symbol_cooldowns: dict[str, str] = {}
    last_rebalance = ""
    equity_curve: list[float] = []
    exposure_curve: list[dict[str, float]] = []
    trades_log: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    realized_by_symbol: dict[str, float] = defaultdict(float)
    total_cost = 0.0
    borrow_cost = 0.0
    turnover_notional = 0.0
    rebalance_count = 0
    rule_hits: dict[str, int] = defaultdict(int)
    latest_weights = {symbol: 0.0 for symbol in symbols}

    def equity() -> float:
        return cash + sum(shares[symbol] * last_prices.get(symbol, 0.0) for symbol in symbols)

    def execute(symbol: str, target_notional: float, date: str, reason: str) -> None:
        nonlocal cash, total_cost, turnover_notional
        price = last_prices.get(symbol, 0.0)
        if price <= 0:
            return
        current_notional = shares[symbol] * price
        delta_notional = target_notional - current_notional
        if abs(delta_notional) < max(0.0, min_trade_notional):
            return
        is_buy = delta_notional > 0
        execution_price = price * (1 + slippage_bps / 10000.0 if is_buy else 1 - slippage_bps / 10000.0)
        target_shares = target_notional / price
        delta_shares = target_shares - shares[symbol]
        trade_value = delta_shares * execution_price
        commission = abs(trade_value) * commission_bps / 10000.0
        old_shares = shares[symbol]
        old_entry = entry_prices[symbol]
        new_shares = old_shares + delta_shares
        close_quantity = 0.0
        realized = 0.0
        if old_shares > 0 and delta_shares < 0:
            close_quantity = min(old_shares, abs(delta_shares))
            realized = (execution_price - old_entry) * close_quantity
        elif old_shares < 0 and delta_shares > 0:
            close_quantity = min(abs(old_shares), delta_shares)
            realized = (old_entry - execution_price) * close_quantity
        realized -= commission * (close_quantity / abs(delta_shares)) if delta_shares and close_quantity else 0.0
        realized_by_symbol[symbol] += realized
        cash -= trade_value + commission
        total_cost += commission + abs(trade_value) * slippage_bps / 10000.0
        turnover_notional += abs(trade_value)
        if abs(new_shares) < 1e-8:
            new_shares = 0.0
            entry_prices[symbol] = 0.0
        elif old_shares == 0 or old_shares * new_shares < 0:
            entry_prices[symbol] = execution_price
        elif old_shares * delta_shares > 0:
            entry_prices[symbol] = ((abs(old_shares) * old_entry) + (abs(delta_shares) * execution_price)) / abs(new_shares)
        shares[symbol] = new_shares
        side = "buy" if delta_shares > 0 and new_shares > 0 else "cover" if delta_shares > 0 else "short" if new_shares < 0 else "sell"
        trades_log.append({"date": date, "symbol": symbol, "action": side, "price": round(execution_price, 4), "shares": round(abs(delta_shares), 4), "value": round(abs(trade_value), 2), "cost": round(commission, 2), "pnl": round(realized, 2), "reason": reason})

    for date in dates:
        for symbol in symbols:
            bar = bar_maps[symbol].get(date)
            if bar and safe_float(bar.get("close"), 0.0) > 0:
                close = safe_float(bar.get("close"), 0.0)
                last_prices[symbol] = close

        daily_borrow = sum(
            abs(shares[symbol] * last_prices.get(symbol, 0.0)) * max(0.0, short_borrow_bps) / 10000.0 / 252.0
            for symbol in symbols
            if shares[symbol] < 0
        )
        if daily_borrow > 0:
            cash -= daily_borrow
            borrow_cost += daily_borrow
            total_cost += daily_borrow

        for symbol in symbols:
            position = shares[symbol]
            entry = entry_prices[symbol]
            price = last_prices.get(symbol, 0.0)
            if not position or entry <= 0 or price <= 0:
                continue
            position_return = ((price / entry - 1) if position > 0 else (entry / price - 1)) * 100.0
            if safe_float(rules.get("stop_loss_pct"), 0.0) > 0 and position_return <= -safe_float(rules.get("stop_loss_pct"), 0.0):
                rule_hits["stop_loss"] += 1
                execute(symbol, 0.0, date, f"触发止损 {rules['stop_loss_pct']:.1f}%")
                if bool(rules.get("allow_reentry", False)):
                    symbol_cooldowns[symbol] = _add_days(date, int(safe_float(rules.get("cooldown_days"), 5)))
                else:
                    blocked_symbols.add(symbol)
            elif safe_float(rules.get("take_profit_pct"), 0.0) > 0 and position_return >= safe_float(rules.get("take_profit_pct"), 0.0):
                rule_hits["take_profit"] += 1
                execute(symbol, 0.0, date, f"触发止盈 {rules['take_profit_pct']:.1f}%")
                if bool(rules.get("allow_reentry", False)):
                    symbol_cooldowns[symbol] = _add_days(date, int(safe_float(rules.get("cooldown_days"), 5)))
                else:
                    blocked_symbols.add(symbol)

        current_equity = equity()
        daily_return = (current_equity / previous_equity - 1) * 100.0 if previous_equity > 0 else 0.0
        peak = max(peak, current_equity)
        drawdown = (peak - current_equity) / peak * 100.0 if peak > 0 else 0.0
        daily_limit = safe_float(rules.get("daily_loss_limit_pct"), 0.0)
        drawdown_limit = safe_float(rules.get("max_drawdown_pct"), 0.0)
        breach = ""
        if daily_limit > 0 and daily_return <= -daily_limit:
            breach = f"单日亏损 {daily_return:.2f}% 触发熔断"
            rule_hits["daily_loss"] += 1
        elif drawdown_limit > 0 and drawdown >= drawdown_limit:
            breach = f"组合回撤 {drawdown:.2f}% 触发熔断"
            rule_hits["drawdown"] += 1
        if breach and any(abs(value) > 1e-8 for value in shares.values()):
            for symbol in symbols:
                execute(symbol, 0.0, date, breach)
            pause_until = _add_days(date, int(safe_float(rules.get("cooldown_days"), 5)))
            halted = not bool(rules.get("allow_reentry", False))
            events.append({"date": date, "type": "circuit_breaker", "message": f"{breach}，暂停至 {pause_until}", "value": round(drawdown, 2)})

        rebalance_key = _rebalance_key(date, rebalance_frequency)
        can_rebalance = not halted and rebalance_key != last_rebalance and (not pause_until or date >= pause_until)
        if can_rebalance:
            min_bars = minimum_history(strategy_key, lookback)
            candidates = []
            for symbol in symbols:
                closes = histories[symbol]["closes"]
                if (
                    len(closes) < min_bars
                    or symbol not in last_prices
                    or symbol in blocked_symbols
                    or (symbol_cooldowns.get(symbol) and date < symbol_cooldowns[symbol])
                ):
                    continue
                metrics = build_metrics(closes, benchmark_history, histories[symbol]["volumes"], lookback)
                score, _reasons, _risks = score_signal(strategy_key, metrics)
                candidates.append({"symbol": symbol, "sector": sector_map.get(symbol, ""), "score": score, "metrics": metrics})
            if candidates:
                weights = estimate_target_weights(
                    candidates,
                    max_total_exposure_pct=safe_float(rules.get("max_total_exposure_pct"), 100.0),
                    max_position_size_pct=safe_float(rules.get("max_position_size_pct"), 20.0),
                    max_short_exposure_pct=safe_float(rules.get("max_short_exposure_pct"), 30.0),
                    max_sector_exposure_pct=safe_float(rules.get("max_sector_exposure_pct"), 40.0),
                    top_n=top_n,
                    allow_short=allow_short,
                )
                rebalance_equity = max(equity(), 0.0)
                targets = {symbol: rebalance_equity * weights.get(symbol, 0.0) / 100.0 for symbol in symbols}
                for symbol in sorted(symbols, key=lambda item: targets[item] - shares[item] * last_prices.get(item, 0.0)):
                    execute(symbol, targets[symbol], date, f"{STRATEGY_LABELS.get(strategy_key, strategy_key)}策略再平衡")
                latest_weights = {symbol: round((shares[symbol] * last_prices.get(symbol, 0.0) / max(equity(), 1.0)) * 100.0, 2) for symbol in symbols}
                last_rebalance = rebalance_key
                rebalance_count += 1

        current_equity = equity()
        gross = sum(abs(shares[symbol] * last_prices.get(symbol, 0.0)) for symbol in symbols) / max(current_equity, 1.0) * 100.0
        net = sum(shares[symbol] * last_prices.get(symbol, 0.0) for symbol in symbols) / max(current_equity, 1.0) * 100.0
        equity_curve.append(round(current_equity, 2))
        exposure_curve.append({"gross": round(gross, 2), "net": round(net, 2)})
        previous_equity = current_equity if current_equity > 0 else previous_equity
        for symbol in symbols:
            bar = bar_maps[symbol].get(date)
            if bar and safe_float(bar.get("close"), 0.0) > 0:
                histories[symbol]["closes"].append(safe_float(bar.get("close"), 0.0))
                histories[symbol]["volumes"].append(safe_float(bar.get("volume"), 0.0))
        benchmark_bar = benchmark_map.get(date)
        if benchmark_bar and safe_float(benchmark_bar.get("close"), 0.0) > 0:
            benchmark_history.append(safe_float(benchmark_bar.get("close"), 0.0))

    for symbol in symbols:
        execute(symbol, 0.0, dates[-1], "回测结束平仓")
    if equity_curve:
        equity_curve[-1] = round(equity(), 2)
    if exposure_curve:
        exposure_curve[-1] = {"gross": 0.0, "net": 0.0}

    baseline_curve = _equal_weight_curve(dates, symbol_bars, initial_capital)
    benchmark_curve = _single_asset_curve(dates, benchmark_bars, initial_capital)
    strategy_metrics = calculate_backtest_metrics(equity_curve, benchmark_curve=benchmark_curve if len(benchmark_curve) == len(equity_curve) else None, initial_capital=initial_capital)
    baseline_metrics = calculate_backtest_metrics(baseline_curve, benchmark_curve=benchmark_curve if len(benchmark_curve) == len(baseline_curve) else None, initial_capital=initial_capital)
    benchmark_metrics = calculate_backtest_metrics(benchmark_curve, initial_capital=initial_capital)
    closed_trades = [trade for trade in trades_log if trade["action"] in {"sell", "cover"}]
    closed_pnls = [safe_float(trade.get("pnl"), 0.0) for trade in closed_trades]
    winning_trades = [value for value in closed_pnls if value > 0]
    losing_trades = [value for value in closed_pnls if value < 0]
    trade_returns = [
        safe_float(trade.get("pnl"), 0.0) / max(safe_float(trade.get("value"), 0.0), 1.0) * 100.0
        for trade in closed_trades
    ]
    strategy_metrics.update({
        "positive_day_rate_pct": strategy_metrics.get("win_rate", 0.0),
        "daily_observation_count": strategy_metrics.get("total_trades", 0),
        "total_trades": len(trades_log),
        "closed_trades": len(closed_trades),
        "win_rate": round(len(winning_trades) / len(closed_trades) * 100.0, 1) if closed_trades else 0.0,
        "profit_factor": round(sum(winning_trades) / abs(sum(losing_trades)), 4) if losing_trades else (999.0 if winning_trades else 0.0),
        "avg_trade_return": round(mean(trade_returns), 4) if trade_returns else 0.0,
    })
    improvement = {
        "return_delta_pct": round(strategy_metrics["total_return_pct"] - baseline_metrics["total_return_pct"], 2),
        "drawdown_reduction_pct": round(baseline_metrics["max_drawdown_pct"] - strategy_metrics["max_drawdown_pct"], 2),
        "sharpe_delta": round(strategy_metrics["sharpe_ratio"] - baseline_metrics["sharpe_ratio"], 4),
        "profit_factor_delta": round(strategy_metrics["profit_factor"] - baseline_metrics["profit_factor"], 4),
    }
    summary = f"策略收益 {strategy_metrics['total_return_pct']:+.2f}%、最大回撤 {strategy_metrics['max_drawdown_pct']:.2f}%；相对等权持有 {improvement['return_delta_pct']:+.2f}%"
    details = {symbol: _source_detail(all_data.get(symbol) or {}) for symbol in symbols + [benchmark]}
    markets = {str((all_data.get(symbol) or {}).get("market") or "") for symbol in symbols}
    currency_mode = "constant_currency" if len(markets - {""}) > 1 else "local_market_currency"
    return {
        "generated_at": utc_now_iso(), "name": name, "market": market, "symbols": symbols, "benchmark": benchmark,
        "start_date": dates[0], "end_date": dates[-1], "initial_capital": initial_capital, "rules": rules,
        "metrics": {
            "risk": strategy_metrics, "baseline": baseline_metrics, "benchmark": benchmark_metrics, "improvement": improvement,
            "rule_hits": dict(rule_hits), "summary": summary,
            "execution": {"commission_bps": commission_bps, "slippage_bps": slippage_bps, "short_borrow_bps": short_borrow_bps, "transaction_cost": round(total_cost, 2), "borrow_cost": round(borrow_cost, 2), "turnover_notional": round(turnover_notional, 2), "turnover_ratio": round(turnover_notional / max(initial_capital, 1.0), 4), "rebalance_count": rebalance_count, "signal_timing": "T-1 收盘生成信号，T 日收盘执行", "currency_mode": currency_mode},
            "attribution": {
                "realized_pnl_by_symbol": {symbol: round(value, 2) for symbol, value in realized_by_symbol.items()},
                "latest_strategy_weights": latest_weights,
                "final_weights": {symbol: 0.0 for symbol in symbols},
                "final_gross_exposure_pct": exposure_curve[-1]["gross"] if exposure_curve else 0.0,
                "final_net_exposure_pct": exposure_curve[-1]["net"] if exposure_curve else 0.0,
            },
        },
        "events": events[-300:], "trades_log": trades_log[-500:], "equity_curve": equity_curve,
        "baseline_curve": baseline_curve, "benchmark_curve": benchmark_curve, "dates": dates,
        "data_sources": {symbol: str(details[symbol].get("source") or "") for symbol in details}, "data_source_details": details,
        "disclaimer": "回测已计入设定的佣金、滑点与借券费；跨市场组合使用常汇率口径，不含汇率损益。历史表现不代表未来收益。",
    }


def _equal_weight_curve(dates: list[str], symbol_bars: dict[str, list[dict[str, Any]]], initial_capital: float) -> list[float]:
    maps = {symbol: {str(row["date"]): safe_float(row.get("close"), 0.0) for row in rows} for symbol, rows in symbol_bars.items()}
    symbols = list(maps)
    budget = initial_capital / max(len(symbols), 1)
    holdings = {symbol: 0.0 for symbol in symbols}
    last = {symbol: 0.0 for symbol in symbols}
    cash = initial_capital
    curve = []
    for date in dates:
        for symbol in symbols:
            price = maps[symbol].get(date, 0.0)
            if price > 0:
                last[symbol] = price
            if holdings[symbol] == 0 and price > 0 and cash >= budget:
                holdings[symbol] = budget / price
                cash -= budget
        curve.append(round(cash + sum(holdings[symbol] * last[symbol] for symbol in symbols), 2))
    return curve or [initial_capital, initial_capital]


def _single_asset_curve(dates: list[str], bars: list[dict[str, Any]], initial_capital: float) -> list[float]:
    prices = {str(row["date"]): safe_float(row.get("close"), 0.0) for row in bars}
    shares = 0.0
    last = 0.0
    curve = []
    for date in dates:
        price = prices.get(date, 0.0)
        if price > 0:
            last = price
            if shares == 0:
                shares = initial_capital / price
        curve.append(round(shares * last if shares else initial_capital, 2))
    return curve or [initial_capital, initial_capital]


def _source_detail(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: payload.get(key) for key in ("symbol", "normalized_symbol", "market", "exchange", "provider_symbol", "asset_class", "source", "source_name", "quality", "adjustment", "is_synthetic", "total_bars", "start_date", "end_date", "fetched_at", "cached", "warnings") if payload.get(key) is not None}
