"""Bounded public market-data reads for the simulator's stock detail panel.

This module never loads an account, changes a position, runs a strategy tick,
or calls a model. Missing source data remains an explicit missing score.
"""
from __future__ import annotations

import asyncio
import copy
import re
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

import httpx

from .stock_factor_scoring import parse_daily_bars, parse_flow_bars, score_capital, score_chips

_BJ = timezone(timedelta(hours=8))
_BASE = "https://push2his.eastmoney.com/api/qt/stock/"
_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
_CACHE: OrderedDict[str, tuple[float, dict]] = OrderedDict()
_PENDING: dict[str, asyncio.Task] = {}
_SLOTS = asyncio.Semaphore(4)
_MAX_SYMBOLS = 64


def normalize_symbol(value: str) -> str:
    code = str(value or "").strip()
    if not re.fullmatch(r"[0-9]{6}", code) or not code.startswith(
        ("000", "001", "002", "003", "300", "301", "600", "601", "603", "605", "688", "689", "4", "8", "920")
    ):
        raise ValueError("请输入六位 A 股股票代码")
    return code


def _missing(kind: str, reason: str = "数据源暂不可用") -> dict:
    chips = kind == "chips"
    return {
        "status": "unavailable", "score": None, "label": "暂无可核验数据",
        "summary": reason, "data_date": None, "date_range": None,
        "source": "东方财富日线" if chips else "东方财富资金统计",
        "method": ("基于历史日线和换手的筹码分布模型；不代表真实账户持仓。"
                   if chips else "近五日主力成交分类净额规则指数；不是机构账户资金或预测胜率。"),
        "model": chips, "metrics": [], "warnings": [],
    }


async def _source_json(path: str, params: dict) -> dict:
    # IPv4 mirrors the existing simulator data client. Fixed host/path and
    # validated stock codes prevent callers from choosing network destinations.
    transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0", retries=0)
    async with httpx.AsyncClient(transport=transport, trust_env=False, timeout=4.0) as client:
        async with client.stream("GET", _BASE + path, params=params, headers=_HEADERS) as response:
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > 1024 * 1024:
                    raise ValueError("source response too large")
    import json
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise ValueError("invalid source response")
    return payload


def _public_section(report: dict) -> dict:
    """Keep the read API status contract stable across scoring implementations."""
    report["status"] = {"ok": "available", "missing": "insufficient"}.get(
        report.get("status"), report.get("status", "unavailable")
    )
    return report


async def _capital(code: str, secid: str, now: datetime) -> dict:
    try:
        payload = await _source_json("fflow/daykline/get", {
            "secid": secid, "fields1": "f1,f2,f3", "fields2": "f51,f52",
            "klt": "101", "lmt": "5",
        })
        return _public_section(score_capital(parse_flow_bars(payload, code, now), now))
    except asyncio.CancelledError:
        raise
    except Exception:
        return _missing("capital", "暂未取得带交易日期的近五日资金流；不使用换手率代替资金分。")


async def _chips(code: str, secid: str, now: datetime) -> dict:
    try:
        payload = await _source_json("kline/get", {
            "secid": secid, "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": "101", "fqt": "1", "end": now.strftime("%Y%m%d"), "lmt": "600",
        })
        return _public_section(score_chips(parse_daily_bars(payload, code, now), now))
    except asyncio.CancelledError:
        raise
    except Exception:
        return _missing("chips", "暂未取得完整历史换手日线，无法估算筹码结构。")


async def _bounded_section(coro, kind: str) -> dict:
    try:
        return await asyncio.wait_for(coro, timeout=4.8)
    except asyncio.TimeoutError:
        return _missing(kind, "数据源查询超时，请稍后重试。")


async def _collect(code: str) -> dict:
    now = datetime.now(_BJ)
    result = {"symbol": code, "as_of": now.isoformat(),
              "capital": _missing("capital"), "chips": _missing("chips")}
    acquired = False
    try:
        await asyncio.wait_for(_SLOTS.acquire(), timeout=0.05)
        acquired = True
        secid = ("1." if code.startswith("6") else "0.") + code
        # Each section handles its own source failure; one unavailable section
        # must not discard the other one's verified result.
        result["capital"], result["chips"] = await asyncio.gather(
            _bounded_section(_capital(code, secid, now), "capital"),
            _bounded_section(_chips(code, secid, now), "chips"),
        )
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        pass
    finally:
        if acquired:
            _SLOTS.release()
    success = any(result[k].get("status") == "available" for k in ("capital", "chips"))
    _CACHE[code] = (time.monotonic() + (60.0 if success else 15.0), result)
    _CACHE.move_to_end(code)
    while len(_CACHE) > _MAX_SYMBOLS:
        _CACHE.popitem(last=False)
    return result


def _done(code: str, task: asyncio.Task) -> None:
    if _PENDING.get(code) is task:
        _PENDING.pop(code, None)
    if not task.cancelled():
        task.exception()  # Retrieve errors even if every requesting tab left.


async def get_stock_factors(symbol: str) -> dict:
    code = normalize_symbol(symbol)
    cached = _CACHE.get(code)
    if cached is not None and cached[0] > time.monotonic():
        _CACHE.move_to_end(code)
        return copy.deepcopy(cached[1])
    task = _PENDING.get(code)
    if task is None:
        if len(_PENDING) >= 8:
            return {"symbol": code, "as_of": datetime.now(_BJ).isoformat(),
                    "capital": _missing("capital", "查询繁忙，请稍后重试。"),
                    "chips": _missing("chips", "查询繁忙，请稍后重试。")}
        task = asyncio.create_task(_collect(code))
        _PENDING[code] = task
        task.add_done_callback(lambda done: _done(code, done))
    # Closing one tab cannot cancel another tab's shared market-data read.
    # The shared task is bounded by the source deadline and concurrency limit.
    return copy.deepcopy(await asyncio.shield(task))
