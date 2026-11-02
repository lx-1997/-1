"""Deterministic, field-level checks on typed research tool responses.

Checks describe the returned evidence, not an independent audit of the provider.
No network, model, user text execution, unit conversion or inferred financials.
"""
from __future__ import annotations

import copy
import math
import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

VERSION = "research-evidence-v1"
CORE_TOOLS = frozenset({"get_market_quote", "get_valuation", "get_financials",
                        "get_financial_statements", "get_analyst_consensus"})
_FIELDS = {
    "get_market_quote": ("price", "change_percent"),
    "get_valuation": ("pe_ratio", "pe_dynamic", "forward_pe", "pb_ratio", "market_cap"),
    "get_financials": ("revenue", "net_income", "eps", "revenue_yoy", "profit_yoy", "roe"),
    "get_financial_statements": ("revenue", "net_income", "free_cash_flow", "capex", "roe"),
    "get_analyst_consensus": ("rating", "consensus_rating", "analyst_count", "institution_count",
                              "target_price", "avg_target_price", "forecast_eps_this_year"),
}
_LABELS = {"get_market_quote": "行情", "get_valuation": "估值", "get_financials": "财报",
           "get_financial_statements": "财务三表", "get_analyst_consensus": "卖方观点"}
_POSITIVE = {"price", "market_cap", "target_price", "avg_target_price"}
_COUNTS = {"analyst_count", "institution_count"}
_RATINGS = {"rating", "consensus_rating"}


def _instant(value: Any, naive_zone: str = "UTC") -> datetime | None:
    try:
        result = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        return (result.replace(tzinfo=ZoneInfo(naive_zone)) if result.tzinfo is None else result).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _symbol(value: Any, market: str) -> str:
    text = str(value or "").strip().upper()
    # Only aliases with an explicit market suffix; never strip all letters or
    # collapse two US share classes (GOOG and GOOGL, BRK.A and BRK.B).
    for suffix in {"US": (".US",), "HK": (".HK",), "CN": (".SH", ".SZ", ".BJ")}.get(market, ()):
        if text.endswith(suffix):
            text = text[:-len(suffix)]
    return text.zfill(5) if market == "HK" and text.isdigit() else text


def _market(args: dict) -> str:
    explicit = str(args.get("market") or "").upper()
    if explicit:
        return explicit
    symbol = str(args.get("symbol") or "").upper()
    if symbol.endswith(".HK") or re.fullmatch(r"\d{1,5}", symbol):
        return "HK"
    if re.fullmatch(r"\d{6}(?:\.(?:SH|SZ|BJ))?", symbol):
        return "CN"
    return "US" if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", symbol) else ""


def check_tool_evidence(tool: str, args: dict, result: dict, *, now: datetime | None = None) -> dict:
    """Copy and check a result; rejected facts never reach the answer context.

    Missing metadata is explicit and limits interpretation; it is not invented
    from the current clock, a tool label or the market's usual currency.
    """
    if tool not in CORE_TOOLS:
        return result
    if isinstance(result.get("evidence_check"), dict) and result["evidence_check"].get("version") == VERSION:
        return copy.deepcopy(result)
    checked = copy.deepcopy(result)
    now = now or datetime.now(timezone.utc)
    now = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)
    data = checked.get("data")
    audit = {"version": VERSION, "tool": tool, "label": _LABELS[tool],
             "status": "no_data", "fields": {}, "issues": [], "independently_verified": False}
    checked["evidence_check"] = audit
    if not checked.get("ok"):
        kind = checked.get("error_type")
        audit["status"] = kind if kind in {"timeout", "transient", "not_authorized"} else "unavailable"
        return checked
    if data is None or data == {}:
        return checked
    if not isinstance(data, dict):
        checked["data"] = None
        audit.update(status="rejected", issues=["unexpected_schema"])
        return checked
    row = data
    if tool == "get_market_quote":
        quotes = data.get("quotes")
        if not isinstance(quotes, list) or not quotes:
            return checked
        market = _market(args)
        matches = [q for q in quotes if isinstance(q, dict) and
                   _symbol(q.get("symbol"), market) == _symbol(args.get("symbol"), market)]
        # Do not silently use the first quote when the requested issuer is absent.
        if len(matches) != 1:
            checked["data"] = None
            audit.update(status="rejected", issues=["quote_identity_missing_or_ambiguous"])
            return checked
        data["quotes"] = matches
        row = matches[0]
    market = _market(args)
    symbol = row.get("symbol") or row.get("code")
    issues = audit["issues"]
    hard_errors = []
    if symbol and _symbol(symbol, market) != _symbol(args.get("symbol"), market):
        hard_errors.append("issuer_mismatch")
    if not symbol:
        issues.append("issuer_not_returned")
    currency = row.get("currency")
    # HK financial statements may legitimately use CNY/USD; don't enforce HKD.
    expected = {"US": "USD", "CN": "CNY", "HK": "HKD"}.get(market)
    if tool in {"get_market_quote", "get_valuation", "get_analyst_consensus"} and currency and expected and currency != expected:
        hard_errors.append("currency_mismatch")
    if not currency:
        issues.append("currency_unspecified")
    market_zone = {"CN": "Asia/Shanghai", "HK": "Asia/Hong_Kong", "US": "America/New_York"}.get(market, "UTC")
    for name in ("fetched_at", "published_date", "report_date", "period_start", "market_time"):
        if row.get(name):
            stamp = _instant(row[name], market_zone if name == "market_time" else "UTC")
            if stamp is None:
                hard_errors.append(f"invalid_{name}")
            elif (stamp.date() > now.date() if name in {"published_date", "report_date", "period_start"} else (stamp - now).total_seconds() > 300):
                hard_errors.append(f"future_{name}")
    start, end, publication = (_instant(row.get(k)) for k in ("period_start", "report_date", "published_date"))
    if start and end and start > end:
        hard_errors.append("period_reversed")
    if end and publication and end > publication:
        hard_errors.append("publication_before_period_end")
    if hard_errors:
        checked["data"] = None
        audit.update(status="rejected", issues=issues + hard_errors)
        return checked
    if tool in {"get_financials", "get_financial_statements"}:
        if not end:
            issues.append("report_period_missing")
        elif (now - end).days > 550:
            issues.append("historical_report_only")
        if not row.get("period_basis"):
            issues.append("period_basis_unspecified")
        if not publication:
            issues.append("publication_date_missing_not_point_in_time")
    else:
        observed = _instant(row.get("market_time"), market_zone) or _instant(row.get("fetched_at"))
        if not observed:
            issues.append("as_of_missing")
        elif (now - observed).total_seconds() > (7 * 86400 if tool == "get_market_quote" else 30 * 86400):
            issues.append("historical_snapshot_only")
    if tool == "get_valuation" and row.get("pe_ratio") is not None and not row.get("pe_basis"):
        issues.append("pe_basis_unspecified")
    source = row.get("source_url") or row.get("provider") or row.get("source") or data.get("provider") or checked.get("provider")
    if not source:
        issues.append("source_unspecified")
    audit.update(symbol=symbol, currency=currency, source=source,
                 pe_basis=row.get("pe_basis"),
                 report_date=row.get("report_date"), period_basis=row.get("period_basis"),
                 published_date=row.get("published_date"), fetched_at=row.get("fetched_at"),
                 market_time=row.get("market_time"), is_realtime=row.get("is_realtime") is True)
    present = 0
    for field in _FIELDS[tool]:
        value = row.get(field)
        state = "missing"
        if value is not None and value != "":
            valid = (isinstance(value, str) and len(value) <= 100) if field in _RATINGS else (
                isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
                and (field not in _POSITIVE or value > 0)
                and (field not in _COUNTS or (value >= 0 and int(value) == value)))
            if valid:
                state = "limited" if issues else "usable"
                present += 1
            else:
                state = "rejected"
                row[field] = None
        audit["fields"][field] = {"status": state, "value": row.get(field) if state in {"usable", "limited"} else None}
    audit["status"] = ("limited" if issues else "usable") if present else "no_data"
    audit["limitations"] = ["仅核验返回结构和口径，不等于已与发行人原文独立交叉验证。"]
    if tool == "get_valuation":
        audit["limitations"].append("市值不代表PE齐全；动态、TTM和前瞻PE不得混用；不得用季度EPS年化补PE。")
    if tool == "get_analyst_consensus":
        audit["limitations"].append("评级和覆盖人数不等于盈利一致预期；不得推断缺失的目标价或预测年度。")
    return checked


def evidence_summary(traces: list[dict]) -> dict:
    checks = [trace["evidence_check"] for trace in traces if isinstance(trace.get("evidence_check"), dict)]
    return {"version": VERSION, "checks": checks,
            "answer_rule": "逐项引用可用事实并注明限制；缺估值不抹掉已有财报分析。禁止引用被拒绝字段、把缺失当零、将取数成功称为独立核验。"}
