from __future__ import annotations

import asyncio
import html
import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qsl, quote, urljoin, urlparse

import httpx
from fastapi import HTTPException, status

from .schemas import (
    DataSourceCreateRequest,
    DataSourceItemRecord,
    DataSourceItemUpdateRequest,
    DataSourceKeywordCrawlRequest,
    DataSourceModuleRefCreateRequest,
    DataSourceModuleRefRecord,
    DataSourceRecord,
    DataSourceSyncRequest,
    DataSourceTagRecord,
)


DB_PATH = Path(
    os.getenv(
        "DEEPFOCUS_DATA_SOURCE_DB_PATH",
        str(Path(__file__).resolve().parents[1] / ".data_sources.sqlite3"),
    )
)

MAX_STORED_TEXT_CHARS = int(os.getenv("DEEPFOCUS_DATA_SOURCE_MAX_CHARS", "60000"))
AUTO_SYNC_LIMIT = int(os.getenv("DEEPFOCUS_DATA_SOURCE_AUTO_SYNC_LIMIT", "4"))
HTTP_TIMEOUT_SECONDS = float(os.getenv("DEEPFOCUS_DATA_SOURCE_HTTP_TIMEOUT_SECONDS", "18"))

SECRET_HEADER_HINTS = ("authorization", "api-key", "apikey", "x-api-key", "token", "secret", "cookie")
PUBLIC_CRAWL_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
}
XUEQIU_TOKEN_ENV_NAMES = ("DEEPFOCUS_XUEQIU_COOKIE", "XUEQIU_TOKEN")
XUEQIU_USER_AGENT_ENV = "DEEPFOCUS_XUEQIU_USER_AGENT"
XUEQIU_REFERER_ENV = "DEEPFOCUS_XUEQIU_REFERER"
XUEQIU_STATUS_URL_ENV = "DEEPFOCUS_XUEQIU_STATUS_URL"
KEYWORD_PROVIDER_POLICIES: dict[str, dict[str, Any]] = {
    "xueqiu": {
        "name": "雪球关键词抓取",
        "description": "按关键词抓取雪球公开页面/接口返回的社区讨论资料。",
        "trust_level": "community",
        "auth_mode": "public_or_user_cookie",
        "risk_level": "high",
        "fallback_provider": "wechat_public",
        "rate_limit": "低频人工触发；遇到验证自动降级",
        "health_score": 58,
        "notes": "支持 DEEPFOCUS_XUEQIU_COOKIE / XUEQIU_TOKEN 自带登录态，可选配置 DEEPFOCUS_XUEQIU_USER_AGENT / DEEPFOCUS_XUEQIU_REFERER；不自动获取 token 或绕过验证。",
    },
    "wechat_public": {
        "name": "公众号关键词抓取",
        "description": "按关键词抓取搜狗微信公开搜索结果中的公众号文章资料。",
        "trust_level": "media",
        "auth_mode": "public_search",
        "risk_level": "medium",
        "fallback_provider": None,
        "rate_limit": "低频关键词搜索；建议中文公司名或事件词",
        "health_score": 74,
        "notes": "公开搜索结果可能存在风控或页面结构变化。",
    },
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> bool:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")
        return True
    return False


def _backfill_source_categories(conn: sqlite3.Connection, *, force: bool = False) -> None:
    rows = conn.execute("SELECT id, name, source_type, category FROM data_sources").fetchall()
    for row in rows:
        category = str(row["category"] or "").strip()
        if category and not force:
            continue
        conn.execute(
            "UPDATE data_sources SET category = ?, updated_at = ? WHERE id = ?",
            (_infer_source_category(row["name"], row["source_type"]), now_iso(), row["id"]),
        )


def _infer_source_category(name: str, source_type: str) -> str:
    text = f"{name or ''} {source_type or ''}".lower()
    if source_type == "market_api" or any(token in text for token in ("行情", "quote", "market")):
        return "market"
    if any(token in text for token in ("财报", "earnings", "eps", "calendar")):
        return "earnings"
    if any(token in text for token in ("公告", "filing", "sec", "10-k", "10-q")):
        return "filing"
    if any(token in text for token in ("雪球", "公众号", "微信", "舆情", "sentiment", "community")):
        return "sentiment"
    if source_type == "upload":
        return "upload"
    if source_type == "manual":
        return "internal"
    if any(token in text for token in ("研报", "research", "报告")):
        return "research"
    return "research"


def init_data_source_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS data_sources (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'research',
                source_type TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                trust_level TEXT NOT NULL,
                output_schema_json TEXT NOT NULL DEFAULT '{}',
                config_json TEXT NOT NULL,
                last_sync_at TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        category_added = _ensure_column(conn, "data_sources", "category", "category TEXT NOT NULL DEFAULT 'research'")
        _ensure_column(conn, "data_sources", "output_schema_json", "output_schema_json TEXT NOT NULL DEFAULT '{}'")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS data_items (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                source_name TEXT NOT NULL,
                source_type TEXT NOT NULL,
                title TEXT NOT NULL,
                symbol TEXT,
                url TEXT,
                text TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                credibility_score REAL NOT NULL,
                collected_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS data_source_module_refs (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                module TEXT NOT NULL,
                role TEXT NOT NULL,
                filters_json TEXT NOT NULL DEFAULT '{}',
                notes TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(source_id, module, role)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_data_source_refs_source ON data_source_module_refs(source_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_data_source_refs_module ON data_source_module_refs(module)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_data_items_symbol ON data_items(symbol)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_data_items_source ON data_items(source_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_data_items_created ON data_items(created_at)")
        _backfill_source_categories(conn, force=category_added)
        conn.commit()


def create_data_source(request: DataSourceCreateRequest) -> DataSourceRecord:
    init_data_source_db()
    if request.source_type in {"server_api", "market_api", "web_page"} and not request.url:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="该数据源类型需要配置 URL。")

    timestamp = now_iso()
    source_id = str(uuid.uuid4())
    config = {
        "url": request.url,
        "method": request.method,
        "headers": request.headers,
        "params": request.params,
        "body": request.body,
        "symbol_param": request.symbol_param,
        "query_param": request.query_param,
        "enabled": request.enabled,
        "notes": request.notes,
    }
    record = {
        "id": source_id,
        "name": request.name.strip(),
        "category": request.category,
        "source_type": request.source_type,
        "description": request.description.strip(),
        "status": "active" if request.enabled else "paused",
        "trust_level": request.trust_level,
        "output_schema_json": json.dumps(request.output_schema, ensure_ascii=False),
        "config_json": json.dumps(config, ensure_ascii=False),
        "last_sync_at": None,
        "last_error": None,
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO data_sources (
                id, name, category, source_type, description, status, trust_level, output_schema_json, config_json,
                last_sync_at, last_error, created_at, updated_at
            ) VALUES (
                :id, :name, :category, :source_type, :description, :status, :trust_level, :output_schema_json, :config_json,
                :last_sync_at, :last_error, :created_at, :updated_at
            )
            """,
            record,
        )
        conn.commit()
    return get_data_source(source_id)  # type: ignore[return-value]


def list_data_sources() -> list[DataSourceRecord]:
    init_data_source_db()
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM data_sources ORDER BY created_at DESC").fetchall()
    return [_row_to_source(dict(row)) for row in rows]


def get_data_source(source_id: str) -> Optional[DataSourceRecord]:
    init_data_source_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM data_sources WHERE id = ?", (source_id,)).fetchone()
    return _row_to_source(dict(row)) if row else None


def delete_data_source(source_id: str) -> bool:
    init_data_source_db()
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM data_sources WHERE id = ?", (source_id,))
        conn.execute("DELETE FROM data_items WHERE source_id = ?", (source_id,))
        conn.execute("DELETE FROM data_source_module_refs WHERE source_id = ?", (source_id,))
        conn.commit()
    return cursor.rowcount > 0


def list_data_source_module_refs(
    *,
    source_id: Optional[str] = None,
    module: Optional[str] = None,
) -> list[DataSourceModuleRefRecord]:
    init_data_source_db()
    clauses: list[str] = []
    values: list[Any] = []
    if source_id:
        clauses.append("ref.source_id = ?")
        values.append(source_id)
    if module:
        clauses.append("ref.module = ?")
        values.append(module)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT
                ref.*,
                source.name AS source_name,
                source.source_type AS source_type,
                source.category AS source_category
            FROM data_source_module_refs ref
            JOIN data_sources source ON source.id = ref.source_id
            {where}
            ORDER BY ref.module ASC, ref.role ASC, ref.updated_at DESC
            """,
            values,
        ).fetchall()
    return [_row_to_module_ref(dict(row)) for row in rows]


def save_data_source_module_ref(request: DataSourceModuleRefCreateRequest) -> DataSourceModuleRefRecord:
    init_data_source_db()
    source = get_data_source(request.source_id)
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Data source not found")

    timestamp = now_iso()
    ref_id = str(uuid.uuid4())
    record = {
        "id": ref_id,
        "source_id": request.source_id,
        "module": request.module,
        "role": request.role,
        "filters_json": json.dumps(request.filters, ensure_ascii=False),
        "notes": request.notes.strip(),
        "enabled": 1 if request.enabled else 0,
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    with _connect() as conn:
        existing = conn.execute(
            """
            SELECT id, created_at
            FROM data_source_module_refs
            WHERE source_id = ? AND module = ? AND role = ?
            LIMIT 1
            """,
            (request.source_id, request.module, request.role),
        ).fetchone()
        if existing:
            record["id"] = existing["id"]
            record["created_at"] = existing["created_at"]
            conn.execute(
                """
                UPDATE data_source_module_refs
                SET filters_json = :filters_json,
                    notes = :notes,
                    enabled = :enabled,
                    updated_at = :updated_at
                WHERE id = :id
                """,
                record,
            )
        else:
            conn.execute(
                """
                INSERT INTO data_source_module_refs (
                    id, source_id, module, role, filters_json, notes, enabled, created_at, updated_at
                ) VALUES (
                    :id, :source_id, :module, :role, :filters_json, :notes, :enabled, :created_at, :updated_at
                )
                """,
                record,
            )
        conn.commit()
    refs = list_data_source_module_refs(source_id=request.source_id)
    for ref in refs:
        if ref.id == record["id"]:
            return ref
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Data source ref save failed")


def delete_data_source_module_ref(ref_id: str) -> bool:
    init_data_source_db()
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM data_source_module_refs WHERE id = ?", (ref_id,))
        conn.commit()
    return cursor.rowcount > 0


async def sync_data_source(source_id: str, request: DataSourceSyncRequest) -> tuple[DataSourceRecord, list[DataSourceItemRecord]]:
    source = get_data_source(source_id)
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Data source not found")
    if source.status == "paused":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Data source is paused")
    config = _raw_source_config(source.id)

    try:
        items = await _fetch_source_items(source, config, request)
        saved = [
            _store_data_item(
                source_id=source.id,
                source_name=source.name,
                source_type=source.source_type,
                title=item["title"],
                text=item["text"],
                symbol=item.get("symbol") or request.symbol,
                url=item.get("url"),
                tags=item.get("tags") or [],
                metadata=item.get("metadata") or {},
                credibility_score=_credibility_score(source.trust_level),
                collected_at=item.get("collected_at"),
            )
            for item in items[: request.limit]
            if item.get("text")
        ]
        _update_source(source.id, status="active", last_sync_at=now_iso(), last_error=None)
        return get_data_source(source.id) or source, saved
    except HTTPException:
        raise
    except Exception as exc:
        _update_source(source.id, status="error", last_error=str(exc))
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"数据源同步失败：{exc}") from exc


async def capture_agent_web_page(request: DataSourceSyncRequest) -> DataSourceItemRecord:
    _, items = await capture_agent_web_pages(request)
    if not items:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="网页没有提取到可用文本。")
    return items[0]


async def capture_agent_web_pages(request: DataSourceSyncRequest) -> tuple[DataSourceRecord, list[DataSourceItemRecord]]:
    if not request.url:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="需要提供网页 URL。")
    source_id = _ensure_builtin_source(
        name="Agent 自主抓取",
        source_type="agent_crawl",
        description="由 Agent 或用户指定 URL 抓取的网页/社区资料。",
        trust_level="community",
    )
    source = get_data_source(source_id)
    if not source:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="内置抓取源初始化失败。")
    _, items = await sync_data_source(source.id, request)
    if not items:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="网页没有提取到可用文本。")
    return get_data_source(source.id) or source, items


async def keyword_crawl_data_source(
    request: DataSourceKeywordCrawlRequest,
) -> tuple[DataSourceRecord, list[DataSourceItemRecord], list[str], dict[str, Any]]:
    keyword = request.keyword.strip()
    if not keyword:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="关键词不能为空。")

    provider = request.provider
    attempted_providers: list[str] = [provider]
    fallback_used = False
    warnings: list[str] = []
    try:
        raw_items, warnings = await _fetch_keyword_provider(provider, request=request, keyword=keyword)
        effective_provider = provider
        fallback_provider = KEYWORD_PROVIDER_POLICIES[provider].get("fallback_provider")
        if (
            not raw_items
            and provider == "xueqiu"
            and fallback_provider
            and _xueqiu_access_blocked(warnings)
        ):
            fallback_used = True
            effective_provider = str(fallback_provider)
            attempted_providers.append(effective_provider)
            fallback_items, fallback_warnings = await _fetch_keyword_provider(
                effective_provider,
                request=request,
                keyword=keyword,
            )
            warnings = [
                *warnings,
                f"已按源策略降级到 {KEYWORD_PROVIDER_POLICIES[effective_provider]['name']}。",
                *fallback_warnings,
            ]
            raw_items = fallback_items

        source = _keyword_source_for_provider(effective_provider)

        saved: list[DataSourceItemRecord] = []
        for rank, item in enumerate(raw_items[: request.limit], start=1):
            if not item.get("text"):
                continue
            metadata = dict(item.get("metadata") or {})
            metadata.setdefault("rank", rank)
            metadata["sort_mode"] = request.sort
            metadata["freshness"] = request.freshness
            metadata["requested_provider"] = provider
            metadata["effective_provider"] = effective_provider
            metadata["fallback_used"] = fallback_used
            saved.append(
                _store_data_item(
                    source_id=source.id,
                    source_name=source.name,
                    source_type=source.source_type,
                    title=item["title"],
                    text=item["text"],
                    symbol=item.get("symbol") or request.symbol,
                    url=item.get("url"),
                    tags=item.get("tags") or [],
                    metadata=metadata,
                    credibility_score=_credibility_score(source.trust_level),
                    collected_at=item.get("collected_at"),
                )
            )
        status_value = "active" if saved else ("error" if warnings else "active")
        _update_source(
            source.id,
            status=status_value,
            last_sync_at=now_iso(),
            last_error="；".join(warnings[:3]) if warnings else None,
        )
        meta = {
            "provider": provider,
            "effective_provider": effective_provider,
            "attempted_providers": attempted_providers,
            "fallback_used": fallback_used,
            "provider_policy": _public_keyword_provider_policy(provider),
        }
        return get_data_source(source.id) or source, saved, warnings, meta
    except Exception as exc:
        source = _keyword_source_for_provider(provider)
        _update_source(source.id, status="error", last_error=str(exc))
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"关键词抓取失败：{exc}") from exc


def _keyword_source_for_provider(provider: str) -> DataSourceRecord:
    provider_meta = KEYWORD_PROVIDER_POLICIES[provider]
    source_id = _ensure_builtin_source(
        name=provider_meta["name"],
        source_type="agent_crawl",
        description=provider_meta["description"],
        trust_level=provider_meta["trust_level"],
    )
    source = get_data_source(source_id)
    if not source:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="关键词抓取源初始化失败。")
    return source


async def _fetch_keyword_provider(
    provider: str,
    *,
    request: DataSourceKeywordCrawlRequest,
    keyword: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    if provider == "xueqiu":
        return await _fetch_xueqiu_keyword(
            keyword=keyword,
            symbol=request.symbol,
            limit=request.limit,
            sort=request.sort,
            freshness=request.freshness,
        )
    if provider == "wechat_public":
        return await _fetch_wechat_public_keyword(
            keyword=keyword,
            symbol=request.symbol,
            limit=request.limit,
            sort=request.sort,
            freshness=request.freshness,
        )
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"暂不支持的数据源：{provider}")


def _xueqiu_access_blocked(warnings: list[str]) -> bool:
    return any(re.search(r"WAF|验证码|反爬|HTTP 401|HTTP 403|HTTP 418|HTTP 429", warning, re.IGNORECASE) for warning in warnings)


def _public_keyword_provider_policy(provider: str) -> dict[str, Any]:
    policy = dict(KEYWORD_PROVIDER_POLICIES[provider])
    policy["provider"] = provider
    policy["configured"] = provider != "xueqiu" or bool(_configured_xueqiu_cookie())
    return policy


def store_upload_item(
    *,
    filename: str,
    text: str,
    parser: str,
    content_type: Optional[str],
    symbol: Optional[str] = None,
    title: Optional[str] = None,
    tags: Optional[list[str]] = None,
) -> DataSourceItemRecord:
    source_id = _ensure_builtin_source(
        name="本地上传资料",
        source_type="upload",
        description="用户上传的财报、研报、表格、纪要和自有资料。",
        trust_level="internal",
    )
    source = get_data_source(source_id)
    if not source:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="上传资料源初始化失败。")
    return _store_data_item(
        source_id=source.id,
        source_name=source.name,
        source_type=source.source_type,
        title=title or filename,
        text=text,
        symbol=symbol,
        url=None,
        tags=tags or [],
        metadata={"filename": filename, "parser": parser, "content_type": content_type},
        credibility_score=_credibility_score(source.trust_level),
        collected_at=now_iso(),
    )


def list_data_items(
    *,
    symbol: Optional[str] = None,
    query: Optional[str] = None,
    source_type: Optional[str] = None,
    source_id: Optional[str] = None,
    tag: Optional[str] = None,
    limit: int = 50,
    sort: str = "time_desc",
) -> list[DataSourceItemRecord]:
    init_data_source_db()
    clauses: list[str] = []
    values: list[Any] = []
    if symbol:
        clauses.append("(data_items.symbol = ? OR data_items.title LIKE ? OR data_items.text LIKE ?)")
        like = f"%{symbol}%"
        values.extend([symbol, like, like])
    if query:
        tokens = _query_tokens(query)
        token_clauses: list[str] = []
        for token in tokens[:6]:
            token_clauses.append("(data_items.title LIKE ? OR data_items.text LIKE ?)")
            like = f"%{token}%"
            values.extend([like, like])
        if token_clauses:
            clauses.append(f"({' OR '.join(token_clauses)})")
    if source_type:
        clauses.append("data_items.source_type = ?")
        values.append(source_type)
    if source_id:
        clauses.append("data_items.source_id = ?")
        values.append(source_id)
    if tag:
        clauses.append("data_items.metadata_json LIKE ?")
        values.append(f"%\"{tag}\"%")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    fetch_limit = min(max(limit * 20, 500), 2000)
    values.append(fetch_limit)
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT
                data_items.*,
                data_sources.category AS source_category
            FROM data_items
            LEFT JOIN data_sources ON data_sources.id = data_items.source_id
            {where}
            ORDER BY data_items.credibility_score DESC, data_items.collected_at DESC, data_items.created_at DESC
            LIMIT ?
            """,
            values,
        ).fetchall()
    records = [_row_to_item(dict(row)) for row in rows]
    return _sort_data_items(records, sort)[:limit]


def get_data_item(item_id: str) -> Optional[DataSourceItemRecord]:
    init_data_source_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM data_items WHERE id = ?", (item_id,)).fetchone()
    return _row_to_item(dict(row)) if row else None


def update_data_item(item_id: str, request: DataSourceItemUpdateRequest) -> Optional[DataSourceItemRecord]:
    init_data_source_db()
    current = get_data_item(item_id)
    if not current:
        return None

    metadata = dict(current.metadata)
    tags = _normalize_tags(request.tags) if request.tags is not None else current.tags
    metadata["tags"] = tags

    updates: dict[str, Any] = {
        "metadata_json": json.dumps(metadata, ensure_ascii=False),
    }
    if request.title is not None:
        updates["title"] = request.title.strip()[:240] or current.title
    if request.symbol is not None:
        updates["symbol"] = request.symbol.strip().upper() or None
    if request.credibility_score is not None:
        updates["credibility_score"] = float(request.credibility_score)
    if request.ai_interpretation is not None:
        interpretation = request.ai_interpretation.strip()
        if interpretation:
            metadata["ai_interpretation"] = interpretation[:20000]
            metadata["ai_interpretation_updated_at"] = now_iso()
        else:
            metadata.pop("ai_interpretation", None)
            metadata.pop("ai_interpretation_updated_at", None)
    updates["metadata_json"] = json.dumps(metadata, ensure_ascii=False)

    assignments = ", ".join(f"{key} = ?" for key in updates)
    values = list(updates.values()) + [item_id]
    with _connect() as conn:
        conn.execute(f"UPDATE data_items SET {assignments} WHERE id = ?", values)
        conn.commit()
    return get_data_item(item_id)


def delete_data_item(item_id: str) -> bool:
    init_data_source_db()
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM data_items WHERE id = ?", (item_id,))
        conn.commit()
    return cursor.rowcount > 0


def list_data_tags() -> list[DataSourceTagRecord]:
    init_data_source_db()
    counts: dict[str, int] = {}
    with _connect() as conn:
        rows = conn.execute("SELECT metadata_json FROM data_items").fetchall()
    for row in rows:
        metadata = json.loads(row["metadata_json"] or "{}")
        for tag in metadata.get("tags") or []:
            if not str(tag).strip():
                continue
            counts[str(tag)] = counts.get(str(tag), 0) + 1
    return [
        DataSourceTagRecord(tag=tag, count=count)
        for tag, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


async def collect_task_evidence(payload: dict[str, Any], limit: int = 8) -> list[dict[str, Any]]:
    symbol = payload.get("symbol") or None
    query = " ".join(
        str(part)
        for part in (
            payload.get("asset_name"),
            payload.get("title"),
            payload.get("objective"),
            str(payload.get("context") or "")[:500],
        )
        if part
    )
    await _auto_sync_sources(symbol=symbol, query=query)
    records = list_data_items(symbol=symbol, limit=limit) if symbol else []
    if len(records) < limit:
        existing_ids = {record.id for record in records}
        records.extend(
            record
            for record in list_data_items(query=query, limit=limit)
            if record.id not in existing_ids
        )
    records = records[:limit]
    return [
        {
            "source": record.source_name,
            "source_type": record.source_type,
            "title": record.title,
            "symbol": record.symbol,
            "url": record.url,
            "tags": record.tags,
            "credibility_score": record.credibility_score,
            "collected_at": record.collected_at,
            "text": record.text[:6000],
        }
        for record in records
    ]


async def _auto_sync_sources(*, symbol: Optional[str], query: str) -> None:
    sources = [
        source
        for source in list_data_sources()
        if source.status in {"active", "error"}
        and source.source_type in {"server_api", "market_api", "web_page"}
        and source.config.get("url")
    ][:AUTO_SYNC_LIMIT]
    for source in sources:
        try:
            await sync_data_source(source.id, DataSourceSyncRequest(symbol=symbol, query=query, limit=8))
        except Exception:
            continue
        await asyncio.sleep(0)


async def _fetch_source_items(
    source: DataSourceRecord,
    config: dict[str, Any],
    request: DataSourceSyncRequest,
) -> list[dict[str, Any]]:
    raw_url = request.url or config.get("url")
    if not raw_url:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="数据源缺少 URL。")
    url = _render_template(str(raw_url), symbol=request.symbol, query=request.query)
    _validate_http_url(url)

    method = str(config.get("method") or "GET").upper()
    if method == "GET":
        known_list_items = await _fetch_known_list_page_items(url, request)
        if known_list_items is not None:
            return known_list_items

    headers = {str(k): _render_template(str(v), symbol=request.symbol, query=request.query) for k, v in (config.get("headers") or {}).items()}
    params = {
        str(k): _render_template(str(v), symbol=request.symbol, query=request.query)
        for k, v in (config.get("params") or {}).items()
    }
    symbol_param = config.get("symbol_param")
    query_param = config.get("query_param")
    if request.symbol and symbol_param:
        params.setdefault(str(symbol_param), request.symbol)
    if request.query and query_param:
        params.setdefault(str(query_param), request.query)

    body = config.get("body")
    if isinstance(body, dict):
        body = _render_nested_template(body, symbol=request.symbol, query=request.query)

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
        if method == "POST":
            response = await client.post(url, headers=headers, params=params, json=body)
        else:
            response = await client.get(url, headers=headers, params=params)
    response.raise_for_status()

    content_type = response.headers.get("content-type", "")
    collected_at = now_iso()
    if "application/json" in content_type.lower():
        return _items_from_json(
            response.json(),
            source_name=source.name,
            fallback_symbol=request.symbol,
            fallback_url=str(response.url),
            collected_at=collected_at,
        )

    text = _html_to_text(response.text) if "<html" in response.text[:500].lower() else _clean_text(response.text)
    title = _extract_html_title(response.text) or source.name
    return [
        {
            "title": title,
            "text": text,
            "symbol": request.symbol,
            "url": str(response.url),
            "tags": [],
            "metadata": {"content_type": content_type},
            "collected_at": collected_at,
        }
    ]


async def _fetch_known_list_page_items(
    url: str,
    request: DataSourceSyncRequest,
) -> Optional[list[dict[str, Any]]]:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    params = dict(parse_qsl(parsed.query))

    if host.endswith("weixin.sogou.com") and parsed.path.startswith("/weixin"):
        keyword = params.get("query") or request.query
        if not keyword:
            return None
        sort = "time_desc" if params.get("sort") == "2" else "relevance"
        items, _warnings = await _fetch_wechat_public_keyword(
            keyword=keyword,
            symbol=request.symbol,
            limit=request.limit,
            sort=sort,
            freshness="any",
        )
        return items

    if host.endswith("xueqiu.com") and (parsed.path.startswith("/k") or "search/status" in parsed.path):
        keyword = params.get("q") or request.query
        if not keyword:
            return None
        sort = "time_desc" if params.get("sortId") == "2" else "relevance"
        items, _warnings = await _fetch_xueqiu_keyword(
            keyword=keyword,
            symbol=request.symbol,
            limit=request.limit,
            sort=sort,
            freshness="any",
        )
        return items

    return None


async def _fetch_wechat_public_keyword(
    *,
    keyword: str,
    symbol: Optional[str],
    limit: int,
    sort: str,
    freshness: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    sort_param = "2" if sort == "time_desc" else "1"
    date_params, freshness_label, start_at, end_at = _wechat_date_params(freshness)
    search_url = (
        f"https://weixin.sogou.com/weixin?type=2&ie=utf8&query={quote(keyword)}"
        f"&sort={sort_param}{date_params}&interation=&wxid=&usip="
    )
    warnings: list[str] = []
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True, headers=PUBLIC_CRAWL_HEADERS) as client:
        response = await client.get(search_url)
    if response.status_code >= 400:
        return [], [f"搜狗微信返回 HTTP {response.status_code}"]

    text = response.text
    if _looks_blocked(text):
        return [], ["搜狗微信返回验证码/风控页面，未抓取正文。"]

    items: list[dict[str, Any]] = []
    blocks = re.findall(
        r'<div class="txt-box">(.*?)(?=<div class="txt-box"|<div id="pagebar_container"|</ul>)',
        text,
        flags=re.DOTALL,
    )
    for block in blocks:
        title_match = re.search(r"<h3>\s*<a[^>]+href=\"([^\"]+)\"[^>]*>(.*?)</a>\s*</h3>", block, flags=re.DOTALL)
        if not title_match:
            continue
        href = html.unescape(title_match.group(1))
        title = _clean_html_fragment(title_match.group(2))
        snippet_match = re.search(r'<p[^>]*class="txt-info"[^>]*>(.*?)</p>', block, flags=re.DOTALL)
        snippet = _clean_html_fragment(snippet_match.group(1)) if snippet_match else ""
        account_match = re.search(r'<span[^>]*class="all-time-y2"[^>]*>(.*?)</span>', block, flags=re.DOTALL)
        account = _clean_html_fragment(account_match.group(1)) if account_match else ""
        time_match = re.search(r'<span[^>]*class="s2"[^>]*>(.*?)</span>', block, flags=re.DOTALL)
        published, published_at = _parse_wechat_publish_time(time_match.group(1) if time_match else "")
        if not _within_datetime_window(published_at, start_at, end_at):
            continue
        absolute_url = urljoin("https://weixin.sogou.com", href)
        body = "\n".join(
            part
            for part in [
                f"标题：{title}",
                f"摘要：{snippet}" if snippet else "",
                f"公众号：{account}" if account else "",
                f"时间：{published}" if published else "",
                f"搜索关键词：{keyword}",
            ]
            if part
        )
        items.append(
            {
                "title": title or f"公众号搜索：{keyword}",
                "text": body,
                "symbol": symbol,
                "url": absolute_url,
                "tags": _normalize_tags([keyword, "公众号", "搜狗微信", *( [symbol] if symbol else [] )]),
                "metadata": {
                    "provider": "wechat_public",
                    "search_url": search_url,
                    "account": account,
                    "published": published,
                    "published_at": published_at,
                    "freshness_window": freshness_label,
                    "capture_mode": "search_result",
                },
                "collected_at": published_at or now_iso(),
            }
        )
        if len(items) >= limit:
            break

    if sort == "time_desc":
        items.sort(key=lambda item: _iso_timestamp(item.get("metadata", {}).get("published_at")), reverse=True)
    if not items:
        if freshness != "any":
            warnings.append("搜狗微信没有返回所选时间范围内的结果，建议换中文关键词或放宽时间范围。")
        else:
            warnings.append("搜狗微信未解析到搜索结果，可能被风控或页面结构变化。")
    return items, warnings


def _parse_wechat_publish_time(fragment: str) -> tuple[str, Optional[str]]:
    match = re.search(r"timeConvert\('(\d{10,13})'\)", fragment or "")
    if match:
        timestamp = _coerce_epoch_seconds(match.group(1))
        if timestamp:
            dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
            return dt.strftime("%Y-%m-%d %H:%M UTC"), dt.isoformat()
    return _clean_html_fragment(fragment), None


def _wechat_date_params(freshness: str) -> tuple[str, str, Optional[datetime], Optional[datetime]]:
    if freshness == "any":
        return "", "全部时间", None, None
    days_by_freshness = {
        "day": 1,
        "week": 7,
        "month": 30,
        "year": 365,
    }
    days = days_by_freshness.get(freshness, 7)
    cn_tz = timezone(timedelta(hours=8))
    end_date = datetime.now(cn_tz).date()
    start_date = end_date - timedelta(days=days)
    start_at = datetime.combine(start_date, datetime.min.time(), tzinfo=cn_tz)
    end_at = datetime.combine(end_date + timedelta(days=1), datetime.min.time(), tzinfo=cn_tz)
    return f"&ft={start_date.isoformat()}&et={end_date.isoformat()}", f"{start_date.isoformat()} 至 {end_date.isoformat()}", start_at, end_at


def _within_datetime_window(value: Optional[str], start_at: Optional[datetime], end_at: Optional[datetime]) -> bool:
    if not start_at and not end_at:
        return True
    timestamp = _iso_timestamp(value)
    if not timestamp:
        return False
    if start_at and timestamp < start_at.timestamp():
        return False
    if end_at and timestamp >= end_at.timestamp():
        return False
    return True


async def _fetch_xueqiu_keyword(
    *,
    keyword: str,
    symbol: Optional[str],
    limit: int,
    sort: str,
    freshness: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    items: list[dict[str, Any]] = []
    encoded_keyword = quote(keyword)
    sort_id = "2" if sort == "time_desc" else "1"
    urls = [
        *_configured_xueqiu_status_urls(keyword),
        f"https://xueqiu.com/query/v1/search/status?sortId={sort_id}&q={encoded_keyword}&count={limit}",
        f"https://xueqiu.com/query/v1/search/status.json?sortId={sort_id}&q={encoded_keyword}&count={limit}&page=1",
        f"https://xueqiu.com/k?q={encoded_keyword}",
    ]
    xueqiu_cookie = _configured_xueqiu_cookie()
    headers = _xueqiu_request_headers(keyword=keyword, cookie=xueqiu_cookie)
    if xueqiu_cookie:
        warnings.append("已使用配置的雪球登录态请求；请确保该账号和用途符合雪球规则。")
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True, headers=headers) as client:
        for crawl_url in urls:
            try:
                response = await client.get(crawl_url)
            except httpx.HTTPError as exc:
                warnings.append(f"雪球请求失败：{exc}")
                continue
            if response.status_code in {401, 403, 418, 429}:
                warnings.append(f"雪球返回 HTTP {response.status_code}，可能需要官方授权、网页登录态或遇到反爬。")
                continue
            if response.status_code >= 400:
                warnings.append(f"雪球返回 HTTP {response.status_code}")
                continue
            content = response.text
            if _looks_blocked(content):
                warnings.append("雪球返回 WAF/验证码页面，未抓取公开讨论。")
                continue

            parsed = _parse_xueqiu_json(content, keyword=keyword, symbol=symbol, crawl_url=crawl_url, limit=limit)
            if not parsed:
                parsed = _parse_xueqiu_html(content, keyword=keyword, symbol=symbol, crawl_url=crawl_url, limit=limit)
            for item in parsed:
                metadata = item.setdefault("metadata", {})
                if isinstance(metadata, dict):
                    metadata["auth_mode"] = "configured_cookie" if xueqiu_cookie else "public"
            items.extend(parsed)
            if len(items) >= limit:
                break

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        key = f"{item.get('title')}|{item.get('url')}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    if sort == "time_desc":
        deduped.sort(key=lambda item: _iso_timestamp(item.get("metadata", {}).get("published_at")), reverse=True)
    if not deduped and not _has_actionable_xueqiu_warning(warnings):
        warnings.append("雪球未解析到搜索结果。")
    return deduped[:limit], warnings


def _has_actionable_xueqiu_warning(warnings: list[str]) -> bool:
    markers = ("请求失败", "HTTP", "WAF", "验证码", "反爬", "未解析")
    return any(any(marker in warning for marker in markers) for warning in warnings)


def _configured_xueqiu_cookie() -> Optional[str]:
    for env_name in XUEQIU_TOKEN_ENV_NAMES:
        cookie = _configured_single_line_value(env_name)
        if not cookie:
            continue
        return cookie
    return None


def _configured_single_line_value(env_name: str) -> Optional[str]:
    value = os.getenv(env_name)
    if not value:
        return None
    cleaned = value.strip().strip('"').strip("'")
    if not cleaned:
        return None
    if "\n" in cleaned or "\r" in cleaned:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{env_name} 不能包含换行，请配置为单行值。",
        )
    return cleaned


def _xueqiu_request_headers(*, keyword: str, cookie: Optional[str]) -> dict[str, str]:
    referer = _configured_single_line_value(XUEQIU_REFERER_ENV) or f"https://xueqiu.com/k?q={quote(keyword)}"
    user_agent = _configured_single_line_value(XUEQIU_USER_AGENT_ENV)
    headers = {
        **PUBLIC_CRAWL_HEADERS,
        "Accept": "application/json, text/plain, */*",
        "Referer": referer,
        "Origin": "https://xueqiu.com",
        "X-Requested-With": "XMLHttpRequest",
    }
    if user_agent:
        headers["User-Agent"] = user_agent
    if cookie:
        headers["Cookie"] = cookie
    return headers


def _configured_xueqiu_status_urls(keyword: str) -> list[str]:
    url = _configured_single_line_value(XUEQIU_STATUS_URL_ENV)
    if not url:
        return []
    parsed = urlparse(url)
    if parsed.netloc and not parsed.netloc.endswith("xueqiu.com"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{XUEQIU_STATUS_URL_ENV} 必须是 xueqiu.com 的请求 URL。",
        )
    query = dict(parse_qsl(parsed.query))
    copied_keyword = query.get("q")
    if copied_keyword and copied_keyword != keyword:
        return []
    return [url]


def _parse_xueqiu_json(
    content: str,
    *,
    keyword: str,
    symbol: Optional[str],
    crawl_url: str,
    limit: int,
) -> list[dict[str, Any]]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return []
    candidates: list[Any] = []
    if isinstance(payload, dict):
        for key in ("list", "statuses", "items", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates = value
                break
            if isinstance(value, dict):
                for nested_key in ("list", "statuses", "items"):
                    nested_value = value.get(nested_key)
                    if isinstance(nested_value, list):
                        candidates = nested_value
                        break
    elif isinstance(payload, list):
        candidates = payload

    items: list[dict[str, Any]] = []
    for raw in candidates[:limit]:
        if not isinstance(raw, dict):
            continue
        explicit_title = _first_clean_xueqiu_field(raw, ("title", "headline"))
        body = _first_clean_xueqiu_field(raw, ("text", "content", "description", "summary"))
        summary = _first_clean_xueqiu_field(raw, ("description", "summary"))
        if summary and summary == body:
            summary = None
        if explicit_title:
            title = explicit_title
        elif body:
            title = _title_from_text(body, fallback=f"雪球讨论：{keyword}")
        else:
            title = f"雪球讨论：{keyword}"

        item_id = raw.get("id") or raw.get("target") or raw.get("url")
        url = str(raw.get("url") or raw.get("target") or "")
        published_at = _xueqiu_published_at(raw)
        author_metadata = _xueqiu_author_metadata(raw)
        engagement_metadata = _xueqiu_engagement_metadata(raw)
        if url.startswith("/"):
            url = urljoin("https://xueqiu.com", url)
        elif item_id and not url:
            url = f"https://xueqiu.com/statuses/{item_id}"
        source_text = _xueqiu_post_text(
            title=title,
            body=body,
            summary=summary,
            author_name=author_metadata.get("author_name"),
            published_at=published_at,
            url=url or crawl_url,
            engagement=engagement_metadata,
            keyword=keyword,
        )
        items.append(
            {
                "title": title[:160],
                "text": source_text,
                "symbol": symbol or raw.get("symbol"),
                "url": url or crawl_url,
                "tags": _normalize_tags([keyword, "雪球", *( [symbol] if symbol else [] )]),
                "metadata": {
                    "provider": "xueqiu",
                    "search_url": crawl_url,
                    "capture_mode": "api_result",
                    "published_at": published_at,
                    "status_id": str(raw.get("id") or "") or None,
                    "target": raw.get("target"),
                    "source_symbol": raw.get("symbol"),
                    "body_length": len(body),
                    "content_preview": body[:360],
                    "content_available": bool(body),
                    "summary": summary,
                    **author_metadata,
                    **engagement_metadata,
                    "raw_keys": sorted(raw.keys())[:40],
                },
                "collected_at": published_at or now_iso(),
            }
        )
    return items


def _first_clean_xueqiu_field(raw: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = raw.get(key)
        if value is None:
            continue
        cleaned = _clean_html_fragment(str(value))
        if cleaned:
            return cleaned
    return ""


def _title_from_text(text: str, *, fallback: str) -> str:
    compact = _clean_text(text)
    if not compact:
        return fallback
    first_line = compact.splitlines()[0]
    if len(first_line) <= 80:
        return first_line
    return f"{first_line[:77]}..."


def _xueqiu_author_metadata(raw: dict[str, Any]) -> dict[str, Any]:
    user = raw.get("user")
    if isinstance(user, dict):
        author_name = _first_text(user, ("screen_name", "name", "username", "nick_name"))
        author_id = _first_text(user, ("id", "user_id", "uid"))
        followers_count = _safe_int(user.get("followers_count") or user.get("followers"))
        verified = user.get("verified")
    else:
        author_name = str(user).strip() if user is not None else None
        author_id = None
        followers_count = None
        verified = None
    return {
        "author_name": author_name,
        "author_id": author_id,
        "author_followers_count": followers_count,
        "author_verified": verified,
    }


def _xueqiu_engagement_metadata(raw: dict[str, Any]) -> dict[str, Optional[int]]:
    return {
        "like_count": _safe_int(raw.get("like_count") or raw.get("likeCount")),
        "reply_count": _safe_int(raw.get("reply_count") or raw.get("replyCount") or raw.get("comment_count")),
        "retweet_count": _safe_int(raw.get("retweet_count") or raw.get("retweetCount") or raw.get("share_count")),
        "view_count": _safe_int(raw.get("view_count") or raw.get("viewCount")),
    }


def _xueqiu_post_text(
    *,
    title: str,
    body: str,
    summary: Optional[str],
    author_name: Any,
    published_at: Optional[str],
    url: str,
    engagement: dict[str, Optional[int]],
    keyword: str,
) -> str:
    engagement_parts = [
        f"点赞 {engagement['like_count']}" if engagement.get("like_count") is not None else "",
        f"评论 {engagement['reply_count']}" if engagement.get("reply_count") is not None else "",
        f"转发 {engagement['retweet_count']}" if engagement.get("retweet_count") is not None else "",
        f"浏览 {engagement['view_count']}" if engagement.get("view_count") is not None else "",
    ]
    sections = [
        f"标题：{title}",
        f"作者：{author_name}" if author_name else "",
        f"发布时间：{published_at}" if published_at else "",
        f"原文链接：{url}" if url else "",
        f"摘要：{summary}" if summary else "",
        "正文：",
        body or "未在接口返回中解析到正文。",
        f"互动：{' / '.join(part for part in engagement_parts if part)}" if any(engagement_parts) else "",
        f"搜索关键词：{keyword}",
    ]
    return "\n".join(part for part in sections if part)


def _parse_xueqiu_html(
    content: str,
    *,
    keyword: str,
    symbol: Optional[str],
    crawl_url: str,
    limit: int,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for href, title in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', content, flags=re.DOTALL):
        clean_title = _clean_html_fragment(title)
        if not clean_title or keyword.lower() not in clean_title.lower():
            continue
        url = urljoin("https://xueqiu.com", html.unescape(href))
        items.append(
            {
                "title": clean_title[:160],
                "text": f"标题：{clean_title}\n搜索关键词：{keyword}",
                "symbol": symbol,
                "url": url,
                "tags": _normalize_tags([keyword, "雪球", *( [symbol] if symbol else [] )]),
                "metadata": {"provider": "xueqiu", "search_url": crawl_url, "capture_mode": "html_result"},
                "collected_at": now_iso(),
            }
        )
        if len(items) >= limit:
            break
    return items


def _xueqiu_published_at(raw: dict[str, Any]) -> Optional[str]:
    for key in ("created_at", "createdAt", "created", "timeBefore", "created_time", "timestamp"):
        value = raw.get(key)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip().isdigit():
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
            except ValueError:
                continue
        timestamp = _coerce_epoch_seconds(value)
        if timestamp:
            return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
    return None


def _items_from_json(
    payload: Any,
    *,
    source_name: str,
    fallback_symbol: Optional[str],
    fallback_url: Optional[str],
    collected_at: str,
) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        raw_items = payload
    elif isinstance(payload, dict):
        raw_items = None
        for key in ("items", "data", "results", "articles", "news", "rows"):
            value = payload.get(key)
            if isinstance(value, list):
                raw_items = value
                break
        raw_items = raw_items if raw_items is not None else [payload]
    else:
        raw_items = [{"text": str(payload)}]

    items: list[dict[str, Any]] = []
    for raw in raw_items:
        if isinstance(raw, dict):
            title = _first_text(raw, ("title", "headline", "name", "subject", "symbol", "ticker")) or source_name
            text = _first_text(raw, ("text", "content", "summary", "description", "body", "abstract"))
            if not text:
                text = json.dumps(raw, ensure_ascii=False, indent=2)
            symbol = _first_text(raw, ("symbol", "ticker", "code")) or fallback_symbol
            url = _first_text(raw, ("url", "link", "href")) or fallback_url
            tags_value = raw.get("tags") or raw.get("keywords") or []
            tags = tags_value if isinstance(tags_value, list) else [str(tags_value)]
            items.append(
                {
                    "title": str(title),
                    "text": _clean_text(str(text)),
                    "symbol": symbol,
                    "url": url,
                    "tags": [str(tag) for tag in tags if str(tag).strip()],
                    "metadata": {"raw_keys": sorted(raw.keys())[:40]},
                    "collected_at": collected_at,
                }
            )
        else:
            items.append(
                {
                    "title": source_name,
                    "text": _clean_text(str(raw)),
                    "symbol": fallback_symbol,
                    "url": fallback_url,
                    "tags": [],
                    "metadata": {},
                    "collected_at": collected_at,
                }
            )
    return items


def _store_data_item(
    *,
    source_id: str,
    source_name: str,
    source_type: str,
    title: str,
    text: str,
    symbol: Optional[str],
    url: Optional[str],
    tags: list[str],
    metadata: dict[str, Any],
    credibility_score: float,
    collected_at: Optional[str],
) -> DataSourceItemRecord:
    init_data_source_db()
    timestamp = now_iso()
    item_id = str(uuid.uuid4())
    clean_text = _clean_text(text)[:MAX_STORED_TEXT_CHARS]
    clean_title = (title or source_name).strip()[:240]
    clean_symbol = symbol.strip().upper() if symbol else None
    record = {
        "id": item_id,
        "source_id": source_id,
        "source_name": source_name,
        "source_type": source_type,
        "title": clean_title,
        "symbol": clean_symbol,
        "url": url,
        "text": clean_text,
        "metadata_json": json.dumps({**metadata, "tags": _normalize_tags(tags)}, ensure_ascii=False),
        "credibility_score": credibility_score,
        "collected_at": collected_at or timestamp,
        "created_at": timestamp,
    }
    with _connect() as conn:
        existing = _find_existing_item_row(conn, source_id=source_id, title=clean_title, symbol=clean_symbol, url=url)
        if existing:
            record["id"] = existing["id"]
            record["created_at"] = existing["created_at"]
            conn.execute(
                """
                UPDATE data_items
                SET source_name = :source_name,
                    source_type = :source_type,
                    title = :title,
                    symbol = :symbol,
                    url = :url,
                    text = :text,
                    metadata_json = :metadata_json,
                    credibility_score = :credibility_score,
                    collected_at = :collected_at
                WHERE id = :id
                """,
                record,
            )
            conn.commit()
            row = conn.execute("SELECT * FROM data_items WHERE id = ?", (record["id"],)).fetchone()
            return _row_to_item(dict(row)) if row else _row_to_item(record)

        conn.execute(
            """
            INSERT INTO data_items (
                id, source_id, source_name, source_type, title, symbol, url, text,
                metadata_json, credibility_score, collected_at, created_at
            ) VALUES (
                :id, :source_id, :source_name, :source_type, :title, :symbol, :url, :text,
                :metadata_json, :credibility_score, :collected_at, :created_at
            )
            """,
            record,
        )
        conn.commit()
    return _row_to_item(record)


def _find_existing_item_row(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    title: str,
    symbol: Optional[str],
    url: Optional[str],
) -> Optional[sqlite3.Row]:
    symbol_value = symbol or ""
    if url:
        return conn.execute(
            """
            SELECT * FROM data_items
            WHERE source_id = ?
              AND (url = ? OR (title = ? AND COALESCE(symbol, '') = ?))
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (source_id, url, title, symbol_value),
        ).fetchone()
    return conn.execute(
        """
        SELECT * FROM data_items
        WHERE source_id = ?
          AND title = ?
          AND COALESCE(symbol, '') = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (source_id, title, symbol_value),
    ).fetchone()


def _ensure_builtin_source(*, name: str, source_type: str, description: str, trust_level: str) -> str:
    init_data_source_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT id FROM data_sources WHERE source_type = ? AND name = ? LIMIT 1",
            (source_type, name),
        ).fetchone()
        if row:
            return row["id"]
        timestamp = now_iso()
        source_id = str(uuid.uuid4())
        category = _infer_source_category(name, source_type)
        conn.execute(
            """
            INSERT INTO data_sources (
                id, name, category, source_type, description, status, trust_level, output_schema_json, config_json,
                last_sync_at, last_error, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, NULL, NULL, ?, ?)
            """,
            (
                source_id,
                name,
                category,
                source_type,
                description,
                trust_level,
                json.dumps(_default_output_schema(category, source_type), ensure_ascii=False),
                json.dumps({"enabled": True}, ensure_ascii=False),
                timestamp,
                timestamp,
            ),
        )
        conn.commit()
    return source_id


def _default_output_schema(category: str, source_type: str) -> dict[str, Any]:
    if category == "market":
        return {"symbol": "string", "price": "number", "change_percent": "number", "timestamp": "datetime"}
    if category == "earnings":
        return {"symbol": "string", "company_name": "string", "report_date": "date", "eps_estimate": "number"}
    if category == "filing":
        return {"symbol": "string", "filing_type": "string", "published_at": "datetime", "url": "string"}
    if category == "sentiment":
        return {"symbol": "string", "title": "string", "author": "string", "published_at": "datetime", "sentiment_hint": "string"}
    if category == "upload" or source_type == "upload":
        return {"filename": "string", "title": "string", "text": "string", "tags": "string[]"}
    return {"title": "string", "symbol": "string", "text": "string", "url": "string", "collected_at": "datetime"}


def _row_to_module_ref(row: dict[str, Any]) -> DataSourceModuleRefRecord:
    source_category = row.get("source_category") or _infer_source_category(row.get("source_name") or "", row.get("source_type") or "")
    return DataSourceModuleRefRecord(
        id=row["id"],
        source_id=row["source_id"],
        source_name=row["source_name"],
        source_type=row["source_type"],
        source_category=source_category,
        module=row["module"],
        role=row["role"],
        filters=json.loads(row.get("filters_json") or "{}"),
        notes=row.get("notes") or "",
        enabled=bool(row.get("enabled")),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_source(row: dict[str, Any]) -> DataSourceRecord:
    config = json.loads(row.get("config_json") or "{}")
    category = row.get("category") or _infer_source_category(row.get("name") or "", row.get("source_type") or "")
    output_schema = json.loads(row.get("output_schema_json") or "{}")
    if not output_schema:
        output_schema = _default_output_schema(category, row.get("source_type") or "")
    with _connect() as conn:
        count_row = conn.execute("SELECT COUNT(*) AS count FROM data_items WHERE source_id = ?", (row["id"],)).fetchone()
    return DataSourceRecord(
        id=row["id"],
        name=row["name"],
        category=category,
        source_type=row["source_type"],
        description=row.get("description") or "",
        status=row["status"],
        trust_level=row["trust_level"],
        output_schema=output_schema,
        config=_public_config(config),
        module_refs=list_data_source_module_refs(source_id=row["id"]),
        items_count=int(count_row["count"] if count_row else 0),
        last_sync_at=row.get("last_sync_at"),
        last_error=row.get("last_error"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _raw_source_config(source_id: str) -> dict[str, Any]:
    with _connect() as conn:
        row = conn.execute("SELECT config_json FROM data_sources WHERE id = ?", (source_id,)).fetchone()
    return json.loads(row["config_json"] or "{}") if row else {}


def _row_to_item(row: dict[str, Any]) -> DataSourceItemRecord:
    metadata = json.loads(row.get("metadata_json") or "{}")
    tags = metadata.pop("tags", []) or []
    text = row.get("text") or ""
    content_preview = metadata.get("content_preview")
    if not isinstance(content_preview, str) or not content_preview.strip():
        content_preview = text[:360]
    return DataSourceItemRecord(
        id=row["id"],
        source_id=row["source_id"],
        source_name=row["source_name"],
        source_type=row["source_type"],
        source_category=row.get("source_category") or _infer_source_category(row.get("source_name") or "", row.get("source_type") or ""),
        title=row["title"],
        symbol=row.get("symbol"),
        url=row.get("url"),
        text=text,
        text_preview=content_preview[:360],
        tags=tags,
        metadata=metadata,
        credibility_score=float(row.get("credibility_score") or 0.5),
        collected_at=row["collected_at"],
        created_at=row["created_at"],
    )


def _sort_data_items(records: list[DataSourceItemRecord], sort: str) -> list[DataSourceItemRecord]:
    if sort == "relevance":
        return sorted(
            records,
            key=lambda record: (
                0 if _safe_int(record.metadata.get("rank")) is not None else 1,
                _safe_int(record.metadata.get("rank")) or 1_000_000,
                -record.credibility_score,
                -_iso_timestamp(record.created_at),
            ),
        )
    return sorted(
        records,
        key=lambda record: (
            1 if record.metadata.get("published_at") else 0,
            _iso_timestamp(record.metadata.get("published_at") or record.collected_at),
            _iso_timestamp(record.created_at),
        ),
        reverse=True,
    )


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_epoch_seconds(value: Any) -> Optional[float]:
    timestamp = _safe_int(value)
    if timestamp is None:
        return None
    if timestamp > 10_000_000_000:
        return timestamp / 1000
    return float(timestamp)


def _iso_timestamp(value: Any) -> float:
    if not value:
        return 0.0
    try:
        normalized = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return 0.0


def _normalize_tags(tags: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        clean = str(tag).strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        normalized.append(clean[:40])
    return normalized[:24]


def _update_source(source_id: str, **updates: Any) -> None:
    if not updates:
        return
    updates["updated_at"] = now_iso()
    assignments = ", ".join(f"{key} = ?" for key in updates)
    values = list(updates.values()) + [source_id]
    with _connect() as conn:
        conn.execute(f"UPDATE data_sources SET {assignments} WHERE id = ?", values)
        conn.commit()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _validate_http_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="只支持 http/https URL。")


def _render_template(value: str, *, symbol: Optional[str], query: Optional[str]) -> str:
    return value.replace("{symbol}", symbol or "").replace("{query}", query or "")


def _render_nested_template(value: dict[str, Any], *, symbol: Optional[str], query: Optional[str]) -> dict[str, Any]:
    rendered: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, str):
            rendered[key] = _render_template(item, symbol=symbol, query=query)
        elif isinstance(item, dict):
            rendered[key] = _render_nested_template(item, symbol=symbol, query=query)
        else:
            rendered[key] = item
    return rendered


def _public_config(config: dict[str, Any]) -> dict[str, Any]:
    public = dict(config)
    headers = dict(public.get("headers") or {})
    public["headers"] = {
        key: ("已配置" if any(hint in key.lower() for hint in SECRET_HEADER_HINTS) else value)
        for key, value in headers.items()
    }
    return public


def _credibility_score(trust_level: str) -> float:
    return {
        "internal": 0.92,
        "official": 0.86,
        "media": 0.68,
        "community": 0.46,
        "unknown": 0.52,
    }.get(trust_level, 0.52)


def _query_tokens(query: str) -> list[str]:
    tokens = re.findall(r"[\w\u4e00-\u9fff]{2,}", query or "")
    seen: set[str] = set()
    result: list[str] = []
    for token in tokens:
        if token not in seen:
            seen.add(token)
            result.append(token)
    return result


def _first_text(raw: dict[str, Any], keys: tuple[str, ...]) -> Optional[str]:
    for key in keys:
        value = raw.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _extract_html_title(html: str) -> Optional[str]:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return _clean_text(re.sub(r"<[^>]+>", " ", match.group(1)))


def _clean_html_fragment(fragment: str) -> str:
    text = re.sub(r"<!--.*?-->", "", fragment, flags=re.DOTALL)
    text = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    return _clean_text(html.unescape(text))


def _looks_blocked(text: str) -> bool:
    lowered = text[:5000].lower()
    return any(
        marker in lowered
        for marker in (
            "captcha",
            "aliyun_waf",
            "_waf_",
            "访问过于频繁",
            "请输入验证码",
            "安全验证",
            "forbidden",
        )
    )


def _html_to_text(html: str) -> str:
    text = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|li|tr|h\d)>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    return _clean_text(text)


def _clean_text(text: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(line for line in lines if line).strip()
