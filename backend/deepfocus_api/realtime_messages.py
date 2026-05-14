from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from fastapi import Request

from .schemas import (
    DataSourceItemRecord,
    RealtimeMessageCreateRequest,
    RealtimeMessageRecord,
)


DB_PATH = Path(
    os.getenv(
        "DEEPFOCUS_REALTIME_MESSAGE_DB_PATH",
        str(Path(__file__).resolve().parents[1] / ".realtime_messages.sqlite3"),
    )
)

MAX_MESSAGES = int(os.getenv("DEEPFOCUS_REALTIME_MAX_MESSAGES", "500"))
_subscribers: set[asyncio.Queue[RealtimeMessageRecord]] = set()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_realtime_message_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS realtime_messages (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                source_id TEXT,
                source_name TEXT,
                source_type TEXT,
                symbol TEXT,
                topic TEXT NOT NULL,
                severity TEXT NOT NULL,
                url TEXT,
                tags_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_realtime_messages_created ON realtime_messages(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_realtime_messages_symbol ON realtime_messages(symbol)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_realtime_messages_topic ON realtime_messages(topic)")
        conn.commit()


def create_realtime_message(request: RealtimeMessageCreateRequest) -> RealtimeMessageRecord:
    init_realtime_message_db()
    timestamp = now_iso()
    message_id = str(uuid.uuid4())
    tags = _normalize_tags(request.tags)
    record = {
        "id": message_id,
        "title": request.title.strip()[:240] or "实时消息",
        "content": request.content.strip()[:8000],
        "source_id": _clean_optional(request.source_id),
        "source_name": _clean_optional(request.source_name),
        "source_type": _clean_optional(request.source_type),
        "symbol": _clean_optional(request.symbol.upper() if request.symbol else None),
        "topic": request.topic.strip()[:80] or "data-source",
        "severity": request.severity,
        "url": _clean_optional(request.url),
        "tags_json": json.dumps(tags, ensure_ascii=False),
        "metadata_json": json.dumps(request.metadata or {}, ensure_ascii=False),
        "created_at": timestamp,
    }
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO realtime_messages (
                id, title, content, source_id, source_name, source_type, symbol, topic,
                severity, url, tags_json, metadata_json, created_at
            ) VALUES (
                :id, :title, :content, :source_id, :source_name, :source_type, :symbol,
                :topic, :severity, :url, :tags_json, :metadata_json, :created_at
            )
            """,
            record,
        )
        _trim_old_messages(conn)
        conn.commit()

    message = _row_to_message(record)
    _broadcast_message(message)
    return message


def list_realtime_messages(
    *,
    symbol: Optional[str] = None,
    topic: Optional[str] = None,
    severity: Optional[str] = None,
    limit: int = 80,
) -> list[RealtimeMessageRecord]:
    init_realtime_message_db()
    clauses: list[str] = []
    values: list[Any] = []
    if symbol:
        clauses.append("symbol = ?")
        values.append(symbol.strip().upper())
    if topic:
        clauses.append("topic = ?")
        values.append(topic.strip())
    if severity:
        clauses.append("severity = ?")
        values.append(severity.strip())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    values.append(max(1, min(limit, 200)))
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM realtime_messages
            {where}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            values,
        ).fetchall()
    return [_row_to_message(dict(row)) for row in rows]


def publish_data_source_items(
    items: list[DataSourceItemRecord],
    *,
    topic: str,
    severity: str = "info",
) -> list[RealtimeMessageRecord]:
    messages: list[RealtimeMessageRecord] = []
    for item in items:
        messages.append(
            create_realtime_message(
                RealtimeMessageCreateRequest(
                    title=f"{item.source_name}：{item.title}",
                    content=item.text_preview or item.text[:360],
                    source_id=item.source_id,
                    source_name=item.source_name,
                    source_type=item.source_type,
                    symbol=item.symbol,
                    topic=topic,
                    severity=severity,  # type: ignore[arg-type]
                    url=item.url,
                    tags=item.tags,
                    metadata={
                        "data_item_id": item.id,
                        "credibility_score": item.credibility_score,
                        "collected_at": item.collected_at,
                        "source_type": item.source_type,
                    },
                )
            )
        )
    return messages


async def realtime_message_event_stream(request: Request) -> AsyncIterator[str]:
    queue: asyncio.Queue[RealtimeMessageRecord] = asyncio.Queue(maxsize=100)
    _subscribers.add(queue)
    try:
        yield _sse_event("connected", {"connected": True, "created_at": now_iso()})
        while True:
            if await request.is_disconnected():
                break
            try:
                message = await asyncio.wait_for(queue.get(), timeout=20)
            except asyncio.TimeoutError:
                yield _sse_event("heartbeat", {"created_at": now_iso()})
                continue
            yield _sse_event("realtime-message", message.model_dump(mode="json"))
    finally:
        _subscribers.discard(queue)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _broadcast_message(message: RealtimeMessageRecord) -> None:
    stale: list[asyncio.Queue[RealtimeMessageRecord]] = []
    for queue in _subscribers:
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            stale.append(queue)
    for queue in stale:
        _subscribers.discard(queue)


def _sse_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _row_to_message(row: dict[str, Any]) -> RealtimeMessageRecord:
    return RealtimeMessageRecord(
        id=row["id"],
        title=row["title"],
        content=row.get("content") or "",
        source_id=row.get("source_id"),
        source_name=row.get("source_name"),
        source_type=row.get("source_type"),
        symbol=row.get("symbol"),
        topic=row.get("topic") or "data-source",
        severity=row.get("severity") or "info",
        url=row.get("url"),
        tags=json.loads(row.get("tags_json") or "[]"),
        metadata=json.loads(row.get("metadata_json") or "{}"),
        created_at=row["created_at"],
    )


def _trim_old_messages(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        DELETE FROM realtime_messages
        WHERE id NOT IN (
            SELECT id FROM realtime_messages
            ORDER BY created_at DESC
            LIMIT ?
        )
        """,
        (MAX_MESSAGES,),
    )


def _clean_optional(value: Optional[str]) -> Optional[str]:
    clean = str(value).strip() if value is not None else ""
    return clean or None


def _normalize_tags(tags: list[str]) -> list[str]:
    normalized: list[str] = []
    for tag in tags:
        clean = str(tag).strip()
        if clean and clean not in normalized:
            normalized.append(clean)
    return normalized[:20]
