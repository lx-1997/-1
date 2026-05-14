from __future__ import annotations

import asyncio
import csv
import os
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from typing import Iterable, Optional

import httpx

from .market_data import normalize_symbols
from .schemas import EarningsCalendarEvent, EarningsCalendarResponse


REQUEST_TIMEOUT = httpx.Timeout(10.0, connect=4.0)
SUPPORTED_HORIZONS = {"3month", "6month", "12month"}
HORIZON_DAYS = {
    "3month": 92,
    "6month": 183,
    "12month": 366,
}
NASDAQ_PUBLIC_SCAN_LIMIT = int(os.getenv("NASDAQ_EARNINGS_SCAN_DAYS", "60"))
EARNINGS_CACHE_TTL_SECONDS = int(os.getenv("EARNINGS_CACHE_TTL_SECONDS", "1800"))
_EARNINGS_CACHE: dict[tuple[tuple[str, ...], str], tuple[float, EarningsCalendarResponse]] = {}

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
) -> EarningsCalendarResponse:
    requested_symbols = normalize_symbols(symbols)
    selected_horizon = horizon if horizon in SUPPORTED_HORIZONS else "3month"
    fetched_at = _utc_now()
    warnings: list[str] = []
    cache_key = (tuple(requested_symbols), selected_horizon)
    cached = _EARNINGS_CACHE.get(cache_key)
    now_ts = datetime.now(timezone.utc).timestamp()
    if cached and now_ts - cached[0] <= EARNINGS_CACHE_TTL_SECONDS:
        return cached[1]

    if not requested_symbols:
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
) -> tuple[list[EarningsCalendarEvent], list[str]]:
    target_symbols = set(symbols)
    today = date.today()
    scan_days = min(HORIZON_DAYS[horizon], NASDAQ_PUBLIC_SCAN_LIMIT)
    warnings: list[str] = []
    failed_days: list[str] = []
    events_by_symbol: dict[str, EarningsCalendarEvent] = {}
    semaphore = asyncio.Semaphore(16)

    async def fetch_day(day: date) -> None:
        async with semaphore:
            try:
                response = await client.get(
                    "https://api.nasdaq.com/api/calendar/earnings",
                    params={"date": day.isoformat()},
                )
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:  # noqa: BLE001
                failed_days.append(f"{day.isoformat()}:{_safe_error(exc)}")
                return

        rows = (payload.get("data") or {}).get("rows") or []
        for row in rows:
            symbol = str(row.get("symbol") or "").strip().upper()
            if symbol not in target_symbols or symbol in events_by_symbol:
                continue

            context = _context_for_symbol(symbol)
            events_by_symbol[symbol] = EarningsCalendarEvent(
                symbol=symbol,
                name=row.get("name") or str(context["name"]),
                report_date=day.isoformat(),
                fiscal_date_ending=_clean_text(row.get("fiscalQuarterEnding")),
                eps_estimate=_to_float(row.get("epsForecast")),
                currency="USD",
                time_of_day=_clean_text(row.get("time")),
                provider="nasdaq_public",
                source_name="Nasdaq 公共日历",
                status="scheduled",
                confidence="estimated",
                watch_items=list(context["watch_items"]),
                focus_metrics=list(context["focus_metrics"]),
                risk_flags=list(context["risk_flags"]),
                related_symbols=list(context["related_symbols"]),
            )

    for start in range(0, scan_days + 1, 21):
        offsets = range(start, min(start + 21, scan_days + 1))
        await asyncio.gather(*(fetch_day(today + timedelta(days=offset)) for offset in offsets))
        if set(events_by_symbol) == target_symbols:
            break

    missing_after_calendar = sorted(target_symbols - set(events_by_symbol))
    if missing_after_calendar:
        estimate_events, estimate_warnings = await _fetch_nasdaq_public_estimates(client, missing_after_calendar)
        for event in estimate_events:
            events_by_symbol.setdefault(event.symbol, event)
    else:
        estimate_warnings = []

    if not events_by_symbol:
        warnings.append("Nasdaq public calendar and forecast returned no matching watchlist data.")
    elif not set(events_by_symbol).intersection(target_symbols - set(missing_after_calendar)):
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
            warnings.append(f"Nasdaq public forecast failed for {symbol}: {_safe_error(exc)}.")
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
            eps_estimate=_to_float(row.get("consensusEPSForecast")),
            currency="USD",
            provider="nasdaq_public",
            source_name="Nasdaq 公共预测",
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
            warnings.append(f"Alpha Vantage failed for {symbol}: {_safe_error(exc)}.")
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
                    eps_estimate=_to_float(row.get("estimate")),
                    currency=row.get("currency") or "USD",
                    time_of_day=_clean_text(row.get("timeOfTheDay")),
                    provider="alpha_vantage",
                    source_name="Alpha Vantage",
                    status="scheduled",
                    confidence="estimated",
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


def _to_float(value: object) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("$", "").replace("%", "")
    if not text or text in {"-", "None", "null"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _clean_text(value: object) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    return exc.__class__.__name__
