"""分析师一致预期（美股 live）：目标价(stockanalysis) + 评级共识(nasdaq)。

直连绕代理（trust_env=False）。仅美股字母代码有覆盖；A股/港股暂无稳定 live 源 → None。缓存 30min。
"""
from __future__ import annotations

import re
import time
from typing import Optional

import httpx

from .valuation_source import _num, _pct

_CACHE: dict[str, tuple[float, Optional[dict]]] = {}
_TTL = 1800.0
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


async def fetch_analyst_consensus(symbol: str, market: Optional[str] = None) -> Optional[dict]:
    """目标价 + 较现价空间 + 评级共识（仅美股 live）。非美股/无数据/失败 → None。"""
    sym = (symbol or "").upper().strip()
    if (market or "").upper() not in ("", "US") or not sym.isalpha():
        return None
    key = f"cons:{sym}"
    hit = _CACHE.get(key)
    if hit and (time.time() - hit[0]) < _TTL:
        return hit[1]

    result: dict = {}
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=12.0) as c:
            # 目标价（stockanalysis overview.analystTarget）
            try:
                r = await c.get(
                    f"https://stockanalysis.com/api/symbol/s/{sym}/overview",
                    headers={"User-Agent": _UA, "Accept": "application/json"},
                )
                if r.status_code == 200:
                    d = (r.json().get("data") or {})
                    at = d.get("analystTarget") or {}
                    tp = _num((at.get("target") or "").replace("$", "")) or _num((str(d.get("target") or "")).split(" ")[0])
                    if tp:
                        result["target_price"] = tp
                        result["upside_pct"] = _pct(at.get("change")) if at.get("change") else _pct(d.get("target"))
            except Exception:
                pass
            # 评级共识（nasdaq analyst ratings）
            try:
                r = await c.get(
                    f"https://api.nasdaq.com/api/analyst/{sym}/ratings",
                    headers={"User-Agent": _UA, "Accept": "application/json"},
                )
                if r.status_code == 200:
                    data = (r.json().get("data") or {})
                    result["rating"] = data.get("meanRatingType")
                    m = re.search(r"(\d+)\s+analyst", data.get("ratingsSummary") or "")
                    result["analyst_count"] = int(m.group(1)) if m else None
            except Exception:
                pass
    except Exception:
        return None

    if result.get("target_price") or result.get("rating"):
        result["provider"] = "nasdaq"
        _CACHE[key] = (time.time(), result)
        return result
    _CACHE[key] = (time.time(), None)
    return None
