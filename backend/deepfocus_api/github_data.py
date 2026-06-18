"""github 托管的真实金融数据源（环境下唯一可达的外部真实数据）。

本机出网受限：行情/财报/宏观域名一律不可达，仅 github 白名单可达。
这里从 github datasets 拉取真实的标普500 月度指数与成分（GICS 行业 / CIK），
用于给证据引擎提供一块**真实、可验证、非 mock** 的市场环境数据。

数据为月度/快照级（非实时 tick），因此用于"市场环境/行业归属"等中期维度，
并在可信度上据实标注；拉取失败则诚实返回空，由引擎标 insufficient。
"""

from __future__ import annotations

import csv
import io
import time
from typing import Optional

import httpx

_SP500_INDEX_URL = "https://raw.githubusercontent.com/datasets/s-and-p-500/master/data/data.csv"
_SP500_CONSTITUENTS_URL = (
    "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv"
)
_CACHE_TTL = 6 * 3600  # github 数据月度更新，缓存 6 小时足够
_cache: dict = {}


async def _fetch_text(url: str, timeout: float = 12.0) -> Optional[str]:
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=True) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.text
    except Exception:
        return None
    return None


async def fetch_sp500_index_history(months: int = 13, force: bool = False) -> list[tuple[str, float]]:
    """标普500 月度收盘历史（真实，github datasets）。不可达则返回空列表。"""
    cached = _cache.get("index")
    if not force and cached and (time.time() - cached[0]) < _CACHE_TTL:
        return cached[1][-months:]
    text = await _fetch_text(_SP500_INDEX_URL, timeout=25.0)
    if not text:
        return []
    data: list[tuple[str, float]] = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) >= 2:
            try:
                close = float(row[1])
            except ValueError:
                continue
            if close > 0:
                data.append((row[0], close))
    _cache["index"] = (time.time(), data)
    return data[-months:]


async def fetch_sp500_constituents() -> dict[str, dict]:
    """标普500 成分 → {symbol: {name, sector, sub_industry, cik}}（真实，github datasets）。"""
    cached = _cache.get("constituents")
    if cached and (time.time() - cached[0]) < _CACHE_TTL:
        return cached[1]
    text = await _fetch_text(_SP500_CONSTITUENTS_URL)
    if not text:
        return {}
    result: dict[str, dict] = {}
    for row in csv.DictReader(io.StringIO(text)):
        sym = (row.get("Symbol") or "").strip().upper()
        if sym:
            result[sym] = {
                "name": (row.get("Security") or "").strip(),
                "sector": (row.get("GICS Sector") or "").strip(),
                "sub_industry": (row.get("GICS Sub-Industry") or "").strip(),
                "cik": (row.get("CIK") or "").strip(),
            }
    _cache["constituents"] = (time.time(), result)
    return result


async def fetch_sp500_constituent(symbol: str) -> Optional[dict]:
    """单个标的的标普500 成分信息（含 GICS 行业），非成分股返回 None。"""
    table = await fetch_sp500_constituents()
    return table.get((symbol or "").strip().upper())


_US10Y_URL = "https://raw.githubusercontent.com/datasets/bond-yields-us-10y/main/data/monthly.csv"


async def fetch_us10y_history(months: int = 13, force: bool = False) -> list[tuple[str, float]]:
    """美国10年期国债收益率月度（真实最新，github datasets）。不可达则返回空列表。"""
    cached = _cache.get("us10y")
    if not force and cached and (time.time() - cached[0]) < _CACHE_TTL:
        return cached[1][-months:]
    text = await _fetch_text(_US10Y_URL)
    if not text:
        return []
    data: list[tuple[str, float]] = []
    rows = csv.reader(io.StringIO(text))
    next(rows, None)  # 跳过 header: Date,Rate
    for row in rows:
        if len(row) >= 2:
            try:
                rate = float(row[1])
            except ValueError:
                continue
            data.append((row[0], rate))
    _cache["us10y"] = (time.time(), data)
    return data[-months:]


_OIL_URL = "https://raw.githubusercontent.com/datasets/oil-prices/main/data/wti-daily.csv"
_GOLD_URL = "https://raw.githubusercontent.com/datasets/gold-prices/main/data/monthly.csv"


async def _fetch_series(url: str, cache_key: str, count: int, timeout: float = 12.0, force: bool = False) -> list[tuple[str, float]]:
    """通用 Date,Value CSV 拉取（github datasets）。失败返回空，不缓存空。"""
    cached = _cache.get(cache_key)
    if not force and cached and (time.time() - cached[0]) < _CACHE_TTL:
        return cached[1][-count:]
    text = await _fetch_text(url, timeout=timeout)
    if not text:
        return []
    data: list[tuple[str, float]] = []
    rows = csv.reader(io.StringIO(text))
    next(rows, None)  # header
    for row in rows:
        if len(row) >= 2:
            try:
                value = float(row[1])
            except ValueError:
                continue
            if value != 0:
                data.append((row[0], value))
    if data:
        _cache[cache_key] = (time.time(), data)
    return data[-count:]


async def fetch_oil_history(points: int = 20, force: bool = False) -> list[tuple[str, float]]:
    """WTI 原油价格（真实最新日度，github datasets）。"""
    return await _fetch_series(_OIL_URL, "oil", points, force=force)


async def fetch_gold_history(months: int = 13, force: bool = False) -> list[tuple[str, float]]:
    """黄金价格（真实最新月度，github datasets）。"""
    return await _fetch_series(_GOLD_URL, "gold", months, force=force)
