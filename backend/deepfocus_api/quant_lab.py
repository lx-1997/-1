from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from .backtest_data import fetch_historical_ohlcv
from .quant_strategy_engine import (
    STRATEGY_LABELS,
    action_from_score,
    build_metrics,
    estimate_target_weights,
    minimum_history,
    run_strategy_backtest,
    score_signal,
)
from .risk_management import get_risk_summary
from .schemas import QuantLabRequest
from .shared_utils import dedupe, safe_float, utc_now_iso


ProgressCallback = Callable[[int, str], Any]


def _clean_symbols(symbols: list[str]) -> list[str]:
    normalized = [str(symbol or "").strip().upper() for symbol in symbols]
    return dedupe([symbol for symbol in normalized if symbol])


async def _progress(callback: Optional[ProgressCallback], value: int, stage: str) -> None:
    if callback is None:
        return
    result = callback(max(0, min(100, int(value))), stage)
    if inspect.isawaitable(result):
        await result


def _current_weight_map(risk_summary: dict[str, Any] | None) -> dict[str, float]:
    if not risk_summary:
        return {}
    portfolio = risk_summary.get("portfolio") or {}
    total_value = safe_float(portfolio.get("total_value"), 0.0)
    weights: dict[str, float] = {}
    for position in risk_summary.get("open_positions", []) or []:
        symbol = str(position.get("symbol") or "").upper()
        if not symbol:
            continue
        size_pct = safe_float(position.get("position_size_pct"), 0.0)
        if size_pct <= 0 and total_value > 0:
            size_pct = safe_float(position.get("notional_value"), 0.0) / total_value * 100.0
        direction = str(position.get("direction") or position.get("side") or "long").lower()
        weights[symbol] = round(-abs(size_pct) if direction in {"short", "sell"} else abs(size_pct), 2)
    return weights


def _source_details(all_data: dict[str, dict[str, Any]], symbols: list[str]) -> dict[str, dict[str, Any]]:
    keys = ("symbol", "normalized_symbol", "market", "exchange", "provider_symbol", "asset_class", "source", "source_name", "quality", "adjustment", "is_synthetic", "total_bars", "start_date", "end_date", "fetched_at", "cached", "warnings")
    return {symbol: {key: all_data.get(symbol, {}).get(key) for key in keys if all_data.get(symbol, {}).get(key) is not None} for symbol in symbols}


def _data_error(symbol: str, payload: dict[str, Any], minimum: int) -> str:
    rows = len(payload.get("bars") or [])
    warnings = "；".join(str(item) for item in (payload.get("warnings") or [])[-2:])
    if rows:
        return f"{symbol} 只有 {rows} 根真实日线，策略至少需要 {minimum} 根"
    return f"{symbol} 无可用真实行情" + (f"（{warnings}）" if warnings else "")


async def run_quant_lab(request: QuantLabRequest, progress_callback: Optional[ProgressCallback] = None) -> dict[str, Any]:
    symbols = _clean_symbols(request.symbols)
    if not symbols:
        raise ValueError("请至少输入一个交易标的")
    market = str(request.market or "AUTO").upper()
    benchmark = str(request.benchmark or "SPY").strip().upper() or "SPY"
    end_date = request.end_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    start_date = request.start_date or (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%d")
    strategy_key = request.strategy_key or "momentum"
    strategy_label = STRATEGY_LABELS.get(strategy_key, strategy_key)
    lookback = max(5, int(request.lookback or 20))
    sector_map = {str(key).strip().upper(): str(value).strip() for key, value in (request.sector_map or {}).items()}
    rules = request.risk_rules.model_dump() if hasattr(request.risk_rules, "model_dump") else dict(request.risk_rules or {})

    await _progress(progress_callback, 5, "正在校验策略与市场代码")
    risk_summary = None
    try:
        risk_summary = await asyncio.to_thread(get_risk_summary)
    except Exception:  # 组合上下文失败不应阻止独立研究
        risk_summary = None

    fetch_symbols = dedupe(symbols + [benchmark])
    market_hint = None if market == "AUTO" else market
    await _progress(progress_callback, 12, f"正在获取 {len(fetch_symbols)} 个标的的真实历史行情")
    fetched = await asyncio.gather(
        *(fetch_historical_ohlcv(symbol, start_date, end_date, market=market_hint) for symbol in fetch_symbols),
        return_exceptions=True,
    )
    all_data: dict[str, dict[str, Any]] = {}
    for symbol, payload in zip(fetch_symbols, fetched):
        if isinstance(payload, Exception):
            all_data[symbol] = {"symbol": symbol, "bars": [], "source": "unavailable", "warnings": [str(payload)], "is_synthetic": False}
        else:
            all_data[symbol] = payload if isinstance(payload, dict) else {"symbol": symbol, "bars": [], "source": "unavailable", "is_synthetic": False}

    min_bars = minimum_history(strategy_key, lookback)
    failures = [_data_error(symbol, all_data.get(symbol) or {}, min_bars) for symbol in symbols if len((all_data.get(symbol) or {}).get("bars") or []) < min_bars]
    benchmark_rows = len((all_data.get(benchmark) or {}).get("bars") or [])
    if benchmark_rows < min(20, min_bars):
        failures.append(_data_error(benchmark, all_data.get(benchmark) or {}, min(20, min_bars)))
    if failures:
        raise ValueError("真实行情校验未通过：" + "；".join(failures) + "。系统已停止计算，不会使用模拟数据。")

    await _progress(progress_callback, 40, "真实行情已校验，正在生成最新信号")
    benchmark_closes = [safe_float(bar.get("close"), 0.0) for bar in all_data[benchmark]["bars"]]
    signals: list[dict[str, Any]] = []
    for symbol in symbols:
        payload = all_data[symbol]
        bars = list(payload.get("bars") or [])
        closes = [safe_float(bar.get("close"), 0.0) for bar in bars]
        volumes = [safe_float(bar.get("volume"), 0.0) for bar in bars]
        metrics = build_metrics(closes, benchmark_closes, volumes, lookback)
        score, reasons, risks = score_signal(strategy_key, metrics)
        action = action_from_score(score)
        if action == "sell" and request.allow_short:
            action = "short"
        signals.append({
            "symbol": symbol,
            "name": symbol,
            "sector": sector_map.get(symbol, ""),
            "market": str(payload.get("market") or market),
            "latest_close": round(closes[-1], 4),
            "score": score,
            "action": action,
            "confidence": round(min(0.95, 0.35 + abs(score) / 120.0), 2),
            "target_weight": 0.0,
            "current_weight": 0.0,
            "reasons": reasons[:4],
            "risk_flags": risks[:4],
            "metrics": metrics,
        })
    signals.sort(key=lambda item: item["score"], reverse=True)

    target_weights = estimate_target_weights(
        signals,
        max_total_exposure_pct=request.risk_rules.max_total_exposure_pct,
        max_position_size_pct=request.risk_rules.max_position_size_pct,
        max_short_exposure_pct=request.risk_rules.max_short_exposure_pct,
        max_sector_exposure_pct=request.risk_rules.max_sector_exposure_pct,
        top_n=request.top_n,
        allow_short=request.allow_short,
    )
    current_weights = _current_weight_map(risk_summary)
    orders: list[dict[str, Any]] = []
    side_counts: dict[str, int] = {"buy": 0, "sell": 0, "short": 0, "cover": 0, "hold": 0}
    for row in signals:
        symbol = row["symbol"]
        target = round(target_weights.get(symbol, 0.0), 2)
        current = round(current_weights.get(symbol, 0.0), 2)
        delta = round(target - current, 2)
        if delta > 1:
            side = "cover" if current < 0 and target >= 0 else "buy"
        elif delta < -1:
            side = "short" if target < 0 and current >= 0 else "sell"
        else:
            side = "hold"
        side_counts[side] += 1
        row["target_weight"] = target
        row["current_weight"] = current
        notional = max(request.initial_capital, 1) * abs(delta) / 100.0
        orders.append({
            "symbol": symbol,
            "side": side,
            "current_weight": current,
            "target_weight": target,
            "delta_weight": delta,
            "quantity": round(notional / row["latest_close"], 4) if row["latest_close"] > 0 else 0.0,
            "notional": round(notional, 2),
            "reason": "；".join(row["reasons"][:2] + row["risk_flags"][:1]),
        })

    await _progress(progress_callback, 62, "正在执行滚动信号与交易成本回测")
    backtest = run_strategy_backtest(
        name=request.name or f"量化系统-{strategy_label}", market=market, symbols=symbols, benchmark=benchmark,
        all_data=all_data, initial_capital=request.initial_capital, strategy_key=strategy_key, lookback=lookback,
        top_n=request.top_n, allow_short=request.allow_short, rules=rules, sector_map=sector_map,
        rebalance_frequency=request.rebalance_frequency, commission_bps=request.commission_bps,
        slippage_bps=request.slippage_bps, short_borrow_bps=request.short_borrow_bps,
        min_trade_notional=request.min_trade_notional,
    )

    await _progress(progress_callback, 90, "正在汇总仓位、归因与数据质量")
    live_portfolio = (risk_summary or {}).get("portfolio") or {}
    target_gross = round(sum(abs(value) for value in target_weights.values()), 2)
    target_net = round(sum(target_weights.values()), 2)
    allocation = {
        "gross_exposure_pct": target_gross,
        "net_exposure_pct": target_net,
        "cash_buffer_pct": round(max(0.0, 100.0 - target_gross), 2),
        "selected_count": len([value for value in target_weights.values() if abs(value) > 0]),
        "buy_count": side_counts["buy"] + side_counts["cover"],
        "sell_count": side_counts["sell"] + side_counts["short"],
        "short_count": side_counts["short"],
        "hold_count": side_counts["hold"],
        "max_target_weight_pct": round(max((abs(value) for value in target_weights.values()), default=0.0), 2),
        "notes": [f"策略 {strategy_label} 已对 {len(signals)} 只标的评分", f"目标总敞口 {target_gross:.2f}%，净敞口 {target_net:+.2f}%"],
    }
    notes = ["所有信号与回测均来自已披露的真实行情源，未使用模拟数据。"]
    if target_gross == 0:
        notes.append("当前信号强度不足，系统建议继续等待。")
    elif signals:
        notes.append(f"当前最高评分为 {signals[0]['symbol']} {signals[0]['score']:.1f}。")
    notes.append(f"回测已计入 {request.commission_bps:.1f}bps 佣金、{request.slippage_bps:.1f}bps 滑点与 {request.short_borrow_bps:.1f}bps 年化借券成本。")
    if backtest.get("metrics", {}).get("risk", {}).get("max_drawdown_pct") is not None:
        notes.append(f"策略回测最大回撤 {backtest['metrics']['risk']['max_drawdown_pct']:.2f}%。")
    details = _source_details(all_data, fetch_symbols)
    await _progress(progress_callback, 100, "量化研究完成")
    return {
        "generated_at": utc_now_iso(), "name": request.name or "量化系统", "market": market,
        "strategy_key": strategy_key, "strategy_label": strategy_label, "symbols": symbols, "benchmark": benchmark,
        "initial_capital": request.initial_capital, "lookback": lookback, "signals": signals, "orders": orders,
        "allocation": allocation,
        "portfolio_context": {
            "current_total_value": safe_float(live_portfolio.get("total_value"), 0.0),
            "current_open_count": int(safe_float(live_portfolio.get("open_count"), 0.0)),
            "current_total_pnl_pct": safe_float(live_portfolio.get("total_pnl_pct"), 0.0),
            "direction_exposure": live_portfolio.get("direction_exposure") or {},
        },
        "backtest": backtest, "risk_summary": risk_summary, "notes": notes,
        "data_sources": {symbol: str(details[symbol].get("source") or "") for symbol in details},
        "data_source_details": details,
        "disclaimer": "QuantLab 仅用于可复现的历史研究与纸上计划，不构成投资建议或自动下单承诺。",
    }
