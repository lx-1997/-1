"""Explicitly fictitious CSV examples; never auto-run as live data."""
import csv
from datetime import date, timedelta
import io
import math


def _dates(count):
    values, day = [], date(2024, 1, 2)
    while len(values) < count:
        if day.weekday() < 5:
            values.append(day.isoformat())
        day += timedelta(days=1)
    return values


def research_template(tool):
    rows = []
    if tool == "performance":
        columns = ["date", "return", "benchmark_return", "currency", "name"]
        rows = [dict(date=day, **{"return": round(0.0003 + math.sin(i * .7) * .006, 6)},
                     benchmark_return=round(.0002 + math.cos(i * .4) * .004, 6), currency="CNY", name="虚构策略，仅演示")
                for i, day in enumerate(_dates(90))]
    elif tool == "portfolio":
        columns = ["date", "symbol", "close", "weight", "currency"]
        for i, day in enumerate(_dates(90)):
            for number in range(3):
                rows.append(dict(date=day, symbol="DEMO" + str(number + 1),
                                 close=round(100 + number * 10 + i * .08 + math.sin(i * .3 + number) * 2, 4),
                                 weight=[.4, .35, .25][number], currency="CNY"))
    elif tool == "financials":
        columns = ["symbol", "period", "currency", "period_type", "revenue", "net_income", "operating_cashflow", "capex", "total_assets", "total_equity", "total_debt", "current_assets", "current_liabilities", "receivables", "inventory"]
        for symbol in ["DEMO1", "DEMO2"]:
            for i in range(8):
                rows.append(dict(symbol=symbol, period=f"{2023 + i // 4}-Q{i % 4 + 1}", currency="CNY", period_type="quarterly",
                                 revenue=1000 + 100 * i, net_income=100 + 10 * i, operating_cashflow=110 + 11 * i,
                                 capex=30, total_assets=3000 + 150 * i, total_equity=2000 + 90 * i, total_debt=500 + 10 * i,
                                 current_assets=1200 + 50 * i, current_liabilities=600 + 20 * i,
                                 receivables=150 + 10 * i, inventory=200 + 10 * i))
    elif tool == "institutions":
        columns = ["cusip", "name", "shares", "value", "put_call", "share_type", "report_date", "filing_date", "value_unit"]
        rows = [dict(cusip="DEMO00001", name="虚构证券，仅演示", shares=1000, value=12000, put_call="", share_type="SH", report_date="2024-06-30", filing_date="2024-08-10", value_unit="USD")]
    elif tool == "funds":
        columns = ["fund", "symbol", "name", "weight", "sector", "as_of"]
        rows = [dict(fund=fund, symbol=symbol, name="虚构证券，仅演示", weight=weight, sector=sector, as_of="2024-06-30")
                for fund, symbol, weight, sector in [("DEMO-FUND-A", "DEMO1", .3, "科技"), ("DEMO-FUND-A", "DEMO2", .2, "消费"),
                                                   ("DEMO-FUND-B", "DEMO1", .25, "科技"), ("DEMO-FUND-B", "DEMO3", .25, "工业")]]
    elif tool == "bonds":
        columns = ["symbol", "name", "price", "conversion_price", "stock_price", "years_to_maturity", "redemption_status", "volume", "as_of"]
        rows = [dict(symbol="DEMO-BOND-1", name="虚构转债，仅演示", price=105, conversion_price=10, stock_price=9, years_to_maturity=2.5,
                     redemption_status="unknown", volume=100000, as_of="2024-06-30")]
    elif tool == "factors":
        columns = ["date", "symbol", "open", "high", "low", "close", "volume"]
        for i, day in enumerate(_dates(240)):
            for number in range(5):
                close = 50 + number * 10 + i * .02 + math.sin(i * .17 + number) * 2 + math.cos(i * .11) * .5
                rows.append(dict(date=day, symbol="DEMO" + str(number + 1), open=round(close - .15, 5), high=round(close + .5, 5),
                                 low=round(close - .5, 5), close=round(close, 5), volume=100000 + int(5000 * math.sin(i * .2 + number))))
    else:
        raise ValueError("未知研究工具")
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)
    return "\ufeff" + output.getvalue()
