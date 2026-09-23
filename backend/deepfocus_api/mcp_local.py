"""Built-in read-only Streamable HTTP MCP endpoint.

This is deliberately small: it provides a real protocol endpoint for the MCP
center and exposes only safe, read-only market-data capabilities.  More
capabilities can be added here without making the MCP hub depend on frontend
routes or on a separate process.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from .market_data import fetch_market_quotes, search_market_symbols


PROTOCOL_VERSION = os.getenv("DEEPFOCUS_MCP_PROTOCOL_VERSION", "2025-06-18")
LOCAL_TOKEN = os.getenv("DEEPFOCUS_MCP_LOCAL_TOKEN", "").strip()


TOOLS: list[dict[str, Any]] = [
    {
        "name": "ping",
        "title": "连接探针",
        "description": "验证 DeepFocus 内置 MCP 服务在线。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "search_market_symbols",
        "title": "搜索标的",
        "description": "按名称、代码或关键词搜索股票标的。只读。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "股票名称、代码或关键词"},
                "market": {"type": "string", "description": "可选：US、CN 或 HK"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_market_quotes",
        "title": "获取行情",
        "description": "获取一个或多个标的的公开行情快照。只读。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbols": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "标的代码列表，例如 [AAPL, 600519.SH]",
                }
            },
            "required": ["symbols"],
            "additionalProperties": False,
        },
    },
]


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def _rpc_error(request_id: Any, code: int, message: str) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}},
        status_code=200,
        headers={"Mcp-Session-Id": str(uuid.uuid4())},
    )


def _result(request_id: Any, result: dict[str, Any], session_id: str) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": request_id, "result": result},
        headers={"Mcp-Session-Id": session_id},
    )


def _text_result(value: Any) -> dict[str, Any]:
    payload = _jsonable(value)
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        "structuredContent": {"data": payload},
        "isError": False,
    }


async def handle_local_mcp_request(request: Request) -> JSONResponse:
    """Handle one JSON-RPC request for the built-in MCP server."""
    if LOCAL_TOKEN and request.headers.get("X-MCP-Token", "") != LOCAL_TOKEN:
        return JSONResponse({"detail": "MCP token invalid"}, status_code=401)

    try:
        payload = await request.json()
    except Exception:
        return _rpc_error(None, -32700, "Invalid JSON")

    if not isinstance(payload, dict):
        return _rpc_error(None, -32600, "Request must be a JSON object")

    request_id = payload.get("id")
    method = str(payload.get("method") or "")
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        return _rpc_error(request_id, -32602, "Params must be an object")

    # Notifications do not receive a JSON-RPC result.  Returning 202 keeps
    # streamable-http clients happy while still making the endpoint easy to
    # exercise with curl/httpx.
    if request_id is None:
        return JSONResponse({}, status_code=202)

    session_id = request.headers.get("Mcp-Session-Id") or str(uuid.uuid4())
    if method == "initialize":
        return _result(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "DeepFocus Built-in MCP", "version": "0.1.0"},
                "instructions": "只读行情能力，适合投研检索和连通性测试。",
            },
            session_id,
        )
    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS}, session_id)
    if method == "resources/list":
        return _result(request_id, {"resources": []}, session_id)
    if method == "prompts/list":
        return _result(request_id, {"prompts": []}, session_id)
    if method != "tools/call":
        return _rpc_error(request_id, -32601, f"Method not found: {method}")

    tool_name = str(params.get("name") or "")
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        return _rpc_error(request_id, -32602, "Tool arguments must be an object")

    try:
        if tool_name == "ping":
            value = {"ok": True, "service": "DeepFocus Built-in MCP", "protocolVersion": PROTOCOL_VERSION}
        elif tool_name == "search_market_symbols":
            query = str(arguments.get("query") or "").strip()
            if not query:
                return _rpc_error(request_id, -32602, "query is required")
            value = await search_market_symbols(query, market=str(arguments.get("market") or "") or None)
        elif tool_name == "get_market_quotes":
            symbols = arguments.get("symbols")
            if isinstance(symbols, str):
                symbols = [item.strip() for item in symbols.split(",") if item.strip()]
            if not isinstance(symbols, list) or not symbols:
                return _rpc_error(request_id, -32602, "symbols must be a non-empty array")
            value = await fetch_market_quotes([str(item) for item in symbols[:30]])
        else:
            return _rpc_error(request_id, -32602, f"Unknown tool: {tool_name}")
    except Exception as exc:  # noqa: BLE001 - MCP errors must remain JSON-RPC errors
        return _result(request_id, {**_text_result({"error": str(exc)}), "isError": True}, session_id)

    return _result(request_id, _text_result(value), session_id)
