from __future__ import annotations

import json
import os
import sqlite3
from . import db
from pathlib import Path
from typing import Any, Optional

from .shared_utils import utc_now_iso

"""研报持久归档：把每次在线拉到的「海外投行研报」累积落库，让老研报不再随知识星球
最新窗口滑出而消失——研报面板从「最新一周流」变成「可往回翻的历史档」。

设计：只存原始条目 dict（id/title/org/date/file_id 等），不存富化字段（instruments/market/
preview_url 在出口处按 file_id 实时富化，保持与在线路径一致、AI 缓存随时更新）。
幂等 upsert（按 id），按 date 倒序取，支持 before 翻页与关键词过滤。容量上限防无界增长。
注意：只能从上线时刻起向前累积（在线源只给最新窗口，无法回填更早历史）。
"""

DB_PATH = db.data_path('.research_archive.sqlite3', 'DEEPFOCUS_RESEARCH_ARCHIVE_DB_PATH')
_MAX_ARCHIVE = int(os.getenv("DEEPFOCUS_RESEARCH_ARCHIVE_MAX", "8000"))  # 容量上限，超量删最老


def _connect() -> sqlite3.Connection:
    conn = db.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_archive() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS research_archive (
                id TEXT PRIMARY KEY,
                date TEXT,
                title TEXT,
                payload TEXT NOT NULL,
                archived_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_research_archive_date ON research_archive(date DESC)")
        conn.commit()


def upsert(items: list[dict[str, Any]]) -> int:
    """把研报条目（原始 dict，需含 id）幂等写入归档。已存在则更新（标题/日期可能修正）。绝不抛出。"""
    rows = [it for it in (items or []) if str((it or {}).get("id") or "").strip()]
    if not rows:
        return 0
    try:
        init_archive()
        now = utc_now_iso()
        with _connect() as conn:
            conn.executemany(
                "INSERT INTO research_archive (id, date, title, payload, archived_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET date=excluded.date, title=excluded.title, payload=excluded.payload",
                [
                    (str(it["id"]).strip(), str(it.get("date") or ""), str(it.get("title") or ""),
                     json.dumps(it, ensure_ascii=False), now)
                    for it in rows
                ],
            )
            # 容量上限：保留最新 _MAX_ARCHIVE 条（按 date 倒序），其余删除
            conn.execute(
                "DELETE FROM research_archive WHERE id NOT IN "
                "(SELECT id FROM research_archive ORDER BY date DESC, archived_at DESC LIMIT ?)",
                (_MAX_ARCHIVE,),
            )
            conn.commit()
        return len(rows)
    except Exception:  # noqa: BLE001 — 归档失败绝不影响研报主链路
        return 0


def query(limit: int = 400, query_text: str = "", before: str = "", before_id: str = "") -> list[dict[str, Any]]:
    """按 date/id 倒序返回归档研报；before+before_id 组成稳定游标，避免同一天被截断后漏项。"""
    try:
        init_archive()
        clauses, params = [], []
        q = (query_text or "").strip()
        if q:
            clauses.append("title LIKE ?"); params.append(f"%{q}%")
        bf = (before or "").strip()
        bid = (before_id or "").strip()
        if bf:
            if bid:
                # 分页不能只用 date < before：一页若在某天中间截断，下一页会跳过该天剩余条目。
                clauses.append("(date < ? OR (date = ? AND id < ?))")
                params.extend([bf, bf, bid])
            else:
                clauses.append("date < ?"); params.append(bf)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(max(1, min(int(limit or 400), 2000)))
        with _connect() as conn:
            rows = conn.execute(
                f"SELECT payload FROM research_archive {where} ORDER BY date DESC, id DESC LIMIT ?", params
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            try:
                out.append(json.loads(r["payload"]))
            except Exception:  # noqa: BLE001
                pass
        return out
    except Exception:  # noqa: BLE001
        return []


def count() -> int:
    try:
        init_archive()
        with _connect() as conn:
            return int(conn.execute("SELECT COUNT(*) AS n FROM research_archive").fetchone()["n"])
    except Exception:  # noqa: BLE001
        return 0


# This ledger is intentionally separate from archive.upsert. Ordinary API
# searches can archive historic reports and must never enqueue them as new.
# Only the scheduler's successful current-feed observations cross this boundary.
_PREWARM_INITIALIZED_KEY = "incremental_feed_baseline_v1"


def _ensure_prewarm_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS research_prewarm_seen (
            file_id TEXT PRIMARY KEY,
            status TEXT NOT NULL CHECK (status IN ('baseline', 'pending', 'done', 'excluded')),
            payload TEXT,
            first_seen_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_research_prewarm_pending "
        "ON research_prewarm_seen(status, first_seen_at, file_id)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS research_prewarm_state "
        "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )


def _prewarm_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        fid = str(raw.get("file_id") or "").strip()
        if not fid or fid in seen:
            continue
        seen.add(fid)
        out.append({**raw, "file_id": fid})
    return out


def observe_prewarm_feed(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Observe one successful scheduler feed, without enqueueing any backlog.

    The first nonempty valid observation atomically records the current archive and feed as
    baseline (zero pending). Only a never-seen file_id in a later feed becomes
    pending. Archive/API upserts do not touch this ledger, so searching historic
    reports neither schedules them nor hides future arrivals from the scheduler.
    Seen baseline/done/excluded ids survive archive pruning and process restarts.

    Returns initialized=True only for the call that established the baseline,
    with baseline/enqueued counts. Exceptions roll the entire observation back.
    """
    from contextlib import closing

    current = _prewarm_items(items)
    if not current:
        # Source adapters may soft-fail with items=[]; recovery must establish
        # the baseline instead of treating the old restored feed as arrivals.
        return {"initialized": False, "baseline": 0, "enqueued": 0}
    init_archive()
    now = utc_now_iso()
    with closing(_connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _ensure_prewarm_schema(conn)
            initialized = conn.execute(
                "SELECT value FROM research_prewarm_state WHERE key=?",
                (_PREWARM_INITIALIZED_KEY,),
            ).fetchone()
            if initialized is None:
                baseline_ids = {item["file_id"] for item in current}
                for row in conn.execute("SELECT payload FROM research_archive"):
                    try:
                        value = json.loads(row["payload"])
                    except (TypeError, ValueError):
                        continue
                    if isinstance(value, dict):
                        fid = str(value.get("file_id") or "").strip()
                        if fid:
                            baseline_ids.add(fid)
                conn.executemany(
                    "INSERT OR IGNORE INTO research_prewarm_seen "
                    "(file_id,status,payload,first_seen_at,updated_at) VALUES (?,'baseline',NULL,?,?)",
                    [(fid, now, now) for fid in baseline_ids],
                )
                conn.execute(
                    "INSERT INTO research_prewarm_state (key,value) VALUES (?,?)",
                    (_PREWARM_INITIALIZED_KEY, now),
                )
                result = {"initialized": True, "baseline": len(baseline_ids), "enqueued": 0}
            else:
                enqueued = 0
                for item in current:
                    fid = item["file_id"]
                    serialized = json.dumps(item, ensure_ascii=False)
                    inserted = conn.execute(
                        "INSERT OR IGNORE INTO research_prewarm_seen "
                        "(file_id,status,payload,first_seen_at,updated_at) VALUES (?,'pending',?,?,?)",
                        (fid, serialized, now, now),
                    )
                    enqueued += int(inserted.rowcount > 0)
                    if not inserted.rowcount:
                        # A pending report can receive a corrected title/name.
                        # Baseline and terminal rows never become pending again.
                        conn.execute(
                            "UPDATE research_prewarm_seen SET payload=?,updated_at=? "
                            "WHERE file_id=? AND status='pending'",
                            (serialized, now, fid),
                        )
                result = {"initialized": False, "baseline": 0, "enqueued": enqueued}
            conn.commit()
            return result
        except BaseException:
            conn.rollback()
            raise


def list_prewarm_pending(limit: Optional[int] = None) -> list[dict[str, Any]]:
    """Return durable pending metadata; default includes the complete queue.

    Creating empty queue tables does not initialize the feed baseline. A failed
    first fetch therefore leaves startup undecided until a successful snapshot.
    """
    from contextlib import closing

    init_archive()
    with closing(_connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _ensure_prewarm_schema(conn)
            sql = (
                "SELECT payload FROM research_prewarm_seen WHERE status='pending' "
                "ORDER BY first_seen_at, file_id"
            )
            params: tuple = ()
            if limit is not None:
                sql += " LIMIT ?"
                params = (max(0, int(limit)),)
            rows = conn.execute(sql, params).fetchall()
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    result: list[dict[str, Any]] = []
    for row in rows:
        try:
            item = json.loads(row["payload"])
        except (TypeError, ValueError):
            continue
        if isinstance(item, dict):
            result.append(item)
    return result


def mark_prewarm_done(file_id: str, status: str = "done") -> bool:
    """Finish a pending report once; keep its id permanently to prevent replay."""
    from contextlib import closing

    if status not in {"done", "excluded", "failed"}:
        raise ValueError("prewarm completion status must be done, excluded or failed")
    fid = str(file_id or "").strip()
    if not fid:
        return False
    init_archive()
    with closing(_connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _ensure_prewarm_schema(conn)
            changed = conn.execute(
                "UPDATE research_prewarm_seen SET status=?,payload=NULL,updated_at=? "
                "WHERE file_id=? AND status='pending'",
                (status, utc_now_iso(), fid),
            ).rowcount
            conn.commit()
            return bool(changed)
        except BaseException:
            conn.rollback()
            raise
