import asyncio
from datetime import date, timedelta

from deepfocus_api import options_signal as options_signal_module
from deepfocus_api.options_signal import (
    OptionContract,
    _analyze_symbol,
    _build_signal,
    _contracts_from_nasdaq,
    _source_profile,
)


def test_build_signal_scores_bullish_call_demand():
    expiration = (date.today() + timedelta(days=30)).isoformat()
    contracts = [
        OptionContract("AAPL", "AAPL-C-100", "call", 100, expiration, mid=7.0, volume=700, open_interest=900, iv=0.32, underlying_price=100),
        OptionContract("AAPL", "AAPL-P-100", "put", 100, expiration, mid=4.0, volume=140, open_interest=420, iv=0.34, underlying_price=100),
        OptionContract("AAPL", "AAPL-C-110", "call", 110, expiration, mid=2.4, volume=900, open_interest=2400, iv=0.35, underlying_price=100),
        OptionContract("AAPL", "AAPL-P-90", "put", 90, expiration, mid=1.7, volume=80, open_interest=1800, iv=0.38, underlying_price=100),
    ]

    signal = _build_signal(
        symbol="AAPL",
        contracts=contracts,
        provider="marketdata_app",
        provider_name="MarketData.app",
        delay_note="delayed",
        horizon_days=45,
    )

    assert signal.direction == "偏多"
    assert signal.score > 60
    assert signal.put_call_volume_ratio is not None
    assert signal.put_call_volume_ratio < 0.2
    assert signal.call_wall == 110
    assert signal.put_wall == 90
    assert signal.expected_move_pct == 0.11


def test_build_signal_detects_unusual_large_option_flow():
    expiration = (date.today() + timedelta(days=7)).isoformat()
    contracts = [
        OptionContract("NVDA", "NVDA-C-125", "call", 125, expiration, mid=2.5, volume=6200, open_interest=800, iv=0.42, underlying_price=120),
        OptionContract("NVDA", "NVDA-P-115", "put", 115, expiration, mid=1.7, volume=320, open_interest=2400, iv=0.44, underlying_price=120),
        OptionContract("NVDA", "NVDA-C-130", "call", 130, expiration, mid=1.1, volume=280, open_interest=1800, iv=0.41, underlying_price=120),
    ]

    signal = _build_signal(
        symbol="NVDA",
        contracts=contracts,
        provider="marketdata_app",
        provider_name="MarketData.app",
        delay_note="delayed",
        horizon_days=45,
    )

    assert signal.unusual_flow_count == 1
    assert signal.unusual_premium_notional > 1_000_000
    assert signal.unusual_flows[0].side == "call"
    assert signal.unusual_flows[0].severity == "高"
    assert "异常大单" in signal.signals[1]


def test_unusual_flow_count_keeps_total_while_returning_top_twelve():
    expiration = (date.today() + timedelta(days=5)).isoformat()
    contracts = [
        OptionContract(
            "TSLA",
            f"TSLA-C-{400 + index}",
            "call",
            400 + index,
            expiration,
            mid=2.0 + index / 10,
            volume=2_000 + index * 250,
            open_interest=500,
            underlying_price=410,
        )
        for index in range(15)
    ]

    signal = _build_signal(
        symbol="TSLA",
        contracts=contracts,
        provider="nasdaq_public",
        provider_name="Nasdaq Public Option Chain",
        delay_note="delayed",
        horizon_days=45,
    )

    assert signal.unusual_flow_count == 15
    assert len(signal.unusual_flows) == 12
    assert "命中 15 条异常大单候选" in signal.signals[1]


def test_nasdaq_parser_keeps_selected_expiration_rows():
    exp_date = date.today() + timedelta(days=20)
    expiration = f"{exp_date:%B} {exp_date.day}, {exp_date:%Y}"
    payload = {
        "data": {
            "lastTrade": "LAST TRADE: $123.45 (AS OF MAY 18, 2026 9:35 AM ET)",
            "table": {
                "rows": [
                    {"expirygroup": expiration},
                    {
                        "expirygroup": "",
                        "strike": "120.00",
                        "c_Last": "7.50",
                        "c_Bid": "7.30",
                        "c_Ask": "7.70",
                        "c_Volume": "1,200",
                        "c_Openinterest": "3,400",
                        "p_Last": "4.10",
                        "p_Bid": "3.90",
                        "p_Ask": "4.30",
                        "p_Volume": "900",
                        "p_Openinterest": "2,800",
                    },
                ]
            },
        }
    }

    contracts = _contracts_from_nasdaq("MSFT", payload, horizon_days=45, max_expirations=1)

    assert len(contracts) == 2
    assert {contract.side for contract in contracts} == {"call", "put"}
    assert contracts[0].underlying_price == 123.45
    assert sum(contract.open_interest or 0 for contract in contracts) == 6200


def test_source_profile_marks_marketdata_as_blocked_without_token(monkeypatch):
    monkeypatch.delenv("MARKETDATA_APP_TOKEN", raising=False)
    monkeypatch.delenv("MARKETDATA_APP_API_KEY", raising=False)

    marketdata = next(source for source in _source_profile() if source.provider == "marketdata_app")

    assert marketdata.status == "blocked"
    assert "自动跳过" in marketdata.notes


def test_analyze_symbol_skips_marketdata_without_token(monkeypatch):
    monkeypatch.delenv("MARKETDATA_APP_TOKEN", raising=False)
    monkeypatch.delenv("MARKETDATA_APP_API_KEY", raising=False)
    expiration = (date.today() + timedelta(days=30)).isoformat()

    async def marketdata_should_not_run(*_args):
        raise AssertionError("MarketData.app should be skipped when no token is configured")

    async def fake_nasdaq(_client, symbol, _horizon_days, _max_expirations):
        return [
            OptionContract(symbol, f"{symbol}-C-100", "call", 100, expiration, mid=6.0, volume=100, open_interest=300, underlying_price=100),
            OptionContract(symbol, f"{symbol}-P-100", "put", 100, expiration, mid=4.0, volume=80, open_interest=260, underlying_price=100),
        ], "Nasdaq Public Option Chain", "Nasdaq delay", []

    async def yahoo_should_not_run(*_args):
        raise AssertionError("Yahoo should not run after Nasdaq succeeds")

    fake_nasdaq.__name__ = "_fetch_nasdaq_public_contracts"
    monkeypatch.setattr(options_signal_module, "_fetch_marketdata_app_contracts", marketdata_should_not_run)
    monkeypatch.setattr(options_signal_module, "_fetch_nasdaq_public_contracts", fake_nasdaq)
    monkeypatch.setattr(options_signal_module, "_fetch_yahoo_public_contracts", yahoo_should_not_run)

    signal, warnings = asyncio.run(_analyze_symbol(object(), "AAPL", 45, 1))

    assert signal.provider == "nasdaq_public"
    assert signal.provider_name == "Nasdaq Public Option Chain"
    assert warnings == []
