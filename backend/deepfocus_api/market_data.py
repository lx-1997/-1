from __future__ import annotations

import asyncio
import csv
import os
import re
from datetime import datetime, timezone
from io import StringIO
from typing import Any, Iterable, Optional

import httpx

from .schemas import MarketQuote, MarketQuoteListResponse, MarketSymbolCandidate, MarketSymbolSearchResponse


MAX_SYMBOLS = 30
MAX_SEARCH_RESULTS = 12
REQUEST_TIMEOUT = httpx.Timeout(8.0, connect=4.0)
EASTMONEY_SEARCH_TOKEN = "D43BF722C8E33BDC906FB84D85E326E8"


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


async def search_market_symbols(query: str, market: Optional[str] = None) -> MarketSymbolSearchResponse:
    cleaned_query = query.strip()
    normalized_market = _normalize_market_filter(market)
    fetched_at = _utc_now()
    warnings: list[str] = []

    if not cleaned_query:
        return MarketSymbolSearchResponse(
            query=query,
            market=normalized_market,
            candidates=[],
            provider="none",
            fetched_at=fetched_at,
            warnings=["No query supplied."],
        )

    headers = {"User-Agent": "DeepFocus/0.1 market-search"}
    candidates: list[MarketSymbolCandidate] = []
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers=headers) as client:
        try:
            response = await client.get(
                "https://searchapi.eastmoney.com/api/suggest/get",
                params={
                    "input": cleaned_query,
                    "type": "14",
                    "token": EASTMONEY_SEARCH_TOKEN,
                    "count": str(MAX_SEARCH_RESULTS),
                },
            )
            response.raise_for_status()
            payload = response.json()
            rows = ((payload.get("QuotationCodeTable") or {}).get("Data") or [])
            candidates = [
                candidate
                for candidate in (_candidate_from_eastmoney(row) for row in rows)
                if candidate and (not normalized_market or candidate.market == normalized_market)
            ]
        except Exception as exc:  # noqa: BLE001 - search must degrade to direct symbol entry
            warnings.append(f"Eastmoney search failed: {_safe_error(exc)}.")

    if not candidates:
        fallback = _direct_symbol_candidate(cleaned_query, normalized_market)
        if fallback:
            candidates.append(fallback)

    return MarketSymbolSearchResponse(
        query=query,
        market=normalized_market,
        candidates=_dedupe_candidates(candidates)[:MAX_SEARCH_RESULTS],
        provider="eastmoney" if candidates else "none",
        fetched_at=fetched_at,
        warnings=warnings,
    )


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
            china_symbols = [symbol for symbol in missing if _infer_market(symbol) in {"CN", "HK"}]
            if china_symbols:
                quotes, provider_warnings = await _fetch_sina_quotes(client, china_symbols)
                _merge_quotes(quote_by_symbol, quotes)
                warnings.extend(provider_warnings)
                missing = [symbol for symbol in requested_symbols if symbol not in quote_by_symbol]

        if missing:
            china_symbols = [symbol for symbol in missing if _infer_market(symbol) in {"CN", "HK"}]
            if china_symbols:
                quotes, provider_warnings = await _fetch_eastmoney_quotes(client, china_symbols)
                _merge_quotes(quote_by_symbol, quotes)
                warnings.extend(provider_warnings)
                missing = [symbol for symbol in requested_symbols if symbol not in quote_by_symbol]

        if missing:
            quotes, provider_warnings = await _fetch_stooq_quotes(client, missing)
            _merge_quotes(quote_by_symbol, quotes)
            warnings.extend(provider_warnings)
            missing = [symbol for symbol in requested_symbols if symbol not in quote_by_symbol]

        if missing:
            quotes, provider_warnings = await _fetch_sina_quotes(client, missing)
            _merge_quotes(quote_by_symbol, quotes)
            warnings.extend(provider_warnings)
            missing = [symbol for symbol in requested_symbols if symbol not in quote_by_symbol]

        if missing:
            quotes, provider_warnings = await _fetch_eastmoney_quotes(client, missing)
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


async def _fetch_eastmoney_quotes(
    client: httpx.AsyncClient,
    symbols: list[str],
) -> tuple[list[MarketQuote], list[str]]:
    if not symbols:
        return [], []

    warnings: list[str] = []

    async def fetch_one(symbol: str) -> Optional[MarketQuote]:
        resolved = _eastmoney_security(symbol)
        if not resolved:
            return None
        secid, _, market, currency, price_divisor = resolved

        try:
            response = await client.get(
                "https://push2.eastmoney.com/api/qt/stock/get",
                params={
                    "secid": secid,
                    "fields": "f43,f44,f45,f46,f47,f48,f57,f58,f60,f169,f170,f152",
                },
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Eastmoney quote failed for {symbol}: {_safe_error(exc)}.")
            return None

        data = payload.get("data") or {}
        price = _scaled_number(data.get("f43"), price_divisor)
        if price is None or price <= 0:
            return None

        previous_close = _scaled_number(data.get("f60"), price_divisor)
        change = _scaled_number(data.get("f169"), price_divisor)
        if change is None and previous_close:
            change = price - previous_close
        change_percent = _scaled_number(data.get("f170"), 100)
        if change_percent is None and previous_close:
            change_percent = ((price - previous_close) / previous_close) * 100

        return MarketQuote(
            symbol=symbol,
            price=price,
            change=change,
            change_percent=change_percent,
            previous_close=previous_close,
            open_price=_scaled_number(data.get("f46"), price_divisor),
            high=_scaled_number(data.get("f44"), price_divisor),
            low=_scaled_number(data.get("f45"), price_divisor),
            volume=_to_float(data.get("f47")),
            currency=currency,
            provider="eastmoney",
            provider_name="东方财富公共行情",
            market_time=None,
            fetched_at=_utc_now(),
            is_realtime=False,
            delay_note=f"免费公共行情快照，覆盖{market}市场；稳定性依赖公开接口可用性。",
        )

    results = await asyncio.gather(*(fetch_one(symbol) for symbol in symbols))
    quotes = [quote for quote in results if quote]
    if not quotes:
        warnings.append("Eastmoney public quote returned no usable quotes.")
    return quotes, warnings


async def _fetch_sina_quotes(
    client: httpx.AsyncClient,
    symbols: list[str],
) -> tuple[list[MarketQuote], list[str]]:
    entries = [entry for entry in (_sina_symbol(symbol) for symbol in symbols) if entry]
    if not entries:
        return [], []

    warnings: list[str] = []
    code_to_symbol = {code: symbol for symbol, code, _market, _currency in entries}
    code_to_meta = {code: (market, currency) for _symbol, code, market, currency in entries}

    try:
        response = await client.get(
            "https://hq.sinajs.cn/list=" + ",".join(code_to_symbol),
            headers={
                "Referer": "https://finance.sina.com.cn",
                "User-Agent": "Mozilla/5.0",
            },
        )
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"Sina quote failed: {_safe_error(exc)}.")
        return [], warnings

    quotes: list[MarketQuote] = []
    for match in re.finditer(r"var hq_str_([^=]+)=\"(.*?)\";", response.text, flags=re.S):
        provider_code, raw = match.groups()
        symbol = code_to_symbol.get(provider_code)
        if not symbol or not raw:
            continue
        fields = raw.split(",")
        market, currency = code_to_meta.get(provider_code, ("OTHER", "USD"))
        quote = _parse_sina_quote(symbol, provider_code, fields, market, currency)
        if quote:
            quotes.append(quote)

    if not quotes:
        warnings.append("Sina public quote returned no usable quotes.")
    return quotes, warnings


def _parse_sina_quote(
    symbol: str,
    provider_code: str,
    fields: list[str],
    market: str,
    currency: str,
) -> Optional[MarketQuote]:
    if provider_code.startswith(("sh", "sz", "bj")) and len(fields) >= 32:
        price = _to_float(fields[3])
        previous_close = _to_float(fields[2])
        if price is None or price <= 0:
            return None
        change = price - previous_close if previous_close is not None else None
        change_percent = (change / previous_close * 100) if change is not None and previous_close else None
        market_time = f"{fields[30]}T{fields[31]}" if fields[30] and fields[31] else None
        return _sina_quote_record(
            symbol=symbol,
            price=price,
            change=change,
            change_percent=change_percent,
            previous_close=previous_close,
            open_price=_to_float(fields[1]),
            high=_to_float(fields[4]),
            low=_to_float(fields[5]),
            volume=_to_float(fields[8]),
            currency=currency,
            market_time=market_time,
            market=market,
        )

    if provider_code.startswith("hk") and len(fields) >= 19:
        price = _to_float(fields[6])
        if price is None or price <= 0:
            return None
        date_text = str(fields[17] or "").replace("/", "-")
        market_time = f"{date_text}T{fields[18]}" if date_text and fields[18] else None
        return _sina_quote_record(
            symbol=symbol,
            price=price,
            change=_to_float(fields[7]),
            change_percent=_to_float(fields[8]),
            previous_close=_to_float(fields[3]),
            open_price=_to_float(fields[2]),
            high=_to_float(fields[4]),
            low=_to_float(fields[5]),
            volume=_to_float(fields[12]),
            currency=currency,
            market_time=market_time,
            market=market,
        )

    if provider_code.startswith("gb_") and len(fields) >= 11:
        price = _to_float(fields[1])
        if price is None or price <= 0:
            return None
        return _sina_quote_record(
            symbol=symbol,
            price=price,
            change=_to_float(fields[4]),
            change_percent=_to_float(fields[2]),
            previous_close=_to_float(fields[26]) if len(fields) > 26 else None,
            open_price=_to_float(fields[5]),
            high=_to_float(fields[6]),
            low=_to_float(fields[7]),
            volume=_to_float(fields[10]),
            currency=currency,
            market_time=fields[3] or None,
            market=market,
        )

    return None


def _sina_quote_record(
    *,
    symbol: str,
    price: float,
    change: Optional[float],
    change_percent: Optional[float],
    previous_close: Optional[float],
    open_price: Optional[float],
    high: Optional[float],
    low: Optional[float],
    volume: Optional[float],
    currency: str,
    market_time: Optional[str],
    market: str,
) -> MarketQuote:
    return MarketQuote(
        symbol=symbol,
        price=price,
        change=change,
        change_percent=change_percent,
        previous_close=previous_close,
        open_price=open_price,
        high=high,
        low=low,
        volume=volume,
        currency=currency,
        provider="sina",
        provider_name="新浪财经公共行情",
        market_time=market_time,
        fetched_at=_utc_now(),
        is_realtime=False,
        delay_note=f"免费公共行情快照，覆盖{market}市场；稳定性依赖公开接口可用性。",
    )


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
        if not _same_quote_symbol(resolved_symbol, symbol):
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


def _normalize_market_filter(market: Optional[str]) -> Optional[str]:
    if not market:
        return None
    value = market.strip().upper()
    return value if value in {"US", "HK", "CN", "OTHER"} else None


def _candidate_from_eastmoney(row: dict[str, Any]) -> Optional[MarketSymbolCandidate]:
    code = str(row.get("Code") or row.get("UnifiedCode") or "").strip().upper()
    name = str(row.get("Name") or "").strip()
    quote_id = str(row.get("QuoteID") or "").strip()
    classify = str(row.get("Classify") or "")
    security_type_name = str(row.get("SecurityTypeName") or "")
    security_type = str(row.get("SecurityType") or "")
    if not code or not name or not quote_id:
        return None

    market = "OTHER"
    symbol = code
    exchange = security_type_name or str(row.get("JYS") or "")
    if classify == "UsStock" and security_type in {"20", "21"}:
        market = "US"
        symbol = code
    elif classify == "HK" or security_type_name == "港股":
        market = "HK"
        symbol = f"{code.zfill(5)}.HK"
    elif classify == "AStock" or security_type_name in {"沪A", "深A", "北证"}:
        market = "CN"
        suffix = "SH" if quote_id.startswith("1.") else "BJ" if "北" in security_type_name else "SZ"
        symbol = f"{code}.{suffix}"
    else:
        return None

    return MarketSymbolCandidate(
        symbol=symbol,
        code=code,
        name=name,
        market=market,
        exchange=exchange,
        security_type=security_type_name,
        quote_id=quote_id,
        provider="eastmoney",
        provider_name="东方财富公共搜索",
    )


def _direct_symbol_candidate(query: str, market: Optional[str]) -> Optional[MarketSymbolCandidate]:
    cleaned = "".join(char for char in query.strip().upper() if char.isalnum() or char in {".", "-"})
    if not cleaned:
        return None

    inferred_market = market or _infer_market(cleaned)
    if inferred_market == "CN":
        code = cleaned.split(".")[0]
        if not re.fullmatch(r"\d{6}", code):
            return None
        suffix = "SH" if cleaned.endswith(".SH") or code.startswith("6") else "BJ" if cleaned.endswith(".BJ") else "SZ"
        return MarketSymbolCandidate(
            symbol=f"{code}.{suffix}",
            code=code,
            name=f"{code} A股",
            market="CN",
            exchange=suffix,
            security_type="A股",
            quote_id=_eastmoney_security(f"{code}.{suffix}")[0] if _eastmoney_security(f"{code}.{suffix}") else None,
            provider="local",
            provider_name="本地符号识别",
        )
    if inferred_market == "HK":
        code = cleaned.replace(".HK", "").zfill(5)
        if not re.fullmatch(r"\d{5}", code):
            return None
        return MarketSymbolCandidate(
            symbol=f"{code}.HK",
            code=code,
            name=f"{code} 港股",
            market="HK",
            exchange="HK",
            security_type="港股",
            quote_id=f"116.{code}",
            provider="local",
            provider_name="本地符号识别",
        )
    if inferred_market == "US":
        code = cleaned.replace(".US", "")
        if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", code):
            return None
        return MarketSymbolCandidate(
            symbol=code,
            code=code,
            name=code,
            market="US",
            exchange="US",
            security_type="美股",
            quote_id=f"105.{code}",
            provider="local",
            provider_name="本地符号识别",
        )
    return None


def _dedupe_candidates(candidates: list[MarketSymbolCandidate]) -> list[MarketSymbolCandidate]:
    deduped: list[MarketSymbolCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = candidate.symbol.upper()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def _infer_market(symbol: str) -> str:
    value = symbol.strip().upper()
    base = value.split(".")[0]
    if value.endswith((".SH", ".SZ", ".BJ")) or re.fullmatch(r"\d{6}", base):
        return "CN"
    if value.endswith(".HK") or re.fullmatch(r"\d{5}", base):
        return "HK"
    if re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", value.replace(".US", "")):
        return "US"
    return "OTHER"


def _eastmoney_security(symbol: str) -> Optional[tuple[str, str, str, str, float]]:
    value = symbol.strip().upper()
    code = value.split(".")[0]
    if value.endswith(".SH") or (re.fullmatch(r"\d{6}", code) and code.startswith("6")):
        return f"1.{code}", f"{code}.SH", "A股", "CNY", 100
    if value.endswith(".SZ") or value.endswith(".BJ") or re.fullmatch(r"[03]\d{5}", code):
        suffix = "BJ" if value.endswith(".BJ") else "SZ"
        return f"0.{code}", f"{code}.{suffix}", "A股", "CNY", 100
    if value.endswith(".HK") or re.fullmatch(r"\d{5}", code):
        hk_code = code.zfill(5)
        return f"116.{hk_code}", f"{hk_code}.HK", "港股", "HKD", 1000
    if re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", value.replace(".US", "")):
        us_code = value.replace(".US", "")
        return f"105.{us_code}", us_code, "美股", "USD", 1000
    return None


def _sina_symbol(symbol: str) -> Optional[tuple[str, str, str, str]]:
    value = symbol.strip().upper()
    code = value.split(".")[0]
    if value.endswith(".SH") or (re.fullmatch(r"\d{6}", code) and code.startswith("6")):
        return symbol, f"sh{code}", "A股", "CNY"
    if value.endswith(".SZ") or re.fullmatch(r"[03]\d{5}", code):
        return symbol, f"sz{code}", "A股", "CNY"
    if value.endswith(".BJ"):
        return symbol, f"bj{code}", "A股", "CNY"
    if value.endswith(".HK") or re.fullmatch(r"\d{5}", code):
        return symbol, f"hk{code.zfill(5)}", "港股", "HKD"
    if re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", value.replace(".US", "")):
        return symbol, f"gb_{value.replace('.US', '').lower()}", "美股", "USD"
    return None


def _scaled_number(value: object, divisor: float) -> Optional[float]:
    raw = _to_float(value)
    if raw is None:
        return None
    return raw / divisor


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


def _same_quote_symbol(resolved_symbol: str, requested_symbol: str) -> bool:
    requested_base = requested_symbol.strip().upper().split(".")[0]
    return resolved_symbol.strip().upper() == requested_base


def _join_market_time(date_value: object, time_value: object) -> Optional[str]:
    date_text = str(date_value or "").strip()
    time_text = str(time_value or "").strip()
    if not date_text or date_text.upper() in {"N/A", "N/D"}:
        return None
    if time_text and time_text.upper() not in {"N/A", "N/D"}:
        return f"{date_text}T{time_text}"
    return date_text
