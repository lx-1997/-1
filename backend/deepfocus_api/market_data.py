from __future__ import annotations

import asyncio
import csv
import os
from datetime import datetime, timezone
from io import StringIO
from typing import Iterable, Optional

import httpx

from .schemas import MarketQuote, MarketQuoteListResponse


MAX_SYMBOLS = 30
REQUEST_TIMEOUT = httpx.Timeout(8.0, connect=4.0)


def normalize_symbols(symbols: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    for symbol in symbols:
        cleaned = "".join(
            char for char in symbol.strip().upper()
            if char.isalnum() or char in {".", "-"}
        )
        if cleaned and cleaned not in normalized:
            normalized.append(cleaned)
        if len(normalized) >= MAX_SYMBOLS:
            break
    return normalized


async def fetch_market_quotes(symbols: Iterable[str]) -> MarketQuoteListResponse:
    requested_symbols = normalize_symbols(symbols)
    fetched_at = _utc_now()
    warnings: list[str] = []
    quote_by_symbol: dict[str, MarketQuote] = {}

    if not requested_symbols:
        return MarketQuoteListResponse(
            quotes=[],
            provider="none",
            fetched_at=fetched_at,
            warnings=["No valid symbols were supplied."],
        )

    headers = {"User-Agent": "DeepFocus/0.1 market-data"}
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers=headers) as client:
        missing = requested_symbols

        finnhub_key = os.getenv("FINNHUB_API_KEY") or os.getenv("FINNHUB_TOKEN")
        if finnhub_key:
            quotes, provider_warnings = await _fetch_finnhub_quotes(client, missing, finnhub_key)
            _merge_quotes(quote_by_symbol, quotes)
            warnings.extend(provider_warnings)
            missing = [symbol for symbol in requested_symbols if symbol not in quote_by_symbol]

        alpha_key = os.getenv("ALPHAVANTAGE_API_KEY") or os.getenv("ALPHA_VANTAGE_API_KEY")
        if missing and alpha_key:
            quotes, provider_warnings = await _fetch_alpha_vantage_quotes(client, missing, alpha_key)
            _merge_quotes(quote_by_symbol, quotes)
            warnings.extend(provider_warnings)
            missing = [symbol for symbol in requested_symbols if symbol not in quote_by_symbol]

        if missing:
            quotes, provider_warnings = await _fetch_stooq_quotes(client, missing)
            _merge_quotes(quote_by_symbol, quotes)
            warnings.extend(provider_warnings)

    ordered_quotes = [
        quote_by_symbol[symbol]
        for symbol in requested_symbols
        if symbol in quote_by_symbol
    ]
    provider_names = {quote.provider for quote in ordered_quotes}
    provider = "none"
    if len(provider_names) == 1:
        provider = next(iter(provider_names))
    elif len(provider_names) > 1:
        provider = "mixed"

    missing_symbols = [symbol for symbol in requested_symbols if symbol not in quote_by_symbol]
    if missing_symbols:
        warnings.append(f"No market quote returned for: {', '.join(missing_symbols)}.")

    return MarketQuoteListResponse(
        quotes=ordered_quotes,
        provider=provider,
        fetched_at=fetched_at,
        warnings=warnings,
    )


async def _fetch_finnhub_quotes(
    client: httpx.AsyncClient,
    symbols: list[str],
    api_key: str,
) -> tuple[list[MarketQuote], list[str]]:
    warnings: list[str] = []

    async def fetch_one(symbol: str) -> Optional[MarketQuote]:
        try:
            response = await client.get(
                "https://finnhub.io/api/v1/quote",
                params={"symbol": symbol, "token": api_key},
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001 - provider failures must not break the app
            warnings.append(f"Finnhub failed for {symbol}: {_safe_error(exc)}.")
            return None

        price = _to_float(payload.get("c"))
        if price is None or price <= 0:
            return None

        previous_close = _to_float(payload.get("pc"))
        change = _to_float(payload.get("d"))
        if change is None and previous_close is not None:
            change = price - previous_close

        change_percent = _to_float(payload.get("dp"))
        if change_percent is None and previous_close:
            change_percent = ((price - previous_close) / previous_close) * 100

        return MarketQuote(
            symbol=symbol,
            price=price,
            change=change,
            change_percent=change_percent,
            previous_close=previous_close,
            open_price=_to_float(payload.get("o")),
            high=_to_float(payload.get("h")),
            low=_to_float(payload.get("l")),
            currency="USD",
            provider="finnhub",
            provider_name="Finnhub",
            market_time=_timestamp_to_iso(payload.get("t")),
            fetched_at=_utc_now(),
            is_realtime=True,
            delay_note="Finnhub quote endpoint; exchange entitlement may still affect delay.",
        )

    results = await asyncio.gather(*(fetch_one(symbol) for symbol in symbols))
    return [quote for quote in results if quote], warnings


async def _fetch_alpha_vantage_quotes(
    client: httpx.AsyncClient,
    symbols: list[str],
    api_key: str,
) -> tuple[list[MarketQuote], list[str]]:
    warnings: list[str] = []

    async def fetch_one(symbol: str) -> Optional[MarketQuote]:
        try:
            response = await client.get(
                "https://www.alphavantage.co/query",
                params={
                    "function": "GLOBAL_QUOTE",
                    "symbol": symbol,
                    "apikey": api_key,
                },
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Alpha Vantage failed for {symbol}: {_safe_error(exc)}.")
            return None

        if payload.get("Note") or payload.get("Information"):
            warnings.append(f"Alpha Vantage limit/message for {symbol}.")
            return None

        quote = payload.get("Global Quote") or {}
        price = _to_float(quote.get("05. price"))
        if price is None or price <= 0:
            return None

        return MarketQuote(
            symbol=symbol,
            price=price,
            change=_to_float(quote.get("09. change")),
            change_percent=_to_float(quote.get("10. change percent")),
            previous_close=_to_float(quote.get("08. previous close")),
            open_price=_to_float(quote.get("02. open")),
            high=_to_float(quote.get("03. high")),
            low=_to_float(quote.get("04. low")),
            volume=_to_float(quote.get("06. volume")),
            currency="USD",
            provider="alpha_vantage",
            provider_name="Alpha Vantage",
            market_time=quote.get("07. latest trading day"),
            fetched_at=_utc_now(),
            is_realtime=False,
            delay_note="Free tier latest available quote; not guaranteed real-time.",
        )

    results = await asyncio.gather(*(fetch_one(symbol) for symbol in symbols))
    return [quote for quote in results if quote], warnings


async def _fetch_stooq_quotes(
    client: httpx.AsyncClient,
    symbols: list[str],
) -> tuple[list[MarketQuote], list[str]]:
    if not symbols:
        return [], []

    warnings: list[str] = []

    async def fetch_one(symbol: str) -> Optional[MarketQuote]:
        try:
            response = await client.get(
                "https://stooq.com/q/l/",
                params={
                    "s": _to_stooq_symbol(symbol),
                    "f": "sd2t2ohlcv",
                    "h": "",
                    "e": "csv",
                },
            )
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Stooq fallback failed for {symbol}: {_safe_error(exc)}.")
            return None

        rows = list(csv.DictReader(StringIO(response.text.strip())))
        if not rows:
            return None

        row = rows[0]
        resolved_symbol = _from_stooq_symbol(row.get("Symbol", ""))
        if resolved_symbol != symbol:
            return None
        price = _to_float(row.get("Close"))
        if price is None or price <= 0:
            return None

        open_price = _to_float(row.get("Open"))
        change = price - open_price if open_price else None
        change_percent = ((change / open_price) * 100) if change is not None and open_price else None
        market_time = _join_market_time(row.get("Date"), row.get("Time"))
        return MarketQuote(
            symbol=symbol,
            price=price,
            change=change,
            change_percent=change_percent,
            open_price=open_price,
            high=_to_float(row.get("High")),
            low=_to_float(row.get("Low")),
            volume=_to_float(row.get("Volume")),
            currency="USD",
            provider="stooq",
            provider_name="Stooq",
            market_time=market_time,
            fetched_at=_utc_now(),
            is_realtime=False,
            delay_note="No-key public fallback; usually delayed or latest snapshot.",
        )

    results = await asyncio.gather(*(fetch_one(symbol) for symbol in symbols))
    quotes = [quote for quote in results if quote]
    if not quotes:
        warnings.append("Stooq fallback returned no usable quotes.")
    return quotes, warnings


def _merge_quotes(target: dict[str, MarketQuote], quotes: list[MarketQuote]) -> None:
    for quote in quotes:
        target.setdefault(quote.symbol, quote)


def _to_float(value: object) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("%", "")
    if not text or text.upper() in {"N/A", "N/D", "NULL", "NONE", "-"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _timestamp_to_iso(value: object) -> Optional[str]:
    timestamp = _to_float(value)
    if not timestamp:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    return exc.__class__.__name__


def _to_stooq_symbol(symbol: str) -> str:
    if "." in symbol:
        return symbol.lower()
    return f"{symbol.lower()}.us"


def _from_stooq_symbol(symbol: str) -> str:
    return symbol.strip().upper().split(".")[0]


def _join_market_time(date_value: object, time_value: object) -> Optional[str]:
    date_text = str(date_value or "").strip()
    time_text = str(time_value or "").strip()
    if not date_text or date_text.upper() in {"N/A", "N/D"}:
        return None
    if time_text and time_text.upper() not in {"N/A", "N/D"}:
        return f"{date_text}T{time_text}"
    return date_text
