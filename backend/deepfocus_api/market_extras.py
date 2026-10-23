"""市场附加数据：宏观日历（今天/明天）+ A 股市场状态仪表。

日历：上游静态 JSON（斧头财信公开资产，与我们快讯/文章同源生态），透传 + 缓存；
仪表：沪深300 指数 K 线诚实推导——技术形态（均线五段状态）、资金热度（成交额比值）、
市场温度（两者加权合成，仅供参考非投资建议）。全部失败安全降级（返回空/unknown）。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

import httpx
from fastapi import APIRouter

router = APIRouter()

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://quote.eastmoney.com/",
}

# ---- 日历（上游静态 JSON，30 分钟缓存；失败保留上一份至 6h） ----

_CAL_URL = "https://futoucaixin.cn/dl/data/calendar.json"
_CAL_TTL = 1800.0
_CAL_STALE_OK = 6 * 3600.0
_cal_cache: tuple[float, Optional[dict[str, Any]]] = (0.0, None)


async def econ_calendar() -> dict[str, Any]:
    global _cal_cache
    ts, hit = _cal_cache
    now = time.time()
    if hit and now - ts < _CAL_TTL:
        return hit
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=12.0) as client:
            r = await client.get(_CAL_URL, headers={"User-Agent": _HEADERS["User-Agent"]})
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, dict) and (data.get("today") or data.get("tomorrow")):
                _cal_cache = (now, data)
                return data
    except Exception:  # noqa: BLE001
        pass
    if hit and now - ts < _CAL_STALE_OK:
        return hit
    return {"generated_at": None, "today": None, "tomorrow": None, "source": "unavailable"}


# ---- 市场状态仪表（沪深300 = 1.000300；30 分钟缓存） ----

_GAUGE_SECID = "1.000300"
_GAUGE_POINTS = 90
_GAUGE_TTL = 1800.0
_gauge_cache: tuple[float, Optional[dict[str, Any]]] = (0.0, None)

_TECH_STATES = ["下跌左侧", "下跌右侧", "震荡", "上涨左侧", "上涨右侧"]
_TECH_SCORE = {"下跌左侧": 25, "下跌右侧": 10, "震荡": 50, "上涨左侧": 70, "上涨右侧": 90}
_HEAT_STATES = ["冰点", "偏冷", "中性", "偏热", "沸点"]


def _position_band(pct: int) -> str:
    """温度→仓位指引档位（规则映射，仅供参考）。"""
    if pct <= 20:
        return "空仓观望"
    if pct <= 40:
        return "轻仓"
    if pct <= 60:
        return "半仓"
    if pct <= 80:
        return "偏重"
    return "重仓"


async def _index_close_amount(points: int) -> list[tuple[str, float, float]]:
    """沪深300 日线 [(date, close, volume)]：东财优先（生产 IP 间歇封锁），新浪 sh000300 兜底。

    volume 为成交量（东财手 / 新浪股），仅用于量能比值的分子分母同源相除。失败 → []。"""
    url = (
        f"https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={_GAUGE_SECID}"
        f"&fields1=f1&fields2=f51,f53,f56&klt=101&fqt=1&end=20500101&lmt={points}"
    )
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=12.0) as client:
                r = await client.get(url, headers=_HEADERS)
            if r.status_code == 200:
                kl = ((r.json().get("data") or {}).get("klines")) or []
                out: list[tuple[str, float, float]] = []
                for line in kl:
                    parts = line.split(",")
                    if len(parts) >= 3:
                        try:
                            out.append((parts[0], float(parts[1]), float(parts[2])))
                        except ValueError:
                            pass
                if out:
                    return out
        except Exception:  # noqa: BLE001
            pass
        if attempt < 2:
            await asyncio.sleep(0.5 * (attempt + 1))
    # 新浪兜底：指数必须显式 sh 前缀（000300 按 0 开头会被误判深市个股）。
    try:
        sina_url = (
            "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            f"CN_MarketData.getKLineData?symbol=sh{_GAUGE_SECID.split('.')[-1]}"
            f"&scale=240&ma=no&datalen={points}"
        )
        async with httpx.AsyncClient(trust_env=False, timeout=12.0) as client:
            r = await client.get(sina_url, headers=_HEADERS)
        data = r.json()
        if isinstance(data, str):
            import json as _json
            data = _json.loads(data)
        out = []
        for row in data or []:
            if not isinstance(row, dict):
                continue
            try:
                out.append((str(row.get("day") or "")[:10], float(row.get("close")), float(row.get("volume") or 0)))
            except (TypeError, ValueError):
                pass
        return out
    except Exception:  # noqa: BLE001
        return []


def _sma(values: list[float], n: int) -> Optional[float]:
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def _heat_state(ratio: Optional[float]) -> str:
    if ratio is None:
        return "未知"
    if ratio >= 1.5:
        return "沸点"
    if ratio >= 1.15:
        return "偏热"
    if ratio >= 0.85:
        return "中性"
    if ratio >= 0.5:
        return "偏冷"
    return "冰点"


def _heat_score(ratio: Optional[float]) -> int:
    if ratio is None:
        return 50
    return max(0, min(100, round((ratio - 0.5) / 1.5 * 100)))


async def market_gauge() -> dict[str, Any]:
    global _gauge_cache
    ts, hit = _gauge_cache
    now = time.time()
    if hit and now - ts < _GAUGE_TTL:
        return hit
    bars = await _index_close_amount(_GAUGE_POINTS)
    if len(bars) < 61:
        if hit and now - ts < _CAL_STALE_OK:
            return hit
        return {"available": False, "reason": "指数数据暂不可用"}

    closes = [b[1] for b in bars]
    amounts = [b[2] for b in bars]
    last_close = closes[-1]
    ma5, ma20, ma60 = _sma(closes, 5), _sma(closes, 20), _sma(closes, 60)
    amt_ma20 = _sma(amounts, 20)

    tech = "震荡"
    if ma20 is not None and ma60 is not None:
        if last_close > ma20 > ma60:
            tech = "上涨右侧"
        elif last_close < ma20 < ma60:
            tech = "下跌右侧"
        elif ma20 > ma60:
            tech = "上涨左侧"
        elif ma20 < ma60:
            tech = "下跌左侧"
    heat_ratio: Optional[float] = None
    if amt_ma20 and amt_ma20 > 0:
        heat_ratio = round(amounts[-1] / amt_ma20, 3)
    heat = _heat_state(heat_ratio)
    tech_score = _TECH_SCORE.get(tech, 50)
    temperature = max(0, min(100, round(tech_score * 0.6 + _heat_score(heat_ratio) * 0.4)))
    chg_1d = round((last_close / closes[-2] - 1) * 100, 2) if len(closes) >= 2 and closes[-2] else None
    position_pct = int(round(temperature / 5.0) * 5)  # 仓位指引：温度取整到 5% 一档

    # 近 10 日温度趋势：同一规则逐日回算（均线/量比都用截至当日的数据）。
    trend: list[dict[str, Any]] = []
    for i in range(max(0, len(bars) - 10), len(bars)):
        c_i = closes[: i + 1]
        ma20_i, ma60_i = _sma(c_i, 20), _sma(c_i, 60)
        c, m20, m60 = c_i[-1], ma20_i, ma60_i
        if m20 is None or m60 is None:
            continue
        if c > m20 > m60:
            t_i = "上涨右侧"
        elif c < m20 < m60:
            t_i = "下跌右侧"
        elif m20 > m60:
            t_i = "上涨左侧"
        elif m20 < m60:
            t_i = "下跌左侧"
        else:
            t_i = "震荡"
        amt_i = amounts[: i + 1]
        amt_ma_i = _sma(amt_i, 20)
        ratio_i = (amt_i[-1] / amt_ma_i) if (amt_ma_i and amt_ma_i > 0) else None
        temp_i = max(0, min(100, round(_TECH_SCORE.get(t_i, 50) * 0.6 + _heat_score(ratio_i) * 0.4)))
        trend.append({"date": bars[i][0], "temperature": temp_i, "close": c})
    temp_delta = (
        trend[-1]["temperature"] - trend[-2]["temperature"]
        if len(trend) >= 2 else None
    )

    out = {
        "available": True,
        "as_of": bars[-1][0],
        "index": "沪深300",
        "close": last_close,
        "chg_1d_pct": chg_1d,
        "tech_state": tech,
        "tech_levels": {"ma5": ma5, "ma20": ma20, "ma60": ma60},
        "heat_state": heat,
        "heat_ratio": heat_ratio,
        "temperature": temperature,
        "position_pct": position_pct,
        "position_band": _position_band(position_pct),
        "temperature_trend": trend,
        "temp_delta_1d": temp_delta,
        "disclaimer": "基于公开指数数据的规则化推导（量能热度为成交量比值），仅供参考，不构成投资建议",
    }
    _gauge_cache = (now, out)
    return out


@router.get("/api/econ/calendar")
async def api_econ_calendar() -> dict[str, Any]:
    """宏观数据日历（今天/明天）：时间/地区/事件/前值/预期/重要性星级。"""
    return await econ_calendar()


@router.get("/api/market/gauge")
async def api_market_gauge() -> dict[str, Any]:
    """A 股市场状态仪表：技术形态 / 资金热度 / 市场温度。"""
    return await market_gauge()
