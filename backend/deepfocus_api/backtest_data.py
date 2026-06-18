from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta
from typing import Any, Optional

from .shared_utils import safe_float, utc_now_iso

CACHE_TTL_SECONDS = 600
_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _cache_key(symbol: str, start: str, end: str, interval: str) -> str:
    return f"{symbol.upper()}|{start}|{end}|{interval}"


async def fetch_historical_ohlcv(
    symbol: str,
    start_date: str,
    end_date: str,
    interval: str = "1d",
) -> dict[str, Any]:
    key = _cache_key(symbol, start_date, end_date, interval)
    now_ts = datetime.utcnow().timestamp()
    if key in _cache:
        ts, data = _cache[key]
        if now_ts - ts < CACHE_TTL_SECONDS:
            return {"symbol": symbol.upper(), "bars": data, "source": "cache", "cached": True, "interval": interval}

    try:
        result = await _fetch_yfinance(symbol, start_date, end_date, interval)
        _cache[key] = (now_ts, result.get("bars", []))
        return result
    except Exception:
        try:
            result = await _fetch_synthetic(symbol, start_date, end_date)
            _cache[key] = (now_ts, result.get("bars", []))
            return result
        except Exception as e:
            return {"symbol": symbol.upper(), "bars": [], "source": "error", "error": str(e), "interval": interval}


async def _fetch_yfinance(symbol: str, start: str, end: str, interval: str = "1d") -> dict[str, Any]:
    try:
        yf = await asyncio.to_thread(_import_yf)
        ticker = yf.Ticker(symbol.upper())
        df = await asyncio.to_thread(
            ticker.history, start=start, end=end, interval=interval, auto_adjust=True
        )
        if df.empty:
            return {"symbol": symbol.upper(), "bars": [], "source": "yfinance_error", "error": "no data returned"}

        bars = []
        for idx, row in df.iterrows():
            bars.append({
                "date": str(idx.date()) if hasattr(idx, "date") else str(idx)[:10],
                "open": round(safe_float(row.get("Open")) or 0, 4),
                "high": round(safe_float(row.get("High")) or 0, 4),
                "low": round(safe_float(row.get("Low")) or 0, 4),
                "close": round(safe_float(row.get("Close")) or 0, 4),
                "volume": int(safe_float(row.get("Volume")) or 0),
            })

        return {
            "symbol": symbol.upper(),
            "bars": bars,
            "source": "yfinance",
            "total_bars": len(bars),
            "interval": interval,
        }
    except Exception as e:
        raise RuntimeError(f"yfinance fetch failed: {e}") from e


async def _fetch_synthetic(symbol: str, start: str, end: str) -> dict[str, Any]:
    try:
        start_dt = datetime.strptime(start[:10], "%Y-%m-%d")
        end_dt = datetime.strptime(end[:10], "%Y-%m-%d")
    except ValueError:
        start_dt = datetime(2020, 1, 1)
        end_dt = datetime.utcnow()

    days = (end_dt - start_dt).days
    if days < 1:
        return {"symbol": symbol.upper(), "bars": [], "source": "synthetic", "error": "date range too short"}

    import random
    seed = sum(ord(c) for c in symbol.upper())
    rng = random.Random(seed)
    price = 100.0 + rng.uniform(0, 200)
    vol_base = 1_000_000 + seed * 1000
    bars = []
    current = start_dt
    for _ in range(min(days, 252 * 3)):
        change_pct = rng.gauss(0.0005, 0.018)
        price = max(0.1, price * (1 + change_pct))
        daily_vol = rng.gauss(0.00015, 0.012)
        price *= (1 + daily_vol)
        high = price * (1 + abs(rng.gauss(0, 0.008)))
        low = price * (1 - abs(rng.gauss(0, 0.008)))
        close = price
        open_price = close * (1 + rng.gauss(0, 0.003))
        vol = int(vol_base * (0.5 + abs(rng.gauss(0, 0.3))))
        bars.append({
            "date": current.strftime("%Y-%m-%d"),
            "open": round(open_price, 2),
            "high": round(max(open_price, high, close), 2),
            "low": round(min(open_price, low, close), 2),
            "close": round(close, 2),
            "volume": vol,
        })
        current += timedelta(days=1)
        while current.weekday() > 4:
            current += timedelta(days=1)

    return {
        "symbol": symbol.upper(),
        "bars": bars,
        "source": "synthetic",
        "total_bars": len(bars),
        "interval": "1d",
    }


def _import_yf():
    import yfinance as yf
    return yf


def extract_price_series(bars: list[dict[str, Any]], field: str = "close") -> list[float]:
    return [safe_float(b.get(field)) or 0 for b in bars]


def bars_to_dataframe(bars: list[dict[str, Any]]) -> dict[str, list]:
    return {
        "dates": [b.get("date", "") for b in bars],
        "open": [safe_float(b.get("open")) or 0 for b in bars],
        "high": [safe_float(b.get("high")) or 0 for b in bars],
        "low": [safe_float(b.get("low")) or 0 for b in bars],
        "close": [safe_float(b.get("close")) or 0 for b in bars],
        "volume": [int(safe_float(b.get("volume")) or 0) for b in bars],
    }
