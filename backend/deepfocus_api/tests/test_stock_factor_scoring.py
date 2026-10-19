"""Synthetic observations verify arithmetic and data gates, not market alpha."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
import math

import pytest

from deepfocus_api.stock_factor_scoring import (
    MIN_CHIP_COVERAGE, PRICE_BINS, parse_daily_bars, parse_flow_bars,
    score_capital, score_chips,
)


NOW = date(2026, 10, 19)
SYMBOL = "600000"


def _payload(lines, code=SYMBOL):
    return {"data": {"code": code, "klines": lines}}


def _daily_line(day=NOW.isoformat(), **changes):
    values = {"date": day, "open": 10, "close": 10.1, "high": 10.3, "low": 9.7,
              "volume": 10000, "amount": 1000000, "amp": 6, "pct": 1,
              "change": .1, "turnover": 2}
    values.update(changes)
    return ",".join(str(value) for value in values.values())


def _bars(count=120, turnover=2, flat=False):
    # Synthetic weekdays exercise arithmetic; source dates are validated for
    # format, future observations and conflicts, not against a trading calendar.
    days, current = [], NOW
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current -= timedelta(days=1)
    result = []
    for index, day in enumerate(reversed(days)):
        close = 10 if flat else 10 + math.sin(index / 7) * .15
        result.append({"date": day.isoformat(), "open": close, "close": close,
                       "high": close if flat else close + .25,
                       "low": close if flat else close - .25,
                       "volume": 10000, "amount": 1000000, "turnover": turnover})
    return result


def _flows(count=5, value=0):
    return [{"date": (NOW - timedelta(days=count - index - 1)).isoformat(), "main_net": value}
            for index in range(count)]


def _metrics(report):
    return {row["key"]: row["value"] for row in report["metrics"]}


def test_daily_parser_maps_correct_fields_and_preserves_zero_turnover():
    rows = parse_daily_bars(_payload([_daily_line(turnover=0)]), SYMBOL, NOW)
    assert rows == [{"date": "2026-10-19", "open": 10.0, "close": 10.1, "high": 10.3,
                     "low": 9.7, "volume": 10000.0, "amount": 1000000.0, "turnover": 0.0}]


@pytest.mark.parametrize("changes", [{"open": 11}, {"low": 0}, {"close": -1},
    {"amount": -1}, {"volume": -1}, {"turnover": -1}, {"turnover": 100.01},
    {"close": "NaN"}, {"turnover": "inf"}, {"amp": "NaN"}, {"pct": "-inf"}])
def test_daily_parser_rejects_invalid_numbers_and_ohlc(changes):
    with pytest.raises(ValueError):
        parse_daily_bars(_payload([_daily_line(**changes)]), SYMBOL, NOW)


@pytest.mark.parametrize("parser,line", [
    (parse_daily_bars, _daily_line("2026-10-20")),
    (parse_flow_bars, "2026-10-20,0"),
    (parse_daily_bars, _daily_line("2026-02-31")),
    (parse_flow_bars, "20261019,0"),
])
def test_parsers_reject_future_and_invalid_dates(parser, line):
    with pytest.raises(ValueError):
        parser(_payload([line]), SYMBOL, NOW)


@pytest.mark.parametrize("parser,line", [(parse_daily_bars, _daily_line()), (parse_flow_bars, "2026-10-19,0")])
def test_parsers_reject_wrong_stock(parser, line):
    with pytest.raises(ValueError, match="mismatch"):
        parser(_payload([line], "000001"), SYMBOL, NOW)
    with pytest.raises(ValueError):
        parser(_payload([line]), "600000.SH", NOW)


def test_duplicate_same_date_must_be_identical_and_output_is_sorted():
    first, second = _daily_line("2026-10-16"), _daily_line()
    rows = parse_daily_bars(_payload([second, first, second]), SYMBOL, NOW)
    assert [row["date"] for row in rows] == ["2026-10-16", "2026-10-19"]
    with pytest.raises(ValueError, match="conflicting"):
        parse_daily_bars(_payload([first, _daily_line("2026-10-16", turnover=3)]), SYMBOL, NOW)
    with pytest.raises(ValueError, match="conflicting"):
        parse_flow_bars(_payload(["2026-10-19,0", "2026-10-19,1"]), SYMBOL, NOW)


def test_flow_parser_preserves_real_zero_and_limits_to_latest_five_dates():
    lines = [f"2026-10-{day:02d},{0 if day == 19 else day * 1000}" for day in range(12, 20)]
    parsed = parse_flow_bars(_payload(list(reversed(lines))), SYMBOL, NOW)
    assert len(parsed) == 5 and parsed[0]["date"] == "2026-10-15"
    assert parsed[-1] == {"date": "2026-10-19", "main_net": 0.0}
    with pytest.raises(ValueError):
        parse_flow_bars(_payload(["2026-10-19,nan"]), SYMBOL, NOW)


def test_capital_valid_zeros_are_scored_as_50_only_with_five_observations():
    report = score_capital(_flows(), NOW)
    assert report["status"] == "ok" and report["score"] == 50
    assert report["currency"] == "CNY"
    assert _metrics(report)["net_5d"] == 0
    assert _metrics(report)["positive_days"] == 0
    assert score_capital(_flows(4), NOW)["score"] is None
    assert score_capital([], NOW)["score"] is None
    assert "不是上涨概率" in report["method"]


def test_capital_formula_and_non_probability_rule_label():
    report = score_capital(_flows(value=40_000_000), NOW)
    assert report["score"] == round(50 + 50 * math.tanh(1), 1)
    assert _metrics(report)["positive_days"] == 5
    assert report["date_range"] == ["2026-10-15", "2026-10-19"]


@pytest.mark.parametrize("value", [-1e300, -1e9, 0, 1e9, 1e300])
def test_capital_score_bounds_and_all_outputs_are_json_finite(value):
    report = score_capital(_flows(value=value), NOW)
    assert report["status"] == "ok" and 0 <= report["score"] <= 100
    json.dumps(report, allow_nan=False)


def test_capital_rejects_stale_future_conflicts_and_overflow():
    assert score_capital(_flows(), NOW + timedelta(days=8))["status"] == "stale"
    assert score_capital(_flows(), NOW + timedelta(days=7))["status"] == "ok"
    future = _flows()
    future[-1]["date"] = "2026-10-20"
    assert score_capital(future, NOW)["status"] == "invalid"
    assert score_capital(_flows() + [{"date": "2026-10-19", "main_net": 1}], NOW)["score"] is None
    assert score_capital(_flows(value=1e308), NOW)["status"] == "invalid"


def test_chip_mass_and_unknown_history_are_conserved():
    report = score_chips(_bars(), NOW)
    assert report["status"] == "ok"
    assert len(report["distribution"]) == PRICE_BINS
    assert all(row["weight"] >= 0 for row in report["distribution"])
    observed = math.fsum(row["weight"] for row in report["distribution"])
    expected_residual = .98 ** 120
    assert report["historical_residual"] == pytest.approx(expected_residual)
    assert observed == pytest.approx(1 - expected_residual)
    assert observed + report["historical_residual"] == pytest.approx(1)
    assert report["coverage"] == pytest.approx(observed)
    assert 0 <= report["score"] <= 100
    json.dumps(report, allow_nan=False)


def test_chip_zero_turnover_does_not_invent_observed_holdings_or_neutral_score():
    report = score_chips(_bars(turnover=0), NOW)
    assert report["status"] == "missing" and report["score"] is None
    assert report["historical_residual"] == 1 and report["coverage"] == 0
    assert math.fsum(row["weight"] for row in report["distribution"]) == 0
    assert "median_cost" not in _metrics(report)
    json.dumps(report, allow_nan=False)


def test_flat_one_price_distribution_has_finite_exact_costs():
    report = score_chips(_bars(flat=True), NOW)
    metrics = _metrics(report)
    assert report["status"] == "ok"
    assert metrics["cost_70"] == [10, 10]
    assert metrics["cost_90"] == [10, 10]
    assert metrics["median_cost"] == 10
    assert metrics["estimated_profit_ratio"] == 100
    assert metrics["concentration_90"] == 0
    assert report["score"] == 100
    json.dumps(report, allow_nan=False)


def test_chip_uses_each_historical_turnover_instead_of_repeating_current_turnover():
    bars = _bars(flat=True)
    for row in bars[60:]:
        row.update(open=20, close=20, high=20, low=20, turnover=0)
    report = score_chips(bars, NOW)
    assert report["status"] == "missing"  # Only 70.24% modeled, despite 120 rows.
    assert report["historical_residual"] == pytest.approx(.98 ** 60)
    assert _metrics(report)["median_cost"] < 10.1
    bars[0]["turnover"] = 100
    report = score_chips(bars, NOW)
    assert report["status"] == "ok" and report["coverage"] == 1
    assert _metrics(report)["median_cost"] < 10.1


def test_chip_requires_both_sample_count_and_eighty_percent_coverage():
    assert score_chips(_bars(119, turnover=100), NOW)["score"] is None
    below = score_chips(_bars(120, turnover=1), NOW)
    above = score_chips(_bars(120, turnover=2), NOW)
    assert below["coverage"] < MIN_CHIP_COVERAGE and below["score"] is None
    assert above["coverage"] > MIN_CHIP_COVERAGE and above["score"] is not None
    assert _metrics(below)["median_cost"] > 0  # Useful metrics remain available.


def test_chip_stale_and_invalid_direct_input_fail_closed():
    report = score_chips(_bars(), NOW + timedelta(days=8))
    assert report["status"] == "stale" and report["score"] is None
    assert _metrics(report)["median_cost"] > 0
    invalid = _bars()
    invalid[-1]["turnover"] = float("nan")
    assert score_chips(invalid, NOW)["status"] == "invalid"
    invalid = _bars()
    invalid[-1]["date"] = "2026-10-20"
    assert score_chips(invalid, NOW)["status"] == "invalid"
    invalid = _bars()
    invalid.append({**invalid[-1], "amount": 2})
    assert score_chips(invalid, NOW)["status"] == "invalid"


def test_scores_do_not_mutate_input_and_accept_china_date_of_aware_now():
    bars, flows = _bars(), _flows()
    saved_bars, saved_flows = deepcopy(bars), deepcopy(flows)
    # UTC October 18 18:00 is October 19 in Shanghai.
    now = datetime(2026, 10, 18, 18, tzinfo=timezone.utc)
    assert score_chips(bars, now)["status"] == "ok"
    assert score_capital(flows, now)["status"] == "ok"
    assert bars == saved_bars and flows == saved_flows


def test_high_profit_ratio_alone_does_not_create_high_chip_structure_score():
    bars = _bars(flat=True, turnover=10)
    bars[-1].update(open=20, close=20, high=20, low=20, turnover=0)
    report = score_chips(bars, NOW)
    assert report["status"] == "ok"
    assert _metrics(report)["estimated_profit_ratio"] == 100
    assert report["score"] < 70
    assert "自定义估算" in report["method"] and "非真实账户" in report["method"]
