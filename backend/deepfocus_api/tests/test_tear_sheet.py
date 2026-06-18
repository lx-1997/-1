from datetime import datetime, timezone

from deepfocus_api.schemas import EarningsCalendarEvent, MarketQuote, OptionsSignal
from deepfocus_api.tear_sheet import (
    build_briefing,
    build_macro_review,
    build_portfolio_review,
    build_tear_sheet,
    build_watchlist_summary,
)


def _quote(provider="eastmoney", change_percent=3.0, is_realtime=True):
    return MarketQuote(
        symbol="TSLA", price=250.0, change_percent=change_percent,
        previous_close=242.0, open_price=246.0, high=255.0, low=245.0, volume=1_000_000,
        currency="USD", provider=provider, provider_name=provider,
        fetched_at="2026-06-04T00:00:00Z", is_realtime=is_realtime,
    )


def _earnings(provider="nasdaq_public", days=7):
    return EarningsCalendarEvent(
        symbol="TSLA", name="Tesla", currency="USD", provider=provider, source_name="Nasdaq",
        report_date="2026-06-11", days_until_report=days, eps_estimate=2.5,
        status="scheduled", confidence="confirmed",
    )


def _options(provider="marketdata", direction="偏多", conviction="高"):
    return OptionsSignal(
        symbol="TSLA", provider=provider, provider_name=provider, source_status="delayed",
        fetched_at=datetime.now(timezone.utc), data_quality=80, direction=direction, score=70,
        conviction=conviction, summary="期权偏多", put_call_volume_ratio=0.7, avg_iv=0.55,
    )


def test_bullish_full():
    ts = build_tear_sheet(
        symbol="TSLA", name="Tesla", market_cap=8e11, currency="USD",
        quote=_quote(change_percent=3.0), earnings_events=[_earnings()],
        options_signal=_options("marketdata", "偏多", "高"),
    )
    dims = {d.key: d for d in ts.dimensions}
    assert dims["momentum"].signal == "bullish"
    assert dims["options"].signal == "bullish"
    assert dims["catalyst"].signal == "neutral"  # 催化是关注项，不直接定多空
    assert dims["scale"].signal == "neutral"
    assert ts.overall_verdict == "重点跟踪"
    assert ts.overall_score > 0
    assert ts.confidence > 0
    assert ts.data_quality.level == "live"  # 全部真实云端/公开源
    # 临近财报应标⚡
    assert "⚡" in dims["catalyst"].headline


def test_bearish():
    ts = build_tear_sheet(
        symbol="X", name="X", market_cap=5e10,
        quote=_quote(change_percent=-3.5), earnings_events=[], options_signal=_options(direction="偏空"),
    )
    dims = {d.key: d for d in ts.dimensions}
    assert dims["momentum"].signal == "bearish"
    assert dims["options"].signal == "bearish"
    assert ts.overall_verdict == "谨慎回避"
    assert ts.overall_score < 0


def test_insufficient_when_all_missing():
    ts = build_tear_sheet(symbol="X", name="X")
    assert all(d.signal == "insufficient" for d in ts.dimensions)
    assert ts.overall_verdict == "数据不足"
    assert ts.confidence == 0.0
    assert len(ts.dimensions) == 9


def test_mock_quality_propagates_to_worst():
    ts = build_tear_sheet(symbol="X", name="X", market_cap=1e11, quote=_quote(provider="mock"))
    dims = {d.key: d for d in ts.dimensions}
    assert dims["momentum"].data_quality.level == "mock"
    assert ts.data_quality.level == "mock"  # 整体取最差档，诚实


def test_partial_only_degraded_quote():
    ts = build_tear_sheet(
        symbol="X", name="X", market_cap=2e11,
        quote=_quote(provider="local-rule", change_percent=0.5, is_realtime=False),
    )
    dims = {d.key: d for d in ts.dimensions}
    assert dims["momentum"].signal == "neutral"  # |0.5%| < 2 → 震荡
    assert dims["catalyst"].signal == "insufficient"
    assert dims["options"].signal == "insufficient"
    assert dims["scale"].signal == "neutral"
    assert dims["momentum"].data_quality.level == "degraded"  # local-rule → 降级
    assert ts.overall_verdict == "中性观察"


def test_unjudgeable_options_is_insufficient():
    ts = build_tear_sheet(
        symbol="X", name="X", market_cap=1e11, quote=_quote(),
        options_signal=_options(direction="不可判定"),
    )
    dims = {d.key: d for d in ts.dimensions}
    assert dims["options"].signal == "insufficient"


# ---- 组合层证据速判 ----

def test_portfolio_review_with_positions():
    summary = {
        "portfolio": {"total_value": 1_000_000, "total_pnl_pct": 6.5,
                      "sector_exposure": {"科技": 55, "金融": 25, "消费": 20}},
        "open_positions": [
            {"symbol": "TSLA", "position_size_pct": 35, "stop_loss": 200, "sector": "科技"},
            {"symbol": "AAPL", "position_size_pct": 25, "stop_loss": None, "sector": "科技"},
            {"symbol": "JPM", "position_size_pct": 20, "stop_loss": 140, "sector": "金融"},
        ],
        "alerts": [{"level": "warning", "message": "科技行业敞口55%超过上限40%"}],
    }
    r = build_portfolio_review(summary)
    dims = {d.key: d for d in r.dimensions}
    assert r.position_count == 3
    assert dims["concentration"].signal == "bearish"  # 最大 35% 偏集中
    assert dims["sector"].signal == "bearish"  # 科技 55% 过度集中
    assert dims["pnl"].signal == "bullish"  # +6.5%
    assert dims["stop_discipline"].signal == "neutral"  # 2/3 设止损
    assert r.overall_verdict in ("需关注", "高风险")
    assert r.data_quality.level == "degraded"  # 本地录入持仓，非交易所实时结算
    assert any("科技" in a for a in r.alerts)


def test_portfolio_review_empty():
    r = build_portfolio_review({"portfolio": {}, "open_positions": [], "alerts": []})
    assert r.overall_verdict == "空仓"
    assert r.position_count == 0
    assert all(d.signal == "insufficient" for d in r.dimensions)
    assert r.confidence == 0.0


def test_portfolio_review_healthy():
    summary = {
        "portfolio": {"total_value": 2_000_000, "total_pnl_pct": 3.0,
                      "sector_exposure": {"科技": 20, "金融": 18, "消费": 15, "医药": 12}},
        "open_positions": [
            {"symbol": "A", "position_size_pct": 12, "stop_loss": 10},
            {"symbol": "B", "position_size_pct": 10, "stop_loss": 9},
            {"symbol": "C", "position_size_pct": 8, "stop_loss": 7},
        ],
        "alerts": [],
    }
    r = build_portfolio_review(summary)
    dims = {d.key: d for d in r.dimensions}
    assert dims["concentration"].signal == "bullish"
    assert dims["sector"].signal == "bullish"
    assert dims["stop_discipline"].signal == "bullish"
    assert r.overall_verdict == "稳健"


# ---- 真实数据源：市场环境维度（标普500）----

def test_market_context_bullish():
    history = [
        ("2026-01-01", 6000.0), ("2026-02-01", 6100.0), ("2026-03-01", 6300.0),
        ("2026-04-01", 6600.0), ("2026-05-01", 6900.0),
    ]
    ts = build_tear_sheet(symbol="X", name="X", market_index_history=history)
    market = {d.key: d for d in ts.dimensions}["market"]
    assert market.signal == "bullish"  # 近3月 6300→6900 ≈ +9.5%
    assert market.data_quality.level == "live"  # github 真实数据
    assert any("标普500" in e for e in market.evidence)


def test_market_context_insufficient_without_data():
    ts = build_tear_sheet(symbol="X", name="X")
    market = {d.key: d for d in ts.dimensions}["market"]
    assert market.signal == "insufficient"


def test_constituent_sector_in_narrative():
    ts = build_tear_sheet(
        symbol="TSLA", name="Tesla", quote=_quote(),
        constituent={"name": "Tesla, Inc.", "sector": "Consumer Discretionary"},
    )
    assert "标普500 成分" in ts.narrative
    assert "Consumer Discretionary" in ts.narrative


def test_macro_rates_headwind():
    rates = [("2026-01", 4.0), ("2026-02", 4.13), ("2026-03", 4.25), ("2026-04", 4.6)]
    ts = build_tear_sheet(symbol="X", name="X", rates_history=rates)
    macro = {d.key: d for d in ts.dimensions}["macro"]
    assert macro.signal == "bearish"  # 10Y 4.6% ≥ 4.5 且上行 → 估值逆风
    assert macro.data_quality.level == "live"  # github 真实数据
    assert any("10Y" in e for e in macro.evidence)


def test_macro_insufficient_without_data():
    ts = build_tear_sheet(symbol="X", name="X")
    assert {d.key: d for d in ts.dimensions}["macro"].signal == "insufficient"


# ---- 宏观环境速判 ----

def test_macro_review_risk_on():
    r = build_macro_review(
        sp500_history=[("1", 6000), ("2", 6200), ("3", 6500), ("4", 6900)],
        rates_history=[("1", 2.5), ("2", 2.4), ("3", 2.3), ("4", 2.2)],
        oil_history=[("1", 100), ("2", 95), ("3", 90)],
        gold_history=[("1", 2000), ("2", 1950), ("3", 1850), ("4", 1800)],
    )
    assert r.overall_verdict == "风险偏好"
    assert all(d.data_quality.level == "live" for d in r.dimensions if d.signal != "insufficient")
    assert len(r.sp500_series) == 4


def test_macro_review_risk_off():
    r = build_macro_review(
        sp500_history=[("1", 7000), ("2", 6800), ("3", 6500), ("4", 6300)],
        rates_history=[("1", 4.0), ("2", 4.3), ("3", 4.6), ("4", 4.8)],
        oil_history=[("1", 80), ("2", 90), ("3", 100)],
        gold_history=[("1", 1800), ("2", 1900), ("3", 2000), ("4", 2100)],
    )
    assert r.overall_verdict == "避险"


def test_macro_review_insufficient():
    r = build_macro_review()
    assert r.overall_verdict == "数据不足"
    # 市场/利率/实际利率/曲线/信用/美元/油/通胀预期/CPI/避险 + 波动率/失业率 = 12 维；无输入时全 insufficient。
    assert len(r.dimensions) == 12
    assert all(d.signal == "insufficient" for d in r.dimensions)
    assert r.confidence == 0.0
    assert r.regime is not None and r.regime.name == "数据不足"


# ---- 宏观风险三件套（复用市场看板 live 读数：VIX / 2s10s / 信用利差）----

def _dash_ind(key: str, value, signal: str, *, unit: str = "", status: str = "解读", change_pct=None):
    """构造一条市场看板形态的指标 dict（_build_indicator 的字段子集），喂给宏观速判。"""
    return {
        "key": key, "name": key, "value": value, "unit": unit, "signal": signal,
        "status": status, "change_pct": change_pct, "source": "test-live",
    }


def test_macro_risk_dims_present_and_mapped():
    risk = {
        "vix": _dash_ind("vix", 34.0, "strong_bearish", status="高度恐慌 — 避险情绪主导", change_pct=12.0),
        "yield_curve": _dash_ind("yield_curve", -0.4, "bearish", unit="%", status="中度倒挂 — 经济放缓预警"),
        "credit_spread": _dash_ind("credit_spread", 360, "bullish", unit="bp", status="偏窄 — 风险偏好高"),
    }
    r = build_macro_review(risk_indicators=risk)
    dims = {d.key: d for d in r.dimensions}
    # strong_bearish 收敛到 bearish；bullish/方向语义保留；解读进入证据，置信度来自 live 源。
    assert dims["vix"].signal == "bearish" and dims["vix"].score < 0
    assert dims["yield_curve"].signal == "bearish"
    assert dims["credit_spread"].signal == "bullish" and dims["credit_spread"].score > 0
    assert dims["vix"].data_quality.level == "live"
    assert any("高度恐慌" in e for e in dims["vix"].evidence)
    assert "日内 +12.0%" in dims["vix"].evidence


def test_macro_risk_dim_insufficient_when_value_missing():
    # 源不可达：value 为 None 或状态以「数据」开头 → 诚实 insufficient，不参与加权。
    risk = {
        "vix": _dash_ind("vix", None, "neutral", status="数据暂不可用"),
        "credit_spread": _dash_ind("credit_spread", None, "neutral", status="数据暂不可用"),
    }
    r = build_macro_review(risk_indicators=risk)
    dims = {d.key: d for d in r.dimensions}
    assert dims["vix"].signal == "insufficient"
    assert dims["yield_curve"].signal == "insufficient"  # 未提供该 key
    assert dims["credit_spread"].signal == "insufficient"


def test_macro_risk_dims_swing_verdict_to_riskoff():
    # 仅靠 live 风险三件套全部 risk-off，也应把整体判成「避险」。
    risk = {
        "vix": _dash_ind("vix", 38.0, "strong_bearish", status="高度恐慌 — 避险主导"),
        "yield_curve": _dash_ind("yield_curve", -0.6, "bearish", unit="%", status="深度倒挂 — 衰退信号"),
        "credit_spread": _dash_ind("credit_spread", 720, "bearish", unit="bp", status="走阔 — 信用风险上升"),
    }
    r = build_macro_review(risk_indicators=risk)
    assert r.overall_verdict == "避险"


def test_macro_tier2_dims_present():
    # Tier2：美元/CPI/失业率经同一 seam 转维度，方向语义沿用看板。
    risk = {
        "dxy": _dash_ind("dxy", 110.0, "bearish", status="极强 — 全球流动性收紧"),
        "cpi": _dash_ind("cpi", 6.2, "bearish", unit="%", status="高通胀 — 紧缩持续"),
        "unemployment": _dash_ind("unemployment", 3.6, "bullish", unit="%", status="偏低 — 就业强劲"),
    }
    r = build_macro_review(risk_indicators=risk)
    dims = {d.key: d for d in r.dimensions}
    assert dims["dxy"].signal == "bearish"
    assert dims["cpi"].signal == "bearish"
    assert dims["unemployment"].signal == "bullish"
    assert len(r.dimensions) == 12


def test_real_rate_and_breakeven_dims():
    # 实际利率(估值锚) + 通胀预期(前瞻)经同一 seam 接入，方向沿用看板阈值。
    risk = {
        "real_rate": _dash_ind("real_rate", 2.6, "bearish", unit="%", status="高实际利率 — 显著限制性"),
        "breakeven": _dash_ind("breakeven", 2.2, "bullish", unit="%", status="通胀预期锚定 — 2%目标附近"),
    }
    r = build_macro_review(risk_indicators=risk)
    dims = {d.key: d for d in r.dimensions}
    assert dims["real_rate"].signal == "bearish" and dims["real_rate"].score < 0
    assert dims["breakeven"].signal == "bullish"
    assert dims["real_rate"].confidence == 0.68  # 估值锚，最高置信度


# ---- 投资时钟体制（增长×通胀象限）----

from deepfocus_api.schemas import TearSheetDimension
from deepfocus_api.tear_sheet import build_macro_regime


def _dim(key: str, signal: str, score: int, conf: float = 0.6) -> TearSheetDimension:
    return TearSheetDimension(key=key, label=key, signal=signal, score=score, headline="h", confidence=conf)


def test_regime_goldilocks():
    # 增长扩张 + 通胀回落 → Goldilocks，超配成长。
    dims = [
        _dim("market", "bullish", 40), _dim("yield_curve", "bullish", 30),
        _dim("credit_spread", "bullish", 25), _dim("vix", "bullish", 20),
        _dim("unemployment", "bullish", 35),
        _dim("inflation", "bullish", 30),  # 油价回落（risk bullish）→ 通胀轴取相反数转负
    ]
    reg = build_macro_regime(dims, cpi_value=1.5)
    assert reg.growth_axis == "扩张" and reg.inflation_axis == "回落"
    assert "Goldilocks" in reg.name
    assert "成长股" in reg.favored
    assert reg.confidence > 0


def test_regime_stagflation():
    # 增长放缓 + 通胀升温 → Stagflation，避险为主。
    dims = [
        _dim("market", "bearish", -40), _dim("yield_curve", "bearish", -30),
        _dim("credit_spread", "bearish", -35), _dim("vix", "bearish", -30),
        _dim("unemployment", "bearish", -25),
        _dim("inflation", "bearish", -40),  # 油价大涨（risk bearish）→ 通胀轴转正
    ]
    reg = build_macro_regime(dims, cpi_value=6.0)
    assert reg.growth_axis == "放缓" and reg.inflation_axis == "升温"
    assert "Stagflation" in reg.name
    assert "黄金" in reg.favored and "成长股" in reg.avoided


def test_regime_insufficient_when_no_signals():
    assert build_macro_regime([]).name == "数据不足"
    # 只有通胀信号、缺增长信号 → 仍数据不足（需两轴皆有信号）。
    assert build_macro_regime([_dim("inflation", "bearish", -20)]).name == "数据不足"


def test_regime_breakeven_drives_inflation_axis():
    # 前瞻通胀预期(breakeven)是通胀轴首选：高 breakeven → 通胀升温，即便油价无信号。
    growth = [_dim("market", "bullish", 40), _dim("yield_curve", "bullish", 30), _dim("vix", "bullish", 20)]
    hot = build_macro_regime(growth, breakeven_value=3.0)   # 3.0% 远高于 2.2 中枢
    cool = build_macro_regime(growth, breakeven_value=1.6)  # 1.6% 低于中枢
    assert hot.inflation_axis == "升温" and "Reflation" in hot.name
    assert cool.inflation_axis == "回落"
    assert any("Breakeven" in e for e in hot.evidence)


def test_regime_ignores_unavailable_cpi_breakeven():
    # 回归：看板用 value=0/「数据暂不可用」表示缺数据，不得被当成 0% 真读数喂进 regime（假通缩）。
    r = build_macro_review(
        sp500_history=[("2026-05-01", 6000), ("2026-05-02", 6200), ("2026-05-03", 6500), ("2026-05-04", 6900)],
        risk_indicators={
            "vix": _dash_ind("vix", 14.0, "bullish", status="低波动 — 趋势上行"),
            "cpi": _dash_ind("cpi", 0, "neutral", status="数据暂不可用"),
            "breakeven": _dash_ind("breakeven", 0, "neutral", status="数据暂不可用"),
        },
    )
    # 通胀轴无任何真实信号（油价未传、CPI/breakeven 不可用）→ 数据不足，而非被假 0% 拖成"回落"。
    assert r.regime.name == "数据不足"
    assert not any("0.0%" in e for e in r.regime.evidence)


def test_freshness_downgrades_stale_data():
    from deepfocus_api.tear_sheet import _apply_freshness, _freshness
    # 远古日期 → 显著折减 + 陈旧提示；不可解析的占位日期 → 不罚。
    factor, note = _freshness("2020-01-01")
    assert factor < 1.0 and note is not None
    assert _freshness("99")[0] == 1.0  # 占位串视为新鲜（保护既有用占位日期的单测）

    fresh_dim = _dim("market", "bullish", 40, conf=0.6)
    stale = _apply_freshness(fresh_dim, "2020-01-01")
    assert stale.confidence < 0.6
    assert any("滞后" in e or "滞" in e for e in stale.evidence)


def test_macro_review_stale_github_data_flagged():
    # 真实路径：传一段远古 sp500 历史，市场维度应被折减置信度并标注。
    old = [("2019-01-01", 2600), ("2019-02-01", 2700), ("2019-03-01", 2800), ("2019-04-01", 2900)]
    r = build_macro_review(sp500_history=old)
    market = {d.key: d for d in r.dimensions}["market"]
    assert market.signal != "insufficient"
    assert market.confidence < 0.6
    assert any("滞后" in e for e in market.evidence)


def test_macro_review_attaches_regime():
    r = build_macro_review(
        sp500_history=[("1", 6000), ("2", 6200), ("3", 6500), ("4", 6900)],
        oil_history=[("1", 100), ("2", 95), ("3", 90)],
        risk_indicators={
            "vix": _dash_ind("vix", 14.0, "bullish", status="低波动 — 趋势上行"),
            "yield_curve": _dash_ind("yield_curve", 1.2, "bullish", unit="%", status="陡峭 — 增长预期强"),
            "unemployment": _dash_ind("unemployment", 3.6, "bullish", unit="%", status="偏低 — 就业强劲"),
        },
    )
    assert r.regime is not None
    assert r.regime.name != "数据不足"
    assert "投资时钟" in r.narrative


# ---- 中国宏观轴（沪深300/北向/人民币/中国10Y，独立于美国 verdict）----

from deepfocus_api.tear_sheet import build_china_macro


def test_china_macro_warm_tilt():
    china = {
        "csi300": _dash_ind("csi300", 3300.0, "bullish", status="偏低 — 估值具备吸引力"),
        "northbound_flow": _dash_ind("northbound_flow", 120.0, "strong_bullish", unit="亿", status="大举流入 — 外资强烈看多A股"),
        "usd_cny": _dash_ind("usd_cny", 6.7, "bullish", status="升值 — 外资流入动力增强"),
        "china_bond_10y": _dash_ind("china_bond_10y", 2.3, "bullish", unit="%", status="宽松 — 估值支撑"),
    }
    dims, read = build_china_macro(china)
    by_key = {d.key: d for d in dims}
    # strong_bullish 收敛到 bullish；中国轴对A股语义保留。
    assert by_key["northbound_flow"].signal == "bullish"
    assert by_key["usd_cny"].signal == "bullish"
    assert "偏暖" in read
    assert by_key["csi300"].data_quality.level == "live"


def test_china_macro_insufficient_when_empty():
    dims, read = build_china_macro(None)
    assert all(d.signal == "insufficient" for d in dims)
    assert "暂不可用" in read


def test_macro_review_attaches_china_block():
    r = build_macro_review(china_indicators={
        "northbound_flow": _dash_ind("northbound_flow", -90.0, "bearish", unit="亿", status="持续流出 — 外资看空"),
        "usd_cny": _dash_ind("usd_cny", 7.3, "bearish", status="贬值 — A股承压"),
    })
    assert len(r.china_dimensions) == 4  # 四件套，缺的标 insufficient
    assert "偏冷" in r.china_read
    # 中国轴独立：不进美国 verdict 维度，dimensions 仍为 12。
    assert len(r.dimensions) == 12


# ---- 投研晨报（多引擎聚合）----

def test_briefing_riskon_empty_portfolio():
    sp = [("1", 6000), ("2", 6200), ("3", 6500), ("4", 6900)]
    rates = [("1", 2.5), ("2", 2.4), ("3", 2.3), ("4", 2.2)]
    macro = build_macro_review(
        sp500_history=sp, rates_history=rates,
        oil_history=[("1", 100), ("2", 95), ("3", 90)],
        gold_history=[("1", 2000), ("2", 1950), ("3", 1850), ("4", 1800)],
    )
    # 空仓但带真实宏观背景（与 endpoint 一致：组合也传 sp500/rates）
    portfolio = build_portfolio_review(
        {"portfolio": {}, "open_positions": [], "alerts": []},
        sp500_history=sp, rates_history=rates,
    )
    b = build_briefing(macro, portfolio)
    assert b.macro_verdict == "风险偏好"
    assert b.portfolio_verdict == "空仓"
    assert "空仓" in b.headline
    assert "顺周期" in b.headline  # risk-on + 空仓 → 顺周期建仓建议
    assert b.macro is macro and b.portfolio is portfolio
    assert b.data_quality.level == "live"  # 两引擎都有 live 数据


def test_briefing_riskoff_highrisk():
    macro = build_macro_review(
        sp500_history=[("1", 7000), ("2", 6800), ("3", 6500), ("4", 6300)],
        rates_history=[("1", 4.0), ("2", 4.3), ("3", 4.6), ("4", 4.8)],
        oil_history=[("1", 80), ("2", 90), ("3", 100)],
        gold_history=[("1", 1800), ("2", 1900), ("3", 2000), ("4", 2100)],
    )
    summary = {
        "portfolio": {"total_value": 1_000_000, "total_pnl_pct": -12.0,
                      "sector_exposure": {"科技": 70, "金融": 30}},
        "open_positions": [
            {"symbol": "TSLA", "position_size_pct": 55, "stop_loss": None, "sector": "科技"},
            {"symbol": "NVDA", "position_size_pct": 45, "stop_loss": None, "sector": "科技"},
        ],
        "alerts": [{"level": "danger", "message": "回撤超过阈值"}],
    }
    portfolio = build_portfolio_review(summary)
    b = build_briefing(macro, portfolio)
    assert b.macro_verdict == "避险"
    assert b.portfolio_verdict == "高风险"
    assert "降低敞口" in b.headline
    assert "避险" in b.headline


def test_briefing_insufficient_macro():
    macro = build_macro_review()  # 数据不足
    portfolio = build_portfolio_review({"portfolio": {}, "open_positions": [], "alerts": []})
    b = build_briefing(macro, portfolio)
    assert b.macro_verdict == "数据不足"
    assert "暂不充分" in b.headline


# ---- 自选股行业暴露（晨报覆盖自选股）----

def test_watchlist_cyclical_riskon():
    s = {
        "AAPL": "Information Technology",
        "MSFT": "Information Technology",
        "AMZN": "Consumer Discretionary",
        "JPM": "Financials",
    }
    w = build_watchlist_summary(s, "风险偏好")
    assert w.total == 4 and w.covered == 4
    assert w.sectors[0].sector == "Information Technology" and w.sectors[0].count == 2
    assert w.sectors[0].pct == 50
    assert "一致" in w.note
    assert w.data_quality.level == "live"


def test_watchlist_defensive_riskoff():
    s = {"KO": "Consumer Staples", "DUK": "Utilities", "JNJ": "Health Care"}
    w = build_watchlist_summary(s, "避险")
    assert w.covered == 3
    assert "稳健" in w.note


def test_watchlist_uncovered():
    w = build_watchlist_summary({"600519.SS": None, "0700.HK": None}, "中性")
    assert w.total == 2 and w.covered == 0
    assert w.sectors == []
    assert "未匹配" in w.note
    assert w.data_quality.level != "live"


# ---- catalyst 盈利质量（nasdaq）----

def test_catalyst_nasdaq_beats():
    from deepfocus_api.tear_sheet import _dim_catalyst

    eps = {
        "history": [
            {"period": "Q1", "consensus": 1.0, "earnings": 1.2},
            {"period": "Q2", "consensus": 1.1, "earnings": 1.3},
            {"period": "Q3", "consensus": 1.2, "earnings": 1.4},
            {"period": "Q4", "consensus": 1.3, "earnings": 1.5},
        ],
        "next_consensus": 1.6,
    }
    d = _dim_catalyst([], nasdaq_eps=eps, symbol="AAPL")
    assert d.signal == "bullish"  # 4/4 超预期
    assert "4/4" in d.headline
    assert d.data_quality.level == "live"
    assert any("1.6" in e for e in d.evidence)
    assert d.sources and d.sources[0].url and "aapl" in d.sources[0].url


def test_catalyst_nasdaq_misses():
    from deepfocus_api.tear_sheet import _dim_catalyst

    eps = {
        "history": [
            {"period": "Q1", "consensus": 2.0, "earnings": 1.0},
            {"period": "Q2", "consensus": 2.0, "earnings": 1.5},
        ],
        "next_consensus": 0.5,
    }
    d = _dim_catalyst([], nasdaq_eps=eps, symbol="X")
    assert d.signal == "bearish"  # 0/2 超预期


def test_catalyst_fallback_when_no_nasdaq():
    from deepfocus_api.tear_sheet import _dim_catalyst

    # 无 nasdaq → 退回原财报临近逻辑（事件 context，neutral）
    d = _dim_catalyst([_earnings(days=7)])
    assert d.signal == "neutral"
    assert "⚡" in d.headline or "财报" in d.headline


# ---- options 期权情绪（nasdaq Put/Call）----

def test_options_nasdaq_bullish():
    from deepfocus_api.tear_sheet import _dim_options

    d = _dim_options(None, nasdaq_opts={"pc_volume_ratio": 0.42, "pc_oi_ratio": 0.43}, symbol="AAPL")
    assert d.signal == "bullish"  # P/C < 0.7 看涨主导
    assert "P/C" in d.headline
    assert d.score > 0
    assert d.data_quality.level == "live"
    assert any("0.42" in e for e in d.evidence)


def test_options_nasdaq_bearish():
    from deepfocus_api.tear_sheet import _dim_options

    d = _dim_options(None, nasdaq_opts={"pc_volume_ratio": 1.3}, symbol="X")
    assert d.signal == "bearish"  # P/C > 1.1 看跌主导
    assert d.score < 0


def test_options_fallback_signal():
    from deepfocus_api.tear_sheet import _dim_options

    # 无 nasdaq → 退回原 OptionsSignal 逻辑
    d = _dim_options(_options(direction="偏多"))
    assert d.signal == "bullish"


# ---- A股 catalyst（东财财报）----

def test_catalyst_eastmoney_high_growth():
    from deepfocus_api.tear_sheet import _dim_catalyst

    fin = {"profit_yoy": 48.5, "revenue_yoy": 52.4, "roe": 5.98, "report_date": "2026-03-31"}
    d = _dim_catalyst([], eastmoney_fin=fin, symbol="300750")
    assert d.signal == "bullish"  # 净利 +48.5% 高增
    assert "高增" in d.headline
    assert d.data_quality.level == "live"
    assert any("净利同比" in e for e in d.evidence)
    assert d.sources and d.sources[0].provider == "eastmoney"


def test_catalyst_eastmoney_decline():
    from deepfocus_api.tear_sheet import _dim_catalyst

    d = _dim_catalyst([], eastmoney_fin={"profit_yoy": -20.0, "revenue_yoy": -10.0}, symbol="600000")
    assert d.signal == "bearish"  # 净利 -20% 下滑


def test_market_context_localized_index():
    from deepfocus_api.tear_sheet import _dim_market_context

    history = [("2026-01", 4000.0), ("2026-02", 4100.0), ("2026-03", 4300.0), ("2026-04", 4500.0)]
    d = _dim_market_context(history, "沪深300", None, "eastmoney")
    assert "沪深300" in d.label
    assert any("沪深300" in e for e in d.evidence)
    assert d.signal == "bullish"  # +12.5% 近3月
    assert d.data_quality.level == "live"


# ---- 整体 dq live（估值源）+ 组合实时（继续抠三方向）----

def test_valuation_source_num_parse():
    from deepfocus_api.valuation_source import _num

    assert _num("4.57T") == 4.57e12
    assert _num("530B") == 530e9
    assert _num("37.73") == 37.73
    assert _num(None) is None
    assert _num("-") is None


def test_dim_scale_live_provider():
    from deepfocus_api.tear_sheet import _dim_scale

    d = _dim_scale(4.57e12, None, "stockanalysis")
    assert d.data_quality.level == "live"  # 市值来自 live 估值源
    assert d.sources and d.sources[0].provider == "stockanalysis"
    # 无 cap_provider → 跟随 quote（无 quote → none/degraded）
    d2 = _dim_scale(4.57e12, None, None)
    assert d2.data_quality.level != "live"


def test_dim_valuation_mixed_source_not_inflated():
    """诚实：52周位置来自 google finance（degraded）驱动 signal 时，即使 PE 是 live 也不虚标 live。"""
    from deepfocus_api.tear_sheet import _dim_valuation

    d = _dim_valuation(
        {"price": 311, "wk52_high": 320, "wk52_low": 195},
        {"pe_ratio": 37.7, "pb_ratio": 50.0, "provider": "stockanalysis"},
    )
    assert d.data_quality.level == "degraded"  # 52周(google)驱动 signal → 整卡取最差，不虚标 live
    assert any("P/E 37.7" in e for e in d.evidence)
    assert any("P/B" in e for e in d.evidence)


def test_dim_valuation_all_live():
    """升级：52周来自 Yahoo 官方（live）+ PE 来自 live 估值源 → 整卡真 live。"""
    from deepfocus_api.schemas import MarketQuote
    from deepfocus_api.tear_sheet import _dim_valuation

    q = MarketQuote(
        symbol="AAPL", price=311, provider="yahoo-finance", provider_name="Yahoo Finance",
        fetched_at="2026-06-05T00:00:00Z", is_realtime=True, wk52_high=320, wk52_low=195,
    )
    d = _dim_valuation({"price": 311}, {"pe_ratio": 37.7, "provider": "stockanalysis"}, q)
    assert d.data_quality.level == "live"  # 52周(yahoo)+PE(stockanalysis) 均 live
    assert any(s.provider == "yahoo-finance" for s in d.sources)
    assert any(s.provider == "stockanalysis" for s in d.sources)
    assert any("52周位置" in e for e in d.evidence)


def test_apply_live_prices():
    from deepfocus_api.risk_management import apply_live_prices

    s = {
        "open_positions": [
            {"symbol": "AAPL", "quantity": 10, "entry_price": 300, "current_price": 300, "status": "open"},
        ],
        "portfolio": {},
    }
    s2 = apply_live_prices(s, {"AAPL": 311.23})
    assert s2["open_positions"][0]["current_price"] == 311.23
    assert s2["price_source"] == "google-finance"
    assert s2["priced_count"] == 1
    assert apply_live_prices({"open_positions": []}, {}).get("price_source") is None  # 空不改


def _sample_ts():
    from deepfocus_api.tear_sheet import build_tear_sheet

    return build_tear_sheet(
        symbol="TEST",
        name="测试标的",
        valuation_data={"pe_ratio": 20.0, "pb_ratio": 5.0, "market_cap": 1e11, "provider": "eastmoney"},
        valuation={"pe_ratio": 20.0},
    )


def test_enhance_narrative_success(monkeypatch):
    """LLM 可用 → narrative 被 LLM 观点替换，provider 标 minimax。"""
    import asyncio

    from deepfocus_api import main as main_mod

    class _Ok:
        provider = "minimax"

        async def synthesize_tear_sheet_narrative(self, _ts):
            return "LLM 合成的买方观点"

    monkeypatch.setattr("deepfocus_api.llm.CloudResearchLLM", _Ok)
    out = asyncio.run(main_mod._enhance_tear_sheet_narrative(_sample_ts()))
    assert out.narrative == "LLM 合成的买方观点"
    assert out.narrative_provider == "minimax"


def test_enhance_narrative_fallback_on_failure(monkeypatch):
    """LLM 抛错 → 回退确定性模板叙述，verdict/provider 不被污染。"""
    import asyncio

    from deepfocus_api import main as main_mod

    class _Boom:
        provider = "minimax"

        async def synthesize_tear_sheet_narrative(self, _ts):
            raise RuntimeError("LLM down")

    monkeypatch.setattr("deepfocus_api.llm.CloudResearchLLM", _Boom)
    ts = _sample_ts()
    base_narr, base_verdict = ts.narrative, ts.overall_verdict
    out = asyncio.run(main_mod._enhance_tear_sheet_narrative(ts))
    assert out.narrative == base_narr  # 回退模板
    assert out.narrative_provider == "rule-template"
    assert out.overall_verdict == base_verdict  # verdict 不变


def test_enhance_narrative_mock_noop(monkeypatch):
    """mock provider → 不调用 LLM，保留确定性模板。"""
    import asyncio

    from deepfocus_api import main as main_mod

    class _Mock:
        provider = "mock"

        async def synthesize_tear_sheet_narrative(self, _ts):
            raise AssertionError("mock provider 不应调用 LLM")

    monkeypatch.setattr("deepfocus_api.llm.CloudResearchLLM", _Mock)
    out = asyncio.run(main_mod._enhance_tear_sheet_narrative(_sample_ts()))
    assert out.narrative_provider == "rule-template"


def test_enhance_review_narrative_portfolio(monkeypatch):
    """组合速判 LLM 增强：narrative 被替换、provider 标 minimax、view 正确传入。"""
    import asyncio

    from deepfocus_api import main as main_mod
    from deepfocus_api.tear_sheet import build_portfolio_review

    seen = {}

    class _Ok:
        provider = "minimax"

        async def synthesize_review_narrative(self, **kw):
            seen["view"] = kw.get("view")
            return "组合 LLM 观点"

    monkeypatch.setattr("deepfocus_api.llm.CloudResearchLLM", _Ok)
    review = build_portfolio_review({"open_positions": [], "portfolio": {}})
    out = asyncio.run(main_mod._enhance_review_narrative(review, view="portfolio", subject="组合"))
    assert out.narrative == "组合 LLM 观点"
    assert out.narrative_provider == "minimax"
    assert seen["view"] == "portfolio"


def test_enhance_briefing_headline_success(monkeypatch):
    """晨报 LLM 增强：headline 被替换、headline_provider 标 minimax。"""
    import asyncio

    from deepfocus_api import main as main_mod
    from deepfocus_api.tear_sheet import build_briefing, build_macro_review, build_portfolio_review

    class _Ok:
        provider = "minimax"

        async def synthesize_briefing_headline(self, *a, **k):
            return "晨会纪要 LLM"

    monkeypatch.setattr("deepfocus_api.llm.CloudResearchLLM", _Ok)
    briefing = build_briefing(build_macro_review(), build_portfolio_review({"open_positions": [], "portfolio": {}}))
    out = asyncio.run(main_mod._enhance_briefing_headline(briefing))
    assert out.headline == "晨会纪要 LLM"
    assert out.headline_provider == "minimax"


def test_enhance_briefing_fallback_on_failure(monkeypatch):
    """晨报 LLM 抛错 → 回退确定性 headline，provider 仍 rule-template。"""
    import asyncio

    from deepfocus_api import main as main_mod
    from deepfocus_api.tear_sheet import build_briefing, build_macro_review, build_portfolio_review

    class _Boom:
        provider = "minimax"

        async def synthesize_briefing_headline(self, *a, **k):
            raise RuntimeError("LLM down")

    monkeypatch.setattr("deepfocus_api.llm.CloudResearchLLM", _Boom)
    briefing = build_briefing(build_macro_review(), build_portfolio_review({"open_positions": [], "portfolio": {}}))
    base = briefing.headline
    out = asyncio.run(main_mod._enhance_briefing_headline(briefing))
    assert out.headline == base
    assert out.headline_provider == "rule-template"


def test_dim_consensus():
    from deepfocus_api.tear_sheet import _dim_consensus

    d = _dim_consensus({"target_price": 298.07, "upside_pct": 40.99, "rating": "Buy", "analyst_count": 39})
    assert d.signal == "bullish"  # 上行 41% → 看多
    assert d.data_quality.level == "live"
    assert any("目标价" in e for e in d.evidence)
    assert any("39 家" in e for e in d.evidence)
    assert _dim_consensus(None).signal == "insufficient"
    assert _dim_consensus({}).signal == "insufficient"


def test_dim_fund_flow():
    from deepfocus_api.tear_sheet import _dim_fund_flow

    d = _dim_fund_flow({"main_flow_5d": 3.2e8, "flow_days": 5})
    assert d.signal == "bullish"  # 主力净流入 3.2 亿
    assert d.data_quality.level == "live"
    assert any("流入" in e for e in d.evidence)
    assert _dim_fund_flow({"main_flow_5d": -2.5e8}).signal == "bearish"
    assert _dim_fund_flow(None).signal == "insufficient"
