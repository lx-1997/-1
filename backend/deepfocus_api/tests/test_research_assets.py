"""Finance/data boundaries, including actual EdgarTools XML parsing offline."""
import base64
import json
import math
from datetime import date
from types import SimpleNamespace

import pytest

from deepfocus_api import research_assets as assets


def table(report, key):
    return next(item["rows"] for item in report["tables"] if item["key"] == key)


def summary(report, label):
    return next(item["value"] for item in report["summary"] if item["label"] == label)


def uploaded(text, name="records.csv", **options):
    return {"file_name": name, "content_base64": base64.b64encode(text.encode()).decode(), **options}


def holding(fund="F1", symbol="AAA", weight=0.3, as_of="2025-06-30", **kwargs):
    return {"fund": fund, "symbol": symbol, "weight": weight, "as_of": as_of, **kwargs}


def bond(**kwargs):
    return {"symbol": "110001", "price": 110, "conversion_price": 10,
            "stock_price": 12, "as_of": "2025-01-01", **kwargs}


def institutional(cusip="037833100", **kwargs):
    return {"cusip": cusip, "name": "Issuer", "shares": 10, "value": 1200,
            "report_date": "2025-06-30", "filing_date": "2025-08-10", **kwargs}


def test_fund_partial_not_renormalized_and_direct_combined():
    report = assets.analyze_funds({"holdings": [holding(), holding(symbol="BBB", weight=0.2)],
                                  "positions": [{"symbol": "F1", "weight": 0.6}],
                                  "direct_positions": [{"symbol": "AAA", "weight": 0.1}]})
    rows = {row["symbol"]: row for row in table(report, "lookthrough")}
    assert rows["AAA"]["weight"] == pytest.approx(0.28)
    assert rows["BBB"]["weight"] == pytest.approx(0.12)
    assert summary(report, "未知/未分配权重") == pytest.approx(0.6)
    assert table(report, "coverage")[0]["coverage"] == 0.5
    assert sum(row["weight"] for row in rows.values()) + summary(report, "未知/未分配权重") == pytest.approx(1)


def test_fund_overlap_is_partial_lower_bound():
    report = assets.analyze_funds({"holdings": [holding(weight=0.3), holding("F2", weight=0.2),
                                                    holding("F2", "BBB", weight=0.1)]})
    assert table(report, "overlap")[0]["overlap"] == pytest.approx(0.2)
    assert any("等权" in warning for warning in report["warnings"])


def test_fund_date_mismatch_is_not_computed():
    report = assets.analyze_funds({"holdings": [holding(), holding("F2", as_of="2025-03-31")]})
    assert table(report, "overlap")[0]["overlap"] is None
    assert any("不计算重合" in warning for warning in report["warnings"])


def test_fund_unknown_fund_is_unknown_exposure():
    report = assets.analyze_funds({"holdings": [holding(weight=1)],
                                  "positions": [{"symbol": "NO_DATA", "weight": 1}]})
    assert summary(report, "未知/未分配权重") == 1
    assert table(report, "lookthrough") == []


@pytest.mark.parametrize("records", [
    [holding(), holding()],
    [holding(weight=0.7), holding(symbol="BBB", weight=0.4)],
    [holding(), holding(symbol="BBB", as_of="2025-03-31")],
    [holding(weight=30)],
    [holding(weight=-0.1)],
])
def test_invalid_fund_holdings_rejected(records):
    with pytest.raises(ValueError):
        assets.analyze_funds({"holdings": records})


def test_fund_duplicate_security_components_aggregate():
    report = assets.analyze_funds({"holdings": [holding(weight=0.2), holding(weight=0.3)]})
    assert table(report, "lookthrough")[0]["weight"] == pytest.approx(0.5)


def test_fund_allocations_cannot_exceed_one():
    with pytest.raises(ValueError, match="总权重"):
        assets.analyze_funds({"holdings": [holding()], "positions": [{"symbol": "F1", "weight": 1}],
                              "direct_positions": [{"symbol": "A", "weight": 0.1}]})


def test_bond_double_low_and_missing_ytm_terms():
    report = assets.analyze_bonds({"records": [bond()]})
    row = table(report, "screen")[0]
    assert row["conversion_value"] == 120
    assert row["premium"] == pytest.approx(110 / 120 - 1)
    assert row["double_low"] == pytest.approx(110 + (110 / 120 - 1) * 100)
    assert row["ytm"] is None
    assert "未核验" in row["risk_flags"]


def test_bond_never_invents_face_value_ytm():
    report = assets.analyze_bonds({"records": [bond(years_to_maturity=2)], "approximate_ytm": True})
    assert table(report, "screen")[0]["ytm"] is None


def test_bond_approximation_must_be_explicit_and_marked():
    record = bond(years_to_maturity=2, redemption_price=120, redemption_status="none")
    assert table(assets.analyze_bonds({"records": [record]}), "screen")[0]["ytm"] is None
    row = table(assets.analyze_bonds({"records": [record], "approximate_ytm": True}), "screen")[0]
    assert row["ytm"] == pytest.approx(math.sqrt(120 / 110) - 1)
    assert "简化" in row["ytm_method"]


def test_bond_cashflow_discounting_is_gross_actual_365():
    report = assets.analyze_bonds({"records": [bond(price=100, redemption_status="none",
        cashflows=[{"date": "2026-01-01", "amount": 5}, {"date": "2027-01-01", "amount": 105}])]})
    assert table(report, "screen")[0]["ytm"] == pytest.approx(0.05, abs=1e-10)


def test_bond_redemption_invalidates_maturity_ytm_and_filters():
    report = assets.analyze_bonds({"records": [bond(redemption_status="announced", redemption_date="2025-02-01",
                     years_to_maturity=2, redemption_price=120)], "approximate_ytm": True})
    assert table(report, "screen") == []
    row = table(report, "excluded")[0]
    assert row["ytm"] is None
    assert "强赎" in row["filter_reason"]


def test_bond_unknown_liquidity_fails_min_volume_filter():
    report = assets.analyze_bonds({"records": [bond()], "filters": {"min_volume": 100}})
    assert len(table(report, "excluded")) == 1


def test_bond_maturity_zero_excluded():
    report = assets.analyze_bonds({"records": [bond(years_to_maturity=0)]})
    assert "已到期" in table(report, "excluded")[0]["filter_reason"]


@pytest.mark.parametrize("change", [{"conversion_price": 0}, {"stock_price": -1}, {"price": True},
                                     {"as_of": "2025-02-30"}, {"redemption_status": "SAFE"},
                                     {"cashflows": [{"date": "2024-01-01", "amount": 100}]}])
def test_bond_bad_data_rejected(change):
    with pytest.raises(ValueError):
        assets.analyze_bonds({"records": [bond(**change)]})


def test_13f_aggregate_managers_preserve_option_and_class():
    report = assets.analyze_institutions({"form": "13F-HR", "value_unit": "USD", "records": [
        institutional(), institutional(shares=20, value=2400), institutional(put_call="CALL"),
        institutional(cusip="037833200") ]})
    rows = table(report, "holdings")
    assert len(rows) == 3
    assert next(row for row in rows if row["identifier"] == "037833100" and row["put_call"] == "NONE")["shares"] == 30
    assert summary(report, "披露证券总价值（非 NAV）") == 6000


def test_13f_missing_prior_never_zero_or_fake_buy():
    report = assets.analyze_institutions({"form": "13F-HR", "value_unit": "USD", "compare_previous": True,
                                         "records": [institutional()]})
    row = table(report, "holdings")[0]
    assert row["previous_shares"] is None and row["share_change"] is None
    assert any("不能视为零" in warning for warning in report["warnings"])


def test_13f_compares_only_matched_securities_and_retains_prior_only():
    previous = [institutional(shares=8, report_date="2025-03-31", filing_date="2025-05-10"),
                institutional(cusip="037833200", report_date="2025-03-31", filing_date="2025-05-10")]
    report = assets.analyze_institutions({"form": "13F-HR", "value_unit": "USD", "records": [institutional()],
                                         "previous_records": previous})
    assert table(report, "holdings")[0]["share_change"] == 2
    assert "不能推定清仓" in table(report, "prior_unmatched")[0]["comparison"]


def test_13f_missing_values_do_not_create_complete_weights():
    report = assets.analyze_institutions({"form": "13F-HR", "value_unit": "USD", "records": [
        institutional(), institutional(cusip="037833200", value=None)]})
    assert summary(report, "披露证券总价值（非 NAV）") is None
    assert all(row["weight"] is None for row in table(report, "holdings"))


def test_13f_explicit_thousands_conversion_once():
    report = assets.analyze_institutions({"form": "13F-HR", "value_unit": "USD_thousands", "records": [institutional(value=3)]})
    assert table(report, "holdings")[0]["value"] == 3000


def test_disclosure_file_level_dates_only_supplement_missing_rows():
    report = assets.analyze_institutions(uploaded("cusip,shares,value\n037833100,10,1200\n", form="13F-HR",
        value_unit="USD", report_date="2025-06-30", filing_date="2025-08-10"))
    row = table(report, "holdings")[0]
    assert row["report_date"] == "2025-06-30" and row["filing_date"] == "2025-08-10"
    report = assets.analyze_institutions({"form": "13F-HR", "value_unit": "USD", "records": [institutional()],
        "report_date": "2025-03-31", "filing_date": "2025-05-10"})
    assert table(report, "holdings")[0]["report_date"] == "2025-06-30"
    assert table(report, "holdings")[0]["filing_date"] == "2025-08-10"


@pytest.mark.parametrize("options", [
    {"value_unit": None}, {"records": [institutional(report_date="2025-01-01"), institutional()]},
    {"records": [institutional(put_call="MIXED")]}, {"records": [institutional(cusip="AAPL")]},
    {"previous_records": [institutional()]},
])
def test_13f_invalid_units_dates_or_identifiers_rejected(options):
    data = {"form": "13F-HR", "value_unit": "USD", "records": [institutional()], **options}
    with pytest.raises(ValueError):
        assets.analyze_institutions(data)


def test_form4_no_mixing_grants_derivatives_with_ps():
    base = {"symbol": "AAPL", "transaction_date": "2025-01-01", "shares": 10, "price": 5,
            "acquired_disposed": "A"}
    report = assets.analyze_institutions({"form": "4", "records": [
        {**base, "transaction_code": "P"}, {**base, "transaction_code": "A"},
        {**base, "transaction_code": "M", "derivative": True},
        {**base, "transaction_code": "S", "acquired_disposed": "D", "price": None}]})
    assert summary(report, "非衍生品 P 买入金额") == 50
    assert summary(report, "非衍生品 S 卖出金额") is None
    assert summary(report, "衍生品记录") == 1


def test_live_identity_required_without_fallback(monkeypatch):
    monkeypatch.delenv("DEEPFOCUS_SEC_IDENTITY", raising=False)
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(RuntimeError, match="联系身份"):
        assets.analyze_institutions({"form": "13F-HR", "cik": "1067983"})


def test_live_disaggregated_value_already_dollars(monkeypatch):
    import pandas as pd
    from edgar.thirteenf import Ambiguous13FValueUnitWarning  # actual dependency capability
    assert issubclass(Ambiguous13FValueUnitWarning, Warning)
    parsed = SimpleNamespace(infotable=pd.DataFrame([{"Cusip": "037833100", "Issuer": "Issuer", "SharesPrnAmount": 10,
        "Value": 1200, "Type": "Shares", "PutCall": "", "Class": "COM"}]),
        report_period="2025-06-30", filing=SimpleNamespace(filing_date="2025-08-10"),
        value_unit_resolution=SimpleNamespace(ambiguous=False, source="schema"))
    rows = assets._live_13f(parsed)
    assert rows[0]["value"] == 1200


FORM4_XML = '''<?xml version="1.0"?><ownershipDocument>
<schemaVersion>X0306</schemaVersion><documentType>4</documentType><periodOfReport>2025-01-02</periodOfReport>
<issuer><issuerCik>0000320193</issuerCik><issuerName>Issuer</issuerName><issuerTradingSymbol>AAPL</issuerTradingSymbol></issuer>
<reportingOwner><reportingOwnerId><rptOwnerCik>0000000001</rptOwnerCik><rptOwnerName>Public Test Owner</rptOwnerName></reportingOwnerId>
<reportingOwnerAddress><rptOwnerStreet1>1 Test Street</rptOwnerStreet1><rptOwnerCity>Test</rptOwnerCity><rptOwnerState>CA</rptOwnerState><rptOwnerZipCode>12345</rptOwnerZipCode></reportingOwnerAddress>
<reportingOwnerRelationship><isDirector>1</isDirector><isOfficer>0</isOfficer><isTenPercentOwner>0</isTenPercentOwner><isOther>0</isOther></reportingOwnerRelationship></reportingOwner>
<nonDerivativeTable><nonDerivativeTransaction><securityTitle><value>Common Stock</value></securityTitle>
<transactionDate><value>2025-01-02</value></transactionDate><transactionCoding><transactionFormType>4</transactionFormType><transactionCode>P</transactionCode><equitySwapInvolved>0</equitySwapInvolved></transactionCoding>
<transactionAmounts><transactionShares><value>10</value></transactionShares><transactionPricePerShare><value>5</value></transactionPricePerShare><transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts>
<postTransactionAmounts><sharesOwnedFollowingTransaction><value>20</value></sharesOwnedFollowingTransaction></postTransactionAmounts><ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature>
</nonDerivativeTransaction></nonDerivativeTable><derivativeTable></derivativeTable><footnotes></footnotes>
<ownerSignature><signatureName>Public Test Owner</signatureName><signatureDate>2025-01-03</signatureDate></ownerSignature></ownershipDocument>'''


def test_real_edgartools_form4_xml_import():
    report = assets.analyze_institutions(uploaded(FORM4_XML, "form4.xml", form="4", filing_date="2025-01-03"))
    assert "EdgarTools" in report["engine"]
    assert "未执行在线查询" in report["sources"][0]["name"]
    assert summary(report, "非衍生品 P 买入金额") == 50


def test_13f_xml_raw_units_dates_and_namespace_preserved():
    xml = '''<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
    <infoTable><nameOfIssuer>Issuer</nameOfIssuer><titleOfClass>COM</titleOfClass><cusip>037833100</cusip><value>3</value>
    <shrsOrPrnAmt><sshPrnamt>10</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt></infoTable></informationTable>'''
    report = assets.analyze_institutions(uploaded(xml, "info.xml", form="13F-HR", value_unit="USD_thousands",
        report_date="2022-12-31", filing_date="2023-02-10"))
    assert table(report, "holdings")[0]["value"] == 3000
    assert table(report, "holdings")[0]["report_date"] == "2022-12-31"


def test_xml_entities_rejected_before_library():
    with pytest.raises(ValueError, match="实体"):
        assets.analyze_institutions(uploaded('<!DOCTYPE x [<!ENTITY a SYSTEM "file:///private">]><ownershipDocument/>',
            "bad.xml", form="4"))


def test_csv_gb18030_and_strict_header_shape():
    text = "fund,symbol,weight,as_of,name\n基金,000001,0.2,2025-06-30,中文名\n"
    data = {"file_name": "fund.csv", "content_base64": base64.b64encode(text.encode("gb18030")).decode()}
    assert table(assets.analyze_funds(data), "lookthrough")[0]["name"] == "中文名"
    with pytest.raises(ValueError, match="列数"):
        assets.analyze_funds(uploaded("fund,symbol,weight,as_of\nF,A,0.2,2025-06-30,EXTRA\n"))
    with pytest.raises(ValueError, match="重复"):
        assets.analyze_funds(uploaded("fund,symbol,weight, weight,as_of\nF,A,0.2,0.9,2025-06-30\n"))


def test_input_nonfinite_and_size_limits():
    with pytest.raises(ValueError):
        assets.analyze_bonds({"records": [bond(price=float("nan"))]})
    with pytest.raises(ValueError, match="5000"):
        assets.analyze_funds({"holdings": [holding()] * 5001})
    with pytest.raises(ValueError, match="2 MiB"):
        assets.analyze_funds({"file_name": "f.csv", "content_base64": "x" * 3000000})


def test_all_reports_json_finite_and_have_contract_fields():
    for report in [assets.analyze_funds({"holdings": [holding()]}), assets.analyze_bonds({"records": [bond()]}),
                   assets.analyze_institutions({"form": "13F-HR", "value_unit": "USD", "records": [institutional()]})]:
        json.dumps(report, allow_nan=False)
        assert {"tool", "title", "engine", "summary", "tables", "charts", "warnings", "methodology", "sources"} <= report.keys()


def fact(concept, value, end, start=None, **kwargs):
    return SimpleNamespace(concept=concept, numeric_value=value, unit="USD", taxonomy="us-gaap", dimensions=None,
        form_type="10-Q", period_start=date.fromisoformat(start) if start else None,
        period_end=date.fromisoformat(end), filing_date=date(2025, 8, 1), accession="0000000001-25-000001", **kwargs)


def test_sec_financial_ytd_cashflow_is_proved_same_concept_difference():
    facts = [fact("Revenues", 100, "2025-03-31", "2025-01-01"),
             fact("Revenues", 200, "2025-06-30", "2025-04-01"),
             fact("NetCashProvidedByUsedInOperatingActivities", 100, "2025-03-31", "2025-01-01"),
             fact("NetCashProvidedByUsedInOperatingActivities", 260, "2025-06-30", "2025-01-01"),
             fact("PaymentsToAcquirePropertyPlantAndEquipment", 10, "2025-03-31", "2025-01-01"),
             fact("PaymentsToAcquirePropertyPlantAndEquipment", 50, "2025-06-30", "2025-01-01"),
             fact("Assets", 1000, "2025-06-30"), fact("DebtCurrent", 20, "2025-06-30"),
             fact("LongTermDebtNoncurrent", 80, "2025-06-30")]
    rows, notes = assets._sec_financial_rows(facts, "TEST", 8)
    assert len(rows) == 2
    assert rows[-1]["operating_cashflow"] == 160
    assert rows[-1]["capex"] == 40
    assert rows[-1]["total_debt"] == 100
    assert "累计差分" in rows[-1]["provenance"]["operating_cashflow"]["method"]
    assert any("未补造" in warning for warning in notes)


def test_sec_financial_missing_predecessor_is_not_ytd_quarter():
    rows, _ = assets._sec_financial_rows([
        fact("Revenues", 200, "2025-06-30", "2025-04-01"),
        fact("NetCashProvidedByUsedInOperatingActivities", 260, "2025-06-30", "2025-01-01"),
        fact("Liabilities", 100, "2025-06-30")], "TEST", 8)
    assert rows[0]["operating_cashflow"] is None
    assert rows[0]["total_debt"] is None


def test_sec_financial_real_fiscal_dates_and_no_zero_for_missing():
    rows, _ = assets._sec_financial_rows([fact("Revenues", 200, "2025-06-28", "2025-03-30")], "TEST", 8)
    assert rows[0]["period"] == "2025-06-28"
    assert rows[0]["period_start"] == "2025-03-30"
    assert rows[0]["capex"] is None


def test_sec_financial_q4_annual_minus_three_quarters():
    rows, _ = assets._sec_financial_rows([
        fact("Revenues", 200, "2025-09-30", "2025-07-01"),
        fact("Revenues", 600, "2025-09-30", "2025-01-01"),
        fact("Revenues", 900, "2025-12-31", "2025-01-01")], "TEST", 8)
    assert rows[-1]["revenue"] == 300
    assert rows[-1]["period_start"] == "2025-10-01"


def test_sec_financial_negative_capex_not_mislabeled_positive_expense():
    rows, notes = assets._sec_financial_rows([fact("Revenues", 200, "2025-06-30", "2025-04-01"),
            fact("PaymentsToAcquirePropertyPlantAndEquipment", -5, "2025-06-30", "2025-04-01")], "TEST", 1)
    assert rows[0]["capex"] is None
    assert any("净回款" in warning for warning in notes)


def test_sec_financial_later_prior_restatement_cannot_corrupt_ytd_difference():
    q1 = fact("NetCashProvidedByUsedInOperatingActivities", 100, "2024-03-31", "2024-01-01")
    q1.filing_date = date(2024, 4, 20)
    q1.accession = "Q1-original"
    q2 = fact("NetCashProvidedByUsedInOperatingActivities", 300, "2024-06-30", "2024-01-01")
    q2.filing_date = date(2024, 7, 20)
    q2.accession = "Q2-original"
    late_q1 = fact("NetCashProvidedByUsedInOperatingActivities", 1000, "2024-03-31", "2024-01-01")
    late_q1.filing_date = date(2025, 4, 20)
    late_q1.accession = "Q1-restated-later"
    rows, _ = assets._sec_financial_rows([fact("Revenues", 200, "2024-06-30", "2024-04-01"), q1, q2, late_q1], "TEST", 1)
    assert rows[0]["operating_cashflow"] == 200
    assert rows[0]["provenance"]["operating_cashflow"]["subtract_accession"] == "Q1-original"
    rows, notes = assets._sec_financial_rows([fact("Revenues", 200, "2024-06-30", "2024-04-01"), q2, late_q1], "TEST", 1)
    assert rows[0]["operating_cashflow"] is None
    assert any("晚期重述" in note for note in notes)
