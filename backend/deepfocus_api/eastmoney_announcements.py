from __future__ import annotations

from .shared_utils import safe_int, safe_error

from datetime import date, datetime
from typing import Any, Optional

import httpx


EASTMONEY_NOTICE_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"
EASTMONEY_SOURCE_NAME = "东方财富公告"
EASTMONEY_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://data.eastmoney.com/notices/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}
EASTMONEY_ANN_TYPE = "SHA,CYB,SZA,BJA,INV"


async def query_eastmoney_announcements(
    *,
    client: httpx.AsyncClient,
    start_at: date,
    end_at: date,
    f_node: str,
    s_node: str = "0",
    limit: int,
    max_pages: int = 8,
) -> tuple[list[dict[str, Any]], int, list[str]]:
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    total = 0
    page_size = min(100, max(20, limit))

    for page_index in range(1, max_pages + 1):
        params = {
            "sr": "-1",
            "page_size": str(page_size),
            "page_index": str(page_index),
            "ann_type": EASTMONEY_ANN_TYPE,
            "client_source": "web",
            "f_node": f_node,
            "s_node": s_node,
            "begin_time": start_at.isoformat(),
            "end_time": end_at.isoformat(),
        }
        try:
            response = await client.get(EASTMONEY_NOTICE_URL, params=params, headers=EASTMONEY_HEADERS)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001 - caller surfaces this as a data-provider warning
            warnings.append(f"{EASTMONEY_SOURCE_NAME} 查询失败：{safe_error(exc)}")
            break

        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            warnings.append(f"{EASTMONEY_SOURCE_NAME} 返回格式异常")
            break

        total = max(total, safe_int(data.get("total_hits")) or 0)
        page_rows = data.get("list") or []
        if not isinstance(page_rows, list) or not page_rows:
            break

        for item in page_rows:
            if isinstance(item, dict):
                rows.append(to_cninfo_like_row(item))

        if len(rows) >= limit or len(page_rows) < page_size:
            break

    return rows[:limit], total, warnings


async def fetch_stock_announcements(symbol: str, days: int = 30, limit: int = 12) -> Optional[dict[str, Any]]:
    """个股近 N 天公告清单（标题/类型/日期/链接）——回答『XX最近有什么公告/有没有回购增发减持』。

    同一 np-anotice 接口按 stock_list 查个股（全市场扫描技能早已在用该网关，可达性已验证）。
    失败/非 A 股 6 位代码 → None（优雅降级，模型转答『无法核验』）。纯交易所公开事实，合规安全。"""
    import re as _re
    from datetime import timedelta

    code = _re.sub(r"\D", "", symbol or "")
    if len(code) != 6:
        return None
    end = date.today()
    start = end - timedelta(days=max(1, min(int(days or 30), 90)))
    params = {
        "sr": "-1",
        "page_size": str(min(50, max(5, int(limit or 12)))),
        "page_index": "1",
        "ann_type": "A",
        "client_source": "web",
        "stock_list": code,
        "begin_time": start.isoformat(),
        "end_time": end.isoformat(),
    }
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=12.0) as client:
            r = await client.get(EASTMONEY_NOTICE_URL, params=params, headers=EASTMONEY_HEADERS)
            r.raise_for_status()
            data = (r.json() or {}).get("data") or {}
    except Exception:  # noqa: BLE001
        return None
    rows2 = data.get("list") if isinstance(data, dict) else None
    items: list[dict[str, Any]] = []
    for it in (rows2 or [])[: int(limit or 12)]:
        if not isinstance(it, dict):
            continue
        art_code = str(it.get("art_code") or "").strip()
        columns = it.get("columns") if isinstance(it.get("columns"), list) else []
        items.append({
            "title": str(it.get("title_ch") or it.get("title") or "").strip(),
            "type": ",".join(_column_names(columns)),
            "date": str(it.get("notice_date") or it.get("display_time") or "").strip()[:10],
            "url": eastmoney_detail_url(code, art_code),
        })
    return {
        "symbol": code,
        "days": int(days or 30),
        "count": len(items),
        "items": items,
        "note": "交易所公开公告，以原文为准" if items else f"近 {int(days or 30)} 天无公告",
    }


async def fetch_next_disclosure(symbol: str) -> Optional[dict[str, Any]]:
    """个股财报预约披露日期（东财 datacenter RPT_PUBLIC_BS_APPOIN，名字天生少个 T）——回答『什么时候出年报/季报』。
    失败/查不到 → None。确定性日期事实。"""
    import re as _re

    code = _re.sub(r"\D", "", symbol or "")
    if len(code) != 6:
        return None
    url = (
        "https://datacenter-web.eastmoney.com/api/data/v1/get"
        # ⚠️东财真实报表名就是 RPT_PUBLIC_BS_APPOIN（少个 T）——RPT_PUBLIC_BS_APPOINT 实测"报表配置不存在"
        "?reportName=RPT_PUBLIC_BS_APPOIN&columns=ALL&pageSize=4"
        "&sortColumns=REPORT_DATE&sortTypes=-1"
        f"&filter=(SECURITY_CODE%3D%22{code}%22)"
    )
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=12.0) as client:
            r = await client.get(url, headers=EASTMONEY_HEADERS)
            if r.status_code != 200:
                return None
            rows2 = ((r.json().get("result") or {}).get("data")) or []
    except Exception:  # noqa: BLE001
        return None
    items: list[dict[str, Any]] = []
    for row in rows2:
        if not isinstance(row, dict):
            continue
        items.append({
            "report_period": str(row.get("REPORT_DATE") or "")[:10],       # 报告期(如 2026-06-30=中报)
            "appointed": str(row.get("APPOINT_PUBLISH_DATE") or "")[:10],  # 预约披露日
            "actual": str(row.get("ACTUAL_PUBLISH_DATE") or "")[:10],      # 实际披露日(未披露为空)
            "name": str(row.get("SECURITY_NAME_ABBR") or ""),
        })
    if not items:
        return None
    return {"symbol": code, "disclosures": items, "note": "以交易所/公司正式公告为准"}


def to_cninfo_like_row(item: dict[str, Any]) -> dict[str, Any]:
    code_info = _primary_code(item)
    art_code = str(item.get("art_code") or "").strip()
    title = str(item.get("title_ch") or item.get("title") or "").strip()
    stock_code = str((code_info or {}).get("stock_code") or "").strip()
    short_name = str((code_info or {}).get("short_name") or "").strip()
    notice_date = str(item.get("notice_date") or item.get("display_time") or "").strip()
    columns = item.get("columns") if isinstance(item.get("columns"), list) else []

    return {
        "secCode": stock_code,
        "secName": short_name,
        "announcementId": art_code,
        "announcementTitle": title,
        "announcementTime": _notice_time_ms(notice_date),
        "adjunctUrl": "",
        "eastmoney_pdf_url": eastmoney_pdf_url(art_code),
        "eastmoney_detail_url": eastmoney_detail_url(stock_code, art_code),
        "source": "eastmoney",
        "sourceName": EASTMONEY_SOURCE_NAME,
        "pageColumn": "eastmoney",
        "announcementType": ",".join(_column_names(columns)),
        "columns": _column_names(columns),
        "raw_notice_date": notice_date,
    }


def eastmoney_pdf_url(art_code: str) -> str:
    return f"https://pdf.dfcfw.com/pdf/H2_{art_code}_1.pdf" if art_code else ""


def eastmoney_detail_url(stock_code: str, art_code: str) -> str:
    if stock_code and art_code:
        return f"https://data.eastmoney.com/notices/detail/{stock_code}/{art_code}.html"
    return "https://data.eastmoney.com/notices/"


def _primary_code(item: dict[str, Any]) -> Optional[dict[str, Any]]:
    codes = item.get("codes")
    if not isinstance(codes, list) or not codes:
        return None
    for code in codes:
        if not isinstance(code, dict):
            continue
        ann_type = str(code.get("ann_type") or "")
        if any(token in ann_type.split(",") for token in ["SHA", "SZA", "CYB", "BJA", "A"]):
            return code
    return codes[0] if isinstance(codes[0], dict) else None


def _column_names(columns: list[Any]) -> list[str]:
    names: list[str] = []
    for column in columns:
        if isinstance(column, dict):
            name = str(column.get("column_name") or "").strip()
            if name:
                names.append(name)
    return names


def _notice_time_ms(value: str) -> int:
    text = (value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S:%f", "%Y-%m-%d"):
        try:
            normalized = text[:19] if fmt == "%Y-%m-%d %H:%M:%S" else text
            parsed = datetime.strptime(normalized, fmt)
            return int(parsed.timestamp() * 1000)
        except ValueError:
            continue
    return int(datetime.now().timestamp() * 1000)