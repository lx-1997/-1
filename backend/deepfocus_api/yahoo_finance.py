"""Yahoo Finance 历史行情——官方 JSON API，提供个股 OHLCV 历史（个股价格趋势图）。

环境真相：外部域名的 403 是沙箱出网代理（HTTPS_PROXY=10.9.0.31:8838）封的，**直连可达**。
故本模块用 httpx `trust_env=False` 直连绕代理。yahoo v8 chart 返回 meta（当前 quote）
+ timestamp/close 历史，覆盖美股 / 港股(.HK) / A股(.SS 沪 /.SZ 深)。缓存 30min（日线变动慢）。
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Optional

import httpx

_CACHE: dict[str, tuple[float, list]] = {}
_CACHE_TTL = 1800.0  # 30min

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _to_yahoo_symbol(symbol: str, market: Optional[str] = None) -> str:
    """转 Yahoo 代码：美股原样，港股加 .HK，A股 6/9 开头→.SS 沪、其余→.SZ 深。"""
    s = re.sub(r"\.(US|O|N|OQ)$", "", (symbol or "").upper()).strip()
    if s.endswith((".HK", ".SS", ".SZ")):
        return s
    raw = (market or "").upper()
    if raw == "HK":
        return f"{s}.HK"
    if raw == "CN" or re.match(r"^\d{6}$", s):
        return f"{s}.SS" if s.startswith(("6", "9")) else f"{s}.SZ"
    return s


async def fetch_yahoo_history(symbol: str, market: Optional[str] = None, range_: str = "6mo") -> list:
    """个股日线收盘历史 [(date, close), ...]。直连绕代理（trust_env=False）。失败返回 []（优雅降级）。"""
    ysym = _to_yahoo_symbol(symbol, market)
    if not ysym:
        return []
    key = f"{ysym}:{range_}"
    hit = _CACHE.get(key)
    if hit and (time.time() - hit[0]) < _CACHE_TTL:
        return hit[1]

    out: list = []
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ysym}?range={range_}&interval=1d"
        async with httpx.AsyncClient(trust_env=False, timeout=15.0) as client:
            r = await client.get(url, headers=_HEADERS)
        if r.status_code == 200:
            res = r.json()["chart"]["result"][0]
            ts = res.get("timestamp") or []
            closes = (res.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
            for t, c in zip(ts, closes):
                if c is None:
                    continue
                d = datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")
                out.append((d, round(float(c), 2)))
    except Exception:
        out = []

    _CACHE[key] = (time.time(), out)
    return out


async def fetch_yahoo_quote(symbol: str, market: Optional[str] = None) -> Optional[dict]:
    """Yahoo 官方 quote（price/昨收/日内高低/成交量/52周，全 live）。直连绕代理。失败 None。

    用 range=5d 的 meta（实时报价）+ closes[-2]（昨收）。比 google finance 网页爬取更权威 → live。
    """
    ysym = _to_yahoo_symbol(symbol, market)
    if not ysym:
        return None
    key = f"q:{ysym}"
    hit = _CACHE.get(key)
    if hit and (time.time() - hit[0]) < 300:  # quote 缓存 5min
        return hit[1]

    result: Optional[dict] = None
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ysym}?range=5d&interval=1d"
        async with httpx.AsyncClient(trust_env=False, timeout=15.0) as client:
            r = await client.get(url, headers=_HEADERS)
        if r.status_code == 200:
            res = r.json()["chart"]["result"][0]
            meta = res.get("meta") or {}
            price = meta.get("regularMarketPrice")
            closes = [c for c in ((res.get("indicators", {}).get("quote") or [{}])[0].get("close") or []) if c is not None]
            prev = closes[-2] if len(closes) >= 2 else meta.get("chartPreviousClose")
            if price and prev:
                result = {
                    "symbol": symbol.upper(),
                    "name": meta.get("shortName") or meta.get("longName"),
                    "price": round(float(price), 4),
                    "previous_close": round(float(prev), 4),
                    "high": meta.get("regularMarketDayHigh"),
                    "low": meta.get("regularMarketDayLow"),
                    "volume": meta.get("regularMarketVolume"),
                    "wk52_high": meta.get("fiftyTwoWeekHigh"),
                    "wk52_low": meta.get("fiftyTwoWeekLow"),
                    "currency": meta.get("currency", "USD"),
                    "provider": "yahoo-finance",
                }
    except Exception:
        result = None

    _CACHE[key] = (time.time(), result)
    return result
