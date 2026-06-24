from __future__ import annotations

import json
import os
import sqlite3
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

DB_PATH = Path(
    os.getenv(
        "DEEPFOCUS_RESEARCH_ARCHIVE_DB_PATH",
        str(Path(__file__).resolve().parents[1] / ".research_archive.sqlite3"),
    )
)
_MAX_ARCHIVE = int(os.getenv("DEEPFOCUS_RESEARCH_ARCHIVE_MAX", "8000"))  # 容量上限，超量删最老


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
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


def query(limit: int = 400, query_text: str = "", before: str = "") -> list[dict[str, Any]]:
    """按 date 倒序返回归档研报（原始 dict）。query_text→标题模糊匹配；before(YYYY-MM-DD)→只取更早的(翻页)。失败→[]。"""
    try:
        init_archive()
        clauses, params = [], []
        q = (query_text or "").strip()
        if q:
            clauses.append("title LIKE ?"); params.append(f"%{q}%")
        bf = (before or "").strip()
        if bf:
            clauses.append("date < ?"); params.append(bf)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(max(1, min(int(limit or 400), 2000)))
        with _connect() as conn:
            rows = conn.execute(
                f"SELECT payload FROM research_archive {where} ORDER BY date DESC, archived_at DESC LIMIT ?", params
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
