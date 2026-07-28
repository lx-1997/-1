from __future__ import annotations

from deepfocus_api.content_ontology import (
    annotate_content,
    build_content_map,
)


SECURITY = {
    "security_id": "security:cn:600519.SH",
    "label": "贵州茅台",
    "canonical_key": "600519.SH",
    "market": "CN",
}


def test_content_annotation_produces_typed_multi_facets(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPFOCUS_CONTENT_ONTOLOGY_DB_PATH", str(tmp_path / "ontology.sqlite3"))
    result = annotate_content(
        content_id="message:test-1",
        content_type="flash",
        title="贵州茅台再次提价，渠道库存仍然承压",
        text="公司上调建议零售价，但短期渠道库存仍处高位。",
        source_name="Bloomberg",
        published_at="2026-07-28T08:00:00Z",
        security_context=SECURITY,
    )

    facets = {tag["facet"] for tag in result["tags"]}
    assert result["tag_count"] >= 7
    assert {"content_type", "entity", "event", "theme", "signal", "horizon", "source"} <= facets
    assert result["entities"][0]["id"] == SECURITY["security_id"]
    assert any(tag["code"] == "price_change" for tag in result["tags"])
    assert any(tag["code"] == "mixed" for tag in result["tags"])


def test_content_map_combines_four_content_types_and_builds_graph(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPFOCUS_CONTENT_ONTOLOGY_DB_PATH", str(tmp_path / "ontology.sqlite3"))
    messages = [
        {
            "id": "f1", "topic": "快讯", "title": "贵州茅台宣布回购",
            "content": "公司公告", "source_name": "交易所", "tags": ["快讯"],
            "created_at": "2026-07-28T08:00:00Z",
        },
        {
            "id": "a1", "topic": "文章", "title": "贵州茅台渠道库存调查",
            "content": "短期批价承压", "source_name": "DAO财经", "tags": ["文章"],
            "created_at": "2026-07-28T07:00:00Z",
        },
        {
            "id": "r1", "topic": "研报", "title": "贵州茅台深度研究",
            "content": "长期品牌壁垒", "source_name": "某证券", "tags": ["研报"],
            "created_at": "2026-07-27T07:00:00Z",
        },
    ]
    result = build_content_map(
        security_context=SECURITY,
        messages=messages,
        notes=[{"id": "n1", "title": "贵州茅台机构交流纪要", "lead": "全年渠道策略"}],
    )

    assert result["stats"]["content_count"] == 4
    assert result["stats"]["avg_tags_per_content"] > 4
    assert result["stats"]["ontology_coverage"] >= 75
    assert set(result["stats"]["content_type_counts"]) == {
        "flash", "article", "research", "institution_note",
    }
    assert any(edge["type"] == "ABOUT" for edge in result["graph"]["edges"])
    assert any(node["kind"] == "tag" for node in result["graph"]["nodes"])
