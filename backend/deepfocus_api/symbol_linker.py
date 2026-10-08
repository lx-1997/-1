"""消息→标的关联索引：站内所有消息（快讯/文章/研报/纪要）按标的的倒排索引。

三级打标来源（source 列）：
  provider — 消息源自带（realtime_messages.symbol 单值列）
  meta     — 收报 LLM 预提取（metadata_json.symbols，研报 wire 管道）
  alias    — 证券主数据简称/全称对标题+正文的词典匹配
入库时同步打标（link_message，毫秒级）；个股工作区按 symbol 查本索引（O(索引)，查询时绝不现场扫文）。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from . import securities_master

DB_PATH = Path(
    __import__("os").getenv(
        "DEEPFOCUS_REALTIME_MESSAGE_DB_PATH",
        str(Path(__file__).resolve().parents[1] / ".realtime_messages.sqlite3"),
    )
)

_ALIAS_SCAN_CHARS = 500  # 正文只扫前 500 字，控制长文打标成本


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15.0)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS message_symbols (
            message_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'alias',
            created_at TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (message_id, symbol)
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_message_symbols_symbol ON message_symbols(symbol, created_at)"
    )
    conn.commit()
    return conn


def _norm(symbol: str | None) -> str | None:
    clean = str(symbol or "").strip().upper()
    return clean or None


def link_message(
    message_id: str,
    symbol: str | None,
    metadata_json: str | None,
    title: str,
    content: str,
    created_at: str = "",
) -> int:
    """对单条消息打标并写入倒排（幂等：INSERT OR IGNORE）。返回本次新增关联数。"""
    if not message_id:
        return 0
    found: dict[str, str] = {}
    provider = _norm(symbol)
    if provider:
        found[provider] = "provider"
    try:
        meta = json.loads(metadata_json or "{}")
        for raw in (meta.get("symbols") or []):
            resolved = _norm(raw) if str(raw or "").strip().replace(".", "").isalnum() else securities_master.resolve_symbol(str(raw))
            if resolved and resolved not in found:
                found[resolved] = "meta"
    except Exception:  # noqa: BLE001 — metadata 损坏不阻断打标
        pass
    aliases = securities_master.alias_map()
    hay = f"{title or ''}\n{content or ''}"[:_ALIAS_SCAN_CHARS]
    if hay:
        for alias, sym in aliases.items():
            if sym in found:
                continue
            if alias in hay:
                found[sym] = "alias"
    if not found:
        return 0
    with _connect() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO message_symbols(message_id, symbol, source, created_at) VALUES(?,?,?,?)",
            [(message_id, sym, src, created_at or "") for sym, src in found.items()],
        )
        return len(found)


def backfill_all(batch: int = 500) -> int:
    """存量全量回填（一次性/主数据更新后可重跑，幂等）。"""
    total = 0
    with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT id, symbol, metadata_json, title, content, created_at FROM realtime_messages")
        while True:
            rows = cur.fetchmany(batch)
            if not rows:
                break
            for row in rows:
                total += link_message(
                    row["id"], row["symbol"], row["metadata_json"], row["title"], row["content"], row["created_at"]
                )
    return total


def messages_by_symbol(symbol: str, limit: int = 30) -> list[dict]:
    """按标的查聚合消息（新→旧），JOIN 消息主表返回完整记录。"""
    sym = _norm(symbol)
    if not sym:
        return []
    limit = max(1, min(int(limit or 30), 100))
    with sqlite3.connect(DB_PATH, timeout=15.0) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT m.id, m.title, m.content, m.source_id, m.source_name, m.source_type,
                      m.symbol, m.topic, m.severity, m.url, m.tags_json, m.metadata_json, m.created_at,
                      ms.source AS link_source
               FROM message_symbols ms JOIN realtime_messages m ON m.id = ms.message_id
               WHERE ms.symbol = ?
               ORDER BY m.created_at DESC LIMIT ?""",
            (sym, limit),
        ).fetchall()
    return [dict(r) for r in rows]
