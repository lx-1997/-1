from __future__ import annotations

import asyncio
import csv
import os
import time
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from typing import Iterable, Optional

import httpx

from .market_data import normalize_symbols
from .schemas import EarningsCalendarEvent, EarningsCalendarResponse
from .shared_utils import to_float, safe_error, utc_now_iso



REQUEST_TIMEOUT = httpx.Timeout(10.0, connect=4.0)
NASDAQ_CALENDAR_TIMEOUT = httpx.Timeout(4.0, connect=2.0)
SUPPORTED_HORIZONS = {"3month", "6month", "12month"}
HORIZON_DAYS = {
    "3month": 92,
    "6month": 183,
    "12month": 366,
}
NASDAQ_PUBLIC_SCAN_LIMIT = int(os.getenv("NASDAQ_EARNINGS_SCAN_DAYS", "120"))
NASDAQ_CALENDAR_BATCH_DAYS = int(os.getenv("NASDAQ_EARNINGS_BATCH_DAYS", "7"))
NASDAQ_CALENDAR_CONCURRENCY = int(os.getenv("NASDAQ_EARNINGS_CONCURRENCY", "6"))
NASDAQ_CALENDAR_SCAN_BUDGET_SECONDS = float(os.getenv("NASDAQ_EARNINGS_SCAN_BUDGET_SECONDS", "14"))
EARNINGS_CACHE_TTL_SECONDS = int(os.getenv("EARNINGS_CACHE_TTL_SECONDS", "1800"))
_EARNINGS_CACHE: dict[tuple[tuple[str, ...], str, Optional[float], bool], tuple[float, EarningsCalendarResponse]] = {}

COMPANY_CONTEXT: dict[str, dict[str, list[str] | str]] = {
    "TSLA": {
        "name": "Tesla",
        "watch_items": ["汽车交付节奏", "汽车毛利率", "能源业务增速", "FSD/Robotaxi 进展"],
        "focus_metrics": ["交付量", "汽车毛利率", "自由现金流", "库存天数"],
        "risk_flags": ["价格战压力", "监管与安全审查", "资本开支波动"],
        "related_symbols": ["RIVN", "GM", "F"],
    },
    "NVDA": {
        "name": "NVIDIA",
        "watch_items": ["数据中心收入", "Blackwell 供给爬坡", "毛利率", "中国市场限制"],
        "focus_metrics": ["Data Center Revenue", "Gross Margin", "Inventory", "Forward Guidance"],
        "risk_flags": ["出口管制", "云厂商 CapEx 节奏", "高估值敏感度"],
        "related_symbols": ["AMD", "AVGO", "TSM"],
    },
    "AAPL": {
        "name": "Apple",
        "watch_items": ["iPhone 收入", "服务业务增速", "大中华区表现", "回购与资本回报"],
        "focus_metrics": ["iPhone Revenue", "Services Growth", "Gross Margin", "Buyback"],
        "risk_flags": ["换机周期放缓", "监管压力", "汇率影响"],
        "related_symbols": ["MSFT", "GOOGL", "QCOM"],
    },
    "MSFT": {
        "name": "Microsoft",
        "watch_items": ["Azure 增速", "AI 贡献", "商业云毛利", "资本开支"],
        "focus_metrics": ["Azure Growth", "Cloud Margin", "AI Revenue", "CapEx"],
        "risk_flags": ["AI 基建投入回收期", "监管审查", "企业软件预算"],
        "related_symbols": ["GOOGL", "AMZN", "ORCL"],
    },
}


async def fetch_earnings_calendar(
    symbols: Iterable[str],
    horizon: str = "3month",
    min_market_cap: Optional[float] = None,
    include_all: bool = False,
) -> EarningsCalendarResponse:
    requested_symbols = normalize_symbols(symbols)
    selected_horizon = horizon if horizon in SUPPORTED_HORIZONS else "3month"
    fetched_at = utc_now_iso()
    warnings: list[str] = []
    market_cap_floor = min_market_cap if min_market_cap and min_market_cap > 0 else None
    cache_key = (tuple(requested_symbols), selected_horizon, market_cap_floor, include_all)
    cached = _EARNINGS_CACHE.get(cache_key)
    now_ts = datetime.now(timezone.utc).timestamp()
    if cached and now_ts - cached[0] <= EARNINGS_CACHE_TTL_SECONDS:
        return cached[1]

    if not requested_symbols and market_cap_floor is None and not include_all:
        return EarningsCalendarResponse(
            events=[],
            provider="none",
            fetched_at=fetched_at,
            warnings=["No valid symbols were supplied."],
        )

    events: list[EarningsCalendarEvent] = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://www.nasdaq.com/",
        "Origin": "https://www.nasdaq.com",
    }

    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT,
        headers=headers,
        follow_redirects=True,
        limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
    ) as client:
        nasdaq_events, nasdaq_warnings = await _fetch_nasdaq_public_events(
            client=client,
            symbols=requested_symbols,
            horizon=selected_horizon,
            min_market_cap=market_cap_floor,
            include_all=include_all,
        )
        events.extend(nasdaq_events)
        warnings.extend(nasdaq_warnings)

        missing_after_nasdaq = [
            symbol for symbol in requested_symbols
            if symbol not in {event.symbol for event in events}
        ]

        alpha_key = os.getenv("ALPHAVANTAGE_API_KEY") or os.getenv("ALPHA_VANTAGE_API_KEY")
        if missing_after_nasdaq and alpha_key:
            alpha_events, alpha_warnings = await _fetch_alpha_vantage_events(
                client=client,
                symbols=missing_after_nasdaq,
                horizon=selected_horizon,
                api_key=alpha_key,
            )
            events.extend(alpha_events)
            warnings.extend(alpha_warnings)

    if requested_symbols:
        existing_symbols = {event.symbol for event in events}
        for symbol in requested_symbols:
            if symbol not in existing_symbols:
                events.append(_template_event(symbol))

    events.sort(key=_sort_key)
    provider_names = {event.provider for event in events}
    provider = "mixed" if len(provider_names) > 1 else next(iter(provider_names), "none")

    response = EarningsCalendarResponse(
        events=events,
        provider=provider,
        fetched_at=fetched_at,
        warnings=warnings,
    )
    _EARNINGS_CACHE[cache_key] = (now_ts, response)
    return response


async def _fetch_nasdaq_public_events(
    client: httpx.AsyncClient,
    symbols: list[str],
    horizon: str,
    min_market_cap: Optional[float] = None,
    include_all: bool = False,
) -> tuple[list[EarningsCalendarEvent], list[str]]:
    target_symbols = set(symbols)
    market_cap_floor = min_market_cap if min_market_cap and min_market_cap > 0 else None
    all_large_cap_mode = not target_symbols and market_cap_floor is not None
    full_calendar_mode = not target_symbols and market_cap_floor is None and include_all
    today = date.today()
    scan_days = min(HORIZON_DAYS[horizon], NASDAQ_PUBLIC_SCAN_LIMIT)
    warnings: list[str] = []
    failed_days: list[str] = []
    events_by_symbol: dict[str, EarningsCalendarEvent] = {}
    dated_symbols: set[str] = set()
    semaphore = asyncio.Semaphore(max(1, NASDAQ_CALENDAR_CONCURRENCY))
    scan_deadline = time.monotonic() + max(4.0, NASDAQ_CALENDAR_SCAN_BUDGET_SECONDS)

    estimate_events, estimate_warnings = (
        await _fetch_nasdaq_public_estimates(client, sorted(target_symbols))
        if target_symbols
        else ([], [])
    )
    for event in estimate_events:
        events_by_symbol[event.symbol] = event

    async def fetch_day(day: date) -> None:
        async with semaphore:
            try:
                response = await client.get(
                    "https://api.nasdaq.com/api/calendar/earnings",
                    params={"date": day.isoformat()},
                    timeout=NASDAQ_CALENDAR_TIMEOUT,
                )
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:  # noqa: BLE001
                failed_days.append(f"{day.isoformat()}:{safe_error(exc)}")
                return

        rows = (payload.get("data") or {}).get("rows") or []
        data_as_of = _clean_text((payload.get("data") or {}).get("asOf"))
        for row in rows:
            symbol = str(row.get("symbol") or "").strip().upper()
            market_cap = to_float(row.get("marketCap"))
            if target_symbols and symbol not in target_symbols:
                continue
            if all_large_cap_mode and (market_cap is None or market_cap < market_cap_floor):
                continue

            context = _context_for_symbol(symbol)
            confidence = _nasdaq_calendar_confidence(row)
            dated_symbols.add(symbol)
            calendar_event = EarningsCalendarEvent(
                symbol=symbol,
                name=row.get("name") or str(context["name"]),
                report_date=day.isoformat(),
                fiscal_date_ending=_clean_text(row.get("fiscalQuarterEnding")),
                eps_estimate=to_float(row.get("epsForecast")),
                market_cap=market_cap,
                analyst_count=_to_int(row.get("noOfEsts")),
                last_year_report_date=_parse_us_date(row.get("lastYearRptDt")),
                last_year_eps=to_float(row.get("lastYearEPS")),
                currency="USD",
                time_of_day=_clean_text(row.get("time")),
                provider="nasdaq_public",
                source_name="Nasdaq 公共日历",
                source_url=_nasdaq_calendar_url(day),
                data_as_of=data_as_of,
                days_until_report=_days_until(day.isoformat(), today),
                is_date_confirmed=confidence == "confirmed",
                status="scheduled",
                confidence=confidence,
                watch_items=list(context["watch_items"]),
                focus_metrics=list(context["focus_metrics"]),
                risk_flags=list(context["risk_flags"]),
                related_symbols=list(context["related_symbols"]),
            )
            existing = events_by_symbol.get(symbol)
            if existing:
                _merge_calendar_details(existing, calendar_event)
            else:
                events_by_symbol[symbol] = calendar_event

    batch_days = max(1, NASDAQ_CALENDAR_BATCH_DAYS)
    for start in range(0, scan_days + 1, batch_days):
        if time.monotonic() >= scan_deadline:
            warnings.append(
                "Nasdaq public calendar scan reached the time budget; "
                "forecast data is shown for remaining symbols."
            )
            break
        offsets = range(start, min(start + batch_days, scan_days + 1))
        await asyncio.gather(*(fetch_day(today + timedelta(days=offset)) for offset in offsets))
        if target_symbols and dated_symbols == target_symbols:
            break

    missing_after_calendar = sorted(target_symbols - dated_symbols)

    if not events_by_symbol:
        if all_large_cap_mode:
            warnings.append("Nasdaq public calendar returned no companies above the market-cap threshold.")
        elif full_calendar_mode:
            warnings.append("Nasdaq public calendar returned no companies in the scanned window.")
        else:
            warnings.append("Nasdaq public calendar and forecast returned no matching watchlist data.")
    elif not dated_symbols:
        warnings.append(
            "Nasdaq public calendar did not return dated events; "
            "Nasdaq public forecasts were used for available EPS estimates."
        )
    elif missing_after_calendar:
        warnings.append(
            "Nasdaq public calendar did not return dates for: "
            f"{', '.join(missing_after_calendar)} within the scanned window; "
            "Nasdaq public forecasts were used for available EPS estimates."
        )
    if scan_days < HORIZON_DAYS[horizon]:
        warnings.append(
            "Nasdaq public date scan was capped at "
            f"{scan_days} days; farther events are shown from forecast data when available."
        )
    if all_large_cap_mode:
        warnings.append(
            "Nasdaq public calendar rows were filtered to companies with market cap >= "
            f"{_format_usd_compact(market_cap_floor)}."
        )
    elif full_calendar_mode:
        warnings.append("Nasdaq public calendar rows were scanned without a market-cap filter.")

    warnings.extend(estimate_warnings)
    if failed_days:
        warnings.append(
            "Nasdaq public calendar skipped "
            f"{len(failed_days)} dates because of temporary request failures."
        )

    return list(events_by_symbol.values()), warnings


async def _fetch_nasdaq_public_estimates(
    client: httpx.AsyncClient,
    symbols: list[str],
) -> tuple[list[EarningsCalendarEvent], list[str]]:
    warnings: list[str] = []

    async def fetch_one(symbol: str) -> Optional[EarningsCalendarEvent]:
        try:
            response = await client.get(
                f"https://api.nasdaq.com/api/analyst/{symbol}/earnings-forecast"
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Nasdaq public forecast failed for {symbol}: {safe_error(exc)}.")
            return None

        rows = ((payload.get("data") or {}).get("quarterlyForecast") or {}).get("rows") or []
        if not rows:
            return None

        row = rows[0]
        context = _context_for_symbol(symbol)
        return EarningsCalendarEvent(
            symbol=symbol,
            name=str(context["name"]),
            report_date=None,
            fiscal_date_ending=_clean_text(row.get("fiscalEnd")),
            eps_estimate=to_float(row.get("consensusEPSForecast")),
            eps_high_estimate=to_float(row.get("highEPSForecast")),
            eps_low_estimate=to_float(row.get("lowEPSForecast")),
            analyst_count=_to_int(row.get("noOfEstimates")),
            revision_up_count=_to_int(row.get("up")),
            revision_down_count=_to_int(row.get("down")),
            currency="USD",
            provider="nasdaq_public",
            source_name="Nasdaq 公共预测",
            source_url=_nasdaq_stock_earnings_url(symbol),
            data_as_of=_clean_text(((payload.get("data") or {}).get("quarterlyForecast") or {}).get("asOf")),
            status="scheduled",
            confidence="estimated",
            watch_items=list(context["watch_items"]),
            focus_metrics=list(context["focus_metrics"]),
            risk_flags=list(context["risk_flags"]),
            related_symbols=list(context["related_symbols"]),
        )

    results = await asyncio.gather(*(fetch_one(symbol) for symbol in symbols))
    events = [event for event in results if event]
    missing = sorted(set(symbols) - {event.symbol for event in events})
    if missing:
        warnings.append(f"Nasdaq public forecast returned no EPS estimates for: {', '.join(missing)}.")
    return events, warnings


async def _fetch_alpha_vantage_events(
    client: httpx.AsyncClient,
    symbols: list[str],
    horizon: str,
    api_key: str,
) -> tuple[list[EarningsCalendarEvent], list[str]]:
    warnings: list[str] = []

    async def fetch_one(symbol: str) -> list[EarningsCalendarEvent]:
        try:
            response = await client.get(
                "https://www.alphavantage.co/query",
                params={
                    "function": "EARNINGS_CALENDAR",
                    "symbol": symbol,
                    "horizon": horizon,
                    "apikey": api_key,
                },
            )
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Alpha Vantage failed for {symbol}: {safe_error(exc)}.")
            return []

        text = response.text.strip()
        if not text or text.startswith("{"):
            warnings.append(f"Alpha Vantage returned no CSV earnings rows for {symbol}.")
            return []

        rows = list(csv.DictReader(StringIO(text)))
        events: list[EarningsCalendarEvent] = []
        for row in rows:
            row_symbol = (row.get("symbol") or symbol).strip().upper()
            if row_symbol != symbol:
                continue
            context = _context_for_symbol(symbol)
            events.append(
                EarningsCalendarEvent(
                    symbol=symbol,
                    name=row.get("name") or str(context["name"]),
                    report_date=_clean_text(row.get("reportDate")),
                    fiscal_date_ending=_clean_text(row.get("fiscalDateEnding")),
                    eps_estimate=to_float(row.get("estimate")),
                    currency=row.get("currency") or "USD",
                    time_of_day=_clean_text(row.get("timeOfTheDay")),
                    provider="alpha_vantage",
                    source_name="Alpha Vantage",
                    source_url="https://www.alphavantage.co/documentation/#earnings-calendar",
                    data_as_of=date.today().isoformat(),
                    days_until_report=_days_until(row.get("reportDate"), date.today()),
                    is_date_confirmed=True,
                    status="scheduled",
                    confidence="confirmed",
                    watch_items=list(context["watch_items"]),
                    focus_metrics=list(context["focus_metrics"]),
                    risk_flags=list(context["risk_flags"]),
                    related_symbols=list(context["related_symbols"]),
                )
            )
        return events

    nested = await asyncio.gather(*(fetch_one(symbol) for symbol in symbols))
    return [event for symbol_events in nested for event in symbol_events], warnings


def _template_event(symbol: str) -> EarningsCalendarEvent:
    context = _context_for_symbol(symbol)
    return EarningsCalendarEvent(
        symbol=symbol,
        name=str(context["name"]),
        report_date=None,
        fiscal_date_ending=None,
        currency="USD",
        provider="watchlist_template",
        source_name="关注池模板",
        status="watchlist_template",
        confidence="pending_provider",
        source_url=None,
        data_as_of=date.today().isoformat(),
        watch_items=list(context["watch_items"]),
        focus_metrics=list(context["focus_metrics"]),
        risk_flags=list(context["risk_flags"]),
        related_symbols=list(context["related_symbols"]),
    )


def _context_for_symbol(symbol: str) -> dict[str, list[str] | str]:
    return COMPANY_CONTEXT.get(
        symbol,
        {
            "name": symbol,
            "watch_items": ["收入增速", "EPS 预期差", "利润率", "管理层指引"],
            "focus_metrics": ["EPS", "Revenue", "Gross Margin", "Guidance"],
            "risk_flags": ["估值敏感度", "行业景气度", "汇率与成本波动"],
            "related_symbols": [],
        },
    )


def _sort_key(event: EarningsCalendarEvent) -> tuple[str, str]:
    return (event.report_date or "9999-12-31", event.symbol)


def _merge_calendar_details(target: EarningsCalendarEvent, calendar_event: EarningsCalendarEvent) -> None:
    target.name = calendar_event.name or target.name
    target.report_date = calendar_event.report_date
    target.fiscal_date_ending = calendar_event.fiscal_date_ending or target.fiscal_date_ending
    target.time_of_day = calendar_event.time_of_day
    target.market_cap = calendar_event.market_cap
    target.last_year_report_date = calendar_event.last_year_report_date
    target.last_year_eps = calendar_event.last_year_eps
    target.days_until_report = calendar_event.days_until_report
    target.is_date_confirmed = calendar_event.is_date_confirmed
    target.status = calendar_event.status
    target.confidence = calendar_event.confidence
    target.source_name = calendar_event.source_name
    target.source_url = calendar_event.source_url
    target.data_as_of = calendar_event.data_as_of or target.data_as_of
    if calendar_event.eps_estimate is not None:
        target.eps_estimate = calendar_event.eps_estimate
    if calendar_event.analyst_count is not None:
        target.analyst_count = calendar_event.analyst_count


def _nasdaq_calendar_confidence(row: dict[str, object]) -> str:
    time_of_day = str(row.get("time") or "").strip().lower()
    if time_of_day and time_of_day != "time-not-supplied":
        return "confirmed"
    return "estimated"
def _to_int(value: object) -> Optional[int]:
    number = to_float(value)
    if number is None:
        return None
    return int(number)


def _clean_text(value: object) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _parse_us_date(value: object) -> Optional[str]:
    text = str(value or "").strip()
    if not text or text.upper() == "N/A":
        return None
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return text


def _days_until(value: object, today: date) -> Optional[int]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        report_day = datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None
    return (report_day - today).days


def _nasdaq_calendar_url(day: date) -> str:
    return f"https://www.nasdaq.com/market-activity/earnings?date={day.isoformat()}"


def _nasdaq_stock_earnings_url(symbol: str) -> str:
    return f"https://www.nasdaq.com/market-activity/stocks/{symbol.lower()}/earnings"


def _format_usd_compact(value: Optional[float]) -> str:
    if value is None:
        return "0"
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.0f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.0f}M"
    return f"${value:,.0f}"