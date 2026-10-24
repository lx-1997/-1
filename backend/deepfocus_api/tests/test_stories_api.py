"""故事 API 路由回归：匿名可读公共故事、登录叠加个人化、埋点限流与非法拦截、画像鉴权。

夹具把统一 storage、故事库、埋点库全部指到 tmp_path，并用 dependency_overrides 注入登录态，
测试不碰任何真实数据、不依赖真实鉴权。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from deepfocus_api import story_engine, story_events, storage, user_profile
from deepfocus_api import stories_api as api
from deepfocus_api.auth import optional_current_user, require_current_user

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPFOCUS_DATABASE_URL", f"sqlite:///{tmp_path / 'core.sqlite3'}")
    monkeypatch.setenv("DEEPFOCUS_STORY_DB_PATH", str(tmp_path / "stories.sqlite3"))
    monkeypatch.setenv("DEEPFOCUS_STORY_EVENTS_DB_PATH", str(tmp_path / "events.sqlite3"))
    monkeypatch.setenv("DEEPFOCUS_CONTENT_ONTOLOGY_DB_PATH", str(tmp_path / "ontology.sqlite3"))
    from deepfocus_api import securities_master
    monkeypatch.setattr(securities_master, "DB_PATH", tmp_path / "realtime.sqlite3")
    securities_master._ALIASES = None
    story_engine.reset_caches()
    storage.reset_engine_for_tests()
    storage.Base.metadata.create_all(storage.get_engine())
    story_engine.init_story_db()
    story_events.init_events_db()
    securities_master.upsert_securities([("600519", "贵州茅台", "CN"), ("300750", "宁德时代", "CN")])
    yield
    storage.reset_engine_for_tests()


@pytest.fixture
def app():
    @asynccontextmanager
    async def _lifespan(_app):
        yield
    application = FastAPI(lifespan=_lifespan)
    application.include_router(api.router)
    return application


def _seed_story(story_id="st1", *, symbol="600519", entity="贵州茅台", themes=None, mass=0.6):
    """直接落一条故事到公共库，绕过内容采集，专注测路由行为。

    用「列名 -> 值」字典动态拼 INSERT，避免手写占位符与 schema 漂移（少一列即报错）。
    """
    import json
    themes = themes or ["consumption"]
    seen = story_engine._iso(NOW - timedelta(hours=2))
    now_iso = story_engine._iso(NOW)
    row = {
        "story_id": story_id, "bucket": f"sec:{symbol}:general", "kind": "company",
        "title": f"{entity} 动态", "summary": "摘要", "symbol": symbol, "entity_name": entity,
        "market": "CN", "event_code": "price_change", "event_label": "价格变化",
        "theme_codes": json.dumps(themes), "theme_labels": json.dumps(["消费"]),
        "signal_code": "unconfirmed", "horizon_code": "unassigned", "impact": "",
        "verify_json": "[]", "mass": mass, "top_tier": "media", "recency": 0.9,
        "item_count": 3, "source_count": 2, "by_type_json": "{}", "by_role_json": "{}",
        "by_signal_json": "{}", "symbols_json": json.dumps([symbol]), "timeline_json": "[]",
        "first_seen": seen, "last_seen": now_iso, "generated_at": now_iso, "updated_at": now_iso,
    }
    cols = ", ".join(row)
    marks = ", ".join("?" * len(row))
    with story_engine._connect() as conn:
        conn.execute(f"INSERT OR REPLACE INTO stories ({cols}) VALUES ({marks})", tuple(row.values()))
        conn.commit()


def test_today_is_public_and_returns_stories(app):
    _seed_story()
    with TestClient(app) as client:
        r = client.get("/api/stories/today")
        assert r.status_code == 200
        body = r.json()
        assert body["stories"][0]["story_id"] == "st1"
        assert "brief" in body and "health" in body


def test_me_is_personalized_for_login_and_plain_for_anonymous(app):
    _seed_story("st1", symbol="600519", entity="贵州茅台", themes=["consumption"], mass=0.4)
    _seed_story("st2", symbol="300750", entity="宁德时代", themes=["energy"], mass=0.9)

    with TestClient(app) as client:
        anon = client.get("/api/stories/me")
        assert anon.status_code == 200
        assert anon.json()["personalized"] is False

    user_profile.set_profile("alice", theme_follows=["consumption"])
    app.dependency_overrides[optional_current_user] = lambda: {"sub": "alice"}
    with TestClient(app) as client:
        mine = client.get("/api/stories/me")
        assert mine.status_code == 200
        body = mine.json()
        assert body["personalized"] is True
        # 订阅消费主题 → 低 mass 的茅台反超高 mass 的宁德
        assert body["stories"][0]["story_id"] == "st1"
        assert body["stories"][0]["why_personal"]
    app.dependency_overrides.clear()


def test_symbol_route_matches_before_catch_all_detail(app):
    _seed_story("st1", symbol="600519")
    with TestClient(app) as client:
        r = client.get("/api/stories/symbol/600519")
        assert r.status_code == 200
        assert r.json()["symbol"] == "600519"
        # catch-all 详情路由仍能取到具体故事
        d = client.get("/api/stories/st1")
        assert d.status_code == 200 and d.json()["story_id"] == "st1"
        # 不存在的 id → 404，而不是被 symbol 路由吞掉
        assert client.get("/api/stories/nope").status_code == 404


def test_brief_text_and_json(app):
    _seed_story()
    with TestClient(app) as client:
        assert client.get("/api/stories/brief").status_code == 200
        text = client.get("/api/stories/brief?fmt=text")
        assert text.status_code == 200
        assert text.headers["content-type"].startswith("text/plain")


def test_event_requires_login_for_feedback_and_rate_limits(app):
    _seed_story()
    # 匿名写 feedback_down → 被 story_events 拒绝（400）
    with TestClient(app) as client:
        anon = client.post("/api/stories/event", json={"story_id": "st1", "action": "feedback_down"})
        assert anon.status_code == 400
        # 匿名写曝光 → 放行
        ok = client.post("/api/stories/event", json={"story_id": "st1", "action": "impression", "position": 0})
        assert ok.status_code == 200 and ok.json()["ok"] is True
        # 缺字段 → 400
        assert client.post("/api/stories/event", json={"action": "click"}).status_code == 400


def test_event_rate_limit_returns_429(app, monkeypatch):
    _seed_story()
    monkeypatch.setattr(api.write_guard, "check_rate", lambda kind, actor: False)
    with TestClient(app) as client:
        r = client.post("/api/stories/event", json={"story_id": "st1", "action": "click"})
        assert r.status_code == 429


def test_profile_requires_login(app):
    with TestClient(app) as client:
        # 未覆盖 require_current_user → 真实鉴权抛 401
        assert client.get("/api/profile").status_code == 401
        assert client.put("/api/profile", json={"markets": ["CN"]}).status_code == 401


def test_profile_roundtrip_when_logged_in(app):
    app.dependency_overrides[require_current_user] = lambda: {"sub": "bob"}
    with TestClient(app) as client:
        put = client.put("/api/profile", json={"markets": ["cn", "us"], "horizon": "mid",
                                               "risk": "balanced", "theme_follows": ["ai", "bogus"]})
        assert put.status_code == 200
        body = put.json()
        assert body["markets"] == ["CN", "US"]
        assert body["theme_follows"] == ["ai"]        # bogus 被丢弃
        got = client.get("/api/profile")
        assert got.status_code == 200 and got.json()["horizon"] == "mid"
    app.dependency_overrides.clear()
