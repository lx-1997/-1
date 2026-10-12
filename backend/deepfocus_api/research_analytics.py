"""Bounded analytics over user-provided data, using actual open-source engines.

Heavy libraries are loaded only inside worker calls. Nothing downloads market
data, writes an account ledger, or places an order.
"""
from __future__ import annotations

import base64
import binascii
import csv
import html
import io
import json
import math
import re
import statistics
import tempfile
import warnings as python_warnings
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse


MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000
MAX_SYMBOLS = 20
_ALIASES = {
    "日期": "date", "交易日期": "date", "日收益率": "return", "收益率": "return",
    "基准收益率": "benchmark_return", "净值": "value", "账户净值": "value", "基准净值": "benchmark_value",
    "证券代码": "symbol", "股票代码": "symbol", "代码": "symbol", "收盘价": "close", "复权收盘价": "close",
    "权重": "weight", "币种": "currency", "期间": "period", "报告期": "period", "营业收入": "revenue",
    "净利润": "net_income", "经营现金流": "operating_cashflow", "资本开支": "capex", "总资产": "total_assets",
    "股东权益": "total_equity", "总权益": "total_equity", "有息负债": "total_debt", "流动资产": "current_assets",
    "流动负债": "current_liabilities", "应收账款": "receivables", "存货": "inventory",
}


def _number(value: Any, label: str, *, optional: bool = False) -> Optional[float]:
    if optional and (value is None or value == "" or value == "--"):
        return None
    if isinstance(value, bool):
        raise ValueError(f"{label}必须是有限数字，不能是布尔值。")
    raw = str(value).strip()
    if "," in raw and not re.fullmatch(r"[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?", raw):
        raise ValueError(f"{label}的千位分隔格式无效，请用小数点和标准数字。")
    try:
        result = float(raw.replace(",", ""))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label}必须是数字；比例请填小数，例如 0.01 表示 1%。") from exc
    if not math.isfinite(result) or abs(result) > 1e16:
        raise ValueError(f"{label}必须是有限、合理的数字。")
    return result


def _finite(value: Any) -> Optional[float]:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _currency(value: Any) -> str:
    raw = str(value or "").strip().upper()
    result = {"人民币": "CNY", "RMB": "CNY", "港币": "HKD", "港元": "HKD", "美元": "USD"}.get(raw, raw)
    if not re.fullmatch(r"[A-Z]{3}", result):
        raise ValueError("请明确选择数据的估值币种，例如 CNY、HKD 或 USD。")
    return result


def _day(value: Any) -> str:
    raw = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            parsed = datetime.strptime(raw, fmt).date()
            if not 1900 <= parsed.year <= 2100:
                break
            return parsed.isoformat()
        except ValueError:
            continue
    raise ValueError(f"日期 {raw[:40]!r} 无效；请使用 YYYY-MM-DD，每个交易日一条。")


def _symbol(value: Any) -> str:
    result = str(value or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{0,23}", result):
        raise ValueError("证券代码不能为空，且只支持字母、数字、点、横线或下划线。")
    return result


def _input(data: dict[str, Any], default_key: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("研究输入必须是 JSON 对象。")
    if "content_base64" not in data:
        try:
            size = len(json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise ValueError("输入必须是可序列化 JSON，不能含 NaN 或 Infinity。") from exc
        if size > MAX_BYTES:
            raise ValueError("输入最多支持 2 MiB。")
        return dict(data)
    encoded = data["content_base64"]
    if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_BYTES + 2) // 3):
        raise ValueError("文件最多支持 2 MiB。")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("文件 Base64 编码无效，请重新选择文件。") from exc
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("文件为空或超过 2 MiB。")
    suffix = Path(str(data.get("file_name", ""))).suffix.lower()
    if suffix not in {".csv", ".tsv", ".json"}:
        raise ValueError("此工具支持 CSV、TSV 或 JSON 数据文件。")
    text = None
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None or "\x00" in text:
        raise ValueError("文件编码无法识别，请另存为 UTF-8 CSV。")
    if suffix == ".json":
        try:
            parsed = json.loads(text)
        except ValueError as exc:
            raise ValueError("JSON 文件格式无效。") from exc
        if isinstance(parsed, list):
            parsed = {default_key: parsed}
        if not isinstance(parsed, dict):
            raise ValueError("JSON 文件必须包含数据对象或记录数组。")
    else:
        try:
            reader = csv.DictReader(io.StringIO(text), delimiter="\t" if suffix == ".tsv" or "\t" in text.splitlines()[0] else ",", strict=True)
            if not reader.fieldnames or len(reader.fieldnames) > 64:
                raise ValueError("CSV 缺少表头或列数超过 64。")
            names = [_ALIASES.get(name.strip(), name.strip().lower()) for name in reader.fieldnames]
            if len(set(names)) != len(names):
                raise ValueError("CSV 含重复列名，请修正表头。")
            if default_key == "returns" and "value" in names and "return" in names:
                raise ValueError("CSV 不能同时提供净值 value 和日收益 return，请只保留一种数据。")
            parsed_rows = []
            for row in reader:
                if None in row:
                    raise ValueError("CSV 某行的列数超过表头，请检查分隔符与引号。")
                if any(value not in {None, ""} for value in row.values()):
                    parsed_rows.append({name: row[original] for name, original in zip(names, reader.fieldnames)})
                if len(parsed_rows) > MAX_ROWS:
                    raise ValueError("最多支持 5000 条数据记录。")
            key = "net_values" if default_key == "returns" and "value" in names else default_key
            parsed = {key: parsed_rows}
        except csv.Error as exc:
            raise ValueError("CSV 格式无效，请检查引号和分隔符。") from exc
    for key, value in data.items():
        if key not in {"file_name", "content_base64"}:
            if key in parsed and parsed[key] != value:
                raise ValueError(f"文件内容与表单中的 {key} 冲突，请统一输入。")
            parsed[key] = value
    parsed["_source_name"] = Path(str(data.get("file_name", "用户上传"))).name[:255]
    return _input(parsed, default_key)


def _records(data: dict[str, Any], key: str, minimum: int = 1) -> list[dict[str, Any]]:
    records = data.get(key)
    if not isinstance(records, list) or not minimum <= len(records) <= MAX_ROWS or any(not isinstance(row, dict) for row in records):
        raise ValueError(f"{key} 需要 {minimum}–5000 条 JSON 记录。")
    return records


def _daily(dates: list[str], warning_list: list[str]) -> None:
    gaps = [(date.fromisoformat(b) - date.fromisoformat(a)).days for a, b in zip(dates, dates[1:])]
    if gaps and statistics.median(gaps) > 3:
        raise ValueError("输入似乎是周/月数据；本工具只接受每日交易日收益或价格，不能按 252 日错误年化。")
    if dates:
        first, last = date.fromisoformat(dates[0]), date.fromisoformat(dates[-1])
        weekdays = sum((first + timedelta(days=offset)).weekday() < 5 for offset in range((last - first).days + 1))
        covered_weekdays = sum(date.fromisoformat(day).weekday() < 5 for day in dates)
        if weekdays and covered_weekdays / weekdays < .8:
            raise ValueError("日频数据工作日覆盖不足 80%；稀疏收益或价格不能按 252 日年化。请提供完整日序列，节假日不填补行情。")
    if any(gap > 10 for gap in gaps):
        warning_list.append("序列有超过 10 个日历日的间隔；未填充缺失行情，请核实节假日、停牌及是否漏日。")


def _base(tool: str, title: str, engine: str, data: dict[str, Any], as_of: str, currency: Optional[str] = None) -> dict[str, Any]:
    source = {"name": data.get("_source_name", "用户提供数据"), "as_of": as_of, "coverage": "仅覆盖本次输入，不自动联网补齐"}
    if currency:
        source["currency"] = currency
    warnings = [str(value)[:1000] for value in data.get("warnings", [])[:100] if isinstance(value, str)] if isinstance(data.get("warnings", []), list) else []
    methodology = [str(value)[:1000] for value in data.get("methodology", [])[:100] if isinstance(value, str)] if isinstance(data.get("methodology", []), list) else []
    sources = []
    for item in data.get("sources", [])[:20] if isinstance(data.get("sources", []), list) else []:
        if not isinstance(item, dict):
            continue
        cleaned = {key: str(item[key])[:1000] for key in ("name", "as_of", "coverage", "currency") if item.get(key) is not None}
        raw_url = str(item.get("url", ""))[:2000]
        parsed_url = urlparse(raw_url)
        if parsed_url.scheme in {"https", "http"} and parsed_url.hostname and not parsed_url.username and not parsed_url.password:
            cleaned["url"] = raw_url
        if cleaned.get("name"):
            sources.append(cleaned)
    if data.get("is_demo") is True or any("虚构" in str(row.get("dataset_note", "")) for key in ("returns", "net_values", "prices", "records") for row in data.get(key, []) if isinstance(row, dict)):
        warnings.append("本次选择的是虚构演示数据，结果仅用于熟悉工具，不能作为真实账户或企业结论。")
    return {"tool": tool, "title": title, "engine": engine, "summary": [], "tables": [], "charts": [],
            "warnings": warnings, "methodology": methodology, "sources": sources or [source]}


def _table(key: str, title: str, columns: list[tuple[str, str, str]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"key": key, "title": title, "columns": [{"key": key, "label": label, "type": kind} for key, label, kind in columns], "rows": rows}


def _quantstats_html(qs, returns, benchmark, rf: float, title: str) -> str:
    """Generate real native HTML in a private, immediately deleted directory."""
    with tempfile.TemporaryDirectory(prefix="research-performance-") as directory:
        path = Path(directory) / "report.html"
        qs.reports.html(returns, benchmark=benchmark, rf=rf, output=str(path), title=html.escape(title),
                        strategy_title="Strategy", benchmark_title="Benchmark", match_dates=False,
                        periods_per_year=252, figfmt="svg")
        result = path.read_text(encoding="utf-8")
    if not result or len(result.encode("utf-8")) > 8 * MAX_BYTES:
        raise RuntimeError("QuantStats HTML 报告为空或超过大小限制。")
    return result


def analyze_performance(data: dict[str, Any]) -> dict[str, Any]:
    data = _input(data, "returns")
    if ("returns" in data) == ("net_values" in data):
        raise ValueError("请只提供 returns 日收益率或 net_values 每日净值其中一种，不能用成交盈亏替代。")
    key = "returns" if "returns" in data else "net_values"
    records = _records(data, key, 30)
    currency = _currency(data.get("currency"))
    rf = _number(data.get("risk_free_rate", 0), "年化无风险利率")
    if not -1 < rf <= 1:
        raise ValueError("无风险利率必须是年化小数，范围大于 -1 且不超过 1。")
    value_key, benchmark_key = ("return", "benchmark_return") if key == "returns" else ("value", "benchmark_value")
    by_date = {}
    has_benchmark = [row.get(benchmark_key) not in {None, ""} for row in records]
    if any(has_benchmark) and not all(has_benchmark):
        raise ValueError("基准数据必须覆盖全部同日记录；请补齐或完全移除基准列，不能将缺失值当 0。")
    for index, row in enumerate(records, 1):
        day = _day(row.get("date"))
        if day in by_date:
            raise ValueError(f"日期 {day} 重复；每日收益/净值只能有一条。")
        value = _number(row.get(value_key), f"第 {index} 行 {value_key}")
        if (key == "returns" and value <= -1) or (key == "net_values" and value <= 0):
            raise ValueError("日收益率必须大于 -1，净值必须大于 0。")
        if row.get("currency") and _currency(row["currency"]) != currency:
            raise ValueError("每日账户及基准必须使用同一估值币种；请先处理换汇。")
        for flow_key in ("cash_flow", "cashflow", "deposit", "withdrawal"):
            if row.get(flow_key) not in {None, ""} and _number(row[flow_key], flow_key) != 0:
                raise ValueError("含出入金的账户余额不是可直接计算的净值；请提供已排除资金流影响的单位净值/日收益。")
        benchmark = _number(row.get(benchmark_key), "基准数值") if all(has_benchmark) else None
        if benchmark is not None and ((key == "returns" and benchmark <= -1) or (key == "net_values" and benchmark <= 0)):
            raise ValueError("基准日收益率必须大于 -1，基准净值必须大于 0。")
        by_date[day] = (value, benchmark)
    dates = sorted(by_date)
    report = _base("performance", f"{str(data.get('name', '净值绩效'))[:80]} · 绩效报告", "QuantStats", data, dates[-1], currency)
    _daily(dates, report["warnings"])
    try:
        import numpy as np
        import pandas as pd
        import quantstats as qs
    except ImportError as exc:
        raise RuntimeError("绩效运行环境缺少 QuantStats/Pandas，请管理员配置分析运行环境。") from exc
    values = pd.Series([by_date[day][0] for day in dates], index=pd.to_datetime(dates), dtype=float, name="Strategy")
    benchmark = pd.Series([by_date[day][1] for day in dates], index=values.index, dtype=float, name="Benchmark") if all(has_benchmark) else None
    if key == "net_values":
        returns = values.pct_change(fill_method=None).iloc[1:]
        benchmark = benchmark.pct_change(fill_method=None).iloc[1:] if benchmark is not None else None
    else:
        returns = values
    if not np.isfinite(returns.to_numpy()).all() or (returns <= -1).any():
        raise ValueError("净值变化得到无效日收益，请核实数值尺度。")
    if returns.min() >= 0 and returns.max() >= 1:
        raise ValueError("全非负且含 100% 以上的日收益会被 QuantStats 误识别为价格；请核实小数单位，此极端序列暂不支持。")
    if benchmark is not None and benchmark.min() >= 0 and benchmark.max() >= 1:
        raise ValueError("基准全非负且含 100% 以上日收益，无法可靠判别；请核实小数单位。")
    with python_warnings.catch_warnings():
        python_warnings.simplefilter("ignore", RuntimeWarning)
        metrics = {"total_return": _finite(qs.stats.comp(returns)), "max_drawdown": _finite(qs.stats.max_drawdown(returns)),
                   "volatility": _finite(qs.stats.volatility(returns, periods=252, prepare_returns=False)),
                   "sharpe": _finite(qs.stats.sharpe(returns, rf=rf, periods=252)),
                   "sortino": _finite(qs.stats.sortino(returns, rf=rf, periods=252)),
                   "positive_period_ratio": _finite(qs.stats.win_rate(returns, prepare_returns=False))}
    if metrics["volatility"] is not None and metrics["volatility"] <= 1e-12:
        metrics["sharpe"] = metrics["sortino"] = None
        report["warnings"].append("样本没有波动，Sharpe/Sortino 不可估计；显示为空，不输出无限值。")
    if not (returns < 0).any():
        metrics["sortino"] = None
        report["warnings"].append("样本没有负收益日，Sortino 下行风险分母无法可靠估计。")
    report["summary"] = [{"label": "累计净收益", "value": metrics["total_return"], "unit": "%"},
                         {"label": "最大回撤", "value": metrics["max_drawdown"], "unit": "%"},
                         {"label": "年化波动率", "value": metrics["volatility"], "unit": "%"},
                         {"label": "Sharpe", "value": metrics["sharpe"]}, {"label": "Sortino", "value": metrics["sortino"]},
                         {"label": "正收益日占比（非零日）", "value": metrics["positive_period_ratio"], "unit": "%"},
                         {"label": "有效收益观测", "value": len(returns), "unit": "日"}, {"label": "估值币种", "value": currency}]
    strategy_nav = (1 + returns).cumprod()
    benchmark_nav = (1 + benchmark).cumprod() if benchmark is not None else None
    if not np.isfinite(strategy_nav.to_numpy()).all():
        raise ValueError("收益复利溢出，请核实是否误填百分数、价格或非日收益。")
    nav_rows = [{"date": index.date().isoformat(), "strategy": _finite(value),
                 **({"benchmark": _finite(benchmark_nav.loc[index])} if benchmark_nav is not None else {})} for index, value in strategy_nav.items()]
    series = [{"key": "strategy", "label": "策略净值（起始基准 1）"}]
    if benchmark is not None:
        benchmark_return = _finite(qs.stats.comp(benchmark))
        report["summary"].append({"label": "基准累计收益", "value": benchmark_return, "unit": "%"})
        series.append({"key": "benchmark", "label": "基准净值"})
    drawdown = strategy_nav / strategy_nav.cummax() - 1
    # Including initial capital is necessary when the first observation loses.
    drawdown = strategy_nav / strategy_nav.cummax().clip(lower=1) - 1
    monthly = (1 + returns).groupby(returns.index.to_period("M")).prod() - 1
    monthly_rows = [{"month": str(index), "return": _finite(value)} for index, value in monthly.items()]
    report["tables"] = [_table("metrics", "指标口径", [("metric", "指标", "text"), ("value", "数值", "number")],
                                [{"metric": key, "value": value} for key, value in metrics.items()]),
                        _table("monthly", "月度净收益", [("month", "月份", "text"), ("return", "收益率", "percent")], monthly_rows)]
    years = sorted({str(index.year) for index in monthly.index})
    monthly_map = {str(index): _finite(value) for index, value in monthly.items()}
    report["charts"] = [{"key": "net_value", "title": "净值与基准", "type": "line", "data": nav_rows, "x_key": "date", "series": series},
                        {"key": "drawdown", "title": "回撤（小数比例）", "type": "line", "data": [{"date": index.date().isoformat(), "drawdown": _finite(value)} for index, value in drawdown.items()], "x_key": "date", "series": [{"key": "drawdown", "label": "回撤"}]},
                        {"key": "monthly_heatmap", "title": "月度收益热图", "type": "heatmap", "data": monthly_rows,
                         "labels": [str(month) for month in range(1, 13)], "row_labels": years, "value_type": "percent",
                         "matrix": [[monthly_map.get(f"{year}-{month:02d}") for month in range(1, 13)] for year in years]}]
    report["methodology"] += ["实际调用 QuantStats，输入为每日净收益或已排除出入金的单位净值；不使用成交盈亏构造账户收益。",
                              f"估值币种 {currency}；无风险利率为年化小数 {rf}，年化因子 252 个交易日。",
                              "收益按输入净口径使用，不再次扣手续费；基准与策略严格使用同一组日期。",
                              "正收益日占比按 QuantStats 非零收益期间统计，不能当成逐笔交易胜率；样本统计不代表未来收益。"]
    report["warnings"].append("少于一年或存在停牌/缺日的样本，其年化指标需谨慎解读；系统未联网验证账户资金流和数据授权。")
    try:
        report["report_html"] = _quantstats_html(qs, returns, benchmark, rf, report["title"])
    except Exception as exc:
        raise RuntimeError(f"QuantStats 报告生成失败：{type(exc).__name__}；请管理员检查依赖版本，未返回伪造报告。") from exc
    report["sources"].append({"name": "QuantStats", "url": "https://github.com/ranaroussi/quantstats"})
    return report


def analyze_portfolio(data: dict[str, Any]) -> dict[str, Any]:
    data = _input(data, "prices")
    records = _records(data, "prices", 60)
    currency = _currency(data.get("currency"))
    if not isinstance(data.get("fx_normalized", False), bool):
        raise ValueError("fx_normalized 必须是布尔值，不能用字符串声明已换汇。")
    prices, csv_weights, row_currencies, adjustments = {}, {}, set(), set()
    for index, row in enumerate(records, 1):
        symbol, day = _symbol(row.get("symbol")), _day(row.get("date"))
        close = _number(row.get("close"), f"第 {index} 行收盘价")
        if close <= 0:
            raise ValueError("收盘价必须大于 0；不能把缺失价补成 0。")
        if (day, symbol) in prices:
            raise ValueError(f"{symbol} 在 {day} 的价格重复。")
        prices[(day, symbol)] = close
        if row.get("currency"):
            row_currencies.add(_currency(row["currency"]))
        if row.get("adjustment"):
            adjustments.add(str(row["adjustment"]).lower())
        if row.get("weight") not in {None, ""}:
            weight = _number(row["weight"], "权重")
            if symbol in csv_weights and abs(csv_weights[symbol] - weight) > 1e-9:
                raise ValueError("CSV 同一股票权重必须一致，本报告是固定权重情景。")
            csv_weights[symbol] = weight
    if adjustments & {"none", "raw", "unadjusted"} or len(adjustments) > 1 or str(data.get("adjustment", "")).lower() in {"none", "raw", "unadjusted"}:
        raise ValueError("请提供所有标的同口径的复权价格，不能混用前复权/后复权/原始价。")
    if (len(row_currencies) > 1 or (row_currencies and row_currencies != {currency})) and data.get("fx_normalized") is not True:
        raise ValueError("价格币种不统一，请先换算到相同估值币种并显式确认 fx_normalized=true；系统不自动换汇。")
    raw_weights = data.get("weights") or [{"symbol": symbol, "weight": weight} for symbol, weight in csv_weights.items()]
    if not isinstance(raw_weights, list) or not 1 <= len(raw_weights) <= MAX_SYMBOLS:
        raise ValueError("请填写 1–20 个标的及正权重，或在 CSV 中加入 weight 列。")
    weights = {}
    for item in raw_weights:
        if not isinstance(item, dict):
            raise ValueError("weights 必须是 symbol/weight 对象数组。")
        symbol = _symbol(item.get("symbol"))
        weight = _number(item.get("weight"), "权重")
        if not 0 < weight <= 1 or symbol in weights:
            raise ValueError("每个股票必须只有一个正权重；0.2 表示 20%，不支持空头或杠杆。")
        weights[symbol] = weight
    if abs(sum(weights.values()) - 1) > 1e-6:
        raise ValueError("权重总和必须为 1，不会自动放大或归一不完整组合。")
    symbols = sorted({symbol for _, symbol in prices})
    if len(symbols) > MAX_SYMBOLS or set(symbols) != set(weights):
        raise ValueError("价格数据与权重标的必须完全匹配，最多 20 个股票。")
    date_sets = [{day for day, item in prices if item == symbol} for symbol in symbols]
    common = sorted(set.intersection(*date_sets))
    if len(common) < 60:
        raise ValueError("每个标的至少需要 60 个共同交易日；缺失价不前向填充。")
    report = _base("portfolio", "持仓风险贡献与相关性", "Riskfolio-Lib", data, common[-1], currency)
    _daily(common, report["warnings"])
    removed = len(set.union(*date_sets)) - len(common)
    if removed:
        report["warnings"].append(f"剔除 {removed} 个未被所有标的共同覆盖的日期，未补价或用停牌价制造收益。")
    try:
        import numpy as np
        import pandas as pd
        import riskfolio as rp
        from riskfolio.src import ParamsEstimation
    except ImportError as exc:
        raise RuntimeError("组合分析运行环境缺少 Riskfolio-Lib，请管理员配置运行环境。") from exc
    frame = pd.DataFrame([[prices[(day, symbol)] for symbol in symbols] for day in common], index=pd.to_datetime(common), columns=symbols)
    returns = frame.pct_change(fill_method=None).iloc[1:]
    if not np.isfinite(returns.to_numpy()).all() or (returns <= -1).any().any():
        raise ValueError("价格相对变动无法计算有限日收益，请检查价格尺度与复权。")
    # The estimation function preserves the observed covariance, including
    # singular/zero samples. Portfolio.assets_stats would clip eigenvalues to
    # make optimization feasible, inventing volatility for flat observations.
    covariance = ParamsEstimation.covar_matrix(returns, method="hist")
    w = pd.Series([weights[symbol] for symbol in symbols], index=symbols, dtype=float)
    portfolio_returns = returns @ w
    daily_risk = float(np.sqrt(max(0, w.to_numpy() @ covariance.to_numpy() @ w.to_numpy())))
    with python_warnings.catch_warnings():
        python_warnings.simplefilter("ignore", RuntimeWarning)
        contributions = np.asarray(rp.Risk_Contribution(w=w, returns=returns, cov=covariance, rm="MV")).reshape(-1)
        cvar = _finite(rp.CVaR_Hist(portfolio_returns.to_numpy(), alpha=0.05))
    clusters = {symbol: None for symbol in symbols}
    if len(symbols) == 1:
        clusters[symbols[0]] = "Cluster 1"
    elif (returns.std() > 1e-12).all():
        cluster_frame = rp.assets_clusters(returns, codependence="pearson", linkage="ward", k=min(3, len(symbols)))
        clusters = dict(zip(cluster_frame["Assets"].astype(str), cluster_frame["Clusters"].astype(str)))
    else:
        report["warnings"].append("有标的样本零波动，相关性与聚类不可可靠估计；未伪造聚类结果。")
    risk_rows = [{"symbol": symbol, "weight": weights[symbol],
                  "risk_contribution": _finite(contributions[index] / daily_risk) if daily_risk > 1e-12 else None,
                  "annual_risk_contribution": _finite(contributions[index] * math.sqrt(252)) if daily_risk > 1e-12 else None,
                  "cluster": clusters.get(symbol)} for index, symbol in enumerate(symbols)]
    report["summary"] = [{"label": "共同价格日期", "value": len(common), "unit": "日"}, {"label": "标的数", "value": len(symbols)},
                         {"label": "年化波动率", "value": daily_risk * math.sqrt(252), "unit": "%"},
                         {"label": "历史日 CVaR（95%）", "value": cvar, "unit": "%"}]
    report["tables"] = [_table("risk_contributions", "权重与风险贡献", [("symbol", "股票", "text"), ("weight", "资金权重", "percent"), ("risk_contribution", "波动风险占比", "percent"), ("annual_risk_contribution", "年化波动贡献", "percent"), ("cluster", "相关性聚类", "text")], risk_rows),
                        _table("covariance", "样本协方差（日收益单位）", [("symbol", "股票", "text")] + [(symbol, symbol, "number") for symbol in symbols],
                               [{"symbol": symbol, **{other: _finite(covariance.loc[symbol, other]) for other in symbols}} for symbol in symbols])]
    correlation = returns.corr()
    report["charts"] = [{"key": "risk_contribution", "title": "波动风险贡献（%）", "type": "bar", "x_key": "symbol", "series": [{"key": "contribution_pct", "label": "风险占比 %"}],
                         "data": [{"symbol": row["symbol"], "contribution_pct": row["risk_contribution"] * 100 if row["risk_contribution"] is not None else None} for row in risk_rows]},
                        {"key": "correlation", "title": "日收益相关性（-1 至 1）", "type": "heatmap", "data": [], "labels": symbols, "row_labels": symbols, "value_type": "number",
                         "matrix": [[_finite(value) for value in row] for row in correlation.to_numpy()]}]
    report["methodology"] += ["实际调用 Riskfolio-Lib 历史协方差、Euler 波动风险贡献、Pearson/Ward 聚类及历史 CVaR。",
                              "历史 CVaR 使用最差 5% 日收益的尾部损失，单位为日收益比例，没有错误乘年化因子。",
                              f"按用户声明，所有收盘价已采用一致复权并换算到 {currency}；系统不联网补行情或汇率。",
                              "权重是每日固定权重的研究情景；不计额外调仓成本，不代表真实账户净值，不生成调仓指令。"]
    report["warnings"].append("风险贡献可能为负（分散化作用）；样本相关性、聚类和尾部风险不代表未来表现。")
    if data.get("fx_normalized") is True:
        report["warnings"].append(f"用户声明价格已统一换汇至 {currency}；原币标签仅供来源核对，系统未验证换汇。")
    if daily_risk <= 1e-12:
        report["warnings"].append("样本组合没有可估计波动，风险贡献占比为空。")
    if cvar is not None and cvar < 0:
        report["warnings"].append("历史 CVaR 为负表示这段样本的最差尾部仍是收益；不表示未来没有损失风险。")
    report["sources"].append({"name": "Riskfolio-Lib", "url": "https://github.com/dcajasn/Riskfolio-Lib"})
    return report


_FINANCIAL_FIELDS = ("revenue", "net_income", "operating_cashflow", "capex", "total_assets", "total_equity", "total_debt", "current_assets", "current_liabilities", "receivables", "inventory")
_FINANCIAL_LABELS = dict(zip(_FINANCIAL_FIELDS, ("营收", "净利润", "经营现金流", "资本支出", "总资产", "股东权益", "有息债务", "流动资产", "流动负债", "应收款", "存货")))


def _financial_warning_summary(report: dict[str, Any]) -> None:
    """Keep disclosure-version risks visible; move technical concept IDs to detail."""
    readable = []
    for warning in report["warnings"]:
        text = warning
        symbol = warning.split(" ", 1)[0]
        if "累计差分" in warning and "不同申报" in warning:
            text = f"{symbol} 部分单季金额由不同申报的累计值差分得到，请核对重述和会计政策。"
        elif "晚期重述" in warning or "前期版本" in warning:
            text = f"{symbol} 部分单季数据缺少当时可比较的披露版本，保留缺失，未混用后续重述。"
        else:
            for field, label in _FINANCIAL_LABELS.items():
                text = re.sub(r"\b" + field + r"\b", label, text)
            text = re.sub(r"(?<=\d{4}-\d{2}-\d{2} )[A-Za-z][A-Za-z0-9_]{15,}(?= )", "财务项目", text)
        if text != warning:
            report["methodology"].append("来源核对明细：" + warning)
        readable.append(text)
    report["warnings"] = list(dict.fromkeys(readable))


def _period(value: Any, period_type: str) -> tuple[str, int]:
    raw = str(value or "").strip().upper()
    if period_type == "annual":
        if re.fullmatch(r"\d{4}", raw):
            year = int(raw)
            if not 1900 <= year <= 2100:
                raise ValueError("年度必须在 1900–2100 年之间。")
            return str(year), date(year, 12, 31).toordinal()
        else:
            parsed = date.fromisoformat(_day(raw))
            return (str(parsed.year) if (parsed.month, parsed.day) == (12, 31) else parsed.isoformat()), parsed.toordinal()
    match = re.fullmatch(r"(\d{4})[- ]?Q([1-4])", raw)
    if match:
        year, quarter = int(match.group(1)), int(match.group(2))
        if not 1900 <= year <= 2100:
            raise ValueError("季度年份必须在 1900–2100 年之间。")
        month, day = ((3, 31), (6, 30), (9, 30), (12, 31))[quarter - 1]
        return f"{year}Q{quarter}", date(year, month, day).toordinal()
    else:
        parsed = date.fromisoformat(_day(raw))
        if (parsed.month, parsed.day) not in {(3, 31), (6, 30), (9, 30), (12, 31)}:
            return parsed.isoformat(), parsed.toordinal()
        year, quarter = parsed.year, (parsed.month - 1) // 3 + 1
    if not 1900 <= year <= 2100:
        raise ValueError("季度年份必须在 1900–2100 年之间。")
    return f"{year}Q{quarter}", parsed.toordinal()


def analyze_financials(data: dict[str, Any]) -> dict[str, Any]:
    data = _input(data, "records")
    records = _records(data, "records")
    period_type = data.get("period_type")
    if period_type not in {"quarterly", "annual"}:
        raise ValueError("请明确选择 quarterly 单季报或 annual 年报；不能混用季报、累计季报和年报。")
    if data.get("flow_basis") in {"ytd", "cumulative"} or data.get("cumulative") is True:
        raise ValueError("累计季报不能直接当单季指标，请先转换为独立单季发生额。")
    parsed, identities, currencies = [], set(), {}
    for index, row in enumerate(records, 1):
        symbol = _symbol(row.get("symbol"))
        period, ordinal = _period(row.get("period"), period_type)
        currency = _currency(row.get("currency"))
        if row.get("period_end") and date.fromisoformat(_day(row["period_end"])).toordinal() != ordinal:
            raise ValueError("period 与真实 period_end 冲突，不能把非自然财年末伪映射为自然季末。")
        if row.get("period_start"):
            start_ordinal = date.fromisoformat(_day(row["period_start"])).toordinal()
            duration = ordinal - start_ordinal + 1
            bounds = (70, 110) if period_type == "quarterly" else (320, 400)
            if not bounds[0] <= duration <= bounds[1]:
                raise ValueError("报告发生额的持续期间不符合单季/单年，请排除累计数据和短过渡期。")
        if row.get("period_type") and row["period_type"] != period_type:
            raise ValueError("同一分析不能混用季报和年报。")
        if row.get("flow_basis") in {"ytd", "cumulative"} or row.get("cumulative") is True:
            raise ValueError("累计数据需先转换为独立单季金额。")
        if (symbol, period) in identities:
            raise ValueError(f"{symbol} 的 {period} 财报重复，请只保留同一口径版本。")
        identities.add((symbol, period))
        if symbol in currencies and currencies[symbol] != currency:
            raise ValueError("同一企业历史币种必须一致；改币种需先统一可比财报金额。")
        currencies[symbol] = currency
        values = {field: _number(row.get(field), f"第 {index} 行 {field}", optional=True) for field in _FINANCIAL_FIELDS}
        if not any(value is not None for value in values.values()):
            raise ValueError("每条财报至少需要一个真实财务数值；不能只给股票代码/期间。")
        for field in ("revenue", "capex", "total_assets", "total_debt", "current_assets", "current_liabilities", "receivables", "inventory"):
            if values[field] is not None and values[field] < 0:
                raise ValueError(f"{field} 不能为负；capex 请填资本支出的正额，不能误减带负号的现金流。")
        parsed.append({"symbol": symbol, "period": period, "ordinal": ordinal, "currency": currency, **values})
    if len(currencies) > MAX_SYMBOLS:
        raise ValueError("企业财报分析最多支持 20 个标的。")
    parsed.sort(key=lambda row: (row["symbol"], row["ordinal"]))
    try:
        from financetoolkit.ratios import profitability_model as profitability
        from financetoolkit.ratios import solvency_model as solvency
        from financetoolkit.ratios import liquidity_model as liquidity
    except ImportError as exc:
        raise RuntimeError("财务分析运行环境缺少 FinanceToolkit，请管理员配置运行环境。") from exc
    report = _base("financials", "企业财报同口径比较", "FinanceToolkit", data, date.fromordinal(max(row["ordinal"] for row in parsed)).isoformat())
    metric_rows, previous = [], {}
    unavailable_balances, nonpositive_equity, missing_periods = {}, {}, {}
    def ratio(function, numerator, denominator, *, positive_denominator=True):
        if numerator is None or denominator is None or (denominator <= 0 if positive_denominator else denominator == 0):
            return None
        return _finite(function(numerator, denominator))
    for row in parsed:
        prev = previous.get(row["symbol"])
        bounds = (70, 110) if period_type == "quarterly" else (320, 400)
        adjacent = prev is not None and bounds[0] <= row["ordinal"] - prev["ordinal"] <= bounds[1]
        avg_assets = (prev["total_assets"] + row["total_assets"]) / 2 if adjacent and prev["total_assets"] is not None and row["total_assets"] is not None else None
        avg_equity = (prev["total_equity"] + row["total_equity"]) / 2 if adjacent and prev["total_equity"] is not None and row["total_equity"] is not None else None
        fcf = _finite(row["operating_cashflow"] - row["capex"]) if row["operating_cashflow"] is not None and row["capex"] is not None else None
        metrics = {"symbol": row["symbol"], "period": row["period"], "period_end": date.fromordinal(row["ordinal"]).isoformat(),
                   "period_type": period_type, "currency": row["currency"], "revenue": row["revenue"], "net_income": row["net_income"],
                   "net_margin": ratio(profitability.get_net_profit_margin, row["net_income"], row["revenue"]),
                   "roe": ratio(profitability.get_return_on_equity, row["net_income"], avg_equity),
                   "roa": ratio(profitability.get_return_on_assets, row["net_income"], avg_assets),
                   "cash_quality": ratio(profitability.get_income_quality_ratio, row["operating_cashflow"], row["net_income"]),
                   "free_cashflow": fcf,
                   "free_cashflow_margin": ratio(profitability.get_free_cash_flow_margin, fcf, row["revenue"]),
                   "debt_to_assets": ratio(solvency.get_debt_to_assets_ratio, row["total_debt"], row["total_assets"]),
                   "debt_to_equity": ratio(solvency.get_debt_to_equity_ratio, row["total_debt"], row["total_equity"]),
                   "current_ratio": ratio(liquidity.get_current_ratio, row["current_assets"], row["current_liabilities"])}
        metric_rows.append(metrics)
        previous[row["symbol"]] = row
        if not adjacent:
            unavailable_balances.setdefault(row["symbol"], []).append(row["period"])
        if row["total_equity"] is not None and row["total_equity"] <= 0:
            nonpositive_equity.setdefault(row["symbol"], []).append(row["period"])
        if any(row[field] is None for field in _FINANCIAL_FIELDS[:7]):
            missing_periods.setdefault(row["symbol"], []).append(row["period"])
    for symbol, affected in unavailable_balances.items():
        report["warnings"].append(f"{symbol} 有 {len(affected)} 期缺少连续上期余额，ROE/ROA 保留为空。")
    for symbol, affected in nonpositive_equity.items():
        report["warnings"].append(f"{symbol} 有 {len(affected)} 期股东权益非正，权益类比率不宜横向比较。")
    for symbol, affected in missing_periods.items():
        report["warnings"].append(f"{symbol} 有 {len(affected)} 期缺失财务项目，相关比率保留为空，未用 0 填充。")
    period_ordinals = {row["period"]: row["ordinal"] for row in parsed}
    periods = sorted(period_ordinals, key=period_ordinals.get)
    columns = [("symbol", "企业", "text"), ("period", "报告期", "text"), ("period_end", "实际期末", "date"),
               ("currency", "币种", "text"), ("revenue", "营收（原币）", "number"), ("net_income", "净利润（原币）", "number"),
               ("net_margin", "净利率", "percent"), ("roe", "ROE（本期）", "percent"), ("roa", "ROA（本期）", "percent"),
               ("cash_quality", "经营现金流/净利润", "number"), ("free_cashflow", "自由现金流（原币）", "number"), ("free_cashflow_margin", "自由现金流率", "percent"),
               ("debt_to_assets", "有息负债/资产", "percent"), ("debt_to_equity", "有息负债/权益", "number"), ("current_ratio", "流动比率", "number")]
    latest = periods[-1]
    latest_rows = [row for row in metric_rows if row["period"] == latest]
    basis = "单季" if period_type == "quarterly" else "年度"
    history = _table("financial_history", f"完整{basis}历史", columns, sorted(metric_rows, key=lambda row: (row["period_end"], row["symbol"]), reverse=True))
    history.update(collapsible=True, default_collapsed=True)
    report["tables"] = [_table("latest_comparison", f"{latest} 同期企业比较（{basis}）", columns, latest_rows), history]
    report["summary"] = [{"label": "企业数", "value": len(currencies)}, {"label": "报告期间数", "value": len(periods)},
                         {"label": "最新同口径期间", "value": latest}, {"label": "最新期实际覆盖企业", "value": len(latest_rows)}]
    report["charts"] = []
    for symbol, currency in sorted(currencies.items()):
        history_rows = [row for row in metric_rows if row["symbol"] == symbol]
        largest = max((abs(row[field]) for row in history_rows for field in ("revenue", "net_income") if row[field] is not None), default=0)
        scale, unit = (1e8, "亿 " + currency) if largest >= 1e8 else ((1e6, "百万 " + currency) if largest >= 1e6 else (1, currency))
        report["charts"].append({"key": f"financial_amounts_{symbol}", "title": f"{symbol} {basis}营收与净利润（{unit}）", "type": "line",
                                 "value_type": "number", "unit": unit, "x_key": "period_end",
                                 "series": [{"key": "revenue", "label": "营收"}, {"key": "net_income", "label": "净利润"}],
                                 "data": [{"period": row["period"], "period_end": row["period_end"], "currency": currency,
                                           "revenue": row["revenue"] / scale if row["revenue"] is not None else None,
                                           "net_income": row["net_income"] / scale if row["net_income"] is not None else None} for row in history_rows]})
        report["charts"].append({"key": f"financial_ratios_{symbol}", "title": f"{symbol} {basis}净利率与 ROE", "type": "line",
                                 "value_type": "percent", "x_key": "period_end",
                                 "series": [{"key": "net_margin", "label": "净利率"}, {"key": "roe", "label": "ROE（本期）"}],
                                 "data": [{"period": row["period"], "period_end": row["period_end"], "net_margin": row["net_margin"], "roe": row["roe"]} for row in history_rows]})
    report["charts"].append({"key": "latest_margins", "title": f"{latest} 同期净利率", "type": "bar", "value_type": "percent", "x_key": "symbol",
                             "series": [{"key": "net_margin", "label": "净利率"}], "data": [{"symbol": row["symbol"], "net_margin": row["net_margin"]} for row in latest_rows]})
    report["methodology"] += ["实际调用 FinanceToolkit 的 profitability/solvency/liquidity 纯公式模块；不实例化联网 Toolkit，不使用 FMP/Yahoo 自动数据源。",
                              f"输入为 {'独立单季' if period_type == 'quarterly' else '单年'} 财报发生额，季度 ROE/ROA 是本季数值，未乘 4 年化，也未拿累计季报当单季。",
                              "ROE/ROA 使用相邻期间期初/期末余额均值；缺连续前期或分母不可解释时为空。",
                              "自由现金流=经营现金流−资本支出正额；有息负债/资产不等于总负债/资产。",
                              "保留真实财年期末，不把 3/29 等日期伪映射为 3/31；只比较同一实际期末企业。相邻期末间隔单季70–110天、单年320–400天方可使用连续期平均余额。",
                              "跨币种原始金额不汇总，缺值和零分母不伪造数据。"]
    _financial_warning_summary(report)
    report["sources"].append({"name": "FinanceToolkit", "url": "https://github.com/JerBouma/FinanceToolkit"})
    return report


def analytics_template(tool: str) -> str:
    """CSV examples are explicitly fictional and never automatically executed."""
    stream = io.StringIO()
    writer = csv.writer(stream)
    if tool == "performance":
        writer.writerow(["date", "return", "benchmark_return", "currency", "dataset_note"])
        day, count = date(2025, 1, 2), 0
        while count < 45:
            if day.weekday() < 5:
                writer.writerow([day.isoformat(), [0.005, -0.003, 0.002, -0.004, 0.006][count % 5], [0.003, -0.002, 0.001, -0.003, 0.004][count % 5], "CNY", "虚构演示数据"])
                count += 1
            day += timedelta(days=1)
    elif tool == "portfolio":
        writer.writerow(["date", "symbol", "close", "weight", "currency", "dataset_note"])
        day, count = date(2025, 1, 2), 0
        while count < 65:
            if day.weekday() < 5:
                for symbol, base, weight, phase in (("DEMO_A", 10, 0.6, 0), ("DEMO_B", 20, 0.4, 1)):
                    writer.writerow([day.isoformat(), symbol, round(base + count * 0.02 + math.sin(count / 3 + phase) * 0.3, 4), weight, "CNY", "虚构演示数据"])
                count += 1
            day += timedelta(days=1)
    elif tool == "financials":
        writer.writerow(["symbol", "period", "currency", *_FINANCIAL_FIELDS[:7], "current_assets", "current_liabilities", "dataset_note"])
        for year in (2022, 2023, 2024, 2025):
            for symbol, scale in (("DEMO_A", 1), ("DEMO_B", 2)):
                growth = year - 2022
                writer.writerow([symbol, year, "CNY", (1000 + growth * 100) * scale, (100 + growth * 10) * scale,
                                 (120 + growth * 10) * scale, 30 * scale, (2000 + growth * 100) * scale,
                                 (1000 + growth * 80) * scale, 500 * scale, 800 * scale, 400 * scale, "虚构演示数据"])
    else:
        raise ValueError("没有此工具的数据模板。")
    return stream.getvalue()
