"""东方财富研报检索（A股/港股 live）。

直连绕代理（trust_env=False；域名已在 model_config.DATA_SOURCE_BYPASS_DOMAINS 中加入 NO_PROXY）。
- 搜索：reportapi.eastmoney.com/report/list（按个股代码）。
- 预览：研报 PDF 直链 pdf.dfcfw.com/pdf/H3_{infoCode}_1.pdf（注意 H3 前缀，公告才是 H2）。
- 东财仅覆盖 A股/港股研报；美股(TSLA 等)无结果，交由知识星球「海外投行报告」源。
结果缓存 10 分钟。
"""
from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Any, Optional

import httpx

from .shared_utils import safe_error, safe_int

EASTMONEY_REPORT_URL = "https://reportapi.eastmoney.com/report/list"
EASTMONEY_REPORT_SOURCE_NAME = "东方财富研报"
EASTMONEY_REPORT_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://data.eastmoney.com/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}

_PDF_HOST = "pdf.dfcfw.com"
_REQUEST_TIMEOUT = httpx.Timeout(12.0, connect=5.0)
_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_TTL = 600.0


def eastmoney_report_pdf_url(info_code: str) -> str:
    """研报 PDF 直链。研报用 H3_ 前缀（公告 eastmoney_pdf_url 用 H2_）。"""
    code = (info_code or "").strip()
    return f"https://{_PDF_HOST}/pdf/H3_{code}_1.pdf" if code else ""


def _qtype_for_market(market: Optional[str]) -> str:
    # qType=0 个股（A股 / 港股复用同一接口，港股覆盖较薄）。
    return "0"


def _normalize_row(item: dict[str, Any]) -> Optional[dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    info_code = str(item.get("infoCode") or "").strip()
    title = str(item.get("title") or "").strip()
    if not info_code or not title:
        return None
    publish = str(item.get("publishDate") or item.get("date") or "").strip()
    rating = str(item.get("sRatingName") or item.get("emRatingName") or "").strip()
    if rating in {"--", "无", "无评级"}:
        rating = ""
    return {
        "info_code": info_code,
        "title": title,
        "org": str(item.get("orgSName") or item.get("orgName") or "").strip(),
        "date": publish[:10],
        "rating": rating,
        "stock_name": str(item.get("stockName") or "").strip(),
        "stock_code": str(item.get("stockCode") or "").strip(),
        "pdf_url": eastmoney_report_pdf_url(info_code),
    }


async def query_eastmoney_reports(
    *,
    code: str,
    market: Optional[str] = None,
    page_size: int = 20,
) -> tuple[list[dict[str, Any]], list[str]]:
    """按个股代码拉取东财研报列表。返回 (归一化行, 警告)。失败时行为空并附警告。"""
    clean_code = (code or "").strip().split(".")[0]
    warnings: list[str] = []
    if not clean_code:
        return [], ["缺少有效的标的代码"]

    page_size = max(1, min(page_size, 50))
    cache_key = f"{clean_code}|{(market or '').upper()}|{page_size}"
    hit = _CACHE.get(cache_key)
    if hit and (time.time() - hit[0]) < _TTL:
        return list(hit[1]), warnings

    end_at = date.today() + timedelta(days=1)
    begin_at = end_at - timedelta(days=365 * 2)
    params = {
        "industryCode": "*",
        "pageSize": str(page_size),
        "pageNo": "1",
        "qType": _qtype_for_market(market),
        "code": clean_code,
        "beginTime": begin_at.isoformat(),
        "endTime": end_at.isoformat(),
        "fields": "",
        "p": "1",
        "pageNumber": "1",
    }

    rows: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=_REQUEST_TIMEOUT) as client:
            response = await client.get(
                EASTMONEY_REPORT_URL, params=params, headers=EASTMONEY_REPORT_HEADERS
            )
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:  # noqa: BLE001 - 交由调用方作为数据源降级提示
        return [], [f"{EASTMONEY_REPORT_SOURCE_NAME} 查询失败：{safe_error(exc)}"]

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return [], [f"{EASTMONEY_REPORT_SOURCE_NAME} 返回格式异常"]

    for item in data:
        normalized = _normalize_row(item)
        if normalized:
            rows.append(normalized)

    if not rows:
        hits = safe_int(payload.get("hits")) if isinstance(payload, dict) else 0
        if not hits:
            warnings.append(f"{EASTMONEY_REPORT_SOURCE_NAME} 暂无该标的研报")

    _CACHE[cache_key] = (time.time(), list(rows))
    return rows, warnings
