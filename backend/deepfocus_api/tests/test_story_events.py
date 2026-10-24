"""故事级埋点回归：动作词表、兴趣聚合衰减与符号、显式反馈、位置 CTR、匿名护栏、剪枝。

夹具把事件库指到 tmp_path，测试不碰仓库里的任何真实数据。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from deepfocus_api import story_events

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPFOCUS_STORY_EVENTS_DB_PATH", str(tmp_path / "events.sqlite3"))
    story_events.init_events_db()
    yield


def test_log_event_rejects_unknown_action_and_empty_story():
    assert story_events.log_event(user_id="u1", story_id="s1", action="bogus") is False
    assert story_events.log_event(user_id="u1", story_id="", action="click") is False
    assert story_events.log_event(user_id="u1", story_id="s1", action="click") is True


def test_anonymous_cannot_write_persistent_feedback_actions():
    # 匿名只允许曝光/点击/停留（用于位置 CTR），带账号态的反馈动作被丢弃。
    assert story_events.log_event(user_id="", story_id="s1", action="impression") is True
    assert story_events.log_event(user_id="", story_id="s1", action="click") is True
    assert story_events.log_event(user_id="", story_id="s1", action="bookmark") is False
    assert story_events.log_event(user_id="", story_id="s1", action="feedback_down") is False


def test_affinity_accumulates_symbols_themes_and_decays_with_age():
    story_events.log_event(user_id="u1", story_id="s1", action="bookmark",
                           symbol="600519", kind="company", themes=["consumption"], now=NOW)
    fresh = story_events.user_affinity("u1", now=NOW)
    assert fresh["symbols"]["600519"] > 0
    assert fresh["themes"]["consumption"] > 0
    assert fresh["kinds"]["company"] > 0

    # 同一交互放到 3 个半衰期之前，权重应显著衰减（指数衰减单调性）。
    old = NOW - timedelta(days=story_events.AFFINITY_HALF_LIFE_DAYS * 3)
    story_events.log_event(user_id="u2", story_id="s1", action="bookmark",
                           symbol="600519", kind="company", themes=["consumption"], now=old)
    aged = story_events.user_affinity("u2", now=NOW)
    assert aged["symbols"]["600519"] < fresh["symbols"]["600519"] * 0.2


def test_negative_feedback_lowers_affinity():
    story_events.log_event(user_id="u1", story_id="s1", action="click",
                           symbol="300750", themes=["energy"], now=NOW)
    story_events.log_event(user_id="u1", story_id="s2", action="feedback_down",
                           symbol="300750", themes=["energy"], now=NOW)
    aff = story_events.user_affinity("u1", now=NOW)
    # 一次点击(+0.3) 抵不过一次不感兴趣(-1.5)，净兴趣应为负。
    assert aff["symbols"]["300750"] < 0
    assert aff["themes"]["energy"] < 0


def test_dwell_multiplier_scales_within_bounds():
    assert story_events._dwell_multiplier(0) == pytest.approx(0.2)
    assert story_events._dwell_multiplier(story_events.DWELL_FULL_MS) == pytest.approx(1.0)
    mid = story_events._dwell_multiplier((story_events.DWELL_MIN_MS + story_events.DWELL_FULL_MS) // 2)
    assert 0.2 < mid < 1.0
    # 久读封顶在 1.5，不会无限放大。
    assert story_events._dwell_multiplier(story_events.DWELL_FULL_MS * 10) <= 1.5


def test_feedback_weights_only_counts_explicit_actions():
    story_events.log_event(user_id="u1", story_id="s1", action="click", now=NOW)
    story_events.log_event(user_id="u1", story_id="s2", action="feedback_up", now=NOW)
    story_events.log_event(user_id="u1", story_id="s3", action="dismiss", now=NOW)
    weights = story_events.user_feedback_weights("u1", now=NOW)
    assert "s1" not in weights              # 点击不是显式故事反馈
    assert weights["s2"] > 0
    assert weights["s3"] < 0


def test_seen_story_ids_only_counts_read_actions():
    for action in ("impression", "click", "dwell", "bookmark"):
        story_events.log_event(user_id="u1", story_id=f"s_{action}", action=action, now=NOW)
    seen = story_events.seen_story_ids("u1", ["s_impression", "s_click", "s_dwell", "s_bookmark", "s_none"])
    assert seen == {"s_click", "s_dwell", "s_bookmark"}


def test_ctr_by_position_orders_impressions_and_clicks():
    for _ in range(10):
        story_events.log_event(user_id="", story_id="s1", action="impression", surface="today", position=0, now=NOW)
    for _ in range(5):
        story_events.log_event(user_id="", story_id="s1", action="click", surface="today", position=0, now=NOW)
    for _ in range(10):
        story_events.log_event(user_id="", story_id="s2", action="impression", surface="today", position=1, now=NOW)
    story_events.log_event(user_id="", story_id="s2", action="click", surface="today", position=1, now=NOW)
    rows = story_events.ctr_by_position(surface="today", now=NOW)
    by_pos = {r["position"]: r for r in rows}
    assert by_pos[0]["ctr"] == pytest.approx(0.5)
    assert by_pos[1]["ctr"] == pytest.approx(0.1)
    assert by_pos[0]["ctr"] > by_pos[1]["ctr"]


def test_story_counts_aggregate_across_users():
    story_events.log_event(user_id="u1", story_id="s1", action="click", now=NOW)
    story_events.log_event(user_id="u2", story_id="s1", action="click", now=NOW)
    story_events.log_event(user_id="u1", story_id="s1", action="bookmark", now=NOW)
    counts = story_events.story_counts(["s1", "s_missing"])
    assert counts["s1"]["click"] == 2
    assert counts["s1"]["bookmark"] == 1
    assert counts["s_missing"] == {}


def test_prune_removes_only_expired_rows():
    story_events.log_event(user_id="u1", story_id="old", action="click",
                           now=NOW - timedelta(days=story_events.RETENTION_DAYS + 5))
    story_events.log_event(user_id="u1", story_id="new", action="click", now=NOW)
    removed = story_events.prune(now=NOW)
    assert removed == 1
    assert story_events.seen_story_ids("u1", ["old", "new"]) == {"new"}


def test_user_cap_rolls_off_oldest_rows(monkeypatch):
    monkeypatch.setattr(story_events, "MAX_ROWS_PER_USER", 20)
    for index in range(45):
        story_events.log_event(user_id="u1", story_id=f"s{index}", action="click", now=NOW)
    with story_events._connect() as conn:
        story_events._enforce_user_cap(conn, "u1")
        total = conn.execute("SELECT COUNT(*) AS n FROM story_events WHERE user_id='u1'").fetchone()["n"]
        oldest = conn.execute(
            "SELECT story_id FROM story_events WHERE user_id='u1' ORDER BY id ASC LIMIT 1"
        ).fetchone()["story_id"]
    assert total == 20
    # 滚动删最旧：s0..s24 被删，剩下的最早一条应是 s25。
    assert oldest == "s25"
