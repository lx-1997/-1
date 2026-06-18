"""持久化数据层：把抓取/计算出的数据点落地 SQLite。

两个用途：① 历史积累——每次构建速判卡/取行情都写一条带时间戳的记录，日积月累成可分析的
数据集；② 读缓存——`latest(..., max_age_seconds=)` 在短窗口内复用上次结果，省掉重复的重型计算/API。

这是「面向投研的数据」维度的**右尺寸第一步**：不上数据湖/数仓/流处理，用平台已有的 SQLite 把数据
先沉淀下来。设计原则——**纯加性、绝不拖垮主流程**：所有写/读失败都静默降级（return / None / []），
既有读路径行为不变。与平台其它 SQLite 存储（agent_tasks / mcp / share_snapshots）同构。
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Optional

from .shared_utils import utc_now_iso

# ⭐ 持久化抑制开关（深度研判用）。深度研判全程以 iFinD 灰度数据取证、产出机构级结论，
# 这些 iFinD 衍生结论绝不可落任何共享持久化面（data_points 被零鉴权 /api/data/history
# 与公开 SEO 页读取）。run_deep_research 进场 set(True)、finally reset，期间本进程上下文
# 内的所有 record() 写入静默跳过——纵深防御：即便取证 agent 误调 get_stock_verdict
# 也不会把 iFinD 衍生 verdict 写进公开缓存。ContextVar 天然按 asyncio 任务隔离，
# 不影响其它并发请求的正常落库。
DEEP_NO_PERSIST: ContextVar[bool] = ContextVar("deep_no_persist", default=False)

DB_PATH = Path(
    os.getenv(
        "DEEPFOCUS_DATA_STORE_DB_PATH",
        str(Path(__file__).resolve().parents[1] / ".data_store.sqlite3"),
    )
)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_data_store() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS data_points (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                symbol TEXT NOT NULL,
                market TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                recorded_ts REAL NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_data_points_lookup "
            "ON data_points(kind, symbol, recorded_ts DESC)"
        )
        conn.commit()
    prune()  # 启动时修剪一次，给每个序列封顶（绝不抛）


# 每个 (kind, symbol) 序列最多保留多少条；以及每多少次写入触发一次修剪（避免无界增长）。
KEEP_PER_SERIES = 500
_PRUNE_EVERY = 200
_write_count = 0


def record(kind: str, symbol: str, payload: Any, *, market: str = "") -> None:
    """写穿一条带时间戳的数据点（历史积累）。失败静默——持久化绝不能拖垮主流程。"""
    global _write_count
    # ⭐ 深度研判进行中：抑制本上下文的一切落库（防 iFinD 衍生结论泄漏到公开历史/SEO）。
    if DEEP_NO_PERSIST.get():
        return
    sym = (symbol or "").upper().strip()
    if not kind or not sym or payload is None:
        return
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO data_points(kind, symbol, market, payload_json, recorded_at, recorded_ts) "
                "VALUES (?,?,?,?,?,?)",
                (
                    kind,
                    sym,
                    (market or "").upper(),
                    json.dumps(payload, ensure_ascii=False, default=str),
                    utc_now_iso(),
                    time.time(),
                ),
            )
            conn.commit()
    except Exception:
        return
    # 周期性修剪，控制长跑进程下表的无界增长（修剪自身失败静默，不影响写入成功）。
    _write_count += 1
    if _write_count % _PRUNE_EVERY == 0:
        prune()


def latest(kind: str, symbol: str, *, max_age_seconds: Optional[float] = None) -> Optional[dict]:
    """最近一条 payload；给了 max_age_seconds 则只返回该窗口内的（读缓存用）。无/过期/失败 → None。"""
    sym = (symbol or "").upper().strip()
    if not sym:
        return None
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT payload_json, recorded_ts FROM data_points "
                "WHERE kind=? AND symbol=? ORDER BY recorded_ts DESC LIMIT 1",
                (kind, sym),
            ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    if max_age_seconds is not None and (time.time() - row["recorded_ts"]) > max_age_seconds:
        return None
    try:
        return json.loads(row["payload_json"])
    except (ValueError, TypeError):
        return None


def history(kind: str, symbol: str, *, limit: int = 200) -> list[dict]:
    """该标的某类数据的历史（新→旧），每项 {recorded_at, payload}。失败 → []。"""
    sym = (symbol or "").upper().strip()
    if not sym:
        return []
    try:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT payload_json, recorded_at FROM data_points "
                "WHERE kind=? AND symbol=? ORDER BY recorded_ts DESC LIMIT ?",
                (kind, sym, max(1, min(int(limit), 1000))),
            ).fetchall()
    except Exception:
        return []
    out: list[dict] = []
    for r in rows:
        try:
            out.append({"recorded_at": r["recorded_at"], "payload": json.loads(r["payload_json"])})
        except (ValueError, TypeError):
            continue
    return out


def hot_symbols(
    kind: str = "verdict",
    *,
    market: Optional[str] = None,
    days: float = 14.0,
    limit: int = 20,
    exclude: Optional[str] = None,
) -> list[dict]:
    """近期被记录最多的标的（关注热度榜）：按 (kind) 在时间窗内的记录条数排序。

    这是站内推荐/「大家也在看」的数据基础——用户研判越多的标的排得越靠前，
    用户量越大榜单越准（数据网络效应的起点）。失败 → []。
    """
    since = time.time() - max(0.0, float(days)) * 86400.0
    sql = (
        "SELECT symbol, market, COUNT(*) AS n, MAX(recorded_ts) AS ts "
        "FROM data_points WHERE kind=? AND recorded_ts>=?"
    )
    params: list[Any] = [kind, since]
    if market:
        sql += " AND market=?"
        params.append(market.upper())
    if exclude:
        sql += " AND symbol<>?"
        params.append(exclude.upper().strip())
    sql += " GROUP BY symbol ORDER BY n DESC, ts DESC LIMIT ?"
    params.append(max(1, min(int(limit), 200)))
    try:
        with _connect() as conn:
            rows = conn.execute(sql, params).fetchall()
    except Exception:
        return []
    return [
        {"symbol": r["symbol"], "market": r["market"], "count": int(r["n"])}
        for r in rows
    ]


def stats() -> dict[str, Any]:
    """数据沉淀概览（观测性种子）：每类数据点数 / 覆盖标的数 / 最新时间。失败 → 空。"""
    try:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT kind, COUNT(*) AS n, COUNT(DISTINCT symbol) AS symbols, MAX(recorded_at) AS latest "
                "FROM data_points GROUP BY kind ORDER BY n DESC"
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) AS n FROM data_points").fetchone()["n"]
    except Exception:
        return {"total": 0, "kinds": []}
    return {
        "total": int(total or 0),
        "kinds": [
            {"kind": r["kind"], "count": r["n"], "symbols": r["symbols"], "latest": r["latest"]}
            for r in rows
        ],
    }


def prune(keep_per_series: Optional[int] = None) -> int:
    """每个 (kind, symbol) 只保留最近 keep_per_series 条，删除更旧的。返回删除行数。失败 → 0。

    keep_per_series 缺省时运行时读模块级 KEEP_PER_SERIES（避免默认参数定义期绑定，便于配置/测试）。
    用窗口函数按时间倒序排名（需 SQLite ≥3.25），删掉排名超出保留数的行。绝不抛——修剪失败不影响主流程。
    """
    keep = KEEP_PER_SERIES if keep_per_series is None else keep_per_series
    try:
        with _connect() as conn:
            cur = conn.execute(
                """
                DELETE FROM data_points
                WHERE id IN (
                    SELECT id FROM (
                        SELECT id, ROW_NUMBER() OVER (
                            PARTITION BY kind, symbol ORDER BY recorded_ts DESC
                        ) AS rn
                        FROM data_points
                    ) WHERE rn > ?
                )
                """,
                (max(1, int(keep)),),
            )
            conn.commit()
            return cur.rowcount or 0
    except Exception:
        return 0
