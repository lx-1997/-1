from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from .shared_utils import safe_float, utc_now_iso


CACHE_TTL_SECONDS = 3600
PROVIDER_TIMEOUT_SECONDS = 8.0
MAX_BARS = 1000
_cache: dict[str, tuple[float, dict[str, Any]]] = {}

_HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
}


def normalize_market_symbol(symbol: str, market: Optional[str] = None) -> dict[str, str]:
    """Normalize a terminal symbol without forcing a mixed watchlist into one market."""
    raw = str(symbol or "").strip().upper()
    hint = str(market or "").strip().upper()
    if not raw:
        return {"requested": "", "symbol": "", "market": "OTHER", "provider_symbol": ""}
    if raw.endswith((".SH", ".SZ", ".BJ")):
        code = re.sub(r"\D", "", raw)
        exchange = raw.rsplit(".", 1)[-1]
        return {"requested": raw, "symbol": code, "market": "CN", "exchange": exchange, "provider_symbol": _tencent_cn_code(code, exchange)}
    if raw.endswith(".HK"):
        code = re.sub(r"\D", "", raw).zfill(5)
        return {"requested": raw, "symbol": code, "market": "HK", "exchange": "HK", "provider_symbol": f"hk{code}"}
    if raw.endswith(".US"):
        code = raw[:-3]
        return {"requested": raw, "symbol": code, "market": "US", "exchange": "US", "provider_symbol": code}
    if re.fullmatch(r"\d{6}", raw):
        exchange = _infer_cn_exchange(raw)
        return {"requested": raw, "symbol": raw, "market": "CN", "exchange": exchange, "provider_symbol": _tencent_cn_code(raw, exchange)}
    if re.fullmatch(r"\d{1,5}", raw):
        code = raw.zfill(5)
        return {"requested": raw, "symbol": code, "market": "HK", "exchange": "HK", "provider_symbol": f"hk{code}"}
    code = raw.replace(" ", "")
    if code in {"HSI", "HSCEI", "HSTECH"}:
        return {"requested": raw, "symbol": code, "market": "HK", "exchange": "HK", "provider_symbol": f"hk{code}"}
    if re.fullmatch(r"[A-Z][A-Z0-9._-]{0,14}", code):
        return {"requested": raw, "symbol": code, "market": "US", "exchange": "US", "provider_symbol": code}
    return {"requested": raw, "symbol": raw, "market": "OTHER", "exchange": "", "provider_symbol": ""}


def _infer_cn_exchange(code: str) -> str:
    if code[:1] in {"4", "8"} or code[:2] == "92":
        return "BJ"
    if code[:1] in {"5", "6", "9"} or code[:2] == "90":
        return "SH"
    return "SZ"


def _tencent_cn_code(code: str, exchange: str) -> str:
    prefix = {"SH": "sh", "SZ": "sz", "BJ": "bj"}.get(exchange, "sz")
    return f"{prefix}{code}"


def _cache_key(symbol: str, start: str, end: str, interval: str, market: Optional[str]) -> str:
    normalized = normalize_market_symbol(symbol, market)
    return f"{normalized['market']}|{normalized['symbol']}|{start}|{end}|{interval}"


def _client(*, referer: str = "") -> httpx.AsyncClient:
    headers = dict(_HTTP_HEADERS)
    if referer:
        headers["Referer"] = referer
    return httpx.AsyncClient(
        trust_env=False,
        timeout=PROVIDER_TIMEOUT_SECONDS,
        headers=headers,
        transport=httpx.AsyncHTTPTransport(local_address="0.0.0.0", retries=1),
    )


def _number(value: Any) -> float:
    if value is None:
        return 0.0
    return safe_float(str(value).replace("$", "").replace(",", "").strip(), 0.0)


def _valid_date(value: str) -> bool:
    try:
        datetime.strptime(str(value)[:10], "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _clean_bars(rows: list[dict[str, Any]], start_date: str, end_date: str) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for row in rows:
        date = str(row.get("date") or row.get("d") or "")[:10]
        if not _valid_date(date) or (start_date and date < start_date) or (end_date and date > end_date):
            continue
        open_price = _number(row.get("open", row.get("o")))
        close = _number(row.get("close", row.get("c")))
        high = _number(row.get("high", row.get("h")))
        low = _number(row.get("low", row.get("l")))
        volume = max(0.0, _number(row.get("volume", row.get("v"))))
        if min(open_price, close, high, low) <= 0:
            continue
        if high + 1e-8 < max(open_price, close) or low - 1e-8 > min(open_price, close):
            continue
        deduped[date] = {
            "date": date,
            "open": round(open_price, 4),
            "high": round(high, 4),
            "low": round(low, 4),
            "close": round(close, 4),
            "volume": round(volume, 4),
        }
    return [deduped[key] for key in sorted(deduped)][-MAX_BARS:]


async def _fetch_tencent(meta: dict[str, str], start_date: str, end_date: str) -> dict[str, Any]:
    provider_symbol = meta["provider_symbol"]
    if not provider_symbol:
        raise RuntimeError("unsupported Tencent symbol")
    params = {"param": f"{provider_symbol},day,{start_date},{end_date},{MAX_BARS},qfq"}
    async with _client(referer="https://gu.qq.com/") as client:
        response = await client.get("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get", params=params)
    response.raise_for_status()
    payload = ((response.json().get("data") or {}).get(provider_symbol) or {})
    raw_rows = payload.get("qfqday") or payload.get("day") or []
    rows = []
    for item in raw_rows:
        if not isinstance(item, list) or len(item) < 5:
            continue
        rows.append({"date": item[0], "open": item[1], "close": item[2], "high": item[3], "low": item[4], "volume": item[5] if len(item) > 5 else 0})
    bars = _clean_bars(rows, start_date, end_date)
    if len(bars) < 2:
        raise RuntimeError("Tencent returned too few valid rows")
    adjusted = bool(payload.get("qfqday"))
    return {
        "bars": bars,
        "source": "tencent_qfq" if adjusted else "tencent",
        "source_name": "腾讯证券前复权" if adjusted else "腾讯证券",
        "adjustment": "qfq" if adjusted else "none",
        "is_synthetic": False,
        "warnings": [] if adjusted else ["该市场数据源仅返回不复权日线，公司行动日附近的收益需谨慎解读"],
    }


async def _fetch_sina_cn(meta: dict[str, str], start_date: str, end_date: str) -> dict[str, Any]:
    if meta["market"] != "CN":
        raise RuntimeError("Sina daily source only supports CN")
    params = {"symbol": meta["provider_symbol"], "scale": "240", "ma": "no", "datalen": str(MAX_BARS)}
    url = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
    async with _client(referer="https://finance.sina.com.cn/") as client:
        response = await client.get(url, params=params)
    response.raise_for_status()
    payload = response.json()
    bars = _clean_bars(payload if isinstance(payload, list) else [], start_date, end_date)
    if len(bars) < 2:
        raise RuntimeError("Sina returned too few valid rows")
    return {"bars": bars, "source": "sina_cn", "source_name": "新浪财经 A股日线", "adjustment": "none", "is_synthetic": False}


async def _fetch_nasdaq_us(meta: dict[str, str], start_date: str, end_date: str) -> dict[str, Any]:
    if meta["market"] != "US":
        raise RuntimeError("Nasdaq historical source only supports US")
    url = f"https://api.nasdaq.com/api/quote/{meta['provider_symbol']}/historical"
    raw_rows: list[dict[str, Any]] = []
    asset_class = ""
    async with _client(referer="https://www.nasdaq.com/") as client:
        for candidate in ("stocks", "etf"):
            params = {"assetclass": candidate, "fromdate": start_date, "todate": end_date, "limit": "9999"}
            response = await client.get(url, params=params)
            response.raise_for_status()
            raw_rows = ((((response.json().get("data") or {}).get("tradesTable") or {}).get("rows")) or [])
            if raw_rows:
                asset_class = candidate
                break
    rows = []
    for item in raw_rows:
        try:
            parsed_date = datetime.strptime(str(item.get("date") or ""), "%m/%d/%Y").strftime("%Y-%m-%d")
        except ValueError:
            continue
        rows.append({"date": parsed_date, "open": item.get("open"), "close": item.get("close"), "high": item.get("high"), "low": item.get("low"), "volume": item.get("volume")})
    bars = _clean_bars(rows, start_date, end_date)
    if len(bars) < 2:
        raise RuntimeError("Nasdaq returned too few valid rows")
    return {
        "bars": bars,
        "source": "nasdaq",
        "source_name": "Nasdaq 历史行情",
        "adjustment": "provider_reported",
        "asset_class": asset_class,
        "is_synthetic": False,
        "warnings": ["价格复权口径以 Nasdaq 公开接口返回值为准"],
    }


async def fetch_historical_ohlcv(
    symbol: str,
    start_date: str,
    end_date: str,
    interval: str = "1d",
    *,
    market: Optional[str] = None,
) -> dict[str, Any]:
    """Fetch verified daily OHLCV; never fabricate synthetic production bars."""
    meta = normalize_market_symbol(symbol, market)
    key = _cache_key(symbol, start_date, end_date, interval, market)
    now_ts = time.time()
    hit = _cache.get(key)
    if hit and now_ts - hit[0] < CACHE_TTL_SECONDS:
        cached = dict(hit[1])
        cached["cached"] = True
        return cached
    if interval != "1d":
        return _error_payload(meta, interval, [f"unsupported interval: {interval}"])
    if not _valid_date(start_date) or not _valid_date(end_date) or start_date >= end_date:
        return _error_payload(meta, interval, ["回测日期无效，开始日期必须早于结束日期"])
    providers = {"CN": (_fetch_tencent, _fetch_sina_cn), "HK": (_fetch_tencent,), "US": (_fetch_nasdaq_us,)}.get(meta["market"], ())
    errors: list[str] = []
    for provider in providers:
        try:
            payload = await asyncio.wait_for(provider(meta, start_date, end_date), timeout=PROVIDER_TIMEOUT_SECONDS + 1)
            provider_warnings = list(payload.pop("warnings", []) or [])
            result = {
                "symbol": meta["requested"], "normalized_symbol": meta["symbol"], "market": meta["market"],
                "exchange": meta.get("exchange", ""), "provider_symbol": meta["provider_symbol"], "interval": interval,
                "total_bars": len(payload["bars"]), "start_date": payload["bars"][0]["date"], "end_date": payload["bars"][-1]["date"],
                "fetched_at": utc_now_iso(), "quality": "verified_real", "warnings": errors + provider_warnings, **payload,
            }
            _cache[key] = (now_ts, result)
            return dict(result)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{provider.__name__.replace('_fetch_', '')}: {type(exc).__name__}: {exc}")
    return _error_payload(meta, interval, errors or ["没有可用的真实历史行情源"])


def _error_payload(meta: dict[str, str], interval: str, errors: list[str]) -> dict[str, Any]:
    return {
        "symbol": meta.get("requested", ""), "normalized_symbol": meta.get("symbol", ""), "market": meta.get("market", "OTHER"),
        "exchange": meta.get("exchange", ""), "provider_symbol": meta.get("provider_symbol", ""), "bars": [], "source": "unavailable",
        "source_name": "真实行情不可用", "quality": "unavailable", "adjustment": "unknown", "is_synthetic": False,
        "warnings": errors, "error": "；".join(errors), "interval": interval, "total_bars": 0,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def extract_price_series(bars: list[dict[str, Any]], field: str = "close") -> list[float]:
    return [safe_float(bar.get(field), 0.0) for bar in bars]


def bars_to_dataframe(bars: list[dict[str, Any]]) -> dict[str, list]:
    return {
        "dates": [bar.get("date", "") for bar in bars], "open": [safe_float(bar.get("open"), 0.0) for bar in bars],
        "high": [safe_float(bar.get("high"), 0.0) for bar in bars], "low": [safe_float(bar.get("low"), 0.0) for bar in bars],
        "close": [safe_float(bar.get("close"), 0.0) for bar in bars], "volume": [int(safe_float(bar.get("volume"), 0.0)) for bar in bars],
    }
