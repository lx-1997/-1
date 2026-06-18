"""个股市值 + 估值（live）：美股 stockanalysis JSON、A股 东财 RPT_VALUEANALYSIS_DET。

直连绕代理（trust_env=False）。让 scale/valuation 维度从 google finance（degraded，网页爬取）
转 live（官方/权威源、当日）。港股复用东财 F10（RPT_HKF10_FN_MAININDICATOR，与财报同源）取市值/PE/PB（HKD）。
两源交叉验证一致（东财 600519 市值 1.591T/PE 19.24 ≈ stockanalysis 1.59T/19.20）。缓存 30min。
"""
from __future__ import annotations

import re
import time
from typing import Optional

import httpx

_CACHE: dict[str, tuple[float, Optional[dict]]] = {}
_CACHE_TTL = 1800.0
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def _num(s) -> Optional[float]:
    """'4.57T'→4.57e12、'37.73'→37.73、None/'-'→None。"""
    s = str(s if s is not None else "").strip().replace(",", "")
    if not s or s in ("-", "—", "n/a", "N/A", "0"):
        return None
    mult = 1.0
    if s and s[-1] in "TBMK":
        mult = {"T": 1e12, "B": 1e9, "M": 1e6, "K": 1e3}[s[-1]]
        s = s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return None


def _pct(s) -> Optional[float]:
    """'$1.04 (0.33%)' / '-0.97%' → 0.33 / -0.97（提取百分数数值）。"""
    m = re.search(r"(-?[\d.]+)%", str(s if s is not None else ""))
    try:
        return float(m.group(1)) if m else None
    except ValueError:
        return None


async def fetch_valuation(symbol: str, market: Optional[str] = None) -> Optional[dict]:
    """市值 + PE/PB（live）。美股→stockanalysis、A股→东财估值表、港股→东财 F10。失败 None。"""
    sym = (symbol or "").upper().strip()
    code = re.sub(r"\D", "", sym)
    mkt = (market or "").upper()
    is_a = mkt == "CN" or (len(code) == 6 and not mkt)
    is_hk = mkt == "HK" or (bool(code) and len(code) != 6)

    key = f"val:{sym}:{mkt}"
    hit = _CACHE.get(key)
    if hit and (time.time() - hit[0]) < _CACHE_TTL:
        return hit[1]

    result: Optional[dict] = None
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=12.0) as client:
            if is_a:
                url = (
                    "https://datacenter-web.eastmoney.com/api/data/v1/get"
                    "?reportName=RPT_VALUEANALYSIS_DET&columns=ALL&pageSize=1"
                    "&sortColumns=TRADE_DATE&sortTypes=-1"
                    f"&filter=(SECURITY_CODE%3D%22{code}%22)"
                )
                r = await client.get(url, headers={"User-Agent": _UA, "Referer": "https://data.eastmoney.com/"})
                if r.status_code == 200:
                    data = ((r.json().get("result") or {}).get("data")) or []
                    if data:
                        d0 = data[0]
                        result = {
                            "market_cap": d0.get("TOTAL_MARKET_CAP"),
                            "pe_ratio": d0.get("PE_TTM"),
                            "pb_ratio": d0.get("PB_MRQ"),
                            "ps_ratio": d0.get("PS_TTM"),
                            "peg": d0.get("PEG_CAR"),
                            "provider": "eastmoney",
                        }
            elif is_hk:
                # 港股 东财 F10 RPT_HKF10_FN_MAININDICATOR（与 fetch_eastmoney_earnings 同源，含市值/PE/PB，单位 HKD）
                secucode = f"{code.zfill(5)}.HK"
                url = (
                    "https://datacenter-web.eastmoney.com/api/data/v1/get"
                    "?reportName=RPT_HKF10_FN_MAININDICATOR&columns=ALL&pageSize=1"
                    "&sortColumns=REPORT_DATE&sortTypes=-1"
                    f"&filter=(SECUCODE%3D%22{secucode}%22)"
                )
                r = await client.get(url, headers={"User-Agent": _UA, "Referer": "https://emweb.securities.eastmoney.com/"})
                if r.status_code == 200:
                    data = ((r.json().get("result") or {}).get("data")) or []
                    if data:
                        d0 = data[0]
                        mc = d0.get("TOTAL_MARKET_CAP")
                        if mc:
                            result = {
                                "market_cap": mc,
                                "pe_ratio": d0.get("PE_TTM"),
                                "pb_ratio": d0.get("PB_TTM"),
                                "provider": "eastmoney",
                            }
            else:
                # 美股 stockanalysis JSON（data.marketCap='4.57T' / data.peRatio='37.73'）
                r = await client.get(
                    f"https://stockanalysis.com/api/symbol/s/{sym}/overview",
                    headers={"User-Agent": _UA, "Accept": "application/json"},
                )
                if r.status_code == 200:
                    d = (r.json().get("data") or {})
                    mc = _num(d.get("marketCap"))
                    if mc:
                        result = {
                            "market_cap": mc,
                            "pe_ratio": _num(d.get("peRatio")),
                            "pb_ratio": None,
                            "forward_pe": _num(d.get("forwardPE")),
                            "beta": _num(d.get("beta")),
                            "dividend_yield": _pct(d.get("dividend")),
                            "provider": "stockanalysis",
                        }
    except Exception:
        result = None

    _CACHE[key] = (time.time(), result)
    return result
