"""Auditable paragraph extraction, revisions and narrative-only integration."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

from deepfocus_api import stock_intelligence as intelligence
from deepfocus_api.stock_platform_research import build_platform_research

NOW = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)


def item(text, *, identifier="one", **changes):
    return {"id": identifier, "title": "测试公司经营观察", "symbol": "600519", "visible_text": text,
            "published_at": "2026-10-10", "body_state": "full", "role": "opinion",
            "source_name": "测试平台研究", "url": "https://example.test/report", **changes}


def channel(*items, **changes):
    return {"channel": "site_articles", "label": "平台文章", "status": "available", "items": list(items),
            "coverage": {"scanned": len(items), "matched": len(items), "complete": True, "window_days": 365}, **changes}


def build(*channels):
    return build_platform_research(list(channels), symbol="600519", name="测试公司", market="CN", now=NOW)


@pytest.mark.parametrize("subject", ["五粮液", "同行公司", "供应商", "公司供应商", "其供应商", "上游"])
def test_company_title_does_not_assign_related_party_operating_metrics(subject):
    result = build(channel(item(f"{subject}净利润增长20%，收入增长10%。因此预计EPS上调。")))
    assert result["claims"]
    assert all(claim["role"] == "context" for claim in result["claims"])
    assert all(not link["support_claim_ids"] and not link["counter_claim_ids"] for link in result["scenario_links"])
    assert result["documents"][0]["role"] == "opinion"


def test_title_confirmed_company_keeps_omitted_subject_and_resets_after_explicit_company():
    result = build(channel(item("净利增长，营收改善。五粮液净利增长。因此预计EPS上调。\n测试公司：净利下滑。")))
    omitted = [claim for claim in result["claims"] if claim["claim"].startswith("净利增长")]
    assert omitted and all(claim["role"] != "context" for claim in omitted)
    peers = [claim for claim in result["claims"] if "五粮液" in claim["claim"] or "因此预计" in claim["claim"]]
    assert peers and all(claim["role"] == "context" for claim in peers)
    target = [claim for claim in result["claims"] if "测试公司" in claim["claim"]]
    assert target and all(claim["role"] != "context" for claim in target)


def test_verified_company_document_clips_peer_clause_and_preserves_exact_citation():
    text = "测试公司净利增长，五粮液股价突破20日均线。"
    result = build(channel(item(text)))
    target = [claim for claim in result["claims"] if claim["role"] != "context"]
    assert target and all("五粮液" not in claim["claim"] and claim["dimension"] != "technical" for claim in target)
    peer = [claim for claim in result["claims"] if "五粮液" in claim["claim"]]
    assert peer and all(claim["role"] == "context" for claim in peer)
    for citation in result["citations"]:
        assert text[citation["start"]:citation["end"]] == citation["quote"]


def test_late_paragraphs_are_read_and_exact_quotes_resolve_to_document_revision():
    visible = "开场背景材料。\n" * 160 + "  渠道库存上升，需关注终端需求是否恢复。  \n净利下滑导致盈利预测下调。"
    result = build(channel(item(visible)))
    assert result["coverage"][0]["read_chars"] == len(visible)
    assert result["documents"][0]["read_chars"] > 420
    assert any("净利下滑" in claim["claim"] for claim in result["claims"])
    assert any(citation["start"] > 420 for citation in result["citations"])
    revision = result["documents"][0]["revision"]
    for citation in result["citations"]:
        assert visible[citation["start"]:citation["end"]] == citation["quote"]
        assert citation["document_revision"] == revision
    assert {c["id"] for c in result["citations"]} == {c["id"] for claim in result["claims"] for c in claim["citations"]}
    assert "未调用新 LLM" in result["method"] and "需要人工复核" in result["method"]


def test_same_position_revision_invalidates_citation_and_claim_ids_but_preserves_document_identity():
    before = build(channel(item("净利增长带动盈利改善。")))
    after = build(channel(item("净利下滑导致盈利承压。")))
    assert before["documents"][0]["id"] == after["documents"][0]["id"]
    assert before["documents"][0]["revision"] != after["documents"][0]["revision"]
    assert {c["id"] for c in before["citations"]}.isdisjoint(c["id"] for c in after["citations"])
    assert {c["id"] for c in before["claims"]}.isdisjoint(c["id"] for c in after["claims"])
    assert before == build(channel(item("净利增长带动盈利改善。")))


@pytest.mark.parametrize("status", ["access_required", "not_enabled", "denied", "disabled"])
def test_denied_channels_discard_every_document_and_hidden_count_or_reason(status):
    result = build(channel(item("会员秘密净利增长", title="隐藏会员标题"), status=status,
                           coverage={"scanned": 999, "matched": 999, "complete": True, "limit_hit": True, "reason": "隐藏会员标题"}))
    assert result["documents"] == result["claims"] == result["citations"] == result["sources"] == []
    assert result["coverage"][0]["scanned"] == result["coverage"][0]["matched"] == result["coverage"][0]["read"] == 0
    assert not result["coverage"][0]["limit_hit"]
    assert "秘密" not in json.dumps(result, ensure_ascii=False) and "隐藏会员标题" not in str(result)


def test_only_visible_text_can_be_quoted_and_truncated_or_summary_content_is_labeled():
    source = item("允许导语：公司净利增长。", body_state="teaser", role="fact",
                  metadata_safe={"cached_summary": "隐藏EPS99元", "logic_lines": [{"evidence": "隐藏股价9999"}]})
    result = build(channel(source))
    assert "隐藏" not in str(result)
    assert all(claim["role"] != "fact" for claim in result["claims"])
    assert all(not link["support_claim_ids"] and not link["counter_claim_ids"] for link in result["scenario_links"])
    assert all(link["context_claim_ids"] for link in result["scenario_links"])
    summary = build(channel(item("公司预期净利增长。", body_state="summary_only", role="fact")))
    assert all(claim["role"] == "analysis" and claim["verification"] == "derived_summary" for claim in summary["claims"])
    clipped = build(channel(item("公司净利增长。", metadata_safe={"text_truncated": True})))
    assert clipped["documents"][0]["body_state"] == "excerpt" and clipped["status"] == "partial"


@pytest.mark.parametrize("state", ["summary_only", "teaser", "title_only"])
def test_budget_truncation_never_upgrades_summary_teaser_or_title_into_directional_excerpt(state):
    result = build(channel(item("公司净利增长带动盈利改善。", body_state=state, role="fact",
                                metadata_safe={"text_truncated": True})))
    assert result["documents"][0]["body_state"] == state
    assert result["coverage"][0]["body_states"] == {state: 1}
    assert all(claim["body_state"] == state for claim in result["claims"])
    assert all(not link["support_claim_ids"] and not link["counter_claim_ids"] for link in result["scenario_links"])
    if state != "title_only":
        assert result["claims"] and all(link["context_claim_ids"] for link in result["scenario_links"])
    else:
        assert result["claims"] == []


@pytest.mark.parametrize("cue", ["预计", "预测", "预期", "有望", "可能", "估计"])
def test_forecast_language_in_full_fast_news_never_becomes_reported_fact(cue):
    result = build(channel(item(f"公司{cue}净利增长。", role="fact"), channel="site_fast_news"))
    assert result["claims"]
    assert all(claim["role"] == "opinion" and claim["verification"] == "source_forecast" for claim in result["claims"])


def test_stale_unknown_industry_and_peer_claims_are_scenario_context_only():
    sources = [
        item("净利增长带动盈利改善。", identifier="old", published_at="2025-01-01"),
        item("净利增长带动盈利改善。", identifier="unknown", published_at=None),
        item("行业需求改善带动净利增长。", identifier="industry", symbol="", relation_refs=[{"kind": "industry", "label": "测试行业", "verification": "context"}]),
        item("同行公司净利增长。", identifier="peer", symbol="", relation_refs=[{"kind": "peer", "label": "同行公司", "symbol": "000001", "verification": "candidate"}]),
    ]
    result = build(channel(*sources))
    assert any(claim["freshness"] == "historical" for claim in result["claims"])
    assert any(claim["freshness"] == "unknown" for claim in result["claims"])
    assert all(not link["support_claim_ids"] and not link["counter_claim_ids"] for link in result["scenario_links"])
    assert all(link["context_claim_ids"] for link in result["scenario_links"])
    assert next(row for row in result["sources"] if row["as_of"] == "2025-01-01")["status"] == "stale"


def test_fresh_full_context_role_with_verified_company_is_never_directional():
    result = build(channel(item("公司净利增长带动盈利改善。", role="context")))
    assert result["claims"] and all(claim["freshness"] == "current" for claim in result["claims"])
    assert result["relations"][0]["verification"] == "verified"
    assert all(not link["support_claim_ids"] and not link["counter_claim_ids"] for link in result["scenario_links"])
    assert all(link["context_claim_ids"] for link in result["scenario_links"])


def test_distinct_records_are_preserved_despite_same_title_date_and_text():
    text = "公司净利增长带动盈利改善。"
    result = build(channel(item(text, identifier="site_articles:article-one"), item(text, identifier="site_articles:article-two")))
    assert len(result["documents"]) == 2 and result["coverage"][0]["deduped"] == 0
    assert {doc["original_id"] for doc in result["documents"]} == {"article-one", "article-two"}
    repeated = build(channel(item(text, identifier="site_articles:article-one")),
                     channel(item(text, identifier="site_research:article-one"), channel="site_research"))
    assert len(repeated["documents"]) == 1 and repeated["coverage"][1]["deduped"] == 1
    assert repeated["documents"][0]["record_namespace"] == "realtime_messages"


def test_supply_relation_requires_visible_proof_and_does_not_invent_supplier_identity():
    result = build(channel(item("净利增长。", relation_refs=[{"kind": "upstream", "label": "候选供应商", "basis": "未在可见正文出现的供应合同", "verification": "verified"}])))
    assert result["relations"][0]["verification"] == "candidate"
    assert not any(link["support_claim_ids"] for link in result["scenario_links"])


def test_stock_target_price_is_not_product_pricing_or_business_model_evidence():
    result = build(channel(item("公司股票目标价格上调至200元，股价估值改善。")))
    assert any(claim["dimension"] == "earnings" for claim in result["claims"])
    assert not any(claim["dimension"] == "business" for claim in result["claims"])
    product = build(channel(item("公司产品价格提升，出厂价改善带动毛利增长。")))
    assert any(claim["dimension"] == "business" for claim in product["claims"])


@pytest.mark.parametrize("term", ["零售价", "零售价格", "售价", "销售单价", "销售价格"])
def test_retail_selling_price_excerpt_is_cited_as_business_evidence(term):
    text = f"贵州茅台自营门店飞天{term}已上调至1719元/瓶，较i茅台售价高出80元。"
    result = build_platform_research([channel(item(text, title="贵州茅台自营门店价格观察", body_state="excerpt"))],
                                    symbol="600519", name="贵州茅台", market="CN", now=NOW)
    claims = [claim for claim in result["claims"] if claim["dimension"] == "business"]
    assert claims and claims[0]["citations"][0]["quote"] == text
    assert claims[0]["body_state"] == "excerpt" and claims[0]["stance"] == "support"
    assert claims[0]["transmission_basis"] == "rule_hypothesis" and "产品价格" in claims[0]["transmission"]
    assert any(claim["id"] in result["scenario_links"][0]["support_claim_ids"] for claim in claims)
    stock = build(channel(item("公司股票目标价上调至200元，股价估值改善。")))
    assert not any(claim["dimension"] == "business" for claim in stock["claims"])


def test_market_value_comparison_remains_context_without_directional_scenario_links():
    result = build_platform_research([channel(item("长鑫科技市值相当于两个贵州茅台，股价估值参考需另行核验。",
        title="长鑫科技市值观察", symbol="", role="context", body_state="excerpt",
        relation_refs=[{"kind": "company", "label": "贵州茅台", "symbol": "600519", "basis": "visible_company_name", "verification": "context"}]))],
        symbol="600519", name="贵州茅台", market="CN", now=NOW)
    assert result["claims"] and any(claim["dimension"] == "earnings" for claim in result["claims"])
    assert all(not link["support_claim_ids"] and not link["counter_claim_ids"] for link in result["scenario_links"])
    assert all(link["context_claim_ids"] for link in result["scenario_links"])


@pytest.mark.parametrize("text", [
    "长鑫科技上市首日创四大纪录，市值突破3.3万亿元，相当于两个贵州茅台。",
    "公司库存突破历史纪录，需核验现金回款。",
    "公司产品销量突破百万瓶，营收增长。",
    "公司产能放量带动盈利改善。",
])
def test_operating_and_market_value_breakthroughs_are_not_technical_price_signals(text):
    result = build(channel(item(text)))
    assert not any(claim["dimension"] == "technical" for claim in result["claims"])


@pytest.mark.parametrize("text", ["公司股价突破前期高点。", "日线放量突破，成交量持续提升。", "收盘缩量回撤，需核验趋势。", "均线走弱，RSI回落。", "贵州茅台成交量持续放量。"])
def test_explicit_price_breakouts_and_strong_indicators_remain_technical(text):
    result = build(channel(item(text)))
    technical = [claim for claim in result["claims"] if claim["dimension"] == "technical"]
    assert technical and technical[0]["citations"][0]["quote"] == text


def test_later_price_context_does_not_upgrade_earlier_market_value_breakout_sentence():
    text = "公司市值突破3.3万亿元。公司股价放量突破前期高点，成交量持续提升。"
    result = build(channel(item(text)))
    technical = [claim for claim in result["claims"] if claim["dimension"] == "technical"]
    assert technical and all("市值" not in claim["claim"] for claim in technical)
    assert technical[0]["citations"][0]["quote"] == "公司股价放量突破前期高点，成交量持续提升。"


def test_body_only_direct_company_clause_is_attributed_without_upgrading_sector_document():
    text = "同行公司净利增长带动盈利改善。\n测试公司净利下滑，需核验回款。同行公司主营产品提价带动毛利增长。"
    result = build(channel(item(text, title="行业多股观察", symbol="", role="fact",
                                relation_refs=[{"kind": "company", "label": "测试公司", "symbol": "600519", "basis": "visible_company_name", "verification": "context"}])))
    assert result["claims"]
    assert all("测试公司" in claim["claim"] and "同行公司" not in claim["claim"] for claim in result["claims"])
    assert all(claim["role"] == "fact" for claim in result["claims"])
    assert result["scenario_links"][0]["counter_claim_ids"]
    assert result["documents"][0]["role"] == "context"
    document_relations = set(result["documents"][0]["relation_ids"])
    assert all(relation["verification"] == "context" for relation in result["relations"] if relation["id"] in document_relations)
    assert all(relation["citation_ids"] for relation in result["relations"] if relation["verification"] == "verified")


def test_conflicts_are_candidates_and_never_infer_reporting_period_from_publication_date():
    result = build(channel(item("本期净利增长。", identifier="positive"),
                           item("预计下期净利下滑。", identifier="negative", published_at="2026-10-12")))
    assert result["conflicts"]
    assert all(row["type"] == "unverified_comparability" and row["verification"] == "candidate" for row in result["conflicts"])
    assert all("不认定" in row["summary"] for row in result["conflicts"])
    assert all(row["id"].startswith("platform-conflict:") for row in result["conflicts"])
    periods = build(channel(item("本期净利增长。", identifier="positive", metadata_safe={"period": "2026H1", "currency": "CNY"}),
                            item("净利下滑。", identifier="negative", metadata_safe={"period": "2026Q3", "currency": "CNY"})))
    assert all(row["type"] == "different_period" for row in periods["conflicts"])
    assert all("可能同时成立" in row["summary"] for row in periods["conflicts"])


def test_negated_and_conditional_cues_do_not_become_observed_direction():
    result = build(channel(item("公司净利未增长；若需求恢复，盈利可能改善。")))
    assert result["claims"] and all(claim["stance"] == "neutral" for claim in result["claims"])
    assert not result["scenario_links"][0]["support_claim_ids"]


def test_future_documents_are_dropped_and_coverage_separates_window_from_material_dates():
    result = build(channel(item("净利增长。"), item("未来秘密业绩", identifier="future", published_at="2026-10-15"), scope="shared_authenticated"))
    assert len(result["documents"]) == 1 and "未来秘密" not in str(result)
    coverage = result["coverage"][0]
    assert coverage["period"] == {"from": "2026-10-10", "to": "2026-10-10"}
    assert coverage["window"]["days"] == 365 and coverage["window"]["from"].startswith("2025-10-13")
    assert coverage["scope"] == "shared_authenticated"


def test_explicit_source_transmission_and_rule_hypothesis_are_distinguished():
    result = build(channel(item("净利增长带动盈利改善。\n公司渠道库存上升。")))
    assert any(claim["transmission_basis"] == "source_explicit" for claim in result["claims"])
    rules = [claim for claim in result["claims"] if claim["transmission_basis"] == "rule_hypothesis"]
    assert rules and all("可能" in claim["transmission"] or "不能" in claim["transmission"] for claim in rules)
    assert all(claim["needs_review"] for claim in result["claims"])


def test_scenario_links_balance_channels_and_deduplicate_same_quote_across_dimensions():
    result = build(channel(item("主营产品价格提升带动营收净利和盈利增长。\n客户渠道改善带动毛利增长。", identifier="article")),
                   channel(item("公司营收增长带动盈利改善。", identifier="note"), channel="institution_notes"),
                   channel(item("公司现金流增长，盈利改善。", identifier="report"), channel="professional_reports"))
    by_id = {claim["id"]: claim for claim in result["claims"]}
    selected = [by_id[cid] for cid in result["scenario_links"][0]["support_claim_ids"]]
    doc_channels = {doc["id"]: doc["channel"] for doc in result["documents"]}
    assert {doc_channels[claim["document_ids"][0]] for claim in selected} == {"site_articles", "institution_notes", "professional_reports"}
    quotes = [claim["citations"][0]["id"] for claim in selected]
    assert len(quotes) == len(set(quotes))


def test_deep_platform_claims_enrich_reasons_and_references_without_changing_numeric_prices():
    inputs = {"name": "测试公司", "quote": {"price": 100, "currency": "CNY", "market_time": "2026-10-13"},
              "valuation": {"pe_ratio": 20, "currency": "CNY"},
              "forecasts": {"year": 2026, "currency": "CNY", "eps_median": 6, "broker_count": 2, "as_of": "2026-10-10"}, "now": NOW}
    before = intelligence.build_stock_intelligence("600519", "CN", **inputs)
    after = intelligence.build_stock_intelligence("600519", "CN", platform_channels=[channel(
        item("公司净利增长带动盈利改善；原文目标股价99999元，EPS999元仅是观点。", identifier="bull"),
        item("公司净利下滑导致盈利下调，需验证回款。", identifier="bear"))], **inputs)
    assert [case["price"] for case in before["valuation"]["scenarios"]] == [case["price"] for case in after["valuation"]["scenarios"]]
    assert before["valuation"]["anchor"] == after["valuation"]["anchor"]
    assert after["valuation"]["scenarios"][0]["platform_claim_ids"]
    assert any("平台参考线索" in reason for reason in after["valuation"]["scenarios"][0]["reasons"])
    assert any("平台反向线索" in reason for reason in after["valuation"]["scenarios"][0]["invalidation"])
    assert any("平台经营验证" in watch["event"] for watch in after["catalysts"])
    source_ids = {source["id"] for source in after["sources"]}
    for referenced in after["dimensions"] + after["valuation"]["scenarios"] + after["catalysts"]:
        assert set(referenced["source_ids"]) <= source_ids
    assert not any(gap.startswith("平台暂无该股") for gap in after["data_gaps"])


def test_legacy_and_new_platform_paths_do_not_double_count_records_or_keep_title_scan():
    visible = "公司净利增长，需核验经营回款。"
    result = intelligence.build_stock_intelligence("600519", "CN", name="测试公司", now=NOW,
        platform_messages=[{"id": "one", "title": "旧路径标题扫描", "content": visible, "created_at": "2026-10-10"}],
        platform_channels=[channel(item(visible, identifier="site_articles:one", title="旧路径标题扫描"))])
    platform_evidence = [entry for entry in result["evidence"] if entry["origin"] == "platform"]
    assert len(platform_evidence) == len(result["platform_research"]["documents"]) == 1
    assert platform_evidence[0]["original_id"] == "one"
    assert platform_evidence[0]["kind"] == "平台文章"
    assert result["platform_research"]["documents"][0]["channel"] == "site_articles"
    assert "platform:one" not in {source["id"] for source in result["sources"]}
    news = next(dimension for dimension in result["dimensions"] if dimension["key"] == "news")
    assert not any(point == "2026-10-10 · 旧路径标题扫描" for point in news["evidence"])
    context = intelligence.build_stock_intelligence("600519", "CN", name="测试公司", now=NOW,
        platform_channels=[channel(item(visible, role="context"))])
    assert all(not case["platform_claim_ids"] for case in context["valuation"]["scenarios"])
    assert not any("平台参考线索" in reason for case in context["valuation"]["scenarios"] for reason in case["reasons"])
    assert not any("平台经营验证" in watch["event"] for watch in context["catalysts"])


def test_gather_request_loader_receives_verified_public_profile_and_skips_legacy_raw_path(monkeypatch):
    from deepfocus_api import cn_consensus, eastmoney_data, market_data, stock_news, valuation_source
    async def empty(*args, **kwargs):
        return None
    for module, funcs in [(eastmoney_data, ["fetch_eastmoney_earnings", "fetch_eastmoney_kline", "fetch_sina_ohlc", "fetch_eastmoney_cashflow", "fetch_eastmoney_balance"]),
                          (market_data, ["fetch_market_quotes"]), (stock_news, ["fetch_stock_news"]),
                          (valuation_source, ["fetch_valuation"]), (cn_consensus, ["fetch_cn_consensus"]),
                          (intelligence, ["fetch_annual_forecasts", "fetch_verified_fund_flow"])]:
        for function in funcs:
            monkeypatch.setattr(module, function, empty)
    async def profile(*args, **kwargs):
        return {"name": "核验公司", "industry": "核验行业"}
    monkeypatch.setattr(intelligence, "fetch_company_profile", profile)
    def forbidden(*args, **kwargs):
        pytest.fail("新 loader 存在时不得重读 legacy symbol_linker/SQL 正文")
    monkeypatch.setattr(intelligence, "load_visible_platform_messages", forbidden)
    queries = []
    async def loader(query):
        queries.append(query)
        return [channel(item("公司净利增长。"))]
    result = asyncio.run(intelligence.gather_stock_intelligence("600519", name="别名", message_view=lambda x: x, platform_source_loader=loader))
    assert queries[0]["name"] == "核验公司" and queries[0]["industry"] == "核验行业"
    assert queries[0]["aliases"] == ["别名"] and queries[0]["window_days"] == 365
    assert result["platform_research"]["documents"] and all(case["price"] is None for case in result["valuation"]["scenarios"])


def body_company_item(text, **changes):
    return item(text, title="行业多公司纪要", symbol="", role="opinion",
                relation_refs=[{"kind": "company", "label": "测试公司", "symbol": "600519",
                                "basis": "visible_company_name", "verification": "context"}], **changes)


@pytest.mark.parametrize("state", ["full", "excerpt"])
@pytest.mark.parametrize("text", [
    "[玫瑰]测试公司：按照组织规范化建设，并未停止对渠道市场活动的支持。",
    "①测试公司：9月货已至，回款进度82%+，预计主营产品同比双位数增长。",
    "测试公司净利增长，回款改善。",
    "600519：净利增长，回款改善。",
    "600519净利增长，回款改善。",
])
def test_explicit_company_heading_or_subject_verifies_only_its_visible_claim(state, text):
    result = build(channel(body_company_item(text, body_state=state), channel="institution_notes"))
    assert result["claims"]
    verified = {relation["id"] for relation in result["relations"] if relation["verification"] == "verified"}
    assert verified and all(verified.intersection(claim["relation_ids"]) for claim in result["claims"])
    assert not verified.intersection(result["documents"][0]["relation_ids"])
    assert result["documents"][0]["role"] == "context"
    for claim in result["claims"]:
        assert claim["needs_review"] and claim["role"] == "opinion"
        for citation in claim["citations"]:
            assert text[citation["start"]:citation["end"]] == citation["quote"]
    if "预计" in text:
        assert result["scenario_links"][0]["support_claim_ids"]
        assert all(claim["verification"] == "source_forecast" for claim in result["claims"])


@pytest.mark.parametrize("text", [
    "推荐测试公司、同行公司，行业净利增长。",
    "建议关注测试公司净利增长。",
    "测试公司供应商净利增长。",
    "测试公司主营产品供应商净利增长。",
    "测试公司业务伙伴利润改善。",
    "测试公司：同行公司净利增长。",
    "测试公司：其供应商股价突破。",
    "测试公司：行业毛利改善。",
    "测试公司：2026年同行公司净利增长20%。",
    "测试公司：9月其供应商净利增长。",
    "测试公司支持供应商产品价格提升。",
    "测试公司公告显示供应商产品价格提升。",
    "测试公司发布同行产品价格上调公告。",
    "测试公司电子：净利增长。",
    "6005190净利增长。",
])
def test_mentions_lists_and_related_party_subjects_remain_context(text):
    result = build(channel(body_company_item(text)))
    assert not any(relation["verification"] == "verified" for relation in result["relations"])
    assert all(claim["role"] == "context" for claim in result["claims"])
    assert all(not link["support_claim_ids"] and not link["counter_claim_ids"] for link in result["scenario_links"])


def test_peer_clause_cannot_inherit_named_company_subject_or_technical_direction():
    text = "测试公司净利增长，同行公司股价放量突破。"
    result = build(channel(body_company_item(text)))
    assert result["claims"] and result["scenario_links"][0]["support_claim_ids"]
    assert all(claim["claim"] == "测试公司净利增长" for claim in result["claims"])
    assert not any(claim["dimension"] == "technical" for claim in result["claims"])


@pytest.mark.parametrize("text", ["测试公司渠道库存改善支撑产品价格提升。", "测试公司产品价格提升，终端经营阻力下降。", "测试公司产品交易量突破100万瓶。", "测试公司产品成交量突破100万吨。"])
def test_product_pricing_or_business_support_is_not_stock_technical_evidence(text):
    result = build(channel(body_company_item(text)))
    assert any(claim["dimension"] == "business" for claim in result["claims"])
    assert not any(claim["dimension"] == "technical" for claim in result["claims"])


@pytest.mark.parametrize("changes", [
    {"body_state": "teaser"}, {"body_state": "summary_only"}, {"body_state": "title_only"},
    {"published_at": "2025-01-01"}, {"published_at": None},
])
def test_explicit_company_text_does_not_bypass_body_or_date_eligibility(changes):
    result = build(channel(body_company_item("测试公司：预计净利增长。", **changes)))
    assert all(not link["support_claim_ids"] and not link["counter_claim_ids"] for link in result["scenario_links"])


@pytest.mark.parametrize("text", ["测试公司预计净利增长。", "测试公司净利将增长20%。", "测试公司净利将继续增长20%。", "测试公司净利将持续同比增长20%。", "测试公司净利将于2027年增长。", "测试公司未来净利增长空间大。", "测试公司目标价上调。"])
def test_explicit_subject_forecasts_remain_source_opinions_when_source_reports_facts(text):
    result = build(channel(body_company_item(text, metadata_safe={"source_role": "fact"})))
    assert result["claims"]
    assert all(claim["role"] == "opinion" and claim["verification"] == "source_forecast" for claim in result["claims"])


def test_forecast_qualifier_cannot_hide_related_party_subject_in_following_clause():
    result = build(channel(body_company_item("测试公司净利增长，预计其供应商产品价格提升。")))
    assert result["claims"] and all(claim["claim"] == "测试公司净利增长" for claim in result["claims"])
    assert not any(claim["dimension"] == "business" for claim in result["claims"])


def test_separate_direct_company_paragraphs_keep_conflict_candidates_without_document_upgrade():
    result = build(channel(body_company_item("测试公司净利增长。", identifier="positive"),
                           body_company_item("测试公司净利下滑。", identifier="negative")))
    assert result["conflicts"] and all(row["verification"] == "candidate" for row in result["conflicts"])
    assert all(document["role"] == "context" for document in result["documents"])


def test_unselected_company_paragraphs_leave_no_orphan_verified_relation():
    text = "\n".join(f"测试公司净利增长{index}%，需核验回款。" for index in range(5))
    result = build(channel(body_company_item(text)))
    citation_ids = {citation["id"] for citation in result["citations"]}
    for relation in result["relations"]:
        if relation["verification"] == "verified":
            assert relation["citation_ids"] and set(relation["citation_ids"]) <= citation_ids


@pytest.mark.parametrize("key,label", [
    ("site_fast_news", "平台快讯"), ("site_articles", "平台文章"), ("site_research", "平台本地研报"),
    ("recent_research", "投行研报已有解读"), ("institution_notes", "机构纪要"), ("celebrity_views", "名人观点"),
    ("professional_reports", "专业财报共享库"), ("owned_research", "我的已完成研究"),
    ("shared_sources", "已明确公开的资料库"),
])
def test_document_channel_has_user_readable_label_without_changing_internal_key(key, label):
    result = build(channel(item("公司净利增长。"), channel=key))
    assert result["documents"][0]["channel"] == key
    assert result["documents"][0]["channel_label"] == label


def test_paragraph_attribution_does_not_change_numeric_scenarios_or_inherit_next_sentence():
    inputs = {"name": "测试公司", "quote": {"price": 100, "currency": "CNY", "market_time": "2026-10-13"},
              "valuation": {"pe_ratio": 20, "currency": "CNY"},
              "forecasts": {"year": 2026, "currency": "CNY", "eps_median": 6, "broker_count": 2, "as_of": "2026-10-10"}, "now": NOW}
    before = intelligence.build_stock_intelligence("600519", "CN", **inputs)
    after = intelligence.build_stock_intelligence("600519", "CN", platform_channels=[channel(body_company_item(
        "测试公司：净利增长。\n股价目标99999元，EPS预测999元。\n同行公司股价突破。"))], **inputs)
    assert [case["price"] for case in before["valuation"]["scenarios"]] == [case["price"] for case in after["valuation"]["scenarios"]]
    assert before["valuation"]["anchor"] == after["valuation"]["anchor"]
    assert after["platform_research"]["claims"]
    assert all("测试公司" in claim["claim"] and "999" not in claim["claim"] for claim in after["platform_research"]["claims"])
