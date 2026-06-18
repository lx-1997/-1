"""Nasdaq 财报数据——盈利质量（EPS beat/miss 历史 + 下季预测）。

直连绕代理（httpx trust_env=False，外部 403 是沙箱代理封的）。仅美股（api.nasdaq.com
按纯字母代码查；港股/A股数字代码无此源 → 返回 None，上层诚实标 insufficient）。缓存 6h。
"""
from __future__ import annotations

import time
from typing import Optional

import httpx

_CACHE: dict[str, tuple[float, Optional[dict]]] = {}
_CACHE_TTL = 6 * 3600.0
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept": "application/json"}


async def fetch_nasdaq_earnings(symbol: str) -> Optional[dict]:
    """美股盈利质量：近几季 beat/miss + 下季 EPS 预测。失败/非美股返回 None（优雅降级）。"""
    sym = (symbol or "").upper().strip()
    if not sym or not sym.isalpha():  # nasdaq 仅认美股字母代码
        return None
    hit = _CACHE.get(sym)
    if hit and (time.time() - hit[0]) < _CACHE_TTL:
        return hit[1]

    result: Optional[dict] = None
    try:
        url = f"https://api.nasdaq.com/api/quote/{sym}/eps"
        async with httpx.AsyncClient(trust_env=False, timeout=12.0) as client:
            r = await client.get(url, headers=_HEADERS)
        if r.status_code == 200:
            eps = ((r.json().get("data") or {}).get("earningsPerShare")) or []
            history = [
                {"period": e.get("period"), "consensus": e.get("consensus"), "earnings": e.get("earnings")}
                for e in eps
                if e.get("type") == "PreviousQuarter" and e.get("consensus") and e.get("earnings")
            ]
            upcoming = [e for e in eps if e.get("type") == "UpcomingQuarter" and e.get("consensus")]
            next_consensus = upcoming[0].get("consensus") if upcoming else None
            if history or next_consensus:
                result = {"history": history, "next_consensus": next_consensus}
    except Exception:
        result = None

    _CACHE[sym] = (time.time(), result)
    return result


_CACHE_OPT: dict[str, tuple[float, Optional[dict]]] = {}


async def fetch_nasdaq_options(symbol: str) -> Optional[dict]:
    """美股期权情绪：Put/Call 量比 + 持仓比（nasdaq option-chain，直连绕代理）。失败/非美股返回 None。"""
    from datetime import datetime, timedelta, timezone

    sym = (symbol or "").upper().strip()
    if not sym or not sym.isalpha():
        return None
    hit = _CACHE_OPT.get(sym)
    if hit and (time.time() - hit[0]) < _CACHE_TTL:
        return hit[1]

    def _n(s) -> float:
        if not s or s in ("--", ""):
            return 0.0
        try:
            return float(str(s).replace(",", ""))
        except ValueError:
            return 0.0

    result: Optional[dict] = None
    try:
        today = datetime.now(timezone.utc)
        fr = today.strftime("%Y-%m-%d")
        to = (today + timedelta(days=75)).strftime("%Y-%m-%d")
        url = (
            f"https://api.nasdaq.com/api/quote/{sym}/option-chain"
            f"?assetclass=stocks&limit=300&fromdate={fr}&todate={to}"
        )
        async with httpx.AsyncClient(trust_env=False, timeout=15.0) as client:
            r = await client.get(url, headers=_HEADERS)
        if r.status_code == 200:
            rows = (((r.json().get("data") or {}).get("table") or {}).get("rows")) or []
            cv = sum(_n(x.get("c_Volume")) for x in rows)
            pv = sum(_n(x.get("p_Volume")) for x in rows)
            coi = sum(_n(x.get("c_Openinterest")) for x in rows)
            poi = sum(_n(x.get("p_Openinterest")) for x in rows)
            if cv > 0:
                result = {
                    "pc_volume_ratio": round(pv / cv, 3),
                    "pc_oi_ratio": round(poi / coi, 3) if coi else None,
                }
    except Exception:
        result = None

    _CACHE_OPT[sym] = (time.time(), result)
    return result
