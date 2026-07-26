from __future__ import annotations

import pytest

from deepfocus_api.investment_ontology import (
    OntologyDemoActionRequest,
    create_demo_action,
    get_demo_snapshot,
    init_ontology_db,
    resolve_alias,
)
from deepfocus_api.auth import PUBLIC_EXACT


def test_aliases_resolve_to_one_canonical_security(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPFOCUS_ONTOLOGY_DB_PATH", str(tmp_path / "ontology.sqlite3"))
    init_ontology_db()

    by_code = resolve_alias("600519")
    by_vendor = resolve_alias("SH600519")
    by_name = resolve_alias("贵州茅台")

    assert by_code is not None
    assert by_vendor is not None
    assert by_name is not None
    assert by_code["id"] == by_vendor["id"] == by_name["id"] == "security:cn:600519.SH"
    assert by_code["canonical_key"] == "600519.SH"
    assert by_code["market"] == "CN"


def test_demo_snapshot_connects_evidence_to_position(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPFOCUS_ONTOLOGY_DB_PATH", str(tmp_path / "ontology.sqlite3"))

    snapshot = get_demo_snapshot("security:cn:600519.SH")

    node_types = {node["type"] for node in snapshot["graph"]["nodes"]}
    relation_types = {edge["type"] for edge in snapshot["graph"]["edges"]}
    assert {"Evidence", "Event", "Thesis", "Security", "Position", "Portfolio"} <= node_types
    assert {"EVIDENCES", "CONTRADICTS", "GOVERNS", "HOLDS"} <= relation_types
    assert snapshot["decision"]["position"]["attributes"]["weight_pct"] == 12.5
    assert snapshot["decision"]["recommended_action_type"] == "reduce_paper"


def test_single_evidence_asset_keeps_semantic_graph_columns(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPFOCUS_ONTOLOGY_DB_PATH", str(tmp_path / "ontology.sqlite3"))

    snapshot = get_demo_snapshot("security:cn:300750.SZ")
    positions = {node["type"]: node["position"]["x"] for node in snapshot["graph"]["nodes"]}

    assert positions["Evidence"] == 8
    assert positions["Event"] == 31
    assert positions["Thesis"] == 54
    assert positions["Security"] == 73
    assert positions["Position"] == 73
    assert positions["Portfolio"] == 91


def test_unknown_canonical_security_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPFOCUS_ONTOLOGY_DB_PATH", str(tmp_path / "ontology.sqlite3"))

    with pytest.raises(ValueError, match="Canonical Security"):
        get_demo_snapshot("security:cn:unknown")


def test_demo_action_is_audited_without_real_trade(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPFOCUS_ONTOLOGY_DB_PATH", str(tmp_path / "ontology.sqlite3"))

    created = create_demo_action(
        OntologyDemoActionRequest(
            security_id="security:cn:300750.SZ",
            action_type="request_research",
            reason="验证海外份额",
        ),
        actor="tester",
    )
    snapshot = get_demo_snapshot("security:cn:300750.SZ")

    assert created.status == "paper-recorded"
    assert created.actor == "tester"
    assert snapshot["actions"][0]["id"] == created.id
    assert snapshot["actions"][0]["reason"] == "验证海外份额"


def test_demo_reads_are_public_but_action_write_requires_login():
    assert "/api/ontology/demo" in PUBLIC_EXACT
    assert "/api/ontology/resolve" in PUBLIC_EXACT
    assert "/api/ontology/demo/actions" not in PUBLIC_EXACT
