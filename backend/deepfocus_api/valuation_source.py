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


async def _fetch_em_realtime(client: "httpx.AsyncClient", secid: Optional[str]) -> Optional[dict]:
    """东财 push2 实时快照：总市值(f116)/流通市值(f117)/动态市盈率(f162)/市净率(f167)。
    与行情页同源、与用户在东财看到的口径一致——根治"我们的 PE/PB 与东财对不上=看着像错的"。"""
    if not secid:
        return None
    try:
        r = await client.get(
            "https://push2.eastmoney.com/api/qt/stock/get",
            params={"secid": secid, "fields": "f116,f117,f162,f167", "fltt": "2", "invt": "2"},
            headers={"User-Agent": _UA},
        )
        d = (r.json().get("data") or {}) if r.status_code == 200 else {}
    except Exception:
        return None
    if not d:
        return None

    def _v(x: object) -> Optional[float]:
        return float(x) if isinstance(x, (int, float)) else None  # 东财缺值返回 "-" → None

    return {
        "market_cap": _v(d.get("f116")),
        "circulating_market_cap": _v(d.get("f117")),
        "pe_dynamic": _v(d.get("f162")),
        "pb_ratio": _v(d.get("f167")),
    }


async def fetch_valuation(symbol: str, market: Optional[str] = None) -> Optional[dict]:
    """市值 + PE/PB（live）。美股→stockanalysis、A股/港股→东财实时行情(市值/PB/动态PE) + 估值表(PE_TTM/PS/PEG)。失败 None。"""
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
        from .eastmoney_data import _em_stock_secid
        async with httpx.AsyncClient(trust_env=False, timeout=12.0) as client:
            if is_a or is_hk:
                # ① 实时快照(与行情页同源)：市值/流通市值/动态PE/PB —— 用户最常对照的口径，优先用它
                rt = await _fetch_em_realtime(client, _em_stock_secid(code, "CN" if is_a else "HK"))
                # ② 估值表：PE_TTM(同花顺口径)/PS/PEG —— 实时快照没有的口径，作补充
                dc: dict = {}
                try:
                    if is_a:
                        url = (
                            "https://datacenter-web.eastmoney.com/api/data/v1/get"
                            "?reportName=RPT_VALUEANALYSIS_DET&columns=ALL&pageSize=1"
                            "&sortColumns=TRADE_DATE&sortTypes=-1"
                            f"&filter=(SECURITY_CODE%3D%22{code}%22)"
                        )
                        r = await client.get(url, headers={"User-Agent": _UA, "Referer": "https://data.eastmoney.com/"})
                        if r.status_code == 200:
                            rows = ((r.json().get("result") or {}).get("data")) or []
                            if rows:
                                d0 = rows[0]
                                dc = {"market_cap": d0.get("TOTAL_MARKET_CAP"), "pe_ttm": d0.get("PE_TTM"),
                                      "pb_mrq": d0.get("PB_MRQ"), "ps_ttm": d0.get("PS_TTM"), "peg": d0.get("PEG_CAR")}
                    else:  # 港股 F10 RPT_HKF10_FN_MAININDICATOR（市值/PE/PB，单位 HKD）
                        secucode = f"{code.zfill(5)}.HK"
                        url = (
                            "https://datacenter-web.eastmoney.com/api/data/v1/get"
                            "?reportName=RPT_HKF10_FN_MAININDICATOR&columns=ALL&pageSize=1"
                            "&sortColumns=REPORT_DATE&sortTypes=-1"
                            f"&filter=(SECUCODE%3D%22{secucode}%22)"
                        )
                        r = await client.get(url, headers={"User-Agent": _UA, "Referer": "https://emweb.securities.eastmoney.com/"})
                        if r.status_code == 200:
                            rows = ((r.json().get("result") or {}).get("data")) or []
                            if rows:
                                d0 = rows[0]
                                dc = {"market_cap": d0.get("TOTAL_MARKET_CAP"), "pe_ttm": d0.get("PE_TTM"),
                                      "pb_mrq": d0.get("PB_TTM")}
                except Exception:  # noqa: BLE001 —— 估值表失败不影响实时快照（实时仍可单独成结果）
                    dc = {}
                if rt or dc:
                    pb_rt = (rt or {}).get("pb_ratio")
                    result = {
                        "market_cap": (rt or {}).get("market_cap") or dc.get("market_cap"),
                        "circulating_market_cap": (rt or {}).get("circulating_market_cap"),
                        "pe_ratio": dc.get("pe_ttm"),                      # 市盈率 TTM(同花顺口径)
                        "pe_dynamic": (rt or {}).get("pe_dynamic"),         # 市盈率 动态(东财行情页口径)
                        "pb_ratio": pb_rt if pb_rt is not None else dc.get("pb_mrq"),  # 实时 PB 优先(更新)
                        "ps_ratio": dc.get("ps_ttm"),
                        "peg": dc.get("peg"),
                        "currency": "HKD" if is_hk else "CNY",  # 港股市值/PE是港元口径，别被当成人民币
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
                            "currency": "USD",  # 美股市值/PE是美元口径
                            "provider": "stockanalysis",
                        }
    except Exception:
        result = None

    # A股/港股市值换算成「亿元」便于展示（数据单位是元，避免模型把 1.46e12 元说成 1.46 亿）
    if result and (is_a or is_hk) and result.get("market_cap"):
        try:
            result["market_cap_yi"] = round(float(result["market_cap"]) / 1e8, 1)
            if result.get("circulating_market_cap"):
                result["circulating_market_cap_yi"] = round(float(result["circulating_market_cap"]) / 1e8, 1)
        except (TypeError, ValueError):
            pass
    _CACHE[key] = (time.time(), result)
    return result
