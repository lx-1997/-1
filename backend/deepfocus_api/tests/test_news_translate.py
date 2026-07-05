"""英文快讯自动翻译回归。

覆盖：英文判定边界、英文→中文直译、指纹缓存命中(不重复调模型)、中文/失败/超时回退原文，
以及 dao_bridge._maybe_translate 把译文写进 title/content、英文原文落 metadata。
全程 mock 掉 MiniMax 与 data_store 缓存，无网络。
"""
import asyncio

import pytest

from deepfocus_api import news_translate as nt, dao_bridge
from deepfocus_api.schemas import RealtimeMessageCreateRequest


class _FakeLLM:
    """假 CloudResearchLLM：记调用次数，按需返回译文或抛错。"""
    calls = 0
    behavior = "ok"  # ok | fail

    def __init__(self, *a, **k):
        pass

    async def complete_json(self, prompt, **kwargs):
        type(self).calls += 1
        if type(self).behavior == "fail":
            raise RuntimeError("model boom")
        return {"title": "欧盟:提议将乌克兰临时保护延长至2028年", "content": ""}


@pytest.fixture(autouse=True)
def _patch(monkeypatch):
    _FakeLLM.calls = 0
    _FakeLLM.behavior = "ok"
    monkeypatch.setattr(nt, "CloudResearchLLM", _FakeLLM)
    store: dict = {}
    monkeypatch.setattr(nt.data_store, "record", lambda kind, sym, payload, **k: store.__setitem__((kind, sym.upper()), payload))
    monkeypatch.setattr(
        nt.data_store, "latest",
        lambda kind, sym, **k: store.get((kind, sym.upper())),
    )
    return store


# ── 英文判定边界 ─────────────────────────────────────────────
def test_looks_english_boundaries():
    assert nt.looks_english("EU: PROPOSES EXTENDING TEMPORARY PROTECTION TO 2028")
    assert not nt.looks_english("中国建材全年转亏37.45亿人民币 末期息派15分")
    assert not nt.looks_english("中国巨石 Q2 EPS 超预期")  # 中文为主
    assert not nt.looks_english("OPEC+")  # 太短，零星缩写不翻


# ── 英文 → 中文 ──────────────────────────────────────────────
def test_translate_english_news():
    out = asyncio.run(nt.translate_news("EU: PROPOSES EXTENDING UKRAINIAN PROTECTION TO 2028", ""))
    assert out is not None
    assert out["title"].startswith("欧盟")
    assert _FakeLLM.calls == 1


def test_chinese_news_not_translated():
    out = asyncio.run(nt.translate_news("中国建材全年转亏37.45亿人民币", "正文也是中文"))
    assert out is None
    assert _FakeLLM.calls == 0  # 中文根本不调模型


def test_cache_hit_skips_model(_patch):
    title = "EU OIL CO-ORDINATION GROUP: SITUATION IS STABLE FOR THE TIME BEING"
    first = asyncio.run(nt.translate_news(title, ""))
    second = asyncio.run(nt.translate_news(title, ""))
    assert first == second
    assert _FakeLLM.calls == 1  # 第二次命中指纹缓存，不再调模型


def test_model_failure_returns_none():
    _FakeLLM.behavior = "fail"
    out = asyncio.run(nt.translate_news("EU: PROPOSES EXTENDING PROTECTION TO 2028", ""))
    assert out is None  # 失败 → None，调用方回退原文


# ── dao_bridge 接线 ─────────────────────────────────────────
def test_maybe_translate_rewrites_and_keeps_original(monkeypatch):
    req = RealtimeMessageCreateRequest(
        title="EU: PROPOSES EXTENDING UKRAINIAN PROTECTION TO 2028",
        content="",
        topic="快讯",
        severity="info",
        tags=["快讯"],
        metadata={"dao_event_id": 123},
    )
    out = asyncio.run(dao_bridge._maybe_translate(req))
    assert out.title.startswith("欧盟")          # 译文进 title
    assert out.metadata["original_title"] == req.title  # 英文原文留底
    assert out.metadata["translated"] is True
    assert out.metadata["dao_event_id"] == 123    # 原 metadata 不丢


def test_maybe_translate_chinese_passthrough():
    req = RealtimeMessageCreateRequest(
        title="中国建材全年转亏37.45亿", content="", topic="快讯", severity="info", tags=["快讯"],
    )
    out = asyncio.run(dao_bridge._maybe_translate(req))
    assert out.title == req.title                 # 中文原样透传
    assert "translated" not in (out.metadata or {})


def test_maybe_translate_disabled(monkeypatch):
    monkeypatch.setattr(dao_bridge, "_TRANSLATE_ENABLED", False)
    req = RealtimeMessageCreateRequest(
        title="EU: PROPOSES EXTENDING PROTECTION TO 2028", content="", topic="快讯",
        severity="info", tags=["快讯"],
    )
    out = asyncio.run(dao_bridge._maybe_translate(req))
    assert out.title == req.title                 # 开关关闭 → 原样
