from __future__ import annotations

from types import SimpleNamespace

from deepfocus_api.research_index import build_research_index, freshness_score


def test_research_index_normalizes_tags_dedupes_within_category_and_keeps_cross_category(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPFOCUS_CONTENT_ONTOLOGY_DB_PATH", str(tmp_path / "ontology.sqlite3"))
    stock = SimpleNamespace(symbol="600519", name="贵州茅台", market="CN")
    modules = {
        "get_site_fast_news": [
            {"title": "贵州茅台提价，渠道库存改善", "snippet": "公司上调价格，库存压力缓解", "published_at": "2026-08-21T08:00:00Z", "tone": "success"},
            {"title": "贵州茅台提价，渠道库存改善", "snippet": "重复快讯", "published_at": "2026-08-21T07:00:00Z", "tone": "success"},
        ],
        "get_stock_research": {"ok": True, "data": [
            {"title": "贵州茅台提价，渠道库存改善", "summary": "机构认为盈利有望改善", "org": "某证券", "date": "2026-08-20"},
        ]},
        "get_institution_notes": {"items": [
            {"title": "贵州茅台渠道调研纪要", "summary": "库存与批价仍需跟踪", "date": "2026-08-21", "tags": ["白酒", "渠道"]},
        ], "count": 1, "source": "稻草财经机构纪要"},
        "get_celebrity_views": {"note": "该功能暂未开放"},
    }

    result = build_research_index(modules, objective="贵州茅台估值和渠道", stock=stock)

    assert result["stats"]["raw_items"] == 4
    assert result["stats"]["deduped_items"] == 3
    assert result["coverage"]["site_fast_news"]["selected_count"] == 1
    assert result["coverage"]["stock_research"]["selected_count"] == 1
    assert result["coverage"]["institution_notes"]["selected_count"] == 1
    first = result["items"][0]
    assert first["annotation_version"] == "rules-v2"
    assert {tag["facet"] for tag in first["tags"]} >= {"content_type", "access", "evidence_role"}
    assert any(tag["label"] in {"消费", "价格变化", "支持论点", "削弱论点"} for tag in first["tags"])


def test_freshness_uses_category_half_life():
    assert freshness_score("2026-08-21T00:00:00Z", "site_fast_news", now=__import__("datetime").datetime(2026, 8, 21, tzinfo=__import__("datetime").timezone.utc)) == 1.0
    assert freshness_score("", "site_fast_news") == 0.35


def test_stock_content_filters_unrelated_entities_and_keeps_provenance(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPFOCUS_CONTENT_ONTOLOGY_DB_PATH", str(tmp_path / "ontology.sqlite3"))
    stock = SimpleNamespace(symbol="002594", name="比亚迪", market="CN")
    result = build_research_index(
        {
            "get_site_articles": [
                {"title": "比亚迪销量与海外渠道跟踪", "summary": "公司交付和出口", "url": "https://example.com/byd"},
                {"title": "德国车展吸引力上升", "summary": "吉利与宁德时代等行业消息", "url": "https://example.com/industry"},
            ],
        },
        objective="比亚迪估值",
        stock=stock,
    )
    titles = [item["title"] for item in result["items"]]
    assert "比亚迪销量与海外渠道跟踪" in titles
    assert "德国车展吸引力上升" not in titles
    digest = next(item for item in result["digest"] if item["title"] == "比亚迪销量与海外渠道跟踪")
    assert digest["url"] == "https://example.com/byd"
    assert digest["entity_relation"] in {"direct", "related"}


def test_research_index_extracts_entities_claims_and_sector_clusters(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPFOCUS_CONTENT_ONTOLOGY_DB_PATH", str(tmp_path / "ontology.sqlite3"))
    result = build_research_index(
        {
            "get_recent_research": [
                {"title": "建滔集团：目标价至90港元，给予买入评级", "summary": "AI算力需求强劲，对应27年10倍PE", "org": "花旗", "date": "2026-08-29"},
                {"title": "英伟达目标价180美元，overweight", "summary": "GPU与算力景气延续", "org": "摩根士丹利", "date": "2026-08-29"},
            ],
            "get_institution_notes": {"items": [
                {"title": "建滔集团电子布提价纪要", "summary": "目标位95港元，机构增持，按12倍PE估值", "date": "2026-08-30"},
            ]},
        },
        objective="最近两天机构纪要和研报",
    )
    analysis = result["analysis"]
    names = {item["name"] for item in analysis["entity_candidates"]}
    assert {"建滔集团", "英伟达"} <= names
    kingboard = next(item for item in analysis["entity_candidates"] if item["name"] == "建滔集团")
    assert {90.0, 95.0} <= {item["value"] for item in kingboard["target_prices"]}
    assert kingboard["ratings"][0]["rating"] in {"buy", "strong_buy"}
    assert any(item["theme"] == "人工智能" for item in analysis["sector_clusters"])
    assert analysis["claim_counts"]["target_price_mentions"] >= 3
