from __future__ import annotations

import asyncio
import math
from statistics import mean
from typing import Any, Optional

from .backtest_data import fetch_historical_ohlcv
from .risk_backtest import run_risk_backtest
from .risk_management import get_risk_summary
from .schemas import QuantLabRequest, QuantLabResponse, RiskBacktestRequest
from .shared_utils import clamp, dedupe, safe_float, utc_now_iso


STRATEGY_LABELS = {
    "momentum": "动量",
    "mean_reversion": "均值回归",
    "trend_following": "趋势跟踪",
    "breakout": "突破",
    "defensive": "防守",
}


def _clean_symbols(symbols: list[str]) -> list[str]:
    normalized = [str(symbol or "").strip().upper() for symbol in symbols]
    return dedupe([symbol for symbol in normalized if symbol])


def _series(values: list[float], lookback: int) -> list[float]:
    if lookback <= 0:
        return values
    return values[-lookback:] if len(values) > lookback else values[:]


def _stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = mean(values)
    return math.sqrt(sum((value - m) ** 2 for value in values) / (len(values) - 1))


def _pct_change(new: float, old: float) -> float:
    if old <= 0:
        return 0.0
    return (new / old - 1) * 100.0


def _drawdown_pct(values: list[float]) -> float:
    peak = values[0] if values else 0.0
    worst = 0.0
    for value in values:
        if value > peak:
            peak = value
        if peak > 0:
            worst = max(worst, (peak - value) / peak * 100.0)
    return round(worst, 2)


def _build_metrics(
    closes: list[float],
    benchmark: list[float],
    volumes: list[float],
    lookback: int,
) -> dict[str, float]:
    if len(closes) < 2:
        return {
            "ret_5": 0.0,
            "ret_20": 0.0,
            "ret_60": 0.0,
            "trend_gap": 0.0,
            "volatility": 0.0,
            "drawdown": 0.0,
            "zscore": 0.0,
            "volume_ratio": 0.0,
            "relative_strength": 0.0,
            "breakout_gap": 0.0,
        }

    base = _series(closes, max(lookback, 20))
    latest = base[-1]
    ma_20 = mean(_series(closes, min(len(closes), 20)))
    ma_60 = mean(_series(closes, min(len(closes), 60)))
    std_20 = _stdev(_series(closes, min(len(closes), 20))) or 1e-9
    ret_5 = _pct_change(latest, closes[-6] if len(closes) >= 6 else closes[0])
    ret_20 = _pct_change(latest, closes[-21] if len(closes) >= 21 else closes[0])
    ret_60 = _pct_change(latest, closes[-61] if len(closes) >= 61 else closes[0])
    trend_gap = _pct_change(ma_20, ma_60) if ma_60 > 0 else 0.0
    zscore = (latest - ma_20) / std_20
    daily_returns = []
    for idx in range(1, len(base)):
        prev = base[idx - 1]
        if prev > 0:
            daily_returns.append((base[idx] / prev - 1) * 100.0)
    volatility = _stdev(daily_returns) * math.sqrt(252) if daily_returns else 0.0
    drawdown = _drawdown_pct(base)
    volume_base = _series(volumes, min(len(volumes), 20))
    volume_ratio = (volumes[-1] / mean(volume_base)) if volume_base and mean(volume_base) > 0 else 0.0
    benchmark_ret_20 = _pct_change(benchmark[-1], benchmark[-21] if len(benchmark) >= 21 else benchmark[0]) if benchmark else 0.0
    relative_strength = ret_20 - benchmark_ret_20
    breakout_gap = _pct_change(latest, max(_series(closes, min(len(closes), 20)))) if closes else 0.0

    return {
        "ret_5": round(ret_5, 2),
        "ret_20": round(ret_20, 2),
        "ret_60": round(ret_60, 2),
        "trend_gap": round(trend_gap, 2),
        "volatility": round(volatility, 2),
        "drawdown": round(drawdown, 2),
        "zscore": round(zscore, 2),
        "volume_ratio": round(volume_ratio, 2),
        "relative_strength": round(relative_strength, 2),
        "breakout_gap": round(breakout_gap, 2),
    }


def _score_signal(strategy_key: str, metrics: dict[str, float]) -> tuple[float, list[str], list[str]]:
    ret_5 = metrics["ret_5"]
    ret_20 = metrics["ret_20"]
    ret_60 = metrics["ret_60"]
    trend_gap = metrics["trend_gap"]
    volatility = metrics["volatility"]
    drawdown = metrics["drawdown"]
    zscore = metrics["zscore"]
    volume_ratio = metrics["volume_ratio"]
    relative_strength = metrics["relative_strength"]
    breakout_gap = metrics["breakout_gap"]

    if strategy_key == "mean_reversion":
        score = (-12 * zscore) - (0.25 * ret_5) - (0.20 * relative_strength) - (0.15 * drawdown) + ((1 - volume_ratio) * 6)
    elif strategy_key == "trend_following":
        score = (0.95 * trend_gap) + (0.50 * ret_20) + (0.35 * relative_strength) - (0.22 * volatility) - (0.16 * drawdown)
    elif strategy_key == "breakout":
        score = (0.90 * breakout_gap) + ((volume_ratio - 1) * 9) + (0.25 * ret_20) + (0.20 * relative_strength) - (0.15 * drawdown)
    elif strategy_key == "defensive":
        score = (-0.50 * volatility) - (0.65 * drawdown) - (0.60 * abs(zscore)) + (0.15 * relative_strength)
    else:
        score = (0.55 * ret_20) + (0.35 * ret_60) + (0.45 * relative_strength) + (0.60 * trend_gap) + ((volume_ratio - 1) * 5) - (0.25 * volatility) - (0.18 * drawdown)

    score = clamp(score, -100.0, 100.0)
    reasons = [
        f"20日收益 {ret_20:+.2f}%",
        f"60日收益 {ret_60:+.2f}%",
        f"相对基准 {relative_strength:+.2f}%",
        f"趋势差 {trend_gap:+.2f}%",
    ]
    risks = []
    if volatility >= 35:
      risks.append(f"波动率偏高 {volatility:.1f}%")
    if drawdown >= 10:
      risks.append(f"近期回撤 {drawdown:.1f}%")
    if abs(zscore) >= 2.5:
      risks.append(f"价格偏离均值 {zscore:+.2f}σ")
    if volume_ratio and volume_ratio < 0.7:
      risks.append(f"成交量偏弱 {volume_ratio:.2f}x")
    return round(score, 2), reasons[:4], risks[:4]


def _action_from_score(score: float) -> str:
    if score >= 18:
        return "buy"
    if score <= -18:
        return "sell"
    return "hold"


def _estimate_target_weights(
    signals: list[dict[str, Any]],
    max_total_exposure_pct: float,
    max_position_size_pct: float,
    top_n: int,
) -> dict[str, float]:
    buy_signals = sorted([sig for sig in signals if sig["action"] == "buy"], key=lambda item: item["score"], reverse=True)
    focus = buy_signals[: max(1, top_n)]
    total_score = sum(max(sig["score"], 0.0) for sig in focus)
    if total_score <= 0:
        return {sig["symbol"]: 0.0 for sig in signals}

    budget = min(max_total_exposure_pct, 100.0)
    raw_weights = {}
    for sig in signals:
        if sig not in focus:
            raw_weights[sig["symbol"]] = 0.0
            continue
        weight = budget * max(sig["score"], 0.0) / total_score
        raw_weights[sig["symbol"]] = min(weight, max_position_size_pct)

    gross = sum(raw_weights.values())
    if gross > budget and gross > 0:
        scale = budget / gross
        raw_weights = {symbol: round(weight * scale, 2) for symbol, weight in raw_weights.items()}
    return {symbol: round(weight, 2) for symbol, weight in raw_weights.items()}


def _current_weight_map(risk_summary: dict[str, Any] | None) -> dict[str, float]:
    if not risk_summary:
        return {}
    portfolio = risk_summary.get("portfolio") or {}
    total_value = safe_float(portfolio.get("total_value"), 0.0)
    weights: dict[str, float] = {}
    for pos in risk_summary.get("open_positions", []) or []:
        symbol = str(pos.get("symbol") or "").upper()
        if not symbol:
            continue
        size_pct = safe_float(pos.get("position_size_pct"), 0.0)
        if size_pct <= 0 and total_value > 0:
            size_pct = safe_float(pos.get("notional_value"), 0.0) / total_value * 100.0
        weights[symbol] = round(size_pct, 2)
    return weights


async def run_quant_lab(request: QuantLabRequest) -> dict[str, Any]:
    symbols = _clean_symbols(request.symbols)
    market = str(request.market or "US")
    benchmark = str(request.benchmark or "SPY").strip().upper() or "SPY"
    start_date = request.start_date or "2024-01-01"
    end_date = request.end_date or utc_now_iso()[:10]
    lookback = max(5, int(request.lookback or 20))
    strategy_key = request.strategy_key or "momentum"
    strategy_label = STRATEGY_LABELS.get(strategy_key, strategy_key)
    sector_map = {str(k).strip().upper(): str(v).strip() for k, v in (request.sector_map or {}).items()}

    risk_summary = None
    try:
        risk_summary = await asyncio.to_thread(get_risk_summary)
    except Exception:
        risk_summary = None

    fetch_targets = {symbol: fetch_historical_ohlcv(symbol, start_date, end_date) for symbol in symbols}
    fetch_targets[benchmark] = fetch_historical_ohlcv(benchmark, start_date, end_date)
    fetched = await asyncio.gather(*fetch_targets.values(), return_exceptions=True)

    all_data: dict[str, dict[str, Any]] = {}
    for (symbol, _), data in zip(fetch_targets.items(), fetched):
        if isinstance(data, Exception):
            all_data[symbol] = {"symbol": symbol, "bars": [], "source": "error", "error": str(data)}
        else:
            all_data[symbol] = data if isinstance(data, dict) else {"symbol": symbol, "bars": [], "source": "unknown"}

    benchmark_bars = list(all_data.get(benchmark, {}).get("bars", []))
    benchmark_closes = [safe_float(bar.get("close"), 0.0) for bar in benchmark_bars if safe_float(bar.get("close"), 0.0) > 0]
    candidate_rows: list[dict[str, Any]] = []

    for symbol in symbols:
        bars = list(all_data.get(symbol, {}).get("bars", []))
        closes = [safe_float(bar.get("close"), 0.0) for bar in bars if safe_float(bar.get("close"), 0.0) > 0]
        volumes = [safe_float(bar.get("volume"), 0.0) for bar in bars]
        metrics = _build_metrics(closes, benchmark_closes, volumes, lookback) if closes else {
            "ret_5": 0.0,
            "ret_20": 0.0,
            "ret_60": 0.0,
            "trend_gap": 0.0,
            "volatility": 0.0,
            "drawdown": 0.0,
            "zscore": 0.0,
            "volume_ratio": 0.0,
            "relative_strength": 0.0,
            "breakout_gap": 0.0,
        }
        score, reasons, risks = _score_signal(strategy_key, metrics)
        action = _action_from_score(score)
        latest_close = closes[-1] if closes else 0.0
        confidence = round(min(0.95, 0.35 + abs(score) / 120.0), 2)
        sector = sector_map.get(symbol, "")
        candidate_rows.append({
            "symbol": symbol,
            "name": symbol,
            "sector": sector,
            "market": market,
            "latest_close": round(latest_close, 4),
            "score": score,
            "action": action,
            "confidence": confidence,
            "target_weight": 0.0,
            "current_weight": 0.0,
            "reasons": reasons,
            "risk_flags": risks,
            "metrics": metrics,
        })

    target_weights = _estimate_target_weights(
        candidate_rows,
        max_total_exposure_pct=request.risk_rules.max_total_exposure_pct,
        max_position_size_pct=request.risk_rules.max_position_size_pct,
        top_n=request.top_n,
    )
    current_weights = _current_weight_map(risk_summary)
    orders: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    buy_count = 0
    sell_count = 0
    hold_count = 0

    for row in sorted(candidate_rows, key=lambda item: item["score"], reverse=True):
        symbol = row["symbol"]
        row["target_weight"] = round(target_weights.get(symbol, 0.0), 2)
        row["current_weight"] = round(current_weights.get(symbol, 0.0), 2)
        delta = round(row["target_weight"] - row["current_weight"], 2)
        side = "hold"
        if delta > 1:
            side = "buy"
            buy_count += 1
        elif delta < -1:
            side = "sell"
            sell_count += 1
        else:
            hold_count += 1
        notional = max(request.initial_capital, 1) * abs(delta) / 100.0
        quantity = round(notional / row["latest_close"], 4) if row["latest_close"] > 0 else 0.0
        orders.append({
            "symbol": symbol,
            "side": side,
            "current_weight": row["current_weight"],
            "target_weight": row["target_weight"],
            "delta_weight": delta,
            "quantity": quantity,
            "notional": round(notional, 2),
            "reason": "；".join(row["reasons"][:2] + row["risk_flags"][:1]),
        })
        signals.append(row)

    backtest = await run_risk_backtest(
        RiskBacktestRequest(
            name=request.name or f"量化系统-{strategy_label}",
            market=request.market,
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
            initial_capital=request.initial_capital,
            benchmark=benchmark,
            rules=request.risk_rules,
            sector_map=sector_map,
        )
    )

    live_portfolio = (risk_summary or {}).get("portfolio") or {}
    current_total = safe_float(live_portfolio.get("total_value"), 0.0)
    target_gross = round(sum(target_weights.values()), 2)
    allocation = {
        "gross_exposure_pct": target_gross,
        "cash_buffer_pct": round(max(0.0, 100.0 - target_gross), 2),
        "selected_count": len([sig for sig in signals if sig["target_weight"] > 0]),
        "buy_count": buy_count,
        "sell_count": sell_count,
        "hold_count": hold_count,
        "max_target_weight_pct": round(max(target_weights.values()) if target_weights else 0.0, 2),
        "notes": [
            f"策略 {strategy_label} 已对 {len(signals)} 只标的评分",
            f"总目标仓位 {target_gross:.2f}%，现金缓冲 {max(0.0, 100.0 - target_gross):.2f}%",
        ],
    }
    notes = []
    if target_gross == 0:
        notes.append("当前信号强度不足，系统建议继续等待。")
    else:
        top_signal = signals[0] if signals else None
        if top_signal:
            notes.append(f"当前最强候选为 {top_signal['symbol']}，评分 {top_signal['score']:.1f}。")
    if live_portfolio:
        notes.append(f"当前组合市值 {current_total:,.0f}，开仓数 {int(safe_float(live_portfolio.get('open_count'), 0))}。")
    if backtest.get("metrics", {}).get("risk", {}).get("max_drawdown_pct") is not None:
        notes.append(f"风控回测最大回撤 {backtest['metrics']['risk']['max_drawdown_pct']:.2f}%。")

    return {
        "generated_at": utc_now_iso(),
        "name": request.name or "量化系统",
        "market": market,
        "strategy_key": strategy_key,
        "strategy_label": strategy_label,
        "symbols": symbols,
        "benchmark": benchmark,
        "initial_capital": request.initial_capital,
        "lookback": lookback,
        "signals": signals,
        "orders": orders,
        "allocation": allocation,
        "portfolio_context": {
            "current_total_value": current_total,
            "current_open_count": int(safe_float(live_portfolio.get("open_count"), 0)),
            "current_total_pnl_pct": safe_float(live_portfolio.get("total_pnl_pct"), 0.0),
            "direction_exposure": (live_portfolio.get("direction_exposure") or {}),
        },
        "backtest": backtest,
        "risk_summary": risk_summary,
        "notes": notes,
        "data_sources": {symbol: str(all_data.get(symbol, {}).get("source", "")) for symbol in symbols + [benchmark]},
        "disclaimer": "量化系统输出为研究与纸上回放用途，不构成投资建议，也不是自动下单承诺。",
    }
