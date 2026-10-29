"""持久化数据层：把抓取/计算出的数据点落地 SQLite。

两个用途：① 历史积累——每次构建速判卡/取行情都写一条带时间戳的记录，日积月累成可分析的
数据集；② 读缓存——`latest(..., max_age_seconds=)` 在短窗口内复用上次结果，省掉重复的重型计算/API。

这是「面向投研的数据」维度的**右尺寸第一步**：不上数据湖/数仓/流处理，用平台已有的 SQLite 把数据
先沉淀下来。设计原则——**纯加性、绝不拖垮主流程**：所有写/读失败都静默降级（return / None / []），
既有读路径行为不变。与平台其它 SQLite 存储（agent_tasks / mcp / share_snapshots）同构。
"""
from __future__ import annotations

import asyncio
import atexit
import concurrent.futures
import json
import logging
import queue
import sqlite3
import threading
import time
from contextlib import closing
from contextvars import ContextVar
from typing import Any, Optional

from . import db
from .shared_utils import utc_now_iso

# ⭐ 持久化抑制开关（深度研判用）。深度研判全程以 iFinD 灰度数据取证、产出机构级结论，
# 这些 iFinD 衍生结论绝不可落任何共享持久化面（data_points 被零鉴权 /api/data/history
# 与公开 SEO 页读取）。run_deep_research 进场 set(True)、finally reset，期间本进程上下文
# 内的所有 record() 写入静默跳过——纵深防御：即便取证 agent 误调 get_stock_verdict
# 也不会把 iFinD 衍生 verdict 写进公开缓存。ContextVar 天然按 asyncio 任务隔离，
# 不影响其它并发请求的正常落库。
DEEP_NO_PERSIST: ContextVar[bool] = ContextVar("deep_no_persist", default=False)

DB_PATH = db.data_path('.data_store.sqlite3', 'DEEPFOCUS_DATA_STORE_DB_PATH')
logger = logging.getLogger(__name__)


def _connect() -> sqlite3.Connection:
    # 落库是加性缓存；锁争用最多等 1s，不能长时间占住 writer。
    conn = db.connect(DB_PATH, timeout=1.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_data_store() -> None:
    global _WRITES_CLOSED
    _WRITES_CLOSED = False
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(_connect()) as conn:
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


# 每次写入只修剪当前序列，用 lookup 索引封顶；请求路径不做全表窗口排序。
KEEP_PER_SERIES = 500


def _record_values(kind: str, sym: str, values: tuple[Any, ...]) -> bool:
    try:
        with closing(_connect()) as conn:
            conn.execute(
                "INSERT INTO data_points(kind, symbol, market, payload_json, recorded_at, recorded_ts) "
                "VALUES (?,?,?,?,?,?)", values,
            )
            _prune_series(conn, kind, sym, max(1, int(KEEP_PER_SERIES)))
            conn.commit()
        return True
    except Exception:
        return False


def _record_params(kind: str, symbol: str, payload: Any, market: str) -> tuple[Any, ...] | None:
    # 必须在调用方检查 ContextVar，writer 线程不能继承深度研判的任务上下文。
    if DEEP_NO_PERSIST.get():
        return None
    sym = (symbol or "").upper().strip()
    if not kind or not sym or payload is None:
        return None
    try:
        return (kind, sym, (market or "").upper(),
                json.dumps(payload, ensure_ascii=False, default=str), utc_now_iso(), time.time())
    except Exception:
        return None


def record(kind: str, symbol: str, payload: Any, *, market: str = "") -> None:
    """同步写穿（CLI/同步调用兼容）；async 路径使用 await async_record()。"""
    values = _record_params(kind, symbol, payload, market)
    if values is not None:
        _record_values(values[0], values[1], values)


def record_confirmed(kind: str, symbol: str, payload: Any, *, market: str = "") -> bool:
    """Acknowledge durable writes for a transactional producer's outbox."""
    values = _record_params(kind, symbol, payload, market)
    return values is not None and _record_values(values[0], values[1], values)


# 一条专用线程，严格限制排队条数和 JSON 字节数，避免 ThreadPoolExecutor 无界队列。
_WRITE_QUEUE_MAX = 128
_WRITE_QUEUE_BYTES = 8 * 1024 * 1024
_WRITER: "_DataWriter | None" = None
_WRITER_LOCK = threading.Lock()
_WRITES_CLOSED = False


class _DataWriter:
    def __init__(self) -> None:
        self.jobs: queue.Queue = queue.Queue(maxsize=_WRITE_QUEUE_MAX)
        self.lock = threading.Lock()
        self.pending_bytes = 0
        self.closing = False
        self.thread = threading.Thread(target=self._run, name="data-store-writer", daemon=True)
        self.thread.start()

    def submit(self, values: tuple[Any, ...], size: int) -> concurrent.futures.Future | None:
        with self.lock:
            if self.closing or self.pending_bytes + size > _WRITE_QUEUE_BYTES:
                return None
            future: concurrent.futures.Future = concurrent.futures.Future()
            try:
                self.jobs.put_nowait((values, size, future))
            except queue.Full:
                return None
            self.pending_bytes += size
            return future

    def _run(self) -> None:
        while True:
            try:
                values, size, future = self.jobs.get(timeout=0.1)
            except queue.Empty:
                with self.lock:
                    if self.closing:
                        return
                continue
            try:
                result = _record_values(values[0], values[1], values)
                if not future.done():
                    future.set_result(result)
            except Exception as exc:
                if not future.done():
                    future.set_exception(exc)
            finally:
                with self.lock:
                    self.pending_bytes -= size
                self.jobs.task_done()


async def async_record(
    kind: str, symbol: str, payload: Any, *, market: str = "", queue_timeout: float = 5.0,
) -> bool:
    """事件循环之外落库，await 后可立即读到；失败/队列过载返回 False。

    已入队的写入即使请求取消仍会提交。尚未入队的取消不会产生写入。
    容量含正在提交的 JSON；超过整个字节预算的单条直接降级，不能撑爆 API 内存。
    """
    global _WRITER
    values = _record_params(kind, symbol, payload, market)
    if values is None:
        return False
    # Python Unicode 最多每字符 4 bytes；用保守上界避免额外编码复制大 JSON。
    size = len(values[3]) * 4
    if size > _WRITE_QUEUE_BYTES:
        return False
    with _WRITER_LOCK:
        if _WRITES_CLOSED:
            return False
        if _WRITER is None:
            _WRITER = _DataWriter()
        writer = _WRITER
    deadline = time.monotonic() + max(0.0, queue_timeout)
    while True:
        future = writer.submit(values, size)
        if future is not None:
            # shield 阻止取消 wrap_future，worker 才能完成已接收的写入。
            return await asyncio.shield(asyncio.wrap_future(future))
        if writer.closing or time.monotonic() >= deadline:
            return False
        await asyncio.sleep(0.01)


def _shutdown_writer(timeout: float = 5.0) -> bool:
    global _WRITES_CLOSED, _WRITER
    with _WRITER_LOCK:
        _WRITES_CLOSED = True
        writer = _WRITER
        if writer is None:
            return True
        with writer.lock:
            writer.closing = True
    writer.thread.join(timeout=max(0.0, timeout))
    drained = not writer.thread.is_alive()
    if drained:
        with _WRITER_LOCK:
            if _WRITER is writer:
                _WRITER = None
    else:
        logger.warning("data-store writer drain timed out; %s jobs remain", writer.jobs.qsize())
    return drained


async def shutdown_data_store(timeout: float = 5.0) -> bool:
    """生命周期关闭：停止接收、尽力排空，join 在事件循环外且有超时。"""
    return await asyncio.to_thread(_shutdown_writer, timeout)


atexit.register(_shutdown_writer)


def latest(kind: str, symbol: str, *, max_age_seconds: Optional[float] = None) -> Optional[dict]:
    """最近一条 payload；给了 max_age_seconds 则只返回该窗口内的（读缓存用）。无/过期/失败 → None。"""
    sym = (symbol or "").upper().strip()
    if not sym:
        return None
    try:
        with closing(_connect()) as conn:
            row = conn.execute(
                "SELECT payload_json, recorded_ts FROM data_points "
                "WHERE kind=? AND symbol=? ORDER BY recorded_ts DESC, id DESC LIMIT 1",
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
        with closing(_connect()) as conn:
            rows = conn.execute(
                "SELECT payload_json, recorded_at FROM data_points "
                "WHERE kind=? AND symbol=? ORDER BY recorded_ts DESC, id DESC LIMIT ?",
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
        with closing(_connect()) as conn:
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
        with closing(_connect()) as conn:
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


def _prune_series(conn: sqlite3.Connection, kind: str, symbol: str, keep: int) -> int:
    cur = conn.execute(
        "DELETE FROM data_points WHERE kind=? AND symbol=? AND id IN ("
        "SELECT id FROM data_points WHERE kind=? AND symbol=? "
        "ORDER BY recorded_ts DESC, id DESC LIMIT -1 OFFSET ?)",
        (kind, symbol, kind, symbol, keep),
    )
    return cur.rowcount or 0


def prune(keep_per_series: Optional[int] = None) -> int:
    """每个 (kind, symbol) 只保留最近 keep_per_series 条，删除更旧的。返回删除行数。失败 → 0。

    keep_per_series 缺省时运行时读模块级 KEEP_PER_SERIES（避免默认参数定义期绑定，便于配置/测试）。
    启动/离线维护专用。每个序列独立提交，避免全表窗口排序和长写锁；请求 record 不调用此函数。
    """
    keep = KEEP_PER_SERIES if keep_per_series is None else keep_per_series
    try:
        with closing(_connect()) as conn:
            series = conn.execute("SELECT DISTINCT kind, symbol FROM data_points").fetchall()
            deleted = 0
            for row in series:
                deleted += _prune_series(conn, row["kind"], row["symbol"], max(1, int(keep)))
                conn.commit()
            return deleted
    except Exception:
        return 0
