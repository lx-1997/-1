from __future__ import annotations

from decimal import Decimal

import pytest

from finogrid.services.web3_products.gamefi import StrategyRun, rank_runs, score_run
from finogrid.services.web3_products.rwa_evidence import EvidenceEvent, event_hash, verify_chain


def _run(strategy_id: str, ret: str, drawdown: str, win_rate: str) -> StrategyRun:
    return StrategyRun(
        strategy_id=strategy_id,
        return_pct=Decimal(ret),
        max_drawdown_pct=Decimal(drawdown),
        win_rate_pct=Decimal(win_rate),
        completed_at="2026-10-07T00:00:00Z",
    )


def test_gamefi_score_penalizes_drawdown():
    low_risk = _run("low-risk", "10", "2", "50")
    high_risk = _run("high-risk", "10", "20", "50")
    assert score_run(low_risk) > score_run(high_risk)


def test_gamefi_ranking_is_deterministic_and_hashed():
    ranked = rank_runs([_run("b", "5", "2", "50"), _run("a", "5", "2", "50")])
    assert [row.strategy_id for row in ranked] == ["a", "b"]
    assert ranked[0].rank == 1
    assert len(ranked[0].snapshot_hash) == 64


def _event(previous: str | None = None) -> EvidenceEvent:
    return EvidenceEvent(
        asset_id="asset-001",
        evidence_type="custody_report",
        source_uri="ipfs://bafy-test",
        content_hash="a" * 64,
        observed_at="2026-10-07T00:00:00Z",
        signer="custodian.example",
        payload={"balance": "1000000", "unit": "USD"},
        previous_event_hash=previous,
    )


def test_rwa_evidence_chain_verifies():
    first = _event()
    first_hash = event_hash(first)
    second = _event(first_hash)
    second_hash = event_hash(second)
    assert verify_chain([(first, first_hash), (second, second_hash)])


def test_rwa_evidence_rejects_bad_uri_and_broken_chain():
    with pytest.raises(ValueError, match="source_uri"):
        event_hash(EvidenceEvent("a", "report", "http://bad", "a" * 64, "now", "signer", {}))
    first = _event()
    assert not verify_chain([(first, "b" * 64)])


def test_ops_console_production_config_rejects_defaults():
    from finogrid.services.ops_console.config import OpsConsoleSettings

    cfg = OpsConsoleSettings(
        app_env="production",
        app_debug=False,
        allowed_origins_value="https://daocaijing.com",
        database_url="postgresql+asyncpg://ops:secret@db.internal:5432/finogrid",
        ops_api_key="ops_dev_key",
    )
    errors = cfg.production_validation_errors()
    assert any("OPS_API_KEY" in error for error in errors)

    cfg = OpsConsoleSettings(
        app_env="production",
        app_debug=False,
        allowed_origins_value="https://daocaijing.com",
        database_url="postgresql+asyncpg://finogrid:password@localhost:5432/finogrid",
        ops_api_key="a-real-ops-secret",
    )
    assert cfg.allowed_origins == ["https://daocaijing.com"]
    errors = cfg.production_validation_errors()
    assert any("DATABASE_URL" in error for error in errors)
