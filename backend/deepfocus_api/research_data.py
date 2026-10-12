"""Reuse verified platform history with explicit currency and provenance."""
from datetime import date, timedelta
import asyncio
import re


async def resolve_platform_history(tool, data):
    if tool not in ("portfolio", "factors") or not data.get("symbols"):
        return data, [], []
    if data.get("prices") or data.get("content_base64") or data.get("file_name"):
        raise ValueError("在线行情查询不能与导入价格文件混用")
    symbols = data["symbols"]
    if isinstance(symbols, str):
        symbols = [s for s in re.split(r"[,;，\s]+", symbols.strip()) if s]
    if not isinstance(symbols, list) or not symbols or len(symbols) > 20 or any(not isinstance(s, str) for s in symbols):
        raise ValueError("请输入 1–20 个资产代码")
    symbols = [s.strip().upper() for s in symbols]
    if len(set(symbols)) != len(symbols) or any(not re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{0,19}", s) for s in symbols):
        raise ValueError("资产代码无效或重复")
    from .backtest_data import fetch_historical_ohlcv, normalize_market_symbol
    metadata = [normalize_market_symbol(s) for s in symbols]
    currencies = {"CN": "CNY", "HK": "HKD", "US": "USD"}
    expected = {currencies.get(m["market"]) for m in metadata}
    if None in expected or len(expected) != 1:
        raise ValueError("在线分析只支持同一估值币种；跨币种请导入已正确换汇的价格")
    if data.get("currency") not in expected:
        raise ValueError("请确认资产所在市场的估值币种：" + "/".join(sorted(expected)))
    normalized = {(m["market"], m["symbol"], m.get("exchange")) for m in metadata}
    if len(normalized) != len(symbols):
        raise ValueError("输入包含同一资产的多个代码别名")
    try:
        start = date.fromisoformat(str(data.get("start_date", "")))
        end = date.fromisoformat(str(data.get("end_date", "")))
    except ValueError:
        raise ValueError("请填写 YYYY-MM-DD 的开始和结束日期")
    latest = date.today() - timedelta(days=1)
    actual_end = min(end, latest)
    if start >= actual_end or (actual_end - start).days > 365 * 5:
        raise ValueError("历史区间必须在过去，且不超过 5 年")
    slots = asyncio.Semaphore(2)
    async def fetch(symbol):
        async with slots:
            result = await fetch_historical_ohlcv(symbol, start.isoformat(), actual_end.isoformat())
        if result.get("is_synthetic") or result.get("quality") not in ("verified_real", "live") or not result.get("bars"):
            raise RuntimeError(f"{symbol} 的真实历史行情暂不可用，请重试或导入合法取得的数据")
        return symbol, result
    values = await asyncio.gather(*(fetch(symbol) for symbol in symbols))
    adjustments = {str(result.get("adjustment", "")).lower() for _, result in values}
    if len(adjustments) != 1 or not adjustments <= {"qfq", "hfq", "adjusted", "total_return"}:
        raise ValueError("数据源未提供同口径的已复权价格；请导入核对过公司行动、复权方式和估值币种的行情文件。")
    prices, sources, warnings = [], [], []
    for symbol, result in values:
        for row in result["bars"]:
            prices.append({"symbol": symbol, "currency": data["currency"], "adjustment": str(result["adjustment"]).lower(),
                           **{key: row[key] for key in ("date", "open", "high", "low", "close", "volume")}})
        sources.append({"name": str(result.get("source_name") or result.get("source") or "平台历史行情") + " · " + symbol,
                        "as_of": result.get("end_date"), "currency": data["currency"],
                        "coverage": f"{len(result['bars'])} 日；复权 {result.get('adjustment','unknown')}；抓取 {result.get('fetched_at','unknown')}"})
        warnings.extend(str(w) for w in result.get("warnings", []))
    if len(prices) > 5000:
        raise ValueError("取得行情超过 5000 条，请缩短区间或减少资产数量")
    if actual_end != end:
        warnings.append("仅使用昨日及之前的行情，避免未收盘日进入统计。")
    resolved = {key: value for key, value in data.items() if key not in ("symbols", "start_date", "end_date")}
    resolved["prices"] = prices
    if resolved.get("weights"):
        resolved["weights"] = [{**w, "symbol": str(w.get("symbol", "")).strip().upper()} for w in resolved["weights"]]
    return resolved, sources, warnings
