"""Numerical oracles, invalid-input guards, and real third-party integration."""
import base64
import copy
import io
import json
import math
from datetime import date, timedelta

import pytest

from deepfocus_api import research_analytics as analytics


def dates(count):
    current, result = date(2025, 1, 2), []
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current.isoformat())
        current += timedelta(days=1)
    return result


def performance_data():
    return {"currency": "CNY", "name": "用户策略", "returns": [
        {"date": day, "return": [-0.02, 0.01, 0.005, -0.004, 0.015][i % 5], "benchmark_return": [0.003, -0.002, 0.001, -0.003, 0.004][i % 5]}
        for i, day in enumerate(dates(45))]}


def portfolio_data(count=65):
    prices = []
    a, b = 10.0, 20.0
    for i, day in enumerate(dates(count)):
        if i:
            a *= 1 + [0.01, -0.02, 0.005, 0.015, -0.01][i % 5]
            b *= 1 + [-0.005, 0.01, 0.003, -0.015, 0.02][i % 5]
        prices.extend([{"date": day, "symbol": "A", "close": a}, {"date": day, "symbol": "B", "close": b}])
    return {"currency": "CNY", "prices": prices, "weights": [{"symbol": "A", "weight": 0.6}, {"symbol": "B", "weight": 0.4}]}


def financial_data():
    return {"period_type": "annual", "records": [
        {"symbol": "A", "period": "2024", "currency": "CNY", "revenue": 1000, "net_income": 100,
         "operating_cashflow": 120, "capex": 30, "total_assets": 1000, "total_equity": 500, "total_debt": 200,
         "current_assets": 500, "current_liabilities": 100},
        {"symbol": "A", "period": "2025", "currency": "CNY", "revenue": 1200, "net_income": 120,
         "operating_cashflow": 150, "capex": 40, "total_assets": 1200, "total_equity": 700, "total_debt": 240,
         "current_assets": 600, "current_liabilities": 200},
        {"symbol": "B", "period": "2024", "currency": "USD", "revenue": 500, "net_income": 50,
         "operating_cashflow": 75, "capex": 20, "total_assets": 700, "total_equity": 300, "total_debt": 100}]}


def summary(report, label):
    return next(item["value"] for item in report["summary"] if item["label"] == label)


def table(report, key):
    return next(item for item in report["tables"] if item["key"] == key)


def financial_rows(report, period):
    return [row for row in table(report, "financial_history")["rows"] if row["period"] == period]


@pytest.fixture(scope="module")
def real_performance_report():
    pytest.importorskip("quantstats")
    return analytics.analyze_performance(performance_data())


def test_actual_quantstats_compound_drawdown_and_risk_oracles(real_performance_report):
    values = [row["return"] for row in performance_data()["returns"]]
    nav = 1.0
    peak = 1.0
    worst = 0.0
    for value in values:
        nav *= 1 + value
        peak = max(peak, nav)
        worst = min(worst, nav / peak - 1)
    mean = sum(values) / len(values)
    sigma = math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))
    assert summary(real_performance_report, "累计净收益") == pytest.approx(nav - 1)
    assert summary(real_performance_report, "最大回撤") == pytest.approx(worst)
    assert summary(real_performance_report, "年化波动率") == pytest.approx(sigma * math.sqrt(252))
    assert summary(real_performance_report, "Sharpe") == pytest.approx(mean / sigma * math.sqrt(252))


def test_real_quantstats_html_contains_native_report_and_safe_title(real_performance_report):
    generated = real_performance_report["report_html"]
    assert "QuantStats" in generated or "quantstats" in generated
    assert "<svg" in generated
    assert len(generated) > 10000
    assert real_performance_report["engine"] == "QuantStats"
    json.dumps(real_performance_report, allow_nan=False)


def test_monthly_returns_and_benchmark_are_same_date_not_trade_winrate(real_performance_report):
    records = performance_data()["returns"]
    expected = {}
    for row in records:
        month = row["date"][:7]
        expected[month] = expected.get(month, 1) * (1 + row["return"])
    for row in table(real_performance_report, "monthly")["rows"]:
        assert row["return"] == pytest.approx(expected[row["month"]] - 1)
    chart = next(item for item in real_performance_report["charts"] if item["key"] == "net_value")
    assert all("benchmark" in row for row in chart["data"])
    assert any("逐笔交易胜率" in text for text in real_performance_report["methodology"])


def test_net_values_compute_real_returns_without_reapplying_fees(monkeypatch):
    pytest.importorskip("quantstats")
    monkeypatch.setattr(analytics, "_quantstats_html", lambda *args: "tested-separately")
    rows, nav = [], 100.0
    for i, day in enumerate(dates(35)):
        if i:
            nav *= 1 + [0.01, -0.004, 0.005][i % 3]
        rows.append({"date": day, "value": nav, "fees": 1000})
    report = analytics.analyze_performance({"net_values": rows, "currency": "USD"})
    assert summary(report, "累计净收益") == pytest.approx(rows[-1]["value"] / rows[0]["value"] - 1)
    assert len(next(item for item in report["charts"] if item["key"] == "net_value")["data"]) == 34


def test_zero_variance_reports_null_ratios_not_infinity():
    pytest.importorskip("quantstats")
    report = analytics.analyze_performance({"currency": "CNY", "returns": [{"date": day, "return": 0} for day in dates(35)]})
    assert summary(report, "Sharpe") is None
    assert summary(report, "Sortino") is None
    assert summary(report, "累计净收益") == 0
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("change,match", [
    (lambda data: data.update(net_values=[]), "只提供"),
    (lambda data: data.update(currency=""), "币种"),
    (lambda data: data["returns"].pop(), None),
    (lambda data: data["returns"][0].update({"return": -1}), "大于 -1"),
    (lambda data: data["returns"][0].update({"return": float("nan")}), "NaN"),
    (lambda data: data["returns"][1].update(date=data["returns"][0]["date"]), "重复"),
    (lambda data: data["returns"][0].pop("benchmark_return"), "基准"),
    (lambda data: data["returns"][0].update(cash_flow=1000), "出入金"),
    (lambda data: data["returns"][0].update(currency="HKD"), "同一估值币种"),
])
def test_performance_rejects_financially_invalid_inputs(change, match, monkeypatch):
    data = performance_data()
    if match is None:
        # Sample-count boundary is 30, rather than an arbitrary full year.
        data["returns"] = data["returns"][:29]
        match = "30"
    else:
        change(data)
    with pytest.raises(ValueError, match=match):
        analytics.analyze_performance(data)


def test_weekly_data_is_not_silently_annualized_as_daily():
    rows = [{"date": (date(2025, 1, 1) + timedelta(days=i * 7)).isoformat(), "return": 0.01} for i in range(35)]
    with pytest.raises(ValueError, match="周/月"):
        analytics.analyze_performance({"currency": "CNY", "returns": rows})


def test_every_other_weekday_returns_or_prices_cannot_be_annualized_as_daily():
    # Median spacing is two days, so the old weekly-frequency heuristic misses
    # this sparse series. Both engines must check overall working-day coverage.
    sparse_days = dates(130)[::2]
    returns = [{"date": day, "return": [-.01, .008][i % 2]} for i, day in enumerate(sparse_days)]
    with pytest.raises(ValueError, match="覆盖不足"):
        analytics.analyze_performance({"currency": "CNY", "returns": returns})
    prices = [{"date": day, "symbol": "A", "close": 100 + i} for i, day in enumerate(sparse_days)]
    with pytest.raises(ValueError, match="覆盖不足"):
        analytics.analyze_portfolio({"currency": "CNY", "prices": prices, "weights": [{"symbol": "A", "weight": 1}]})


def test_holiday_gaps_remain_unfilled_with_actual_daily_compounding(monkeypatch):
    pytest.importorskip("quantstats")
    monkeypatch.setattr(analytics, "_quantstats_html", lambda *args: "native-report-tested-separately")
    all_days = dates(80)
    # Five declared holiday weekdays leave >80% coverage; legitimate closures
    # are retained as gaps rather than invented zero-return observations.
    used_days = [day for i, day in enumerate(all_days) if not 30 <= i < 35]
    rows = [{"date": day, "return": [-.01, .006, .003][i % 3]} for i, day in enumerate(used_days)]
    report = analytics.analyze_performance({"currency": "CNY", "returns": rows})
    assert summary(report, "有效收益观测") == 75
    assert summary(report, "累计净收益") == pytest.approx(math.prod(1 + row["return"] for row in rows) - 1)
    line = next(chart for chart in report["charts"] if chart["key"] == "net_value")
    assert [row["date"] for row in line["data"]] == used_days


def test_actual_riskfolio_contributions_covariance_and_daily_cvar():
    np = pytest.importorskip("numpy")
    pytest.importorskip("riskfolio")
    data = portfolio_data()
    report = analytics.analyze_portfolio(data)
    prices = {symbol: [row["close"] for row in data["prices"] if row["symbol"] == symbol] for symbol in ("A", "B")}
    r = np.array([[prices[symbol][i] / prices[symbol][i - 1] - 1 for symbol in ("A", "B")] for i in range(1, 65)])
    cov = np.cov(r, rowvar=False, ddof=1)
    w = np.array([0.6, 0.4])
    variance = w @ cov @ w
    expected_fractions = w * (cov @ w) / variance
    rows = table(report, "risk_contributions")["rows"]
    assert [row["risk_contribution"] for row in rows] == pytest.approx(expected_fractions, rel=1e-4)
    assert sum(row["risk_contribution"] for row in rows) == pytest.approx(1, rel=1e-4)
    assert summary(report, "年化波动率") == pytest.approx(math.sqrt(variance * 252))
    covariance_rows = table(report, "covariance")["rows"]
    assert covariance_rows[0]["A"] == pytest.approx(cov[0, 0])
    loss_returns = sorted(r @ w)
    alpha_n = 0.05 * len(loss_returns)
    cutoff = math.ceil(alpha_n) - 1
    expected_cvar = -loss_returns[cutoff] - sum(value - loss_returns[cutoff] for value in loss_returns[:cutoff + 1]) / alpha_n
    assert summary(report, "历史日 CVaR（95%）") == pytest.approx(expected_cvar)
    assert all(row["cluster"] is not None for row in rows)
    json.dumps(report, allow_nan=False)


def test_flat_prices_do_not_get_invented_optimizer_volatility():
    pytest.importorskip("riskfolio")
    data = portfolio_data()
    for row in data["prices"]:
        row["close"] = 10
    report = analytics.analyze_portfolio(data)
    assert summary(report, "年化波动率") == 0
    assert summary(report, "历史日 CVaR（95%）") == 0
    assert all(row["risk_contribution"] is None for row in table(report, "risk_contributions")["rows"])
    json.dumps(report, allow_nan=False)


def test_partial_dates_are_dropped_not_forward_filled():
    pytest.importorskip("riskfolio")
    data = portfolio_data(66)
    data["prices"] = [row for row in data["prices"] if not (row["symbol"] == "A" and row["date"] == dates(66)[10])]
    report = analytics.analyze_portfolio(data)
    assert summary(report, "共同价格日期") == 65
    assert any("未补价" in warning for warning in report["warnings"])


@pytest.mark.parametrize("change,match", [
    (lambda data: data["weights"][0].update(weight=60), "正权重"),
    (lambda data: data["weights"][0].update(weight=0.5), "总和"),
    (lambda data: data["weights"].append({"symbol": "A", "weight": 0.1}), "一个正权重"),
    (lambda data: data["prices"][0].update(close=0), "大于 0"),
    (lambda data: data["prices"].append(data["prices"][0]), "重复"),
    (lambda data: data["prices"][0].update(currency="USD"), "币种"),
    (lambda data: data["prices"][0].update(adjustment="raw"), "复权"),
    (lambda data: data.update(fx_normalized="true"), "布尔"),
])
def test_portfolio_input_guards(change, match):
    data = portfolio_data()
    change(data)
    with pytest.raises(ValueError, match=match):
        analytics.analyze_portfolio(data)


def test_minimum_common_dates_cannot_be_faked_by_longer_other_asset():
    data = portfolio_data(61)
    data["prices"] = [row for row in data["prices"] if not (row["symbol"] == "A" and row["date"] in dates(61)[:2])]
    with pytest.raises(ValueError, match="60 个共同"):
        analytics.analyze_portfolio(data)


def test_actual_financetoolkit_ratios_use_average_balances_and_same_period_peers(monkeypatch):
    ft = pytest.importorskip("financetoolkit")
    monkeypatch.setattr(ft.Toolkit, "__init__", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("No data-provider Toolkit may be created")))
    report = analytics.analyze_financials(financial_data())
    current = table(report, "latest_comparison")["rows"]
    assert len(current) == 1 and current[0]["symbol"] == "A"
    row = current[0]
    assert row["net_margin"] == pytest.approx(0.1)
    assert row["roe"] == pytest.approx(120 / 600)
    assert row["roa"] == pytest.approx(120 / 1100)
    assert row["free_cashflow"] == 110
    assert row["free_cashflow_margin"] == pytest.approx(110 / 1200)
    assert row["debt_to_assets"] == pytest.approx(0.2)
    assert row["current_ratio"] == pytest.approx(3)
    assert financial_rows(report, "2024")[0]["roe"] is None
    json.dumps(report, allow_nan=False)


def test_missing_financials_zero_denominators_and_loss_remain_unknown():
    pytest.importorskip("financetoolkit")
    data = financial_data()
    data["records"][1].update(revenue=0, current_liabilities=0, capex=None, net_income=-50, total_equity=-20)
    row = financial_rows(analytics.analyze_financials(data), "2025")[0]
    assert row["net_margin"] is None
    assert row["cash_quality"] is None
    assert row["current_ratio"] is None
    assert row["free_cashflow"] is None
    assert row["debt_to_equity"] is None


def test_nonconsecutive_reporting_periods_do_not_get_average_balance_roe():
    pytest.importorskip("financetoolkit")
    data = financial_data()
    data["records"][1]["period"] = "2026"
    row = financial_rows(analytics.analyze_financials(data), "2026")[0]
    assert row["roe"] is None and row["roa"] is None


def test_quarterly_metrics_are_single_period_not_four_times_annualized():
    pytest.importorskip("financetoolkit")
    data = financial_data()
    data["period_type"] = "quarterly"
    data["records"] = data["records"][:2]
    data["records"][0]["period"] = "2025Q1"
    data["records"][1]["period"] = "2025-06-30"
    report = analytics.analyze_financials(data)
    assert financial_rows(report, "2025Q2")[0]["roe"] == pytest.approx(0.2)
    assert any("未乘 4" in text for text in report["methodology"])


@pytest.mark.parametrize("change,match", [
    (lambda data: data.pop("period_type"), "明确选择"),
    (lambda data: data.update(flow_basis="ytd"), "累计"),
    (lambda data: data["records"][1].update(currency="HKD"), "历史币种"),
    (lambda data: data["records"][0].update(capex=-30), "资本支出"),
    (lambda data: data["records"].append(data["records"][0]), "重复"),
    (lambda data: data["records"][1].update(period_type="quarterly"), "混用"),
    (lambda data: data["records"][0].update(period_end="2024-06-30"), "冲突"),
])
def test_financial_period_currency_and_cashflow_conventions(change, match):
    data = financial_data()
    change(data)
    with pytest.raises(ValueError, match=match):
        analytics.analyze_financials(data)


@pytest.mark.parametrize("tool,options", [("performance", {"currency": "CNY"}), ("portfolio", {"currency": "CNY"}), ("financials", {"period_type": "annual"})])
def test_explicit_fictional_csv_template_roundtrips(tool, options, monkeypatch):
    pytest.importorskip({"performance": "quantstats", "portfolio": "riskfolio", "financials": "financetoolkit"}[tool])
    monkeypatch.setattr(analytics, "_quantstats_html", lambda *args: "tested-separately")
    raw = analytics.analytics_template(tool).encode("utf-8-sig")
    data = {"file_name": "template.csv", "content_base64": base64.b64encode(raw).decode(), **options}
    report = getattr(analytics, f"analyze_{tool}")(data)
    assert any("虚构演示" in warning for warning in report["warnings"])
    assert report["tables"] and report["charts"]
    json.dumps(report, allow_nan=False)


def test_gb18030_csv_keeps_financial_codes_and_missing_values():
    pytest.importorskip("financetoolkit")
    raw = "证券代码,报告期,币种,营业收入,净利润,经营现金流,资本开支\n000001,2025,人民币,1000,100,,30\n".encode("gb18030")
    report = analytics.analyze_financials({"file_name": "statement.csv", "content_base64": base64.b64encode(raw).decode(), "period_type": "annual"})
    row = report["tables"][0]["rows"][0]
    assert row["symbol"] == "000001" and row["net_margin"] == 0.1
    assert row["cash_quality"] is None and row["free_cashflow"] is None


def test_actual_fiscal_dates_are_retained_and_not_false_peer_comparisons():
    pytest.importorskip("financetoolkit")
    data = financial_data()
    data["period_type"] = "quarterly"
    data["records"][0].update(period="2025-03-29", period_start="2024-12-29", period_end="2025-03-29")
    data["records"][1].update(period="2025-06-28", period_start="2025-03-30", period_end="2025-06-28")
    data["records"][2].update(period="2025Q2")
    report = analytics.analyze_financials(data)
    assert financial_rows(report, "2025-06-28")[0]["roe"] == pytest.approx(0.2)
    assert len(financial_rows(report, "2025-06-28")) == 1
    assert len(financial_rows(report, "2025Q2")) == 1
    assert [row["symbol"] for row in table(report, "latest_comparison")["rows"]] == ["B"]
    assert summary(report, "最新同口径期间") == "2025Q2"
    assert report["sources"][0]["as_of"] == "2025-06-30"


def test_eight_fiscal_quarters_are_real_trends_with_currency_units_and_missing_gaps():
    pytest.importorskip("financetoolkit")
    ends = ["2024-03-30", "2024-06-29", "2024-09-28", "2024-12-28", "2025-03-29", "2025-06-28", "2025-09-27", "2025-12-27"]
    records = []
    for symbol, currency, scale in [("A", "USD", 1e9), ("B", "CNY", 1e4)]:
        for index, end in enumerate(ends):
            records.append({"symbol": symbol, "period": end, "period_end": end, "currency": currency,
                            "revenue": (100 + index) * scale, "net_income": (10 + index) * scale,
                            "operating_cashflow": 15 * scale, "capex": 5 * scale,
                            "total_assets": (200 + index) * scale, "total_equity": (100 + index) * scale,
                            "total_debt": 50 * scale, "current_assets": 40 * scale, "current_liabilities": 20 * scale})
    records[3].update(revenue=None, net_income=None)
    report = analytics.analyze_financials({"period_type": "quarterly", "records": list(reversed(records))})
    assert [item["key"] for item in report["tables"]] == ["latest_comparison", "financial_history"]
    latest = report["tables"][0]["rows"]
    assert {row["symbol"] for row in latest} == {"A", "B"}
    assert all(row["period_end"] == ends[-1] and row["period_type"] == "quarterly" for row in latest)
    history = table(report, "financial_history")
    assert len(history["rows"]) == 16 and history["collapsible"] and history["default_collapsed"]
    assert financial_rows(report, ends[0])[0]["revenue"] in {100e9, 1e6}
    charts = {chart["key"]: chart for chart in report["charts"]}
    amounts_a, amounts_b = charts["financial_amounts_A"], charts["financial_amounts_B"]
    assert amounts_a["unit"] == "亿 USD" and amounts_b["unit"] == "百万 CNY"
    assert amounts_a["data"][0]["revenue"] == 1000 and amounts_b["data"][0]["revenue"] == 1
    assert amounts_a["data"][3]["revenue"] is None and amounts_a["data"][3]["net_income"] is None
    for key in ("financial_amounts_A", "financial_ratios_A", "financial_amounts_B", "financial_ratios_B"):
        assert charts[key]["type"] == "line"
        assert [row["period_end"] for row in charts[key]["data"]] == ends
    ratios = charts["financial_ratios_A"]
    assert ratios["value_type"] == "percent" and ratios["data"][0]["roe"] is None
    assert ratios["data"][1]["roe"] == pytest.approx(11 / 100.5)
    assert ratios["data"][3]["roe"] is None and ratios["data"][3]["net_margin"] is None
    assert charts["latest_margins"]["value_type"] == "percent"
    assert charts["latest_margins"]["data"][0]["net_margin"] == pytest.approx(17 / 107)
    json.dumps(report, allow_nan=False)


def test_disclosure_restatement_warnings_are_brief_without_losing_audit_details():
    pytest.importorskip("financetoolkit")
    data = financial_data()
    originals = [f"A 2025-0{quarter + 1}-28 NetCashProvidedByUsedInOperatingActivities 使用本期申报时已公开的前期累计差分；来源为不同申报，请核对重述与会计政策" for quarter in range(4)]
    originals += ["A 2025-06-28 PaymentsToAcquirePropertyPlantAndEquipment 缺少本期申报时可核对的前期版本，未将晚期重述混入累计差分",
                  "A 部分指标缺失或不支持：operating_cashflow, capex；保持空值，不以负债总额代有息债务"]
    data["warnings"] = originals
    report = analytics.analyze_financials(data)
    assert sum("不同申报" in warning for warning in report["warnings"]) == 1
    assert any("重述" in warning and "会计政策" in warning for warning in report["warnings"])
    assert any("未混用后续重述" in warning for warning in report["warnings"])
    assert any("经营现金流, 资本支出" in warning for warning in report["warnings"])
    assert not any("NetCashProvided" in warning or "PaymentsToAcquire" in warning for warning in report["warnings"])
    assert all(any(original in detail for detail in report["methodology"]) for original in originals)


def test_live_source_disclosure_metadata_is_preserved():
    pytest.importorskip("financetoolkit")
    data = financial_data()
    data["sources"] = [{"name": "SEC companyfacts", "url": "https://www.sec.gov/edgar", "as_of": "2026-01-15", "coverage": "Filed facts only"}]
    data["warnings"] = ["Not a real-time quote"]
    data["methodology"] = ["CFO cumulative facts differenced within the same fiscal year"]
    report = analytics.analyze_financials(data)
    assert report["sources"][0] == data["sources"][0]
    assert data["warnings"][0] in report["warnings"]
    assert data["methodology"][0] in report["methodology"]


@pytest.mark.parametrize("data", [
    {"file_name": "bad.csv", "content_base64": "%%%", "currency": "CNY"},
    {"file_name": "huge.csv", "content_base64": "A" * (4 * ((analytics.MAX_BYTES + 2) // 3) + 1)},
    {"file_name": "binary.xlsx", "content_base64": base64.b64encode(b"binary").decode()},
    {"records": [{"symbol": "A", "period": "2025", "currency": "CNY", "revenue": float("inf")}], "period_type": "annual"},
])
def test_file_and_json_validation_is_bounded_and_actionable(data):
    with pytest.raises(ValueError):
        analytics.analyze_financials(data)


def test_no_input_has_no_automatic_success_or_demo():
    for function in (analytics.analyze_performance, analytics.analyze_portfolio, analytics.analyze_financials):
        with pytest.raises(ValueError):
            function({})


def test_third_party_analytics_never_downloads_or_opens_browser(monkeypatch):
    requests = pytest.importorskip("requests")
    qs = pytest.importorskip("quantstats")
    pytest.importorskip("riskfolio")
    pytest.importorskip("financetoolkit")
    import webbrowser
    def forbidden(*args, **kwargs):
        raise AssertionError("analytics may not call external providers or open a browser")
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(qs.utils, "download_returns", forbidden)
    monkeypatch.setattr(webbrowser, "open", forbidden)
    # Native HTML still runs; an output path avoids QuantStats' browser branch.
    data = performance_data()
    data["name"] = '<img src=x onerror="alert(1)">'
    report = analytics.analyze_performance(data)
    assert '<img src=x onerror="alert(1)">' not in report["report_html"]
    assert "&lt;img" in report["report_html"]
    assert analytics.analyze_portfolio(portfolio_data())["engine"] == "Riskfolio-Lib"
    assert analytics.analyze_financials(financial_data())["engine"] == "FinanceToolkit"


def test_invalid_thousands_format_is_not_silently_changed():
    data = financial_data()
    data["records"][0]["revenue"] = "1,2"
    with pytest.raises(ValueError, match="千位"):
        analytics.analyze_financials(data)


def test_oversize_json_records_do_not_start_third_party_work():
    data = financial_data()
    data["records"] = data["records"] * 1668
    with pytest.raises(ValueError, match="5000|2 MiB"):
        analytics.analyze_financials(data)
