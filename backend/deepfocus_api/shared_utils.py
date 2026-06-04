from __future__ import annotations

import math
import re
from datetime import date, datetime, timezone
from html import unescape
from typing import Any, Iterable, Optional


def to_float(value: object) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if math.isnan(value) or math.isinf(value):
            return None
        return float(value)
    text = str(value).strip()
    if not text or text in {"--", "N/A", "nan", "None", "null", "n/a", "-"}:
        return None
    text = text.replace("$", "").replace(",", "").replace("%", "")
    is_negative = text.startswith("(") and text.endswith(")")
    if is_negative:
        text = text[1:-1]
    try:
        parsed = float(text)
    except ValueError:
        return None
    if is_negative:
        parsed = -parsed
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def safe_error(exc: Exception) -> str:
    import httpx

    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    text = str(exc).strip()
    return text[:240] or exc.__class__.__name__


def dedupe(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def utc_now_dt() -> datetime:
    return datetime.now(timezone.utc)


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def safe_int(
    value: Any,
    default: Optional[int] = None,
    *,
    low: Optional[int] = None,
    high: Optional[int] = None,
) -> Optional[int]:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        if default is not None:
            number = default
        else:
            return None
    if low is not None and number < low:
        return low
    if high is not None and number > high:
        return high
    return number


def safe_float(
    value: Any,
    default: Optional[float] = None,
    *,
    low: Optional[float] = None,
    high: Optional[float] = None,
) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        if default is not None:
            number = default
        else:
            return None
    if low is not None and number < low:
        return low
    if high is not None and number > high:
        return high
    return number


def clean_title(value: str) -> str:
    text = unescape(value)
    text = re.sub(r"</?em>", "", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("\u3000", " ").replace("&nbsp;", " ")
    return re.sub(r"\s+", "", text).strip()


def parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def display_value(value: str) -> str:
    return value.strip() if value and value.strip() else "\u672a\u8bc6\u522b"


def join_nonempty(values: list[str], *, separator: str) -> str:
    return separator.join(value for value in values if value)


def short_text(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) > limit:
        return f"{text[:limit]}..."
    return text


def num(value: Any, scale: float = 1.0) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "\u2014", "\u2013"}:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    return round(float(match.group(0)) * scale, 3)


def pct_change(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    if current is None or previous in (None, 0):
        return None
    return round((current - previous) / abs(previous) * 100, 3)


def fmt_usd(value: Optional[float], signed: bool = False) -> str:
    if value is None:
        return "\u2014"
    prefix = "+" if signed and value > 0 else "-" if value < 0 else ""
    abs_value = abs(value)
    if abs_value >= 1_000_000_000:
        return f"{prefix}${abs_value / 1_000_000_000:.2f}B"
    if abs_value >= 1_000_000:
        return f"{prefix}${abs_value / 1_000_000:.2f}M"
    if abs_value >= 1_000:
        return f"{prefix}${abs_value / 1_000:.1f}K"
    return f"{prefix}${abs_value:.0f}"


def fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return "\u2014"
    return f"{'+' if value > 0 else ''}{value:.1f}%"


def profit_factor(total_wins: float, total_losses: float, *, cap: float = 999.99) -> float:
    """\u76c8\u4e8f\u6bd4 = \u603b\u76c8\u5229 / \u603b\u4e8f\u635f\u3002\u65e0\u4e8f\u635f\u4f46\u6709\u76c8\u5229\u65f6\u8fd4\u56de\u5c01\u9876\u503c\uff08\u221e \u4ee3\u7406\uff0c\u907f\u514d\u663e\u793a 0 \u88ab\u8bef\u8bfb\u4e3a\u65e0\u76c8\u5229\uff09\uff0c
    \u5168\u4e3a 0 \u65f6\u8fd4\u56de 0\uff1b\u4e0a\u754c\u7edf\u4e00\u94b3\u5236\u5230 cap\u3002total_losses \u53d6\u7edd\u5bf9\u503c\uff0c\u8c03\u7528\u65b9\u53ef\u4f20\u6709\u7b26\u53f7\u6216\u65e0\u7b26\u53f7\u503c\u3002"""
    losses = abs(total_losses)
    if losses > 0:
        return round(min(total_wins / losses, cap), 2)
    return cap if total_wins > 0 else 0.0
