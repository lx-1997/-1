"""题材/产业链导航——概念板块榜 + 题材→受益股(顺藤摸瓜) + 个股题材 + agent 工具。"""
import asyncio

from fastapi.testclient import TestClient

from deepfocus_api import theme_navigation as tn
from deepfocus_api.agent_tools import TOOL_REGISTRY, execute_tool
from deepfocus_api.auth import is_public_path
from deepfocus_api.main import app

client = TestClient(app)

_BOARDS = [{"code": "BK0917", "name": "半导体概念", "pct": 3.2, "leader": "中芯国际", "leader_code": "688981", "leader_pct": 10.0}]
_STOCKS = [
    {"code": "688981", "name": "中芯国际", "price": 50.1, "pct": 5.5},
    {"code": "002049", "name": "紫光国微", "price": 80.0, "pct": 3.1},
]


def test_themes_boards(monkeypatch):
    async def fake(limit=40):
        return _BOARDS
    monkeypatch.setattr(tn, "fetch_concept_boards", fake)
    r = client.get("/api/themes/boards")
    assert r.status_code == 200
    b = r.json()
    assert b["count"] == 1 and b["boards"][0]["name"] == "半导体概念" and b["is_realtime"] is False


def test_themes_detail_by_name(monkeypatch):
    async def fake_find(name):
        assert name == "半导体"
        return {"code": "BK0917", "name": "半导体概念"}

    async def fake_stocks(code, limit=40):
        assert code == "BK0917"
        return _STOCKS
    monkeypatch.setattr(tn, "find_board_by_name", fake_find)
    monkeypatch.setattr(tn, "fetch_board_stocks", fake_stocks)
    r = client.get("/api/themes/detail", params={"q": "半导体"})
    assert r.status_code == 200
    b = r.json()
    assert b["board"]["code"] == "BK0917" and b["count"] == 2 and b["stocks"][0]["name"] == "中芯国际"


def test_themes_detail_by_code(monkeypatch):
    async def fake_pairs():
        return [{"code": "BK0917", "name": "半导体概念"}]

    async def fake_stocks(code, limit=40):
        return _STOCKS
    monkeypatch.setattr(tn, "_board_name_pairs", fake_pairs)
    monkeypatch.setattr(tn, "fetch_board_stocks", fake_stocks)
    r = client.get("/api/themes/detail", params={"code": "bk0917"})
    assert r.status_code == 200
    assert r.json()["board"]["name"] == "半导体概念"


def test_themes_detail_bad_code():
    assert client.get("/api/themes/detail", params={"code": "XYZ"}).status_code == 400


def test_themes_detail_needs_arg():
    assert client.get("/api/themes/detail").status_code == 400


def test_themes_detail_not_found(monkeypatch):
    async def fake_find(name):
        return None
    monkeypatch.setattr(tn, "find_board_by_name", fake_find)
    r = client.get("/api/themes/detail", params={"q": "不存在的题材"})
    assert r.status_code == 200 and r.json()["board"] is None


def test_themes_stock(monkeypatch):
    async def fake(symbol):
        return {"symbol": "600519", "name": "贵州茅台", "industry": "白酒Ⅱ", "board": "贵州板块"}
    monkeypatch.setattr(tn, "fetch_stock_themes", fake)
    r = client.get("/api/themes/stock", params={"symbol": "600519"})
    assert r.status_code == 200 and r.json()["industry"] == "白酒Ⅱ"


def test_themes_public_paths():
    for p in ("/api/themes/boards", "/api/themes/detail", "/api/themes/stock"):
        assert is_public_path(p) is True


def test_agent_tool_theme_stocks(monkeypatch):
    async def fake_find(name):
        return {"code": "BK0917", "name": "半导体概念"}

    async def fake_stocks(code, limit=12):
        return _STOCKS
    monkeypatch.setattr(tn, "find_board_by_name", fake_find)
    monkeypatch.setattr(tn, "fetch_board_stocks", fake_stocks)
    assert "get_theme_stocks" in TOOL_REGISTRY
    out = asyncio.run(execute_tool("get_theme_stocks", {"theme": "半导体"}))
    assert out["ok"] and out["data"]["kind"] == "theme_stocks"
    assert out["data"]["theme"] == "半导体概念" and out["data"]["stocks"][0]["name"] == "中芯国际"


def test_agent_tool_theme_ranking(monkeypatch):
    async def fake_boards(limit=12):
        return _BOARDS
    monkeypatch.setattr(tn, "fetch_concept_boards", fake_boards)
    out = asyncio.run(execute_tool("get_theme_stocks", {}))
    assert out["ok"] and out["data"]["kind"] == "board_ranking" and out["data"]["boards"][0]["name"] == "半导体概念"


def test_agent_tool_theme_not_found(monkeypatch):
    async def fake_find(name):
        return None
    monkeypatch.setattr(tn, "find_board_by_name", fake_find)
    out = asyncio.run(execute_tool("get_theme_stocks", {"theme": "查无此题材zzz"}))
    assert out["ok"] and out["data"]["kind"] == "not_found"


def test_board_code_validation(monkeypatch):
    # fetch_board_stocks 只接受 BK\d+，防注入/乱传
    monkeypatch.setattr(tn, "_CACHE", {})
    assert asyncio.run(tn.fetch_board_stocks("'; DROP")) == []
    assert asyncio.run(tn.fetch_board_stocks("")) == []


_LADDER = {"date": "20260626", "count": 2, "ladder": [
    {"code": "002674", "name": "兴业科技", "pct": 10.0, "boards": 6, "days_ct": "6天6板", "industry": "纺织制造", "broke": 0},
    {"code": "605366", "name": "宏柏新材", "pct": 10.02, "boards": 4, "days_ct": "4天4板", "industry": "化学制品", "broke": 31},
]}


def test_themes_limit_up(monkeypatch):
    async def fake(limit=60):
        return _LADDER
    monkeypatch.setattr(tn, "fetch_limit_up_ladder", fake)
    r = client.get("/api/themes/limit-up")
    assert r.status_code == 200
    b = r.json()
    assert b["count"] == 2 and b["date"] == "20260626" and b["ladder"][0]["boards"] == 6


def test_limit_up_public_path():
    assert is_public_path("/api/themes/limit-up") is True


def test_agent_tool_limit_up(monkeypatch):
    async def fake(limit=30):
        return _LADDER
    monkeypatch.setattr(tn, "fetch_limit_up_ladder", fake)
    out = asyncio.run(execute_tool("get_limit_up_ladder", {}))
    assert out["ok"] and out["data"]["ladder"][0]["name"] == "兴业科技" and out["data"]["ladder"][0]["boards"] == 6


def test_ladder_sorted_by_boards_desc():
    # 真实调用：连板数降序（首项 boards >= 末项）
    import deepfocus_api.theme_navigation as _tn
    _tn._CACHE.pop("zt_ladder", None)
    d = asyncio.run(_tn.fetch_limit_up_ladder(limit=10))
    if d["ladder"] and len(d["ladder"]) >= 2:  # 非交易日数据源可能空，空则跳过断言
        assert d["ladder"][0]["boards"] >= d["ladder"][-1]["boards"]
