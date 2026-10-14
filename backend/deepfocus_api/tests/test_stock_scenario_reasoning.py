"""Scenario explanations must retain arithmetic, entitlement and source scope."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from deepfocus_api.stock_intelligence import build_stock_intelligence
from deepfocus_api.stock_scenario_reasoning import enrich_stock_scenario_reasoning


NOW = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)


def document(text, identifier="support", **changes):
    return {"id": identifier, "title": "测试公司经营跟踪", "visible_text": text,
            "body_state": "full", "role": "opinion", "published_at": "2026-10-10",
            "source_name": "已授权机构纪要", "symbol": "600519", **changes}


def channel(*items, **changes):
    return {"channel": "institution_notes", "label": "机构纪要", "status": "available",
            "items": list(items), "coverage": {"complete": True, "scanned": len(items), "matched": len(items)}, **changes}


def snapshot(**changes):
    inputs = {
        "name": "测试公司", "now": NOW,
        "quote": {"price": 100, "currency": "CNY", "provider": "eastmoney", "market_time": "2026-10-14"},
        "valuation": {"pe_ratio": 20, "currency": "CNY", "as_of": "2026-10-13"},
        "financials": {"report_date": "2026-06-30", "eps": 2, "profit_yoy": -1.95, "revenue_yoy": 5,
                       "net_income": 100, "revenue": 200, "gross_margin": 80},
        "cashflow": {"report_date": "2026-06-30", "ocf": 159},
        "forecasts": {"year": 2026, "currency": "CNY", "eps_median": 6, "eps_low": 5.8, "eps_high": 6.2,
                      "broker_count": 4, "as_of": "2026-10-01", "oldest_report": "2026-09-15", "reports": []},
        "history": [(f"2026-09-{i:02d}", 100 + i / 10) for i in range(1, 31)] + [("2026-10-13", 105)],
        "flow": {"main_flow_5d": 30, "flow_start_date": "2026-10-12", "flow_end_date": "2026-10-13",
                 "as_of": "2026-10-14", "currency": "CNY", "series": [
                     {"date": "2026-10-12", "main_net": 10, "close": 101},
                     {"date": "2026-10-13", "main_net": 20, "close": 102}]},
        "platform_channels": [channel(
            document("公司回款改善带动经营现金流增长，需关注终端需求。", metadata_safe={"period": "2026H1"}),
            document("公司净利下滑导致盈利承压，需验证库存。", "counter", metadata_safe={"period": "2026H1"}))],
    }
    inputs.update(changes)
    return build_stock_intelligence("600519", "CN", **inputs)


def scenarios(result):
    return {row["key"]: row for row in result["valuation"]["scenarios"]}


def driver(result, key, scenario="optimistic"):
    return next(row for row in scenarios(result)[scenario]["reasoning"]["drivers"] if row["key"] == key)


def test_company_profile_details_are_cited_context_without_implied_moat():
    result = snapshot(profile={"description": "测试公司销售订阅软件。", "business_scope": "软件服务与研发。", "as_of": "2026-10-01"})
    observations = driver(result, "business")["observed"]
    description = next(row for row in observations if row["id"] == "business:description")
    assert "销售订阅软件" in description["statement"] and "护城河" in description["statement"]
    assert description["source_ids"] == ["profile"] and description["role"] == "context" and description["direction"] == "unknown"


@pytest.mark.parametrize("change", [{"status": "insufficient"}, {"amount_unit": "未核验"}, {"currency": "未知"}])
def test_daily_capital_records_do_not_establish_direction_without_valid_status_units_currency(change):
    result = snapshot()
    result["capital"].update(change)
    enrich_stock_scenario_reasoning(result)
    row = next(row for row in driver(result, "capital")["observed"] if row["id"] == "capital:recent-main")
    assert row["status"] != "available" and row["direction"] == "unknown"


def test_counter_claims_are_not_also_listed_as_support_for_the_same_scenario():
    result = snapshot()
    for key in scenarios(result):
        for row in scenarios(result)[key]["reasoning"]["drivers"]:
            assert not set(row["platform_claim_ids"]) & set(row["counter_claim_ids"])


def test_compact_scenario_reasons_explain_different_numeric_requirements_and_observed_limits():
    rows = scenarios(snapshot())
    optimistic = rows["optimistic"]["reasoning"]["card_summary"]
    base = rows["base"]["reasoning"]["card_summary"]
    pessimistic = rows["pessimistic"]["reasoning"]["card_summary"]
    assert "EPS 6.90" in optimistic and "PE 24.00" in optimistic and "-1.95%" in optimistic and "高于" in optimistic
    assert "1.59 倍" in base and "仍待财报核验" in base
    assert "EPS 5.10" in pessimistic and "PE 16.00" in pessimistic and "同时发生" in pessimistic
    assert all("暂不估价" in row["reasoning"]["card_summary"] for row in scenarios(snapshot(valuation=None)).values())


def test_enrichment_is_deterministic_idempotent_and_retains_all_original_arithmetic():
    result = snapshot()
    original = deepcopy(result)
    assert enrich_stock_scenario_reasoning(result) is result
    once = deepcopy(result)
    assert enrich_stock_scenario_reasoning(result) == once
    assert enrich_stock_scenario_reasoning(deepcopy(original)) == once
    for key, current in scenarios(result).items():
        previous = scenarios(original)[key]
        for field in previous:
            assert current[field] == previous[field]
        bridge = current["reasoning"]["numeric_bridge"]
        assert bridge["price"] == previous["price"]
        assert bridge["base_price"] + bridge["eps_effect"] + bridge["pe_effect"] + bridge["combined_effect"] == pytest.approx(previous["price"])
    assert result["valuation"]["anchor"] == original["valuation"]["anchor"]


def test_each_scenario_has_six_distinct_conditions_and_dated_company_metrics():
    result = enrich_stock_scenario_reasoning(snapshot())
    assert all(len(row["reasoning"]["drivers"]) == 6 for row in scenarios(result).values())
    condition = driver(result, "fundamentals")["condition"]
    assert "测试公司" in condition and "-1.95" in condition and "2026-06-30" in condition
    assert "全年 EPS +15%" in condition and "需要验证" in condition
    fundamental = driver(result, "fundamentals")
    ratio = next(row for row in fundamental["observed"] if row["label"] == "经营现金流/归母净利")
    assert ratio["value"] == 1.59 and ratio["unit"] == "倍" and ratio["as_of"] == "2026-06-30"
    assert ratio["source_ids"] == ["financials", "cashflow"] and ratio["status"] == "available"
    assert len({driver(result, "fundamentals", key)["condition"] for key in scenarios(result)}) == 3
    assert driver(result, "fundamentals")["assessment"] == "mixed"


def test_eps_sample_dispersion_is_not_probability_and_is_independent_of_fixed_factors():
    result = enrich_stock_scenario_reasoning(snapshot())
    expected = {"optimistic": "above", "base": "within", "pessimistic": "below"}
    for key, row in scenarios(result).items():
        sample = row["reasoning"]["numeric_bridge"]["forecast_sample"]
        assert sample["eps_low"] == 5.8 and sample["eps_median"] == 6 and sample["eps_high"] == 6.2
        assert sample["year"] == 2026 and sample["currency"] == "CNY" and sample["broker_count"] == 4
        assert sample["scenario_vs_sample"] == expected[key]
        assert "采样范围 5.80–6.20 CNY" in driver(result, "earnings", key)["condition"]
        assert "不是概率" in sample["note"]
        assert "固定因子" in driver(result, "earnings", key)["condition"]


def test_sources_deduplicate_and_current_opposing_platform_claims_are_kept():
    result = enrich_stock_scenario_reasoning(snapshot())
    registered = {row["id"] for row in result["sources"]}
    claims = {row["id"]: row for row in result["platform_research"]["claims"]}
    optimistic = scenarios(result)["optimistic"]["reasoning"]
    pessimistic = scenarios(result)["pessimistic"]["reasoning"]
    assert optimistic["counter_claim_ids"] and pessimistic["counter_claim_ids"]
    assert any(claims[cid]["stance"] == "counter" for cid in optimistic["counter_claim_ids"])
    assert any(claims[cid]["stance"] == "support" for cid in pessimistic["counter_claim_ids"])
    for row in scenarios(result).values():
        reasoning = row["reasoning"]
        assert set(reasoning["source_ids"]) <= registered
        assert len(reasoning["source_ids"]) == len(set(reasoning["source_ids"]))
        assert set(reasoning["platform_claim_ids"]) <= set(claims)
    explicit = driver(result, "fundamentals")["transmission"]
    assert "平台原文传导（观点/分析" in explicit and "研究" not in explicit.split("平台原文传导", 1)[0]
    assert "经营传导假设" in explicit


@pytest.mark.parametrize("body_state,published,relations", [
    ("summary_only", "2026-10-10", None), ("teaser", "2026-10-10", None),
    ("full", "2024-01-01", None), ("full", None, None),
    ("full", "2026-10-10", [{"kind": "company", "symbol": "600519", "verification": "context"}]),
])
def test_summary_historical_unknown_and_unverified_subject_remain_background(body_state, published, relations):
    changes = {"body_state": body_state, "published_at": published}
    if relations is not None:
        changes["entity_refs"] = relations
        changes["symbol"] = None
    result = enrich_stock_scenario_reasoning(snapshot(platform_channels=[channel(document("测试公司净利增长带动盈利改善。", **changes))]))
    # An explicit subject in full current text can legitimately upgrade a
    # collector candidate; use an industry sentence for the unverified case.
    if relations is not None:
        result = enrich_stock_scenario_reasoning(snapshot(platform_channels=[channel(document("测试公司与白酒行业净利增长带动盈利改善。", **changes))]))
    assert not result["scenario_reasoning"]["coverage"]["current_company_claims"]
    assert result["scenario_reasoning"]["coverage"]["background_claims"]
    for scenario in scenarios(result).values():
        assert not scenario["reasoning"]["platform_claim_ids"]
        assert scenario["reasoning"]["context_claim_ids"]
        assert all(row["direction"] == "unknown" for detail in scenario["reasoning"]["drivers"]
                   for row in detail["observed"] if row["origin"] == "platform")


def test_broker_predictions_and_plain_text_target_numbers_never_become_price_inputs():
    before = snapshot(platform_channels=[])
    after = snapshot(platform_channels=[channel(document("公司预计净利增长带动盈利改善，目标股价9999元，EPS999元。", role="fact"))])
    enrich_stock_scenario_reasoning(after)
    assert [row["price"] for row in before["valuation"]["scenarios"]] == [row["price"] for row in after["valuation"]["scenarios"]]
    observed = [row for scenario in scenarios(after).values() for detail in scenario["reasoning"]["drivers"] for row in detail["observed"] if row["origin"] == "platform"]
    assert observed and all(row["role"] == "opinion" for row in observed)
    assert all(row["value"] is None for row in observed)


def test_peer_subject_does_not_inherit_target_document_title():
    result = enrich_stock_scenario_reasoning(snapshot(platform_channels=[channel(document("五粮液净利润增长20%，收入增长10%。"))]))
    assert result["platform_research"]["claims"]
    assert result["scenario_reasoning"]["coverage"]["current_company_claims"] == 0
    assert all(not row["reasoning"]["platform_claim_ids"] for row in scenarios(result).values())


def test_orphan_revision_source_and_quote_bindings_do_not_reappear_in_scenario():
    result = snapshot()
    first = result["platform_research"]["claims"][0]
    stale_id = first["id"]
    first["citations"][0]["document_revision"] = "wrong-revision"
    second = result["platform_research"]["claims"][1]
    wrong_source_id = second["id"]
    second["source_ids"].append("absent-source")
    enrich_stock_scenario_reasoning(result)
    for scenario in scenarios(result).values():
        reasoning = scenario["reasoning"]
        assert stale_id not in reasoning["platform_claim_ids"] + reasoning["context_claim_ids"]
        assert wrong_source_id not in reasoning["platform_claim_ids"] + reasoning["context_claim_ids"]


def test_visibility_is_request_bound_and_no_denied_or_unowned_text_is_reflected():
    visible = channel(document("公司回款改善带动现金流增长。", "owner-visible"), channel="owned_research")
    denied = channel(document("隐藏会员秘密盈利增长999%。", "private-denied"), status="access_required")
    result = enrich_stock_scenario_reasoning(snapshot(platform_channels=[visible, denied]))
    assert "隐藏会员秘密" not in str(result)
    assert result["scenario_reasoning"]["coverage"]["current_company_claims"] > 0
    all_ids = {row["id"] for row in result["platform_research"]["claims"]}
    assert set(scenarios(result)["optimistic"]["reasoning"]["platform_claim_ids"]) <= all_ids


def test_undated_or_stale_facts_are_visible_but_never_current_support():
    result = enrich_stock_scenario_reasoning(snapshot(
        financials={"eps": 2, "profit_yoy": 50, "net_income": 100}, cashflow={"ocf": 159},
        history=[("2024-01-01", 100 + i) for i in range(25)],
        flow={"main_flow_5d": 1e9, "as_of": "2026-10-14"}, platform_channels=[]))
    fundamentals = driver(result, "fundamentals")
    assert fundamentals["assessment"] == "unknown"
    assert all(row["direction"] == "unknown" for row in fundamentals["observed"] if row["label"] != "PE TTM")
    assert not any(row["label"] == "经营现金流/归母净利" and row["status"] == "available" for row in fundamentals["observed"])
    capital = driver(result, "capital")
    assert capital["assessment"] == "unknown"
    assert any(row["value"] == 10 for row in capital["observed"])
    assert all(row["status"] == "unknown" for row in capital["observed"])
    assert any("日期" in row["statement"] and "未核验" in row["statement"] for row in capital["observed"])


@pytest.mark.parametrize("changes", [
    {"history_adjustment": "复权未核验（新浪源未声明复权）"},
    {"history_currency": "USD"}, {"history_provider": "unknown", "history_currency": ""},
])
def test_technical_unverified_adjustment_or_currency_cannot_confirm_trade(changes):
    result = enrich_stock_scenario_reasoning(snapshot(**changes))
    detail = driver(result, "technical")
    assert detail["assessment"] == "unknown"
    assert all(row["direction"] == "unknown" for row in detail["observed"])
    assert any("未接入成交量" in line for line in detail["limitations"])
    assert "不机械改变长期 EPS" in detail["transmission"]


def test_empty_data_retains_six_explicit_gaps_and_null_prices_not_zeros():
    result = build_stock_intelligence("TSLA", "US", name="Tesla", now=NOW, platform_channels=[])
    enrich_stock_scenario_reasoning(result)
    assert result["scenario_reasoning"]["coverage"]["observed_dimensions"] == 0
    for scenario in scenarios(result).values():
        reasoning = scenario["reasoning"]
        assert reasoning["numeric_bridge"]["price"] is None
        assert reasoning["numeric_bridge"]["base_eps"] is None
        assert reasoning["numeric_bridge"]["eps_effect"] is None
        assert all(row["assessment"] == "unknown" for row in reasoning["drivers"])
        assert all(row["observed"] for row in reasoning["drivers"])


def test_cross_currency_and_missing_forecasts_keep_sensitivity_without_inferred_annual_eps():
    forecast = {"year": 2026, "currency": "USD", "eps_median": 6, "eps_low": 5, "eps_high": 7,
                "broker_count": 4, "as_of": "2026-10-01", "reports": []}
    for result in (snapshot(forecasts=forecast), snapshot(forecasts=None)):
        enrich_stock_scenario_reasoning(result)
        for scenario in scenarios(result).values():
            bridge = scenario["reasoning"]["numeric_bridge"]
            assert bridge["mode"] == "sensitivity" and bridge["base_price"] == 100
            assert bridge["base_eps"] is None and bridge["scenario_eps"] is None and bridge["forecast_year"] is None
            assert bridge["forecast_sample"]["eps_low"] is None
            assert bridge["forecast_sample"]["scenario_vs_sample"] == "unknown"


def test_pe_market_date_missing_is_not_replaced_by_fetched_time():
    result = enrich_stock_scenario_reasoning(snapshot(valuation={"pe_ratio": 20, "currency": "CNY"}))
    bridge = scenarios(result)["optimistic"]["reasoning"]["numeric_bridge"]
    assert bridge["price"] == 165.6
    assert any("估值" in line and "数据日期未核验" in line for line in bridge["gaps"])
    pe = next(row for row in driver(result, "fundamentals")["observed"] if row["label"] == "PE TTM")
    assert pe["status"] == "unknown" and pe["as_of"] is None


@pytest.mark.parametrize("value,status", [(None, "missing"), (0, "unknown")])
def test_missing_capital_scalar_is_readable_without_reinterpreting_zero(value, status):
    result = snapshot(flow=None)
    dimension = next(row for row in result["dimensions"] if row["key"] == "capital")
    dimension["metrics"] = [{"label": "五日主力净额", "value": value, "unit": "元"}]
    enrich_stock_scenario_reasoning(result)
    row = driver(result, "capital")["observed"][0]
    assert row["value"] == value and row["status"] == status and row["direction"] == "unknown"
    assert "None" not in row["statement"]
    assert ("未提供资金数据" in row["statement"]) == (value is None)
