"""A股日历（散户每天回访的确定性理由）：限售解禁 / 新股申购与上市 / 财报预约披露。

统一出口 `fetch_cn_calendar(days=7)` → {"as_of","days","lift_bans","new_stocks","disclosures"}，
各子项独立取数、任一失败返 [] 不拖垮整体。全部为交易所/东财 datacenter 公开确定性事实（零 AI 叙述），
合规安全区：只列日期事件，不加任何方向性解读。

来源 datacenter-web.eastmoney.com（直连绕代理 trust_env=False，与 dragon_tiger 同款基建）：
  - RPT_LIFT_STAGE        限售解禁（按 FREE_DATE 窗口）
  - RPTA_APP_IPOAPPLY     新股日历（申购按 APPLY_DATE / 上市按 LISTING_DATE 窗口；
                          RPT_APF_NEWSTOCK 已实测「报表配置不存在」，弃用）
  - RPT_PUBLIC_BS_APPOIN  财报预约披露（⚠️东财真实 reportName 就是少个 T，
                          RPT_PUBLIC_BS_APPOINT 实测「报表配置不存在」）
缓存 6h（cache key 含 as_of 日期，跨天自然失效）。
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

try:
    from deepfocus_api.shared_utils import safe_float
except Exception:  # pragma: no cover - 容错导入
    def safe_float(value, default=None):  # type: ignore
        try:
            if value is None or value == "":
                return default
            return float(value)
        except (TypeError, ValueError):
            return default


_CACHE: dict = {}
_CACHE_TTL = 6 * 3600.0  # 6h
_DATACENTER = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://data.eastmoney.com/",
}
_BJ_TZ = timezone(timedelta(hours=8))


def _bj_today() -> datetime:
    return datetime.now(_BJ_TZ)


def _d(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _date10(raw: Any) -> str:
    """东财日期 '2026-07-02 00:00:00' → '2026-07-02'；缺失 → ''。"""
    return str(raw or "")[:10]


def _pick(row: dict, *keys: str) -> Any:
    """防御性取值：按候选键顺序取第一个非空（东财同类报表字段名常有出入）。"""
    for k in keys:
        v = row.get(k)
        if v not in (None, ""):
            return v
    return None


async def _fetch_rows(
    client: httpx.AsyncClient,
    report_name: str,
    filter_str: str,
    sort_columns: str,
    sort_types: str = "1",
    page_size: int = 100,
) -> list:
    """datacenter 单页取数：失败/success!=True → []（绝不抛）。"""
    url = (
        f"{_DATACENTER}?reportName={report_name}&columns=ALL&pageSize={int(page_size)}"
        f"&sortColumns={sort_columns}&sortTypes={sort_types}&filter={filter_str}"
    )
    try:
        r = await client.get(url, headers=_HEADERS)
        if r.status_code != 200:
            return []
        payload = r.json()
        if not payload.get("success"):
            return []
        rows = ((payload.get("result") or {}).get("data")) or []
        return [row for row in rows if isinstance(row, dict)]
    except Exception:  # noqa: BLE001
        return []


def _window_filter(column: str, start: str, end: str) -> str:
    return f"({column}%3E%3D%27{start}%27)({column}%3C%3D%27{end}%27)"


async def _fetch_lift_bans(client: httpx.AsyncClient, start: str, end: str) -> list:
    """限售解禁：[{code,name,date,market_cap_wy(解禁市值·万元),type}]，按日期升序。"""
    rows = await _fetch_rows(
        client, "RPT_LIFT_STAGE", _window_filter("FREE_DATE", start, end), "FREE_DATE",
    )
    out: list = []
    for row in rows:
        code = str(_pick(row, "SECURITY_CODE") or "").strip()
        name = str(_pick(row, "SECURITY_NAME_ABBR", "SECURITY_NAME") or "").strip()
        date = _date10(_pick(row, "FREE_DATE"))
        if not code or not date:
            continue
        out.append({
            "code": code,
            "name": name,
            "date": date,
            "market_cap_wy": safe_float(_pick(row, "LIFT_MARKET_CAP", "ALIFT_MARKET_CAP")),  # 万元
            "type": str(_pick(row, "FREE_SHARES_TYPE") or "").strip(),
        })
    return out


async def _fetch_new_stocks(client: httpx.AsyncClient, start: str, end: str) -> list:
    """新股申购/上市：[{code,name,stage(申购/上市),date,price,market}]，按日期升序、去重。"""
    apply_rows = await _fetch_rows(
        client, "RPTA_APP_IPOAPPLY", _window_filter("APPLY_DATE", start, end), "APPLY_DATE",
    )
    listing_rows = await _fetch_rows(
        client, "RPTA_APP_IPOAPPLY", _window_filter("LISTING_DATE", start, end), "LISTING_DATE",
    )
    out: list = []
    seen: set = set()
    for stage, date_key, rows in (("申购", "APPLY_DATE", apply_rows), ("上市", "LISTING_DATE", listing_rows)):
        for row in rows:
            code = str(_pick(row, "SECURITY_CODE") or "").strip()
            date = _date10(_pick(row, date_key))
            if not code or not date or (stage, code) in seen:
                continue
            seen.add((stage, code))
            out.append({
                "code": code,
                "name": str(_pick(row, "SECURITY_NAME_ABBR", "SECURITY_NAME") or "").strip(),
                "stage": stage,
                "date": date,
                "price": safe_float(_pick(row, "ISSUE_PRICE")),  # 发行价（未定价为 None）
                "market": str(_pick(row, "MARKET_TYPE_NEW", "TRADE_MARKET") or "").strip(),
            })
    out.sort(key=lambda it: (it["date"], it["stage"], it["code"]))
    return out


async def _fetch_disclosures(client: httpx.AsyncClient, start: str, end: str) -> list:
    """财报预约披露：[{code,name,date,period(如 2026年 半年报)}]，按预约日升序。"""
    rows = await _fetch_rows(
        client, "RPT_PUBLIC_BS_APPOIN",
        _window_filter("APPOINT_PUBLISH_DATE", start, end), "APPOINT_PUBLISH_DATE",
    )
    out: list = []
    for row in rows:
        code = str(_pick(row, "SECURITY_CODE") or "").strip()
        date = _date10(_pick(row, "APPOINT_PUBLISH_DATE"))
        if not code or not date:
            continue
        out.append({
            "code": code,
            "name": str(_pick(row, "SECURITY_NAME_ABBR", "SECURITY_NAME") or "").strip(),
            "date": date,
            "period": str(_pick(row, "REPORT_TYPE_NAME") or _date10(_pick(row, "REPORT_DATE")) or "").strip(),
        })
    return out


async def fetch_cn_calendar(days: int = 7) -> dict:
    """未来 N 天 A股日历。各子项失败返 []；整体绝不抛。

    返回::

        {"as_of": "2026-07-02", "days": 7,
         "lift_bans":   [{code,name,date,market_cap_wy,type}, ...],
         "new_stocks":  [{code,name,stage,date,price,market}, ...],
         "disclosures": [{code,name,date,period}, ...]}
    """
    n = max(1, min(int(days or 7), 30))
    today = _bj_today()
    start = _d(today)
    end = _d(today + timedelta(days=n))

    cache_key = f"cal:{n}:{start}"  # 含 as_of：跨天自然失效
    hit = _CACHE.get(cache_key)
    if hit and (time.time() - hit[0]) < _CACHE_TTL:
        return hit[1]

    lift_bans: list = []
    new_stocks: list = []
    disclosures: list = []
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=12.0) as client:
            results = await asyncio.gather(
                _fetch_lift_bans(client, start, end),
                _fetch_new_stocks(client, start, end),
                _fetch_disclosures(client, start, end),
                return_exceptions=True,
            )
        lift_bans = results[0] if isinstance(results[0], list) else []
        new_stocks = results[1] if isinstance(results[1], list) else []
        disclosures = results[2] if isinstance(results[2], list) else []
    except Exception:  # noqa: BLE001 —— 连 client 都建不起来也不崩
        pass

    result = {
        "as_of": start,
        "days": n,
        "lift_bans": lift_bans,
        "new_stocks": new_stocks,
        "disclosures": disclosures,
    }
    if lift_bans or new_stocks or disclosures:  # 全空不缓存（网络抖动别把空结果钉 6h）
        _CACHE[cache_key] = (time.time(), result)
    return result
