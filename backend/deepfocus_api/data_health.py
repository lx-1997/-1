"""行情源健康监控：调用成败/延迟/质量落库 + 连续失败自动降级 + 新鲜度告警。

为什么存在：iFinD 配额耗尽曾致 AI 模拟盘静默停摆 34 天才被发现；东财 push2 对云 IP
封锁迫使临时换源。目标：任何行情源断供在分钟级通过管理员端点暴露，并在降级链里
自动绕开连续失败的源——而不是靠用户感知或事后排障。

三层结构：
1. record_source_call：各行情链路（quotes/kline/fund/ingest）每次调用落一行事件（尽力而为，绝不抛出）。
2. provider_in_cooldown：同源连续失败 ≥3 次进入 10 分钟冷却，行情调度跳过该源（末位兜底除外）。
3. health_snapshot：按源聚合失败率/连续失败/最近成功时间，按 kind 给新鲜度分级与告警清单。
"""
from __future__ import annotations

import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from . import db
from .shared_utils import utc_now_iso

DB_PATH = db.data_path(".data_health.sqlite3", "DEEPFOCUS_DATA_HEALTH_DB_PATH")

# 各链路的新鲜度阈值（小时）：超过则视为「断供嫌疑」，进入告警清单
FRESHNESS_THRESHOLD_HOURS = {
    "quotes": 6.0,    # 实时报价链（盘中高频调用，6h 无成功基本等于断供）
    "kline": 26.0,    # 日线/历史行情
    "fund": 26.0,     # 模拟盘行情撮合
    "ingest": 26.0,   # 内容入库
}

FAIL_THRESHOLD = 3          # 连续失败次数达到该值 → 进入冷却
COOLDOWN_SECONDS = 600.0    # 冷却时长 10 分钟
PRUNE_DAYS = 7

# 进程内快速决策状态（重启即重置——冷却本就是短周期行为，不依赖落库）
_CONSEC_FAILS: dict[tuple[str, str], int] = defaultdict(int)
_COOLDOWN_UNTIL: dict[tuple[str, str], float] = {}
_PRUNE_TICK = [0]
_DB_READY = [False]


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = db.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_db() -> None:
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS data_health_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                source TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'quotes',
                symbol TEXT NOT NULL DEFAULT '',
                ok INTEGER NOT NULL DEFAULT 0,
                latency_ms REAL,
                quality TEXT NOT NULL DEFAULT '',
                detail TEXT NOT NULL DEFAULT ''
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_dhe_source_ts ON data_health_events(source, ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_dhe_kind_ts ON data_health_events(kind, ts)")
        conn.commit()


def record_source_call(
    source: str,
    kind: str,
    ok: bool,
    *,
    symbol: str = "",
    latency_ms: Optional[float] = None,
    quality: str = "",
    detail: str = "",
) -> None:
    """记录一次行情源调用结果。尽力而为：任何异常静默吞掉，绝不影响主链路。"""
    source = str(source or "unknown")[:60]
    kind = str(kind or "quotes")[:20]
    try:
        now = utc_now_iso()
        key = (source, kind)
        _CONSEC_FAILS[key] = 0 if ok else _CONSEC_FAILS[key] + 1
        if not ok and _CONSEC_FAILS[key] >= FAIL_THRESHOLD:
            _COOLDOWN_UNTIL[key] = time.monotonic() + COOLDOWN_SECONDS
        elif ok:
            _COOLDOWN_UNTIL.pop(key, None)
        _PRUNE_TICK[0] += 1
        prune = _PRUNE_TICK[0] % 50 == 0
        if not _DB_READY[0]:
            _init_db()
            _DB_READY[0] = True
        with _connect() as conn:
            conn.execute(
                "INSERT INTO data_health_events (ts, source, kind, symbol, ok, latency_ms, quality, detail)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (now, source, kind, str(symbol or "")[:30], 1 if ok else 0,
                 round(latency_ms, 1) if latency_ms is not None else None,
                 str(quality or "")[:40], str(detail or "")[:200]),
            )
            if prune:
                cutoff = (datetime.now(timezone.utc) - timedelta(days=PRUNE_DAYS)).isoformat()
                conn.execute("DELETE FROM data_health_events WHERE ts < ?", (cutoff,))
            conn.commit()
    except Exception:  # noqa: BLE001 —— 监控自身绝不拖垮行情链路
        pass


def provider_in_cooldown(source: str, kind: str = "quotes") -> bool:
    """该源是否处于连续失败冷却期（调度方应暂时绕开，末位兜底除外）。"""
    until = _COOLDOWN_UNTIL.get((str(source), str(kind)))
    if until is None:
        return False
    if time.monotonic() >= until:
        _COOLDOWN_UNTIL.pop((str(source), str(kind)), None)
        return False
    return True


def _parse_ts(value: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:  # noqa: BLE001
        return None


def health_snapshot(window_hours: int = 24) -> dict[str, Any]:
    """管理员健康快照：按源聚合 24h 成败/延迟 + 按 kind 聚合新鲜度 + 告警清单。"""
    _init_db()
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=max(1, window_hours))).isoformat()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT source, kind, ok, latency_ms, ts, quality, detail FROM data_health_events"
            " WHERE ts >= ? ORDER BY ts ASC", (cutoff,),
        ).fetchall()

    agg: dict[tuple[str, str], dict[str, Any]] = {}
    fresh: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = (row["source"], row["kind"])
        cell = agg.setdefault(key, {
            "source": row["source"], "kind": row["kind"], "calls": 0, "fails": 0,
            "latency_sum": 0.0, "latency_n": 0, "last_ok_at": "", "last_fail_at": "",
        })
        cell["calls"] += 1
        if not row["ok"]:
            cell["fails"] += 1
            cell["last_fail_at"] = row["ts"]
        else:
            cell["last_ok_at"] = row["ts"]
        if row["latency_ms"] is not None:
            cell["latency_sum"] += float(row["latency_ms"])
            cell["latency_n"] += 1
        # 全局（跨源）按 kind 取最近一次成功——新鲜度看「这条链路还有没有活口」
        if row["ok"]:
            cur = fresh.get(row["kind"])
            if cur is None or row["ts"] > cur["last_success_at"]:
                fresh[row["kind"]] = {"kind": row["kind"], "last_success_at": row["ts"], "source": row["source"]}

    sources: list[dict[str, Any]] = []
    alerts: list[str] = []
    for (source, kind), cell in sorted(agg.items()):
        key = (source, kind)
        fail_rate = round(cell["fails"] / cell["calls"] * 100, 1) if cell["calls"] else 0.0
        consec = _CONSEC_FAILS.get(key, 0)
        cooldown = provider_in_cooldown(source, kind)
        entry = {
            "source": source, "kind": kind, "calls": cell["calls"], "fails": cell["fails"],
            "fail_rate_pct": fail_rate, "consecutive_fails": consec,
            "cooldown_until": (datetime.now(timezone.utc) + timedelta(
                seconds=max(0.0, _COOLDOWN_UNTIL[key] - time.monotonic()))).isoformat() if cooldown else "",
            "last_ok_at": cell["last_ok_at"], "last_fail_at": cell["last_fail_at"],
            "avg_latency_ms": round(cell["latency_sum"] / cell["latency_n"], 1) if cell["latency_n"] else None,
        }
        sources.append(entry)
        if cooldown:
            alerts.append(f"{source}({kind}) 连续失败 {consec} 次，已进入 10 分钟降级冷却")

    freshness: list[dict[str, Any]] = []
    for kind, threshold_h in FRESHNESS_THRESHOLD_HOURS.items():
        info = fresh.get(kind)
        if not info:
            freshness.append({"kind": kind, "last_success_at": "", "age_hours": None,
                              "status": "no_data", "threshold_hours": threshold_h,
                              "source": ""})
            alerts.append(f"链路 {kind} 在近 {window_hours}h 内无任何成功调用")
            continue
        dt = _parse_ts(info["last_success_at"])
        age_h = round((now - dt).total_seconds() / 3600.0, 2) if dt else None
        status = "ok"
        if age_h is not None and age_h >= threshold_h:
            status = "stale"
            alerts.append(f"链路 {kind} 最近成功已是 {age_h}h 前（阈值 {threshold_h}h，源 {info['source']}）——疑似断供")
        freshness.append({"kind": kind, "last_success_at": info["last_success_at"], "age_hours": age_h,
                          "status": status, "threshold_hours": threshold_h, "source": info["source"]})

    return {
        "generated_at": utc_now_iso(),
        "window_hours": max(1, window_hours),
        "sources": sources,
        "freshness": freshness,
        "alerts": alerts,
        "ok": not alerts,
    }


# ── 管理员端点：/api/admin/data-health —— 断供分钟级暴露，不再靠用户感知 ──────
from fastapi import APIRouter, Depends  # noqa: E402

from .auth import require_admin  # noqa: E402

router = APIRouter(prefix="/api/admin", tags=["data-health"])


@router.get("/data-health")
async def admin_data_health(window_hours: int = 24, _admin: dict = Depends(require_admin)) -> dict[str, Any]:
    """行情源健康快照：24h 各源成败/延迟/冷却状态 + 各链路新鲜度分级 + 断供告警清单。"""
    return health_snapshot(window_hours)
