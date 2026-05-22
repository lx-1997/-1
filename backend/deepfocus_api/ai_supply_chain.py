from __future__ import annotations

import re
import asyncio
import csv
import io
from datetime import date, datetime, timedelta, timezone
from html import unescape
from typing import Any, Optional

import httpx

FED_G17_TABLE_URL = "https://www.federalreserve.gov/releases/g17/current/table2_sup.htm"
FRED_G17_ELECTRONICS_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=CAPUTLG334S"
FRED_G17_SEMICONDUCTOR_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=CAPUTLG3344S"
TRENDFORCE_AI_SERVER_URL = "https://www.trendforce.com/research/category/Semiconductors/AI%20Server_HBM_Server"

FALLBACK_OFFICIAL_MONTHLY = {
    "electronics": {
        "2025-05": 74.4,
        "2025-06": 73.9,
        "2025-07": 75.1,
        "2025-08": 74.5,
        "2025-09": 74.0,
        "2025-10": 74.7,
        "2025-11": 74.7,
        "2025-12": 74.1,
        "2026-01": 74.9,
        "2026-02": 74.7,
        "2026-03": 74.7,
        "2026-04": 75.4,
    },
    "semiconductor": {
        "2025-05": 78.0,
        "2025-06": 76.2,
        "2025-07": 78.9,
        "2025-08": 76.5,
        "2025-09": 73.7,
        "2025-10": 74.1,
        "2025-11": 73.1,
        "2025-12": 72.3,
        "2026-01": 74.3,
        "2026-02": 72.9,
        "2026-03": 72.2,
        "2026-04": 72.2,
    },
}

DELIVERY_OBSERVATIONS = {
    "2026-02-23": {"cowos": 44, "hbm": 44, "optical": 36, "power": 58, "wafer": 28, "substrate": 34, "pcb": 26, "ssd": 20, "rack": 28},
    "2026-03-02": {"cowos": 45, "hbm": 45, "optical": 37, "power": 59, "wafer": 29, "substrate": 35, "pcb": 27, "ssd": 21, "rack": 29},
    "2026-03-09": {"cowos": 46, "hbm": 46, "optical": 39, "power": 60, "wafer": 30, "substrate": 36, "pcb": 28, "ssd": 22, "rack": 30},
    "2026-03-16": {"cowos": 47, "hbm": 48, "optical": 41, "power": 61, "wafer": 31, "substrate": 37, "pcb": 29, "ssd": 23, "rack": 31},
    "2026-03-23": {"cowos": 48, "hbm": 49, "optical": 43, "power": 62, "wafer": 32, "substrate": 38, "pcb": 30, "ssd": 24, "rack": 32},
    "2026-03-30": {"cowos": 48, "hbm": 50, "optical": 44, "power": 62, "wafer": 32, "substrate": 39, "pcb": 31, "ssd": 25, "rack": 33},
    "2026-04-06": {"cowos": 49, "hbm": 50, "optical": 46, "power": 63, "wafer": 33, "substrate": 40, "pcb": 32, "ssd": 26, "rack": 34},
    "2026-04-13": {"cowos": 50, "hbm": 51, "optical": 48, "power": 63, "wafer": 33, "substrate": 41, "pcb": 33, "ssd": 27, "rack": 35},
    "2026-04-20": {"cowos": 51, "hbm": 51, "optical": 50, "power": 64, "wafer": 34, "substrate": 42, "pcb": 34, "ssd": 28, "rack": 36},
    "2026-04-27": {"cowos": 52, "hbm": 52, "optical": 52, "power": 65, "wafer": 34, "substrate": 43, "pcb": 35, "ssd": 29, "rack": 37},
}

AI_PROXY_OBSERVATIONS = {
    "2026-02-23": 90.6,
    "2026-03-02": 90.9,
    "2026-03-09": 91.2,
    "2026-03-16": 91.5,
    "2026-03-23": 91.8,
    "2026-03-30": 92.0,
    "2026-04-06": 92.1,
    "2026-04-13": 92.3,
    "2026-04-20": 92.5,
    "2026-04-27": 92.6,
}

PRICE_INDEX_OBSERVATIONS = {
    "2026-02-23": {"hbmDram": 100, "enterpriseSsd": 100, "cowosPackaging": 100, "substratePcb": 100, "powerIc": 100, "opticalModule": 100, "rackBom": 100},
    "2026-03-02": {"hbmDram": 104, "enterpriseSsd": 105, "cowosPackaging": 101, "substratePcb": 102, "powerIc": 101, "opticalModule": 101, "rackBom": 101},
    "2026-03-09": {"hbmDram": 108, "enterpriseSsd": 111, "cowosPackaging": 102, "substratePcb": 104, "powerIc": 102, "opticalModule": 102, "rackBom": 103},
    "2026-03-16": {"hbmDram": 113, "enterpriseSsd": 118, "cowosPackaging": 104, "substratePcb": 107, "powerIc": 104, "opticalModule": 103, "rackBom": 105},
    "2026-03-23": {"hbmDram": 119, "enterpriseSsd": 126, "cowosPackaging": 106, "substratePcb": 110, "powerIc": 106, "opticalModule": 104, "rackBom": 107},
    "2026-03-30": {"hbmDram": 126, "enterpriseSsd": 135, "cowosPackaging": 108, "substratePcb": 113, "powerIc": 108, "opticalModule": 105, "rackBom": 109},
    "2026-04-06": {"hbmDram": 132, "enterpriseSsd": 143, "cowosPackaging": 110, "substratePcb": 116, "powerIc": 110, "opticalModule": 106, "rackBom": 111},
    "2026-04-13": {"hbmDram": 138, "enterpriseSsd": 151, "cowosPackaging": 112, "substratePcb": 118, "powerIc": 111, "opticalModule": 107, "rackBom": 113},
    "2026-04-20": {"hbmDram": 144, "enterpriseSsd": 158, "cowosPackaging": 114, "substratePcb": 120, "powerIc": 112, "opticalModule": 108, "rackBom": 114},
    "2026-04-27": {"hbmDram": 149, "enterpriseSsd": 164, "cowosPackaging": 116, "substratePcb": 122, "powerIc": 113, "opticalModule": 109, "rackBom": 116},
    "2026-05-04": {"hbmDram": 152, "enterpriseSsd": 169, "cowosPackaging": 117, "substratePcb": 124, "powerIc": 115, "opticalModule": 110, "rackBom": 118},
    "2026-05-11": {"hbmDram": 155, "enterpriseSsd": 173, "cowosPackaging": 118, "substratePcb": 125, "powerIc": 116, "opticalModule": 111, "rackBom": 119},
    "2026-05-18": {"hbmDram": 157, "enterpriseSsd": 176, "cowosPackaging": 119, "substratePcb": 126, "powerIc": 117, "opticalModule": 112, "rackBom": 120},
}

DELIVERY_BACKCAST_STEPS = {
    "cowos": 0.35,
    "hbm": 0.38,
    "optical": 0.45,
    "power": 0.22,
    "wafer": 0.18,
    "substrate": 0.22,
    "pcb": 0.2,
    "ssd": 0.18,
    "rack": 0.18,
}

DELIVERY_FLOORS = {
    "cowos": 26,
    "hbm": 26,
    "optical": 18,
    "power": 46,
    "wafer": 20,
    "substrate": 24,
    "pcb": 18,
    "ssd": 14,
    "rack": 20,
}

PRICE_BACKCAST_STEPS = {
    "hbmDram": 1.2,
    "enterpriseSsd": 1.4,
    "cowosPackaging": 0.28,
    "substratePcb": 0.42,
    "powerIc": 0.25,
    "opticalModule": 0.18,
    "rackBom": 0.32,
}

PRICE_FLOORS = {
    "hbmDram": 72,
    "enterpriseSsd": 68,
    "cowosPackaging": 92,
    "substratePcb": 86,
    "powerIc": 92,
    "opticalModule": 94,
    "rackBom": 90,
}

SOURCE_CACHE_TTL_SECONDS = 600
_SOURCE_CACHE: Optional[tuple[datetime, dict[str, dict[str, float]], str, Optional[str], list[dict[str, str]]]] = None


async def fetch_ai_supply_chain_capacity_trends(horizon: str = "3m") -> dict[str, Any]:
    warnings: list[str] = []
    week_count = _horizon_week_count(horizon)
    official, official_source, official_release_date, industry_updates, source_warnings = await _get_source_bundle()
    warnings.extend(source_warnings)
    weeks = _last_monday_series(date.today(), count=week_count)
    capacity_trend = [
        _capacity_week_point(week, official)
        for week in weeks
    ]
    delivery_trend = [
        _delivery_week_point(week)
        for week in weeks
    ]
    pricing_trend = [
        _pricing_week_point(week)
        for week in weeks
    ]
    _apply_industry_signal_points(capacity_trend, delivery_trend, industry_updates)

    return {
        "capacity_trend": capacity_trend,
        "delivery_trend": delivery_trend,
        "pricing_trend": pricing_trend,
        "horizon": "1y" if week_count == 52 else "3m",
        "week_count": week_count,
        "official_source": official_source,
        "official_observed_through": _latest_month(official),
        "official_release_date": official_release_date,
        "proxy_observed_through": max(DELIVERY_OBSERVATIONS),
        "pricing_observed_through": max(PRICE_INDEX_OBSERVATIONS),
        "industry_observed_through": _latest_update_date(industry_updates),
        "industry_updates": industry_updates,
        "stale_from": _first_stale_week(delivery_trend),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "sources": [
            {
                "name": "FRED / Federal Reserve G.17 monthly series",
                "url": FRED_G17_ELECTRONICS_URL,
                "type": "official_monthly",
                "note": "电脑与电子产品、半导体及电子元件产能利用率。官方月度口径，非周频。",
            },
            {
                "name": "TrendForce / company calls / industry proxy basket",
                "url": TRENDFORCE_AI_SERVER_URL,
                "type": "proxy_weekly",
                "note": "CoWoS、HBM、先进制程、载板/PCB、SSD、光互联、AI服务器和电力设备交付周期代理观测；无统一官方周频利用率。",
            },
            {
                "name": "TrendForce memory pricing surveys and AI supply chain reports",
                "url": "https://www.trendforce.com/presscenter/news/20260331-12995.html",
                "type": "pricing_index",
                "note": "HBM/DRAM、Enterprise SSD/NAND、CoWoS、载板/PCB、电源IC、光模块和机柜BOM报价压力指数。公开源多为涨价区间和产业信号，非供应商逐笔报价。",
            },
        ],
        "warnings": warnings,
    }


async def _get_source_bundle() -> tuple[dict[str, dict[str, float]], str, Optional[str], list[dict[str, str]], list[str]]:
    global _SOURCE_CACHE
    now = datetime.now(timezone.utc)
    if _SOURCE_CACHE and (now - _SOURCE_CACHE[0]).total_seconds() < SOURCE_CACHE_TTL_SECONDS:
        _, official, official_source, release_date, updates = _SOURCE_CACHE
        return official, official_source, release_date, updates, []

    warnings: list[str] = []
    official = FALLBACK_OFFICIAL_MONTHLY
    official_source = "official_recent_cache"
    release_date: Optional[str] = None
    updates: list[dict[str, str]] = []

    try:
        official = await _fetch_fred_g17_monthly()
        official_source = "fred_g17_monthly"
    except Exception:
        pass

    try:
        _, release_date = await _fetch_fed_g17_monthly()
    except Exception:
        release_date = None

    try:
        updates = await _fetch_trendforce_ai_updates()
    except Exception as exc:
        warnings.append(f"TrendForce 产业信号读取失败，已保留最近结构化代理观测：{_clean_error(exc)}")

    _SOURCE_CACHE = (now, official, official_source, release_date, updates)
    return official, official_source, release_date, updates, warnings


async def _fetch_fed_g17_monthly() -> tuple[dict[str, dict[str, float]], Optional[str]]:
    async with httpx.AsyncClient(
        timeout=8,
        follow_redirects=True,
        headers={"User-Agent": "DeepFocus/0.1 ai-supply-chain"},
    ) as client:
        response = await client.get(FED_G17_TABLE_URL)
        response.raise_for_status()
    html = response.text
    months = _fed_recent_month_keys(html)
    release_date = _extract_release_date(html)
    return (
        {
            "electronics": _extract_fed_row_months(html, "Computer&nbsp;and&nbsp;electronic&nbsp;products", months),
            "semiconductor": _extract_fed_row_months(html, "Semiconductor&nbsp;and&nbsp;related", months),
        },
        release_date,
    )


async def _fetch_fred_g17_monthly() -> dict[str, dict[str, float]]:
    async with httpx.AsyncClient(
        timeout=5,
        follow_redirects=True,
        headers={"User-Agent": "DeepFocus/0.1 ai-supply-chain"},
    ) as client:
        electronics_response, semiconductor_response = await asyncio.gather(
            client.get(FRED_G17_ELECTRONICS_URL),
            client.get(FRED_G17_SEMICONDUCTOR_URL),
        )
        electronics_response.raise_for_status()
        semiconductor_response.raise_for_status()
    return {
        "electronics": _parse_fred_monthly_csv(electronics_response.text, "CAPUTLG334S"),
        "semiconductor": _parse_fred_monthly_csv(semiconductor_response.text, "CAPUTLG3344S"),
    }


def _parse_fred_monthly_csv(text: str, series_id: str) -> dict[str, float]:
    values: dict[str, float] = {}
    rows = csv.DictReader(io.StringIO(text))
    for row in rows:
        value = (row.get(series_id) or "").strip()
        if not value or value == ".":
            continue
        values[(row.get("observation_date") or "")[:7]] = round(float(value), 1)
    if not values:
        raise ValueError(f"FRED series has no values: {series_id}")
    return values


async def _fetch_trendforce_ai_updates() -> list[dict[str, str]]:
    async with httpx.AsyncClient(
        timeout=8,
        follow_redirects=True,
        headers={"User-Agent": "DeepFocus/0.1 ai-supply-chain"},
    ) as client:
        response = await client.get(TRENDFORCE_AI_SERVER_URL)
        response.raise_for_status()
    html = response.text
    updates = _extract_trendforce_reports(html) + _extract_trendforce_news(html)
    deduped: dict[tuple[str, str], dict[str, str]] = {}
    for update in updates:
        key = (update["date"], update["title"])
        deduped[key] = update
    return sorted(deduped.values(), key=lambda item: item["date"], reverse=True)[:10]


def _fed_recent_month_keys(html: str) -> list[str]:
    header_match = re.search(r'id=hdr29>.*?id=hdr32>.*?</tr>', html, re.I | re.S)
    if not header_match:
        return ["2026-01", "2026-02", "2026-03", "2026-04"]
    titles = re.findall(r'title="([a-z]+)".*?</abbr>', header_match.group(0), re.I | re.S)
    month_num = {
        "january": "01",
        "february": "02",
        "march": "03",
        "april": "04",
        "may": "05",
        "june": "06",
        "july": "07",
        "august": "08",
        "september": "09",
        "october": "10",
        "november": "11",
        "december": "12",
    }
    keys = [f"2026-{month_num[title.lower()]}" for title in titles if title.lower() in month_num]
    return keys[-4:] or ["2026-01", "2026-02", "2026-03", "2026-04"]


def _extract_release_date(html: str) -> Optional[str]:
    match = re.search(r"Release Date:\s*([A-Za-z]+\s+\d{1,2},\s+\d{4})", html)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%B %d, %Y").date().isoformat()
    except ValueError:
        return match.group(1)


def _extract_trendforce_reports(html: str) -> list[dict[str, str]]:
    updates: list[dict[str, str]] = []
    for block in re.findall(r'<div class="list-item">.*?</div>\s*</div>\s*</div>', html, re.I | re.S):
        title_match = re.search(r"<strong>(.*?)</strong>", block, re.I | re.S)
        date_match = re.search(r"(\d{4}/\d{2}/\d{2})", block)
        href_match = re.search(r'href="([^"]+)"', block)
        if not title_match or not date_match:
            continue
        title = _clean_html_text(title_match.group(1))
        if not _looks_ai_supply_chain_related(title):
            continue
        updates.append(
            {
                "date": date_match.group(1).replace("/", "-"),
                "title": title,
                "url": _absolute_trendforce_url(href_match.group(1) if href_match else TRENDFORCE_AI_SERVER_URL),
                "type": "research",
            }
        )
    return updates


def _extract_trendforce_news(html: str) -> list[dict[str, str]]:
    updates: list[dict[str, str]] = []
    pattern = (
        r'<a href="([^"]+)">\s*'
        r'<h5[^>]*>(.*?)</h5>\s*'
        r"</a>.*?"
        r"(\d{4}/\d{2}/\d{2})"
    )
    for href, raw_title, raw_date in re.findall(pattern, html, re.I | re.S):
        title = _clean_html_text(raw_title)
        if not _looks_ai_supply_chain_related(title):
            continue
        updates.append(
            {
                "date": raw_date.replace("/", "-"),
                "title": title,
                "url": _absolute_trendforce_url(href),
                "type": "news",
            }
        )
    return updates


def _looks_ai_supply_chain_related(text: str) -> bool:
    lowered = text.lower()
    keywords = (
        "ai",
        "hbm",
        "server",
        "cowos",
        "capacity",
        "component lead",
        "data center",
        "datacenter",
        "supply chain",
        "nvidia",
        "dram",
    )
    return any(keyword in lowered for keyword in keywords)


def _clean_html_text(text: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", text))).strip()


def _absolute_trendforce_url(href: str) -> str:
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return f"https://www.trendforce.com{href}"
    return f"https://www.trendforce.com/{href}"


def _extract_fed_row_months(html: str, row_label: str, months: list[str]) -> dict[str, float]:
    label_at = html.find(row_label)
    if label_at < 0:
        raise ValueError(f"Fed row not found: {row_label}")
    row_end = html.find("</tr>", label_at)
    row = html[label_at:row_end]
    values: dict[str, float] = {}
    for month_key, header in zip(months, ("hdr29", "hdr30", "hdr31", "hdr32")):
        match = re.search(rf'headers="[^"]*{header}[^"]*"[^>]*>([^<]+)</td>', row)
        if not match:
            continue
        values[month_key] = float(unescape(match.group(1)).replace("&nbsp;", "").strip())
    if not values:
        raise ValueError(f"Fed row has no monthly values: {row_label}")
    return values


def _last_monday_series(today: date, *, count: int) -> list[date]:
    monday = today - timedelta(days=today.weekday())
    start = monday - timedelta(weeks=count - 1)
    return [start + timedelta(weeks=index) for index in range(count)]


def _horizon_week_count(horizon: str) -> int:
    normalized = (horizon or "3m").lower()
    return 52 if normalized in {"1y", "year", "12m", "52w"} else 13


def _capacity_week_point(week: date, official: dict[str, dict[str, float]]) -> dict[str, Any]:
    month_key = week.strftime("%Y-%m")
    electronics = official.get("electronics", {}).get(month_key)
    semiconductor = official.get("semiconductor", {}).get(month_key)
    observed = electronics is not None or semiconductor is not None
    return {
        "week": week.strftime("%m/%d"),
        "date": week.isoformat(),
        "electronics": electronics,
        "semiconductor": semiconductor,
        "aiProxy": _ai_proxy_value(week),
        "observed": observed or _ai_proxy_value(week) is not None,
        "estimate": week.isoformat() not in AI_PROXY_OBSERVATIONS and _ai_proxy_value(week) is not None,
    }


def _ai_proxy_value(week: date) -> Optional[float]:
    exact = AI_PROXY_OBSERVATIONS.get(week.isoformat())
    if exact is not None:
        return exact
    earliest = date.fromisoformat(min(AI_PROXY_OBSERVATIONS))
    if week >= earliest:
        return None
    weeks_before = max(0, (earliest - week).days // 7)
    base = AI_PROXY_OBSERVATIONS[earliest.isoformat()]
    return max(82.0, round(base - 0.22 * weeks_before, 1))


def _delivery_week_point(week: date) -> dict[str, Any]:
    values = DELIVERY_OBSERVATIONS.get(week.isoformat()) or _backcast_week_values(week, DELIVERY_OBSERVATIONS, DELIVERY_BACKCAST_STEPS, DELIVERY_FLOORS) or {}
    return {
        "week": week.strftime("%m/%d"),
        "date": week.isoformat(),
        "cowos": values.get("cowos"),
        "hbm": values.get("hbm"),
        "optical": values.get("optical"),
        "power": values.get("power"),
        "wafer": values.get("wafer"),
        "substrate": values.get("substrate"),
        "pcb": values.get("pcb"),
        "ssd": values.get("ssd"),
        "rack": values.get("rack"),
        "observed": bool(values),
        "estimate": values is not None and week.isoformat() not in DELIVERY_OBSERVATIONS,
    }


def _pricing_week_point(week: date) -> dict[str, Any]:
    values = PRICE_INDEX_OBSERVATIONS.get(week.isoformat()) or {}
    signal = week.isoformat() > max(DELIVERY_OBSERVATIONS)
    earliest_pricing_week = date.fromisoformat(min(PRICE_INDEX_OBSERVATIONS))
    return {
        "week": week.strftime("%m/%d"),
        "date": week.isoformat(),
        "hbmDram": values.get("hbmDram"),
        "enterpriseSsd": values.get("enterpriseSsd"),
        "cowosPackaging": values.get("cowosPackaging"),
        "substratePcb": values.get("substratePcb"),
        "powerIc": values.get("powerIc"),
        "opticalModule": values.get("opticalModule"),
        "rackBom": values.get("rackBom"),
        "observed": bool(values),
        "signal": bool(values) and signal,
        "estimate": False,
        "history_gap": not values and week < earliest_pricing_week,
        "signal_source": "public pricing survey and industry signal index" if values and signal else None,
    }


def _backcast_week_values(
    week: date,
    observations: dict[str, dict[str, int]],
    steps: dict[str, float],
    floors: dict[str, int],
) -> Optional[dict[str, int]]:
    earliest_key = min(observations)
    earliest = date.fromisoformat(earliest_key)
    if week >= earliest:
        return None
    weeks_before = max(0, (earliest - week).days // 7)
    base = observations[earliest_key]
    return {
        key: int(max(floors[key], round(base[key] - steps[key] * weeks_before)))
        for key in steps
    }


def _apply_industry_signal_points(
    capacity_trend: list[dict[str, Any]],
    delivery_trend: list[dict[str, Any]],
    updates: list[dict[str, str]],
) -> None:
    if not updates:
        return
    last_structured_week = date.fromisoformat(max(DELIVERY_OBSERVATIONS))
    current_delivery = dict(DELIVERY_OBSERVATIONS[last_structured_week.isoformat()])
    current_proxy = AI_PROXY_OBSERVATIONS[last_structured_week.isoformat()]

    updates_by_week: dict[str, list[dict[str, str]]] = {}
    for update in updates:
        try:
            update_date = date.fromisoformat(update["date"])
        except (KeyError, ValueError):
            continue
        week = update_date - timedelta(days=update_date.weekday())
        if week <= last_structured_week:
            continue
        updates_by_week.setdefault(week.isoformat(), []).append(update)

    capacity_by_date = {point["date"]: point for point in capacity_trend}
    delivery_by_date = {point["date"]: point for point in delivery_trend}
    for week_key in sorted(updates_by_week):
        capacity_point = capacity_by_date.get(week_key)
        delivery_point = delivery_by_date.get(week_key)
        if not capacity_point or not delivery_point:
            continue
        week_updates = updates_by_week[week_key]
        titles = " ".join(update.get("title", "") for update in week_updates).lower()
        pressure = _industry_signal_pressure(titles)
        if pressure <= 0:
            continue

        cowos_step = 1 if _mentions_any(titles, ("cowos", "advanced packaging", "nvidia", "ai server", "supply chain", "capacity")) else 0
        hbm_step = 1 if _mentions_any(titles, ("hbm", "dram", "nvidia", "ai server", "supply chain")) else 0
        optical_step = 1 if _mentions_any(titles, ("rack", "infra", "data center", "server", "csp", "network")) else 0
        power_step = 1 if _mentions_any(titles, ("power", "rack", "infra", "data center", "capex", "csp")) else 0
        wafer_step = 1 if _mentions_any(titles, ("wafer", "3nm", "4nm", "capacity", "nvidia", "ai server")) else 0
        substrate_step = 1 if _mentions_any(titles, ("substrate", "advanced packaging", "cowos", "supply chain", "nvidia")) else 0
        pcb_step = 1 if _mentions_any(titles, ("pcb", "t-glass", "server", "rack", "supply chain")) else 0
        ssd_step = 1 if _mentions_any(titles, ("ssd", "nand", "dram", "storage", "ai server")) else 0
        rack_step = 1 if _mentions_any(titles, ("rack", "ai server", "server", "infra", "csp", "data center")) else 0

        current_delivery["cowos"] = min(60, current_delivery["cowos"] + cowos_step)
        current_delivery["hbm"] = min(60, current_delivery["hbm"] + hbm_step)
        current_delivery["optical"] = min(60, current_delivery["optical"] + optical_step)
        current_delivery["power"] = min(72, current_delivery["power"] + power_step)
        current_delivery["wafer"] = min(48, current_delivery["wafer"] + wafer_step)
        current_delivery["substrate"] = min(56, current_delivery["substrate"] + substrate_step)
        current_delivery["pcb"] = min(52, current_delivery["pcb"] + pcb_step)
        current_delivery["ssd"] = min(44, current_delivery["ssd"] + ssd_step)
        current_delivery["rack"] = min(52, current_delivery["rack"] + rack_step)
        current_proxy = min(95.5, round(current_proxy + pressure, 1))
        signal_date = max(update["date"] for update in week_updates if update.get("date"))

        capacity_point["aiProxy"] = current_proxy
        capacity_point["observed"] = True
        capacity_point["signal"] = True
        capacity_point["signal_date"] = signal_date
        capacity_point["signal_source"] = "TrendForce public industry signal estimate"

        delivery_point.update(current_delivery)
        delivery_point["observed"] = True
        delivery_point["signal"] = True
        delivery_point["signal_date"] = signal_date
        delivery_point["signal_source"] = "TrendForce public industry signal estimate"


def _industry_signal_pressure(titles: str) -> float:
    score = 0.0
    if _mentions_any(titles, ("tight", "shortage", "lead time", "capacity", "supply chain", "scales up")):
        score += 0.3
    if _mentions_any(titles, ("ai", "hbm", "dram", "server", "nvidia")):
        score += 0.2
    if _mentions_any(titles, ("power", "rack", "data center", "infra", "csp", "capex")):
        score += 0.1
    return min(score, 0.6)


def _mentions_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _latest_month(values: dict[str, dict[str, float]]) -> Optional[str]:
    months = sorted({month for group in values.values() for month in group})
    return months[-1] if months else None


def _latest_update_date(updates: list[dict[str, str]]) -> Optional[str]:
    dates = sorted(update["date"] for update in updates if update.get("date"))
    return dates[-1] if dates else None


def _first_stale_week(points: list[dict[str, Any]]) -> Optional[str]:
    for point in points:
        if not point.get("observed"):
            return str(point.get("week") or "")
    return None


def _clean_error(exc: Exception) -> str:
    return str(exc).replace("\n", " ")[:160]
