from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from deepfocus_api import research_harness as rh
from deepfocus_api import dulus_runtime as dr
from deepfocus_api import agent_tools
from deepfocus_api.schemas import StockSnapshot


def test_broad_stock_selection_isolated_from_page_stock_and_old_topic() -> None:
    context = (
        "当前标的：国金证券（600109）\n"
        "【最近对话】\n用户: AI算力选股\n助手: 中际旭创\n\n用户当前消息：\n\n"
        "【用户上传文件：偏好.txt】\n只看高股息"
    )

    assert rh.is_stock_selection_request("你推荐买哪些股票") is True
    assert rh.stock_selection_needs_clarification("你推荐买哪些股票", context) is True
    cleaned = rh.sanitize_stock_selection_context("你推荐买哪些股票", context)
    assert "国金证券" not in cleaned
    assert "AI算力" not in cleaned
    assert "只看高股息" in cleaned  # 用户本轮附件仍保留

    assert rh.is_stock_selection_request("推荐买贵州茅台这只股票吗") is False
    assert rh.stock_selection_needs_clarification("AI算力板块推荐哪些股票", context) is False
    assert rh.is_stock_selection_request("按 A 股、6—12 个月、均衡型筛 3 只") is True


def test_common_stock_selection_phrasings_and_default_are_supported() -> None:
    for query in (
        "最近买什么票",
        "给我三只股票",
        "港股长期稳健推荐3只",
        "短线买哪几个",
        "不考虑当前页面，重新选股",
        "按默认方案",
    ):
        assert rh.is_stock_selection_request(query) is True, query
    assert rh.stock_selection_needs_clarification("按默认方案") is False
    assert rh.normalize_stock_selection_request("按默认方案") == "按 A 股、6—12 个月、均衡型筛 3 只"
    assert rh.stock_selection_needs_clarification("A股有什么股票推荐") is True
    assert rh.stock_selection_needs_clarification("进攻型筛3只") is True
    assert rh.stock_selection_needs_clarification("港股长期稳健推荐3只") is False
    assert rh.stock_selection_needs_clarification("机器人板块挑3只") is False


def test_comparison_question_extracts_each_current_side() -> None:
    stocks = rh._comparison_stocks("宁德时代和比亚迪更偏向谁？")
    assert [(item.symbol, item.name) for item in stocks] == [
        ("300750", "宁德时代"),
        ("002594", "比亚迪"),
    ]
    assert rh.is_comparison_question("宁德时代和比亚迪更偏向谁？") is True
    assert rh.is_comparison_question("贵州茅台现在估值贵不贵？") is False


def test_cross_market_name_is_locked_before_generic_research() -> None:
    stock = rh._stock_from_text("建滔集团值得买吗")
    assert stock is not None
    assert stock.symbol == "00148"
    assert stock.name == "建滔集团"
    assert stock.market == "HK"


@pytest.mark.asyncio
async def test_harness_skips_non_research_greeting(monkeypatch) -> None:
    async def should_not_run(*args, **kwargs):
        raise AssertionError("greeting must not invoke research tools")

    monkeypatch.setattr(rh, "execute_tool", should_not_run)
    packet = await rh.build_research_packet("你好")

    assert packet["modules"] == {}
    assert packet["gaps"] == ["非研究类问题"]


@pytest.mark.asyncio
async def test_harness_collects_real_modules_and_rag(monkeypatch) -> None:
    calls: list[str] = []
    rag_queries: list[str] = []

    async def fake_execute(name, arguments, **kwargs):
        calls.append(name)
        if name == "get_market_quote":
            return {"ok": True, "data": {"quotes": [{"name": "测试股份", "price": 12.3, "change_percent": 1.2}], "provider": "test-market"}}
        return {"ok": True, "data": {"value": name}, "provider": "test-source"}

    monkeypatch.setattr(rh, "execute_tool", fake_execute)
    def fake_list_data_items(**kwargs):
        rag_queries.append(str(kwargs.get("query") or ""))
        return [SimpleNamespace(
            title="测试证据",
            source_name="测试证据库",
            url="https://example.com/evidence",
            credibility_score=0.9,
            collected_at="2026-08-21T00:00:00Z",
            created_at="2026-08-21T00:00:00Z",
            text_preview="测试摘要",
        )]

    monkeypatch.setattr(rh, "list_data_items", fake_list_data_items)

    packet = await rh.build_research_packet(
        "研究这只股票的估值和风险",
        stock=StockSnapshot(symbol="600519", name="测试股份", market="CN"),
    )

    assert "get_market_quote" in calls
    assert "get_valuation" in calls
    assert "get_financial_statements" in calls
    for tool in (
        "get_site_fast_news",
        "get_site_articles",
        "get_stock_research",
        "get_recent_research",
        "get_institution_notes",
        "get_celebrity_views",
        "get_industry_context",
        "get_peer_comparison",
        "get_supply_chain_context",
    ):
        assert tool in calls
    assert packet["content_scope"]["快讯"]["status"] == "available"
    assert packet["content_scope"]["文章"]["status"] == "available"
    assert packet["content_scope"]["机构纪要"]["status"] == "available"
    assert packet["content_scope"]["公开数据"]["status"] == "available"
    assert "research_index" in packet["modules"]
    assert packet["modules"]["research_index"]["stats"]["annotation_version"] == "rules-v2"
    assert "evidence_rag" in packet["modules"]
    assert packet["stock"].current_price == 12.3
    assert "测试证据库" in packet["sources"]
    assert "get_site_articles" not in packet["context"].split("证据缺口：", 1)[-1]
    assert rag_queries and all("研究这只股票" not in query for query in rag_queries)
    assert any("测试股份" in query and "600519" in query for query in rag_queries)


@pytest.mark.asyncio
async def test_harness_streams_auditable_progress_without_model_reasoning(monkeypatch) -> None:
    events: list[tuple[str, str]] = []

    async def fake_execute(name, arguments, **kwargs):
        return {"ok": True, "data": {"items": [{"title": name, "summary": "可复核摘要", "source_name": "测试源"}]}}

    async def progress(phase, payload):
        events.append((phase, str(payload.get("tool") or "")))

    monkeypatch.setattr(rh, "execute_tool", fake_execute)
    monkeypatch.setattr(rh, "list_data_items", lambda **kwargs: [])
    await rh.build_research_packet(
        "建滔集团值得买吗",
        progress=progress,
    )
    assert any(phase == "start" and tool == "get_industry_context" for phase, tool in events)
    assert any(phase == "result" and tool == "get_supply_chain_context" for phase, tool in events)


@pytest.mark.asyncio
async def test_comparison_harness_keeps_both_sides_and_scope(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []

    async def fake_execute(name, arguments, **kwargs):
        calls.append((name, arguments))
        if name == "compare_stocks":
            return {
                "ok": True,
                "data": {
                    "items": [
                        {"symbol": "300750", "name": "宁德时代", "data_gaps": []},
                        {"symbol": "002594", "name": "比亚迪", "data_gaps": []},
                    ]
                },
            }
        return {"ok": True, "data": {"items": [{"title": name}]}}

    monkeypatch.setattr(rh, "execute_tool", fake_execute)
    monkeypatch.setattr(rh, "list_data_items", lambda **kwargs: [])
    monkeypatch.setattr(rh, "build_research_index", lambda *args, **kwargs: {"digest": [], "coverage": {}, "stats": {}})

    packet = await rh.build_research_packet("宁德时代和比亚迪更偏向谁？")

    assert packet["comparison"]["is_comparison"] is True
    assert [item["symbol"] for item in packet["comparison"]["items"]["items"]] == ["300750", "002594"]
    assert {args["symbols"] for name, args in calls if name == "compare_stocks"} == {"300750,002594"}
    assert packet["content_scope"]["比较口径"]["status"] == "available"
    assert {item["symbol"] for item in packet["content_scope"]["快讯"]["sides"]} == {"300750", "002594"}
    assert "比较题硬规则" in packet["context"]


def test_comparison_fallback_mentions_both_companies() -> None:
    bundle = {
        "items": [
            {"symbol": "300750", "name": "宁德时代", "quote": {"price": 377.8}, "valuation": {"pe_ratio": 21.1, "pb_ratio": 4.7}, "financials": {"revenue_yoy": 54.8, "profit_yoy": 42, "roe": 12.1}},
            {"symbol": "002594", "name": "比亚迪", "quote": {"price": 100}, "valuation": {"pe_ratio": 30, "pb_ratio": 5}, "financials": {"revenue_yoy": 20, "profit_yoy": -5, "roe": 8}},
        ]
    }
    context = "数据模块：" + json.dumps({"compare_stocks": json.dumps(bundle, ensure_ascii=False)}) + "\n证据缺口："
    answer = dr._comparison_fallback_content(
        dr.DulusRoundtableRequest(objective="宁德时代和比亚迪更偏向谁？", context=context),
        [],
    )
    assert "宁德时代" in answer
    assert "比亚迪" in answer
    assert "资料覆盖多少也不代表公司更好" in answer


@pytest.mark.asyncio
async def test_harness_marks_whitelist_content_without_leaking_it(monkeypatch) -> None:
    async def fake_execute(name, arguments, **kwargs):
        if name == "get_celebrity_views":
            return {"ok": True, "data": {"note": "该功能暂未开放"}}
        return {"ok": True, "data": {"value": name}}

    monkeypatch.setattr(rh, "execute_tool", fake_execute)
    monkeypatch.setattr(rh, "list_data_items", lambda **kwargs: [])
    packet = await rh.build_research_packet(
        "研究贵州茅台估值",
        stock=StockSnapshot(symbol="600519", name="贵州茅台", market="CN"),
    )

    assert packet["content_scope"]["名人观点"]["status"] == "not_authorized"
    assert "名人观点: 当前账号未授权，未纳入结论" in packet["gaps"]
    assert packet["modules"]["get_celebrity_views"] == {"note": "该功能暂未开放"}


@pytest.mark.asyncio
async def test_institution_notes_tool_keeps_category_separate(monkeypatch) -> None:
    from datetime import datetime, timezone
    from deepfocus_api import agent_tools
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 31, 7, tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(agent_tools, "_dt", FixedDateTime)
    async def fake_stream(**kwargs):
        assert kwargs["keyword"] == "贵州茅台"
        return {"items": [{
            "title": "贵州茅台渠道调研纪要",
            "text": "机构交流关注批价和库存。",
            "create_time": "2026-08-21T08:00:00Z",
            "tags": ["白酒", "调研"],
        }]}

    monkeypatch.setattr("deepfocus_api.zsxq_stream.fetch_stream", fake_stream)
    result = await agent_tools.execute_tool(
        "get_institution_notes", {"query": "贵州茅台", "limit": 4}
    )
    assert result["ok"] is True
    assert result["data"]["source"] == "稻草财经机构纪要"
    assert result["data"]["items"][0]["title"] == "贵州茅台渠道调研纪要"


@pytest.mark.asyncio
async def test_institution_notes_code_boundary_and_dedup(monkeypatch) -> None:
    from datetime import datetime, timezone
    from deepfocus_api import agent_tools
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 31, 7, tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(agent_tools, "_dt", FixedDateTime)
    async def fake_stream(**kwargs):
        assert kwargs["keyword"] == "00148"
        return {"items": [
            {"title": "建议积极关注|天舟文化300148|华鑫传媒朱珠", "text": "传媒行业观点。", "date": "2026-08-30"},
            {"title": "建滔集团00148中报交流纪要", "text": "机构交流关注覆铜板景气度。", "date": "2026-08-29"},
            {"title": "建滔集团00148中报交流纪要", "text": "同一条纪要的重复入流。", "date": "2026-08-29"},
        ]}

    monkeypatch.setattr("deepfocus_api.zsxq_stream.fetch_stream", fake_stream)
    result = await agent_tools.execute_tool(
        "get_institution_notes", {"query": "00148", "limit": 8}
    )
    assert result["ok"] is True
    assert result["data"]["count"] == 1
    assert result["data"]["items"][0]["title"] == "建滔集团00148中报交流纪要"


@pytest.mark.asyncio
async def test_recent_content_digest_scans_institution_notes_with_window(monkeypatch) -> None:
    calls: dict[str, object] = {}

    async def fake_site(**kwargs):
        return {"items": [], "count": 0, "coverage": {"complete": True}}

    async def fake_wire(**kwargs):
        return {"items": [], "count": 0, "coverage": {"complete": True}}

    async def fake_notes(**kwargs):
        calls.update(kwargs)
        return {"items": [{"title": "建滔集团机构纪要", "summary": "CCL涨价", "date": "2026-08-30"}], "count": 1,
                "coverage": {"complete": True}}

    monkeypatch.setattr(agent_tools, "_recent_site_content", fake_site)
    monkeypatch.setattr(agent_tools, "_recent_wire_content", fake_wire)
    monkeypatch.setattr(agent_tools, "_tool_get_institution_notes", fake_notes)
    monkeypatch.setattr(
        "deepfocus_api.research_index.build_research_index",
        lambda modules, **kwargs: {"stats": {"raw_items": 1, "deduped_items": 1, "selected_items": 1, "annotation_version": "test"}, "coverage": {}, "digest": []},
    )

    result = await agent_tools.execute_tool(
        "get_recent_content_digest", {"query": "", "days": 2, "limit": 200}
    )
    assert result["ok"] is True
    assert calls == {"query": "", "days": 2, "limit": 200, "scan_limit": 500}
    assert result["data"]["sources"]["机构纪要"]["count"] == 1


def test_evidence_references_group_platform_and_external_sources() -> None:
    refs = rh._evidence_references(
        {
            "items": [{
                "id": "research:1",
                "category": "site_articles",
                "category_label": "文章",
                "title": "贵州茅台渠道价格跟踪",
                "summary": "站内研究摘要",
                "source_name": "稻草财经",
                "url": "https://daocaijing.com/articles/1",
                "published_at": "2026-08-21",
                "source_score": 0.78,
            }],
        },
        [{
            "tool": "get_valuation",
            "ok": True,
            "source": "东方财富估值",
            "summary": "已返回真实数据",
        }],
    )

    assert refs[0]["group_label"] == "稻草财经平台"
    assert refs[0]["title"] == "贵州茅台渠道价格跟踪"
    assert refs[0]["url"].startswith("https://daocaijing.com")
    assert any(ref["group_label"] == "外部公开数据" and ref["title"] == "估值快照" for ref in refs)


def test_live_references_project_each_content_type_from_tool_result() -> None:
    refs = rh.live_references_for_trace(
        "get_recent_content_digest",
        {
            "ok": True,
            "data": {
                "sources": {
                    "快讯": {"items": [{"title": "茅台渠道价格出现变化", "published_at": "2026-08-31T09:20:00Z"}]},
                    "文章": {"items": [{"title": "白酒库存与需求跟踪", "published_at": "2026-08-30"}]},
                    "投行研报": {"items": [{"title": "贵州茅台：维持增持", "org": "某证券", "date": "2026-08-29"}]},
                    "机构纪要": {"items": [{"title": "渠道调研纪要：终端动销", "date": "2026-08-28"}]},
                },
            },
        },
        limit=8,
    )

    assert [(item["category"], item["title"]) for item in refs] == [
        ("快讯", "茅台渠道价格出现变化"),
        ("文章", "白酒库存与需求跟踪"),
        ("投行研报", "贵州茅台：维持增持"),
        ("机构纪要", "渠道调研纪要：终端动销"),
    ]
    assert refs[2]["source"] == "某证券"
    assert refs[3]["published_at"] == "2026-08-28"


@pytest.mark.asyncio
async def test_dulus_roundtable_consumes_harness_packet(monkeypatch) -> None:
    class FakeLLM:
        provider = "openai"
        provider_name = "openai-compatible"
        model = "test-model"

        async def complete_json(self, prompt, **kwargs):
            return {
                "content": "基于统一数据包完成角色判断。",
                "key_points": ["有真实数据"],
                "risks": [],
                "actions": ["继续核验"],
                "confidence": 0.7,
            }

    monkeypatch.setattr(dr, "crawl_evidence_if_thin", lambda *args, **kwargs: _done())

    async def fake_packet(*args, **kwargs):
        return {
            "stock": StockSnapshot(symbol="600519", name="测试股份", market="CN"),
            "modules": {"get_market_quote": {"price": 12.3}},
            "traces": [],
            "sources": ["测试行情源"],
            "gaps": [],
            "context": "【统一研究 Harness 数据包】\n行情：12.3",
        }

    monkeypatch.setattr(dr, "build_research_packet", fake_packet)
    response = await dr.run_dulus_roundtable(
        FakeLLM(),
        dr.DulusRoundtableRequest(
            objective="研究测试股份估值",
            participants=["evidence", "research", "risk"],
            enabled_tools=["market_snapshot", "risk_review"],
        ),
    )

    assert response.turns
    assert any(trace.tool == "research_harness" for trace in response.tool_traces)
    assert "测试行情源" in response.sources


async def _done():
    return 0
