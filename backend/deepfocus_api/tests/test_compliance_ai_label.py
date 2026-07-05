"""AI 生成内容显式标识（《人工智能生成合成内容标识办法》2025-09-01 施行）——compliance.ai_label 契约。"""
from deepfocus_api import compliance


def test_ai_label_appends_notice():
    out = compliance.ai_label("这是一段 AI 叙述")
    assert "AI 生成" in out and out.startswith("这是一段 AI 叙述")


def test_ai_label_brief_variant_is_short():
    out = compliance.ai_label("答案", brief=True)
    assert out == "答案（AI 生成，仅供参考）"


def test_ai_label_idempotent():
    once = compliance.ai_label("答案", brief=True)
    assert compliance.ai_label(once, brief=True) == once          # 已带标识不重复追加
    assert compliance.ai_label(compliance.ai_label("正文")) == compliance.ai_label("正文")


def test_ai_label_respects_existing_variants():
    # 历史手写过的变体（如"AI 辅助生成"）视作已标识，不再追加
    s = "内容……（AI 辅助生成）"
    assert compliance.ai_label(s) == s


def test_ai_label_empty_passthrough():
    assert compliance.ai_label("") == ""


def test_has_ai_label():
    assert compliance.has_ai_label("xx（AI 生成，仅供参考）")
    assert not compliance.has_ai_label("普通叙述")
