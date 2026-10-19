"""Deterministic stock factors from validated Eastmoney observations.

This module performs no I/O.  The chip model is an estimate from turnover and
prices, not a reconstruction of shareholder accounts or a prediction model.
"""
from __future__ import annotations

from datetime import date, datetime
import math
import re
from typing import Any
from zoneinfo import ZoneInfo


MIN_CHIP_DAYS = 120
MIN_CHIP_COVERAGE = 0.80
PRICE_BINS = 201
MAX_AGE_DAYS = 7
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_CAPITAL_METHOD = (
    "自定义资金规则指数：50 + 50 × tanh(最近5个有效交易日主力净流入合计 / 200000000元)。"
    "指数不是上涨概率；净额币种CNY，以元计；至少5个不同日期，最新数据距今不超过7个自然日。"
)
_CHIP_METHOD = (
    "换手衰减筹码分布模型（自定义估算）：fqt=1前复权日线；201个等宽价格区间；"
    "每日新增筹码按[最低价,最高价]三角分布，众数为(开+收+高+低)/4；"
    "旧筹码乘(1-历史当日换手率)，新筹码权重为历史当日换手率。"
    "历史残余=逐日(1-换手率)连乘；获利比例与成本分位数按已覆盖筹码归一化估算。"
    "90%集中度=(95%成本分位-5%成本分位)/(95%成本分位+5%成本分位)。"
    "结构规则分=40×max(0,1-90%集中度/0.20)"
    "+35×max(0,1-|收盘价/中位成本-1|/0.20)+25×(1-上方筹码比例)。"
    "得分偏好成本集中、现价接近中位成本及上方压力较低；不是预测胜率，非真实账户持仓；盘中日线为取数时点的未收盘数据。"
    "至少120个有效日线且估算覆盖率≥80%，最新数据距今不超过7个自然日才评分。"
)


def _today(now: datetime | date | str) -> date:
    if isinstance(now, datetime):
        return now.astimezone(_SHANGHAI).date() if now.tzinfo else now.date()
    if isinstance(now, date):
        return now
    return _day(now)


def _day(value: Any) -> date:
    if isinstance(value, datetime):
        return value.astimezone(_SHANGHAI).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("invalid trading date")
    return date.fromisoformat(value)


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"invalid {field}")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"invalid {field}") from exc
    if not math.isfinite(number):
        raise ValueError(f"non-finite {field}")
    return number


def _payload_lines(payload: Any, symbol: str) -> list:
    if not isinstance(symbol, str) or not re.fullmatch(r"\d{6}", symbol):
        raise ValueError("symbol must contain exactly six digits")
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict) or str(data.get("code", "")) != symbol:
        raise ValueError("Eastmoney response stock code mismatch")
    lines = data.get("klines")
    if not isinstance(lines, list):
        raise ValueError("missing Eastmoney klines")
    return lines


def _unique_rows(rows: list[dict], today: date) -> list[dict]:
    indexed: dict[str, dict] = {}
    for row in rows:
        day = _day(row["date"])
        if day > today:
            raise ValueError("future trading date")
        normalized = {**row, "date": day.isoformat()}
        old = indexed.get(normalized["date"])
        if old is not None and old != normalized:
            raise ValueError("conflicting duplicate trading date")
        indexed[normalized["date"]] = normalized
    return [indexed[key] for key in sorted(indexed)]


def parse_daily_bars(payload: Any, symbol: str, now: datetime | date | str) -> list[dict]:
    """Parse fields f51..f61; reject the response if any row is invalid.

    Dates are canonical YYYY-MM-DD strings and observations are ascending.
    Identical duplicate dates are harmless; conflicting duplicates fail closed.
    """
    rows = []
    for line in _payload_lines(payload, symbol):
        fields = line.split(",") if isinstance(line, str) else []
        if len(fields) < 11:
            raise ValueError("incomplete daily bar")
        values = [_number(value, f"f{52 + i}") for i, value in enumerate(fields[1:11])]
        opening, close, high, low, volume, amount, _, _, _, turnover = values
        if low <= 0 or not low <= min(opening, close) <= max(opening, close) <= high:
            raise ValueError("invalid OHLC range")
        if volume < 0 or amount < 0 or not 0 <= turnover <= 100:
            raise ValueError("invalid volume, amount or turnover")
        rows.append({"date": fields[0], "open": opening, "close": close,
                     "high": high, "low": low, "volume": volume,
                     "amount": amount, "turnover": turnover})
    return _unique_rows(rows, _today(now))


def parse_flow_bars(payload: Any, symbol: str, now: datetime | date | str) -> list[dict]:
    """Parse f51=date and f52=main net flow (CNY yuan), retaining real zeros."""
    rows = []
    for line in _payload_lines(payload, symbol):
        fields = line.split(",") if isinstance(line, str) else []
        if len(fields) < 2:
            raise ValueError("incomplete capital flow bar")
        rows.append({"date": fields[0], "main_net": _number(fields[1], "main_net")})
    return _unique_rows(rows, _today(now))[-5:]


def _report(kind: str, method: str, rows: list[dict] | None = None) -> dict:
    rows = rows or []
    return {"status": "missing", "score": None, "label": "数据不足", "summary": "等待有效数据",
            "data_date": rows[-1]["date"] if rows else None,
            "date_range": [rows[0]["date"], rows[-1]["date"]] if rows else [],
            "source": "东方财富历史资金流" if kind == "capital" else "东方财富前复权日线（fqt=1）",
            "method": method, "metrics": [], "model": True, "warnings": []}


def _invalid(kind: str, method: str, error: Exception) -> dict:
    report = _report(kind, method)
    report.update(status="invalid", label="数据校验未通过", summary="源数据未通过校验，暂不评分")
    report["warnings"] = [str(error)]
    return report


def _metric(key: str, label: str, value: Any, unit: str = "") -> dict:
    return {"key": key, "label": label, "value": value, "unit": unit}


def score_capital(rows: list[dict], now: datetime | date | str) -> dict:
    """Score five validated observations; no missing-data neutral default."""
    try:
        today = _today(now)
        clean = _unique_rows([
            {"date": row["date"], "main_net": _number(row["main_net"], "main_net")}
            for row in rows
        ], today)[-5:]
        total = math.fsum(row["main_net"] for row in clean)
        if not math.isfinite(total):
            raise ValueError("capital flow aggregate is not finite")
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        return _invalid("capital", _CAPITAL_METHOD, exc)
    report = _report("capital", _CAPITAL_METHOD, clean)
    report["metrics"] = [_metric("net_5d", "近5个有效交易日主力净额", total, "元"),
                         _metric("positive_days", "主力净流入天数", sum(row["main_net"] > 0 for row in clean), "天"),
                         _metric("sample_days", "有效观测日", len(clean), "天")]
    report["currency"] = "CNY"
    if not clean:
        return report
    if (today - _day(clean[-1]["date"])).days > MAX_AGE_DAYS:
        report.update(status="stale", label="数据已过期", summary="最新资金数据超过7个自然日，暂不评分")
        report["warnings"].append("stale: latest observation is more than 7 calendar days old")
        return report
    if len(clean) < 5:
        report["summary"] = f"仅有{len(clean)}个有效交易日，至少5日才评分"
        return report
    score = round(50 + 50 * math.tanh(total / 200_000_000), 1)
    report.update(status="ok", score=score,
                  label="资金偏流入" if score >= 60 else "资金偏流出" if score <= 40 else "资金流向中性",
                  summary=f"近5个有效交易日主力净额{total / 100_000_000:.2f}亿元，规则指数{score:.1f}")
    return report


def _triangle_cdf(price: float, low: float, high: float, mode: float) -> float:
    if price <= low:
        return 0.0
    if price >= high:
        return 1.0
    if price <= mode and mode > low:
        return ((price - low) / (high - low)) * ((price - low) / (mode - low))
    return 1 - ((high - price) / (high - low)) * ((high - price) / (high - mode))


def _daily_mass(edges: list[float], low: float, high: float, mode: float) -> list[float]:
    """Integrate the triangle over each price bin, preserving total mass."""
    count = len(edges) - 1
    if high == low:
        mass = [0.0] * count
        index = min(count - 1, max(0, int((low - edges[0]) / (edges[-1] - edges[0]) * count))) if edges[-1] > edges[0] else count // 2
        mass[index] = 1.0
        return mass
    cdf = [_triangle_cdf(edge, low, high, mode) for edge in edges]
    mass = [max(0.0, cdf[index + 1] - cdf[index]) for index in range(count)]
    total = math.fsum(mass)
    if total <= 0:
        raise ValueError("empty daily price distribution")
    return [value / total for value in mass]


def _quantile(prices: list[float], weights: list[float], target: float) -> float:
    running = 0.0
    for price, weight in zip(prices, weights):
        running += weight
        if running >= target:
            return price
    return prices[-1]


def _validated_daily_rows(bars: list[dict], today: date) -> list[dict]:
    clean = []
    for row in bars:
        values = {key: _number(row[key], key) for key in ("open", "close", "high", "low", "volume", "amount", "turnover")}
        if values["low"] <= 0 or not values["low"] <= min(values["open"], values["close"]) <= max(values["open"], values["close"]) <= values["high"]:
            raise ValueError("invalid OHLC range")
        if values["volume"] < 0 or values["amount"] < 0 or not 0 <= values["turnover"] <= 100:
            raise ValueError("invalid volume, amount or turnover")
        clean.append({"date": row["date"], **values})
    return _unique_rows(clean, today)


def score_chips(bars: list[dict], now: datetime | date | str) -> dict:
    """Estimate turnover-decayed costs and an explicitly custom structure score."""
    try:
        today = _today(now)
        clean = _validated_daily_rows(bars, today)
        report = _report("chips", _CHIP_METHOD, clean)
        if not clean:
            return report
        low, high = min(row["low"] for row in clean), max(row["high"] for row in clean)
        span = high - low
        edges = [low + span * (index / PRICE_BINS) for index in range(PRICE_BINS + 1)]
        prices = [low + span * ((index + 0.5) / PRICE_BINS) for index in range(PRICE_BINS)]
        mass, residual = [0.0] * PRICE_BINS, 1.0
        for row in clean:
            turnover = row["turnover"] / 100
            # Dividing each component first avoids overflowing a finite price sum.
            mode = row["open"] / 4 + row["close"] / 4 + row["high"] / 4 + row["low"] / 4
            new_mass = _daily_mass(edges, row["low"], row["high"], mode)
            mass = [old * (1 - turnover) + new * turnover for old, new in zip(mass, new_mass)]
            residual *= 1 - turnover
        coverage = 1 - residual
        report["metrics"] = [_metric("sample_days", "有效日线", len(clean), "天"),
                             _metric("coverage", "估算覆盖率", round(coverage * 100, 3), "%"),
                             _metric("historical_residual", "未覆盖历史残余", round(residual * 100, 3), "%")]
        # Raw weights sum to observed coverage.  Unknown initial history is separate.
        report["distribution"] = [{"price": price, "weight": weight} for price, weight in zip(prices, mass)]
        report["coverage"] = coverage
        report["historical_residual"] = residual
        total = math.fsum(mass)
        if total > 0:
            weights = [weight / total for weight in mass]
            q05, q15, median, q85, q95 = [_quantile(prices, weights, q) for q in (0.05, 0.15, 0.50, 0.85, 0.95)]
            close = clean[-1]["close"]
            winners = math.fsum(weight for price, weight in zip(prices, weights) if price <= close)
            overhead = max(0.0, min(1.0, 1 - winners))
            concentration = (q95 - q05) / q95 / (1 + q05 / q95)
            near_cost = max(0.0, 1 - abs(close / median - 1) / 0.20)
            density = max(0.0, 1 - concentration / 0.20)
            score = min(100.0, max(0.0, 40 * density + 35 * near_cost + 25 * (1 - overhead)))
            if not all(math.isfinite(value) for value in (q05, q15, median, q85, q95, winners, concentration, score)):
                raise ValueError("non-finite chip model output")
            report["metrics"].extend([
                _metric("estimated_profit_ratio", "估算获利筹码比例", round(winners * 100, 2), "%"),
                _metric("cost_70", "70%估算成本区间", [round(q15, 4), round(q85, 4)], "元"),
                _metric("cost_90", "90%估算成本区间", [round(q05, 4), round(q95, 4)], "元"),
                _metric("median_cost", "估算中位成本", round(median, 4), "元"),
                _metric("concentration_90", "90%估算集中度", round(concentration * 100, 2), "%"),
                _metric("overhead_ratio", "估算上方筹码比例", round(overhead * 100, 2), "%"),
            ])
        else:
            score = None
        report["warnings"].append("筹码由历史价格与换手率建模估算，非真实持仓账户；不含未覆盖历史成本分布。")
        if (today - _day(clean[-1]["date"])).days > MAX_AGE_DAYS:
            report.update(status="stale", label="数据已过期", summary="最新日线超过7个自然日，暂不评分")
            report["warnings"].append("stale: latest observation is more than 7 calendar days old")
        elif len(clean) < MIN_CHIP_DAYS or coverage < MIN_CHIP_COVERAGE or score is None:
            report.update(summary=f"有效{len(clean)}日，估算覆盖{coverage:.1%}；需至少120日且覆盖80%才评分")
        else:
            rounded = round(score, 1)
            report.update(status="ok", score=rounded,
                          label="结构较集中" if rounded >= 80 else "结构适中" if rounded >= 60 else "结构偏弱",
                          summary=f"筹码结构规则指数{rounded:.1f}，估算覆盖率{coverage:.1%}")
        return report
    except (ValueError, KeyError, TypeError, OverflowError, ZeroDivisionError) as exc:
        return _invalid("chips", _CHIP_METHOD, exc)
