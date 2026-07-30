from __future__ import annotations

from deepfocus_api.market_risk_radar import (
    _company_key,
    _dedupe_market_rows,
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


def test_market_risk_radar_is_exact_public_endpoint() -> None:
    assert is_public_path("/api/market-risk-radar")
    assert not is_public_path("/api/market-risk-radar/private")
