"""Capability policy checks at the API/assistant boundary."""
from __future__ import annotations

from copy import deepcopy

from deepfocus_api.platform_capabilities import boundary_summary, capability_catalog, get_capability


class SchemaApp:
    def __init__(self, document):
        self.document = document

    def openapi(self):
        return self.document


def test_new_or_nearby_routes_are_not_implicitly_callable():
    app = SchemaApp({"paths": {
        "/api/me/watchlist": {"get": {}, "post": {}, "delete": {}},
        "/api/me/watchlist/admin": {"get": {}},
        "/api/new-future-operation": {"post": {}},
        "/api/admin/reset-password": {"post": {}},
        "/api/agents/tool-research": {"post": {}},
    }})
    assert {item["id"] for item in capability_catalog(app)} == {
        "get:/api/me/watchlist", "post:/api/me/watchlist",
    }
    assert get_capability(app, "delete:/api/me/watchlist") is None


def test_resolves_nested_inputs_without_mutating_the_openapi_document():
    document = {"paths": {"/api/me/watchlist": {"post": {
        "requestBody": {"$ref": "#/components/requestBodies/Watchlist"},
    }}}, "components": {
        "requestBodies": {"Watchlist": {"required": True, "content": {
            "application/json": {"schema": {"$ref": "#/components/schemas/Watchlist"}},
        }}},
        "schemas": {"Watchlist": {"type": "object", "properties": {
            "symbols": {"type": "array", "items": {"$ref": "#/components/schemas/Symbol"}},
        }}, "Symbol": {"type": "string", "minLength": 1}},
    }}
    original = deepcopy(document)
    app = SchemaApp(document)
    capability = get_capability(app, "post:/api/me/watchlist")
    schema = capability["parameters"]["requestBody"]["content"]["application/json"]["schema"]
    assert schema["properties"]["symbols"]["items"] == {"type": "string", "minLength": 1}
    schema["properties"]["symbols"]["items"]["minLength"] = 99
    assert app.document == original
    assert get_capability(app, capability["id"])["parameters"]["requestBody"]["content"]["application/json"]["schema"]["properties"]["symbols"]["items"]["minLength"] == 1


def test_broken_or_non_json_reviewed_route_is_closed():
    app = SchemaApp({"paths": {
        "/api/me/watchlist": {"post": {"requestBody": {"$ref": "#/missing"}}},
        "/api/agents/tasks": {"post": {"requestBody": {"content": {
            "multipart/form-data": {"schema": {"type": "object"}},
        }}}},
    }})
    assert capability_catalog(app) == []


def test_real_catalog_covers_user_domains_and_preserves_action_boundaries():
    from deepfocus_api.main import app

    catalog = capability_catalog(app)
    by_id = {item["id"]: item for item in catalog}
    assert len(by_id) == len(catalog)
    representatives = {
        "account": "get:/api/auth/me", "watchlist": "post:/api/me/watchlist",
        "market": "get:/api/market/quotes", "content": "get:/api/realtime/messages",
        "analysis": "post:/api/skills/cn-earnings/diagnose",
        "tasks": "post:/api/agents/tasks", "portfolio": "get:/api/risk/summary",
        "backtest": "post:/api/backtest", "alerts": "post:/api/realtime/recall/subscribe-email",
        "data_sources": "post:/api/data-sources", "research_library": "post:/api/pro-research/reports/ingest-item",
        "sharing": "post:/api/report/share", "simulation": "get:/api/ai-fund/snapshot",
    }
    for group, capability in representatives.items():
        assert by_id[capability]["group"] == group
    assert by_id["post:/api/me/watchlist"]["effect"] == "write"
    assert by_id["post:/api/risk/positions"]["effect"] == "confirm"
    assert by_id["post:/api/realtime/recall/subscribe-email"]["effect"] == "confirm"
    for item in catalog:
        if item["method"] == "DELETE":
            assert item["effect"] == "confirm"
        if item["shared"] and item["effect"] != "read":
            assert item["effect"] == "confirm"
            assert item["required_role"] == "admin"


def test_real_catalog_excludes_credentials_recursive_chat_binary_and_device_actions():
    from deepfocus_api.main import app

    by_id = {item["id"]: item for item in capability_catalog(app)}
    excluded = {
        "get:/api/fingpt/model-config", "post:/api/fingpt/model-config",
        "get:/api/mcp/servers", "post:/api/mcp/servers/{server_id}/tools/call",
        "post:/api/account/mcp-tokens", "post:/api/auth/login",
        "post:/api/agents/tool-research", "post:/api/agents/orchestrator-chat",
        "get:/api/research/wire-file", "get:/api/research/pdf-token",
        "get:/api/realtime/messages/{message_id}/original-text",
        "post:/api/backtest/{backtest_id}/run", "post:/api/data-sources/upload",
        "post:/api/realtime/recall/subscriptions", "post:/api/weixin/bind/start",
    }
    assert not excluded.intersection(by_id)
    interactions = {item["id"]: item for item in boundary_summary(app)["frontend_interactions"]}
    assert interactions["post:/api/backtest/{backtest_id}/run"]["frontend_interaction"] is True
    assert interactions["post:/api/realtime/recall/subscriptions"]["enabled"] is False


def test_raw_request_inputs_are_documented_and_memory_is_scoped_to_account():
    from deepfocus_api.main import app

    bookmark = get_capability(app, "post:/api/me/bookmark")
    body = bookmark["parameters"]["requestBody"]["content"]["application/json"]["schema"]
    assert body["required"] == ["message_id"]
    assert body["additionalProperties"] is False
    memory = get_capability(app, "post:/api/dulus/memory")
    body = memory["parameters"]["requestBody"]["content"]["application/json"]["schema"]
    assert body["properties"]["scope"]["enum"] == ["user"]
    assert "scope" in body["required"]
    read_memory = get_capability(app, "get:/api/dulus/memory")
    scope = next(p for p in read_memory["parameters"]["parameters"] if p["name"] == "scope")
    assert scope["required"] is True
    assert scope["schema"]["enum"] == ["user"]
