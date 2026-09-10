from __future__ import annotations

import asyncio
import json

import fitz
import pytest

from deepfocus_api import research_vision as rv


def _pdf_bytes(pages: int = 3) -> bytes:
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"page {i + 1}")
    return doc.tobytes()


class _FakeLLM:
    def __init__(self, *, replies=None, raises_then=None):
        self.provider = "minimax"
        self.model = "MiniMax-M3"
        self._replies = list(replies or [])
        self._raises_then = list(raises_then or [])
        self.calls = []

    async def complete_vision(self, prompt, image_pngs, **kwargs):
        self.calls.append(len(image_pngs))
        if self._raises_then:
            exc = self._raises_then.pop(0)
            if exc is not None:
                raise exc
        return self._replies.pop(0)


# --- pure helpers ----------------------------------------------------------

def test_render_pdf_to_pngs_caps_pages():
    pngs = rv.render_pdf_to_pngs(_pdf_bytes(5), max_pages=3)
    assert len(pngs) == 3
    assert all(p[:8] == b"\x89PNG\r\n\x1a\n" for p in pngs)  # PNG magic


def test_render_caps_at_max_vision_pages():
    pngs = rv.render_pdf_to_pngs(_pdf_bytes(rv.MAX_VISION_PAGES + 3), max_pages=999)
    assert len(pngs) == rv.MAX_VISION_PAGES


def test_clamp_confidence_and_str_list():
    assert rv._clamp_confidence("0.8") == 0.8
    assert rv._clamp_confidence(2) == 1.0
    assert rv._clamp_confidence("x") == 0.5
    assert rv._as_str_list(["a", "", "b"], 5) == ["a", "b"]
    assert rv._as_str_list("solo", 5) == ["solo"]


def test_report_prompt_asks_for_neutral_restatement_without_six_dimension_block():
    prompt = rv._build_text_prompt("某公司深度", "TEST", "正文" * 300)
    assert '"core_logic"' in prompt
    assert "首屏字段" in prompt
    assert '"logic_lines"' in prompt
    assert "中性" in prompt  # 解读定位：中性、完整地复述研报重点
    for label in ("事实", "传导", "影响", "验证"):
        assert label in prompt
    # 六维「综合判断与边界」已下线，提示词不得再要求生成
    assert '"df_take"' not in prompt
    for label in ("成立前提", "反方与遗漏", "行业/周期", "敏感变量", "横向参照", "跟踪清单"):
        assert label not in prompt


def test_compact_report_normalization_caps_first_screen_but_keeps_deep_analysis():
    data = {
        "one_liner": "偏多：盈利预测上调",
        "bullish": ["利好1", "利好2", "利好3", "利好4", "利好5"],
        "bearish": ["风险1", "风险2", "风险3", "风险4", "风险5"],
        "takeaway": "重复启示",
        "df_take": "【成立前提】需求持续。\n【跟踪清单】跟踪订单。",
    }
    result = rv._normalize_result(
        data, provider="test", pages=1, disclaimer="", compact_report=True,
    )
    assert result["bullish"] == ["利好1", "利好2", "利好3", "利好4"]
    assert result["bearish"] == ["风险1", "风险2", "风险3", "风险4"]
    assert result["takeaway"] == ""
    assert result["df_take"] == ""  # 六维 df_take 已下线，研报解读不再输出


def test_ensure_report_depth_strips_legacy_six_dimension_cache():
    result = rv.ensure_report_depth({
        "one_liner": "偏多：供应链信号改善",
        "core_logic": "需求改善可能带动设备与测试环节。",
        "bullish": ["报告提及多环节出现积极信号"],
        "bearish": ["报告未提供盈利预测变化"],
        "df_take": "【成立前提】需求持续。\n【反方与遗漏】原文未提及。",
    })
    assert result["df_take"] == ""
    assert len(result["logic_lines"]) >= 1  # 逻辑线层仍会兜底补齐
    assert "无风险提示不等于" not in result["df_take"]


def test_prompts_are_source_bound_and_do_not_request_independent_commentary():
    report_prompt = rv._build_text_prompt("某公司", "TEST", "正文" * 100)
    news_prompt = rv._build_news_prompt("标题", "正文" * 100)
    for prompt in (report_prompt, news_prompt):
        assert "原文未提及" in prompt
        assert "不得根据常识" in prompt or "不能根据常识" in prompt
        assert "不是给出独立点评" in prompt or "不得加入报告之外的原创判断" in prompt


def test_news_format_is_neutral_and_does_not_fabricate_four_logic_lines():
    prompt = rv._build_news_prompt("苹果 Mac 与 AI 硬件", "正文" * 100)
    assert "不要把标题或单一正面措辞改写成‘看多/看空’" in prompt
    assert "logic_lines 最多3条" in prompt

    result = rv._normalize_result(
        {
            "one_liner": "***报道***&#x6807;题称苹果 Mac 获得 AI 硬件成功",
            "summary": "事件摘要",
            "core_logic": "",
            "bullish": [],
            "bearish": [],
            "logic_lines": [{"title": "Mac", "evidence": "原文事实"}],
        },
        provider="test", pages=0, disclaimer="", logic_line_limit=3, backfill_logic_lines=False,
    )
    result = rv.normalize_news_result(result)
    assert len(result["logic_lines"]) == 1
    assert result["one_liner"] == "报道标题称苹果 Mac 获得 AI 硬件成功"


def test_integrated_logic_lines_are_normalized_and_backfilled():
    result = rv._normalize_result({
        "summary": "四条线共同影响结论。",
        "core_logic": "需求增长传导到订单和利润。",
        "bullish": ["供给改善", "价格稳定"],
        "bearish": ["需求不及预期"],
        "logic_lines": [{
            "name": "需求线", "fact": "订单增加", "mechanism": "订单增加→出货提升",
            "beneficiaries": "相关公司", "trigger": "跟踪下季度出货",
        }],
    }, provider="test", pages=0, disclaimer="", compact_report=False)
    assert len(result["logic_lines"]) >= 4
    first = result["logic_lines"][0]
    assert first["title"] == "需求线"
    assert first["evidence"] == "订单增加"
    assert first["chain"] == "订单增加→出货提升"
    assert first["impact"] == "相关公司"
    assert first["watch"] == "跟踪下季度出货"


# --- analyze_pdf_vision ----------------------------------------------------

def test_analyze_pdf_vision_parses_json(monkeypatch):
    reply = json.dumps({
        "summary": "标的看多", "key_points": ["要点1", "要点2"],
        "risks": ["风险1"], "rating": "买入", "target_price": "$415", "confidence": 0.82,
    })
    fake = _FakeLLM(replies=[reply])
    monkeypatch.setattr(rv, "CloudResearchLLM", lambda: fake)

    result = asyncio.run(rv.analyze_pdf_vision(_pdf_bytes(2), title="特斯拉", symbol="TSLA"))
    assert result["summary"] == "标的看多"
    assert result["key_points"] == ["要点1", "要点2"]
    assert result["rating"] == "买入"
    assert result["target_price"] == "$415"
    assert result["confidence"] == 0.82
    assert result["pages_analyzed"] == 2
    assert result["provider"] == "MiniMax-M3"


def test_analyze_pdf_vision_drops_sensitive_page_and_retries(monkeypatch):
    sensitive = RuntimeError(
        "视觉模型调用失败：Error code: 422 - messages[0]'s content[2] image is sensitive (1026)"
    )
    ok = json.dumps({"summary": "ok", "key_points": ["k"], "risks": [], "confidence": 0.5})
    fake = _FakeLLM(replies=[ok], raises_then=[sensitive, None])
    monkeypatch.setattr(rv, "CloudResearchLLM", lambda: fake)

    result = asyncio.run(rv.analyze_pdf_vision(_pdf_bytes(3), title="x"))
    # 3 张图 → 第一次 content[2](第2张)被判 sensitive 丢弃 → 第二次用 2 张成功
    assert fake.calls == [3, 2]
    assert result["pages_analyzed"] == 2
    assert result["summary"] == "ok"


def test_analyze_pdf_vision_rejects_mock_provider(monkeypatch):
    class _Mock:
        provider = "mock"
        model = "mock"
    monkeypatch.setattr(rv, "CloudResearchLLM", lambda: _Mock())
    with pytest.raises(RuntimeError):
        asyncio.run(rv.analyze_pdf_vision(_pdf_bytes(1)))


def test_analyze_pdf_vision_empty_reply_raises(monkeypatch):
    fake = _FakeLLM(replies=[json.dumps({"summary": "", "key_points": []})])
    monkeypatch.setattr(rv, "CloudResearchLLM", lambda: fake)
    with pytest.raises(RuntimeError):
        asyncio.run(rv.analyze_pdf_vision(_pdf_bytes(1)))


# --- 我方品牌水印不得污染文本通道 -------------------------------------------
# 历史事故：图片型研报（原文无文字层）经 pdf_brand 打完品牌水印后，
# extract_pdf_text 抽出的“正文”全是每页平铺的 www.daocaijing.com 水印字，
# 且轻松超过 MIN_TEXT_CHARS → 文本通道把纯水印喂给模型（解读成“原文只有网址水印”），
# 也不再回退视觉解读。以下用真实 _add_brand 打水印复现并守住修复。

def _branded_pdf(pages: int, body_lines: int = 0) -> bytes:
    from deepfocus_api.pdf_brand import _add_brand

    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page()
        for i in range(body_lines):
            page.insert_text((72, 90 + i * 16), f"real body line {i} about earnings growth")
        _add_brand(page)
    return doc.tobytes()


def test_extract_text_image_pdf_with_brand_falls_below_text_gate():
    # 3 页纯图片型（无正文文字层）+ 品牌水印：剥离后应几乎无文本 → 走视觉回退
    text = rv.extract_pdf_text(_branded_pdf(3))
    assert "daocaijing" not in text
    assert "更多投研内容" not in text
    assert text == ""
    assert len(text) < rv.MIN_TEXT_CHARS


def test_extract_text_keeps_real_body_and_strips_brand():
    text = rv.extract_pdf_text(_branded_pdf(1, body_lines=35))
    assert "real body line 3" in text  # 正文保留
    assert "daocaijing" not in text     # 水印剥净


# --- 模型 JSON 引号修复（prod 真实失败样本）----------------------------------

def test_parse_model_json_repairs_unescaped_inner_quotes():
    raw = (
        '```json\n{\n'
        '  "one_liner": "看多：KTOS、AVAV是新一批受益的"新主承包商"",\n'
        '  "summary": "报告称"金穹"反导系统是主线之一。",\n'
        '  "confidence": 0.9\n'
        '}\n```'
    )
    data = rv._parse_model_json(raw)
    assert data, "修复后应能解析"
    assert "新主承包商" in data["one_liner"]
    assert "金穹" in data["summary"]
    assert data["confidence"] == 0.9


def test_parse_model_json_standard_and_garbage():
    assert rv._parse_model_json('{"a": 1}') == {"a": 1}
    assert rv._parse_model_json("") == {}
    assert rv._parse_model_json("完全不是 JSON") == {}
