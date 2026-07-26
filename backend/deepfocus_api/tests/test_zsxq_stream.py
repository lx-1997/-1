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
        "links": [
            {"label": "锂电池材料价格周报", "url": "https://share.note.youdao.com/a"},
            {"label": "知识星球-安全中心", "url": "javascript:alert(1)"},
        ],
        "create_time": "2026-07-06T11:18:00.000+0800", "digested": True,
        "likes_count": 3, "comments_count": 5,
        "comments": [{"author": "张三", "text": "赞", "likes_count": 1}],
    }
    it = zs._norm_topic(t)
    assert it is not None
    assert it["id"] == "123" and it["digested"] and it["date"] == "2026-07-06"
    assert it["image_fulls"] == ["http://a/1.jpg"]  # 缺原图回退小图
    assert it["links"] == [{"label": "锂电池材料价格周报", "url": "https://share.note.youdao.com/a"}]
    assert it["comments"][0]["author"] == "张三"
    assert it["comments_count"] == 5
    # ⭐不透出星球来源(author)、原文链接(url)、帖子点赞数(likes_count)
    assert "author" not in it and "url" not in it and "likes_count" not in it
    assert zs._norm_topic({"topicId": "9", "text": "", "images": []}) is None  # 空帖丢弃
    assert zs._norm_topic("junk") is None


def test_file_marker_posts_are_dropped():
    # 文件上传占位帖（研报本体，已在「研报」标签）：纯 hashtag、无正文、无图 → 丢弃
    assert zs._norm_topic({"topicId": "1", "text": "#海外投行报告#", "images": []}) is None
    assert zs._norm_topic({"topicId": "2", "text": "#会议音频#", "images": []}) is None
    assert zs._norm_topic({"topicId": "3", "text": "#纪要&报告#  ", "images": []}) is None
    # 真纪要保留：hashtag 之外有正文
    keep = zs._norm_topic({"topicId": "4", "text": "#出处未知#  950Q3满产已完全不够用", "images": []})
    assert keep is not None and "950Q3" in keep["text"]
    # 纯 hashtag 但带图（图片型纪要）→ 保留
    img = zs._norm_topic({"topicId": "5", "text": "#公募加仓#", "images": ["http://a/1.jpg"]})
    assert img is not None


def test_fetch_stream_rejects_unknown_group():
    with pytest.raises(ValueError):
        asyncio.run(zs.fetch_stream(group="999999999"))


def test_fetch_comments_requires_topic_id():
    res = asyncio.run(zs.fetch_comments(""))
    assert res["error"] and res["comments"] == []
