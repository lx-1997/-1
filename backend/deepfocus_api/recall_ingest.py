"""把独立信息流接入统一的实时召回/FCM 管线。

DAO 事件已经会写入 ``realtime_messages``，但研报工作台和知识星球纪要原先
只通过各自的查询接口被前端/Android 轮询，离线时无法走同一套 FCM 订阅过滤。
本模块只负责「发现新条目 → 幂等落库 → 调用 create_realtime_message」，不改变
原有查询接口，也不保存额外的付费社群全文。

首次成功抓取只建立基线，避免部署/重启时把历史列表全部刷成通知；可通过
``DEEPFOCUS_RECALL_INGEST_BACKFILL`` 显式指定首次要补发的最新条数。
"""

from __future__ import annotations

import asyncio
import os
import re
import sqlite3
from . import db
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from .schemas import RealtimeMessageCreateRequest


DB_PATH = Path(
    os.getenv(
        "DEEPFOCUS_RECALL_INGEST_STATE_PATH",
        str(Path(__file__).resolve().parents[1] / ".recall_ingest.sqlite3"),
    )
)
MAX_CONTENT_CHARS = 1200


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


DEFAULT_INTERVAL_SECONDS = _env_float("DEEPFOCUS_RECALL_INGEST_SECONDS", 75.0)
DEFAULT_INITIAL_DELAY_SECONDS = _env_float("DEEPFOCUS_RECALL_INGEST_INITIAL_DELAY_SECONDS", 15.0)
MAX_SEEN_PER_SOURCE = max(1000, _env_int("DEEPFOCUS_RECALL_INGEST_MAX_SEEN", 20000))


def _connect() -> sqlite3.Connection:
    conn = db.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_recall_ingest_db() -> None:
    """创建幂等状态表；失败由调用方捕获，不影响主服务启动。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS recall_ingest_state (
                source TEXT PRIMARY KEY,
                initialized INTEGER NOT NULL DEFAULT 0,
                last_success_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS recall_ingest_seen (
                source TEXT NOT NULL,
                external_id TEXT NOT NULL,
                message_id TEXT,
                first_seen_at TEXT NOT NULL,
                PRIMARY KEY (source, external_id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_recall_ingest_seen_source ON recall_ingest_seen(source)")
        conn.commit()


def _clean_text(value: Any, limit: int = MAX_CONTENT_CHARS) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _clean_tags(values: Iterable[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        tag = _clean_text(value, 40).strip("#")
        if not tag or tag.casefold() in seen:
            continue
        seen.add(tag.casefold())
        out.append(tag)
    return out[:10]


def _severity_for_text(text: str, *, digested: bool = False) -> str:
    lowered = text.casefold()
    if any(word in lowered for word in ("暴跌", "崩盘", "退市", "破产", "黑天鹅", "暴雷")):
        return "critical"
    if digested or any(word in lowered for word in ("风险", "下调", "减持", "利空", "警示", "调查")):
        return "warning"
    if any(word in lowered for word in ("上调", "增持", "利好", "超预期", "获批", "增长")):
        return "success"
    return "info"


def _app_url(value: Any = "") -> str:
    raw = str(value or "").strip()
    if raw.lower().startswith(("http://", "https://")):
        return raw[:1200]
    base = os.getenv("DEEPFOCUS_APP_BASE_URL", "https://daocaijing.com").strip().rstrip("/")
    if raw.startswith("/"):
        return f"{base}{raw}"[:1200]
    return base[:1200]


def normalize_research_item(item: dict[str, Any]) -> Optional[tuple[str, RealtimeMessageCreateRequest]]:
    """把研究报告元数据归一化成一条可召回消息（不透出全文）。"""
    if not isinstance(item, dict):
        return None
    external_id = _clean_text(item.get("file_id") or item.get("id"), 160)
    title = _clean_text(item.get("title"), 240)
    if not external_id or not title:
        return None
    org = _clean_text(item.get("org") or "海外投行", 80)
    date = _clean_text(item.get("created_at") or item.get("date"), 40)
    hashtag = _clean_text(item.get("hashtag"), 60)
    instruments = item.get("instruments") if isinstance(item.get("instruments"), list) else []
    symbols = _clean_tags(instruments)
    content_parts = [org]
    if date:
        content_parts.append(date[:10])
    if hashtag:
        content_parts.append(hashtag.strip("#"))
    if symbols:
        content_parts.append("提及 " + "、".join(symbols[:5]))
    content = " · ".join(content_parts)
    tags = _clean_tags(["研报", hashtag, *instruments])
    request = RealtimeMessageCreateRequest(
        title=title,
        content=content,
        source_id=f"research-wire:{external_id}",
        source_name=org,
        source_type="research-wire",
        symbol=symbols[0] if symbols else None,
        topic="研报",
        severity=_severity_for_text(f"{title} {content}"),
        url=_app_url(item.get("preview_url")),
        tags=tags,
        metadata={
            "ingest_source": "research_wire",
            "external_id": external_id,
            "native_dedupe_id": f"report:{external_id}",
            "published_at": date,
            "symbols": symbols,
        },
    )
    return external_id, request


def normalize_institution_note(item: dict[str, Any]) -> Optional[tuple[str, RealtimeMessageCreateRequest]]:
    """把知识星球普通帖子归一化成机构纪要通知；正文只取短摘要。"""
    if not isinstance(item, dict):
        return None
    external_id = _clean_text(item.get("id"), 160)
    title = _clean_text(item.get("title") or "机构纪要更新", 240)
    body = _clean_text(item.get("text"), MAX_CONTENT_CHARS)
    if not external_id or (not body and title == "机构纪要更新"):
        return None
    raw_tags = item.get("tags") if isinstance(item.get("tags"), list) else []
    tags = _clean_tags(["机构纪要", *raw_tags])
    created = _clean_text(item.get("create_time") or item.get("date"), 40)
    digested = bool(item.get("digested"))
    request = RealtimeMessageCreateRequest(
        title=title,
        content=body or "机构纪要有新更新，点击查看详情。",
        source_id=f"zsxq:{external_id}",
        source_name="机构纪要",
        source_type="institution-note",
        topic="机构纪要",
        severity=_severity_for_text(f"{title} {body}", digested=digested),
        url=_app_url(),
        tags=tags,
        metadata={
            "ingest_source": "zsxq_stream",
            "external_id": external_id,
            "native_dedupe_id": f"zsxq:{external_id}",
            "published_at": created,
            "digested": digested,
            "symbols": [],
        },
    )
    return external_id, request


def ingest_items(
    source: str,
    items: Iterable[dict[str, Any]],
    normalizer: Callable[[dict[str, Any]], Optional[tuple[str, RealtimeMessageCreateRequest]]],
    *,
    create_fn: Optional[Callable[[RealtimeMessageCreateRequest], Any]] = None,
    backfill: Optional[int] = None,
) -> dict[str, int]:
    """幂等摄入一批条目，返回 ``seen/new/notified/baseline`` 统计。"""
    init_recall_ingest_db()
    parsed: list[tuple[str, RealtimeMessageCreateRequest]] = []
    for item in items:
        normalized = normalizer(item)
        if normalized:
            parsed.append(normalized)
    # 上游通常按新到旧返回；即使未排序，状态/去重语义仍然成立。
    with _connect() as conn:
        state = conn.execute("SELECT initialized FROM recall_ingest_state WHERE source = ?", (source,)).fetchone()
        initialized = bool(state and state["initialized"])
        seen_rows = conn.execute("SELECT external_id FROM recall_ingest_seen WHERE source = ?", (source,)).fetchall()
        seen = {str(row["external_id"]) for row in seen_rows}
        if not initialized:
            limit = max(0, _env_int("DEEPFOCUS_RECALL_INGEST_BACKFILL", 0) if backfill is None else int(backfill))
            notified = 0
            for index, (external_id, request) in enumerate(parsed):
                message_id = None
                if index < limit:
                    fn = create_fn or _default_create
                    message = fn(request)
                    message_id = getattr(message, "id", None)
                    notified += int(message is not None)
                conn.execute(
                    "INSERT OR IGNORE INTO recall_ingest_seen(source, external_id, message_id, first_seen_at) VALUES (?, ?, ?, ?)",
                    (source, external_id, message_id, _now()),
                )
            conn.execute(
                "INSERT INTO recall_ingest_state(source, initialized, last_success_at) VALUES (?, 1, ?) "
                "ON CONFLICT(source) DO UPDATE SET initialized=1, last_success_at=excluded.last_success_at",
                (source, _now()),
            )
            _prune_seen(conn, source)
            conn.commit()
            return {"seen": len(parsed), "new": 0, "notified": notified, "baseline": len(parsed)}

        fn = create_fn or _default_create
        fresh: list[tuple[str, RealtimeMessageCreateRequest]] = [(eid, req) for eid, req in parsed if eid not in seen]
        notified = 0
        for external_id, request in fresh:
            message = fn(request)
            message_id = getattr(message, "id", None)
            conn.execute(
                "INSERT OR IGNORE INTO recall_ingest_seen(source, external_id, message_id, first_seen_at) VALUES (?, ?, ?, ?)",
                (source, external_id, message_id, _now()),
            )
            notified += int(message is not None)
        conn.execute("UPDATE recall_ingest_state SET last_success_at = ? WHERE source = ?", (_now(), source))
        _prune_seen(conn, source)
        conn.commit()
    return {"seen": len(parsed), "new": len(fresh), "notified": notified, "baseline": 0}


def _default_create(request: RealtimeMessageCreateRequest) -> Any:
    from .realtime_messages import create_realtime_message

    return create_realtime_message(request)


def _prune_seen(conn: sqlite3.Connection, source: str) -> None:
    """限制幂等表大小，长期运行不会因消息源增长而无限占盘。"""
    conn.execute(
        "DELETE FROM recall_ingest_seen WHERE source = ? AND rowid NOT IN "
        "(SELECT rowid FROM recall_ingest_seen WHERE source = ? ORDER BY first_seen_at DESC LIMIT ?)",
        (source, source, MAX_SEEN_PER_SOURCE),
    )


async def run_recall_ingest() -> None:
    """后台同步研报/机构纪要；单源失败不影响其它源和主服务。"""
    if os.getenv("DEEPFOCUS_RECALL_INGEST_ENABLED", "1").strip().lower() not in {"1", "true", "yes", "on"}:
        print("[recall-ingest] 未启用（DEEPFOCUS_RECALL_INGEST_ENABLED=0）")
        return
    interval = max(20.0, _env_float("DEEPFOCUS_RECALL_INGEST_SECONDS", DEFAULT_INTERVAL_SECONDS))
    delay = max(0.0, _env_float("DEEPFOCUS_RECALL_INGEST_INITIAL_DELAY_SECONDS", DEFAULT_INITIAL_DELAY_SECONDS))
    await asyncio.sleep(delay)
    print(f"[recall-ingest] 启动：研报/机构纪要每 {interval:.0f}s 接入统一召回")
    while True:
        try:
            from .research_wire import fetch_research_wire_online

            data = await fetch_research_wire_online(limit=120, use_cache=False)
            stats = ingest_items("research_wire", data.get("items") or [], normalize_research_item)
            if stats["new"]:
                print(f"[recall-ingest] 研报新增 {stats['new']}，已通知 {stats['notified']}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 单源故障不阻塞其它后台任务
            print(f"[recall-ingest] 研报同步失败：{type(exc).__name__}")
        try:
            from .zsxq_stream import fetch_stream

            data = await fetch_stream(limit=20, use_cache=False)
            stats = ingest_items("zsxq_stream", data.get("items") or [], normalize_institution_note)
            if stats["new"]:
                print(f"[recall-ingest] 机构纪要新增 {stats['new']}，已通知 {stats['notified']}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 登录态/工作台故障可自愈重试
            print(f"[recall-ingest] 机构纪要同步失败：{type(exc).__name__}")
        await asyncio.sleep(interval)
