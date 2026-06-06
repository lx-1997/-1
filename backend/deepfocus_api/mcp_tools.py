"""把已配置的外部 MCP 工具动态接入 tool-use agent。

与 agent_tools.py 的静态内部工具不同，MCP 工具是用户运行时配置的（存于 mcp_hub 的 sqlite），
所以这里在 agent 每次运行时**动态发现**，而非 import 期注册。

安全红线（autonomous agent 不应越过人工闸门）：只暴露
  ① 已启用（enabled）② streamable_http 传输（call_mcp_tool 仅支持此种）③ 无需人工审批（approval_required=False）
的服务器的工具。approval_required 的服务器**永不**被 agent 自主调用；调用一律传 approved=False（诚实：
无人工批准）。call_mcp_tool 内部还有 _ensure_tool_allowed 逐工具白名单做纵深防御。
每个 MCP 工具失败都收敛成异常→上层 execute_tool 包成 {"ok": False, ...}，不拖垮 agent 循环。
"""
from __future__ import annotations

import re
from typing import Any

from .agent_tools import AgentTool
from .mcp_hub import call_mcp_tool, list_mcp_capabilities, list_mcp_servers
from .schemas import McpToolCallRequest


def _safe_tool_name(server_id: str, tool_name: str, used: set[str]) -> str:
    """生成符合 OpenAI 工具名约束（^[a-zA-Z0-9_-]+$、≤64）且全局唯一的名字。
    dispatch 不靠名字反解（handler 闭包已捕获 server_id+tool_name），故只需唯一+合法。"""
    raw = f"mcp__{server_id}__{tool_name}"
    base = re.sub(r"[^a-zA-Z0-9_-]", "_", raw)[:64]
    candidate = base
    suffix = 1
    while candidate in used:
        tag = f"_{suffix}"
        candidate = base[: 64 - len(tag)] + tag
        suffix += 1
    used.add(candidate)
    return candidate


def _make_mcp_handler(server_id: str, tool_name: str):
    async def handler(**arguments: Any) -> Any:
        resp = await call_mcp_tool(
            server_id,
            # approved=False：agent 自主调用不代表人工批准；本就只对免审批服务器开放。
            McpToolCallRequest(tool_name=tool_name, arguments=dict(arguments), approved=False),
        )
        return {"result": resp.result, "preview": resp.content_preview}

    return handler


async def discover_mcp_agent_tools() -> list[AgentTool]:
    """枚举可被 agent 自主调用的 MCP 工具。失败/无配置 → []（best-effort，绝不抛断 agent）。"""
    try:
        servers = list_mcp_servers()
    except Exception:
        return []

    tools: list[AgentTool] = []
    used: set[str] = set()
    for server in servers:
        if not server.enabled or server.approval_required or server.transport != "streamable_http":
            continue
        try:
            capabilities = list_mcp_capabilities(server_id=server.id, capability_type="tool")
        except Exception:
            continue
        for cap in capabilities:
            params = cap.schema_ if isinstance(cap.schema_, dict) and cap.schema_ else {"type": "object", "properties": {}}
            tools.append(AgentTool(
                name=_safe_tool_name(server.id, cap.name, used),
                description=f"[MCP·{server.name}] {cap.description or cap.name}"[:1024],
                parameters=params,
                handler=_make_mcp_handler(server.id, cap.name),
            ))
    return tools
