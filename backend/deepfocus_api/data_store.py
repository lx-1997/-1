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
from pathlib import Path
from typing import Any, Optional

from .shared_utils import utc_now_iso

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
