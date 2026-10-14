from __future__ import annotations

import asyncio
from types import SimpleNamespace

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


def _mock_stream_dependencies(monkeypatch, fetch):
    """Exercise the retrieval boundary without network or ontology/share writes."""
    from deepfocus_api import research_wire

    calls = []
    persisted = []

    class Response:
        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self.data

    class Client:
        def __init__(self, **kwargs):
            assert kwargs == {"trust_env": False}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            assert url.endswith("/api/search-topics")
            calls.append(dict(kwargs["json"]))
            return Response(fetch(kwargs["json"]))

    monkeypatch.setattr(zs, "stream_groups", lambda: [{"id": "allowed", "name": "机构纪要"}])
    monkeypatch.setattr(zs, "_CACHE", {})
    monkeypatch.setattr(zs, "time", SimpleNamespace(monotonic=lambda: 10.0))
    monkeypatch.setattr(zs, "_norm_topic", lambda row: dict(row))
    monkeypatch.setattr(zs, "_persist_share_topics", lambda rows: persisted.append(list(rows)))
    monkeypatch.setattr(research_wire, "auth_payload", lambda: {})
    monkeypatch.setattr(zs.httpx, "AsyncClient", Client)
    return calls, persisted


def test_fetch_stream_default_retains_four_page_contract_and_cache_key(monkeypatch):
    rows = [{"id": str(index), "text": "纪要正文"} for index in range(40)]
    calls, persisted = _mock_stream_dependencies(
        monkeypatch, lambda payload: {"items": rows, "nextEndTime": "older", "hasMore": True},
    )

    first = asyncio.run(zs.fetch_stream(keyword="  茅台  ", limit=99))
    cached = asyncio.run(zs.fetch_stream(keyword="茅台", limit=99))

    assert calls == [{"group": "allowed", "resultLimit": 40, "searchPages": 4, "keyword": "茅台"}]
    assert first == cached
    assert first["items"] == rows and first["total"] == 40
    assert first["next_before"] == "older" and first["has_more"] is True
    assert list(zs._CACHE) == [("allowed", "茅台")]
    assert persisted == [rows]


@pytest.mark.parametrize(("limit", "expected_count"), [(99, 20), (3, 3)])
def test_fetch_stream_stock_scope_caps_provider_request_and_response(monkeypatch, limit, expected_count):
    rows = [{"id": str(index), "text": "纪要正文"} for index in range(35)]
    calls, persisted = _mock_stream_dependencies(monkeypatch, lambda payload: {"items": rows})

    result = asyncio.run(zs.fetch_stream(keyword="茅台", limit=limit, stock_scope=True))

    assert calls == [{"group": "allowed", "resultLimit": expected_count, "searchPages": 1, "keyword": "茅台"}]
    assert result["items"] == rows[:expected_count] and result["total"] == expected_count
    assert list(zs._CACHE) == [("allowed", "茅台", "stock")]
    assert persisted == [rows[:expected_count]]


def test_fetch_stream_stock_and_default_caches_do_not_cross_on_hit_or_failure(monkeypatch):
    offline = False

    def fetch(payload):
        if offline:
            raise RuntimeError("upstream unavailable")
        mode = "stock" if payload["searchPages"] == 1 else "default"
        return {"items": [{"id": mode, "text": f"{mode} result"}]}

    calls, _ = _mock_stream_dependencies(monkeypatch, fetch)
    default = asyncio.run(zs.fetch_stream(keyword="茅台"))
    stock = asyncio.run(zs.fetch_stream(keyword="茅台", stock_scope=True))
    assert default["items"][0]["id"] == "default"
    assert stock["items"][0]["id"] == "stock"
    assert len(calls) == 2

    assert asyncio.run(zs.fetch_stream(keyword=" 茅台 ")) == default
    assert asyncio.run(zs.fetch_stream(keyword=" 茅台 ", stock_scope=True)) == stock
    assert len(calls) == 2

    offline = True
    assert asyncio.run(zs.fetch_stream(keyword="茅台", use_cache=False)) == default
    assert asyncio.run(zs.fetch_stream(keyword="茅台", stock_scope=True, use_cache=False)) == stock
    assert len(calls) == 4
    assert set(zs._CACHE) == {("allowed", "茅台"), ("allowed", "茅台", "stock")}


@pytest.mark.parametrize("stock_scope", [False, True])
def test_fetch_stream_unknown_group_cannot_use_cached_results_or_network(monkeypatch, stock_scope):
    calls, persisted = _mock_stream_dependencies(monkeypatch, lambda payload: {"items": []})
    key = ("forbidden", "茅台", "stock") if stock_scope else ("forbidden", "茅台")
    zs._CACHE[key] = (10.0, {"items": [{"id": "private", "text": "不可见纪要"}]})

    with pytest.raises(ValueError, match="未知星球"):
        asyncio.run(zs.fetch_stream(group="forbidden", keyword="茅台", stock_scope=stock_scope))

    assert calls == [] and persisted == []
