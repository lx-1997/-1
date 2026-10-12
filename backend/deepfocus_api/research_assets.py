"""Private research on imported asset data and official SEC disclosures.

No trading, academic-only quote feeds, arbitrary URLs, or account persistence.
All weights/returns are decimals; reports preserve incomplete coverage.
"""
from __future__ import annotations

import base64
import binascii
import csv
import io
import json
import math
import os
import re
import threading
import warnings as python_warnings
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date
from typing import Any

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000
_SEC_LOCK = threading.Lock()


def _input(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("输入必须为 JSON 对象")
    try:
        encoded = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError) as exc:
        raise ValueError("输入必须为有效 JSON，数值不可为 NaN/Infinity") from exc
    # A 2 MiB file needs 4/3 of that size after base64 encoding.
    if len(encoded) > MAX_BYTES * 4 // 3 + 65536:
        raise ValueError("输入超过 2 MiB 文件限制")
    if "content_base64" not in data and len(encoded) > MAX_BYTES:
        raise ValueError("JSON 数据超过 2 MiB 限制")
    return data


def _text(value: Any, field: str, *, required=False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise ValueError(f"{field} 必须为文本")
    value = str(value).strip()
    if len(value) > 256 or any(ord(c) < 32 for c in value):
        raise ValueError(f"{field} 文本过长或包含控制字符")
    if required and not value:
        raise ValueError(f"缺少 {field}")
    return value


def _number(value: Any, field: str, *, required=False, minimum=None, positive=False):
    if value is None or value == "":
        if required:
            raise ValueError(f"缺少 {field}")
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} 必须为数值")
    try:
        result = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{field} 必须为数值") from exc
    if not math.isfinite(result) or abs(result) > 1e15:
        raise ValueError(f"{field} 数值无效或超出范围")
    if (minimum is not None and result < minimum) or (positive and result <= 0):
        raise ValueError(f"{field} 数值超出允许范围")
    return result


def _date(value: Any, field: str, *, required=False):
    value = _text(value, field, required=required)
    if not value:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"{field} 使用 YYYY-MM-DD 日期")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError(f"{field} 日期无效") from exc


def _boolean(value: Any, field: str, default=False):
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false", "1", "0"}:
        return value.strip().lower() in {"true", "1"}
    raise ValueError(f"{field} 必须为 true/false")


def _records(value: Any, field: str, *, empty=False) -> list[dict]:
    if not isinstance(value, list) or len(value) > MAX_ROWS or (not value and not empty):
        raise ValueError(f"{field} 必须包含 1 至 {MAX_ROWS} 行")
    if any(not isinstance(row, dict) for row in value):
        raise ValueError(f"{field} 每行必须为对象")
    return value


def _file(data: dict) -> tuple[str, str]:
    name = _text(data.get("file_name"), "file_name", required=True)
    encoded = data.get("content_base64")
    if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_BYTES + 2) // 3):
        raise ValueError("文件必须为有效 base64，且不超过 2 MiB")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("文件 base64 无效") from exc
    if len(raw) > MAX_BYTES:
        raise ValueError("文件超过 2 MiB")
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = raw.decode(encoding)
            if "\x00" in text:
                raise ValueError("只支持文本 CSV/XML")
            return name, text
        except UnicodeDecodeError:
            continue
    raise ValueError("文件编码必须为 UTF-8 或 GB18030")


def _csv(text: str) -> list[dict]:
    try:
        reader = csv.DictReader(io.StringIO(text))
        names = [name.strip() for name in reader.fieldnames] if reader.fieldnames else []
        if not names or any(not name for name in names) or len(set(names)) != len(names) or len(names) > 40:
            raise ValueError("CSV 表头为空、重复或过多")
        reader.fieldnames = names
        rows = []
        for row in reader:
            if None in row or any(v is None for v in row.values()):
                raise ValueError("CSV 行与表头列数不一致")
            if any(v.strip() for v in row.values()):
                rows.append({k.strip(): v.strip() for k, v in row.items()})
            if len(rows) > MAX_ROWS:
                raise ValueError("CSV 超过 5000 行")
        return _records(rows, "CSV")
    except csv.Error as exc:
        raise ValueError("CSV 格式无效") from exc


def _dataset(data: dict, key: str) -> tuple[list[dict], str]:
    if "content_base64" in data:
        if key in data:
            raise ValueError("文件与 JSON 数据不能同时提交")
        name, text = _file(data)
        if not name.lower().endswith(".csv"):
            raise ValueError("请上传 CSV 文件")
        return _csv(text), "用户导入 CSV"
    return _records(data.get(key), key), "用户导入 JSON"


def _table(key, title, columns, rows):
    return {"key": key, "title": title,
            "columns": [{"key": k, "label": label, "type": kind} for k, label, kind in columns],
            "rows": rows}


def _report(tool, title, engine, summary, tables, charts, warnings, methodology, sources):
    result = {"tool": tool, "title": title, "engine": engine, "summary": summary,
              "tables": tables, "charts": charts, "warnings": list(dict.fromkeys(warnings)),
              "methodology": methodology, "sources": sources}
    # Never return NaN/Inf even when an upstream library does.
    try:
        json.dumps(result, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise ValueError("数据计算超出有效数值范围，请核对输入量纲") from exc
    return result


def _weight(value, field):
    result = _number(value, field, required=True, minimum=0)
    if result > 1:
        raise ValueError(f"{field} 使用 0 至 1 小数，不是百分数")
    return result


def _positions(data, key):
    rows = _records(data.get(key, []), key, empty=True)
    totals = defaultdict(float)
    for row in rows:
        symbol = _text(row.get("symbol"), f"{key}.symbol", required=True)
        totals[symbol] += _weight(row.get("weight"), f"{key}.weight")
    return dict(totals)


def analyze_funds(data: dict) -> dict:
    """Disclosed lookthrough, retaining unreported holdings and date mismatch."""
    data = _input(data)
    raw, source_name = _dataset(data, "holdings")
    funds: dict[str, dict] = {}
    seen = set()
    messages = []
    for record in raw:
        fund = _text(record.get("fund"), "fund", required=True)
        symbol = _text(record.get("symbol"), "symbol", required=True)
        as_of = _date(record.get("as_of"), "as_of", required=True)
        weight = _weight(record.get("weight"), "holdings.weight")
        fingerprint = (fund, symbol, as_of, weight)
        if fingerprint in seen:
            raise ValueError(f"{fund}/{symbol} 出现完全重复持仓行，请去重后上传")
        seen.add(fingerprint)
        if fund not in funds:
            funds[fund] = {"as_of": as_of, "holdings": {}, "coverage": 0.0}
        item = funds[fund]
        if item["as_of"] != as_of:
            raise ValueError(f"{fund} 包含多个披露日期，请只选择同一报告期")
        item["coverage"] += weight
        holdings = item["holdings"]
        name = _text(record.get("name"), "name") or symbol
        sector = _text(record.get("sector"), "sector") or "未提供行业"
        if symbol in holdings:
            holdings[symbol]["weight"] += weight
            if holdings[symbol]["sector"] != sector:
                holdings[symbol]["sector"] = "行业标注冲突"
                messages.append(f"{fund}/{symbol} 行业标注不一致")
        else:
            holdings[symbol] = {"symbol": symbol, "name": name, "sector": sector, "weight": weight}
    if len(funds) > 20:
        raise ValueError("最多分析 20 只基金")
    for fund, item in funds.items():
        if item["coverage"] > 1 + 1e-9:
            raise ValueError(f"{fund} 披露持仓权重超过 100%，请核对单位或重复行")
        item["coverage"] = min(1.0, item["coverage"])
        if item["coverage"] < 1 - 1e-9:
            messages.append(f"{fund} 仅披露 {item['coverage']:.1%}，未披露部分保留为未知，不归一化")
    supplied_positions = "positions" in data
    positions = _positions(data, "positions")
    direct = _positions(data, "direct_positions")
    if not supplied_positions:
        # Equal allocation is a comparison scenario, never inferred account holdings.
        remaining = 1 - sum(direct.values())
        if remaining < -1e-9:
            raise ValueError("direct_positions 总权重超过 100%")
        positions = {fund: max(0, remaining) / len(funds) for fund in funds}
        messages.append("未提供基金配置权重，使用等权基金比较情景；这不是账户实际持仓")
    total_allocated = sum(positions.values()) + sum(direct.values())
    if total_allocated > 1 + 1e-9:
        raise ValueError("基金配置与直接持仓总权重超过 100%")
    exposures: dict[str, dict] = {}
    unknown = max(0.0, 1 - total_allocated)
    dates = set()
    coverage_rows = []
    for fund, item in sorted(funds.items()):
        portfolio_weight = positions.get(fund, 0.0)
        unknown += portfolio_weight * (1 - item["coverage"])
        if portfolio_weight > 0:
            dates.add(item["as_of"])
        coverage_rows.append({"fund": fund, "as_of": item["as_of"], "coverage": item["coverage"],
                              "unreported": 1 - item["coverage"], "portfolio_weight": portfolio_weight})
        for symbol, holding in item["holdings"].items():
            exposure = exposures.setdefault(symbol, {"symbol": symbol, "name": holding["name"],
                                                        "weight": 0.0, "fund_weight": 0.0,
                                                        "direct_weight": 0.0, "fund_count": 0})
            contribution = portfolio_weight * holding["weight"]
            exposure["weight"] += contribution
            exposure["fund_weight"] += contribution
            exposure["fund_count"] += int(contribution > 0)
    for fund, weight in positions.items():
        if fund not in funds:
            unknown += weight
            messages.append(f"基金 {fund} 无披露数据，其配置权重计入未知")
    for symbol, weight in direct.items():
        exposure = exposures.setdefault(symbol, {"symbol": symbol, "name": symbol,
                                                    "weight": 0.0, "fund_weight": 0.0,
                                                    "direct_weight": 0.0, "fund_count": 0})
        exposure["weight"] += weight
        exposure["direct_weight"] += weight
    if len(dates) > 1:
        messages.append("配置基金披露日期不同，穿透仅是各期披露拼接，不能称为同日实际持仓")
    overlaps = []
    names = sorted(funds)
    for i, first in enumerate(names):
        for second in names[i + 1:]:
            a, b = funds[first], funds[second]
            compatible = a["as_of"] == b["as_of"]
            overlap = (sum(min(a["holdings"][s]["weight"], b["holdings"][s]["weight"])
                           for s in a["holdings"].keys() & b["holdings"].keys()) if compatible else None)
            if not compatible:
                messages.append(f"{first} 与 {second} 披露日期不同，不计算重合度")
            overlaps.append({"fund_a": first, "fund_b": second, "as_of_a": a["as_of"],
                             "as_of_b": b["as_of"], "overlap": overlap,
                             "status": "披露部分重合下界" if compatible else "日期不可比"})
    exposure_rows = sorted((r for r in exposures.values() if r["weight"] > 0), key=lambda r: -r["weight"])
    charts = [{"key": "lookthrough", "title": "披露证券穿透权重（前 20）", "type": "bar",
               "value_type": "percent",
               "data": exposure_rows[:20], "x_key": "symbol", "series": [{"key": "weight", "label": "权重"}]}]
    return _report("funds", "基金穿透与持仓重合", "Disclosed holdings arithmetic", [
        {"label": "基金数量", "value": len(funds)},
        {"label": "已知证券权重", "value": sum(r["weight"] for r in exposure_rows), "unit": "%"},
        {"label": "未知/未分配权重", "value": min(1.0, unknown), "unit": "%", "tone": "warning"}], [
        _table("coverage", "披露覆盖率", [("fund", "基金", "text"), ("as_of", "披露日期", "date"),
               ("coverage", "披露权重", "percent"), ("unreported", "未披露", "percent"),
               ("portfolio_weight", "配置权重", "percent")], coverage_rows),
        _table("lookthrough", "已披露证券穿透", [("symbol", "证券", "text"), ("name", "名称", "text"),
               ("weight", "总权重", "percent"), ("fund_weight", "基金间接权重", "percent"),
               ("direct_weight", "直接权重", "percent"), ("fund_count", "覆盖基金数", "number")], exposure_rows),
        _table("overlap", "同披露日重合度", [("fund_a", "基金 A", "text"), ("fund_b", "基金 B", "text"),
               ("overlap", "重合权重下界", "percent"), ("status", "可比性", "text"),
               ("as_of_a", "A 披露日", "date"), ("as_of_b", "B 披露日", "date")], overlaps)], charts,
        messages, ["仅使用用户导入的发行人披露或授权持仓，权重为小数。",
                   "穿透权重=基金配置权重×披露证券权重+直接持仓权重。",
                   "重合度=同日披露中同证券 min(基金A权重,基金B权重)之和；部分披露只构成下界。",
                   "未披露、未配置和缺少基金数据的份额保留为未知；不等同于现金或实际实时持仓。"],
        [{"name": source_name, "as_of": item["as_of"], "coverage": f"{fund}: {item['coverage']:.2%}"}
         for fund, item in sorted(funds.items())])


def _schedule_ytm(price: float, flows: list[dict], as_of: str):
    if not isinstance(flows, list) or not 1 <= len(flows) <= 100:
        raise ValueError("cashflows 必须包含 1 至 100 个未来现金流")
    origin = date.fromisoformat(as_of)
    schedule = []
    for flow in flows:
        if not isinstance(flow, dict):
            raise ValueError("cashflows 每行必须为对象")
        day = date.fromisoformat(_date(flow.get("date"), "cashflows.date", required=True))
        amount = _number(flow.get("amount"), "cashflows.amount", required=True, positive=True)
        years = (day - origin).days / 365.0
        if years <= 0:
            raise ValueError("cashflows 只能包含 as_of 之后的正现金流（每百元面值）")
        schedule.append((years, amount))
    def pv(rate):
        total = 0.0
        for years, amount in schedule:
            exponent = -years * math.log1p(rate)
            total += amount * math.exp(min(700, exponent))
        return total
    low, high = -0.999999, 1.0
    if pv(low) < price:
        return None
    while pv(high) > price and high < 1e6:
        high = 2 * high + 1
    if pv(high) > price:
        return None
    for _ in range(120):
        middle = (low + high) / 2
        if pv(middle) > price:
            low = middle
        else:
            high = middle
    return (low + high) / 2


def analyze_bonds(data: dict) -> dict:
    """Per-100-face convertible screening; unknown terms stay unverified."""
    data = _input(data)
    raw, source_name = _dataset(data, "records")
    filters = data.get("filters", {})
    if not isinstance(filters, dict):
        raise ValueError("filters 必须为对象")
    max_price = _number(filters.get("max_price"), "max_price", positive=True)
    max_premium = _number(filters.get("max_premium"), "max_premium")
    min_volume = _number(filters.get("min_volume"), "min_volume", minimum=0)
    min_years = _number(filters.get("min_years_to_maturity"), "min_years_to_maturity", minimum=0)
    exclude_redemption = _boolean(filters.get("exclude_redemption"), "exclude_redemption", True)
    approximate = _boolean(data.get("approximate_ytm"), "approximate_ytm", False)
    messages = []
    rows = []
    seen = set()
    status_alias = {"无": "none", "未强赎": "none", "不强赎": "not_redeem", "公告强赎": "announced",
                    "已公告": "announced", "强赎": "announced", "已触发": "triggered",
                    "待确认": "pending", "已完成": "completed", "未知": "unknown"}
    active_status = {"announced", "triggered", "pending", "completed"}
    allowed_status = active_status | {"none", "not_redeem", "unknown"}
    for record in raw:
        symbol = _text(record.get("symbol"), "symbol", required=True)
        if symbol in seen:
            raise ValueError(f"{symbol} 出现重复证券，请只保留同一时点一行")
        seen.add(symbol)
        as_of = _date(record.get("as_of"), "as_of", required=True)
        price = _number(record.get("price"), "price", required=True, positive=True)
        conversion_price = _number(record.get("conversion_price"), "conversion_price", required=True, positive=True)
        stock_price = _number(record.get("stock_price"), "stock_price", required=True, positive=True)
        conversion_value = stock_price / conversion_price * 100
        premium = price / conversion_value - 1
        years = _number(record.get("years_to_maturity"), "years_to_maturity", minimum=0)
        redemption_price = _number(record.get("redemption_price"), "redemption_price", positive=True)
        volume = _number(record.get("volume"), "volume", minimum=0)
        redemption_date = _date(record.get("redemption_date"), "redemption_date")
        put_date = _date(record.get("put_date"), "put_date")
        status_raw = _text(record.get("redemption_status"), "redemption_status").lower()
        status = status_alias.get(status_raw, status_raw) or "unknown"
        if status not in allowed_status:
            raise ValueError("redemption_status 使用 none/not_redeem/announced/triggered/pending/completed/unknown")
        risks = []
        excluded = []
        if status == "unknown":
            risks.append("强赎状态未核验")
        if status in active_status or redemption_date:
            risks.append("存在强赎事件或日期，须核对发行人公告")
            if exclude_redemption:
                excluded.append("强赎事件")
        if redemption_date and redemption_date <= as_of:
            risks.append("强赎日期已到/已过")
        if put_date:
            risks.append("存在回售日期，须核对窗口与条件" if put_date > as_of else "回售日期已到/已过，须核对条款")
        if years is None:
            risks.append("剩余期限未核验")
        elif years == 0:
            risks.append("已到期")
            excluded.append("已到期")
        if redemption_price is None:
            risks.append("到期兑付价格未核验")
        if volume is None:
            risks.append("成交量未提供")
        if not record.get("rating"):
            risks.append("信用评级未提供")
        if max_price is not None and price > max_price:
            excluded.append("超过最高价格")
        if max_premium is not None and premium > max_premium:
            excluded.append("超过最高溢价率")
        if min_volume is not None and (volume is None or volume < min_volume):
            excluded.append("流动性不足或未知")
        if min_years is not None and (years is None or years < min_years):
            excluded.append("期限不足或未知")
        ytm = None
        ytm_method = "未计算：缺少现金流计划"
        flows = record.get("cashflows")
        if flows is not None:
            ytm = _schedule_ytm(price, flows, as_of)
            ytm_method = "导入现金流计划，ACT/365 毛收益率" if ytm is not None else "现金流解超出数值范围"
        elif approximate and years is not None and years > 0 and redemption_price is not None:
            exponent = math.log(redemption_price / price) / years
            if abs(exponent) <= 700:
                ytm = math.expm1(exponent)
                ytm_method = "简化毛收益率：仅到期兑付，忽略息票/税费/强赎/转股"
        if status in active_status or redemption_date or years == 0:
            ytm = None
            ytm_method = "未计算：强赎事件或到期令原现金流计划不适用"
        rows.append({"symbol": symbol, "name": _text(record.get("name"), "name") or symbol,
                     "as_of": as_of, "price": price, "conversion_value": conversion_value,
                     "premium": premium, "double_low": price + premium * 100,
                     "years_to_maturity": years, "redemption_price": redemption_price,
                     "redemption_status": status, "redemption_date": redemption_date,
                     "put_date": put_date, "rating": _text(record.get("rating"), "rating") or None,
                     "volume": volume, "ytm": ytm, "ytm_method": ytm_method,
                     "risk_flags": "；".join(risks) or "导入字段已核验，仍须核对完整发行条款",
                     "selected": not excluded, "filter_reason": "；".join(excluded) or "满足已设置筛选条件"})
    dates = sorted({row["as_of"] for row in rows})
    if len(dates) > 1:
        messages.append("数据时点不同；双低排名仅为各自快照的混合比较，请统一行情时间")
    if any("未核验" in row["risk_flags"] for row in rows):
        messages.append("缺失条款保留为未核验；满足价格筛选不代表条款安全")
    selected = sorted((row for row in rows if row["selected"]), key=lambda row: row["double_low"])
    rejected = [row for row in rows if not row["selected"]]
    cols = [("symbol", "转债", "text"), ("name", "名称", "text"), ("price", "价格/百元面值", "number"),
            ("conversion_value", "转股价值", "number"), ("premium", "转股溢价率", "percent"),
            ("double_low", "双低值", "number"), ("ytm", "毛年化收益率", "percent"),
            ("ytm_method", "收益率口径", "text"), ("years_to_maturity", "剩余年数", "number"),
            ("volume", "成交量（导入单位）", "number"), ("redemption_status", "强赎状态", "text"),
            ("redemption_date", "强赎日期", "date"), ("put_date", "回售日期", "date"),
            ("risk_flags", "条款风险", "text"), ("filter_reason", "筛选说明", "text"), ("as_of", "数据日期", "date")]
    return _report("bonds", "可转债双低与条款筛选", "Convertible arithmetic / cashflow discounting", [
        {"label": "导入转债", "value": len(rows)}, {"label": "通过筛选", "value": len(selected)},
        {"label": "排除", "value": len(rejected)},
        {"label": "最低双低值", "value": selected[0]["double_low"] if selected else None}], [
        _table("screen", "通过筛选（不构成买入建议）", cols, selected),
        _table("excluded", "排除与原因", cols, rejected)], [
        {"key": "double_low", "title": "通过筛选的双低值（前 20）", "type": "bar", "data": selected[:20],
         "x_key": "symbol", "series": [{"key": "double_low", "label": "双低值"}]}], messages,
        ["仅使用用户导入发行人或授权行情；价格、转股价和兑付金额均以每百元面值计。",
         "转股价值=正股价格÷转股价×100；溢价率=转债价格÷转股价值−1；双低值=价格+溢价率×100。",
         "未提供现金流计划不计算精确 YTM；显式启用近似时，仅算到期兑付的复利毛收益率。",
         "现金流计划使用 ACT/365 年折现，含最后兑付但不自动添加本金；不包含税费、违约或转股收益。",
         "强赎事件默认排除；未知条款保留标记，不能由低双低推断安全。成交量阈值必须与导入单位一致。"],
        [{"name": source_name, "as_of": day, "coverage": "仅导入证券及条款"} for day in dates])


def _xml(text: str):
    if re.search(r"<!\s*(DOCTYPE|ENTITY)\b", text, re.IGNORECASE):
        raise ValueError("XML 不接受 DTD 或实体声明")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError("XML 格式无效，请导入 SEC 原始 XML 文件") from exc
    # Strip namespaces only within this local, already parsed document.
    for element in root.iter():
        element.tag = element.tag.rsplit("}", 1)[-1]
    return root


def _xml_text(node, path, default=""):
    item = node.find(path)
    return (item.text or "").strip() if item is not None else default


def _thirteenf_xml(text: str, data: dict) -> list[dict]:
    root = _xml(text)
    if root.tag != "informationTable":
        raise ValueError("13F 请导入 informationTable XML，并填写报告期与金额单位")
    period = _date(data.get("report_date") or data.get("as_of"), "report_date", required=True)
    filed = _date(data.get("filing_date"), "filing_date")
    if not data.get("value_unit"):
        raise ValueError("13F 信息表缺少金额单位，请核对原文件并设置 value_unit 为 USD 或 USD_thousands")
    rows = []
    for item in root.findall("infoTable"):
        # Validate raw mandatory values before any parser can silently turn them into zero.
        rows.append({"cusip": _xml_text(item, "cusip"), "name": _xml_text(item, "nameOfIssuer"),
                     "security_class": _xml_text(item, "titleOfClass"),
                     "value": _number(_xml_text(item, "value"), "XML.value", required=True, minimum=0),
                     "shares": _number(_xml_text(item, "shrsOrPrnAmt/sshPrnamt"), "XML.shares", required=True, minimum=0),
                     "share_type": _text(_xml_text(item, "shrsOrPrnAmt/sshPrnamtType"), "XML.share_type", required=True),
                     "put_call": _xml_text(item, "putCall"), "report_date": period, "filing_date": filed})
    return _records(rows, "13F XML")


def _form4_xml(text: str, filing_date=None) -> list[dict]:
    root = _xml(text)
    if root.tag != "ownershipDocument" or _xml_text(root, "documentType") != "4":
        raise ValueError("Form 4 请导入 documentType=4 的 ownershipDocument XML")
    owners = "; ".join(_xml_text(item, "reportingOwnerId/rptOwnerName") for item in root.findall("reportingOwner"))
    symbol = _xml_text(root, "issuer/issuerTradingSymbol")
    rows = []
    for derivative, tag in ((False, "nonDerivativeTable/nonDerivativeTransaction"),
                            (True, "derivativeTable/derivativeTransaction")):
        for item in root.findall(tag):
            rows.append({"symbol": symbol, "security_title": _xml_text(item, "securityTitle/value"),
                         "transaction_date": _xml_text(item, "transactionDate/value"),
                         "transaction_code": _xml_text(item, "transactionCoding/transactionCode"),
                         "shares": _xml_text(item, "transactionAmounts/transactionShares/value"),
                         "price": _xml_text(item, "transactionAmounts/transactionPricePerShare/value"),
                         "acquired_disposed": _xml_text(item, "transactionAmounts/transactionAcquiredDisposedCode/value"),
                         "derivative": derivative, "owner": owners, "filing_date": filing_date,
                         "ownership": _xml_text(item, "ownershipNature/directOrIndirectOwnership/value")})
    return _records(rows, "Form 4 交易记录")


def _edgar_module():
    try:
        import edgar
        return edgar
    except ImportError as exc:
        raise RuntimeError("EdgarTools 依赖未安装；请配置研究运行环境，或导入标准化披露 CSV/JSON") from exc


def _form4_library_validate(text: str):
    _edgar_module()
    try:
        from edgar.ownership.table_containers import DerivativeTable, NonDerivativeTable
        from edgar.xmltools import find_element, parse_xml
        root = parse_xml(text)
        # Ownership.from_xml resolves reporting-owner CIKs over the network.
        # Imported files must work offline, so use the actual library's two
        # transaction-table parsers without that unrelated entity lookup.
        for tag, parser in (("nonDerivativeTable", NonDerivativeTable), ("derivativeTable", DerivativeTable)):
            element = find_element(root, tag)
            if element is not None:
                parser.extract(element, form="4")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("EdgarTools 无法解析该 Form 4 XML；请核对官方原文件") from exc


def _sec_source(filing, period, form):
    accession = str(filing.accession_no)
    if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession):
        raise RuntimeError("SEC 返回的申报编号无效")
    cik = int(filing.cik)
    return {"name": f"SEC EDGAR {form} 官方披露", "as_of": period,
            "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{accession}-index.html",
            "coverage": f"申报日期 {filing.filing_date}；仅已公开披露", "currency": "USD"}


def _live_13f(report):
    """Use disaggregated rows so PUT/CALL and principal never get merged by CUSIP."""
    with python_warnings.catch_warnings():
        try:
            from edgar.thirteenf import Ambiguous13FValueUnitWarning
            python_warnings.simplefilter("error", Ambiguous13FValueUnitWarning)
        except ImportError as exc:
            raise RuntimeError("EdgarTools 版本不支持明确 13F 金额归一化；请升级研究环境") from exc
        frame = report.infotable
        if frame is None or frame.empty:
            raise RuntimeError("该 SEC 13F 无可解析持仓信息表；可导入官方 XML/CSV")
        if len(frame) > MAX_ROWS:
            raise ValueError("该 SEC 13F 超过 5000 行，请导入筛选后的披露数据")
        resolution = report.value_unit_resolution
        if resolution.ambiguous and resolution.source != "schema":
            raise RuntimeError("SEC 13F 金额单位存在歧义；请核验原文件并以明确金额单位导入")
        period = _date(str(report.report_period), "SEC.report_date", required=True)
        filed = _date(str(report.filing.filing_date), "SEC.filing_date", required=True)
        result = []
        for row in frame.to_dict("records"):
            result.append({"cusip": row.get("Cusip"), "name": row.get("Issuer"),
                           "security_class": row.get("Class"), "shares": row.get("SharesPrnAmount"),
                           "value": row.get("Value"), "share_type": row.get("Type"),
                           "put_call": row.get("PutCall") or "", "report_date": period, "filing_date": filed})
        return result


def _fetch_sec(data: dict, form: str):
    cik = _text(data.get("cik"), "cik", required=True)
    if not re.fullmatch(r"\d{1,10}", cik) or int(cik) == 0:
        raise ValueError("cik 必须是 1 至 10 位 SEC 实体数字编号")
    identity = os.environ.get("DEEPFOCUS_SEC_IDENTITY") or os.environ.get("SEC_USER_AGENT")
    if not identity or "@" not in identity or "\n" in identity or "\r" in identity:
        raise RuntimeError("SEC 官方查询未配置有效联系身份；请设置 DEEPFOCUS_SEC_IDENTITY 或 SEC_USER_AGENT，或导入官方披露文件")
    edgar = _edgar_module()
    compare = _boolean(data.get("compare_previous"), "compare_previous")
    # EdgarTools identity is global; prevent cross-request mutation during downloads.
    with _SEC_LOCK:
        try:
            edgar.set_identity(identity)
            company = edgar.Company(int(cik))
            filings = company.get_filings(form=form)
            if filings is None or len(filings) == 0:
                raise RuntimeError(f"该 CIK 没有 {form} 官方披露，请核对实体编号")
            filing = filings.latest(1)
            parsed = filing.obj()
            if parsed is None:
                raise RuntimeError("SEC 官方申报无法解析，请导入原始披露文件")
            if form == "13F-HR":
                current = _live_13f(parsed)
                period = current[0]["report_date"]
                sources = [_sec_source(filing, period, form)]
                previous = None
                if compare:
                    prior = parsed.previous_holding_report()
                    if prior is not None:
                        previous = _live_13f(prior)
                        sources.append(_sec_source(prior.filing, previous[0]["report_date"], form))
                return current, previous, sources
            # The library fetches/parses the official filing; normalization preserves
            # individual derivative/non-derivative XML transactions, without market-code guesses.
            xml = filing.xml()
            if not isinstance(xml, str) or len(xml.encode("utf-8")) > MAX_BYTES:
                raise RuntimeError("SEC Form 4 XML 缺失或超过 2 MiB")
            current = _form4_xml(xml, str(filing.filing_date))
            return current, None, [_sec_source(filing, str(filing.filing_date), form)]
        except (ValueError, RuntimeError):
            raise
        except Exception as exc:
            # Never propagate exception URLs/headers/contact identity to clients.
            raise RuntimeError("SEC 官方数据查询失败；请稍后重试或导入官方 XML/CSV。未生成替代数据") from exc


def _normalize_13f(records, unit):
    if unit not in {"USD", "USD_thousands"}:
        raise ValueError("13F value_unit 必须明确为 USD 或 USD_thousands")
    normalized = {}
    periods = set()
    filed_dates = set()
    for row in _records(records, "records"):
        identifier = _text(row.get("cusip") or row.get("identifier") or row.get("symbol"), "cusip/identifier/symbol", required=True)
        if row.get("cusip") and not re.fullmatch(r"[A-Za-z0-9*@#]{9}", identifier):
            raise ValueError("CUSIP 必须是 9 位证券标识")
        identifier = identifier.upper()
        put_call = _text(row.get("put_call"), "put_call").upper()
        if put_call not in {"", "PUT", "CALL"}:
            raise ValueError("put_call 必须为空、PUT 或 CALL")
        share_type = _text(row.get("share_type"), "share_type").upper() or "SH"
        share_type = {"SHARES": "SH", "PRINCIPAL": "PRN"}.get(share_type, share_type)
        if share_type not in {"SH", "PRN"}:
            raise ValueError("share_type 必须为 SH 或 PRN")
        period = _date(row.get("report_date") or row.get("as_of"), "report_date/as_of", required=True)
        filed = _date(row.get("filing_date"), "filing_date")
        if filed and filed < period:
            raise ValueError("filing_date 不得早于 report_date")
        periods.add(period)
        if filed:
            filed_dates.add(filed)
        key = (identifier, put_call, share_type)
        shares = _number(row.get("shares"), "shares", required=True, minimum=0)
        value = _number(row.get("value"), "value", minimum=0)
        if value is not None and unit == "USD_thousands":
            value *= 1000
        item = normalized.setdefault(key, {"identifier": identifier, "name": _text(row.get("name"), "name") or identifier,
                                           "put_call": put_call or "NONE", "share_type": share_type,
                                           "security_class": _text(row.get("security_class"), "security_class"),
                                           "shares": 0.0, "value": 0.0, "report_date": period,
                                           "filing_date": filed, "source_rows": 0})
        if row.get("security_class") and item["security_class"] and row["security_class"] != item["security_class"]:
            raise ValueError(f"{identifier} 同一标识出现不同证券类别，请核对标识")
        item["shares"] += shares
        item["source_rows"] += 1
        item["value"] = item["value"] + value if item["value"] is not None and value is not None else None
    if len(periods) != 1 or len(filed_dates) > 1:
        raise ValueError("13F 每组记录必须属于同一报告期与申报日期")
    return normalized, next(iter(periods))


def _institutions_13f(current, previous, data, sources, engine):
    now, current_period = _normalize_13f(current, data.get("value_unit"))
    prior = {}
    previous_period = None
    messages = ["13F 是历史季度披露，非实时仓位；不包含全部资产、空头及未公开持仓。",
                "期权的申报价值是标的证券价值口径，不是期权权利金、净敞口或账户资产净值。"]
    if previous is not None:
        prior, previous_period = _normalize_13f(previous, data.get("previous_value_unit", data.get("value_unit")))
        if previous_period >= current_period:
            raise ValueError("前期报告期必须早于当前报告期")
        if (date.fromisoformat(current_period) - date.fromisoformat(previous_period)).days > 110:
            messages.append("比较报告期不相邻，差额不是单季度变动")
    elif _boolean(data.get("compare_previous"), "compare_previous"):
        messages.append("前期披露不可用，不计算仓位变动；缺少前期不能视为零持仓")
    complete_value = all(item["value"] is not None for item in now.values())
    total_value = sum(item["value"] for item in now.values()) if complete_value else None
    if total_value is None:
        messages.append("部分持仓价值缺失，披露总价值与价值权重保持空值")
    if any(not item["filing_date"] for item in now.values()):
        messages.append("部分记录未提供申报日期，报告期不能替代实际公开日期")
    sources = [{**source, "as_of": source.get("as_of") or current_period} for source in sources]
    if previous_period and not any(source.get("as_of") == previous_period for source in sources):
        sources.append({"name": "用户导入前期披露", "as_of": previous_period,
                        "coverage": "仅导入前期记录，缺少证券不视为零", "currency": "USD"})
    rows = []
    for key, item in now.items():
        row = dict(item)
        row["weight"] = item["value"] / total_value if total_value and item["value"] is not None else None
        row["previous_shares"] = None
        row["share_change"] = None
        row["previous_value"] = None
        row["value_change"] = None
        row["comparison"] = "未提供前期" if previous is None else "未在前期披露中匹配"
        if key in prior:
            before = prior[key]
            row["previous_shares"] = before["shares"]
            row["share_change"] = item["shares"] - before["shares"]
            row["previous_value"] = before["value"]
            if item["value"] is not None and before["value"] is not None:
                row["value_change"] = item["value"] - before["value"]
            row["comparison"] = "同标识份额增加" if row["share_change"] > 0 else "同标识份额减少" if row["share_change"] < 0 else "同标识份额不变"
        rows.append(row)
    # Prior-only positions remain visible but are never automatically called sales.
    missing_current = [{"identifier": item["identifier"], "put_call": item["put_call"],
                        "share_type": item["share_type"], "previous_shares": item["shares"],
                        "previous_value": item["value"], "comparison": "未在当前披露中匹配；不能推定清仓"}
                       for key, item in prior.items() if key not in now]
    rows.sort(key=lambda row: -(row["value"] or 0))
    return _report("institutions", "SEC 13F 机构历史披露", engine, [
        {"label": "披露报告期", "value": current_period},
        {"label": "比较报告期", "value": previous_period},
        {"label": "分类持仓数", "value": len(rows)},
        {"label": "披露证券总价值（非 NAV）", "value": total_value, "unit": "USD"}], [
        _table("holdings", "历史披露与同标识变动", [("identifier", "证券标识", "text"), ("name", "名称", "text"),
               ("put_call", "期权分类", "text"), ("share_type", "份额单位", "text"), ("shares", "份额/本金", "number"),
               ("value", "披露价值 USD", "number"), ("weight", "披露价值权重", "percent"),
               ("previous_shares", "前期份额", "number"), ("share_change", "份额变化", "number"),
               ("value_change", "价值变化 USD", "number"), ("comparison", "比较状态", "text"),
               ("report_date", "报告期", "date"), ("filing_date", "申报日期", "date")], rows),
        _table("prior_unmatched", "前期存在但当前未匹配", [("identifier", "证券标识", "text"),
               ("put_call", "期权分类", "text"), ("previous_shares", "前期份额", "number"),
               ("previous_value", "前期价值 USD", "number"), ("comparison", "说明", "text")], missing_current)], [], messages,
        ["以证券标识+CUSIP 对应类别、PUT/CALL、SH/PRN 分组汇总多管理人记录，股票与期权不混算。",
         "金额统一为美元；原始导入记录必须明确 USD 或 USD_thousands，不猜测单位。",
         "只对前后均披露的同标识计算变化；缺少某期记录不按零处理，也不推定买入或清仓。",
         "份额变化含转股、拆并股、申报修订等影响，不能等同于已执行买卖；价值差额同时受价格变化影响。"], sources)


def _institutions_form4(records, sources, engine):
    messages = ["Form 4 是已申报内幕人交易，非机构完整持仓；授权、赠与、行权和代扣税不等于公开市场买卖。"]
    rows = []
    dates = set()
    for record in _records(records, "records"):
        transaction_date = _date(record.get("transaction_date"), "transaction_date", required=True)
        filed = _date(record.get("filing_date"), "filing_date")
        if filed and filed < transaction_date:
            raise ValueError("filing_date 不得早于 transaction_date")
        derivative = _boolean(record.get("derivative"), "derivative")
        shares = _number(record.get("shares"), "shares", required=True, minimum=0)
        price = _number(record.get("price"), "price", minimum=0)
        code = _text(record.get("transaction_code"), "transaction_code", required=True).upper()
        if not re.fullmatch(r"[A-Z]", code):
            raise ValueError("transaction_code 必须为 SEC 单字母交易类型")
        direction = _text(record.get("acquired_disposed"), "acquired_disposed", required=True).upper()
        if direction not in {"A", "D"}:
            raise ValueError("acquired_disposed 必须为 A(取得) 或 D(处置)")
        if (code == "P" and direction != "A") or (code == "S" and direction != "D"):
            raise ValueError("P/S 交易类型与 A/D 取得处置方向冲突")
        symbol = _text(record.get("symbol") or record.get("security_title"), "symbol/security_title", required=True)
        market = not derivative and code in {"P", "S"}
        dates.add(transaction_date)
        rows.append({"symbol": symbol, "security_title": _text(record.get("security_title"), "security_title") or symbol,
                     "owner": _text(record.get("owner"), "owner") or "未提供",
                     "transaction_date": transaction_date, "filing_date": filed,
                     "transaction_code": code, "acquired_disposed": direction,
                     "classification": "P/S 买入或卖出（可含私下交易）" if market else "衍生品" if derivative else "非 P/S 交易",
                     "derivative": derivative, "shares": shares, "price": price,
                     "reported_transaction_value": shares * price if price is not None else None,
                     "signed_shares": shares if direction == "A" else -shares,
                     "market_value": shares * price if market and price is not None else None})
    purchases = [row for row in rows if row["classification"].startswith("P/S") and row["transaction_code"] == "P"]
    sales = [row for row in rows if row["classification"].startswith("P/S") and row["transaction_code"] == "S"]
    def total_value(items):
        return sum(row["market_value"] for row in items) if all(row["market_value"] is not None for row in items) else None
    if any(row["price"] is None for row in rows):
        messages.append("部分交易价格缺失或仅在脚注说明，相关金额与汇总保持空值")
    if any(not row["filing_date"] for row in rows):
        messages.append("未提供申报日期，交易日期不能替代 SEC 公开日期")
    sources = [{**source, "as_of": source.get("as_of") or max(dates)} for source in sources]
    return _report("institutions", "SEC Form 4 内幕人披露", engine, [
        {"label": "交易记录", "value": len(rows)}, {"label": "非衍生品 P 买入金额", "value": total_value(purchases), "unit": "USD"},
        {"label": "非衍生品 S 卖出金额", "value": total_value(sales), "unit": "USD"},
        {"label": "衍生品记录", "value": sum(row["derivative"] for row in rows)}], [
        _table("transactions", "逐笔披露：保留 SEC 交易类型", [("symbol", "证券", "text"), ("owner", "申报人", "text"),
               ("security_title", "证券类别", "text"), ("transaction_date", "交易日期", "date"),
               ("filing_date", "申报日期", "date"), ("transaction_code", "SEC 类型", "text"),
               ("acquired_disposed", "A取得/D处置", "text"), ("classification", "分类", "text"),
               ("shares", "份额", "number"), ("price", "披露单价", "number"),
               ("reported_transaction_value", "披露份额×单价 USD", "number")], sorted(rows, key=lambda row: row["transaction_date"], reverse=True))], [], messages,
        ["只汇总非衍生品 P/S 记录金额；SEC P/S 可以包含公开市场或私下交易，不一概称为公开市场成交。",
         "A/D 表示取得/处置；非 P/S 交易与衍生品保留逐笔分类，不能混作净买入。",
         "金额为申报份额×申报价格，不包含费用；缺价格不补零。申报金额不等于当前市场价值。"], sources)


def analyze_institutions(data: dict) -> dict:
    """SEC official query or explicitly supplied disclosure data, never fake fallback."""
    data = _input(data)
    form = _text(data.get("form"), "form", required=True)
    if form not in {"13F-HR", "4"}:
        raise ValueError("form 必须为 13F-HR 或 4")
    if "cik" in data and ("records" in data or "content_base64" in data):
        raise ValueError("请选择 SEC 在线查询或文件/JSON 导入，不能同时提交")
    if "cik" in data:
        current, previous, sources = _fetch_sec(data, form)
        options = {**data, "value_unit": "USD", "previous_value_unit": "USD"}
        engine = "EdgarTools / SEC EDGAR live"
    else:
        previous = data.get("previous_records")
        options = data
        if "content_base64" in data:
            if "records" in data:
                raise ValueError("文件与 JSON 记录不能同时提交")
            name, text = _file(data)
            if name.lower().endswith(".xml"):
                if form == "13F-HR":
                    current = _thirteenf_xml(text, data)
                    engine = "SEC informationTable XML import / disclosure arithmetic"
                else:
                    # Validate safe XML before passing it to the genuine library parser.
                    _form4_xml(text, _date(data.get("filing_date"), "filing_date"))
                    _form4_library_validate(text)
                    current = _form4_xml(text, _date(data.get("filing_date"), "filing_date"))
                    engine = "EdgarTools / SEC Form 4 XML import"
                source_name = "用户导入 SEC XML（未执行在线查询）"
            elif name.lower().endswith(".csv"):
                current = _csv(text)
                source_name = "用户导入披露 CSV（未执行 SEC 在线查询）"
                engine = "Imported disclosure arithmetic"
            else:
                raise ValueError("机构披露文件只支持 CSV 或官方 XML")
        else:
            current = _records(data.get("records"), "records")
            source_name = "用户导入披露 JSON（未执行 SEC 在线查询）"
            engine = "Imported disclosure arithmetic"
        # File-level metadata supplements missing row metadata only. A disclosed
        # row date always wins; never rewrite an official report/transaction date.
        current = [{**row,
                    **({"report_date": data["report_date"]} if form == "13F-HR" and data.get("report_date")
                       and not (row.get("report_date") or row.get("as_of")) else {}),
                    **({"filing_date": data["filing_date"]} if data.get("filing_date") and not row.get("filing_date") else {})}
                   for row in current]
        sources = [{"name": source_name, "coverage": "仅用户导入披露；不代表实时或完整持仓", "currency": "USD"}]
    if form == "13F-HR":
        return _institutions_13f(current, previous, options, sources, engine)
    if previous is not None or _boolean(data.get("compare_previous"), "compare_previous"):
        raise ValueError("Form 4 为逐笔交易，compare_previous 只适用于 13F")
    return _institutions_form4(current, sources, engine)


_SEC_FINANCIAL_CONCEPTS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "RevenueFromContractWithCustomerIncludingAssessedTax", "Revenues", "SalesRevenueNet"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "operating_cashflow": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "total_assets": ["Assets"], "total_equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "current_assets": ["AssetsCurrent"], "current_liabilities": ["LiabilitiesCurrent"],
    "receivables": ["AccountsReceivableNetCurrent"], "inventory": ["InventoryNet"],
}


def _sec_financial_rows(facts, symbol: str, max_periods: int):
    """Normalize actual non-dimensional USD facts; derive only proved YTD deltas."""
    usable = []
    for fact in facts:
        if (fact.unit != "USD" or fact.taxonomy != "us-gaap" or fact.dimensions
                or fact.form_type not in {"10-K", "10-K/A", "10-Q", "10-Q/A"}
                or fact.numeric_value is None or fact.period_end is None):
            continue
        value = _number(fact.numeric_value, "SEC.numeric_value")
        if value is None:
            continue
        usable.append({"concept": fact.concept.rsplit(":", 1)[-1], "value": value,
                       "start": fact.period_start, "end": fact.period_end,
                       "filed": fact.filing_date, "accession": fact.accession})
    if not usable:
        raise RuntimeError(f"{symbol} 无支持的 SEC US-GAAP 美元财务事实；请导入发行人财务数据")
    by_concept = defaultdict(list)
    for fact in usable:
        by_concept[fact["concept"]].append(fact)
    def newest(rows):
        return max(rows, key=lambda row: (row["filed"] or date.min, row["accession"])) if rows else None
    def provenance(fact, method):
        return {"concept": fact["concept"], "period_start": fact["start"].isoformat() if fact["start"] else None,
                "period_end": fact["end"].isoformat(), "filing_date": fact["filed"].isoformat() if fact["filed"] else None,
                "accession": fact["accession"], "method": method}
    anchors = [fact for fact in usable if fact["concept"] in _SEC_FINANCIAL_CONCEPTS["revenue"] + _SEC_FINANCIAL_CONCEPTS["net_income"]
               and fact["start"] and 70 <= (fact["end"] - fact["start"]).days + 1 <= 400]
    ends = sorted({fact["end"] for fact in anchors})
    periods = []
    for end in ends:
        direct = [fact for fact in anchors if fact["end"] == end and 70 <= (end - fact["start"]).days + 1 <= 110]
        if direct:
            # All quarter facts should describe one boundary. Do not use an arbitrary
            # shorter segment duration to split cumulative financial statements.
            starts = {fact["start"] for fact in direct}
            if len(starts) != 1:
                continue
            start = next(iter(starts))
        else:
            previous_ends = [prior for prior in ends if 70 <= (end - prior).days <= 110]
            if not previous_ends:
                continue
            from datetime import timedelta
            start = max(previous_ends) + timedelta(days=1)
        periods.append((start, end))
    messages = []
    def duration(concepts, start, end):
        for concept in concepts:
            concept_rows = by_concept[concept]
            direct = newest([row for row in concept_rows if row["start"] == start and row["end"] == end])
            if direct:
                return direct["value"], provenance(direct, "官方单季事实")
            # Same concept, same fiscal start, immediately preceding quarter boundary.
            cumulative = [row for row in concept_rows if row["start"] and row["start"] < start and row["end"] == end
                          and (end - row["start"]).days + 1 <= 400]
            if cumulative:
                current = newest(cumulative)
                candidates = [row for row in concept_rows if row["start"] == current["start"]
                              and row["end"] < end and (start - row["end"]).days == 1]
                # A later restatement of Q1 cannot be subtracted from an older Q2
                # cumulative filing. Prefer the same filing; otherwise retain only
                # the preceding version already public when this YTD was filed.
                same_filing = [row for row in candidates if row["accession"] and row["accession"] == current["accession"]]
                contemporaneous = [row for row in candidates if row["filed"] is not None
                                   and current["filed"] is not None and row["filed"] <= current["filed"]]
                previous = newest(same_filing) or newest(contemporaneous)
                if previous:
                    detail = provenance(current, "同概念同财年累计差分为单季（前期版本截至本期申报）")
                    detail["subtract_accession"] = previous["accession"]
                    detail["subtract_period_end"] = previous["end"].isoformat()
                    detail["subtract_filing_date"] = previous["filed"].isoformat() if previous["filed"] else None
                    detail["same_filing"] = previous["accession"] == current["accession"]
                    if not detail["same_filing"]:
                        messages.append(f"{symbol} {end} {concept} 使用本期申报时已公开的前期累计差分；来源为不同申报，请核对重述与会计政策")
                    return current["value"] - previous["value"], detail
                messages.append(f"{symbol} {end} {concept} 缺少本期申报时可核对的前期版本，未将晚期重述混入累计差分")
        return None, None
    def instant(concepts, end):
        for concept in concepts:
            fact = newest([row for row in by_concept[concept] if row["start"] is None and row["end"] == end])
            if fact:
                return fact["value"], provenance(fact, "官方期末事实")
        return None, None
    rows = []
    for start, end in periods[-max_periods:]:
        record = {"symbol": symbol, "period": end.isoformat(), "period_start": start.isoformat(),
                  "period_end": end.isoformat(), "currency": "USD", "provenance": {}}
        for field, concepts in _SEC_FINANCIAL_CONCEPTS.items():
            value, details = duration(concepts, start, end) if field in {"revenue", "net_income", "operating_cashflow", "capex"} else instant(concepts, end)
            if field == "capex" and value is not None and value < 0:
                value = None
                messages.append(f"{symbol} {end} 资本支出为负，可能含净回款，保留缺失以免误作支出")
            record[field] = value
            if details:
                record["provenance"][field] = details
        # Never substitute liabilities for interest-bearing debt or missing components for zero.
        current_debt, current_detail = instant(["DebtCurrent"], end)
        noncurrent_debt, noncurrent_detail = instant(["LongTermDebtNoncurrent"], end)
        debt_details = []
        if current_debt is not None and noncurrent_debt is not None:
            total_debt = current_debt + noncurrent_debt
            debt_details = [current_detail, noncurrent_detail]
        else:
            short_debt, short_detail = instant(["ShortTermBorrowings", "CommercialPaper"], end)
            current_long, current_long_detail = instant(["LongTermDebtCurrent"], end)
            if all(value is not None for value in (short_debt, current_long, noncurrent_debt)):
                total_debt = short_debt + current_long + noncurrent_debt
                debt_details = [short_detail, current_long_detail, noncurrent_detail]
            else:
                total_debt = None
        record["total_debt"] = total_debt
        if debt_details:
            record["provenance"]["total_debt"] = {"method": "有息债务已披露组成之和", "components": debt_details}
        rows.append(record)
    if not rows:
        raise RuntimeError(f"{symbol} 没有可核对的完整单季日期边界；请导入实际季度财务数据")
    if len(rows) < max_periods:
        messages.append(f"{symbol} 仅有 {len(rows)} 个可核对季度，未补造 {max_periods} 期")
    missing = sorted({field for row in rows for field in list(_SEC_FINANCIAL_CONCEPTS) + ["total_debt"] if row[field] is None})
    if missing:
        messages.append(f"{symbol} 部分指标缺失或不支持：{', '.join(missing)}；保持空值，不以负债总额代有息债务")
    return rows, messages


def fetch_sec_financial_records(symbols: list[str], max_periods: int = 8) -> dict:
    """Official SEC live facts for financials; returns an input dataset, not a report."""
    if not isinstance(symbols, list) or not 1 <= len(symbols) <= 5:
        raise ValueError("SEC 在线财务查询一次支持 1 至 5 个美股代码")
    if isinstance(max_periods, bool) or not isinstance(max_periods, int) or not 1 <= max_periods <= 8:
        raise ValueError("max_periods 必须为 1 至 8")
    normalized_symbols = []
    for value in symbols:
        symbol = _text(value, "symbols", required=True).upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", symbol):
            raise ValueError("symbols 必须为美股代码，不接受 URL")
        if symbol in normalized_symbols:
            raise ValueError("symbols 不得重复")
        normalized_symbols.append(symbol)
    identity = os.environ.get("DEEPFOCUS_SEC_IDENTITY") or os.environ.get("SEC_USER_AGENT")
    if not identity or "@" not in identity or "\n" in identity or "\r" in identity:
        raise RuntimeError("SEC 在线财务未配置联系身份；请配置 SEC_USER_AGENT 或导入发行人财务文件")
    edgar = _edgar_module()
    records, sources, messages = [], [], []
    with _SEC_LOCK:
        try:
            edgar.set_identity(identity)
            for symbol in normalized_symbols:
                company = edgar.Company(symbol)
                facts = company.get_facts()
                if facts is None:
                    raise RuntimeError(f"{symbol} 没有 SEC 财务事实")
                rows, notes = _sec_financial_rows(facts, symbol, max_periods)
                records.extend(rows)
                messages.extend(notes)
                sources.append({"name": f"SEC EDGAR {symbol} 公司财务事实", "currency": "USD",
                                "url": f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(company.cik):010d}.json",
                                "as_of": rows[-1]["period_end"], "coverage": f"{len(rows)} 个实际季度；指标可能缺失"})
        except (ValueError, RuntimeError):
            raise
        except Exception as exc:
            raise RuntimeError("SEC 官方财务查询失败；请稍后重试或导入发行人/授权财务文件，未生成替代数据") from exc
    return {"records": records, "period_type": "quarterly", "sources": sources, "warnings": messages,
            "methodology": ["EdgarTools 从 SEC 官方 Company Facts 读取 US-GAAP 美元事实，不使用付费/学术限定行情源。",
                            "保留实际财年起止日；单季区间 70 至 110 天，只有同概念同财年累计区间可证明衔接时才差分。",
                            "本期使用最新已披露版本，累计差分只采用同申报或截至本期申报已公开的前期版本；跨申报须核对重述，不是历史时点回测数据。未披露指标保持空值，最多实际 8 季。",
                            "有息债务只加明确的现有披露组成，不以 liabilities 代替；资本支出为正支出，缺失不补零。"]}
