from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import HTTPException, status

from .schemas import (
    McpCapabilityRecord,
    McpServerCreateRequest,
    McpServerRecord,
    McpToolCallRequest,
    McpToolCallResponse,
)


DB_PATH = Path(
    os.getenv(
        "DEEPFOCUS_MCP_DB_PATH",
        str(Path(__file__).resolve().parents[1] / ".mcp_hub.sqlite3"),
    )
)

HTTP_TIMEOUT_SECONDS = float(os.getenv("DEEPFOCUS_MCP_HTTP_TIMEOUT_SECONDS", "20"))
MCP_PROTOCOL_VERSION = os.getenv("DEEPFOCUS_MCP_PROTOCOL_VERSION", "2025-06-18")
SECRET_HEADER_HINTS = ("authorization", "api-key", "apikey", "x-api-key", "token", "secret", "cookie")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_mcp_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mcp_servers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                transport TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                trust_level TEXT NOT NULL,
                risk_level TEXT NOT NULL,
                approval_required INTEGER NOT NULL,
                enabled INTEGER NOT NULL,
                config_json TEXT NOT NULL,
                last_connected_at TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mcp_capabilities (
                id TEXT PRIMARY KEY,
                server_id TEXT NOT NULL,
                capability_type TEXT NOT NULL,
                name TEXT NOT NULL,
                title TEXT,
                description TEXT NOT NULL DEFAULT '',
                schema_json TEXT NOT NULL,
                uri TEXT,
                mime_type TEXT,
                metadata_json TEXT NOT NULL,
                discovered_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mcp_capabilities_server ON mcp_capabilities(server_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mcp_capabilities_type ON mcp_capabilities(capability_type)")
        conn.commit()


def list_mcp_servers() -> list[McpServerRecord]:
    init_mcp_db()
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM mcp_servers ORDER BY created_at DESC").fetchall()
    return [_row_to_server(dict(row)) for row in rows]


def get_mcp_server(server_id: str) -> Optional[McpServerRecord]:
    init_mcp_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM mcp_servers WHERE id = ?", (server_id,)).fetchone()
    return _row_to_server(dict(row)) if row else None


def create_mcp_server(request: McpServerCreateRequest) -> McpServerRecord:
    init_mcp_db()
    if request.transport == "streamable_http" and not request.url:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Streamable HTTP MCP 需要配置 URL。")
    if request.transport == "stdio" and not request.command:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="stdio MCP 需要配置 command。")

    timestamp = now_iso()
    server_id = str(uuid.uuid4())
    config = {
        "url": request.url,
        "command": request.command,
        "args": request.args,
        "env": request.env,
        "headers": request.headers,
        "allowed_tools": _normalize_names(request.allowed_tools),
        "blocked_tools": _normalize_names(request.blocked_tools),
        "notes": request.notes,
    }
    record = {
        "id": server_id,
        "name": request.name.strip()[:160] or "MCP Server",
        "transport": request.transport,
        "description": request.description.strip(),
        "status": "unknown" if request.enabled else "disabled",
        "trust_level": request.trust_level,
        "risk_level": request.risk_level,
        "approval_required": 1 if request.approval_required else 0,
        "enabled": 1 if request.enabled else 0,
        "config_json": json.dumps(config, ensure_ascii=False),
        "last_connected_at": None,
        "last_error": None,
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO mcp_servers (
                id, name, transport, description, status, trust_level, risk_level,
                approval_required, enabled, config_json, last_connected_at, last_error,
                created_at, updated_at
            ) VALUES (
                :id, :name, :transport, :description, :status, :trust_level, :risk_level,
                :approval_required, :enabled, :config_json, :last_connected_at, :last_error,
                :created_at, :updated_at
            )
            """,
            record,
        )
        conn.commit()
    return get_mcp_server(server_id)  # type: ignore[return-value]


def delete_mcp_server(server_id: str) -> bool:
    init_mcp_db()
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM mcp_servers WHERE id = ?", (server_id,))
        conn.execute("DELETE FROM mcp_capabilities WHERE server_id = ?", (server_id,))
        conn.commit()
    return cursor.rowcount > 0


def list_mcp_capabilities(
    *,
    server_id: Optional[str] = None,
    capability_type: Optional[str] = None,
) -> list[McpCapabilityRecord]:
    init_mcp_db()
    clauses: list[str] = []
    values: list[Any] = []
    if server_id:
        clauses.append("server_id = ?")
        values.append(server_id)
    if capability_type:
        clauses.append("capability_type = ?")
        values.append(capability_type)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM mcp_capabilities
            {where}
            ORDER BY capability_type, name
            """,
            values,
        ).fetchall()
    return [_row_to_capability(dict(row)) for row in rows]


async def discover_mcp_server(server_id: str) -> tuple[McpServerRecord, list[McpCapabilityRecord], list[str]]:
    server = get_mcp_server(server_id)
    if not server:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found")
    if not server.enabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该 MCP server 已停用。")
    if server.transport != "streamable_http":
        _update_server(server.id, status="error", last_error="当前后端只直接探测 Streamable HTTP；stdio/hosted 先作为配置资产管理。")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="当前模块直接支持 Streamable HTTP 探测；stdio/hosted server 已可登记，但需要接入受控 runner 后再执行。",
        )

    config = _raw_server_config(server.id)
    warnings: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
            session_headers = _request_headers(config)
            try:
                await _send_mcp_request(
                    client,
                    url=str(config["url"]),
                    headers=session_headers,
                    method="initialize",
                    params={
                        "protocolVersion": MCP_PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "DeepFocus MCP Center", "version": "0.1.0"},
                    },
                )
                await _send_mcp_notification(
                    client,
                    url=str(config["url"]),
                    headers=session_headers,
                    method="notifications/initialized",
                    params={},
                )
            except Exception as exc:
                warnings.append(f"initialize 未完成：{_clean_error(exc)}")

            tools = await _safe_list_capabilities(
                client,
                url=str(config["url"]),
                headers=session_headers,
                method="tools/list",
                result_key="tools",
                warnings=warnings,
            )
            resources = await _safe_list_capabilities(
                client,
                url=str(config["url"]),
                headers=session_headers,
                method="resources/list",
                result_key="resources",
                warnings=warnings,
            )
            prompts = await _safe_list_capabilities(
                client,
                url=str(config["url"]),
                headers=session_headers,
                method="prompts/list",
                result_key="prompts",
                warnings=warnings,
            )

        records = _replace_capabilities(server.id, tools=tools, resources=resources, prompts=prompts)
        _update_server(server.id, status="connected", last_connected_at=now_iso(), last_error="；".join(warnings[:3]) or None)
        return get_mcp_server(server.id) or server, records, warnings
    except HTTPException:
        raise
    except Exception as exc:
        _update_server(server.id, status="error", last_error=_clean_error(exc))
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"MCP 探测失败：{_clean_error(exc)}") from exc


async def call_mcp_tool(server_id: str, request: McpToolCallRequest) -> McpToolCallResponse:
    server = get_mcp_server(server_id)
    if not server:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found")
    if not server.enabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该 MCP server 已停用。")
    if server.transport != "streamable_http":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="当前只直接执行 Streamable HTTP MCP 工具。")
    if server.approval_required and not request.approved:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该 MCP server 要求人工确认后才能调用工具。")

    config = _raw_server_config(server.id)
    tool_name = request.tool_name.strip()
    _ensure_tool_allowed(config, tool_name)

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
        session_headers = _request_headers(config)
        try:
            await _send_mcp_request(
                client,
                url=str(config["url"]),
                headers=session_headers,
                method="initialize",
                params={
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "DeepFocus MCP Center", "version": "0.1.0"},
                },
            )
        except Exception:
            pass
        result = await _send_mcp_request(
            client,
            url=str(config["url"]),
            headers=session_headers,
            method="tools/call",
            params={"name": tool_name, "arguments": request.arguments},
            name=tool_name,
        )

    called_at = now_iso()
    return McpToolCallResponse(
        server=get_mcp_server(server.id) or server,
        tool_name=tool_name,
        arguments=request.arguments,
        result=result,
        content_preview=_content_preview(result),
        called_at=called_at,
    )


def _replace_capabilities(
    server_id: str,
    *,
    tools: list[dict[str, Any]],
    resources: list[dict[str, Any]],
    prompts: list[dict[str, Any]],
) -> list[McpCapabilityRecord]:
    timestamp = now_iso()
    rows: list[dict[str, Any]] = []

    for tool in tools:
        name = str(tool.get("name") or "").strip()
        if not name:
            continue
        rows.append(
            {
                "id": str(uuid.uuid4()),
                "server_id": server_id,
                "capability_type": "tool",
                "name": name,
                "title": tool.get("title"),
                "description": str(tool.get("description") or ""),
                "schema_json": json.dumps(
                    {
                        "inputSchema": tool.get("inputSchema") or {},
                        "outputSchema": tool.get("outputSchema") or {},
                    },
                    ensure_ascii=False,
                ),
                "uri": None,
                "mime_type": None,
                "metadata_json": json.dumps(tool, ensure_ascii=False),
                "discovered_at": timestamp,
            }
        )

    for resource in resources:
        uri = str(resource.get("uri") or "").strip()
        name = str(resource.get("name") or resource.get("title") or uri).strip()
        if not uri and not name:
            continue
        rows.append(
            {
                "id": str(uuid.uuid4()),
                "server_id": server_id,
                "capability_type": "resource",
                "name": name or uri,
                "title": resource.get("title"),
                "description": str(resource.get("description") or ""),
                "schema_json": json.dumps({}, ensure_ascii=False),
                "uri": uri or None,
                "mime_type": resource.get("mimeType"),
                "metadata_json": json.dumps(resource, ensure_ascii=False),
                "discovered_at": timestamp,
            }
        )

    for prompt in prompts:
        name = str(prompt.get("name") or "").strip()
        if not name:
            continue
        rows.append(
            {
                "id": str(uuid.uuid4()),
                "server_id": server_id,
                "capability_type": "prompt",
                "name": name,
                "title": prompt.get("title"),
                "description": str(prompt.get("description") or ""),
                "schema_json": json.dumps({"arguments": prompt.get("arguments") or []}, ensure_ascii=False),
                "uri": None,
                "mime_type": None,
                "metadata_json": json.dumps(prompt, ensure_ascii=False),
                "discovered_at": timestamp,
            }
        )

    with _connect() as conn:
        conn.execute("DELETE FROM mcp_capabilities WHERE server_id = ?", (server_id,))
        conn.executemany(
            """
            INSERT INTO mcp_capabilities (
                id, server_id, capability_type, name, title, description, schema_json,
                uri, mime_type, metadata_json, discovered_at
            ) VALUES (
                :id, :server_id, :capability_type, :name, :title, :description, :schema_json,
                :uri, :mime_type, :metadata_json, :discovered_at
            )
            """,
            rows,
        )
        conn.commit()
    return [_row_to_capability(row) for row in rows]


async def _safe_list_capabilities(
    client: httpx.AsyncClient,
    *,
    url: str,
    headers: dict[str, str],
    method: str,
    result_key: str,
    warnings: list[str],
) -> list[dict[str, Any]]:
    try:
        result = await _send_mcp_request(client, url=url, headers=headers, method=method, params={})
        value = result.get(result_key)
        return value if isinstance(value, list) else []
    except Exception as exc:
        warnings.append(f"{method} 失败：{_clean_error(exc)}")
        return []


async def _send_mcp_request(
    client: httpx.AsyncClient,
    *,
    url: str,
    headers: dict[str, str],
    method: str,
    params: dict[str, Any],
    name: Optional[str] = None,
) -> dict[str, Any]:
    request_id = str(uuid.uuid4())
    payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    response = await client.post(
        url,
        json=payload,
        headers=_mcp_headers(headers, method=method, name=name),
    )
    response.raise_for_status()
    session_id = response.headers.get("Mcp-Session-Id") or response.headers.get("mcp-session-id")
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    data = _json_from_response(response)
    if not isinstance(data, dict):
        raise ValueError("MCP server returned a non-object response")
    if data.get("error"):
        raise ValueError(json.dumps(data["error"], ensure_ascii=False))
    result = data.get("result") or {}
    return result if isinstance(result, dict) else {"value": result}


async def _send_mcp_notification(
    client: httpx.AsyncClient,
    *,
    url: str,
    headers: dict[str, str],
    method: str,
    params: dict[str, Any],
) -> None:
    payload = {"jsonrpc": "2.0", "method": method, "params": params}
    try:
        response = await client.post(
            url,
            json=payload,
            headers=_mcp_headers(headers, method=method),
        )
        response.raise_for_status()
    except Exception:
        return


def _json_from_response(response: httpx.Response) -> Any:
    content_type = response.headers.get("content-type", "").lower()
    if "text/event-stream" not in content_type:
        return response.json()

    events: list[str] = []
    for line in response.text.splitlines():
        if not line.startswith("data:"):
            continue
        value = line[5:].strip()
        if value and value != "[DONE]":
            events.append(value)
    if not events:
        return {}
    return json.loads(events[-1])


def _mcp_headers(headers: dict[str, str], *, method: str, name: Optional[str] = None) -> dict[str, str]:
    next_headers = {
        **headers,
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
        "Mcp-Method": method,
    }
    if name:
        next_headers["Mcp-Name"] = name
    return next_headers


def _request_headers(config: dict[str, Any]) -> dict[str, str]:
    headers = {str(key): str(value) for key, value in (config.get("headers") or {}).items() if str(key).strip()}
    return headers


def _ensure_tool_allowed(config: dict[str, Any], tool_name: str) -> None:
    allowed_tools = set(_normalize_names(config.get("allowed_tools") or []))
    blocked_tools = set(_normalize_names(config.get("blocked_tools") or []))
    if allowed_tools and tool_name not in allowed_tools:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="该工具不在 allow-list 内。")
    if tool_name in blocked_tools:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="该工具已被 block-list 禁用。")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _update_server(
    server_id: str,
    *,
    status: str,
    last_connected_at: Optional[str] = None,
    last_error: Optional[str] = None,
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            UPDATE mcp_servers
            SET status = ?,
                last_connected_at = COALESCE(?, last_connected_at),
                last_error = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (status, last_connected_at, last_error, now_iso(), server_id),
        )
        conn.commit()


def _raw_server_config(server_id: str) -> dict[str, Any]:
    with _connect() as conn:
        row = conn.execute("SELECT config_json FROM mcp_servers WHERE id = ?", (server_id,)).fetchone()
    return json.loads(row["config_json"] or "{}") if row else {}


def _row_to_server(row: dict[str, Any]) -> McpServerRecord:
    config = json.loads(row.get("config_json") or "{}")
    counts = _capability_counts(row["id"])
    return McpServerRecord(
        id=row["id"],
        name=row["name"],
        transport=row["transport"],
        description=row.get("description") or "",
        status=row.get("status") or "unknown",
        trust_level=row.get("trust_level") or "unknown",
        risk_level=row.get("risk_level") or "medium",
        approval_required=bool(row.get("approval_required")),
        enabled=bool(row.get("enabled")),
        config=_public_config(config),
        tool_count=counts["tool"],
        resource_count=counts["resource"],
        prompt_count=counts["prompt"],
        last_connected_at=row.get("last_connected_at"),
        last_error=row.get("last_error"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_capability(row: dict[str, Any]) -> McpCapabilityRecord:
    return McpCapabilityRecord(
        id=row["id"],
        server_id=row["server_id"],
        capability_type=row["capability_type"],
        name=row["name"],
        title=row.get("title"),
        description=row.get("description") or "",
        schema=json.loads(row.get("schema_json") or "{}"),
        uri=row.get("uri"),
        mime_type=row.get("mime_type"),
        metadata=json.loads(row.get("metadata_json") or "{}"),
        discovered_at=row["discovered_at"],
    )


def _capability_counts(server_id: str) -> dict[str, int]:
    counts = {"tool": 0, "resource": 0, "prompt": 0}
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT capability_type, COUNT(*) AS count
            FROM mcp_capabilities
            WHERE server_id = ?
            GROUP BY capability_type
            """,
            (server_id,),
        ).fetchall()
    for row in rows:
        if row["capability_type"] in counts:
            counts[row["capability_type"]] = int(row["count"])
    return counts


def _public_config(config: dict[str, Any]) -> dict[str, Any]:
    public = dict(config)
    public["headers"] = _redact_mapping(public.get("headers") or {})
    public["env"] = _redact_mapping(public.get("env") or {})
    return public


def _redact_mapping(value: dict[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, item in value.items():
        if any(hint in str(key).lower() for hint in SECRET_HEADER_HINTS):
            text = str(item)
            redacted[key] = f"{text[:6]}..." if text else ""
        else:
            redacted[key] = item
    return redacted


def _normalize_names(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    names: list[str] = []
    for value in values:
        clean = str(value).strip()
        if clean and clean not in names:
            names.append(clean)
    return names


def _content_preview(result: dict[str, Any]) -> str:
    content = result.get("content")
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("text"):
                    texts.append(str(item["text"]))
                elif item.get("type"):
                    texts.append(str(item.get("type")))
        if texts:
            return "\n".join(texts)[:1000]
    structured = result.get("structuredContent")
    if structured is not None:
        return json.dumps(structured, ensure_ascii=False)[:1000]
    return json.dumps(result, ensure_ascii=False)[:1000]


def _clean_error(exc: Exception) -> str:
    return str(exc).replace("\n", " ")[:500]
