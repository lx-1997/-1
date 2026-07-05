"""名人观点模块测试：可插拔数据源(curated/zsxq) + 富媒体条目 + 配置/媒体安全 + 综述兼容。"""

import asyncio

import pytest

from deepfocus_api import celebrity_views as cv
from deepfocus_api.people_voices import digest_cache_key
from deepfocus_api.schemas import CelebrityProfile


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """每个用例：配置指向临时空文件、媒体落临时目录、清空缓存——彼此隔离、不污染真实配置。"""
    monkeypatch.setattr(cv, "_CFG_PATH", tmp_path / "cfg.json")
    monkeypatch.setattr(cv, "_MEDIA_DIR", tmp_path / "media")
    cv._CACHE.clear()
    yield
    cv._CACHE.clear()


def test_default_roster_sources_and_zsxq_binding():
    cfg = cv.get_config()
    assert cfg["enabled"] is True
    assert "zsxq" in cfg["sources"] and "curated" in cfg["sources"]
    assert len(cfg["celebrities"]) >= 1
    # 洪灝走知识星球真实帖子
    honghao = next(c for c in cfg["celebrities"] if c["id"] == "honghao")
    assert honghao["zsxq_group"] == "88885882121542"
    # 默认花名册不再内置任何示例言论——观点只来自真实数据源，绝不编造名人发言
    assert all(c.get("items") == [] for c in cfg["celebrities"])


def test_curated_items_fill_missing_date_with_today():
    celeb = {"id": "x", "items": [{"title": "无日期条目", "body": "正文"}]}
    items = cv._curated_items(celeb)
    assert len(items) == 1
    assert items[0].reported_date == cv._today_cst()
    assert items[0].source_type == "curated"


def test_fetch_views_curated_only_shape_and_media():
    # 关 zsxq → 确定性、不打网络；运营录入一位带富媒体条目的名人 + 无条目的洪灝
    cv.set_config({
        "sources": ["curated"],
        "celebrities": [
            {"id": "demoer", "name": "示例大佬", "items": [{
                "title": "【示例】富媒体观点", "body": "正文",
                "image_urls": ["/api/celebrity/media/pic.png"],
                "audio_url": f"/api/celebrity/media/{cv.SAMPLE_AUDIO_NAME}",
                "date": "2026-06-01",
            }]},
            {"id": "honghao", "name": "洪灝", "zsxq_group": "88885882121542"},
        ],
    })
    resp = asyncio.run(cv.fetch_celebrity_views())
    assert resp.provider == "curated"
    assert resp.sources == ["curated"]
    demoer = next(f for f in resp.figures if f.id == "demoer")
    assert demoer.items and demoer.items[0].image_urls and demoer.items[0].audio_url
    assert demoer.data_quality.level == "live"
    honghao = next(f for f in resp.figures if f.id == "honghao")
    assert honghao.item_count == 0  # curated-only 下洪灝无条目 → 降级
    assert honghao.data_quality.level == "degraded"


def test_fetch_views_cache_hit_and_signature_invalidation():
    cv.set_config({"sources": ["curated"]})
    asyncio.run(cv.fetch_celebrity_views())
    r2 = asyncio.run(cv.fetch_celebrity_views())
    assert r2.cache_age_seconds >= 0
    sig_a = cv._config_signature(cv.get_config())
    cv.set_config({"celebrities": [{"id": "solo", "name": "独一"}]})
    sig_b = cv._config_signature(cv.get_config())
    assert sig_a != sig_b


def test_zsxq_items_maps_topics(monkeypatch):
    """zsxq 适配器把工作台返回的帖子映射成富媒体条目（正文+图片+作者+时间+原文链接）。"""
    fake = {
        "items": [
            {
                "topicId": "T1",
                "text": "铜周线 历史新高。\n第二段说明。",
                "images": ["https://images.zsxq.com/a?token=x", "https://images.zsxq.com/b?token=y"],
                "create_time": "2026-06-07T07:29:58.694+0800",
                "author": "洪灝",
                "digested": True,
                "url": "https://wx.zsxq.com/topic/T1",
            },
            {"topicId": "T2", "text": "", "images": []},  # 纯空 → 跳过
        ]
    }

    class _Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return fake

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): return _Resp()

    monkeypatch.setattr(cv.httpx, "AsyncClient", _Client)
    celeb = {"id": "honghao", "name": "洪灝", "zsxq_group": "88885882121542", "zsxq_keyword": ""}
    items = asyncio.run(cv._zsxq_items(celeb, group="", max_n=8))
    assert len(items) == 1  # 空帖被跳过
    it = items[0]
    assert it.title.startswith("铜周线")
    assert it.body.startswith("铜周线") and "第二段" in it.body
    assert len(it.image_urls) == 2
    assert it.source_name == "洪灝"
    assert it.source_url == "https://wx.zsxq.com/topic/T1"
    assert it.source_type == "zsxq"
    assert it.reported_date == "2026-06-07"
    assert "精华" in it.tags  # digested


def test_zsxq_no_group_returns_empty_no_network():
    celeb = {"id": "x", "name": "无星球", "zsxq_keyword": ""}
    items = asyncio.run(cv._zsxq_items(celeb, group="", max_n=5))
    assert items == []


def test_topic_to_item_maps_comments_and_topic_id():
    """帖子随带的预览评论 → comments 字段；comments_count 取上游总数；topic_id 透传供拉全评论。"""
    celeb = {"id": "honghao", "name": "洪灝"}
    t = {
        "topicId": "82255211525441252",
        "text": "中概互联的机会。",
        "images": [],
        "create_time": "2026-07-01T08:00:00.000+0800",
        "comments_count": 107,
        "comments": [
            {"author": "老邢", "text": "其实Intel才是老登。", "create_time": "2026-07-03T10:43:59.583+0800", "likes_count": 10, "sticky": False},
            {"author": "", "text": "", "sticky": False},  # 空评论 → 丢弃
        ],
    }
    it = cv._topic_to_item(celeb, t)
    assert it is not None
    assert it.topic_id == "82255211525441252"
    assert it.comments_count == 107
    assert len(it.comments) == 1
    assert it.comments[0].author == "老邢" and it.comments[0].likes_count == 10


def test_fetch_topic_comments_normalizes_and_degrades(monkeypatch):
    """加载全部评论：正常返回归一化评论；未知名人 None；上游故障优雅降级不 500。"""
    fake = {
        "comments": [
            {"author": "李明", "text": "你方下场我登场。", "create_time": "2026-07-03T10:47:44.947+0800", "likes_count": 3, "sticky": True},
            {"author": "", "text": ""},  # 空 → 丢弃
        ],
        "hasMore": True,
    }

    class _Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return fake

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): return _Resp()

    monkeypatch.setattr(cv.httpx, "AsyncClient", _Client)
    res = asyncio.run(cv.fetch_topic_comments("honghao", "82255211525441252", limit=50))
    assert res is not None and res.get("error") is None if "error" in res else True
    assert res["count"] == 1 and res["has_more"] is True
    assert res["comments"][0]["author"] == "李明" and res["comments"][0]["sticky"] is True

    assert asyncio.run(cv.fetch_topic_comments("nobody", "1")) is None  # 未知名人

    class _Boom(_Client):
        async def post(self, *a, **k): raise RuntimeError("workbench down")

    monkeypatch.setattr(cv.httpx, "AsyncClient", _Boom)
    res2 = asyncio.run(cv.fetch_topic_comments("honghao", "82255211525441252"))
    assert res2 is not None and res2["comments"] == [] and res2.get("error")


def test_safe_media_name_rejects_traversal_and_bad_ext():
    assert cv._safe_media_name("../../etc/passwd") is None
    assert cv._safe_media_name("a b.png") is None
    assert cv._safe_media_name("note.txt") is None
    assert cv._safe_media_name("ok.png") == "ok.png"
    assert cv._safe_media_name("voice.mp3") == "voice.mp3"


def test_sample_audio_generated_on_demand():
    p = cv.media_file(cv.SAMPLE_AUDIO_NAME)
    assert p is not None and p.exists()
    assert p.suffix == ".wav"
    assert cv.media_content_type(p) == "audio/wav"


def test_save_media_roundtrip():
    saved = cv.save_media("clip.wav", b"RIFFxxxx")
    assert saved == "clip.wav"
    assert cv.media_file("clip.wav") is not None
    assert cv.save_media("../evil.wav", b"x") == "evil.wav"  # basename 中和穿越
    assert cv.save_media("evil.exe", b"x") is None            # 类型被拒


def test_set_config_sanitizes_celebrities_and_filters_sources():
    out = cv.set_config({
        "sources": ["curated", "zsxq", "unknown-source"],
        "celebrities": [
            {"id": "a", "name": "甲", "zsxq_group": "123", "items": [{"title": "观点", "body": "b"}]},
            {"id": "", "name": "无id被丢"},
            "not-a-dict",
        ],
    })
    assert out["sources"] == ["curated", "zsxq"]  # 未知源被过滤
    assert len(out["celebrities"]) == 1
    assert out["celebrities"][0]["id"] == "a"
    assert out["celebrities"][0]["zsxq_group"] == "123"  # zsxq 字段保留


def test_fetch_celebrity_unknown_none_known_profile():
    cv.set_config({"sources": ["curated"]})
    assert asyncio.run(cv.fetch_celebrity("nobody")) is None
    prof = asyncio.run(cv.fetch_celebrity("honghao"))
    assert isinstance(prof, CelebrityProfile)
    assert prof.name == "洪灝"


def test_celebrity_profile_compatible_with_digest_synthesizer():
    cv.set_config({"sources": ["curated"]})
    prof = asyncio.run(cv.fetch_celebrity("honghao"))
    key = digest_cache_key(prof)  # 不抛 = 字段同构（id + items[].title）
    assert key.startswith("honghao:")
    for attr in ("name", "role", "org", "why_it_matters", "item_count", "items"):
        assert hasattr(prof, attr)
