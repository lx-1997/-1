"""Schema-specific JSON retries must preserve full news evidence fields."""
import json

import pytest

from deepfocus_api.llm import CloudResearchLLM


class FakeCompletion(CloudResearchLLM):
    @property
    def provider(self):
        return "test"

    def __init__(self, replies):
        super().__init__()
        self.replies = iter(replies)
        self.prompts = []

    async def _complete_text(self, prompt, **kwargs):
        self.prompts.append(prompt)
        return next(self.replies)


@pytest.mark.asyncio
async def test_news_schema_repair_preserves_two_complete_measures():
    evidence = [
        {"title": "入境限制", "evidence": "禁止两名部长入境香港和澳门。"},
        {"title": "交易合作限制", "evidence": "禁止中国机构和个人与两名部长进行交易或合作。"},
    ]
    model = FakeCompletion(["not JSON", "{broken JSON}", json.dumps({"summary": "两项具体制裁措施。", "logic_lines": evidence})])
    result = await model.complete_json("新闻正文", retry_schema_hint="保持新闻 summary/logic_lines 对象数组，保留完整措施。")
    assert result["logic_lines"] == evidence
    assert len(model.prompts) == 3
    for prompt in model.prompts[1:]:
        assert "logic_lines" in prompt
        assert "保留完整措施" in prompt
        assert "每项不超过" not in prompt
        assert "不要 Markdown" in prompt


@pytest.mark.asyncio
async def test_default_json_repairs_keep_the_existing_compact_contract():
    model = FakeCompletion(["not JSON", "{broken JSON}", '{"summary":"有效内容"}'])
    result = await model.complete_json("普通任务")
    assert result["summary"] == "有效内容"
    assert "每项不超过 18" in model.prompts[1]
    assert "每项不超过 24" in model.prompts[2]
