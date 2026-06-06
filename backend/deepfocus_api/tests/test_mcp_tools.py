"""外部 MCP 工具动态接入（mcp_tools.discover_mcp_agent_tools）的回归守卫。

锁定安全红线（autonomous agent 只见 启用+免审批+streamable_http 的工具）、名字净化/去重、
调用一律 approved=False、以及失败 best-effort 降级为空。用 SimpleNamespace 桩，不触 sqlite/网络。
"""
from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace

from deepfocus_api import mcp_tools


def _server(**kw):
    base = dict(id="srv1", name="FS", transport="streamable_http", enabled=True, approval_required=False)
    base.update(kw)
    return SimpleNamespace(**base)


def _cap(name, description="", schema=None):
    return SimpleNamespace(name=name, description=description, schema_=schema or {})


def test_discover_filters_disabled_approval_and_non_http(monkeypatch):
    servers = [
        _server(id="ok", name="OK"),                     # ✓ 暴露
        _server(id="disabled", enabled=False),           # ✗ 停用
        _server(id="approval", approval_required=True),  # ✗ 需人工审批（安全红线）
        _server(id="stdio", transport="stdio"),          # ✗ 非 streamable_http
    ]
    caps = {
        "ok": [_cap("read_file", "读文件", {"type": "object", "properties": {"path": {"type": "string"}}})],
        "disabled": [_cap("x")], "approval": [_cap("y")], "stdio": [_cap("z")],
    }
    monkeypatch.setattr(mcp_tools, "list_mcp_servers", lambda: servers)
    monkeypatch.setattr(mcp_tools, "list_mcp_capabilities", lambda *, server_id, capability_type: caps.get(server_id, []))

    tools = asyncio.run(mcp_tools.discover_mcp_agent_tools())

    assert len(tools) == 1
    tool = tools[0]
    assert tool.name.startswith("mcp__ok__read_file")
    assert tool.parameters["properties"]["path"]["type"] == "string"
    assert tool.description.startswith("[MCP·OK]")


def test_empty_schema_defaults_to_object(monkeypatch):
    monkeypatch.setattr(mcp_tools, "list_mcp_servers", lambda: [_server(id="ok", name="OK")])
    monkeypatch.setattr(mcp_tools, "list_mcp_capabilities", lambda *, server_id, capability_type: [_cap("ping")])
    tools = asyncio.run(mcp_tools.discover_mcp_agent_tools())
    assert tools[0].parameters == {"type": "object", "properties": {}}


def test_mcp_handler_calls_call_mcp_tool_with_approved_false(monkeypatch):
    captured = {}

    async def fake_call(server_id, request):
        captured.update(
            server_id=server_id,
            tool_name=request.tool_name,
            arguments=request.arguments,
            approved=request.approved,
        )
        return SimpleNamespace(result={"content": "hello"}, content_preview="hello")

    monkeypatch.setattr(mcp_tools, "list_mcp_servers", lambda: [_server(id="ok", name="OK")])
    monkeypatch.setattr(mcp_tools, "list_mcp_capabilities", lambda *, server_id, capability_type: [_cap("read_file")])
    monkeypatch.setattr(mcp_tools, "call_mcp_tool", fake_call)

    tools = asyncio.run(mcp_tools.discover_mcp_agent_tools())
    out = asyncio.run(tools[0].handler(path="/tmp/a"))

    assert captured["server_id"] == "ok"
    assert captured["tool_name"] == "read_file"
    assert captured["arguments"] == {"path": "/tmp/a"}
    assert captured["approved"] is False  # 安全红线：agent 自主调用绝不冒充人工批准
    assert out["result"] == {"content": "hello"}


def test_tool_name_sanitized_and_unique(monkeypatch):
    monkeypatch.setattr(mcp_tools, "list_mcp_servers", lambda: [_server(id="s/1", name="S")])
    monkeypatch.setattr(
        mcp_tools, "list_mcp_capabilities",
        lambda *, server_id, capability_type: [_cap("a.b"), _cap("a.b")],  # 同名+非法字符
    )
    tools = asyncio.run(mcp_tools.discover_mcp_agent_tools())
    names = [t.name for t in tools]
    assert len(names) == len(set(names)) == 2  # 去重
    assert all(re.fullmatch(r"[A-Za-z0-9_-]+", n) for n in names)  # 合法 OpenAI 工具名


def test_discover_empty_on_server_list_failure(monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(mcp_tools, "list_mcp_servers", boom)
    assert asyncio.run(mcp_tools.discover_mcp_agent_tools()) == []


def test_discover_skips_server_whose_caps_fail(monkeypatch):
    monkeypatch.setattr(mcp_tools, "list_mcp_servers", lambda: [_server(id="bad", name="B"), _server(id="ok", name="OK")])

    def caps(*, server_id, capability_type):
        if server_id == "bad":
            raise RuntimeError("caps fail")
        return [_cap("go")]

    monkeypatch.setattr(mcp_tools, "list_mcp_capabilities", caps)
    tools = asyncio.run(mcp_tools.discover_mcp_agent_tools())
    assert [t.name for t in tools] == ["mcp__ok__go"]  # 坏服务器被跳过，好的仍可用
