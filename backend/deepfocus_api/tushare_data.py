from __future__ import annotations

import os
import re
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from .schemas import AShareStructuredDataResponse
from .shared_utils import safe_error, utc_now_iso


TUSHARE_API_URL = os.getenv("TUSHARE_API_URL", "http://api.tushare.pro")
TUSHARE_TIMEOUT = httpx.Timeout(12.0, connect=5.0)
TUSHARE_TOKEN_ENV_NAMES = ("TUSHARE_TOKEN", "TUSHARE_PRO_TOKEN", "TS_TOKEN")
DAILY_FIELDS = "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"
DAILY_BASIC_FIELDS = "ts_code,trade_date,close,turnover_rate,volume_ratio,pe,pb,total_mv,circ_mv"
STOCK_BASIC_FIELDS = "ts_code,symbol,name,area,industry,market,list_date"


def tushare_token_configured() -> bool:
    return bool(_tushare_token())


def _tushare_token() -> Optional[str]:
    for env_name in TUSHARE_TOKEN_ENV_NAMES:
        token = (os.getenv(env_name) or "").strip().strip('"').strip("'")
        if token:
            return token
    return None


def normalize_ts_code(symbol: str) -> Optional[str]:
    value = (symbol or "").strip().upper()
    if not value:
        return None
    if re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", value):
        return value
    code = value.split(".")[0]
    if not re.fullmatch(r"\d{6}", code):
        return None
    if code.startswith("6"):
        return f"{code}.SH"
    if code.startswith(("4", "8")):
        return f"{code}.BJ"
    return f"{code}.SZ"


async def fetch_ashare_structured_data(
    symbol: str,
    *,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 120,
) -> AShareStructuredDataResponse:
    ts_code = normalize_ts_code(symbol) or symbol.strip().upper()
    fetched_at = utc_now_iso()
    warnings: list[str] = []
    token = _tushare_token()
    if not token:
        return AShareStructuredDataResponse(
            symbol=symbol,
            ts_code=ts_code,
            configured=False,
            status="unconfigured",
            fetched_at=fetched_at,
            warnings=["未配置 TUSHARE_TOKEN / TUSHARE_PRO_TOKEN / TS_TOKEN，A 股结构化数据层暂不可用。"],
        )
    if not normalize_ts_code(symbol):
        return AShareStructuredDataResponse(
            symbol=symbol,
            ts_code=ts_code,
            configured=True,
            status="error",
            fetched_at=fetched_at,
            warnings=["Tushare Pro 结构化层仅支持 A 股代码，例如 600519.SH / 000001.SZ。"],
        )

    end = _normalize_trade_date(end_date) or datetime.now(timezone.utc).strftime("%Y%m%d")
    start = _normalize_trade_date(start_date) or (datetime.now(timezone.utc) - timedelta(days=220)).strftime("%Y%m%d")
    safe_limit = max(1, min(limit, 500))

    async with httpx.AsyncClient(timeout=TUSHARE_TIMEOUT) as client:
        stock_basic_task = _call_tushare(
            client,
            token=token,
            api_name="stock_basic",
            params={"ts_code": ts_code, "list_status": "L"},
            fields=STOCK_BASIC_FIELDS,
        )
        daily_task = _call_tushare(
            client,
            token=token,
            api_name="daily",
            params={"ts_code": ts_code, "start_date": start, "end_date": end},
            fields=DAILY_FIELDS,
        )
        daily_basic_task = _call_tushare(
            client,
            token=token,
            api_name="daily_basic",
            params={"ts_code": ts_code, "start_date": start, "end_date": end},
            fields=DAILY_BASIC_FIELDS,
        )
        stock_basic_result, daily_result, daily_basic_result = await _gather_tushare_results(
            stock_basic_task,
            daily_task,
            daily_basic_task,
        )

    stock_basic, stock_warnings = stock_basic_result
    daily, daily_warnings = daily_result
    daily_basic, daily_basic_warnings = daily_basic_result
    warnings.extend(stock_warnings)
    warnings.extend(daily_warnings)
    warnings.extend(daily_basic_warnings)

    return AShareStructuredDataResponse(
        symbol=symbol,
        ts_code=ts_code,
        configured=True,
        status="ready" if daily or daily_basic or stock_basic else ("error" if warnings else "empty"),
        fetched_at=fetched_at,
        stock_basic=stock_basic[0] if stock_basic else {},
        daily=daily[:safe_limit],
        daily_basic=daily_basic[:safe_limit],
        warnings=warnings[:8],
    )


async def _gather_tushare_results(*aws: Any) -> tuple[tuple[list[dict[str, Any]], list[str]], ...]:
    results: list[tuple[list[dict[str, Any]], list[str]]] = []
    for result in await asyncio.gather(*aws, return_exceptions=True):
        if isinstance(result, Exception):
            results.append(([], [f"Tushare Pro 请求失败：{safe_error(result)}"]))
        else:
            results.append(result)
    return tuple(results)


async def _call_tushare(
    client: httpx.AsyncClient,
    *,
    token: str,
    api_name: str,
    params: dict[str, Any],
    fields: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    try:
        response = await client.post(
            TUSHARE_API_URL,
            json={
                "api_name": api_name,
                "token": token,
                "params": params,
                "fields": fields,
            },
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        return [], [f"Tushare Pro {api_name} 请求失败：{safe_error(exc)}"]

    if not isinstance(payload, dict):
        return [], [f"Tushare Pro {api_name} 返回格式异常。"]
    if payload.get("code") not in (0, "0", None):
        message = str(payload.get("msg") or payload.get("message") or "未知错误")
        return [], [f"Tushare Pro {api_name} 返回错误：{message}"]

    data = payload.get("data") or {}
    field_names = data.get("fields") or []
    items = data.get("items") or []
    if not isinstance(field_names, list) or not isinstance(items, list):
        return [], [f"Tushare Pro {api_name} data 字段异常。"]
    rows = [
        dict(zip(field_names, item))
        for item in items
        if isinstance(item, list)
    ]
    if not rows:
        warnings.append(f"Tushare Pro {api_name} 未返回数据。")
    return rows, warnings


def _normalize_trade_date(value: Optional[str]) -> Optional[str]:
    text = (value or "").strip()
    if not text:
        return None
    if re.fullmatch(r"\d{8}", text):
        return text
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text.replace("-", "")
    return None
