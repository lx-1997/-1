from __future__ import annotations

import asyncio

import pytest

from deepfocus_api import zsxq_stream as zs


def test_stream_groups_default_is_research_group():
    groups = zs.stream_groups()
    assert groups and groups[0]["id"]
    assert groups[0]["name"] == "机构纪要"


def test_stream_groups_env_override(monkeypatch):
    monkeypatch.setenv(
        "DEEPFOCUS_ZSXQ_STREAM_GROUPS",
        '[{"id":"111","name":"甲"},{"id":"222","name":"乙"}]',
    )
    groups = zs.stream_groups()
    assert [g["id"] for g in groups] == ["111", "222"]


def test_stream_groups_bad_env_falls_back(monkeypatch):
    monkeypatch.setenv("DEEPFOCUS_ZSXQ_STREAM_GROUPS", "not-json")
    assert zs.stream_groups()[0]["name"] == "机构纪要"


def test_norm_topic_full_and_empty():
    t = {
        "topicId": "123", "text": "【广发机械】银轮股份\n# 液冷：核心低位资产",
        "images": ["http://a/1.jpg"], "image_fulls": [], "author": "水木调研纪要",
        "create_time": "2026-07-06T11:18:00.000+0800", "digested": True,
        "likes_count": 3, "comments_count": 5,
        "comments": [{"author": "张三", "text": "赞", "likes_count": 1}],
    }
    it = zs._norm_topic(t)
    assert it is not None
    assert it["id"] == "123" and it["digested"] and it["date"] == "2026-07-06"
    assert it["image_fulls"] == ["http://a/1.jpg"]  # 缺原图回退小图
    assert it["comments"][0]["author"] == "张三"
    assert it["comments_count"] == 5
    assert "author" not in it and "url" not in it  # ⭐不透出星球来源与原文链接
    assert zs._norm_topic({"topicId": "9", "text": "", "images": []}) is None  # 空帖丢弃
    assert zs._norm_topic("junk") is None


def test_fetch_stream_rejects_unknown_group():
    with pytest.raises(ValueError):
        asyncio.run(zs.fetch_stream(group="999999999"))


def test_fetch_comments_requires_topic_id():
    res = asyncio.run(zs.fetch_comments(""))
    assert res["error"] and res["comments"] == []
