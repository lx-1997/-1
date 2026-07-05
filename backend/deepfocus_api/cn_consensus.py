"""A股/港股卖方一致预期（机构评级 + 目标价）——补齐 consensus_source 只覆盖美股的缺口。

数据源（live 东财，直连绕代理 trust_env=False）：
  - A股：reportapi.eastmoney.com/report/list（个股近 N 月研报列表 → 聚合评级分布 / 平均·区间目标价 /
    机构家数 / 预测 EPS·PE）。datacenter 无对外开放的 A股评级聚合表，故走研报列表自聚合。
  - 港股：datacenter RPT_HKF10_INFO_ORGRATING（东财已聚合：每家机构最新评级 + 目标价 + RATINGAVG 平均评级
    + RATINGORGNUM 机构家数）。

目标价币种：A股=人民币(CNY)，港股=港元(HKD)。
任何失败 / 无数据 → None（绝不抛异常，优雅降级）。缓存 6h。
"""
from __future__ import annotations

import re
import time
from collections import Counter
from datetime import datetime, timedelta
from typing import Optional

import httpx

from .shared_utils import safe_float

_CACHE: dict[str, tuple[float, Optional[dict]]] = {}
_CACHE_TTL = 6 * 3600.0
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://data.eastmoney.com/",
}

# 评级中文 → 多空方向归一（仅用于排序展示，不做投资建议）。
_RATING_ORDER = {"买入": 5, "增持": 4, "持有": 3, "中性": 3, "减持": 2, "卖出": 1}


def _normalize_rating(raw: Optional[str]) -> Optional[str]:
    """把机构评级原文归一到 买入/增持/持有/减持/卖出（含港股「买进(Buy)」等英括号写法）。"""
    if not raw:
        return None
    t = str(raw)
    # 港股 RATING_NAME_NEW 已是中文；RATING_NAME 形如「买进(Buy)」「区间操作」
    if any(k in t for k in ("买入", "买进", "强烈推荐", "强推", "Buy", "Strong")):
        return "买入"
    if any(k in t for k in ("增持", "推荐", "跑赢", "优于", "Overweight", "Outperform", "Add", "Accumulate")):
        return "增持"
    if any(k in t for k in ("持有", "中性", "区间", "Hold", "Neutral", "Equal")):
        return "持有"
    if any(k in t for k in ("减持", "跑输", "Underweight", "Underperform", "Reduce")):
        return "减持"
    if any(k in t for k in ("卖出", "Sell")):
        return "卖出"
    return None


async def _fetch_cn(code: str, limit: int) -> Optional[dict]:
    """A股：reportapi 研报列表自聚合。"""
    begin = (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")
    url = (
        "https://reportapi.eastmoney.com/report/list"
        f"?pageSize={max(10, min(limit, 100))}&beginTime={begin}&endTime=2030-01-01"
        f"&pageNo=1&qType=0&code={code}"
    )
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=12.0) as client:
            r = await client.get(url, headers=_HEADERS)
        if r.status_code != 200:
            return None
        payload = r.json()
    except Exception:
        return None

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list) or not data:
        return None

    name = None
    rating_counter: Counter = Counter()
    targets: list[float] = []
    eps_this: list[float] = []
    pe_this: list[float] = []
    latest_date = ""
    for item in data:
        if not isinstance(item, dict):
            continue
        name = name or (item.get("stockName") or None)
        norm = _normalize_rating(item.get("emRatingName") or item.get("lastEmRatingName"))
        if norm:
            rating_counter[norm] += 1
        tp = safe_float(item.get("indvAimPriceT"))
        if tp and tp > 0:
            targets.append(tp)
        ep = safe_float(item.get("predictThisYearEps"))
        if ep and ep > 0:
            eps_this.append(ep)
        pe = safe_float(item.get("predictThisYearPe"))
        if pe and pe > 0:
            pe_this.append(pe)
        pub = str(item.get("publishDate") or "")[:10]
        if pub > latest_date:
            latest_date = pub

    total_rated = sum(rating_counter.values())
    if total_rated == 0 and not targets:
        return None

    rating_summary = {k: rating_counter[k] for k in sorted(rating_counter, key=lambda x: -_RATING_ORDER.get(x, 0))}
    # 多数派评级（家数最多者）
    consensus_rating = max(rating_summary, key=rating_summary.get) if rating_summary else None

    result: dict = {
        "symbol": code,
        "name": name,
        "market": "A股",
        "currency": "CNY",
        "rating_summary": rating_summary,  # {评级: 机构家数}
        "consensus_rating": consensus_rating,  # 多数派评级
        "report_count": len(data),  # 近半年研报条数
        "institution_count": total_rated,  # 给出明确评级的研报家数
        "period": f"近180天，截至{latest_date}" if latest_date else "近180天",
        "source": "eastmoney",
    }
    if targets:
        avg_t = round(sum(targets) / len(targets), 2)
        result["avg_target_price"] = avg_t
        result["target_price_low"] = round(min(targets), 2)
        result["target_price_high"] = round(max(targets), 2)
        result["target_price_count"] = len(targets)
    if eps_this:
        result["forecast_eps_this_year"] = round(sum(eps_this) / len(eps_this), 2)
    if pe_this:
        result["forecast_pe_this_year"] = round(sum(pe_this) / len(pe_this), 1)
    return result


async def _hk_current_price(code5: str) -> Optional[float]:
    """港股现价（港元）：push2 secid=116.{code}。⚠️港股 f43 原始整数是 ÷1000（与 A股 ÷100 不同，
    已用茅台 1168.63÷100 与腾讯 411.80÷1000 对新浪真值校准）。失败 None。"""
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=8.0) as client:
            r = await client.get(
                f"https://push2.eastmoney.com/api/qt/stock/get?secid=116.{code5}&fields=f43",
                headers=_HEADERS,
            )
        if r.status_code == 200:
            f43 = ((r.json().get("data") or {}).get("f43"))
            v = safe_float(f43)
            if v and v > 0:
                return round(v / 1000.0, 3)
    except Exception:
        return None
    return None


async def _fetch_hk(code: str, limit: int) -> Optional[dict]:
    """港股：datacenter RPT_HKF10_INFO_ORGRATING（东财已聚合每家机构评级 + 目标价）。"""
    code5 = code.zfill(5)
    secucode = f"{code5}.HK"
    url = (
        "https://datacenter-web.eastmoney.com/api/data/v1/get"
        f"?reportName=RPT_HKF10_INFO_ORGRATING&columns=ALL&pageSize={max(10, min(limit, 100))}"
        "&sortColumns=PUBLISH_DATE&sortTypes=-1"
        f"&filter=(SECUCODE=%22{secucode}%22)"
    )
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=12.0) as client:
            r = await client.get(url, headers=_HEADERS)
        if r.status_code != 200:
            return None
        data = ((r.json().get("result") or {}).get("data")) or []
    except Exception:
        return None
    if not data:
        return None

    name = None
    rating_counter: Counter = Counter()
    targets: list[float] = []
    latest_date = ""
    rating_avg = None
    org_num = None
    for item in data:
        if not isinstance(item, dict):
            continue
        name = name or item.get("SECURITY_NAME_ABBR")
        rating_avg = rating_avg or item.get("RATINGAVG")
        if org_num is None:
            org_num = item.get("RATINGORGNUM")
        norm = _normalize_rating(item.get("RATING_NAME_NEW") or item.get("RATING_NAME"))
        if norm:
            rating_counter[norm] += 1
        tp = safe_float(item.get("TARGET_PRICE"))
        if tp and tp > 0:
            targets.append(tp)
        pub = str(item.get("PUBLISH_DATE") or "")[:10]
        if pub > latest_date:
            latest_date = pub

    total_rated = sum(rating_counter.values())
    if total_rated == 0 and not targets:
        return None

    rating_summary = {k: rating_counter[k] for k in sorted(rating_counter, key=lambda x: -_RATING_ORDER.get(x, 0))}
    consensus_rating = _normalize_rating(rating_avg) or (
        max(rating_summary, key=rating_summary.get) if rating_summary else None
    )

    result: dict = {
        "symbol": code5,
        "name": name,
        "market": "港股",
        "currency": "HKD",
        "rating_summary": rating_summary,
        "consensus_rating": consensus_rating,
        "report_count": len(data),
        "institution_count": org_num if isinstance(org_num, int) and org_num > 0 else total_rated,
        "period": f"近期，截至{latest_date}" if latest_date else "近期",
        "source": "eastmoney",
    }
    if targets:
        avg_t = round(sum(targets) / len(targets), 2)
        result["avg_target_price"] = avg_t
        result["target_price_low"] = round(min(targets), 2)
        result["target_price_high"] = round(max(targets), 2)
        result["target_price_count"] = len(targets)
        cur = await _hk_current_price(code5)
        if cur and cur > 0:
            result["current_price"] = cur
            result["target_upside"] = round((avg_t - cur) / cur * 100.0, 1)  # 平均目标价相对现价的空间 %
    return result


async def _a_current_price(code: str) -> Optional[float]:
    """A股现价（人民币）：push2 secid 沪 1./深 0.，f43÷100。失败 None。"""
    secid = f"1.{code}" if code[0] in ("6", "9") else f"0.{code}"
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=8.0) as client:
            r = await client.get(
                f"https://push2.eastmoney.com/api/qt/stock/get?secid={secid}&fields=f43",
                headers=_HEADERS,
            )
        if r.status_code == 200:
            v = safe_float(((r.json().get("data") or {}).get("f43")))
            if v and v > 0:
                return round(v / 100.0, 2)
    except Exception:
        return None
    return None


async def fetch_cn_consensus(
    symbol: str, market: Optional[str] = None, limit: int = 50
) -> Optional[dict]:
    """A股/港股卖方一致预期：机构评级分布 + 平均·区间目标价 + 机构家数 + 目标空间。

    入参：
      symbol — 代码（A股 6 位数字 / 港股可 5 位或带前导零）；market — "CN"/"A"/"HK"，留空则按代码长度推断
              （6 位数字→A股，否则→港股）；limit — 取多少条研报/机构记录聚合。
    返回 JSON-able dict（rating_summary / consensus_rating / avg_target_price / target_price_low/high /
         target_upside? / institution_count / period / currency / name），无数据 → None，绝不抛异常。
    """
    code = re.sub(r"\D", "", symbol or "")
    if not code:
        return None
    mkt = (market or "").upper()
    is_hk = mkt == "HK" or (mkt not in ("CN", "A", "ASHARE") and len(code) != 6)

    cache_key = ("hk:" if is_hk else "a:") + code + f":{limit}"
    hit = _CACHE.get(cache_key)
    if hit and (time.time() - hit[0]) < _CACHE_TTL:
        return hit[1]

    result: Optional[dict] = None
    try:
        if is_hk:
            result = await _fetch_hk(code, limit)
        else:
            result = await _fetch_cn(code, limit)
            # A股补现价 → 目标空间
            if result and result.get("avg_target_price"):
                cur = await _a_current_price(code)
                if cur and cur > 0:
                    result["current_price"] = cur
                    result["target_upside"] = round(
                        (result["avg_target_price"] - cur) / cur * 100.0, 1
                    )
    except Exception:
        result = None

    _CACHE[cache_key] = (time.time(), result)
    return result
