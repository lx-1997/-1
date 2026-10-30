"""A股分红送转历史（F10 派息明细）——给 AI 智能体看真实分红方案，防模型编造。

直连绕代理（httpx trust_env=False，外部 403/超时多是沙箱代理封的）。仅 A股（6 位数字代码）。
来源 datacenter-web.eastmoney.com 的 RPT_SHAREBONUS_DET（分红送转明细）。

核心字段（供应商报告期目录，不是完整实施事件账簿）：
- IMPL_PLAN_PROFILE  供应商方案文案，如「10派280.2423元(含税)」「10转4派5元」；须以实施公告核对
- BONUS_RATIO        每10股送股数；IT_RATIO 每10股转增数；PRETAX_BONUS_RMB 每10股税前派现(元)
- DIVIDENT_RATIO     股息率(小数，×100 = %)
- EQUITY_RECORD_DATE 股权登记日；EX_DIVIDEND_DATE 除权除息日
- ASSIGN_PROGRESS    进度（预披露 / 董事会预案 / 股东大会决议通过 / 实施分配 …）
- REPORT_DATE        对应报告期（分红年度/报告期末）

实样发现目录遗漏特别分红、同日现金总额不同，不能用于直接入账或计算完整总回报。
缓存 6h。任何失败/无数据 → None（优雅降级，绝不抛异常）。
"""
from __future__ import annotations

import re
import time
import copy
from typing import Any, Optional

import httpx

from .shared_utils import safe_float

_CACHE: dict[str, tuple[float, Optional[list]]] = {}
_CACHE_TTL = 6 * 3600.0

_BASE_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://data.eastmoney.com/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
}


def _ymd(value: Any) -> Optional[str]:
    """东财日期形如 '2026-06-26 00:00:00' → '2026-06-26'；空/无 → None。"""
    s = str(value or "").strip()
    return s[:10] if len(s) >= 10 else None


def _build_plan(row: dict) -> Optional[str]:
    """优先用东财成文方案（防编造）；缺失时按 送/转/派 三段拼一个保守描述。"""
    profile = str(row.get("IMPL_PLAN_PROFILE") or "").strip()
    if profile:
        return profile
    send = safe_float(row.get("BONUS_RATIO"), low=0.0)
    transfer = safe_float(row.get("IT_RATIO"), low=0.0)
    cash = safe_float(row.get("PRETAX_BONUS_RMB"), low=0.0)
    parts: list[str] = []
    if send:
        parts.append(f"送{send:g}")
    if transfer:
        parts.append(f"转{transfer:g}")
    if cash:
        parts.append(f"派{cash:g}元")
    return ("10" + "".join(parts) + "(含税)") if parts else None


async def fetch_dividend_history(
    symbol: str,
    market: Optional[str] = None,
    limit: int = 6,
) -> Optional[list[dict[str, Any]]]:
    """A股最近 ``limit`` 期分红送转历史，按公告日倒序（最新在前）。

    入参 symbol 取数字代码（去掉非数字）；market 预留，目前仅支持 A股（6 位代码）。
    返回 JSON-able list[dict]，每项含 方案文案 + 拆解字段 + 关键日期 + 进度 + 股息率。
    无数据/非 A股/请求失败 → None（绝不抛异常）。
    """
    code = re.sub(r"\D", "", symbol or "")
    if len(code) != 6:
        # 仅 A股 F10 分红明细；港股/美股该接口无数据，诚实返回 None。
        return None
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = 6
    n = max(1, min(n, 40))

    cache_key = f"{code}:{n}"
    hit = _CACHE.get(cache_key)
    if hit and (time.time() - hit[0]) < _CACHE_TTL:
        return copy.deepcopy(hit[1])

    result: Optional[list[dict[str, Any]]] = None
    try:
        url = (
            f"{_BASE_URL}?reportName=RPT_SHAREBONUS_DET&columns=ALL"
            f"&pageSize={n}&sortColumns=NOTICE_DATE&sortTypes=-1"
            f"&filter=(SECURITY_CODE%3D%22{code}%22)"
        )
        async with httpx.AsyncClient(trust_env=False, timeout=12.0) as client:
            r = await client.get(url, headers=_HEADERS)
        if r.status_code == 200:
            data = ((r.json().get("result") or {}).get("data")) or []
            rows: list[dict[str, Any]] = []
            for row in data:
                if not isinstance(row, dict):
                    raise ValueError('Invalid dividend catalog row')
                if row.get('SECURITY_CODE') != code:
                    raise ValueError('Dividend catalog security mismatch')
                yield_frac = safe_float(row.get("DIVIDENT_RATIO"))
                rows.append(
                    {
                        "name": row.get("SECURITY_NAME_ABBR"),
                        "code": code,
                        "report_period": _ymd(row.get("REPORT_DATE")),  # 分红对应报告期
                        "plan": _build_plan(row),                       # 供应商目录文案，非原文核验
                        "bonus_per_10": safe_float(row.get("BONUS_RATIO"), low=0.0),   # 每10股送股
                        "transfer_per_10": safe_float(row.get("IT_RATIO"), low=0.0),   # 每10股转增
                        "cash_per_10_pretax": safe_float(row.get("PRETAX_BONUS_RMB"), low=0.0),  # 每10股税前派现(元)
                        "dividend_yield_pct": (round(yield_frac * 100, 4) if yield_frac is not None else None),  # 股息率 %
                        "record_date": _ymd(row.get("EQUITY_RECORD_DATE")),   # 股权登记日
                        "ex_dividend_date": _ymd(row.get("EX_DIVIDEND_DATE")),  # 除权除息日
                        "progress": row.get("ASSIGN_PROGRESS"),  # 进度
                        "notice_date": _ymd(row.get("NOTICE_DATE")),  # 公告日
                        "eps": safe_float(row.get("BASIC_EPS")),  # 对应每股收益(参考)
                        "data_quality": {
                            "source": "eastmoney_report_period_catalog",
                            "complete_event_history_verified": False,
                            "implementation_filing_verified": False,
                            "execution_usable": False,
                            "warning": "报告期目录可能遗漏特别分红或未含同日全部现金；金额、登记日、支付日及除息参考须以实施公告核对，不能据此计算完整分红或总回报。",
                        },
                    }
                )
            if rows:
                result = rows
    except Exception:
        result = None

    _CACHE[cache_key] = (time.time(), result)
    return copy.deepcopy(result)
