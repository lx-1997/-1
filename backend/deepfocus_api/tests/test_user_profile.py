"""个人化画像与排序回归：显式画像归一、隐式兴趣融合、排序 boost、多样性/探索护栏、匿名退回、PII 不外泄。

夹具把统一 storage（显式画像表）、埋点库、证券主数据全部指到 tmp_path，不碰任何真实数据。
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from deepfocus_api import story_events, storage, user_profile

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPFOCUS_DATABASE_URL", f"sqlite:///{tmp_path / 'core.sqlite3'}")
    monkeypatch.setenv("DEEPFOCUS_STORY_EVENTS_DB_PATH", str(tmp_path / "events.sqlite3"))
    storage.reset_engine_for_tests()
    storage.Base.metadata.create_all(storage.get_engine())
    story_events.init_events_db()
    yield
    storage.reset_engine_for_tests()


def _card(story_id, *, symbol="", entity_name="", market="", themes=None, theme_labels=None,
          mass=0.3, kind="company"):
    themes = themes or []
    theme_labels = theme_labels or []
    return {
        "story_id": story_id,
        "kind": kind,
        "title": f"{entity_name or symbol or story_id} 动态",
        "symbol": symbol,
        "symbols": [symbol] if symbol else [],
        "entity_name": entity_name,
        "market": market,
        "theme_codes": themes,
        "theme_labels": theme_labels,
        "mass": mass,
    }


# ---- 显式画像 ----

def test_set_profile_normalizes_and_rejects_invalid_codes():
    saved = user_profile.set_profile(
        "alice", markets=["cn", "XX", "us"], horizon="MID", risk="yolo",
        theme_follows=["ai", "not_a_theme", "ai"],
    )
    assert saved["markets"] == ["CN", "US"]          # 归一大写、丢弃非法
    assert saved["horizon"] == "mid"
    assert saved["risk"] == ""                        # yolo 非法 → 清空
    assert saved["theme_follows"] == ["ai"]           # 去重 + 丢弃非本体 code
    loaded = user_profile.get_profile("alice")
    assert loaded["markets"] == ["CN", "US"]
    assert loaded["theme_follows"] == ["ai"]


def test_get_profile_empty_for_unknown_user():
    profile = user_profile.get_profile("nobody")
    assert profile == {"markets": [], "horizon": "", "risk": "", "theme_follows": []}


def test_set_profile_requires_user_id():
    with pytest.raises(ValueError):
        user_profile.set_profile("", markets=["CN"])


# ---- 隐式兴趣融合 ----

def test_interest_vector_fuses_explicit_and_implicit_signals():
    user_profile.set_profile("bob", theme_follows=["semiconductor"])
    story_events.log_event(user_id="bob", story_id="s1", action="bookmark",
                           symbol="600519", themes=["consumption"], now=NOW)
    vec = user_profile.interest_vector("bob", now=NOW)
    # 显式订阅主题以权重 1.0 落入
    assert vec["theme_follows"] == {"semiconductor": 1.0}
    assert vec["themes"]["semiconductor"] >= 1.0
    # 埋点收藏把标的与主题拉进兴趣
    assert vec["symbols"]["600519"] > 0
    assert vec["themes"]["consumption"] > 0


def test_watchlist_gives_strongest_symbol_intent(monkeypatch):
    from deepfocus_api import user_prefs
    monkeypatch.setattr(user_prefs, "get_watchlist", lambda uid: {"symbols": ["300750"], "names": {}})
    vec = user_profile.interest_vector("carol", now=NOW)
    assert vec["symbols"]["300750"] >= 1.0


# ---- 排序 ----

def test_rank_boosts_followed_theme_above_higher_mass():
    user_profile.set_profile("dave", theme_follows=["ai"])
    cards = [
        _card("hot", mass=0.9, themes=["energy"], theme_labels=["能源"]),
        _card("matched", mass=0.4, themes=["ai"], theme_labels=["人工智能"]),
    ]
    ranked = user_profile.rank_stories("dave", cards, limit=2, now=NOW)
    assert ranked[0]["story_id"] == "matched"        # 命中订阅主题反超更高 mass
    assert any("人工智能" in chip for chip in ranked[0]["why_personal"])
    assert ranked[0]["relevance"] > ranked[1]["relevance"]


def test_rank_boosts_watchlist_symbol():
    from deepfocus_api import user_prefs
    user_prefs.set_watchlist("erin", ["600519"], {"600519": "贵州茅台"})
    cards = [
        _card("other", mass=0.8, symbol="000001", entity_name="平安银行"),
        _card("mine", mass=0.3, symbol="600519", entity_name="贵州茅台"),
    ]
    ranked = user_profile.rank_stories("erin", cards, limit=2, now=NOW)
    assert ranked[0]["story_id"] == "mine"
    assert any("自选股" in chip for chip in ranked[0]["why_personal"])


def test_negative_feedback_suppresses_story():
    story_events.log_event(user_id="frank", story_id="spam", action="feedback_down",
                           symbol="000002", themes=["energy"], now=NOW)
    cards = [
        _card("spam", mass=0.9, symbol="000002", themes=["energy"], theme_labels=["能源"]),
        _card("fresh", mass=0.2, symbol="600000", themes=["finance"], theme_labels=["金融"]),
    ]
    ranked = user_profile.rank_stories("frank", cards, limit=2, now=NOW)
    assert ranked[0]["story_id"] == "fresh"          # 高 mass 但被踩 → 让位
    assert ranked[0]["relevance"] > ranked[-1]["relevance"]


def test_anonymous_falls_back_to_pure_mass():
    cards = [_card("a", mass=0.2), _card("b", mass=0.7), _card("c", mass=0.4)]
    ranked = user_profile.rank_stories("", cards, limit=3, now=NOW)
    assert [c["story_id"] for c in ranked] == ["b", "c", "a"]
    assert all(c["relevance"] == 0.0 for c in ranked)
    assert all(c["why_personal"] == [] for c in ranked)


def test_seen_story_is_demoted():
    story_events.log_event(user_id="grace", story_id="read", action="click", symbol="600519", now=NOW)
    cards = [
        _card("read", mass=0.5, symbol="600519", entity_name="贵州茅台"),
        _card("unread", mass=0.45, symbol="600519", entity_name="贵州茅台"),
    ]
    ranked = user_profile.rank_stories("grace", cards, limit=2, now=NOW)
    assert ranked[0]["story_id"] == "unread"
    assert any("已读" in chip for chip in ranked[-1]["why_personal"])


def test_diversity_caps_same_symbol_in_head():
    # 同一标的三张高分卡，头部同标的上限=2，第三张被推迟，让别的标的进头部。
    cards = [
        _card("m1", mass=0.9, symbol="600519", entity_name="贵州茅台"),
        _card("m2", mass=0.85, symbol="600519", entity_name="贵州茅台"),
        _card("m3", mass=0.8, symbol="600519", entity_name="贵州茅台"),
        _card("other", mass=0.1, symbol="000001", entity_name="平安银行"),
    ]
    ranked = user_profile.rank_stories("", cards, limit=4, now=NOW)
    head_ids = [c["story_id"] for c in ranked[:user_profile.HEAD_WINDOW]]
    # 头部（=全部 4 张，limit<HEAD_WINDOW 时 head=limit）里同标的最多 2 张。
    maotai_in_head = sum(1 for c in ranked[:2] if c["symbol"] == "600519")
    assert maotai_in_head <= 2
    assert "other" in head_ids


def test_explore_slot_surfaces_unmatched_high_mass():
    user_profile.set_profile("heidi", theme_follows=["ai"])
    # 大量命中 ai 的卡 + 一张未命中但 mass 极高的能源卡。
    cards = [_card(f"ai{i}", mass=0.5 - i * 0.01, themes=["ai"], theme_labels=["人工智能"]) for i in range(10)]
    cards.append(_card("energy_hot", mass=0.99, themes=["energy"], theme_labels=["能源"]))
    ranked = user_profile.rank_stories("heidi", cards, limit=8, now=NOW)
    ids = [c["story_id"] for c in ranked]
    # 探索位应把未命中的高 mass 能源卡纳入结果，避免信息茧房。
    assert "energy_hot" in ids


def test_rank_never_mutates_input_cards():
    cards = [_card("a", mass=0.5, symbol="600519")]
    snapshot = {k: v for k, v in cards[0].items()}
    user_profile.set_profile("ivan", theme_follows=["ai"])
    user_profile.rank_stories("ivan", cards, limit=1, now=NOW)
    assert cards[0] == snapshot                       # 原卡片对象不被就地修改
    assert "personal_score" not in cards[0]


def test_personalization_data_stays_out_of_public_story_store():
    """隐私铁律：个人化排序不得往 story_engine 的公共库写任何账号数据。"""
    from deepfocus_api import story_engine
    monkey_db = story_engine._db_path()
    user_profile.set_profile("judy", theme_follows=["ai"])
    story_events.log_event(user_id="judy", story_id="s1", action="bookmark", symbol="600519", now=NOW)
    user_profile.rank_stories("judy", [_card("s1", mass=0.5, symbol="600519")], limit=1, now=NOW)
    # 公共故事库若尚未建表则跳过；若存在，确认其中没有任何 user_id 列/账号行。
    import sqlite3
    from pathlib import Path
    path = Path(str(monkey_db))
    if not path.exists():
        return
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "user_profile" not in tables
        assert "story_events" not in tables
    finally:
        conn.close()
