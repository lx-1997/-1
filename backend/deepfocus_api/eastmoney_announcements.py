from __future__ import annotations

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
            warnings.append(f"{EASTMONEY_SOURCE_NAME} 查询失败：{_safe_error(exc)}")
            break

        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            warnings.append(f"{EASTMONEY_SOURCE_NAME} 返回格式异常")
            break

        total = max(total, _safe_int(data.get("total_hits")))
        page_rows = data.get("list") or []
        if not isinstance(page_rows, list) or not page_rows:
            break

        for item in page_rows:
            if isinstance(item, dict):
                rows.append(to_cninfo_like_row(item))

        if len(rows) >= limit or len(page_rows) < page_size:
            break

    return rows[:limit], total, warnings


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


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    text = str(exc).strip()
    return text[:160] or exc.__class__.__name__
