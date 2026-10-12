from __future__ import annotations

import asyncio
import json

import fitz
import httpx
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


# --- news quality and cache recovery ---------------------------------------

_SANCTIONS_TITLE = "路透社：中国因台湾问题对巴拉圭和伯利兹的部长实施制裁"
_SANCTIONS_BODY = (
    "中国自周一起对巴拉圭通信部长及伯利兹外长实施制裁，原因是这两个国家仍与台湾保持关系。"
    "相关措施包括禁止两人入境香港和澳门，并禁止中国机构和个人与其进行交易或合作。"
)


def _failed_sanctions_result():
    return {
        "one_liner": "中国因台湾问题对巴拉圭和伯利兹的部长实施制裁",
        "summary": "中国对巴拉圭和伯利兹的部长实施制裁，原因是台湾问题。具体措施和影响尚不明确。",
        "core_logic": "", "logic_lines": [], "bullish": [], "bearish": [],
        "confidence": 0.0,
    }


def _good_sanctions_result():
    return {
        "subject": "巴拉圭通信部长、伯利兹外长",
        "one_liner": "两名部长被限制入境香港、澳门，并受到交易合作禁令",
        "summary": "制裁从周一起实施，涉及巴拉圭通信部长及伯利兹外长。两人不得入境香港和澳门；中国机构和个人不得与其进行交易或合作。",
        "core_logic": "两国仍与台湾保持关系，因此中国对相关部长实施制裁。",
        "logic_lines": [
            {"title": "两名部长入境受限", "evidence": "禁止两人入境香港和澳门", "chain": "", "impact": "巴拉圭通信部长、伯利兹外长", "watch": ""},
            {"title": "交易合作受限", "evidence": "禁止中国机构和个人与两人交易或合作", "chain": "", "impact": "中国机构和个人", "watch": ""},
        ],
        "bullish": [], "bearish": [], "confidence": 0.85,
    }


@pytest.fixture
def news_runtime(monkeypatch):
    """Keep news tests deterministic and prevent writes to the shared store."""
    from deepfocus_api import data_store

    def setup(replies, cached=None):
        class NewsLLM:
            provider = "test"
            model = "test-news-model"

            def __init__(self):
                self.replies = list(replies)
                self.calls = []

            async def complete_json(self, prompt, **kwargs):
                self.calls.append((prompt, kwargs))
                reply = self.replies.pop(0)
                if isinstance(reply, Exception):
                    raise reply
                return reply

        fake = NewsLLM()
        writes = []
        reads = []

        def latest(kind, key, **kwargs):
            reads.append((kind, key))
            return cached

        monkeypatch.setattr(rv, "CloudResearchLLM", lambda: fake)
        monkeypatch.setattr(data_store, "latest", latest)
        monkeypatch.setattr(data_store, "record", lambda kind, key, value: writes.append((kind, key, value)))
        return fake, reads, writes

    return setup


def test_news_normalization_removes_legacy_report_placeholders_only():
    legacy = rv.ensure_logic_lines({"summary": "事件摘要", "confidence": 0.0})
    assert legacy["logic_lines"][0]["title"] == "信息边界"
    cleaned = rv.normalize_news_result(legacy)
    assert cleaned["logic_lines"] == []
    assert rv.ensure_logic_lines(legacy)["logic_lines"]  # Report behavior remains intact.

    cleaned = rv.normalize_news_result({
        "summary": "两人不得入境香港和澳门。",
        "core_logic": "原文未明确说明", "takeaway": "原文未提及",
        "logic_lines": legacy["logic_lines"] + [{
            "title": "入境限制", "evidence": "禁止入境香港和澳门",
            "chain": "原文未明确说明", "impact": "两名部长", "watch": "原文未给出验证条件。",
        }],
    })
    assert len(cleaned["logic_lines"]) == 1
    assert cleaned["logic_lines"][0]["evidence"] == "禁止入境香港和澳门"
    assert cleaned["logic_lines"][0]["chain"] == ""
    assert cleaned["logic_lines"][0]["watch"] == ""
    assert cleaned["core_logic"] == cleaned["takeaway"] == ""


def test_news_quality_rejects_zero_confidence_and_title_without_new_facts():
    assert rv.news_result_needs_refresh(_failed_sanctions_result(), _SANCTIONS_TITLE)
    assert rv.news_result_needs_refresh({
        "one_liner": _SANCTIONS_TITLE, "summary": _SANCTIONS_TITLE, "confidence": 0.9,
    }, _SANCTIONS_TITLE)
    assert rv.news_result_needs_refresh({"summary": "摘要", "confidence": float("nan")})
    assert not rv.news_result_needs_refresh(_good_sanctions_result(), _SANCTIONS_TITLE)
    # One concrete detail is enough for a short event; no row-count quota.
    assert not rv.news_result_needs_refresh({
        "one_liner": "公司发布新品", "summary": "新品定价299元，下周一发售。", "confidence": 0.7,
    }, "公司发布新品")
    assert not rv.news_result_needs_refresh({
        "one_liner": "公司发布新品", "summary": "公司发布新品，定价299元。", "confidence": 0.7,
    }, "公司发布新品")


def test_news_retries_failed_sanctions_reading_and_keeps_both_measures(news_runtime):
    fake, _, writes = news_runtime([_failed_sanctions_result(), _good_sanctions_result()])
    result = asyncio.run(rv.analyze_news(_SANCTIONS_TITLE, _SANCTIONS_BODY))
    assert len(fake.calls) == 2
    assert "香港和澳门" in result["summary"]
    assert "交易或合作" in result["summary"]
    assert len(result["logic_lines"]) == 2
    assert result["source_note"] == "基于提供的正文"
    assert len(writes) == 1 and writes[0][1].startswith("NEWS:v5:")
    for prompt, kwargs in fake.calls:
        assert "禁止两人入境香港和澳门" in prompt
        assert "具体措施" in kwargs["retry_schema_hint"]
        assert "logic_lines" in kwargs["retry_schema_hint"]
    assert "上次解读未达到质量要求" in fake.calls[1][0]


def test_news_bad_internal_cache_is_regenerated(news_runtime):
    fake, reads, writes = news_runtime([_good_sanctions_result()], cached=rv.ensure_logic_lines(_failed_sanctions_result()))
    result = asyncio.run(rv.analyze_news(_SANCTIONS_TITLE, _SANCTIONS_BODY))
    assert len(fake.calls) == len(writes) == 1
    assert reads[0][1].startswith("NEWS:v5:")
    assert result["logic_lines"][0]["title"] != "信息边界"


def test_news_refresh_bypasses_good_internal_cache(news_runtime):
    fake, reads, writes = news_runtime([_good_sanctions_result()], cached=_good_sanctions_result())
    result = asyncio.run(rv.analyze_news(_SANCTIONS_TITLE, _SANCTIONS_BODY, refresh=True))
    assert reads == []
    assert len(fake.calls) == len(writes) == 1
    assert "交易或合作" in result["summary"]


@pytest.mark.parametrize("reply", [_failed_sanctions_result(), {}, ValueError("invalid JSON")])
def test_news_real_body_failure_is_retryable_and_never_cached(news_runtime, reply):
    fake, _, writes = news_runtime([reply, reply])
    with pytest.raises(RuntimeError, match="自动重试后仍未达到质量要求"):
        asyncio.run(rv.analyze_news(_SANCTIONS_TITLE, _SANCTIONS_BODY))
    assert len(fake.calls) == 2
    assert writes == []


def test_news_title_only_fallback_is_honest_and_never_cached(news_runtime):
    fake, _, writes = news_runtime([{}])
    result = asyncio.run(rv.analyze_news("某公司今日发布新款产品", ""))
    assert len(fake.calls) == 1
    assert rv.is_title_only_result(result)
    assert result["source_note"].startswith("⚠")
    assert result["logic_lines"] == []
    assert result["confidence"] == 0.0
    assert writes == []


def test_news_short_lead_failure_does_not_claim_body_unavailable(news_runtime):
    _, _, writes = news_runtime([{}])
    result = asyncio.run(rv.analyze_news("公司发布新品", "新品定价299元，下周一发售。"))
    assert "299元" in result["one_liner"]
    assert "原文正文不可得" not in result["one_liner"]
    assert "标题和导语" in result["source_note"]
    assert writes == []


def test_news_generation_timeout_leaves_time_for_bounded_retry(news_runtime, monkeypatch):
    fake, _, writes = news_runtime([])
    cancelled = []

    async def blocked(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    fake.complete_json = blocked
    monkeypatch.setattr(rv, "_NEWS_ATTEMPT_TIMEOUTS", (0.01, 0.01))

    async def run():
        with pytest.raises(RuntimeError, match="自动重试后仍未达到质量要求"):
            await asyncio.wait_for(rv.analyze_news(_SANCTIONS_TITLE, _SANCTIONS_BODY), 0.5)

    asyncio.run(run())
    assert len(cancelled) == 2
    assert writes == []


@pytest.mark.parametrize("url", [
    "https://files.example.com/article.PNG?download=1",
    "https://files.example.com/article.pdf",
])
def test_article_media_urls_skip_http_fetch(monkeypatch, url):
    def no_client(*args, **kwargs):
        raise AssertionError("Image/PDF source must use the original-text reader, not HTML fetch")

    monkeypatch.setattr(rv.httpx, "AsyncClient", no_client)
    assert asyncio.run(rv._fetch_article_text(url)) == ""


@pytest.mark.parametrize("content_type,content", [
    ("image/png", b"\x89PNG\r\n\x1a\n" + b"image-data" * 100),
    ("text/html", b"\x89PNG\r\n\x1a\n" + b"image-data" * 100),
    ("application/pdf", b"%PDF-1.7\n" + b"pdf-data" * 100),
    ("text/plain", b"binary\x00payload" * 100),
    ("application/octet-stream", b"opaque-binary-data"),
])
def test_article_fetch_rejects_binary_response(monkeypatch, content_type, content):
    response = httpx.Response(200, headers={"Content-Type": content_type}, content=content)

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url):
            return response

    monkeypatch.setattr(rv.httpx, "AsyncClient", Client)
    assert asyncio.run(rv._fetch_article_text("https://files.example.com/source")) == ""


@pytest.mark.parametrize("content_type", ["text/html; charset=utf-8", "application/xhtml+xml"])
def test_article_fetch_keeps_html_body(monkeypatch, content_type):
    paragraph = "公司将在下周推出新品，售价299元，并在三个城市同步销售。"
    response = httpx.Response(
        200, headers={"Content-Type": content_type},
        content=f"<html><body><p>{paragraph}</p><p>{paragraph}</p><p>{paragraph}</p></body></html>".encode("utf-8"),
    )

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url):
            return response

    monkeypatch.setattr(rv.httpx, "AsyncClient", Client)
    text = asyncio.run(rv._fetch_article_text("https://news.example.com/story"))
    assert paragraph in text
    assert "<p>" not in text


def test_article_missing_mime_keeps_utf8_text_at_probe_boundary():
    assert rv._article_response_is_text(httpx.Response(200, content=("正文" * 300).encode("utf-8")))
    assert not rv._article_response_is_text(httpx.Response(200, content=b"\xff" * 600))


def test_news_media_source_skips_text_fetch_callback(news_runtime, monkeypatch):
    fake, _, _ = news_runtime([_good_sanctions_result()])

    async def no_fetch(*args, **kwargs):
        raise AssertionError("Do not replace cleaned article prose with screenshot bytes")

    monkeypatch.setattr(rv, "_fetch_article_text", no_fetch)
    result = asyncio.run(rv.analyze_news(
        _SANCTIONS_TITLE, _SANCTIONS_BODY, url="https://files.example.com/uploads/article.png",
    ))
    assert result["source_note"] == "基于提供的正文"
    assert _SANCTIONS_BODY in fake.calls[0][0]


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
