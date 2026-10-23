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


# ---- v2 融合维度：站内情绪 / 风险雷达 / 站内参与度 ----

def _sentiment_score() -> Optional[int]:
    """近 72h 资讯表态情绪：看多占比 0-100；无表态 → None（权重让渡）。"""
    try:
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        from .engagement import NewsReaction
        from .storage import session_scope
        from sqlalchemy import select, func
        since = _dt.now(_tz.utc) - _td(hours=72)
        with session_scope() as session:
            rows = session.execute(
                select(NewsReaction.stance, func.count()).where(
                    NewsReaction.created_at >= since,
                    NewsReaction.stance.in_(("bull", "bear")),
                ).group_by(NewsReaction.stance)
            ).all()
        counts = {s: int(c) for s, c in rows}
        total = counts.get("bull", 0) + counts.get("bear", 0)
        if total <= 0:
            return None
        return round(counts.get("bull", 0) / total * 100)
    except Exception:  # noqa: BLE001
        return None


def _participation_score() -> Optional[int]:
    """站内参与度：近 72h 研判记录覆盖标的数（30 只 → 100 分封顶）。"""
    try:
        from contextlib import closing
        from datetime import datetime as _dt, timezone as _tz
        from . import data_store
        since = _dt.now(_tz.utc) - _td(hours=72)
        since_ts = since.timestamp()
        with closing(data_store._connect()) as conn:
            row = conn.execute(
                "SELECT COUNT(DISTINCT symbol) FROM data_points WHERE recorded_ts >= ?",
                (since_ts,),
            ).fetchone()
        count = int(row[0]) if row and row[0] is not None else 0
        return max(0, min(100, round(count / 30 * 100)))
    except Exception:  # noqa: BLE001
        return None


async def _risk_score() -> Optional[int]:
    """风险雷达反向分：A/H/美股市值前 20 中橙/红灯占比越高分越低。失败 → None。"""
    try:
        from .market_risk_radar import build_market_risk_radar
        data = await asyncio.wait_for(build_market_risk_radar(limit=20), timeout=25)
        levels: list[str] = []
        for value in (data or {}).values():
            if isinstance(value, list):
                for row in value:
                    if isinstance(row, dict) and row.get("risk_level"):
                        levels.append(str(row["risk_level"]).lower())
        if not levels:
            return None
        weight = {"red": 2, "orange": 1}
        penalty = sum(weight.get(lv, 0) for lv in levels)
        score = round(max(0.0, 100.0 - penalty / max(1, len(levels)) * 100 * 0.6))
        return score
    except Exception:  # noqa: BLE001
        return None


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

    # ---- v2 融合：站内情绪 / 风险雷达 / 站内参与度（缺席维度权重让渡） ----
    senti, risk, part = await asyncio.gather(
        asyncio.to_thread(_sentiment_score),
        _risk_score(),
        asyncio.to_thread(_participation_score),
    )
    dims = [
        {"key": "technique", "name": "技术价量", "score": temperature, "weight": 40, "used": True},
        {"key": "sentiment", "name": "资讯情绪", "score": senti, "weight": 20, "used": senti is not None},
        {"key": "risk", "name": "风险雷达", "score": risk, "weight": 20, "used": risk is not None},
        {"key": "participation", "name": "站内参与", "score": part, "weight": 20, "used": part is not None},
    ]
    used = [d for d in dims if d["used"]]
    weight_sum = sum(d["weight"] for d in used) or 1
    temperature_v2 = round(sum(d["score"] * d["weight"] for d in used) / weight_sum)

    # ---- 回测（仅价量模型）：近 120 日逐日温度分桶 vs 前向收益 ----
    bt_daily: list[tuple[int, float]] = []  # (温度, 当日收盘)
    for i in range(60, len(bars)):
        c_i = closes[: i + 1]
        ma20_i, ma60_i = _sma(c_i, 20), _sma(c_i, 60)
        if ma20_i is None or ma60_i is None:
            continue
        c_b, m20_b, m60_b = c_i[-1], ma20_i, ma60_i
        if c_b > m20_b > m60_b:
            t_b = "上涨右侧"
        elif c_b < m20_b < m60_b:
            t_b = "下跌右侧"
        elif m20_b > m60_b:
            t_b = "上涨左侧"
        elif m20_b < m60_b:
            t_b = "下跌左侧"
        else:
            t_b = "震荡"
        amt_i = amounts[: i + 1]
        amt_ma_i = _sma(amt_i, 20)
        ratio_b = (amt_i[-1] / amt_ma_i) if (amt_ma_i and amt_ma_i > 0) else None
        bt_daily.append((
            max(0, min(100, round(_TECH_SCORE.get(t_b, 50) * 0.6 + _heat_score(ratio_b) * 0.4))),
            c_b,
        ))
    backtest: dict[str, Any] = {"days": len(bt_daily), "model": "price-only", "buckets": [], "corr_temp_fwd5": None}
    if len(bt_daily) >= 20:
        for lo, hi, label in ((0, 40, "≤40 谨慎区"), (40, 60, "40-60 中性区"), (60, 101, "≥60 积极区")):
            pairs = [(t, c) for t, c in bt_daily if lo <= t < hi]
            f5 = []
            for idx in range(len(bt_daily) - 5):
                t0, c0 = bt_daily[idx]
                if lo <= t0 < hi:
                    try:
                        f5.append((bt_daily[idx + 5][1] / c0 - 1) * 100)
                    except (ZeroDivisionError, IndexError):
                        pass
            n_f5 = len(f5)
            backtest["buckets"].append({
                "band": label,
                "n": len(pairs),
                "fwd5_avg_pct": round(sum(f5) / n_f5, 3) if n_f5 else None,
                "fwd5_win_rate": round(sum(1 for x in f5 if x > 0) / n_f5 * 100, 1) if n_f5 else None,
            })
        n_pair = len(bt_daily) - 5
        if n_pair > 4:
            xs = [t for t, _ in bt_daily[:n_pair]]
            ys = [(bt_daily[i + 5][1] / c - 1) * 100 for i, (_, c) in enumerate(bt_daily[:n_pair])]
            mx, my = sum(xs) / n_pair, sum(ys) / n_pair
            cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
            vx = (sum((x - mx) ** 2 for x in xs)) ** 0.5
            vy = (sum((y - my) ** 2 for y in ys)) ** 0.5
            backtest["corr_temp_fwd5"] = round(cov / (vx * vy), 3) if vx and vy else None

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
        trend.append({
            "date": bars[i][0],
            "temperature": temp_i,
            "close": c,
            "tech_state": t_i,
            "tech_score": _TECH_SCORE.get(t_i, 50),
            "heat_state": _heat_state(ratio_i),
            "heat_ratio": ratio_i,
            "heat_score": _heat_score(ratio_i),
        })
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
        "temperature": temperature_v2,
        "temperature_v1_price_only": temperature,
        "dimensions": dims,
        "backtest": backtest,
        "position_pct": int(round(temperature_v2 / 5.0) * 5),
        "position_band": _position_band(int(round(temperature_v2 / 5.0) * 5)),
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
