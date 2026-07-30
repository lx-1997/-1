from __future__ import annotations

from types import SimpleNamespace

from deepfocus_api.market_risk_radar import (
    SiteContent,
    _company_key,
    _dedupe_market_rows,
    _information_risk,
    _matches_company,
    _risk_level,
    _score_company,
)
from deepfocus_api.auth import is_public_path


def _raw(symbol: str, name: str, cap: float, change: float = 0.0) -> dict:
    return {
        "f12": symbol,
        "f14": name,
        "f20": cap,
        "f2": 100,
        "f3": change,
        "f7": 2,
        "f8": 1,
        "f9": 20,
        "f10": 1,
        "f23": 3,
        "f24": 5,
        "f25": 8,
        "f100": "科技",
        "f184": 1,
    }


def test_company_key_deduplicates_dual_share_classes() -> None:
    assert _company_key("US", "GOOG", "谷歌-C") == _company_key("US", "GOOGL", "谷歌-A")
    assert _company_key("US", "BRK.A", "伯克希尔-A") == _company_key("US", "BRK.B", "伯克希尔-B")


def test_market_rows_are_sorted_and_company_deduplicated() -> None:
    rows = _dedupe_market_rows(
        [
            _raw("GOOG", "谷歌-C", 400),
            _raw("GOOGL", "谷歌-A", 410),
            _raw("MSFT", "微软", 300),
            _raw("AAPL", "苹果", 500),
        ],
        "US",
        3,
    )
    assert [row["symbol"] for row in rows] == ["AAPL", "GOOGL", "MSFT"]


def test_selloff_and_outflow_raise_deterministic_risk_score() -> None:
    calm = {
        **_dedupe_market_rows([_raw("AAPL", "苹果", 500, 1.0)], "US", 1)[0],
        "change_60d_pct": 12,
        "main_net_inflow_pct": 2,
    }
    stressed = {
        **calm,
        "change_pct": -7,
        "change_60d_pct": -25,
        "amplitude_pct": 10,
        "volume_ratio": 2.4,
        "main_net_inflow_pct": -6,
    }
    calm_score = _score_company(
        calm,
        macro_risk=35,
        macro_label="指标分化",
        industry_change=0.5,
        messages=[],
        ranking_status="live",
    )
    stressed_score = _score_company(
        stressed,
        macro_risk=65,
        macro_label="指标偏谨慎",
        industry_change=-3,
        messages=[],
        ranking_status="live",
    )
    assert stressed_score["risk_score"] > calm_score["risk_score"] + 25
    assert _risk_level(stressed_score["risk_score"]) in {"orange", "red"}


def test_options_dimension_only_changes_weight_when_chain_is_valid() -> None:
    company = {
        **_dedupe_market_rows([_raw("NVDA", "英伟达", 500, -1.0)], "US", 1)[0],
        "change_60d_pct": -3,
        "main_net_inflow_pct": -1,
    }
    unavailable = _score_company(
        company,
        macro_risk=40,
        macro_label="指标分化",
        industry_change=-0.5,
        messages=[],
        ranking_status="live",
        options_signal=SimpleNamespace(
            source_status="unavailable",
            contract_count=0,
            data_quality=0,
        ),
    )
    high_tail_risk = _score_company(
        company,
        macro_risk=40,
        macro_label="指标分化",
        industry_change=-0.5,
        messages=[],
        ranking_status="live",
        options_signal=SimpleNamespace(
            source_status="delayed",
            contract_count=1200,
            expiration_count=2,
            data_quality=82,
            tail_event_risk_score=88,
            tail_event_risk_level="红灯",
            tail_event_risk_summary="左尾事件风险红灯。",
            tail_event_risk_reasons=["近月 put 异常放量。"],
            provider_name="授权期权链",
            direction="偏空",
            conviction="高",
            fetched_at="2026-07-30T10:00:00+00:00",
        ),
    )
    assert "options" not in unavailable["dimensions"]
    assert unavailable["options_signal"]["status"] == "unavailable"
    assert high_tail_risk["dimensions"]["options"] == 88
    assert high_tail_risk["risk_score"] > unavailable["risk_score"]
    assert any("期权左尾风险红灯" in driver for driver in high_tail_risk["drivers"])


def test_site_content_matches_symbol_suffix_and_keeps_neutral_linkage() -> None:
    company = {
        **_dedupe_market_rows([_raw("300750", "宁德时代", 500)], "CN", 1)[0],
    }
    item = SiteContent(
        id="corpus:1",
        title="宁德时代深度研究",
        content="公司发布新产品，业务保持增长。",
        symbol="300750.SZ",
        source_name="daocaijing资料库",
        content_type="抓取/上传资料",
        published_at="2026-07-30T10:00:00+00:00",
    )
    assert _matches_company(item, company)
    _score, evidence, risk_count = _information_risk([item])
    assert len(evidence) == 1
    assert evidence[0]["content_type"] == "抓取/上传资料"
    assert evidence[0]["severity"] == "info"
    assert risk_count == 0


def test_market_risk_radar_is_exact_public_endpoint() -> None:
    assert is_public_path("/api/market-risk-radar")
    assert not is_public_path("/api/market-risk-radar/private")
