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
    req = _article_request("中信证券上半年业绩增长，资本市场业务回暖。" * 20)

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
    non_article = short.model_copy(update={"topic": "研报", "content": "长正文。" * 100})

    assert asyncio.run(dao_bridge._maybe_pre_read_article(short)).title == short.title
    assert asyncio.run(dao_bridge._maybe_pre_read_article(non_article)).title == non_article.title


def test_long_flash_gets_a_concise_preview_title(monkeypatch):
    class FakeLLM:
        async def complete_json(self, prompt, **kwargs):
            assert "原始标题" in prompt
            return {
                "title": "美联储会议纪要显示降息分歧",
                "summary": "概括快讯核心变化。",
            }

    monkeypatch.setattr("deepfocus_api.llm.CloudResearchLLM", FakeLLM)
    req = _article_request("美联储会议纪要显示官员对降息时点存在分歧。" * 20).model_copy(
        update={
            "title": "路透社：美联储会议纪要显示官员对降息时点存在分歧。",
            "topic": "快讯",
            "tags": ["快讯"],
        }
    )

    out = asyncio.run(dao_bridge._maybe_pre_read_article(req))

    assert out.title == "美联储会议纪要显示降息分歧"
    assert out.content == req.content
    assert out.metadata["display_title_pre_read"] is True


def test_preview_failure_keeps_original_article_title(monkeypatch):
    class FailingLLM:
        async def complete_json(self, prompt, **kwargs):
            raise TimeoutError("model timeout")

    monkeypatch.setattr("deepfocus_api.llm.CloudResearchLLM", FailingLLM)
    req = _article_request("正文内容。" * 100)

    out = asyncio.run(dao_bridge._maybe_pre_read_article(req))

    assert out == req


def test_preview_rejects_unsupported_entities(monkeypatch):
    class HallucinatingLLM:
        async def complete_json(self, prompt, **kwargs):
            return {"title": "必和必拓财年净利润下滑15%", "summary": "模型猜测"}

    monkeypatch.setattr("deepfocus_api.llm.CloudResearchLLM", HallucinatingLLM)
    req = _article_request("Fortescue利润出现下滑，中国市场竞争仍在持续。" * 20)

    out = asyncio.run(dao_bridge._maybe_pre_read_article(req))

    assert out == req


def test_preview_strips_source_prefix_and_allows_title_compression(monkeypatch):
    class RewriterLLM:
        async def complete_json(self, prompt, **kwargs):
            return {"title": "路透社：富达国际拟退出中国独资基金业务", "summary": "压缩原标题"}

    monkeypatch.setattr("deepfocus_api.llm.CloudResearchLLM", RewriterLLM)
    req = _article_request("富达国际计划退出其在中国的独资基金管理业务。" * 20)

    out = asyncio.run(dao_bridge._maybe_pre_read_article(req))

    assert out.title == "富达国际拟退出中国独资基金业务"


def test_preview_falls_back_to_stripped_source_prefix_when_model_keeps_title(monkeypatch):
    class UnchangedLLM:
        async def complete_json(self, prompt, **kwargs):
            return {"title": "彭博社：Meta悄然成为微软最大AI客户之一", "summary": ""}

    monkeypatch.setattr("deepfocus_api.llm.CloudResearchLLM", UnchangedLLM)
    req = _article_request("Meta已经成为微软在人工智能领域最大的客户之一。" * 20).model_copy(
        update={"title": "彭博社：Meta悄然成为微软最大AI客户之一"}
    )

    out = asyncio.run(dao_bridge._maybe_pre_read_article(req))

    assert out.title == "Meta悄然成为微软最大AI客户之一"
    assert out.metadata["article_pre_read"] is True


def test_preview_falls_back_to_cleaned_source_wrapper_for_long_flash(monkeypatch):
    class UnchangedLLM:
        async def complete_json(self, prompt, **kwargs):
            return {"title": "【英伟达计划年底前向中国发货AI芯片 - 路透社】", "summary": ""}

    monkeypatch.setattr("deepfocus_api.llm.CloudResearchLLM", UnchangedLLM)
    req = _article_request("英伟达计划年底前向中国发货AI芯片。" * 100).model_copy(
        update={
            "title": "【英伟达计划年底前向中国发货AI芯片 - 路透社】",
            "topic": "快讯",
            "tags": ["快讯"],
        }
    )

    out = asyncio.run(dao_bridge._maybe_pre_read_article(req))

    assert out.title == "英伟达计划年底前向中国发货AI芯片"
    assert out.metadata["display_title_pre_read"] is True
