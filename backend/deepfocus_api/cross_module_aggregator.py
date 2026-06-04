from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

from .market_data import fetch_market_quotes, MarketQuoteListResponse
from .risk_management import list_positions, get_risk_summary
from .data_sources import list_data_items
from .market_dashboard import fetch_market_dashboard as _fetch_market_dashboard
from .shared_utils import safe_float, safe_int, utc_now_iso


async def gather_market_quotes(symbols: list[str]) -> dict[str, Any]:
    if not symbols:
        return {"quotes": [], "source": "none"}
    try:
        quotes_resp: MarketQuoteListResponse = await fetch_market_quotes(symbols)
        quotes = []
        for q in quotes_resp.quotes:
            quotes.append({
                "symbol": q.symbol,
                "name": q.name,
                "price": q.price,
                "change": q.change,
                "change_pct": q.change_pct,
                "volume": q.volume,
                "high": q.high,
                "low": q.low,
                "open": q.open,
                "prev_close": q.prev_close,
                "market": q.market,
            })
        return {"quotes": quotes, "source": quotes_resp.provider}
    except Exception as e:
        return {"quotes": [], "source": "error", "error": str(e)}


async def gather_macro_brief() -> dict[str, Any]:
    try:
        dashboard = await _fetch_market_dashboard()
        indicators = []
        for cat in dashboard.get("categories", []):
            for ind in cat.get("indicators", []):
                if ind.get("status") and not str(ind.get("status", "")).startswith("数据"):
                    indicators.append({
                        "name": ind["name"],
                        "value": ind["value"],
                        "unit": ind.get("unit", ""),
                        "signal": ind.get("signal", "neutral"),
                        "reading": ind.get("current_reading", ""),
                    })
        return {
            "overall_signal": dashboard.get("overall_signal", ""),
            "overall_score": dashboard.get("overall_score", 0),
            "overall_summary": dashboard.get("overall_summary", ""),
            "indicators": indicators,
        }
    except Exception as e:
        return {"overall_signal": "", "overall_score": 0, "indicators": [], "error": str(e)}


async def gather_risk_context(symbols: Optional[list[str]] = None) -> dict[str, Any]:
    try:
        all_positions = list_positions(status="open")
        matched = []
        if symbols:
            normalized = {s.upper().strip() for s in symbols}
            matched = [p for p in all_positions if p.get("symbol", "").upper().strip() in normalized]
        else:
            matched = all_positions[:10]

        positions = []
        for p in matched:
            positions.append({
                "symbol": p.get("symbol"),
                "name": p.get("name"),
                "direction": p.get("direction"),
                "entry_price": p.get("entry_price"),
                "current_price": p.get("current_price"),
                "quantity": p.get("quantity"),
                "position_size_pct": p.get("position_size_pct"),
                "stop_loss": p.get("stop_loss"),
                "take_profit": p.get("take_profit"),
                "sector": p.get("sector"),
            })

        summary = get_risk_summary() if matched else {}

        return {
            "positions": positions,
            "total_positions": len(positions),
            "total_matched": len(matched),
            "portfolio_market_value": safe_float(summary.get("total_market_value")),
            "total_unrealized_pnl": safe_float(summary.get("total_unrealized_pnl")),
            "var_95": safe_float(summary.get("var_95")),
            "sharpe_ratio": safe_float(summary.get("sharpe_ratio"), default=0),
            "net_exposure_pct": safe_float(summary.get("net_exposure_pct")),
            "alerts": summary.get("alerts", []),
        }
    except Exception as e:
        return {"positions": [], "error": str(e)}


async def gather_data_source_evidence(
    symbol: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = 8,
) -> dict[str, Any]:
    try:
        items = list_data_items(symbol=symbol, query=query, limit=limit, sort="time_desc")
        evidence = []
        for item in items:
            # list_data_items 返回 DataSourceItemRecord(Pydantic)——按属性访问，不能 .get()。
            evidence.append({
                "id": item.id,
                "title": item.title or item.text_preview or "",
                "summary": item.text_preview or "",
                "source": item.source_name or "",
                "url": item.url or "",
                "credibility": item.credibility_score,
                "tags": list(item.tags or []),
                "collected_at": item.collected_at or item.created_at or "",
            })
        return {"evidence_items": evidence, "total_found": len(evidence), "symbol": symbol, "query": query}
    except Exception as e:
        return {"evidence_items": [], "error": str(e)}


async def gather_professional_metrics(symbol: Optional[str] = None) -> dict[str, Any]:
    try:
        from .professional_research import list_professional_metrics
        metrics = list_professional_metrics(symbol=symbol, limit=20)
        result = []
        for m in metrics:
            # list_professional_metrics 返回 ProfessionalMetricRecord(Pydantic)——按属性访问，
            # 且字段对齐真实 schema（metric_label/value/unit/raw_value/confidence/created_at）。
            result.append({
                "metric_name": m.metric_label or m.metric_key,
                "symbol": m.symbol or "",
                "metric_value": m.value if m.value is not None else m.raw_value,
                "metric_unit": m.unit or "",
                "period": m.period or "",
                "confidence": m.confidence,
                "excerpt": (m.source_excerpt or "")[:160],
                "extracted_at": m.created_at or "",
            })
        return {"metrics": result, "total": len(result), "symbol": symbol}
    except Exception as e:
        return {"metrics": [], "error": str(e)}


async def gather_supply_chain_context(symbol: Optional[str] = None) -> dict[str, Any]:
    try:
        from .ai_supply_chain import fetch_ai_supply_chain_capacity_trends
        trends = await fetch_ai_supply_chain_capacity_trends(horizon="3m")
        summary = trends.get("summary", {})
        return {
            "has_data": bool(summary),
            "summary": str(summary)[:500] if summary else "",
        }
    except Exception:
        return {"has_data": False}


async def gather_customs_trade_context() -> dict[str, Any]:
    try:
        from .customs_trade import fetch_customs_trade_snapshot
        snapshot = await fetch_customs_trade_snapshot()
        return {
            "has_data": bool(snapshot.get("items")),
            "item_count": len(snapshot.get("items", [])),
            "update_time": snapshot.get("update_time", ""),
        }
    except Exception:
        return {"has_data": False}


async def gather_backtest_context(symbol: str) -> dict[str, Any]:
    try:
        from .backtest_executor import list_backtest_results
        data = await list_backtest_results(symbol)
        bts = data.get("backtests", [])
        results = []
        for bt in bts[:5]:
            metrics = (bt.get("result") or {}).get("metrics", {})
            results.append({
                "name": bt.get("name", ""),
                "strategy": bt.get("strategy_type", ""),
                "status": bt.get("status", ""),
                "return_pct": metrics.get("total_return_pct"),
                "sharpe": metrics.get("sharpe_ratio"),
                "max_dd_pct": metrics.get("max_drawdown_pct"),
            })
        return {"backtests": results, "total": len(bts), "symbol": symbol.upper()}
    except Exception:
        return {"backtests": [], "total": 0}


async def gather_all_for_stock(
    symbol: str,
    *,
    include_macro: bool = True,
    include_risk: bool = True,
    include_evidence: bool = True,
    include_metrics: bool = True,
    include_supply_chain: bool = False,
    include_trade: bool = False,
) -> dict[str, Any]:
    symbol_norm = symbol.upper().strip()
    tasks: dict[str, Any] = {}

    tasks["quotes"] = gather_market_quotes([symbol_norm])

    if include_macro:
        tasks["macro"] = gather_macro_brief()
    if include_risk:
        tasks["risk"] = gather_risk_context([symbol_norm])
    if include_evidence:
        tasks["evidence"] = gather_data_source_evidence(symbol=symbol_norm)
    if include_metrics:
        tasks["metrics"] = gather_professional_metrics(symbol=symbol_norm)
    if include_supply_chain:
        tasks["supply_chain"] = gather_supply_chain_context(symbol_norm)
    if include_trade:
        tasks["trade"] = gather_customs_trade_context()

    # 各子模块（行情/宏观/风险/证据/指标/供应链/海关）相互独立，并发执行；
    # return_exceptions=True 保持原「单项失败转 {error} 不拖累其它」的语义。
    results = {}
    keys = list(tasks.keys())
    outcomes = await asyncio.gather(*tasks.values(), return_exceptions=True)
    for key, outcome in zip(keys, outcomes):
        results[key] = {"error": str(outcome)} if isinstance(outcome, Exception) else outcome

    return {
        "symbol": symbol_norm,
        "generated_at": utc_now_iso(),
        "modules_available": list(results.keys()),
        **results,
    }


def build_injection_block(aggregated: dict[str, Any]) -> str:
    lines: list[str] = []
    symbol = aggregated.get("symbol", "")

    quotes = aggregated.get("quotes", {})
    quote_list = quotes.get("quotes", [])
    if quote_list:
        q = quote_list[0]
        lines.append(f"[实时行情] {symbol}: ${q.get('price')} 涨跌: {q.get('change_pct', 0):+.2f}% 成交量: {q.get('volume')} 最高:{q.get('high')} 最低:{q.get('low')} 来源:{quotes.get('source')}")

    macro = aggregated.get("macro", {})
    if macro.get("indicators"):
        macro_items = macro["indicators"][:10]
        macro_str = " | ".join(f"{m['name']} {m['value']}{m.get('unit','')}({m.get('signal','')})" for m in macro_items)
        lines.append(f"[宏观环境] 综合信号:{macro.get('overall_signal')} 评分:{macro.get('overall_score')} | {macro_str}")

    risk = aggregated.get("risk", {})
    positions = risk.get("positions", [])
    if positions:
        risk_str = "; ".join(f"{p['symbol']} {p['direction']} {p['quantity']}股 @{p.get('entry_price')} 现价@{p.get('current_price')}" for p in positions[:5])
        lines.append(f"[持仓风险] 已匹配{risk.get('total_matched')}笔持仓 组合市值:{risk.get('portfolio_market_value')} VaR95:{risk.get('var_95')} | {risk_str}")

    evidence = aggregated.get("evidence", {})
    evidence_items = evidence.get("evidence_items", [])
    if evidence_items:
        ev_str = "; ".join(f"[{e['title'][:40]}]" for e in evidence_items[:5])
        lines.append(f"[数据源证据] 找到{evidence.get('total_found')}条相关: {ev_str}")

    metrics = aggregated.get("metrics", {})
    metric_list = metrics.get("metrics", [])
    if metric_list:
        mt_str = "; ".join(f"{m['metric_name']}={m['metric_value']}{m.get('metric_unit','')}" for m in metric_list[:8])
        lines.append(f"[财报指标] {metrics.get('total')}项: {mt_str}")

    supply_chain = aggregated.get("supply_chain", {})
    if supply_chain.get("has_data"):
        lines.append(f"[供应链] {supply_chain.get('summary', '')}")

    trade = aggregated.get("trade", {})
    if trade.get("has_data"):
        lines.append(f"[海关贸易] {trade.get('item_count')}条记录")

    lines.append(f"\n以上是系统自动为你聚合的跨模块实测数据。请基于这些最新数据回答用户问题。\n")
    return "\n".join(lines)