"""内置 Streamable HTTP MCP 端点的协议级冒烟测试。"""

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from deepfocus_api.mcp_local import handle_local_mcp_request


def _client() -> TestClient:
    app = FastAPI()

    @app.post("/mcp")
    async def mcp(request: Request):
        return await handle_local_mcp_request(request)

    return TestClient(app)


def test_builtin_mcp_initialize_discover_and_ping():
    with _client() as client:
        initialized = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test"}},
            },
        )
        assert initialized.status_code == 200
        assert initialized.json()["result"]["serverInfo"]["name"] == "DeepFocus Built-in MCP"
        session_id = initialized.headers["Mcp-Session-Id"]

        listed = client.post(
            "/mcp",
            headers={"Mcp-Session-Id": session_id},
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        assert {tool["name"] for tool in listed.json()["result"]["tools"]} == {
            "ping",
            "search_market_symbols",
            "get_market_quotes",
        }

        ping = client.post(
            "/mcp",
            headers={"Mcp-Session-Id": session_id},
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "ping", "arguments": {}},
            },
        )
        assert ping.status_code == 200
        assert ping.json()["result"]["structuredContent"]["data"]["ok"] is True


def test_builtin_mcp_rejects_unknown_tool():
    with _client() as client:
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "delete_everything", "arguments": {}},
            },
        )
        assert response.status_code == 200
        assert response.json()["error"]["code"] == -32602
