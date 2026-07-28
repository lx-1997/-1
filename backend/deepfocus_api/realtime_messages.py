from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional

from fastapi import Request

from .shared_utils import utc_now_iso
from .news_filter import discard_reason, scrub
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

MAX_MESSAGES = int(os.getenv("DEEPFOCUS_REALTIME_MAX_MESSAGES", "20000"))
MAX_SUBSCRIBERS = int(os.getenv("DEEPFOCUS_REALTIME_MAX_SUBSCRIBERS", "300"))
# SSE 最大存活时长(秒)：长连接到点主动收尾，让浏览器原生 EventSource 重连。
# 作用=回收「后台/已离开标签页长期占用的连接槽」，防止 200 总量上限被僵尸连接永久占满。
# 活跃用户重连无感(几秒);移动端被挂起的后台页则借机彻底释放槽位。0/负数=不限(回退旧行为)。
STREAM_MAX_LIFETIME_SECONDS = int(os.getenv("DEEPFOCUS_REALTIME_STREAM_MAX_LIFETIME", "1500"))
_subscribers: set[asyncio.Queue[RealtimeMessageRecord]] = set()
# 新消息落库+广播后的同步钩子（如离线召回扇出）。解耦：本模块不依赖召回实现。
_post_hooks: list[Callable[[RealtimeMessageRecord], None]] = []


def subscriber_count() -> int:
    """当前在线的实时快讯 SSE 长连接数（容量监控用：这正是会占满 nginx 连接的那批）。"""
    return len(_subscribers)


def register_post_message_hook(hook: Callable[[RealtimeMessageRecord], None]) -> None:
    """注册一个「新消息后置钩子」。重复注册同一个 hook 不会叠加。"""
    if hook not in _post_hooks:
        _post_hooks.append(hook)


def _run_post_hooks(message: RealtimeMessageRecord) -> None:
    """运行新消息后置钩子（如离线召回扇出）。

    ⭐ 钩子里的召回投递是【同步阻塞 IO】(smtplib SMTP timeout=15 / httpx.post 微信桥 timeout=15)。
    本函数被 create_realtime_message 在事件循环线程上调用（async handler / dao_bridge 摄入循环），
    若内联执行，一条快讯命中 N 个订阅就会把单 worker 的事件循环串行卡住 ~15s×N，冻结所有并发
    请求(SSE/行情/速判)。故：检测到运行中的事件循环时，把每个钩子 fire-and-forget 挪到 worker
    线程（dispatch_recall 全程同步、无循环亲和性，线程内执行安全）；无运行循环时（同步调用方/
    单测）内联执行，行为与原先完全一致。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    for hook in list(_post_hooks):
        try:
            if loop is not None:
                loop.run_in_executor(None, hook, message)  # 挪出事件循环，绝不阻塞主链路
            else:
                hook(message)
        except Exception:  # 调度/执行失败绝不影响消息创建/广播主链路
            pass


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


def create_realtime_message(request: RealtimeMessageCreateRequest) -> Optional[RealtimeMessageRecord]:
    # 内容过滤：①引流广告 ②垃圾/非新闻(反爬提示、域名喊话页) ③可见处出现竞品词(futou/斧头) → 整条拦下(不入库/不广播/不召回)；
    # 只在结构化 url 里夹带竞品域名的正经研报不拦——由下方 scrub 抹域名后照常保留。
    reason = discard_reason(request.title or "", request.content or "")
    if reason:
        print(f"[news-filter] 拦截入库 {reason} | {(request.title or '')[:60]}")
        return None
    _t, _c, _u, _changed = scrub(request.title or "", request.content or "", request.url or "")
    if _changed:
        print(f"[news-filter] 抹竞品域名后保留 | {(_t or '')[:60]}")
    init_realtime_message_db()
    timestamp = utc_now_iso()
    message_id = str(uuid.uuid4())
    tags = _normalize_tags(request.tags)
    record = {
        "id": message_id,
        "title": _t.strip()[:240] or "实时消息",
        "content": _c.strip()[:8000],
        "source_id": _clean_optional(request.source_id),
        "source_name": _clean_optional(request.source_name),
        "source_type": _clean_optional(request.source_type),
        "symbol": _clean_optional(request.symbol.upper() if request.symbol else None),
        "topic": request.topic.strip()[:80] or "data-source",
        "severity": request.severity,
        "url": _clean_optional(_u),
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
    # 所有快讯/文章/研报在入库时同步生成类型化多标签和实体关系。
    # 标注失败不得阻塞实时消息主链路；后续 content-map 读取时还会自动补标。
    try:
        from .content_ontology import annotation_from_message
        annotation_from_message(message, persist=True)
    except Exception:  # noqa: BLE001
        pass
    _broadcast_message(message)
    _run_post_hooks(message)
    return message


def update_realtime_message_fields(
    message_id: str,
    *,
    severity: Optional[str] = None,
    metadata_patch: Optional[dict[str, Any]] = None,
) -> Optional[RealtimeMessageRecord]:
    """更新已发布消息的 severity / metadata 并重新广播同 id 消息（前端按 id 原地替换）。

    用于「秒发 + 回填」模式：消息先按规则秒推，AI 情绪等慢结果出来后回填更新。
    找不到该消息返回 None；不触发 post hooks（避免召回等副作用重复执行）。"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM realtime_messages WHERE id = ?", (message_id,)
        ).fetchone()
        if row is None:
            return None
        record = dict(row)
        if severity:
            record["severity"] = severity
        if metadata_patch:
            try:
                merged = json.loads(record.get("metadata_json") or "{}")
            except json.JSONDecodeError:
                merged = {}
            merged.update(metadata_patch)
            record["metadata_json"] = json.dumps(merged, ensure_ascii=False)
        conn.execute(
            "UPDATE realtime_messages SET severity = :severity, metadata_json = :metadata_json WHERE id = :id",
            {"severity": record["severity"], "metadata_json": record["metadata_json"], "id": message_id},
        )
        conn.commit()
    message = _row_to_message(record)
    _broadcast_message(message)
    return message


def list_realtime_messages(
    *,
    symbol: Optional[str] = None,
    topic: Optional[str] = None,
    severity: Optional[str] = None,
    since: Optional[str] = None,
    before: Optional[str] = None,
    q: Optional[str] = None,
    anyq: Optional[str] = None,
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
    if q and q.strip():  # 关键词检索全量历史：空格分词，每词命中标题或正文（AND）
        for term in q.strip().split()[:6]:
            like = f"%{term}%"
            clauses.append("(title LIKE ? OR content LIKE ?)")
            values.extend([like, like])
    if anyq and anyq.strip():  # 多别名检索：任一别名命中标题或正文即可（OR）——选股(几条)/自选 tab(全自选股别名)共用
        # 上限放宽到 48：自选 tab 会把所有自选股别名一起发来；服务端做召回，客户端再精确收敛（综述按标题等）
        aliases = [a.strip() for a in anyq.split(",") if a.strip()][:48]
        if aliases:
            ors = []
            for a in aliases:
                like = f"%{a}%"
                ors.append("(title LIKE ? OR content LIKE ?)")
                values.extend([like, like])
            clauses.append("(" + " OR ".join(ors) + ")")
    if since and since.strip():  # 增量：只取比本地更新的（轮询用，响应极小）
        clauses.append("created_at > ?")
        values.append(since.strip())
    if before and before.strip():  # 翻页：只取比游标更旧的（向历史回翻）
        clauses.append("created_at < ?")
        values.append(before.strip())
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


def get_realtime_message(message_id: str) -> Optional[RealtimeMessageRecord]:
    """按 id 取单条消息（文章公开落地页用）。找不到/失败 → None。"""
    mid = (message_id or "").strip()
    if not mid:
        return None
    init_realtime_message_db()
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT * FROM realtime_messages WHERE id = ?", (mid,)
            ).fetchone()
    except Exception:
        return None
    return _row_to_message(dict(row)) if row else None


def publish_data_source_items(
    items: list[DataSourceItemRecord],
    *,
    topic: str,
    severity: str = "info",
) -> list[RealtimeMessageRecord]:
    messages: list[RealtimeMessageRecord] = []
    for item in items:
        msg = create_realtime_message(
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
        if msg is not None:  # 命中内容过滤 → 跳过
            messages.append(msg)
    return messages


async def realtime_message_event_stream(request: Request) -> AsyncIterator[str]:
    if len(_subscribers) >= MAX_SUBSCRIBERS:
        yield _sse_event("error", {"message": "当前在线人数已达上限，请稍后重试。"})
        return
    queue: asyncio.Queue[RealtimeMessageRecord] = asyncio.Queue(maxsize=100)
    _subscribers.add(queue)
    started = asyncio.get_event_loop().time()  # 单调时钟：用于最大存活时长回收，无需新增 import
    try:
        yield _sse_event("connected", {"connected": True, "created_at": utc_now_iso()})
        while True:
            if await request.is_disconnected():
                break
            # 到达最大存活时长 → 主动收尾,客户端原生重连;回收后台/僵尸标签页长期占用的连接槽。
            if STREAM_MAX_LIFETIME_SECONDS > 0 and (asyncio.get_event_loop().time() - started) >= STREAM_MAX_LIFETIME_SECONDS:
                yield _sse_event("reconnect", {"reason": "max-lifetime", "created_at": utc_now_iso()})
                break
            try:
                message = await asyncio.wait_for(queue.get(), timeout=15)
            except asyncio.TimeoutError:
                yield _sse_event("heartbeat", {"created_at": utc_now_iso()})
                continue
            yield _sse_event("realtime-message", message.model_dump(mode="json"))
    finally:
        _subscribers.discard(queue)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=8)
    conn.row_factory = sqlite3.Row
    try:  # WAL：并发读写不互锁（实时流持续写入 + 多用户并发读）
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=8000")
        conn.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.Error:
        pass
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
    # 新快讯 → 立即唤醒微信推送循环（事件驱动，近实时；无渠道则静默）
    if getattr(message, "topic", "") == "快讯":
        try:
            from . import weixin_channel
            weixin_channel.notify_new_flash()
        except Exception:  # noqa: BLE001
            pass


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
