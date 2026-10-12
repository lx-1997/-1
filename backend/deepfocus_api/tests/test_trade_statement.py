"""Offline accounting tests; no app, database, or provider is required."""
import csv
import io
import zipfile

import pytest

from deepfocus_api.trade_statement import (
    MAX_FILE_BYTES,
    StatementError,
    analyze_statement,
    statement_template,
)


HEADERS = ["date", "time", "symbol", "name", "side", "quantity", "price", "gross_amount", "fees", "net_amount", "currency", "share_balance", "trade_id", "order_id"]


def content(records, *, headers=HEADERS, encoding="utf-8", delimiter=","):
    stream = io.StringIO()
    writer = csv.writer(stream, delimiter=delimiter)
    writer.writerow(headers)
    for record in records:
        writer.writerow([record.get(header, "") for header in headers])
    return stream.getvalue().encode(encoding)


def trade(side="buy", quantity=100, price=10, **updates):
    result = {"date": "2026-01-05", "time": "10:00:00", "symbol": "000001", "name": "测试股票", "side": side,
              "quantity": quantity, "price": price, "fees": 0, "currency": "CNY"}
    result.update(updates)
    return result


def group(result, currency="CNY"):
    return next(item for item in result["currencies"] if item["currency"] == currency)


def test_fifo_partial_lots_and_fees_allocated_once():
    records = [trade(fees=5, trade_id=1), trade(price=11, date="2026-01-06", fees=5, trade_id=2),
               trade("sell", 150, 12, date="2026-02-01", fees=6, trade_id=3),
               trade("sell", 50, 13, date="2026-02-02", fees=4, trade_id=4)]
    result = analyze_statement(content(records), "trades.csv")
    summary = group(result)
    assert summary["buy_amount"] == 2110
    assert summary["sell_amount"] == 2440
    assert summary["realized_pnl"] == 330
    assert summary["fees"] == 20
    assert summary["open_quantity"] == 0
    assert summary["closed_trades"] == 2
    assert summary["winning_trades"] == 2
    assert summary["win_rate"] == 100
    assert summary["monthly"] == [{"month": "2026-01", "fees": 10, "realized_pnl": 0},
                                   {"month": "2026-02", "fees": 10, "realized_pnl": 330}]


def test_partial_sell_retains_proportional_buy_fee_cost():
    records = [trade(fees=3), trade("sell", 40, 12, date="2026-01-06", fees=2)]
    summary = group(analyze_statement(content(records), "trades.csv"))
    assert summary["realized_pnl"] == 76.8  # 478 - 1003 * 40 / 100
    assert summary["open_quantity"] == 60
    assert summary["symbols"][0]["open_quantity"] == 60


def test_net_settlement_is_authoritative_and_not_double_charged():
    records = [trade(gross_amount=1000, fees=5, net_amount=-1007),
               trade("sell", price=12, date="2026-01-06", gross_amount=1200, fees=5, net_amount=1190)]
    result = analyze_statement(content(records), "trades.csv")
    assert group(result)["realized_pnl"] == 183
    assert group(result)["fees"] == 10
    assert result["trades"][0]["net_amount"] == 1007
    assert any(issue["code"] == "net_amount_authoritative" for issue in result["issues"])


def test_opening_balance_unknown_cost_excludes_only_its_portion():
    records = [trade(fees=5, share_balance=150),
               trade("sell", 75, 12, date="2026-01-06", fees=3, share_balance=75)]
    result = analyze_statement(content(records), "trades.csv")
    summary = group(result)
    assert summary["unknown_cost_quantity"] == 50
    assert summary["realized_pnl"] == 47.75  # 897 * 25/75 - 1005 * 25/100
    assert summary["open_quantity"] == 75
    assert any(issue["code"] == "opening_cost_unknown" for issue in result["issues"])


def test_unmatched_sale_has_no_profit_or_win_rate():
    result = analyze_statement(content([trade("sell", 40, 12)]), "trades.csv")
    summary = group(result)
    assert summary["realized_pnl"] == 0
    assert summary["closed_trades"] == 0
    assert summary["unknown_cost_quantity"] == 40
    assert summary["win_rate"] is None


def test_currencies_never_share_lots_or_aggregate_pnl():
    records = [trade(currency="CNY"), trade("sell", price=12, date="2026-01-06", currency="USD")]
    result = analyze_statement(content(records), "mixed.csv", settlement_currency="CNY")
    assert len(result["currencies"]) == 2
    assert group(result, "CNY")["open_quantity"] == 100
    assert group(result, "USD")["unknown_cost_quantity"] == 100
    assert "realized_pnl" not in result


def test_hk_connect_requires_explicit_settlement_currency():
    records = [trade(side="港股通买入", symbol="00700", currency="港币")]
    with pytest.raises(StatementError, match="港股通"):
        analyze_statement(content(records), "connect.csv")
    result = analyze_statement(content(records), "connect.csv", settlement_currency="人民币")
    assert result["trades"][0]["currency"] == "CNY"
    assert result["trades"][0]["symbol"] == "00700"
    assert group(result)["buy_amount"] == 1000
    assert any("没有乘汇率" in warning for warning in result["warnings"])


def test_override_changes_missing_currency_but_preserves_normal_currency():
    records = [trade(currency=""), trade(symbol="AAPL", currency="USD", trade_id=2),
               trade(symbol="00700", currency="HKD", trade_id=3)]
    result = analyze_statement(content(records), "mixed.csv", settlement_currency="HKD")
    assert [item["currency"] for item in result["trades"]] == ["HKD", "USD", "HKD"]
    assert any("用户指定的 HKD" in warning for warning in result["warnings"])


def test_duplicate_full_source_record_only():
    buy = trade(trade_id=1, order_id="ORDER-1")
    independent = trade(trade_id=2, order_id="ORDER-1")
    result = analyze_statement(content([buy, buy, independent]), "duplicate.csv")
    assert result["total_rows"] == 3
    assert result["trade_count"] == 2
    assert result["skipped_rows"] == 1
    assert group(result)["open_quantity"] == 200


def test_duplicate_order_id_without_trade_id_requires_full_record_match():
    records = [trade(order_id="A"), trade(order_id="A"), trade(order_id="A", quantity=50)]
    result = analyze_statement(content(records), "orders.csv")
    assert result["trade_count"] == 2
    assert group(result)["open_quantity"] == 150


def test_identical_rows_without_source_id_are_independent_fills():
    result = analyze_statement(content([trade(), trade()]), "fills.csv")
    assert result["trade_count"] == 2
    assert result["skipped_rows"] == 0


def test_reverse_order_sorted_by_date_time_and_source_sequence():
    records = [trade("sell", price=12, trade_id=2), trade(trade_id=1)]
    result = analyze_statement(content(records), "reverse.csv")
    assert [item["side"] for item in result["trades"]] == ["buy", "sell"]
    assert group(result)["realized_pnl"] == 200


def test_same_day_times_sorted_before_source_sequence():
    records = [trade("sell", price=12, time="14:00", trade_id=1), trade(time="09:30", trade_id=2)]
    result = analyze_statement(content(records), "reverse.csv")
    assert group(result)["realized_pnl"] == 200


def test_negative_sell_quantity_is_export_sign_not_short_position():
    result = analyze_statement(content([trade(), trade("sell", -100, 12, date="2026-01-06")]), "signed.csv")
    assert result["trades"][1]["quantity"] == 100
    assert group(result)["realized_pnl"] == 200


def test_signed_broker_amounts_use_absolute_values_with_explicit_sides():
    records = [trade(quantity=-100, gross_amount=-1000, net_amount=-1005, fees=5),
               trade("sell", -100, 12, date="2026-01-06", gross_amount=-1200, net_amount=-1195, fees=5)]
    result = analyze_statement(content(records), "signed-amounts.csv")
    assert group(result)["realized_pnl"] == 190
    assert group(result)["buy_amount"] == 1005
    assert group(result)["sell_amount"] == 1195
    assert result["trades"][0]["quantity"] == 100
    assert any("方向已明确" in warning for warning in result["warnings"])


def test_same_symbol_same_currency_different_markets_never_share_costs():
    records = [trade(symbol="ABC.US"), trade("sell", symbol="ABC.HK", price=12, date="2026-01-06")]
    result = analyze_statement(content(records), "cross-market.csv")
    summary = group(result)
    assert summary["realized_pnl"] == 0
    assert summary["unknown_cost_quantity"] == 100
    assert len(summary["symbols"]) == 2
    assert {item["market"] for item in summary["symbols"]} == {"US", "HK"}
    assert {item["market"] for item in result["trades"]} == {"US", "HK"}


def test_explicit_market_column_is_part_of_security_identity():
    records = [trade(symbol="600000", market="CN"),
               trade("sell", symbol="600000", market="HK", price=12, date="2026-01-06")]
    result = analyze_statement(content(records, headers=HEADERS + ["market"]), "markets.csv")
    assert group(result)["realized_pnl"] == 0
    assert group(result)["unknown_cost_quantity"] == 100


def test_conflicting_market_and_suffix_is_rejected():
    records = [trade(symbol="00700.HK", market="CN")]
    with pytest.raises(StatementError, match="冲突"):
        analyze_statement(content(records, headers=HEADERS + ["market"]), "conflict.csv")


@pytest.mark.parametrize("buy_symbol,sell_symbol,currency", [("000001.SZ", "000001", "CNY"),
                                                          ("600000.SH", "sh600000", "CNY"),
                                                          ("700.HK", "00700", "HKD"),
                                                          ("AAPL.US", "AAPL", "USD")])
def test_exchange_suffixes_and_prefixes_match_same_security(buy_symbol, sell_symbol, currency):
    records = [trade(symbol=buy_symbol, currency=currency),
               trade("sell", price=12, symbol=sell_symbol, date="2026-01-06", currency=currency)]
    summary = group(analyze_statement(content(records), "symbols.csv"), currency)
    assert summary["realized_pnl"] == 200
    assert summary["open_quantity"] == 0


def test_balance_mismatch_excludes_current_sale_not_only_future_sales():
    records = [trade(share_balance=100),
               trade("sell", price=20, date="2026-01-06", share_balance=100)]
    result = analyze_statement(content(records), "balance.csv")
    assert group(result)["realized_pnl"] == 0
    assert group(result)["unknown_cost_quantity"] == 100
    assert group(result)["open_quantity"] == 100
    assert any(issue["code"] == "balance_mismatch" for issue in result["issues"])


def test_invalid_row_keeps_source_sequence_when_export_is_reversed():
    records = [trade("sell", price=12, trade_id=3),
               trade(quantity="broken", price=5, trade_id=2), trade(trade_id=1)]
    result = analyze_statement(content(records), "reverse-invalid.csv")
    assert group(result)["realized_pnl"] == 0
    assert group(result)["unknown_cost_quantity"] == 100


def test_ambiguous_same_day_order_never_reports_fabricated_profit():
    records = [trade(price=5, time=""), trade("sell", price=10, time=""), trade(price=15, time="")]
    result = analyze_statement(content(records), "unknown-order.csv")
    assert group(result)["realized_pnl"] == 0
    assert group(result)["closed_trades"] == 0
    assert group(result)["unknown_cost_quantity"] == 100
    assert any(issue["code"] == "same_day_order_uncertain" for issue in result["issues"])


def test_ambiguous_order_recovery_requires_later_zero_balance():
    records = [trade(price=5, time="", share_balance=100), trade("sell", price=10, time="", share_balance=0),
               trade(price=15, time="", share_balance=100),
               trade("sell", price=20, date="2026-01-06", share_balance=0),
               trade(date="2026-01-07", share_balance=100),
               trade("sell", price=12, date="2026-01-08", share_balance=0)]
    summary = group(analyze_statement(content(records), "order-recovery.csv"))
    assert summary["realized_pnl"] == 200
    assert summary["closed_trades"] == 1
    assert summary["unknown_cost_quantity"] == 200


def test_missing_currency_requires_user_choice():
    with pytest.raises(StatementError, match="缺少结算币种"):
        analyze_statement(content([trade(symbol="AAPL", currency="")]), "no-currency.csv")


def test_generic_chinese_commission_plus_taxes_and_transfer_fees():
    raw = ("日期,证券代码,买卖方向,数量,价格,成交金额,手续费,印花税,过户费,币种\n"
           "2026-01-05,000001,买入,100,10,1000,5,0,1,CNY\n"
           "2026-01-06,000001,卖出,100,12,1200,5,1,1,CNY\n")
    summary = group(analyze_statement(raw.encode(), "fees.csv"))
    assert summary["fees"] == 13
    assert summary["realized_pnl"] == 187


def test_gb18030_pseudo_xls_preserves_leading_zero():
    raw = ('序号\t发生日期\t发生时间\t证券代码\t证券名称\t交易类别\t成交数量\t成交均价\t成交金额\t发生金额\t佣金\t币种\n'
           '="1"\t="20260105"\t="100000"\t="000001"\t="平安银行"\t="证券买入"\t="100"\t="10"\t="1000"\t="-1005"\t="5"\t="人民币"\n'
           '="2"\t="20260106"\t="100000"\t="000001"\t="平安银行"\t="证券卖出"\t="100"\t="12"\t="1200"\t="1195"\t="5"\t="人民币"\n')
    result = analyze_statement(raw.encode("gb18030"), "券商.xls")
    assert result["trades"][0]["symbol"] == "000001"
    assert group(result)["realized_pnl"] == 190


def test_utf8_bom_and_quoted_thousands():
    records = [trade(quantity="1,000", price="10.25", fees="5.50")]
    result = analyze_statement(content(records, encoding="utf-8-sig"), "quoted.csv")
    assert group(result)["buy_amount"] == 10255.5


def test_total_fee_takes_precedence_over_components():
    headers = HEADERS + ["commission", "stamp_duty"]
    records = [trade(fees=6, commission=5, stamp_duty=1)]
    assert group(analyze_statement(content(records, headers=headers), "fees.csv"))["fees"] == 6


def test_company_action_does_not_create_zero_cost_profit():
    records = [trade(), trade(side="红股入账", quantity=100, price="", date="2026-01-06"),
               trade("sell", 200, 12, date="2026-01-07")]
    result = analyze_statement(content(records), "corporate.csv")
    assert group(result)["realized_pnl"] == 0
    assert group(result)["unknown_cost_quantity"] == 200
    assert any(issue["code"] == "cost_basis_uncertain" for issue in result["issues"])


def test_invalid_dated_security_movement_taints_later_sale():
    records = [trade(), trade(quantity="NaN", date="2026-01-06"),
               trade("sell", price=12, date="2026-01-07")]
    result = analyze_statement(content(records), "invalid.csv")
    assert result["skipped_rows"] == 1
    assert group(result)["realized_pnl"] == 0


def test_invalid_undated_security_movement_taints_cost_chain():
    records = [trade(), trade(quantity=100, date="0000-00-00"),
               trade("sell", price=12, date="2026-01-07")]
    result = analyze_statement(content(records), "invalid-date.csv")
    assert group(result)["realized_pnl"] == 0
    assert any("成本链可能不完整" in warning for warning in result["warnings"])


def test_undated_movement_cannot_be_cleared_by_an_ordinary_share_balance():
    records = [trade(share_balance=100), trade(quantity=100, date="0000-00-00"),
               trade("sell", price=12, date="2026-01-07", share_balance=0)]
    summary = group(analyze_statement(content(records), "undated-balance.csv"))
    assert summary["realized_pnl"] == 0
    assert summary["unknown_cost_quantity"] == 100


@pytest.mark.parametrize("field,value", [("quantity", "NaN"), ("price", "Infinity"), ("fees", "-1"), ("quantity", "0"), ("price", "-10"), ("gross_amount", "0")])
def test_invalid_numeric_only_file_is_rejected(field, value):
    with pytest.raises(StatementError, match="没有可分析"):
        analyze_statement(content([trade(**{field: value})]), "invalid.csv")


@pytest.mark.parametrize("raw,filename", [(b"", "empty.csv"), (bytes.fromhex("d0cf11e0a1b11ae1"), "binary.xls"),
                                         (b"PKbroken", "broken.xlsx"), (b"<html><table></table></html>", "html.xls"),
                                         (b"x\x00y", "binary.csv"), (b"abc", "file.pdf")])
def test_unsupported_and_damaged_files_are_rejected(raw, filename):
    with pytest.raises(StatementError):
        analyze_statement(raw, filename)


def test_file_size_and_record_limits():
    with pytest.raises(StatementError, match="2 MiB"):
        analyze_statement(b"x" * (MAX_FILE_BYTES + 1), "large.csv")
    with pytest.raises(StatementError, match="5000"):
        analyze_statement(content([trade()] * 5001), "rows.csv")


def test_xlsx_parses_actual_dates_and_numeric_fields():
    openpyxl = pytest.importorskip("openpyxl")
    from datetime import datetime
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["成交日期", "证券代码", "买卖方向", "成交数量", "成交金额", "佣金", "币种"])
    sheet.append([datetime(2026, 1, 5), "000001", "买入", 100, 1000, 5, "CNY"])
    sheet.append([datetime(2026, 1, 6), "000001", "卖出", 100, 1200, 5, "CNY"])
    stream = io.BytesIO()
    book.save(stream)
    result = analyze_statement(stream.getvalue(), "trades.xlsx")
    assert result["trades"][0]["price"] == 10
    assert result["trades"][0]["symbol"] == "000001"
    assert group(result)["realized_pnl"] == 190


def test_xlsx_decompression_budget():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("huge.xml", b"x" * (33 * 1024 * 1024))
    with pytest.raises(StatementError, match="解压"):
        analyze_statement(stream.getvalue(), "bomb.xlsx")


def test_template_is_complete_parseable_fictional_example():
    result = analyze_statement(statement_template().encode("utf-8-sig"), "template.csv")
    assert result["trade_count"] == 3
    assert group(result)["currency"] == "CNY"
    assert group(result)["open_quantity"] == 50
    assert all("虚构" in item["name"] for item in result["trades"])
