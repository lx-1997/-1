"""Google Finance 行情抓取——环境内唯一可达的全市场实时个股源（突破 403）。

外部行情 API（yahoo / stooq / nasdaq / alphavantage / iex）在本环境全部 403/000，
唯独 google.com/finance 可达。本模块爬取其 SSR 页面内嵌的 JSON 数据块提取真实行情，
覆盖美股(NASDAQ/NYSE) / 港股(HKG) / A股(SHA/SHE)。准实时、可能延迟，故标
degraded + is_realtime=False（诚实）。缓存 5min 平衡实时性与速率。

关键：必须 follow_redirects（→ /finance/beta/）+ 完整浏览器 headers，否则拿到空 body。
提取锚点是 HTML 内嵌 JSON `["SYM",..],"Name",0,"USD",[price,chg,pct,..],null,prev_close`，
比会变的 CSS class 稳定得多。
"""
from __future__ import annotations

import re
import time
from typing import Optional

import httpx

# 行情准实时，缓存短（5min）；比 403 强、又不过度抓取。
_CACHE: dict[str, tuple[float, Optional[dict]]] = {}
_CACHE_TTL = 300.0

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _clean_symbol(symbol: str) -> str:
    """去掉 .US/.HK/.SS 等后缀，Google 用纯代码 + 单独的交易所。"""
    return re.sub(r"\.(US|HK|SS|SH|SZ|O|N|OQ)$", "", (symbol or "").upper()).strip()


def _guess_exchanges(symbol: str, market: Optional[str] = None) -> list[str]:
    """推断 Google Finance 交易所代码；美股先 NASDAQ 后 NYSE 兜底。"""
    s = symbol.upper()
    raw = (market or "").upper()
    if raw == "HK" or s.endswith(".HK"):
        return ["HKG"]
    if s.endswith(".SS") or s.endswith(".SH"):
        return ["SHA"]
    if s.endswith(".SZ"):
        return ["SHE"]
    if raw == "CN" or re.match(r"^\d{6}$", s):
        digits = re.sub(r"\D", "", s)
        return ["SHA"] if digits.startswith(("6", "9")) else ["SHE"]
    return ["NASDAQ", "NYSE"]


# 内嵌 JSON：["SYM"<,"EXCH"或,["EXCH"]>],"Name",0,"USD",[price,chg,pct,..],null,prev_close
_PRICE_TAIL = (
    r'"[,\]].{0,40}?"([^"]+)",\d+,"([A-Z]{3})",'
    r"\[([\d.eE+-]+),[\d.eE+-]+,[\d.eE+-]+[^\]]*\],[^,]*,([\d.eE+-]+)"
)


def _num(s: Optional[str]) -> Optional[float]:
    """解析 Google Finance 的数值串：'$313.23'→313.23，'4.57T'→4.57e12，'0.35%'→0.35。"""
    s = (s or "").strip().lstrip("$").replace(",", "").replace("+", "")
    if not s or s in ("-", "—", "0.00"):
        return None
    mult = 1.0
    if s and s[-1] in "TBMK":
        mult = {"T": 1e12, "B": 1e9, "M": 1e6, "K": 1e3}[s[-1]]
        s = s[:-1]
    elif s.endswith("%"):
        s = s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return None


# 统计区每行：<div ...>Label</div><div class="dO6ijd">Value</div>
_STAT_RE = r'>([^<>]+)</div><div class="dO6ijd">([^<]+)</div>'


def _parse(html: str, symbol: str) -> Optional[dict]:
    m = re.search(r'\["' + re.escape(symbol) + _PRICE_TAIL, html)
    if not m:
        return None
    name, currency, price_s, prev_s = m.group(1), m.group(2), m.group(3), m.group(4)
    name = re.sub(r"\\u([0-9a-fA-F]{4})", lambda mm: chr(int(mm.group(1), 16)), name)
    try:
        price = float(price_s)
        prev = float(prev_s)
    except ValueError:
        return None
    change = round(price - prev, 4)
    pct = round(change / prev * 100, 4) if prev else 0.0

    # 统计区 label→value，每个 label 取首次出现（= 目标股，相关股在后）。
    stats: dict[str, str] = {}
    for k, v in re.findall(_STAT_RE, html):
        if k not in stats:
            stats[k] = v

    return {
        "symbol": symbol,
        "name": name,
        "price": price,
        "previous_close": prev,
        "change": change,
        "change_percent": pct,
        "currency": currency,
        "open": _num(stats.get("Open")),
        "high": _num(stats.get("High")),
        "low": _num(stats.get("Low")),
        "volume": _num(stats.get("Volume")) or _num(stats.get("Avg. vol.")),
        "market_cap": _num(stats.get("Mkt. cap")),
        "pe_ratio": _num(stats.get("P/E ratio")),
        "wk52_high": _num(stats.get("52-wk high")),
        "wk52_low": _num(stats.get("52-wk low")),
        "eps": _num(stats.get("EPS")),
        "provider": "google-finance",
    }


async def fetch_google_finance_quote(symbol: str, market: Optional[str] = None, max_age: float = _CACHE_TTL) -> Optional[dict]:
    """抓取真实个股行情，美股/港股/A股通用。失败返回 None（优雅降级，绝不抛给上层）。
    max_age：缓存可接受的最大秒数（实时行情传小值如 6；速判卡等用默认 300）。"""
    sym = _clean_symbol(symbol)
    if not sym:
        return None
    cache_key = f"{sym}:{(market or '').upper()}"
    hit = _CACHE.get(cache_key)
    if hit and (time.time() - hit[0]) < max_age:
        return hit[1]

    result: Optional[dict] = None
    try:
        async with httpx.AsyncClient(trust_env=True, follow_redirects=True, timeout=15.0) as client:
            for exch in _guess_exchanges(sym, market):
                try:
                    r = await client.get(
                        f"https://www.google.com/finance/quote/{sym}:{exch}",
                        headers=_HEADERS,
                    )
                except Exception:
                    continue
                if r.status_code != 200:
                    continue
                data = _parse(r.text, sym)
                if data:
                    data["exchange"] = exch
                    result = data
                    break
    except Exception:
        result = None

    _CACHE[cache_key] = (time.time(), result)
    return result
