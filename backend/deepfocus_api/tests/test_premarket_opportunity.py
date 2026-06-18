from datetime import datetime, timezone

from deepfocus_api.premarket_opportunity import THEMES, _score_theme
from deepfocus_api.schemas import MarketQuote


def _quote(symbol: str, change: float) -> MarketQuote:
    return MarketQuote(
        symbol=symbol,
        price=100,
        change=change,
        change_percent=change,
        provider="test",
        provider_name="Test",
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )


def test_score_theme_promotes_collective_us_impulse_to_observation():
    theme = next(item for item in THEMES if item.key == "ai_compute")
    quote_by_symbol = {
        "NVDA": _quote("NVDA", 3.2),
        "AMD": _quote("AMD", 2.5),
        "AVGO": _quote("AVGO", 1.8),
        "MU": _quote("MU", 1.4),
        "SMCI": _quote("SMCI", 2.1),
        "QQQ": _quote("QQQ", 0.8),
        "SMH": _quote("SMH", 2.0),
        "SOXX": _quote("SOXX", 1.7),
        "ASHR": _quote("ASHR", 0.5),
    }

    scored = _score_theme(theme, quote_by_symbol, {})

    assert scored.stance in {"重点机会", "观察机会"}
    assert scored.direction == "上行"
    assert scored.opportunity_score >= 60
    assert any("美股主题龙头平均涨跌幅" in item for item in scored.evidence)


def test_score_theme_flags_collective_drop_as_risk():
    theme = next(item for item in THEMES if item.key == "ev_robotics")
    quote_by_symbol = {
        "TSLA": _quote("TSLA", -4.0),
        "RIVN": _quote("RIVN", -5.0),
        "LI": _quote("LI", -3.0),
        "NIO": _quote("NIO", -4.5),
        "XPEV": _quote("XPEV", -3.5),
        "QQQ": _quote("QQQ", -1.2),
        "KWEB": _quote("KWEB", -2.0),
        "ASHR": _quote("ASHR", -1.0),
    }

    scored = _score_theme(theme, quote_by_symbol, {})

    assert scored.stance == "风险回避"
    assert scored.direction == "下行"
    assert scored.risk_score >= 72
