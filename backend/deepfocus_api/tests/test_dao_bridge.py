import asyncio

from deepfocus_api import dao_bridge
from deepfocus_api.schemas import RealtimeMessageCreateRequest


def test_bridge_poll_defaults_to_five_minutes(monkeypatch):
    monkeypatch.delenv("DEEPFOCUS_DAO_BRIDGE_POLL_SECONDS", raising=False)

    assert dao_bridge._bridge_config()["poll"] == 300.0


def test_bridge_poll_can_be_overridden(monkeypatch):
    monkeypatch.setenv("DEEPFOCUS_DAO_BRIDGE_POLL_SECONDS", "600")

    assert dao_bridge._bridge_config()["poll"] == 600.0


def _article_request(content: str) -> RealtimeMessageCreateRequest:
    return RealtimeMessageCreateRequest(
        title="中信证券2026年上半年经营情况及行业观点",
        content=content,
        topic="文章",
        severity="info",
        tags=["文章"],
        metadata={"dao_event_id": 42},
    )


def test_long_article_gets_a_concise_preview_title(monkeypatch):
    class FakeLLM:
        async def complete_json(self, prompt, **kwargs):
            assert "正文" in prompt
            assert kwargs["max_tokens"] == 180
            return {
                "title": "中信证券：上半年业绩增长，资本市场业务回暖",
                "summary": "聚焦业绩变化与资本市场业务修复。",
            }

    monkeypatch.setattr("deepfocus_api.llm.CloudResearchLLM", FakeLLM)
    req = _article_request("中国资本市场运行平稳，机构业务与财富管理出现积极变化。" * 20)

    out = asyncio.run(dao_bridge._maybe_pre_read_article(req))

    assert out.title == "中信证券：上半年业绩增长，资本市场业务回暖"
    assert out.content == req.content
    assert out.metadata["article_pre_read"] is True
    assert out.metadata["article_original_title"] == req.title
    assert out.metadata["article_pre_read_summary"] == "聚焦业绩变化与资本市场业务修复。"


def test_short_or_non_article_does_not_call_preview_model(monkeypatch):
    class ExplodingLLM:
        def __init__(self):
            raise AssertionError("short/non-article content must not call the model")

    monkeypatch.setattr("deepfocus_api.llm.CloudResearchLLM", ExplodingLLM)
    short = _article_request("短文章正文")
    non_article = short.model_copy(update={"topic": "快讯", "content": "长正文。" * 100})

    assert asyncio.run(dao_bridge._maybe_pre_read_article(short)).title == short.title
    assert asyncio.run(dao_bridge._maybe_pre_read_article(non_article)).title == non_article.title


def test_preview_failure_keeps_original_article_title(monkeypatch):
    class FailingLLM:
        async def complete_json(self, prompt, **kwargs):
            raise TimeoutError("model timeout")

    monkeypatch.setattr("deepfocus_api.llm.CloudResearchLLM", FailingLLM)
    req = _article_request("正文内容。" * 100)

    out = asyncio.run(dao_bridge._maybe_pre_read_article(req))

    assert out == req
