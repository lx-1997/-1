"""信号→收益归因闭环：把站内内容信号（快讯/研报/文章/纪要）变成可度量的信息边际。

数据底座：symbol_linker 的 message_symbols 倒排索引（provider/meta/alias 三源打标）。
对每条「消息×标的」信号，用真实日线计算发布后 N 个交易日的前瞻收益与相对基准指数的
超额收益，落库并聚合出命中率/平均超额——研报解读与题材标签由此从「资讯功能」升级为
「可验证的信息边际」。

口径（诚实约定）：
- 基线 = 消息北京时间日 **前一交易日** 收盘（消息发布前市场已知的价格，避免用当日收盘
  造成前视偏差）；前瞻收益自基线起算 T+N。
- 基准：A股→沪深300(000300.SH)，港股→恒生(HSI)，美股→SPY；基准缺失时超额记 None。
- 行情一律走 backtest_data 真实日线多源链（带缓存与健康台账），无数据就跳过，绝不造数。
"""
from __future__ import annotations

import asyncio
import sqlite3
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from . import db
from .shared_utils import safe_float, utc_now_iso

DB_PATH = db.data_path(".signal_attribution.sqlite3", "DEEPFOCUS_SIGNAL_ATTRIB_DB_PATH")

HORIZONS = (1, 3, 5, 10, 20)
BENCHMARK_BY_MARKET = {"CN": "000300.SH", "HK": "HSI", "US": "SPY"}
BJ_TZ = timezone(timedelta(hours=8))
FETCH_CONCURRENCY = 6
MAX_SYMBOLS_PER_BATCH = 25          # 单次计算最多新拉多少只标的的 K 线（其余留待下轮）
LOOKBACK_DAYS = 180                 # 只归因最近 N 天的信号

_FIELDS = ("message_id", "symbol", "source_type", "topic", "title", "signal_at",
           "baseline_date", "horizon", "fwd_ret_pct", "bench_ret_pct", "excess_pct", "computed_at")


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = db.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signal_attrib (
                message_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                source_type TEXT NOT NULL DEFAULT '',
                topic TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                signal_at TEXT NOT NULL,
                baseline_date TEXT NOT NULL DEFAULT '',
                horizon INTEGER NOT NULL,
                fwd_ret_pct REAL,
                bench_ret_pct REAL,
                excess_pct REAL,
                computed_at TEXT NOT NULL,
                PRIMARY KEY (message_id, symbol, horizon)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sa_symbol ON signal_attrib(symbol, horizon)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sa_type ON signal_attrib(source_type, horizon)")
        conn.commit()


def _bj_date(utc_iso: str) -> str:
    try:
        dt = datetime.fromisoformat(str(utc_iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(BJ_TZ).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return ""


def _pending_signals(limit_symbols: int) -> dict[str, list[dict[str, Any]]]:
    """取「20 日窗口尚未填满」的信号，按标的分组（每标的只拉一次 K 线）。"""
    init_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).isoformat()
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    with _connect() as conn:
        rows = conn.execute(
            """SELECT ms.message_id, ms.symbol, m.source_type, m.topic, m.title, m.created_at
               FROM message_symbols ms
               JOIN realtime_messages m ON m.id = ms.message_id
               WHERE m.created_at >= ?
               AND NOT EXISTS (SELECT 1 FROM signal_attrib sa
                               WHERE sa.message_id = ms.message_id AND sa.symbol = ms.symbol AND sa.horizon = 20)
               ORDER BY m.created_at DESC""",
            (cutoff,),
        ).fetchall()
    for row in rows:
        sym = str(row["symbol"] or "").upper()
        if not sym:
            continue
        by_symbol.setdefault(sym, []).append({
            "message_id": row["message_id"], "symbol": sym,
            "source_type": str(row["source_type"] or "资讯"),
            "topic": str(row["topic"] or ""), "title": str(row["title"] or "")[:160],
            "signal_at": row["created_at"],
        })
    # 老信号优先补齐；单批限拉 K 线的标的数，控制外部请求量
    ordered: dict[str, list[dict[str, Any]]] = {}
    for sym in sorted(by_symbol, key=lambda s: min(x["signal_at"] for x in by_symbol[s])):
        ordered[sym] = by_symbol[sym]
        if len(ordered) >= max(1, limit_symbols):
            break
    return ordered


def _close_on(bars: list[dict[str, Any]], date: str) -> Optional[float]:
    for bar in bars:
        if str(bar.get("date", "")) == date:
            value = safe_float(bar.get("close"), 0.0)
            return value if value > 0 else None
    return None


def _bench_ret(bench_map: dict[str, float], d0: str, d1: str) -> Optional[float]:
    """基准在 [d0→d1] 的收益；日期缺失时向前找最近交易日（≤7 天），找不到记 None。"""
    def _lookup(date: str) -> Optional[float]:
        if date in bench_map:
            return bench_map[date]
        try:
            cur = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            return None
        for _ in range(7):
            cur -= timedelta(days=1)
            key = cur.strftime("%Y-%m-%d")
            if key in bench_map:
                return bench_map[key]
        return None

    c0, c1 = _lookup(d0), _lookup(d1)
    if c0 and c1 and c0 > 0:
        return round((c1 / c0 - 1) * 100, 4)
    return None


async def _attribute_symbol(symbol: str, signals: list[dict[str, Any]], bench_map: dict[str, float]) -> dict[str, Any]:
    from .backtest_data import fetch_historical_ohlcv
    end = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    start = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS + 40)).strftime("%Y-%m-%d")
    payload = await fetch_historical_ohlcv(symbol, start, end)
    bars = sorted((payload.get("bars") or []), key=lambda b: str(b.get("date", "")))
    dates = [str(b.get("date", "")) for b in bars]
    rows_written = 0
    if bars:
        for sig in signals:
            d_sig = _bj_date(sig["signal_at"])
            if not d_sig:
                continue
            idx0 = max((i for i, d in enumerate(dates) if d < d_sig), default=-1)
            if idx0 < 0:
                continue  # 上市晚于信号或无更早收盘——跳过，下一轮再试
            c0 = safe_float(bars[idx0].get("close"), 0.0)
            if c0 <= 0:
                continue
            for horizon in HORIZONS:
                j = idx0 + horizon
                if j >= len(bars):
                    continue  # 窗口未走完，留待后续轮次补齐
                c1 = safe_float(bars[j].get("close"), 0.0)
                if c1 <= 0:
                    continue
                fwd = round((c1 / c0 - 1) * 100, 4)
                bench = _bench_ret(bench_map, dates[idx0], dates[j])
                excess = round(fwd - bench, 4) if bench is not None else None
                with _connect() as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO signal_attrib "
                        "(message_id, symbol, source_type, topic, title, signal_at, baseline_date, horizon,"
                        " fwd_ret_pct, bench_ret_pct, excess_pct, computed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (sig["message_id"], symbol, sig["source_type"], sig["topic"], sig["title"],
                         sig["signal_at"], dates[idx0], horizon, fwd, bench, excess, utc_now_iso()),
                    )
                    conn.commit()
                rows_written += 1
    return {"symbol": symbol, "bars": len(bars), "source": payload.get("source", ""), "rows": rows_written}


async def _bench_map_for(markets: set[str]) -> dict[str, float]:
    from .backtest_data import fetch_historical_ohlcv
    end = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    start = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS + 40)).strftime("%Y-%m-%d")
    result: dict[str, float] = {}
    for market in markets:
        code = BENCHMARK_BY_MARKET.get(market)
        if not code:
            continue
        payload = await fetch_historical_ohlcv(code, start, end)
        for bar in payload.get("bars") or []:
            close = safe_float(bar.get("close"), 0.0)
            if close > 0:
                result[str(bar.get("date", ""))] = close
    return result


async def run_attribution_batch(max_symbols: int = MAX_SYMBOLS_PER_BATCH) -> dict[str, Any]:
    """补齐一批待归因信号（受标的数与并发上限约束，幂等可反复调用）。"""
    pending = _pending_signals(max_symbols)
    if not pending:
        return {"ok": True, "computed_symbols": 0, "rows": 0, "remaining": 0,
                "note": "没有待归因的信号（或全部窗口已填满）"}
    from . import symbol_linker as _sl  # 仅用于确认索引可用
    markets = set()
    try:
        from .backtest_data import normalize_market_symbol
        markets = {str(normalize_market_symbol(sym).get("market") or "CN") for sym in pending}
    except Exception:  # noqa: BLE001
        markets = {"CN"}
    bench_map = await _bench_map_for(markets)
    sem = asyncio.Semaphore(FETCH_CONCURRENCY)

    async def _guarded(sym: str, sigs: list[dict[str, Any]]) -> dict[str, Any]:
        async with sem:
            try:
                return await _attribute_symbol(sym, sigs, bench_map)
            except Exception as exc:  # noqa: BLE001 —— 单标的失败不拖垮整批
                return {"symbol": sym, "error": f"{type(exc).__name__}: {exc}"[:160], "rows": 0}

    results = await asyncio.gather(*(_guarded(sym, sigs) for sym, sigs in pending.items()))
    rows = sum(int(r.get("rows") or 0) for r in results)
    with _connect() as conn:
        remaining = conn.execute(
            """SELECT COUNT(DISTINCT ms.message_id || '|' || ms.symbol) FROM message_symbols ms
               JOIN realtime_messages m ON m.id = ms.message_id
               WHERE m.created_at >= ?
               AND NOT EXISTS (SELECT 1 FROM signal_attrib sa
                               WHERE sa.message_id = ms.message_id AND sa.symbol = ms.symbol AND sa.horizon = 20)""",
            ((datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).isoformat(),),
        ).fetchone()[0]
    return {"ok": True, "computed_symbols": len(pending), "rows": rows,
            "remaining": int(remaining), "details": results}


def attribution_summary(window_days: int = 180, topic_top: int = 12) -> dict[str, Any]:
    """聚合命中率：按信号来源×窗口给出样本量/正收益占比/平均超额；附题材榜与近期信号。"""
    init_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(7, window_days))).isoformat()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT source_type, topic, symbol, title, signal_at, horizon, fwd_ret_pct, excess_pct"
            " FROM signal_attrib WHERE computed_at >= ? AND fwd_ret_pct IS NOT NULL",
            (cutoff,),
        ).fetchall()

    def _bucket_stats(items: list[sqlite3.Row]) -> dict[str, Any]:
        fwd = [safe_float(r["fwd_ret_pct"]) for r in items]
        excess = [safe_float(r["excess_pct"]) for r in items if r["excess_pct"] is not None]
        if not fwd:
            return {}
        return {
            "n": len(fwd),
            "hit_rate_pct": round(sum(1 for v in fwd if v > 0) / len(fwd) * 100, 1),
            "avg_fwd_pct": round(statistics.fmean(fwd), 3),
            "median_fwd_pct": round(statistics.median(fwd), 3),
            "avg_excess_pct": round(statistics.fmean(excess), 3) if excess else None,
            "excess_hit_rate_pct": round(sum(1 for v in excess if v > 0) / len(excess) * 100, 1) if excess else None,
        }

    by_type: dict[str, dict[str, Any]] = {}
    for horizon in HORIZONS:
        subset = [r for r in rows if r["horizon"] == horizon]
        grouped: dict[str, list[sqlite3.Row]] = {}
        for r in subset:
            grouped.setdefault(str(r["source_type"] or "资讯"), []).append(r)
        by_type[str(horizon)] = {name: _bucket_stats(items) for name, items in sorted(grouped.items())}

    topics: list[dict[str, Any]] = []
    horizon_focus = 5
    topic_rows = [r for r in rows if r["horizon"] == horizon_focus and (r["topic"] or "") not in ("", "data-source")]
    topic_groups: dict[str, list[sqlite3.Row]] = {}
    for r in topic_rows:
        topic_groups.setdefault(str(r["topic"]), []).append(r)
    for name, items in topic_groups.items():
        stats = _bucket_stats(items)
        if stats.get("n", 0) >= 3:
            topics.append({"topic": name, "horizon": horizon_focus, **stats})
    topics.sort(key=lambda t: (t.get("avg_excess_pct") or -999), reverse=True)

    with _connect() as conn:
        total_signals = conn.execute("SELECT COUNT(DISTINCT message_id || '|' || symbol) FROM signal_attrib").fetchone()[0]
        complete = conn.execute(
            "SELECT COUNT(*) FROM (SELECT message_id, symbol FROM signal_attrib GROUP BY message_id, symbol"
            " HAVING SUM(CASE WHEN horizon=20 THEN 1 ELSE 0 END) > 0)"
        ).fetchone()[0]
        recent = [dict(r) for r in conn.execute(
            "SELECT symbol, source_type, topic, title, signal_at, horizon, fwd_ret_pct, excess_pct"
            " FROM signal_attrib WHERE horizon = 5 ORDER BY computed_at DESC LIMIT 20").fetchall()]

    return {
        "generated_at": utc_now_iso(), "window_days": max(7, window_days),
        "baseline_convention": "基线=信号北京时间日前一交易日收盘；前瞻收益自基线起算 T+N",
        "benchmarks": BENCHMARK_BY_MARKET,
        "totals": {"signals_seen": int(total_signals), "signals_complete": int(complete)},
        "by_source_type": by_type,
        "topics_by_excess": topics[:max(1, topic_top)],
        "recent": recent,
    }


# ── 端点：读公开（聚合统计）、算管理员（触发补齐批） ────────────────────────────
from fastapi import APIRouter, Depends  # noqa: E402

from .auth import require_admin  # noqa: E402

router = APIRouter(prefix="/api/quant/signal-attribution", tags=["signal-attribution"])


@router.get("")
async def get_signal_attribution(window_days: int = 180) -> dict[str, Any]:
    """信号命中率聚合（公开只读）：来源×窗口的胜率/平均前瞻/超额，题材榜与近期信号。"""
    return attribution_summary(window_days=window_days)


@router.post("/compute")
async def compute_signal_attribution(max_symbols: int = MAX_SYMBOLS_PER_BATCH,
                                     _admin: dict = Depends(require_admin)) -> dict[str, Any]:
    """触发一轮增量归因（管理员）：补齐待计算信号，幂等，可由 cron 反复调用。"""
    return await run_attribution_batch(max_symbols=max_symbols)
