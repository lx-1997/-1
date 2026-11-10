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


@router.get("/api/market-dashboard/insight-study")
async def macro_insight_study_report() -> dict[str, Any]:
    """Frozen research summary only; no account data or arbitrary file access."""
    from .macro_insight_report import REPORT
    return REPORT


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
_GAUGE_POINTS = 520  # 覆盖约两年：回测需要跨牛熊的温度分桶样本
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
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
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


async def _breadth_score() -> Optional[int]:
    """市场宽度：A股市值前 120 中上涨家数占比 → 0-100（35% 涨=0，65% 涨=100）。"""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Referer": "http://finance.sina.com.cn/",
        }
        url = ("http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
               "Market_Center.getHQNodeData?page=1&num=120&sort=mktcap&asc=0"
               "&node=hs_a&symbol=&_s_r_a=page")
        async with httpx.AsyncClient(trust_env=False, timeout=15.0, headers=headers) as client:
            r = await client.get(url)
        data = r.json()
        if isinstance(data, str):
            import json as _json
            data = _json.loads(data)
        rows = [d for d in data or [] if isinstance(d, dict) and d.get("changepercent") is not None]
        if len(rows) < 60:
            return None
        up = sum(1 for d in rows if float(d["changepercent"]) > 0)
        ratio = up / len(rows)
        return round(max(0.0, min(1.0, (ratio - 0.35) / 0.3)) * 100)
    except Exception:  # noqa: BLE001
        return None


async def _vix_score() -> Optional[int]:
    """VIX 恐慌指数 → 波动率分：12 → 100，35 → 0（线性）。走市场看板缓存，失败 → None。"""
    try:
        from .market_dashboard import fetch_market_dashboard

        def _walk(o: Any) -> Optional[float]:
            if isinstance(o, dict):
                for k, v in o.items():
                    if k in ("vix", "spx_vix") and isinstance(v, dict) and v.get("price") is not None:
                        try:
                            return float(v["price"])
                        except (TypeError, ValueError):
                            return None
                    found = _walk(v)
                    if found is not None:
                        return found
            elif isinstance(o, list):
                for v in o:
                    found = _walk(v)
                    if found is not None:
                        return found
            return None

        data = await asyncio.wait_for(fetch_market_dashboard(), timeout=20)
        vix = _walk(data)
        if vix is None or vix <= 0:
            return None
        return round(max(0.0, min(1.0, (35.0 - vix) / 23.0)) * 100)
    except Exception:  # noqa: BLE001
        return None


async def _lift_score() -> Optional[int]:
    """解禁压力反向分：未来 30 日解禁市值 vs 近 6 个月月均 → ≤0.8 倍满分，≥2 倍零分。"""
    try:
        import httpx
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        from . import cn_calendar as cal
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                   "Referer": "https://data.eastmoney.com/"}
        now = _dt.now(_tz.utc)
        past = await asyncio.wait_for(cal._fetch_lift_bans(
            httpx.AsyncClient(trust_env=False, timeout=15.0, headers=headers),
            (now - _td(days=180)).strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d")), timeout=20)
        future = await asyncio.wait_for(cal._fetch_lift_bans(
            httpx.AsyncClient(trust_env=False, timeout=15.0, headers=headers),
            now.strftime("%Y-%m-%d"), (now + _td(days=30)).strftime("%Y-%m-%d")), timeout=20)
        if not past:
            return None
        past_monthly = sum(float(p.get("market_cap_wy") or 0) for p in past) / 6.0
        future_cap = sum(float(f.get("market_cap_wy") or 0) for f in future)
        if past_monthly <= 0:
            return None
        ratio = future_cap / past_monthly
        return round(max(0.0, min(1.0, (2.0 - ratio) / 1.2)) * 100)
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


def _intraday_elapsed_ratio(now: Optional[Any] = None) -> Optional[float]:
    """今日已交易分钟占全日(240min)比例；非 A 股交易时段返回 None（今日无盘中半根 bar）。
    交易时段=交易日 09:30–11:30 / 13:00–15:00（周末即可判定；节假日误差只影响归一精度，可容忍）。"""
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    d = now or _dt.now(_tz(_td(hours=8)))
    if d.weekday() >= 5:
        return None
    hm = d.hour * 60 + d.minute
    if not (570 <= hm <= 900):
        return None
    if hm <= 690:        # 上午 09:30–11:30
        elapsed = hm - 570
    elif hm < 780:       # 午间休市：上午已交易满 120 分钟（市场暂停，进度不再增加）
        elapsed = 120
    else:                # 下午 13:00–15:00
        elapsed = 120 + (hm - 780)
    return max(0.05, min(1.0, elapsed / 240.0))


def _adj_heat_ratio(raw_ratio: float, elapsed: Optional[float]) -> float:
    """盘中今日 bar 是半根 K 线：按已交易分钟占比还原全日口径。
    不修正时早盘量能比必然被腰斩（实测上午 0 分、午后 39 分），用户看到的资金热度随分钟漂移。"""
    if elapsed is not None and elapsed < 1.0:
        return raw_ratio / max(elapsed, 0.15)
    return raw_ratio


# ---- v2.5 参数自优化：滚动前推（walk-forward）+ 样本外验收 ----
# 机制：训练窗 240 个交易日网格寻优（方向准确率）→ 60 日未见样本验收 → 滚动前进。
# 只有优化参数在未见样本上确实验更准（≥默认参数+2pct）才采纳；否则诚实沿用默认。

_DEF_PARAMS = {"short": 20, "long": 60, "w_tech": 0.6}
_OPT_GRID = [(s, l, w) for s in (5, 10, 20) for l in (20, 40, 60) for w in (0.5, 0.6, 0.7)]
_WF_TRAIN, _WF_TEST, _WF_STEP = 240, 60, 60
_OPT_ADOPT_EDGE = 2.0  # 未见样本准确率至少比默认高 2 个百分点才采纳

_TECH_UP_STATES = {"上涨右侧", "上涨左侧"}


def _daily_temperature(bars: list, short_n: int, long_n: int, w_tech: float) -> list[dict[str, Any]]:
    """给定均线参数，逐日计算技术状态/资金热度/温度（只用截至当日的数据）。"""
    closes = [b[1] for b in bars]
    amounts = [b[2] for b in bars]
    daily: list[dict[str, Any]] = []
    for i in range(max(short_n, long_n) - 1, len(bars)):
        c_i = closes[: i + 1]
        ma_s, ma_l = _sma(c_i, short_n), _sma(c_i, long_n)
        if ma_s is None or ma_l is None:
            continue
        c, m_s, m_l = c_i[-1], ma_s, ma_l
        if c > m_s > m_l:
            t = "上涨右侧"
        elif c < m_s < m_l:
            t = "下跌右侧"
        elif m_s > m_l:
            t = "上涨左侧"
        elif m_s < m_l:
            t = "下跌左侧"
        else:
            t = "震荡"
        amt_i = amounts[: i + 1]
        amt_ma = _sma(amt_i, 20)
        ratio = (amt_i[-1] / amt_ma) if (amt_ma and amt_ma > 0) else None
        tech_score = _TECH_SCORE.get(t, 50)
        heat_score = _heat_score(ratio)
        temp = max(0, min(100, round(tech_score * w_tech + heat_score * (1 - w_tech))))
        daily.append({
            "date": bars[i][0], "close": c, "tech_state": t, "tech_score": tech_score,
            "heat_state": _heat_state(ratio), "heat_ratio": ratio,
            "heat_score": heat_score, "temperature": temp,
        })
    return daily


def _direction_accuracy(daily: list[dict[str, Any]], i0: int, i1: int) -> Optional[float]:
    """方向判断准确率：状态看多/看空 与 其后 5 日实际方向 的一致比例。"""
    correct = total = 0
    for i in range(i0, i1 - 5):
        state = daily[i].get("tech_state") or ""
        if state in ("上涨右侧", "上涨左侧"):
            predicted_up = True
        elif state in ("下跌右侧", "下跌左侧"):
            predicted_up = False
        else:
            continue
        c_now, c_fwd = daily[i]["close"], daily[i + 5]["close"]
        if c_now <= 0:
            continue
        actual_up = c_fwd > c_now
        total += 1
        correct += int(predicted_up == actual_up)
    return round(correct / total * 100, 2) if total >= 20 else None


def _walk_forward_params(bars: list) -> dict[str, Any]:
    """滚动前推寻优：每折训练窗内选最优参数，未见样本验收；返回采纳结果与完整报告。"""
    bl = len(bars)
    daily_def = _daily_temperature(bars, _DEF_PARAMS["short"], _DEF_PARAMS["long"], _DEF_PARAMS["w_tech"])
    def_acc = _direction_accuracy(daily_def, 0, bl)
    combo_oos: dict[tuple, list[float]] = {}
    folds = 0
    start = 0
    while start + _WF_TRAIN + 5 <= bl:
        train_i0, train_i1 = start, min(start + _WF_TRAIN, bl - 5)
        best, best_acc = None, -1.0
        for s, l, w in _OPT_GRID:
            d = _daily_temperature(bars, s, l, w)
            acc = _direction_accuracy(d, train_i0, train_i1)
            if acc is not None and acc > best_acc:
                best, best_acc = (s, l, w), acc
        if best is None:
            break
        d_test = _daily_temperature(bars, best[0], best[1], best[2])
        oos = _direction_accuracy(d_test, train_i1, min(train_i1 + _WF_TEST, bl))
        if oos is not None:
            combo_oos.setdefault(best, []).append(oos)
            folds += 1
        start += _WF_STEP
    chosen = _DEF_PARAMS
    adopted = False
    oos_def_mean = _mean(combo_oos.get((_DEF_PARAMS["short"], _DEF_PARAMS["long"], _DEF_PARAMS["w_tech"]), []))
    best_combo, best_oos = None, -1.0
    for combo, accs in combo_oos.items():
        m = _mean(accs)
        if m > best_oos:
            best_combo, best_oos = combo, m
    if best_combo and best_oos is not None and oos_def_mean is not None and best_oos >= oos_def_mean + _OPT_ADOPT_EDGE:
        chosen = {"short": best_combo[0], "long": best_combo[1], "w_tech": best_combo[2]}
        adopted = True
    return {
        "folds": folds,
        "oos_default": oos_def_mean,
        "oos_optimized": best_oos if best_oos is not None and best_oos >= 0 else None,
        "adopted": adopted,
        "params": chosen,
        "combo_oos_mean": {f"MA{c[0]}/{c[1]} w{c[2]}": round(m, 2) for c, m in sorted(combo_oos.items())},
    }


def _mean(xs: list[float]) -> Optional[float]:
    return round(sum(xs) / len(xs), 2) if xs else None


async def market_gauge() -> dict[str, Any]:
    """Completed multi-index daily model; historical and current scores share rules."""
    from .market_regime_data import market_gauge_v4
    return await market_gauge_v4()


@router.get("/api/econ/calendar")
async def api_econ_calendar() -> dict[str, Any]:
    """宏观数据日历（今天/明天）：时间/地区/事件/前值/预期/重要性星级。"""
    return await econ_calendar()


@router.get("/api/market/gauge")
async def api_market_gauge() -> dict[str, Any]:
    """A 股市场状态仪表：技术形态 / 资金热度 / 市场温度。"""
    return await market_gauge()
