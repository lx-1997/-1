import asyncio
from datetime import date, timedelta

from deepfocus_api import options_signal as options_signal_module
from deepfocus_api.options_signal import (
    OptionContract,
    PriceAction,
    _analyze_symbol,
    _apply_tail_event_risk,
    _build_signal,
    _contracts_from_nasdaq,
    _contracts_from_tradier_chain,
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


def test_build_signal_calculates_gamma_exposure_when_greeks_available():
    expiration = (date.today() + timedelta(days=30)).isoformat()
    contracts = [
        OptionContract("MSFT", "MSFT-C-420", "call", 420, expiration, mid=8.0, volume=100, open_interest=1000, gamma=0.012, underlying_price=410),
        OptionContract("MSFT", "MSFT-P-400", "put", 400, expiration, mid=7.0, volume=120, open_interest=800, gamma=0.010, underlying_price=410),
        OptionContract("MSFT", "MSFT-C-430", "call", 430, expiration, mid=4.0, volume=80, open_interest=500, gamma=0.008, underlying_price=410),
    ]

    signal = _build_signal(
        symbol="MSFT",
        contracts=contracts,
        provider="marketdata_app",
        provider_name="MarketData.app",
        delay_note="delayed",
        horizon_days=45,
    )

    assert signal.gamma_exposure_status == "available"
    assert signal.call_gamma_exposure > 0
    assert signal.put_gamma_exposure < 0
    assert signal.net_gamma_exposure > 0
    assert signal.gamma_wall == 420
    assert len(signal.gamma_strikes) == 3
    assert any("净 Gamma Exposure" in line for line in signal.signals)


def test_build_signal_estimates_gamma_exposure_from_free_chain_prices():
    expiration = (date.today() + timedelta(days=30)).isoformat()
    contracts = [
        OptionContract("MSFT", "MSFT-C-410", "call", 410, expiration, mid=12.0, volume=100, open_interest=1200, underlying_price=410),
        OptionContract("MSFT", "MSFT-P-400", "put", 400, expiration, mid=7.0, volume=140, open_interest=900, underlying_price=410),
        OptionContract("MSFT", "MSFT-C-430", "call", 430, expiration, mid=4.0, volume=90, open_interest=700, underlying_price=410),
    ]

    signal = _build_signal(
        symbol="MSFT",
        contracts=contracts,
        provider="nasdaq_public",
        provider_name="Nasdaq Public Option Chain",
        delay_note="delayed",
        horizon_days=45,
    )

    assert signal.gamma_exposure_status == "estimated"
    assert signal.call_gamma_exposure > 0
    assert signal.put_gamma_exposure < 0
    assert len(signal.gamma_strikes) == 3
    assert any("估算净 Gamma Exposure" in line for line in signal.signals)
    assert any("估算值" in flag for flag in signal.risk_flags)


def test_tradier_parser_maps_greeks_and_iv():
    expiration = (date.today() + timedelta(days=21)).isoformat()
    payload = {
        "options": {
            "option": [
                {
                    "symbol": "MSFT260619C00420000",
                    "option_type": "call",
                    "expiration_date": expiration,
                    "strike": 420,
                    "bid": 7.9,
                    "ask": 8.1,
                    "last": 8.0,
                    "volume": 120,
                    "open_interest": 900,
                    "greeks": {
                        "mid_iv": 0.32,
                        "delta": 0.52,
                        "gamma": 0.012,
                        "theta": -0.04,
                        "vega": 0.20,
                        "updated_at": "2026-05-22 15:59:00",
                    },
                }
            ]
        }
    }

    [contract] = _contracts_from_tradier_chain("MSFT", expiration, payload, underlying_price=410)

    assert contract.option_symbol == "MSFT260619C00420000"
    assert contract.side == "call"
    assert contract.iv == 0.32
    assert contract.gamma == 0.012
    assert contract.theta == -0.04
    assert contract.vega == 0.20
    assert contract.underlying_price == 410


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


def test_tail_event_risk_red_when_put_flow_peer_sync_and_price_leak():
    expiration = (date.today() + timedelta(days=7)).isoformat()
    futu = _build_signal(
        symbol="FUTU",
        contracts=[
            OptionContract("FUTU", "FUTU-C-92", "call", 92, expiration, mid=3.0, volume=500, open_interest=800, underlying_price=90),
            OptionContract("FUTU", "FUTU-P-82", "put", 82, expiration, mid=2.0, volume=3200, open_interest=120, underlying_price=90),
            OptionContract("FUTU", "FUTU-P-77", "put", 77, expiration, mid=1.3, volume=6100, open_interest=130, underlying_price=90),
        ],
        provider="nasdaq_public",
        provider_name="Nasdaq Public Option Chain",
        delay_note="delayed",
        horizon_days=45,
    )
    tigr = _build_signal(
        symbol="TIGR",
        contracts=[
            OptionContract("TIGR", "TIGR-C-5", "call", 5, expiration, mid=0.1, volume=300, open_interest=900, underlying_price=4.4),
            OptionContract("TIGR", "TIGR-P-4", "put", 4, expiration, mid=0.2, volume=2100, open_interest=80, underlying_price=4.4),
        ],
        provider="nasdaq_public",
        provider_name="Nasdaq Public Option Chain",
        delay_note="delayed",
        horizon_days=45,
    )

    signals = _apply_tail_event_risk(
        [futu, tigr],
        {
            "FUTU": PriceAction(
                symbol="FUTU",
                latest_date=date.today().isoformat(),
                latest_change_pct=-0.7,
                latest_volume_vs_5d=0.8,
                recent_large_drop_count=2,
                max_recent_drop_pct=-13.8,
            ),
            "TIGR": PriceAction(
                symbol="TIGR",
                latest_date=date.today().isoformat(),
                latest_change_pct=-0.9,
                latest_volume_vs_5d=1.4,
                recent_large_drop_count=1,
                max_recent_drop_pct=-7.2,
            ),
        },
    )

    futu_signal = next(signal for signal in signals if signal.symbol == "FUTU")
    assert futu_signal.tail_event_risk_level == "红灯"
    assert futu_signal.tail_event_risk_score >= 75
    assert any("同池联动" in reason for reason in futu_signal.tail_event_risk_reasons)
    assert any("隔夜左尾事件风险高" in action for action in futu_signal.tail_event_risk_actions)
    assert futu_signal.forecast_label == "高风险回避"
    assert futu_signal.forecast_score <= 35


def test_tail_event_risk_uses_post_event_wording_after_extreme_drop():
    expiration = (date.today() + timedelta(days=7)).isoformat()
    signal = _build_signal(
        symbol="FUTU",
        contracts=[
            OptionContract("FUTU", "FUTU-C-92", "call", 92, expiration, mid=3.0, volume=300, open_interest=700, underlying_price=90),
            OptionContract("FUTU", "FUTU-P-82", "put", 82, expiration, mid=2.4, volume=4000, open_interest=100, underlying_price=90),
            OptionContract("FUTU", "FUTU-P-77", "put", 77, expiration, mid=1.6, volume=6500, open_interest=140, underlying_price=90),
            OptionContract("FUTU", "FUTU-P-70", "put", 70, expiration, mid=1.0, volume=2500, open_interest=110, underlying_price=90),
        ],
        provider="nasdaq_public",
        provider_name="Nasdaq Public Option Chain",
        delay_note="delayed",
        horizon_days=45,
    )

    [updated] = _apply_tail_event_risk(
        [signal],
        {
            "FUTU": PriceAction(
                symbol="FUTU",
                latest_date=date.today().isoformat(),
                latest_change_pct=-27.5,
                latest_volume_vs_5d=25.0,
                recent_large_drop_count=1,
                max_recent_drop_pct=-27.5,
            )
        },
    )

    assert updated.tail_event_risk_level == "红灯"
    assert "事件后风险" in updated.tail_event_risk_summary
    assert not any("提前押注" in line for line in [updated.tail_event_risk_summary, *updated.signals])
    assert any("不把红灯理解成新的事前预测" in action for action in updated.tail_event_risk_actions)
    assert updated.forecast_label == "高风险回避"
    assert "二次冲击" in updated.forecast_summary


def test_tail_event_risk_yellow_for_single_price_drop_without_put_confirmation():
    expiration = (date.today() + timedelta(days=30)).isoformat()
    signal = _build_signal(
        symbol="AAPL",
        contracts=[
            OptionContract("AAPL", "AAPL-C-100", "call", 100, expiration, mid=5.0, volume=300, open_interest=500, underlying_price=100),
            OptionContract("AAPL", "AAPL-P-95", "put", 95, expiration, mid=2.0, volume=120, open_interest=600, underlying_price=100),
        ],
        provider="nasdaq_public",
        provider_name="Nasdaq Public Option Chain",
        delay_note="delayed",
        horizon_days=45,
    )

    [updated] = _apply_tail_event_risk(
        [signal],
        {
            "AAPL": PriceAction(
                symbol="AAPL",
                latest_date=date.today().isoformat(),
                latest_change_pct=-5.4,
                latest_volume_vs_5d=1.0,
                recent_large_drop_count=0,
                max_recent_drop_pct=-5.4,
            )
        },
    )

    assert updated.tail_event_risk_level == "黄灯"
    assert 30 <= updated.tail_event_risk_score < 55
    assert any("最新交易日股价下跌" in reason for reason in updated.tail_event_risk_reasons)


def test_predictive_forecast_bullish_when_calls_and_price_confirm():
    expiration = (date.today() + timedelta(days=21)).isoformat()
    signal = _build_signal(
        symbol="NVDA",
        contracts=[
            OptionContract("NVDA", "NVDA-C-120", "call", 120, expiration, mid=8.0, volume=5000, open_interest=900, gamma=0.010, underlying_price=118),
            OptionContract("NVDA", "NVDA-C-125", "call", 125, expiration, mid=4.0, volume=3800, open_interest=1000, gamma=0.008, underlying_price=118),
            OptionContract("NVDA", "NVDA-P-110", "put", 110, expiration, mid=2.0, volume=400, open_interest=2200, gamma=0.007, underlying_price=118),
        ],
        provider="marketdata_app",
        provider_name="MarketData.app",
        delay_note="delayed",
        horizon_days=45,
    )

    [updated] = _apply_tail_event_risk(
        [signal],
        {
            "NVDA": PriceAction(
                symbol="NVDA",
                latest_date=date.today().isoformat(),
                latest_change_pct=1.2,
                five_day_change_pct=5.5,
                twenty_day_change_pct=12.0,
                close_vs_20d_avg_pct=4.0,
                latest_volume_vs_5d=1.1,
                recent_large_drop_count=0,
            )
        },
    )

    assert updated.forecast_label in {"看涨", "强看涨", "震荡偏强"}
    assert updated.forecast_score > 55
    assert any("价格确认偏强" in reason for reason in updated.forecast_reasons)


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
