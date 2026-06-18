from __future__ import annotations

import asyncio
import math
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional

import httpx

from .shared_utils import to_float, safe_error, dedupe, clamp, utc_now_dt
from .schemas import (

    OptionsExpirationSignal,
    OptionsGammaStrike,
    OptionsKeyStrike,
    OptionsSignal,
    OptionsSignalResponse,
    OptionsSourceStatus,
    OptionsUnusualFlow,
)


MAX_SYMBOLS = 12
REQUEST_TIMEOUT = httpx.Timeout(10.0, connect=4.0)
DEFAULT_RISK_FREE_RATE = 0.045


@dataclass
class OptionContract:
    symbol: str
    option_symbol: str
    side: str
    strike: float
    expiration: str
    dte: Optional[int] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    mid: Optional[float] = None
    last: Optional[float] = None
    volume: Optional[float] = None
    open_interest: Optional[float] = None
    iv: Optional[float] = None
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    gamma_estimated: bool = False
    underlying_price: Optional[float] = None
    updated_at: Optional[str] = None


@dataclass
class PriceAction:
    symbol: str
    latest_date: str
    close: Optional[float] = None
    latest_change_pct: Optional[float] = None
    five_day_change_pct: Optional[float] = None
    twenty_day_change_pct: Optional[float] = None
    close_vs_20d_avg_pct: Optional[float] = None
    latest_volume_vs_5d: Optional[float] = None
    latest_range_pct: Optional[float] = None
    recent_large_drop_count: int = 0
    recent_volume_spike_count: int = 0
    max_recent_drop_pct: Optional[float] = None


def normalize_option_symbols(symbols: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    for symbol in symbols:
        cleaned = "".join(
            char for char in symbol.strip().upper()
            if char.isalnum() or char in {".", "-", "^"}
        )
        if not cleaned or cleaned in normalized:
            continue
        normalized.append(cleaned)
        if len(normalized) >= MAX_SYMBOLS:
            break
    return normalized


async def fetch_options_signals(
    symbols: Iterable[str],
    horizon_days: int = 45,
    max_expirations: int = 3,
) -> OptionsSignalResponse:
    requested_symbols = normalize_option_symbols(symbols)
    horizon_days = max(7, min(int(horizon_days or 45), 180))
    max_expirations = max(1, min(int(max_expirations or 3), 6))
    generated_at = utc_now_dt()
    warnings: list[str] = []

    if not requested_symbols:
        return OptionsSignalResponse(
            generated_at=generated_at,
            horizon_days=horizon_days,
            provider="none",
            signals=[],
            sources=_source_profile(),
            warnings=["No valid option symbols were supplied."],
        )

    headers = {
        "User-Agent": "Mozilla/5.0 DeepFocus/0.1 options-signal",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://www.nasdaq.com",
        "Referer": "https://www.nasdaq.com/",
    }
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers=headers) as client:
        analyses = await asyncio.gather(
            *(
                _analyze_symbol(client, symbol, horizon_days, max_expirations)
                for symbol in requested_symbols
            )
        )
        price_analyses = await asyncio.gather(
            *(_fetch_nasdaq_price_action(client, symbol) for symbol in requested_symbols)
        )

    signals: list[OptionsSignal] = []
    for signal, signal_warnings in analyses:
        signals.append(signal)
        warnings.extend(signal_warnings)
    price_actions: dict[str, PriceAction] = {}
    for price_action, price_warnings in price_analyses:
        if price_action:
            price_actions[price_action.symbol] = price_action
        warnings.extend(price_warnings)

    signals = _apply_tail_event_risk(signals, price_actions)

    providers = {signal.provider for signal in signals if signal.provider != "none"}
    if len(providers) == 1:
        provider = next(iter(providers))
    elif providers:
        provider = "mixed"
    else:
        provider = "none"

    return OptionsSignalResponse(
        generated_at=generated_at,
        horizon_days=horizon_days,
        provider=provider,
        signals=signals,
        sources=_source_profile(),
        warnings=dedupe(warnings),
    )


async def _analyze_symbol(
    client: httpx.AsyncClient,
    symbol: str,
    horizon_days: int,
    max_expirations: int,
) -> tuple[OptionsSignal, list[str]]:
    diagnostics: list[str] = []

    providers = []
    if _has_marketdata_token():
        providers.append(_fetch_marketdata_app_contracts)
    if _has_tradier_token():
        providers.append(_fetch_tradier_contracts)
    providers.extend([
        _fetch_nasdaq_public_contracts,
        _fetch_yahoo_public_contracts,
    ])
    for provider in providers:
        contracts, provider_name, delay_note, provider_warnings = await provider(
            client,
            symbol,
            horizon_days,
            max_expirations,
        )
        if contracts:
            return (
                _build_signal(
                    symbol=symbol,
                    contracts=contracts,
                    provider=provider.__name__.replace("_fetch_", "").replace("_contracts", ""),
                    provider_name=provider_name,
                    delay_note=delay_note,
                    horizon_days=horizon_days,
                ),
                provider_warnings,
            )
        diagnostics.extend(provider_warnings)

    return (
        _empty_signal(
            symbol=symbol,
            warning="免费期权链源暂未返回可用合约；可配置 MARKETDATA_APP_TOKEN、TRADIER_ACCESS_TOKEN 或稍后重试。",
        ),
        diagnostics,
    )


async def _fetch_marketdata_app_contracts(
    client: httpx.AsyncClient,
    symbol: str,
    horizon_days: int,
    max_expirations: int,
) -> tuple[list[OptionContract], str, str, list[str]]:
    warnings: list[str] = []
    headers = _marketdata_auth_headers()
    provider_name = "MarketData.app"
    delay_note = "MarketData.app options chain；免费层通常为延迟/日终数据，具体时效取决于账户权限。"
    if not headers:
        return [], provider_name, delay_note, []

    try:
        response = await client.get(
            f"https://api.marketdata.app/v1/options/expirations/{symbol}/",
            headers=headers,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        return [], provider_name, delay_note, [f"MarketData.app expirations failed for {symbol}: {safe_error(exc)}."]

    if payload.get("s") != "ok":
        message = payload.get("errmsg") or "unknown provider message"
        return [], provider_name, delay_note, [f"MarketData.app unavailable for {symbol}: {message}."]

    expirations = [
        item for item in payload.get("expirations", [])
        if isinstance(item, str)
    ]
    selected_expirations = _select_expirations(expirations, horizon_days, max_expirations)
    if not selected_expirations:
        return [], provider_name, delay_note, [f"MarketData.app returned no future expirations for {symbol}."]

    contracts: list[OptionContract] = []
    for expiration in selected_expirations:
        try:
            response = await client.get(
                f"https://api.marketdata.app/v1/options/chain/{symbol}/",
                params={"expiration": expiration},
                headers=headers,
            )
            response.raise_for_status()
            chain = response.json()
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"MarketData.app chain failed for {symbol} {expiration}: {safe_error(exc)}.")
            continue

        if chain.get("s") != "ok":
            message = chain.get("errmsg") or "unknown provider message"
            warnings.append(f"MarketData.app chain unavailable for {symbol} {expiration}: {message}.")
            continue

        contracts.extend(_contracts_from_marketdata(symbol, expiration, chain))

    return contracts, provider_name, delay_note, warnings


async def _fetch_nasdaq_public_contracts(
    client: httpx.AsyncClient,
    symbol: str,
    horizon_days: int,
    max_expirations: int,
) -> tuple[list[OptionContract], str, str, list[str]]:
    provider_name = "Nasdaq Public Option Chain"
    delay_note = "Nasdaq 公开网页期权链；免费延迟快照，常缺 IV/Greeks，bid/ask 可能为空。"
    try:
        response = await client.get(
            f"https://api.nasdaq.com/api/quote/{symbol}/option-chain",
            params={
                "assetclass": "stocks",
                "limit": "9999",
                "callput": "callput",
                "money": "all",
                "type": "all",
            },
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        return [], provider_name, delay_note, [f"Nasdaq public option chain failed for {symbol}: {safe_error(exc)}."]

    contracts = _contracts_from_nasdaq(symbol, payload, horizon_days, max_expirations)
    return contracts, provider_name, delay_note, []


async def _fetch_yahoo_public_contracts(
    client: httpx.AsyncClient,
    symbol: str,
    horizon_days: int,
    max_expirations: int,
) -> tuple[list[OptionContract], str, str, list[str]]:
    provider_name = "Yahoo Finance Public Chain"
    delay_note = "Yahoo Finance 公共期权链；无官方 SLA，可能因地区、风控或 Cookie 策略不可用。"
    warnings: list[str] = []
    try:
        response = await client.get(f"https://query1.finance.yahoo.com/v7/finance/options/{symbol}")
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        return [], provider_name, delay_note, [f"Yahoo options chain failed for {symbol}: {safe_error(exc)}."]

    result = ((payload.get("optionChain") or {}).get("result") or [])
    if not result:
        return [], provider_name, delay_note, [f"Yahoo options chain returned no result for {symbol}."]

    first = result[0]
    expiration_dates = [
        _date_from_timestamp(item)
        for item in first.get("expirationDates", [])
        if _date_from_timestamp(item)
    ]
    selected_expirations = _select_expirations(expiration_dates, horizon_days, max_expirations)
    if not selected_expirations and first.get("options"):
        return _contracts_from_yahoo_result(symbol, first), provider_name, delay_note, warnings

    contracts: list[OptionContract] = []
    for expiration in selected_expirations:
        timestamp = _timestamp_from_date(expiration)
        if timestamp is None:
            continue
        try:
            response = await client.get(
                f"https://query1.finance.yahoo.com/v7/finance/options/{symbol}",
                params={"date": timestamp},
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Yahoo options chain failed for {symbol} {expiration}: {safe_error(exc)}.")
            continue
        result = ((payload.get("optionChain") or {}).get("result") or [])
        if result:
            contracts.extend(_contracts_from_yahoo_result(symbol, result[0]))
    return contracts, provider_name, delay_note, warnings


async def _fetch_tradier_contracts(
    client: httpx.AsyncClient,
    symbol: str,
    horizon_days: int,
    max_expirations: int,
) -> tuple[list[OptionContract], str, str, list[str]]:
    provider_name = "Tradier Options Chain"
    delay_note = "Tradier options chain；Greek/IV 字段来自 ORATS，实时性取决于账户与 OPRA 权限。"
    headers = _tradier_auth_headers()
    if not headers:
        return [], provider_name, delay_note, []

    warnings: list[str] = []
    try:
        response = await client.get(
            "https://api.tradier.com/v1/markets/options/expirations",
            params={"symbol": symbol, "includeAllRoots": "true"},
            headers=headers,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        return [], provider_name, delay_note, [f"Tradier expirations failed for {symbol}: {safe_error(exc)}."]

    expirations_payload = (payload.get("expirations") or {}).get("date") or []
    if isinstance(expirations_payload, str):
        expirations = [expirations_payload]
    else:
        expirations = [item for item in expirations_payload if isinstance(item, str)]
    selected_expirations = _select_expirations(expirations, horizon_days, max_expirations)
    if not selected_expirations:
        return [], provider_name, delay_note, [f"Tradier returned no future expirations for {symbol}."]

    underlying_price = await _fetch_tradier_underlying_price(client, symbol, headers)
    contracts: list[OptionContract] = []
    for expiration in selected_expirations:
        try:
            response = await client.get(
                "https://api.tradier.com/v1/markets/options/chains",
                params={"symbol": symbol, "expiration": expiration, "greeks": "true"},
                headers=headers,
            )
            response.raise_for_status()
            chain = response.json()
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Tradier chain failed for {symbol} {expiration}: {safe_error(exc)}.")
            continue
        contracts.extend(_contracts_from_tradier_chain(symbol, expiration, chain, underlying_price))

    return contracts, provider_name, delay_note, warnings


async def _fetch_tradier_underlying_price(
    client: httpx.AsyncClient,
    symbol: str,
    headers: dict[str, str],
) -> Optional[float]:
    try:
        response = await client.get(
            "https://api.tradier.com/v1/markets/quotes",
            params={"symbols": symbol},
            headers=headers,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:  # noqa: BLE001 - underlying price is useful but not mandatory
        return None
    quote = (payload.get("quotes") or {}).get("quote")
    if isinstance(quote, list):
        quote = quote[0] if quote else None
    if not isinstance(quote, dict):
        return None
    return _first_number([
        to_float(quote.get("last")),
        to_float(quote.get("close")),
        to_float(quote.get("prevclose")),
    ])


async def _fetch_nasdaq_price_action(
    client: httpx.AsyncClient,
    symbol: str,
) -> tuple[Optional[PriceAction], list[str]]:
    to_date = date.today()
    from_date = to_date - timedelta(days=45)
    try:
        response = await client.get(
            f"https://api.nasdaq.com/api/quote/{symbol}/historical",
            params={
                "assetclass": "stocks",
                "fromdate": from_date.isoformat(),
                "todate": to_date.isoformat(),
                "limit": "9999",
            },
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001 - price context should never break options
        return None, [f"Nasdaq historical price failed for {symbol}: {safe_error(exc)}."]

    rows = (((payload.get("data") or {}).get("tradesTable") or {}).get("rows") or [])
    parsed_rows: list[dict[str, Any]] = []
    for row in rows:
        raw_date = str(row.get("date") or "").strip()
        try:
            row_date = datetime.strptime(raw_date, "%m/%d/%Y").date()
        except ValueError:
            continue
        close = to_float(row.get("close"))
        high = to_float(row.get("high"))
        low = to_float(row.get("low"))
        volume = to_float(row.get("volume"))
        if close is None or close <= 0:
            continue
        parsed_rows.append({
            "date": row_date,
            "close": close,
            "high": high,
            "low": low,
            "volume": volume,
        })

    parsed_rows = sorted(parsed_rows, key=lambda item: item["date"])
    if len(parsed_rows) < 2:
        return None, [f"Nasdaq historical price returned too few rows for {symbol}."]

    for index, row in enumerate(parsed_rows):
        previous = parsed_rows[index - 1] if index > 0 else None
        if previous and previous.get("close"):
            row["change_pct"] = ((row["close"] / previous["close"]) - 1) * 100
        else:
            row["change_pct"] = None
        previous_volumes = [
            item["volume"] for item in parsed_rows[max(0, index - 5):index]
            if item.get("volume") is not None and item.get("volume") > 0
        ]
        if len(previous_volumes) >= 3 and row.get("volume"):
            row["volume_vs_5d"] = row["volume"] / (sum(previous_volumes) / len(previous_volumes))
        else:
            row["volume_vs_5d"] = None
        if row.get("high") is not None and row.get("low") is not None and row["close"] > 0:
            row["range_pct"] = ((row["high"] - row["low"]) / row["close"]) * 100
        else:
            row["range_pct"] = None

    recent = parsed_rows[-12:]
    latest = parsed_rows[-1]
    large_drop_rows = [
        row for row in recent
        if row.get("change_pct") is not None
        and row["change_pct"] <= -5
        and (
            (row.get("volume_vs_5d") is not None and row["volume_vs_5d"] >= 1.2)
            or (row.get("range_pct") is not None and row["range_pct"] >= 6)
        )
    ]
    volume_spike_rows = [
        row for row in recent
        if row.get("volume_vs_5d") is not None and row["volume_vs_5d"] >= 1.5
    ]
    recent_changes = [
        row["change_pct"] for row in recent
        if row.get("change_pct") is not None
    ]
    five_day_change_pct = None
    if len(parsed_rows) >= 6 and parsed_rows[-6].get("close"):
        five_day_change_pct = ((latest["close"] / parsed_rows[-6]["close"]) - 1) * 100
    twenty_day_change_pct = None
    if len(parsed_rows) >= 21 and parsed_rows[-21].get("close"):
        twenty_day_change_pct = ((latest["close"] / parsed_rows[-21]["close"]) - 1) * 100
    twenty_day_closes = [
        row["close"] for row in parsed_rows[-20:]
        if row.get("close") is not None and row.get("close") > 0
    ]
    close_vs_20d_avg_pct = None
    if len(twenty_day_closes) >= 10:
        avg_20d = sum(twenty_day_closes) / len(twenty_day_closes)
        if avg_20d > 0:
            close_vs_20d_avg_pct = ((latest["close"] / avg_20d) - 1) * 100

    return PriceAction(
        symbol=symbol,
        latest_date=latest["date"].isoformat(),
        close=latest.get("close"),
        latest_change_pct=latest.get("change_pct"),
        five_day_change_pct=five_day_change_pct,
        twenty_day_change_pct=twenty_day_change_pct,
        close_vs_20d_avg_pct=close_vs_20d_avg_pct,
        latest_volume_vs_5d=latest.get("volume_vs_5d"),
        latest_range_pct=latest.get("range_pct"),
        recent_large_drop_count=len(large_drop_rows),
        recent_volume_spike_count=len(volume_spike_rows),
        max_recent_drop_pct=min(recent_changes) if recent_changes else None,
    ), []


def _contracts_from_marketdata(symbol: str, selected_expiration: str, payload: dict[str, Any]) -> list[OptionContract]:
    option_symbols = payload.get("optionSymbol") or []
    contracts: list[OptionContract] = []
    for index in range(len(option_symbols)):
        side = str(_array_value(payload, "side", index) or "").lower()
        if side not in {"call", "put"}:
            continue
        strike = to_float(_array_value(payload, "strike", index))
        if strike is None:
            continue
        expiration = _date_from_timestamp(_array_value(payload, "expiration", index)) or selected_expiration
        mid = to_float(_array_value(payload, "mid", index))
        bid = to_float(_array_value(payload, "bid", index))
        ask = to_float(_array_value(payload, "ask", index))
        last = to_float(_array_value(payload, "last", index))
        if mid is None:
            mid = _mid_from_prices(bid, ask, last)
        contracts.append(
            OptionContract(
                symbol=symbol,
                option_symbol=str(option_symbols[index]),
                side=side,
                strike=strike,
                expiration=expiration,
                dte=_to_int(_array_value(payload, "dte", index)),
                bid=bid,
                ask=ask,
                mid=mid,
                last=last,
                volume=to_float(_array_value(payload, "volume", index)),
                open_interest=to_float(_array_value(payload, "openInterest", index)),
                iv=_normalize_iv(to_float(_array_value(payload, "iv", index))),
                delta=to_float(_array_value(payload, "delta", index)),
                gamma=to_float(_array_value(payload, "gamma", index)),
                theta=to_float(_array_value(payload, "theta", index)),
                vega=to_float(_array_value(payload, "vega", index)),
                underlying_price=to_float(_array_value(payload, "underlyingPrice", index)),
                updated_at=_timestamp_to_iso(_array_value(payload, "updated", index)),
            )
        )
    return contracts


def _contracts_from_tradier_chain(
    symbol: str,
    selected_expiration: str,
    payload: dict[str, Any],
    underlying_price: Optional[float],
) -> list[OptionContract]:
    options_payload = (payload.get("options") or {}).get("option") or []
    if isinstance(options_payload, dict):
        rows = [options_payload]
    elif isinstance(options_payload, list):
        rows = options_payload
    else:
        rows = []

    contracts: list[OptionContract] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        side = str(row.get("option_type") or row.get("type") or "").lower()
        if side not in {"call", "put"}:
            continue
        strike = to_float(row.get("strike"))
        if strike is None:
            continue
        greeks = row.get("greeks") if isinstance(row.get("greeks"), dict) else {}
        bid = to_float(row.get("bid"))
        ask = to_float(row.get("ask"))
        last = to_float(row.get("last"))
        expiration = str(row.get("expiration_date") or selected_expiration)
        contracts.append(
            OptionContract(
                symbol=symbol,
                option_symbol=str(row.get("symbol") or f"{symbol}-{expiration}-{side}-{strike:g}"),
                side=side,
                strike=strike,
                expiration=expiration,
                dte=_days_to_expiration(expiration),
                bid=bid,
                ask=ask,
                mid=_mid_from_prices(bid, ask, last),
                last=last,
                volume=to_float(row.get("volume")),
                open_interest=to_float(row.get("open_interest")),
                iv=_normalize_iv(_first_number([
                    to_float(greeks.get("mid_iv")),
                    to_float(greeks.get("smv_vol")),
                    to_float(greeks.get("bid_iv")),
                    to_float(greeks.get("ask_iv")),
                ])),
                delta=to_float(greeks.get("delta")),
                gamma=to_float(greeks.get("gamma")),
                theta=to_float(greeks.get("theta")),
                vega=to_float(greeks.get("vega")),
                underlying_price=underlying_price,
                updated_at=str(greeks.get("updated_at") or "") or None,
            )
        )
    return contracts


def _contracts_from_nasdaq(
    symbol: str,
    payload: dict[str, Any],
    horizon_days: int,
    max_expirations: int,
) -> list[OptionContract]:
    data = payload.get("data") or {}
    rows = ((data.get("table") or {}).get("rows") or [])
    underlying_price = _parse_last_trade_price(data.get("lastTrade"))
    current_expiration: Optional[str] = None
    contracts: list[OptionContract] = []

    for row in rows:
        expiry_group = row.get("expirygroup")
        if expiry_group:
            current_expiration = _parse_nasdaq_expiration(expiry_group)
            continue
        if not current_expiration:
            continue
        strike = to_float(row.get("strike"))
        if strike is None:
            continue
        for side, prefix in (("call", "c"), ("put", "p")):
            last = to_float(row.get(f"{prefix}_Last"))
            bid = to_float(row.get(f"{prefix}_Bid"))
            ask = to_float(row.get(f"{prefix}_Ask"))
            volume = to_float(row.get(f"{prefix}_Volume"))
            open_interest = to_float(row.get(f"{prefix}_Openinterest"))
            if all(value is None for value in (last, bid, ask, volume, open_interest)):
                continue
            contracts.append(
                OptionContract(
                    symbol=symbol,
                    option_symbol=f"{symbol}-{current_expiration}-{side[0].upper()}-{strike:g}",
                    side=side,
                    strike=strike,
                    expiration=current_expiration,
                    dte=_days_to_expiration(current_expiration),
                    bid=bid,
                    ask=ask,
                    mid=_mid_from_prices(bid, ask, last),
                    last=last,
                    volume=volume,
                    open_interest=open_interest,
                    underlying_price=underlying_price,
                )
            )

    selected = set(_select_expirations(
        sorted({contract.expiration for contract in contracts}),
        horizon_days,
        max_expirations,
    ))
    if not selected:
        return []
    return [contract for contract in contracts if contract.expiration in selected]


def _contracts_from_yahoo_result(symbol: str, result: dict[str, Any]) -> list[OptionContract]:
    quote = result.get("quote") or {}
    underlying_price = to_float(quote.get("regularMarketPrice"))
    options = result.get("options") or []
    if not options:
        return []
    payload = options[0]
    expiration = _date_from_timestamp(payload.get("expirationDate")) or ""
    contracts: list[OptionContract] = []
    for side, key in (("call", "calls"), ("put", "puts")):
        for row in payload.get(key, []) or []:
            strike = to_float(row.get("strike"))
            if strike is None or not expiration:
                continue
            bid = to_float(row.get("bid"))
            ask = to_float(row.get("ask"))
            last = to_float(row.get("lastPrice"))
            contracts.append(
                OptionContract(
                    symbol=symbol,
                    option_symbol=str(row.get("contractSymbol") or f"{symbol}-{expiration}-{side}-{strike:g}"),
                    side=side,
                    strike=strike,
                    expiration=expiration,
                    dte=_days_to_expiration(expiration),
                    bid=bid,
                    ask=ask,
                    mid=_mid_from_prices(bid, ask, last),
                    last=last,
                    volume=to_float(row.get("volume")),
                    open_interest=to_float(row.get("openInterest")),
                    iv=_normalize_iv(to_float(row.get("impliedVolatility"))),
                    underlying_price=underlying_price,
                    updated_at=_timestamp_to_iso(row.get("lastTradeDate")),
                )
            )
    return contracts


def _build_signal(
    symbol: str,
    contracts: list[OptionContract],
    provider: str,
    provider_name: str,
    delay_note: str,
    horizon_days: int,
) -> OptionsSignal:
    contracts = [
        contract for contract in contracts
        if contract.side in {"call", "put"} and contract.strike > 0
    ]
    underlying_price = _first_number(contract.underlying_price for contract in contracts)
    expirations = _build_expiration_signals(contracts, underlying_price)

    call_contracts = [contract for contract in contracts if contract.side == "call"]
    put_contracts = [contract for contract in contracts if contract.side == "put"]
    call_volume = _sum_field(call_contracts, "volume")
    put_volume = _sum_field(put_contracts, "volume")
    call_open_interest = _sum_field(call_contracts, "open_interest")
    put_open_interest = _sum_field(put_contracts, "open_interest")
    pcr_volume = _safe_ratio(put_volume, call_volume)
    pcr_open_interest = _safe_ratio(put_open_interest, call_open_interest)

    call_wall = _strike_with_max(call_contracts, "open_interest", underlying_price, above=True)
    put_wall = _strike_with_max(put_contracts, "open_interest", underlying_price, above=False)
    max_pain = _calculate_max_pain(contracts)
    expected_move_abs, expected_move_pct = _nearest_expected_move(expirations)
    avg_iv = _average_iv(contracts, underlying_price)
    iv_skew = _iv_skew(contracts, underlying_price)
    term_structure = _term_structure(expirations)
    pin_risk_score = _pin_risk_score(contracts, underlying_price)
    gamma_profile = _gamma_exposure_profile(contracts, underlying_price)
    unusual_flow_candidates = _detect_unusual_flows(contracts, underlying_price)
    unusual_flows = unusual_flow_candidates[:12]
    unusual_flow_count = len(unusual_flow_candidates)
    unusual_premium_notional = sum(flow.premium_notional or 0 for flow in unusual_flow_candidates)
    unusual_flow_bias = _unusual_flow_bias(unusual_flow_candidates)
    key_strikes = _key_strikes(contracts, underlying_price)
    data_quality = _data_quality_score(contracts, provider, avg_iv, underlying_price)
    score = _direction_score(
        pcr_volume=pcr_volume,
        pcr_open_interest=pcr_open_interest,
        iv_skew=iv_skew,
        call_wall=call_wall,
        put_wall=put_wall,
        max_pain=max_pain,
        underlying_price=underlying_price,
        pin_risk_score=pin_risk_score,
        data_quality=data_quality,
        unusual_flow_bias=unusual_flow_bias,
    )
    direction = "偏多" if score >= 62 else "偏空" if score <= 38 else "中性"
    conviction = "高" if data_quality >= 72 and abs(score - 50) >= 18 else "中" if data_quality >= 45 else "低"
    signals = _build_signal_lines(
        direction,
        score,
        pcr_volume,
        pcr_open_interest,
        call_wall,
        put_wall,
        max_pain,
        expected_move_pct,
        avg_iv,
        iv_skew,
        pin_risk_score,
        gamma_profile,
        underlying_price,
        unusual_flows,
        unusual_flow_count,
    )
    risk_flags = _risk_flags(
        contracts,
        provider,
        data_quality,
        avg_iv,
        call_volume + put_volume,
        delay_note,
        unusual_flow_candidates,
        gamma_profile["status"],
    )
    source_status = "delayed" if provider != "none" else "unavailable"
    if provider == "nasdaq_public":
        source_status = "partial"

    summary = _summary_text(direction, score, conviction, signals, risk_flags, horizon_days)

    return OptionsSignal(
        symbol=symbol,
        provider=provider,
        provider_name=provider_name,
        source_status=source_status,
        underlying_price=underlying_price,
        fetched_at=utc_now_dt(),
        expiration_count=len(expirations),
        contract_count=len(contracts),
        data_quality=data_quality,
        direction=direction,
        score=score,
        conviction=conviction,
        summary=summary,
        call_volume=call_volume,
        put_volume=put_volume,
        call_open_interest=call_open_interest,
        put_open_interest=put_open_interest,
        put_call_volume_ratio=pcr_volume,
        put_call_open_interest_ratio=pcr_open_interest,
        avg_iv=avg_iv,
        iv_skew=iv_skew,
        term_structure=term_structure,
        max_pain=max_pain,
        call_wall=call_wall,
        put_wall=put_wall,
        expected_move_abs=expected_move_abs,
        expected_move_pct=expected_move_pct,
        pin_risk_score=pin_risk_score,
        gamma_exposure_status=gamma_profile["status"],
        net_gamma_exposure=gamma_profile["net_gamma_exposure"],
        call_gamma_exposure=gamma_profile["call_gamma_exposure"],
        put_gamma_exposure=gamma_profile["put_gamma_exposure"],
        gamma_wall=gamma_profile["gamma_wall"],
        negative_gamma_wall=gamma_profile["negative_gamma_wall"],
        zero_gamma_estimate=gamma_profile["zero_gamma_estimate"],
        gamma_strikes=gamma_profile["strikes"],
        unusual_flow_count=unusual_flow_count,
        unusual_premium_notional=unusual_premium_notional,
        unusual_flows=unusual_flows,
        key_strikes=key_strikes,
        expirations=expirations,
        signals=signals,
        risk_flags=risk_flags,
        delay_note=delay_note,
    )


def _empty_signal(symbol: str, warning: str) -> OptionsSignal:
    return OptionsSignal(
        symbol=symbol,
        provider="none",
        provider_name="No free option chain source",
        source_status="unavailable",
        fetched_at=utc_now_dt(),
        expiration_count=0,
        contract_count=0,
        data_quality=0,
        direction="不可判定",
        score=50,
        conviction="低",
        summary=warning,
        call_volume=0,
        put_volume=0,
        call_open_interest=0,
        put_open_interest=0,
        pin_risk_score=0,
        signals=[],
        risk_flags=[warning],
        delay_note="未返回可用期权链。",
    )


def _build_expiration_signals(
    contracts: list[OptionContract],
    underlying_price: Optional[float],
) -> list[OptionsExpirationSignal]:
    summaries: list[OptionsExpirationSignal] = []
    for expiration in sorted({contract.expiration for contract in contracts}):
        bucket = [contract for contract in contracts if contract.expiration == expiration]
        calls = [contract for contract in bucket if contract.side == "call"]
        puts = [contract for contract in bucket if contract.side == "put"]
        call_volume = _sum_field(calls, "volume")
        put_volume = _sum_field(puts, "volume")
        call_oi = _sum_field(calls, "open_interest")
        put_oi = _sum_field(puts, "open_interest")
        straddle = _atm_straddle_mid(bucket, underlying_price)
        expected_move_pct = _safe_ratio(straddle, underlying_price) if straddle is not None else None
        summaries.append(
            OptionsExpirationSignal(
                expiration=expiration,
                dte=_first_int(contract.dte for contract in bucket) or _days_to_expiration(expiration),
                contract_count=len(bucket),
                call_volume=call_volume,
                put_volume=put_volume,
                call_open_interest=call_oi,
                put_open_interest=put_oi,
                pcr_volume=_safe_ratio(put_volume, call_volume),
                pcr_open_interest=_safe_ratio(put_oi, call_oi),
                atm_straddle_mid=straddle,
                expected_move_pct=expected_move_pct,
                atm_iv=_atm_iv(bucket, underlying_price),
            )
        )
    return summaries


def _atm_straddle_mid(contracts: list[OptionContract], underlying_price: Optional[float]) -> Optional[float]:
    if not underlying_price:
        return None
    calls = [contract for contract in contracts if contract.side == "call" and contract.mid is not None]
    puts = [contract for contract in contracts if contract.side == "put" and contract.mid is not None]
    if not calls or not puts:
        return None
    call = min(calls, key=lambda contract: abs(contract.strike - underlying_price))
    put = min(puts, key=lambda contract: abs(contract.strike - underlying_price))
    if abs(call.strike - put.strike) > underlying_price * 0.05:
        return None
    return (call.mid or 0) + (put.mid or 0)


def _atm_iv(contracts: list[OptionContract], underlying_price: Optional[float]) -> Optional[float]:
    if not underlying_price:
        return None
    values = sorted(
        (
            (abs(contract.strike - underlying_price), contract.iv)
            for contract in contracts
            if contract.iv is not None and contract.iv > 0
        ),
        key=lambda item: item[0],
    )
    ivs = [value for _distance, value in values[:4]]
    return _mean(ivs)


def _nearest_expected_move(expirations: list[OptionsExpirationSignal]) -> tuple[Optional[float], Optional[float]]:
    for item in expirations:
        if item.atm_straddle_mid is not None and item.expected_move_pct is not None:
            return item.atm_straddle_mid, item.expected_move_pct
    return None, None


def _average_iv(contracts: list[OptionContract], underlying_price: Optional[float]) -> Optional[float]:
    values = [
        contract.iv for contract in contracts
        if contract.iv is not None
        and contract.iv > 0
        and (not underlying_price or 0.75 * underlying_price <= contract.strike <= 1.25 * underlying_price)
    ]
    return _mean(values)


def _iv_skew(contracts: list[OptionContract], underlying_price: Optional[float]) -> Optional[float]:
    if not underlying_price:
        return None
    put_ivs = [
        contract.iv for contract in contracts
        if contract.side == "put"
        and contract.iv is not None
        and 0.85 * underlying_price <= contract.strike <= 0.98 * underlying_price
    ]
    call_ivs = [
        contract.iv for contract in contracts
        if contract.side == "call"
        and contract.iv is not None
        and 1.02 * underlying_price <= contract.strike <= 1.15 * underlying_price
    ]
    put_mean = _mean(put_ivs)
    call_mean = _mean(call_ivs)
    if put_mean is None or call_mean is None:
        return None
    return put_mean - call_mean


def _term_structure(expirations: list[OptionsExpirationSignal]) -> str:
    iv_expirations = [item for item in expirations if item.atm_iv is not None]
    if len(iv_expirations) < 2:
        return "IV期限结构待补充"
    near = iv_expirations[0].atm_iv or 0
    far = iv_expirations[-1].atm_iv or 0
    spread = near - far
    if spread > 0.03:
        return "近月IV高于远月，事件/短线风险定价偏高"
    if spread < -0.03:
        return "远月IV高于近月，市场更关注中期波动"
    return "近远月IV相对平坦"


def _calculate_max_pain(contracts: list[OptionContract]) -> Optional[float]:
    strikes = sorted({contract.strike for contract in contracts})
    oi_contracts = [
        contract for contract in contracts
        if contract.open_interest is not None and contract.open_interest > 0
    ]
    if not strikes or not oi_contracts:
        return None
    best_strike: Optional[float] = None
    best_payout: Optional[float] = None
    for settlement in strikes:
        payout = 0.0
        for contract in oi_contracts:
            oi = contract.open_interest or 0
            if contract.side == "call":
                payout += oi * max(settlement - contract.strike, 0)
            else:
                payout += oi * max(contract.strike - settlement, 0)
        if best_payout is None or payout < best_payout:
            best_payout = payout
            best_strike = settlement
    return best_strike


def _strike_with_max(
    contracts: list[OptionContract],
    field: str,
    underlying_price: Optional[float],
    above: Optional[bool] = None,
) -> Optional[float]:
    candidates = [
        contract for contract in contracts
        if getattr(contract, field) is not None and getattr(contract, field) > 0
    ]
    if underlying_price and above is not None:
        directional = [
            contract for contract in candidates
            if (contract.strike >= underlying_price if above else contract.strike <= underlying_price)
        ]
        if directional:
            candidates = directional
    if not candidates:
        return None
    return max(candidates, key=lambda contract: getattr(contract, field) or 0).strike


def _pin_risk_score(contracts: list[OptionContract], underlying_price: Optional[float]) -> int:
    if not underlying_price:
        return 0
    total_oi = _sum_field(contracts, "open_interest")
    if total_oi <= 0:
        return 0
    near_oi = sum(
        contract.open_interest or 0
        for contract in contracts
        if abs(contract.strike - underlying_price) / underlying_price <= 0.05
    )
    return int(round(clamp((near_oi / total_oi) * 180, 0, 100)))


def _key_strikes(
    contracts: list[OptionContract],
    underlying_price: Optional[float],
) -> list[OptionsKeyStrike]:
    items: list[OptionsKeyStrike] = []
    configs = [
        ("call", "open_interest", "看涨OI墙", "上方阻力/突破确认位"),
        ("put", "open_interest", "看跌OI墙", "下方支撑/失守风险位"),
        ("call", "volume", "看涨成交活跃", "短线追涨/事件博弈"),
        ("put", "volume", "看跌成交活跃", "短线防守/对冲需求"),
    ]
    for side, field, metric, interpretation in configs:
        side_contracts = [contract for contract in contracts if contract.side == side]
        grouped: dict[float, float] = {}
        for contract in side_contracts:
            value = getattr(contract, field) or 0
            if value > 0:
                grouped[contract.strike] = grouped.get(contract.strike, 0) + value
        for strike, value in sorted(grouped.items(), key=lambda item: item[1], reverse=True)[:3]:
            distance_pct = None
            if underlying_price:
                distance_pct = (strike - underlying_price) / underlying_price
            items.append(
                OptionsKeyStrike(
                    side=side,
                    strike=strike,
                    metric=metric,
                    value=value,
                    distance_pct=distance_pct,
                    interpretation=interpretation,
                )
            )
    return sorted(items, key=lambda item: item.value, reverse=True)[:10]


def _gamma_exposure_profile(
    contracts: list[OptionContract],
    underlying_price: Optional[float],
) -> dict[str, Any]:
    empty = {
        "status": "unavailable",
        "net_gamma_exposure": 0.0,
        "call_gamma_exposure": 0.0,
        "put_gamma_exposure": 0.0,
        "gamma_wall": None,
        "negative_gamma_wall": None,
        "zero_gamma_estimate": None,
        "strikes": [],
    }
    if not underlying_price:
        return empty

    grouped: dict[float, dict[str, float]] = {}
    used_direct_gamma = False
    used_estimated_gamma = False
    for contract in contracts:
        gamma = contract.gamma
        gamma_is_estimated = contract.gamma_estimated
        if gamma is None or gamma <= 0:
            gamma = _estimate_contract_gamma(contract, underlying_price)
            gamma_is_estimated = True
        if gamma is None or gamma <= 0:
            continue
        open_interest = contract.open_interest or 0
        if open_interest <= 0:
            continue
        # Dollar gamma exposure for a 1% move. Calls are treated as positive and puts
        # as negative, a common dealer-position proxy when only open interest is known.
        exposure = gamma * open_interest * 100 * underlying_price * underlying_price * 0.01
        if contract.side == "put":
            exposure *= -1
        if gamma_is_estimated:
            used_estimated_gamma = True
        else:
            used_direct_gamma = True
        bucket = grouped.setdefault(contract.strike, {
            "call": 0.0,
            "put": 0.0,
            "oi": 0.0,
        })
        if contract.side == "call":
            bucket["call"] += exposure
        elif contract.side == "put":
            bucket["put"] += exposure
        bucket["oi"] += open_interest

    if not grouped:
        return empty

    strikes: list[OptionsGammaStrike] = []
    for strike, values in grouped.items():
        net = values["call"] + values["put"]
        strikes.append(
            OptionsGammaStrike(
                strike=strike,
                net_gamma_exposure=net,
                call_gamma_exposure=values["call"],
                put_gamma_exposure=values["put"],
                total_open_interest=values["oi"],
                distance_pct=(strike - underlying_price) / underlying_price,
            )
        )
    strikes = sorted(strikes, key=lambda item: item.strike)
    call_gex = sum(item.call_gamma_exposure for item in strikes)
    put_gex = sum(item.put_gamma_exposure for item in strikes)
    net_gex = call_gex + put_gex
    positive_strikes = [item for item in strikes if item.net_gamma_exposure > 0]
    negative_strikes = [item for item in strikes if item.net_gamma_exposure < 0]
    gamma_wall = (
        max(positive_strikes, key=lambda item: item.net_gamma_exposure).strike
        if positive_strikes else None
    )
    negative_gamma_wall = (
        min(negative_strikes, key=lambda item: item.net_gamma_exposure).strike
        if negative_strikes else None
    )
    zero_gamma_estimate = _estimate_zero_gamma(strikes)
    top_strikes = sorted(
        strikes,
        key=lambda item: abs(item.net_gamma_exposure),
        reverse=True,
    )[:12]

    return {
        "status": "available" if used_direct_gamma else "estimated" if used_estimated_gamma else "unavailable",
        "net_gamma_exposure": net_gex,
        "call_gamma_exposure": call_gex,
        "put_gamma_exposure": put_gex,
        "gamma_wall": gamma_wall,
        "negative_gamma_wall": negative_gamma_wall,
        "zero_gamma_estimate": zero_gamma_estimate,
        "strikes": top_strikes,
    }


def _estimate_contract_gamma(
    contract: OptionContract,
    underlying_price: Optional[float],
) -> Optional[float]:
    if not underlying_price or underlying_price <= 0 or contract.strike <= 0:
        return None
    dte = contract.dte if contract.dte is not None else _days_to_expiration(contract.expiration)
    if dte is None or dte <= 0:
        return None
    time_to_expiration = max(dte, 1) / 365
    volatility = contract.iv
    if volatility is None or volatility <= 0:
        mark_price = _first_number([contract.mid, contract.last, contract.bid, contract.ask])
        if mark_price is None or mark_price <= 0:
            return None
        volatility = _implied_volatility(
            side=contract.side,
            option_price=mark_price,
            underlying_price=underlying_price,
            strike=contract.strike,
            time_to_expiration=time_to_expiration,
            risk_free_rate=DEFAULT_RISK_FREE_RATE,
        )
    if volatility is None or volatility <= 0:
        return None
    return _black_scholes_gamma(
        underlying_price=underlying_price,
        strike=contract.strike,
        time_to_expiration=time_to_expiration,
        volatility=volatility,
        risk_free_rate=DEFAULT_RISK_FREE_RATE,
    )


def _implied_volatility(
    side: str,
    option_price: float,
    underlying_price: float,
    strike: float,
    time_to_expiration: float,
    risk_free_rate: float,
) -> Optional[float]:
    intrinsic = max(underlying_price - strike, 0) if side == "call" else max(strike - underlying_price, 0)
    discounted_strike = strike * math.exp(-risk_free_rate * time_to_expiration)
    upper_bound = underlying_price if side == "call" else discounted_strike
    if option_price < max(0.01, intrinsic * 0.98) or option_price > upper_bound * 1.2:
        return None

    low = 0.05
    high = 5.0
    low_price = _black_scholes_price(side, underlying_price, strike, time_to_expiration, low, risk_free_rate)
    high_price = _black_scholes_price(side, underlying_price, strike, time_to_expiration, high, risk_free_rate)
    if option_price <= low_price:
        return low
    if option_price >= high_price:
        return high

    for _ in range(60):
        mid = (low + high) / 2
        model_price = _black_scholes_price(side, underlying_price, strike, time_to_expiration, mid, risk_free_rate)
        if model_price < option_price:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def _black_scholes_price(
    side: str,
    underlying_price: float,
    strike: float,
    time_to_expiration: float,
    volatility: float,
    risk_free_rate: float,
) -> float:
    d1, d2 = _black_scholes_d1_d2(
        underlying_price,
        strike,
        time_to_expiration,
        volatility,
        risk_free_rate,
    )
    discounted_strike = strike * math.exp(-risk_free_rate * time_to_expiration)
    if side == "put":
        return discounted_strike * _normal_cdf(-d2) - underlying_price * _normal_cdf(-d1)
    return underlying_price * _normal_cdf(d1) - discounted_strike * _normal_cdf(d2)


def _black_scholes_gamma(
    underlying_price: float,
    strike: float,
    time_to_expiration: float,
    volatility: float,
    risk_free_rate: float,
) -> Optional[float]:
    if underlying_price <= 0 or strike <= 0 or time_to_expiration <= 0 or volatility <= 0:
        return None
    d1, _d2 = _black_scholes_d1_d2(
        underlying_price,
        strike,
        time_to_expiration,
        volatility,
        risk_free_rate,
    )
    denominator = underlying_price * volatility * math.sqrt(time_to_expiration)
    if denominator <= 0:
        return None
    return _normal_pdf(d1) / denominator


def _black_scholes_d1_d2(
    underlying_price: float,
    strike: float,
    time_to_expiration: float,
    volatility: float,
    risk_free_rate: float,
) -> tuple[float, float]:
    sigma_sqrt_t = volatility * math.sqrt(time_to_expiration)
    d1 = (
        math.log(underlying_price / strike)
        + (risk_free_rate + 0.5 * volatility * volatility) * time_to_expiration
    ) / sigma_sqrt_t
    return d1, d1 - sigma_sqrt_t


def _normal_pdf(value: float) -> float:
    return math.exp(-0.5 * value * value) / math.sqrt(2 * math.pi)


def _normal_cdf(value: float) -> float:
    return 0.5 * (1 + math.erf(value / math.sqrt(2)))


def _estimate_zero_gamma(strikes: list[OptionsGammaStrike]) -> Optional[float]:
    if len(strikes) < 2:
        return None
    ordered = sorted(strikes, key=lambda item: item.strike)
    cumulative = 0.0
    previous_strike: Optional[float] = None
    previous_cumulative: Optional[float] = None
    for item in ordered:
        cumulative += item.net_gamma_exposure
        if previous_cumulative is not None and previous_strike is not None:
            if previous_cumulative == 0:
                return previous_strike
            if cumulative == 0:
                return item.strike
            if (previous_cumulative < 0 < cumulative) or (previous_cumulative > 0 > cumulative):
                span = item.strike - previous_strike
                if span == 0:
                    return item.strike
                weight = abs(previous_cumulative) / (abs(previous_cumulative) + abs(cumulative))
                return previous_strike + span * weight
        previous_strike = item.strike
        previous_cumulative = cumulative
    return None


def _detect_unusual_flows(
    contracts: list[OptionContract],
    underlying_price: Optional[float],
) -> list[OptionsUnusualFlow]:
    flows: list[OptionsUnusualFlow] = []
    for contract in contracts:
        volume = contract.volume or 0
        if volume <= 0:
            continue

        mark_price = _first_number([contract.mid, contract.last, contract.bid, contract.ask])
        premium_notional = mark_price * volume * 100 if mark_price and mark_price > 0 else None
        open_interest = contract.open_interest if contract.open_interest is not None and contract.open_interest >= 0 else None
        volume_oi_ratio = _safe_ratio(volume, open_interest) if open_interest and open_interest > 0 else None
        distance_pct = (contract.strike - underlying_price) / underlying_price if underlying_price else None

        score = 0.0
        if volume >= 10_000:
            score += 35
        elif volume >= 5_000:
            score += 30
        elif volume >= 1_000:
            score += 22
        elif volume >= 500:
            score += 15
        elif volume >= 250:
            score += 8

        if premium_notional is not None:
            if premium_notional >= 5_000_000:
                score += 35
            elif premium_notional >= 1_000_000:
                score += 28
            elif premium_notional >= 250_000:
                score += 18
            elif premium_notional >= 100_000:
                score += 10

        if volume_oi_ratio is not None:
            if volume_oi_ratio >= 5:
                score += 25
            elif volume_oi_ratio >= 2:
                score += 18
            elif volume_oi_ratio >= 1:
                score += 10
        elif volume >= 500 and (open_interest is None or open_interest <= 0):
            score += 18

        if distance_pct is not None:
            abs_distance = abs(distance_pct)
            if abs_distance <= 0.15:
                score += 8
            elif abs_distance <= 0.30:
                score += 4

        dte = contract.dte if contract.dte is not None else _days_to_expiration(contract.expiration)
        if dte is not None and dte <= 14:
            score += 5

        if score < 45:
            continue
        if volume < 500 and (premium_notional is None or premium_notional < 100_000) and (volume_oi_ratio is None or volume_oi_ratio < 1.2):
            continue

        final_score = int(round(clamp(score, 0, 100)))
        severity = "高" if final_score >= 75 else "中" if final_score >= 58 else "低"
        reason_parts = [f"成交量 {volume:g}"]
        if open_interest is not None:
            reason_parts.append(f"OI {open_interest:g}")
        if volume_oi_ratio is not None:
            reason_parts.append(f"量/OI {volume_oi_ratio:.2f}x")
        elif open_interest is None or open_interest <= 0:
            reason_parts.append("OI 低/缺失")
        if premium_notional is not None:
            reason_parts.append(f"权利金约 ${premium_notional / 1_000_000:.2f}M")
        if dte is not None:
            reason_parts.append(f"{dte}DTE")

        side_text = "看涨" if contract.side == "call" else "看跌"
        open_hint = "，量/OI 偏高，疑似新开仓或滚动" if (volume_oi_ratio or 0) >= 1 else ""
        interpretation = (
            f"{side_text}合约异常放量{open_hint}；免费链只能识别成交/OI/权利金异常，"
            "主动买卖方向需用逐笔订单流复核。"
        )

        flows.append(
            OptionsUnusualFlow(
                option_symbol=contract.option_symbol,
                side=contract.side,  # type: ignore[arg-type]
                expiration=contract.expiration,
                dte=dte,
                strike=contract.strike,
                volume=volume,
                open_interest=open_interest,
                volume_open_interest_ratio=volume_oi_ratio,
                mark_price=mark_price,
                premium_notional=premium_notional,
                distance_pct=distance_pct,
                score=final_score,
                severity=severity,  # type: ignore[arg-type]
                reason="；".join(reason_parts),
                interpretation=interpretation,
                updated_at=contract.updated_at,
            )
        )

    return sorted(
        flows,
        key=lambda item: (
            item.score,
            item.premium_notional or 0,
            item.volume,
        ),
        reverse=True,
    )


def _unusual_flow_bias(flows: list[OptionsUnusualFlow]) -> float:
    if not flows:
        return 0.0
    call_weight = 0.0
    put_weight = 0.0
    for flow in flows:
        premium = flow.premium_notional or 0
        premium_weight = 1 + min(math.log10(max(premium, 1)) / 6, 1.2)
        weight = flow.score * premium_weight
        if flow.side == "call":
            call_weight += weight
        elif flow.side == "put":
            put_weight += weight
    total = call_weight + put_weight
    if total <= 0:
        return 0.0
    return clamp(((call_weight - put_weight) / total) * 8, -8, 8)


def _direction_score(
    *,
    pcr_volume: Optional[float],
    pcr_open_interest: Optional[float],
    iv_skew: Optional[float],
    call_wall: Optional[float],
    put_wall: Optional[float],
    max_pain: Optional[float],
    underlying_price: Optional[float],
    pin_risk_score: int,
    data_quality: int,
    unusual_flow_bias: float = 0.0,
) -> int:
    score = 50.0
    if pcr_volume is not None:
        if pcr_volume < 0.65:
            score += 12
        elif pcr_volume < 0.9:
            score += 6
        elif pcr_volume > 1.35:
            score -= 12
        elif pcr_volume > 1.05:
            score -= 5
    if pcr_open_interest is not None:
        if pcr_open_interest < 0.75:
            score += 7
        elif pcr_open_interest > 1.25:
            score -= 7
    if underlying_price:
        if call_wall and call_wall > underlying_price * 1.03:
            score += 5
        if put_wall and put_wall < underlying_price * 0.97:
            score += 3
        if max_pain and max_pain > underlying_price * 1.02:
            score += 4
        elif max_pain and max_pain < underlying_price * 0.98:
            score -= 4
    if iv_skew is not None:
        if iv_skew > 0.06:
            score -= 7
        elif iv_skew < -0.03:
            score += 5
    score += unusual_flow_bias
    if pin_risk_score >= 55:
        score = 50 + (score - 50) * 0.82
    if data_quality < 35:
        score = 50 + (score - 50) * 0.55
    return int(round(clamp(score, 0, 100)))


def _apply_tail_event_risk(
    signals: list[OptionsSignal],
    price_actions: dict[str, PriceAction],
) -> list[OptionsSignal]:
    put_pressure_symbols = {
        signal.symbol
        for signal in signals
        if _has_tail_put_pressure(signal)
    }
    peer_put_pressure_count = len(put_pressure_symbols)
    updated: list[OptionsSignal] = []
    for signal in signals:
        price_action = price_actions.get(signal.symbol)
        risk = _tail_event_risk(signal, price_action, peer_put_pressure_count)
        tail_summary = risk["summary"]
        signal_lines = [tail_summary, *signal.signals] if risk["level"] != "绿灯" else signal.signals
        risk_flags = signal.risk_flags
        if risk["level"] in {"橙灯", "红灯"}:
            risk_flags = dedupe([
                *signal.risk_flags,
                "左尾事件风险分使用期权链、价格异常和同池联动做预警；新闻/监管文本和实时逐笔订单流接入后置信度会更高。",
            ])
        risk_adjusted_signal = signal.model_copy(update={
            "tail_event_risk_score": risk["score"],
            "tail_event_risk_level": risk["level"],
            "tail_event_risk_summary": tail_summary,
            "tail_event_risk_reasons": risk["reasons"],
            "tail_event_risk_actions": risk["actions"],
            "signals": signal_lines[:10],
            "risk_flags": risk_flags[:7],
        })
        forecast = _predictive_forecast(risk_adjusted_signal, price_action, peer_put_pressure_count)
        updated.append(risk_adjusted_signal.model_copy(update={
            "forecast_score": forecast["score"],
            "forecast_label": forecast["label"],
            "forecast_confidence": forecast["confidence"],
            "forecast_summary": forecast["summary"],
            "forecast_reasons": forecast["reasons"],
            "forecast_actions": forecast["actions"],
            "forecast_invalidations": forecast["invalidations"],
        }))
    return updated


def _has_tail_put_pressure(signal: OptionsSignal) -> bool:
    return _defensive_put_pressure(signal)[0]


def _defensive_put_pressure(signal: OptionsSignal) -> tuple[bool, float]:
    near_put_flows = [
        flow for flow in signal.unusual_flows
        if flow.side == "put" and (flow.dte is None or flow.dte <= 21)
    ]
    high_near_put_flows = [flow for flow in near_put_flows if flow.score >= 58]
    put_premium = sum(flow.premium_notional or 0 for flow in near_put_flows)
    premium_share = _safe_ratio(put_premium, signal.unusual_premium_notional) or 0.0
    pcr_volume = signal.put_call_volume_ratio
    pcr_oi = signal.put_call_open_interest_ratio
    pressure = (
        (pcr_volume is not None and pcr_volume >= 1.35)
        or (pcr_oi is not None and pcr_oi >= 2.0)
        or (
            bool(high_near_put_flows)
            and premium_share >= 0.60
            and (pcr_volume is None or pcr_volume >= 0.90)
        )
    )
    return pressure, premium_share


def _tail_event_risk(
    signal: OptionsSignal,
    price_action: Optional[PriceAction],
    peer_put_pressure_count: int,
) -> dict[str, Any]:
    reasons: list[str] = []
    score = 0.0

    near_put_flows = [
        flow for flow in signal.unusual_flows
        if flow.side == "put" and (flow.dte is None or flow.dte <= 21)
    ]
    high_near_put_flows = [flow for flow in near_put_flows if flow.score >= 58]
    put_premium = sum(flow.premium_notional or 0 for flow in near_put_flows)
    top_put_flow = high_near_put_flows[0] if high_near_put_flows else near_put_flows[0] if near_put_flows else None
    defensive_put_pressure, put_premium_share = _defensive_put_pressure(signal)

    pcr_volume = signal.put_call_volume_ratio
    if pcr_volume is not None:
        if pcr_volume >= 2.0:
            score += 25
            reasons.append(f"看跌/看涨成交量比 {pcr_volume:.2f}，看跌交易明显占优。")
        elif pcr_volume >= 1.35:
            score += 15
            reasons.append(f"看跌/看涨成交量比 {pcr_volume:.2f}，看跌交易偏多。")

    pcr_oi = signal.put_call_open_interest_ratio
    if pcr_oi is not None:
        if pcr_oi >= 2.0:
            score += 15
            reasons.append(f"看跌/看涨未平仓量比 {pcr_oi:.2f}，存量仓位偏向看跌。")
        elif pcr_oi >= 1.25:
            score += 8
            reasons.append(f"看跌/看涨未平仓量比 {pcr_oi:.2f}，仓位略偏防守。")

    if defensive_put_pressure and high_near_put_flows:
        score += 24 if len(high_near_put_flows) >= 2 else 17
        reasons.append(f"近月 put 异常放量 {len(high_near_put_flows)} 条，疑似短期左尾保护或押注。")
    elif defensive_put_pressure and near_put_flows:
        score += 12
        reasons.append(f"近月 put 出现 {len(near_put_flows)} 条异常候选。")
    elif near_put_flows:
        score += 4
        reasons.append("有近月 put 异常候选，但整体 Put/Call 或权利金占比未明显偏防守，已降权。")

    if top_put_flow and top_put_flow.volume_open_interest_ratio is not None and top_put_flow.volume_open_interest_ratio >= 2:
        score += 10
        reasons.append(f"最高分 put 量/OI {top_put_flow.volume_open_interest_ratio:.2f}x，可能是新开仓或快速滚动。")

    if put_premium >= 5_000_000:
        score += 18
        reasons.append(f"近月异常 put 权利金约 ${put_premium / 1_000_000:.1f}M，资金量级较大。")
    elif put_premium >= 1_000_000:
        score += 10
        reasons.append(f"近月异常 put 权利金约 ${put_premium / 1_000_000:.1f}M。")

    latest_drop = False
    price_leak = False
    volume_spike = False
    event_realized = _tail_event_realized(price_action)
    if price_action:
        if price_action.latest_change_pct is not None and price_action.latest_change_pct <= -5:
            latest_drop = True
            score += 14
            reasons.append(f"最新交易日股价下跌 {price_action.latest_change_pct:.1f}%。")
        if price_action.latest_volume_vs_5d is not None and price_action.latest_volume_vs_5d >= 1.5:
            volume_spike = True
            score += 8
            reasons.append(f"最新成交量为前 5 日均量 {price_action.latest_volume_vs_5d:.1f}x。")
        if price_action.recent_large_drop_count > 0:
            price_leak = True
            score += 12 + min(price_action.recent_large_drop_count - 1, 2) * 4
            if price_action.max_recent_drop_pct is not None:
                reasons.append(
                    f"近 12 个交易日出现 {price_action.recent_large_drop_count} 次放量/高振幅大跌，最大单日跌幅 {price_action.max_recent_drop_pct:.1f}%。"
                )
            else:
                reasons.append(f"近 12 个交易日出现 {price_action.recent_large_drop_count} 次异常下跌。")
        elif latest_drop:
            price_leak = True
        if event_realized:
            reasons.append("最新交易日已出现极端下跌/放量，红灯更偏向事件后复盘与二次冲击预警。")
    else:
        reasons.append("未取到价格历史，单日跌幅和量比规则暂未参与评分。")

    peer_sync = peer_put_pressure_count >= 2 and _has_tail_put_pressure(signal)
    if peer_sync:
        score += 12
        reasons.append(f"本次扫描池内有 {peer_put_pressure_count} 个标的同时出现 put/防守仓位压力，存在同池联动。")

    put_anomaly = defensive_put_pressure
    own_alert_hint = bool(high_near_put_flows) or (pcr_volume is not None and pcr_volume >= 2.0)
    yellow_conditions = [
        latest_drop,
        volume_spike,
        put_anomaly,
        peer_sync,
    ]
    orange_conditions = [
        defensive_put_pressure and (len(high_near_put_flows) >= 2 or len(near_put_flows) >= 3),
        peer_sync,
        price_leak,
        defensive_put_pressure,
    ]
    red_conditions = [
        defensive_put_pressure and bool(near_put_flows) and any((flow.dte is None or flow.dte <= 14) for flow in near_put_flows),
        peer_sync,
        price_leak,
        own_alert_hint,
    ]

    if defensive_put_pressure and put_premium_share >= 0.60 and put_premium > 0:
        reasons.append(f"异常权利金中近月 put 占比约 {put_premium_share * 100:.0f}%。")

    if any(yellow_conditions):
        score = max(score, 30)
    if sum(bool(item) for item in orange_conditions) >= 2:
        score = max(score, 55)
    if all(red_conditions):
        score = max(score, 75)

    if signal.data_quality < 35:
        score *= 0.82
        reasons.append("期权链字段较稀疏，左尾风险分已降权。")
        if any(yellow_conditions):
            score = max(score, 30)
        if sum(bool(item) for item in orange_conditions) >= 2:
            score = max(score, 55)
        if all(red_conditions):
            score = max(score, 75)

    score_int = int(round(clamp(score, 0, 100)))
    if score_int >= 75:
        level = "红灯"
    elif score_int >= 55:
        level = "橙灯"
    elif score_int >= 30:
        level = "黄灯"
    else:
        level = "绿灯"

    actions = _tail_event_actions(level, event_realized)
    summary = _tail_event_summary(level, score_int, event_realized)
    if not reasons:
        reasons = ["未看到明显 put 异常、价格漏风或同池联动。"]

    return {
        "score": score_int,
        "level": level,
        "summary": summary,
        "reasons": dedupe(reasons)[:6],
        "actions": actions,
    }


def _tail_event_realized(price_action: Optional[PriceAction]) -> bool:
    if not price_action or price_action.latest_change_pct is None:
        return False
    if price_action.latest_change_pct <= -18:
        return True
    volume_ratio = price_action.latest_volume_vs_5d or 0
    return price_action.latest_change_pct <= -12 and volume_ratio >= 2


def _tail_event_summary(level: str, score: int, event_realized: bool = False) -> str:
    if level == "红灯":
        if event_realized:
            return f"左尾事件风险红灯，{score}/100：重大下跌已发生，当前是事件后风险与二次冲击预警。"
        return f"左尾事件风险红灯，{score}/100：市场可能正在提前押注重大坏消息。"
    if level == "橙灯":
        if event_realized:
            return f"左尾事件风险橙灯，{score}/100：事件后防守仓位仍高，需观察是否继续扩散。"
        return f"左尾事件风险橙灯，{score}/100：风险升温，需降低无保护隔夜暴露。"
    if level == "黄灯":
        if event_realized:
            return f"左尾事件风险黄灯，{score}/100：大跌后仍有异常苗头，先做事件复盘。"
        return f"左尾事件风险黄灯，{score}/100：出现异常苗头，先加入观察池。"
    return f"左尾事件风险绿灯，{score}/100：暂无明显重大坏消息押注。"


def _tail_event_actions(level: str, event_realized: bool = False) -> list[str]:
    if level == "红灯":
        if event_realized:
            return [
                "强提醒：利空/大跌已进入定价阶段，不把红灯理解成新的事前预测。",
                "先评估公告影响是否一次性兑现；等待成交量、股价和 put 仓位降温后再考虑抄底。",
                "复核公告、监管口径和同业反应，警惕二次处罚、评级下调或强平链条。",
            ]
        return [
            "强提醒：隔夜左尾事件风险高，不适合无保护持有。",
            "优先考虑减仓、买保护性 put/价差，或暂停抄底。",
            "用实时订单流、监管新闻和公司公告复核是否存在消息泄露。",
        ]
    if level == "橙灯":
        if event_realized:
            return [
                "按事件后交易处理，先确认坏消息是否被充分定价。",
                "跟踪 put 仓位、成交量和同业股价是否同步降温。",
                "若继续放量下跌或公告风险扩大，升级为红灯。",
            ]
        return [
            "降低裸多仓位，避免把普通回调当成无风险抄底。",
            "复核监管、诉讼、财报和公告日历等旧风险。",
            "若后续再出现近月 put 放量或同业同步，升级为红灯。",
        ]
    if level == "黄灯":
        return [
            "加入观察池，等待第二个信号确认。",
            "不建议追多；已有仓位可考虑轻量保护。",
        ]
    return ["暂无左尾事件预警，继续结合方向分、价格和基本面观察。"]


def _predictive_forecast(
    signal: OptionsSignal,
    price_action: Optional[PriceAction],
    peer_put_pressure_count: int,
) -> dict[str, Any]:
    if signal.provider == "none" or signal.contract_count <= 0:
        return {
            "score": 50,
            "label": "不可判定",
            "confidence": "低",
            "summary": "走势预判不可判定：当前缺少可用期权链。",
            "reasons": ["未取到有效期权链，不能构建走势预判。"],
            "actions": ["等待数据源恢复后再评估。"],
            "invalidations": ["补齐授权期权数据后重新计算。"],
        }

    score = 50.0
    confidence_points = 18.0 + signal.data_quality * 0.45
    reasons: list[str] = []
    actions: list[str] = []
    invalidations: list[str] = []

    direction_edge = (signal.score - 50) * 0.42
    score += direction_edge
    if abs(signal.score - 50) >= 10:
        reasons.append(f"期权方向分 {signal.score}/100，提供{'偏多' if signal.score > 50 else '偏空'}基础信号。")

    call_flow_premium = sum(flow.premium_notional or 0 for flow in signal.unusual_flows if flow.side == "call")
    put_flow_premium = sum(flow.premium_notional or 0 for flow in signal.unusual_flows if flow.side == "put")
    total_flow_premium = call_flow_premium + put_flow_premium
    if total_flow_premium > 0:
        flow_imbalance = (call_flow_premium - put_flow_premium) / total_flow_premium
        flow_points = clamp(flow_imbalance * 16, -16, 16)
        score += flow_points
        confidence_points += min(12, total_flow_premium / 1_000_000 * 2)
        if flow_imbalance >= 0.25:
            reasons.append(f"异常大单权利金偏向 Call，Call 占比约 {call_flow_premium / total_flow_premium * 100:.0f}%。")
        elif flow_imbalance <= -0.25:
            reasons.append(f"异常大单权利金偏向 Put，Put 占比约 {put_flow_premium / total_flow_premium * 100:.0f}%。")

    if signal.iv_skew is not None:
        if signal.iv_skew >= 0.08:
            score -= 7
            reasons.append(f"OTM Put IV 明显贵于 Call，偏斜 {signal.iv_skew * 100:.1f} 个百分点，左尾保险需求强。")
        elif signal.iv_skew <= -0.03:
            score += 5
            reasons.append(f"Call IV 相对更贵，偏斜 {signal.iv_skew * 100:.1f} 个百分点，追涨需求更强。")
        confidence_points += 5

    if "近月IV高于远月" in signal.term_structure:
        confidence_points -= 4
        reasons.append("近月 IV 高于远月，市场正在定价短线事件风险。")
    elif "远月IV高于近月" in signal.term_structure:
        score = 50 + (score - 50) * 0.88
        reasons.append("远月 IV 高于近月，短线方向信号需要降权。")

    if signal.gamma_exposure_status in {"available", "estimated"} and signal.underlying_price:
        confidence_points += 8 if signal.gamma_exposure_status == "available" else 4
        gex_ratio = signal.net_gamma_exposure / max(1.0, signal.underlying_price * signal.underlying_price * 10_000)
        if signal.net_gamma_exposure < 0:
            if score >= 54:
                score += 4
            elif score <= 46:
                score -= 4
            reasons.append("净 GEX 为负，行情更容易放大已有方向，追涨杀跌风险都更高。")
        elif signal.net_gamma_exposure > 0:
            score = 50 + (score - 50) * 0.86
            reasons.append("净 GEX 为正，价格更容易均值回归，单边预判降权。")
        if abs(gex_ratio) >= 0.8:
            confidence_points += 3
        if signal.gamma_exposure_status == "estimated":
            reasons.append("GEX 来自免费源价格反推，属于估算信号。")

    if signal.pin_risk_score >= 55:
        score = 50 + (score - 50) * 0.80
        reasons.append(f"Pin Risk {signal.pin_risk_score}/100，临近关键价位时单边走势容易被压制。")

    if signal.max_pain and signal.underlying_price:
        max_pain_gap = (signal.max_pain - signal.underlying_price) / signal.underlying_price
        if abs(max_pain_gap) >= 0.12:
            score += clamp(max_pain_gap * 20, -5, 5)
            reasons.append(f"Max Pain 与现价偏离 {max_pain_gap * 100:.1f}%，到期前存在回拉/牵引观察价值。")

    if price_action:
        confidence_points += 8
        if price_action.five_day_change_pct is not None:
            if price_action.five_day_change_pct >= 4:
                score += 6
                reasons.append(f"近 5 日股价上涨 {price_action.five_day_change_pct:.1f}%，价格确认偏强。")
            elif price_action.five_day_change_pct <= -4:
                score -= 6
                reasons.append(f"近 5 日股价下跌 {price_action.five_day_change_pct:.1f}%，价格确认偏弱。")
        if price_action.twenty_day_change_pct is not None:
            if price_action.twenty_day_change_pct >= 8:
                score += 4
                reasons.append(f"近 20 日趋势上涨 {price_action.twenty_day_change_pct:.1f}%，中短线趋势顺风。")
            elif price_action.twenty_day_change_pct <= -8:
                score -= 4
                reasons.append(f"近 20 日趋势下跌 {price_action.twenty_day_change_pct:.1f}%，中短线趋势逆风。")
        if price_action.close_vs_20d_avg_pct is not None and abs(price_action.close_vs_20d_avg_pct) >= 8:
            confidence_points -= 3
            reasons.append(f"现价偏离 20 日均价 {price_action.close_vs_20d_avg_pct:.1f}%，短线追单需防回撤。")
    else:
        confidence_points -= 8
        reasons.append("未取到价格确认信号，走势预判置信度降权。")

    event_realized = _tail_event_realized(price_action)
    if signal.tail_event_risk_level == "红灯":
        score = min(score, 35 if event_realized else 32)
        confidence_points += 10
        label = "高风险回避"
        if event_realized:
            reasons.append("左尾红灯且重大下跌已发生，当前重点是二次冲击和再定价风险。")
        else:
            reasons.append("左尾红灯未完全兑现，隔夜/事件风险优先级高于方向分。")
    elif signal.tail_event_risk_level == "橙灯":
        score -= 8
        confidence_points += 4
        label = _forecast_label(score)
        reasons.append("左尾橙灯，方向信号需要扣除事件风险折价。")
    else:
        label = _forecast_label(score)

    score_int = int(round(clamp(score, 0, 100)))
    if signal.tail_event_risk_level == "红灯":
        label = "高风险回避"
    else:
        label = _forecast_label(score_int)
    confidence = _forecast_confidence(confidence_points, score_int, signal.tail_event_risk_level)

    if label == "高风险回避":
        actions = [
            "不把方向分当作买入信号；先等事件、成交量和 put 仓位降温。",
            "已有仓位优先做保护或减仓，避免无保护隔夜暴露。",
            "等待左尾风险降到黄灯/绿灯后，再用方向分和价格确认重评。",
        ]
    elif label in {"强看涨", "看涨", "震荡偏强"}:
        actions = [
            "只把它作为概率优势，不追满仓；优先用分批或价差控制回撤。",
            "若价格与期权信号继续同向，才提高仓位信心。",
        ]
    elif label in {"看跌", "震荡偏弱"}:
        actions = [
            "避免无保护抄底；已有多头先降低仓位或买保护。",
            "若价格跌破关键 put wall/前低，同时 put 继续放量，风险会继续上升。",
        ]
    else:
        actions = [
            "当前更适合观察，不适合单靠期权信号下注。",
            "等待方向分、异常大单和价格趋势形成同向共振。",
        ]

    if signal.put_wall is not None:
        invalidations.append(f"跌破主要 Put OI 墙 {signal.put_wall:g} 后，多头预判失效或需降级。")
    if signal.call_wall is not None:
        invalidations.append(f"突破/受阻主要 Call OI 墙 {signal.call_wall:g}，用于确认上行动能或压力。")
    if signal.tail_event_risk_level in {"橙灯", "红灯"}:
        invalidations.append("左尾风险未降温前，不把任何偏多信号视为高胜率信号。")
    if not invalidations:
        invalidations.append("若方向分回落到 45-55 且异常流消失，预判自动降为观察。")

    if not reasons:
        reasons = ["期权链没有形成足够一致的方向、波动率或价格确认信号。"]

    return {
        "score": score_int,
        "label": label,
        "confidence": confidence,
        "summary": _forecast_summary(label, score_int, confidence),
        "reasons": dedupe(reasons)[:7],
        "actions": actions,
        "invalidations": dedupe(invalidations)[:4],
    }


def _forecast_label(score: float) -> str:
    if score >= 72:
        return "强看涨"
    if score >= 62:
        return "看涨"
    if score >= 55:
        return "震荡偏强"
    if score <= 34:
        return "看跌"
    if score <= 45:
        return "震荡偏弱"
    return "震荡"


def _forecast_confidence(confidence_points: float, score: int, tail_level: str) -> str:
    if tail_level == "红灯":
        return "高"
    distance = abs(score - 50)
    if confidence_points >= 68 and distance >= 14:
        return "高"
    if confidence_points >= 45 and distance >= 8:
        return "中"
    return "低"


def _forecast_summary(label: str, score: int, confidence: str) -> str:
    if label == "高风险回避":
        return f"走势预判：高风险回避，预判分 {score}/100，置信度{confidence}；先防重大波动和二次冲击。"
    if label in {"强看涨", "看涨", "震荡偏强"}:
        return f"走势预判：{label}，预判分 {score}/100，置信度{confidence}；适合作为偏多观察线索。"
    if label in {"看跌", "震荡偏弱"}:
        return f"走势预判：{label}，预判分 {score}/100，置信度{confidence}；优先控制下行风险。"
    return f"走势预判：震荡，预判分 {score}/100，置信度{confidence}；等待更明确的期权和价格共振。"


def _data_quality_score(
    contracts: list[OptionContract],
    provider: str,
    avg_iv: Optional[float],
    underlying_price: Optional[float],
) -> int:
    if not contracts:
        return 0
    score = 35
    if len({contract.expiration for contract in contracts}) >= 2:
        score += 12
    if sum(1 for contract in contracts if (contract.open_interest or 0) > 0) >= max(8, len(contracts) * 0.35):
        score += 18
    if sum(1 for contract in contracts if (contract.volume or 0) > 0) >= max(6, len(contracts) * 0.2):
        score += 10
    if avg_iv is not None:
        score += 12
    if any(contract.gamma is not None and contract.gamma > 0 for contract in contracts):
        score += 8
    if any(contract.bid is not None and contract.ask is not None for contract in contracts):
        score += 8
    if underlying_price:
        score += 5
    if provider == "nasdaq_public":
        score -= 8
    return int(round(clamp(score, 0, 100)))


def _risk_flags(
    contracts: list[OptionContract],
    provider: str,
    data_quality: int,
    avg_iv: Optional[float],
    total_volume: float,
    delay_note: str,
    unusual_flows: list[OptionsUnusualFlow],
    gamma_exposure_status: str,
) -> list[str]:
    flags = [delay_note]
    if provider == "nasdaq_public":
        if gamma_exposure_status == "estimated":
            flags.append("Nasdaq 免费快照不提供官方 IV/Greeks；当前 GEX 使用价格反推估算，真实波动率曲面需授权数据源。")
        else:
            flags.append("Nasdaq 免费快照缺少 IV/Greeks，不能计算真实波动率曲面和 Gamma Exposure。")
    if data_quality < 45:
        flags.append("期权链字段较稀疏，方向分数只适合做观察线索。")
    if avg_iv is None:
        flags.append("缺少有效 IV，预期波动和偏斜信号会降权。")
    if gamma_exposure_status == "unavailable":
        flags.append("缺少有效 Gamma，暂不能计算 Gamma Exposure；配置 MarketData.app 或 Tradier 后可启用。")
    elif gamma_exposure_status == "estimated":
        flags.append("Gamma Exposure 为免费期权价格反推的估算值，不等同 OPRA/ORATS Greeks；需用授权数据源复核。")
    if total_volume <= 0:
        flags.append("当前成交量为空或为 0，PCR 成交口径不可用。")
    if unusual_flows:
        flags.append("异常大单为期权链成交/OI/权利金异常候选，不等同实时逐笔扫单；需用 OPRA 或券商订单流复核主动方向。")
    spread_values = []
    for contract in contracts:
        if contract.bid is None or contract.ask is None or contract.mid is None or contract.mid <= 0:
            continue
        spread_values.append((contract.ask - contract.bid) / contract.mid)
    avg_spread = _mean(spread_values)
    if avg_spread is not None and avg_spread > 0.35:
        flags.append("平均买卖价差偏宽，短线交易滑点风险高。")
    return dedupe(flags)[:6]


def _build_signal_lines(
    direction: str,
    score: int,
    pcr_volume: Optional[float],
    pcr_open_interest: Optional[float],
    call_wall: Optional[float],
    put_wall: Optional[float],
    max_pain: Optional[float],
    expected_move_pct: Optional[float],
    avg_iv: Optional[float],
    iv_skew: Optional[float],
    pin_risk_score: int,
    gamma_profile: dict[str, Any],
    underlying_price: Optional[float],
    unusual_flows: list[OptionsUnusualFlow],
    unusual_flow_count: int,
) -> list[str]:
    lines = [f"期权投票为{direction}，方向分 {score}/100。"]
    if unusual_flows:
        top = unusual_flows[0]
        side_text = "Call" if top.side == "call" else "Put"
        premium_text = (
            f"，权利金约 ${top.premium_notional / 1_000_000:.2f}M"
            if top.premium_notional is not None
            else ""
        )
        ratio_text = (
            f"，量/OI {top.volume_open_interest_ratio:.2f}x"
            if top.volume_open_interest_ratio is not None
            else ""
        )
        lines.append(
            f"命中 {unusual_flow_count} 条异常大单候选；最高分为 {side_text} {top.strike:g} "
            f"{top.expiration}，成交 {top.volume:g}{premium_text}{ratio_text}。"
        )
    if pcr_volume is not None:
        lines.append(f"成交量 Put/Call Ratio 为 {pcr_volume:.2f}。")
    if pcr_open_interest is not None:
        lines.append(f"未平仓 Put/Call Ratio 为 {pcr_open_interest:.2f}。")
    if underlying_price and call_wall:
        lines.append(f"主要看涨 OI 墙在 {call_wall:g}，相对现价 {((call_wall - underlying_price) / underlying_price) * 100:.1f}%。")
    if underlying_price and put_wall:
        lines.append(f"主要看跌 OI 墙在 {put_wall:g}，相对现价 {((put_wall - underlying_price) / underlying_price) * 100:.1f}%。")
    if max_pain is not None:
        lines.append(f"Max Pain 估算位 {max_pain:g}。")
    if expected_move_pct is not None:
        lines.append(f"近月 ATM 跨式隐含波动区间约 +/-{expected_move_pct * 100:.1f}%。")
    if avg_iv is not None:
        lines.append(f"近价合约平均 IV 约 {avg_iv * 100:.1f}%。")
    if iv_skew is not None:
        lines.append(f"OTM Put-Call IV 偏斜 {iv_skew * 100:.1f} 个百分点。")
    if pin_risk_score >= 45:
        lines.append(f"近价 OI 集中度较高，Pin Risk {pin_risk_score}/100。")
    if gamma_profile.get("status") in {"available", "estimated"}:
        net_gex = gamma_profile.get("net_gamma_exposure") or 0
        gamma_wall = gamma_profile.get("gamma_wall")
        negative_gamma_wall = gamma_profile.get("negative_gamma_wall")
        prefix = "估算净" if gamma_profile.get("status") == "estimated" else "净"
        lines.append(f"{prefix} Gamma Exposure 约 ${net_gex / 1_000_000:.1f}M/1%。")
        if gamma_wall is not None:
            lines.append(f"正 Gamma Wall 估算位 {gamma_wall:g}。")
        if negative_gamma_wall is not None:
            lines.append(f"负 Gamma Wall 估算位 {negative_gamma_wall:g}。")
    return lines


def _summary_text(
    direction: str,
    score: int,
    conviction: str,
    signals: list[str],
    risk_flags: list[str],
    horizon_days: int,
) -> str:
    lead = signals[0] if signals else f"{horizon_days}日期权链暂未形成明确投票。"
    return f"{lead} 置信度{conviction}；{horizon_days}日窗口内更适合作为{direction}观察线索。"


def _source_profile() -> list[OptionsSourceStatus]:
    has_marketdata_token = _has_marketdata_token()
    has_tradier_token = _has_tradier_token()
    return [
        OptionsSourceStatus(
            provider="marketdata_app",
            name="MarketData.app Options Chain",
            status="ready" if has_marketdata_token else "blocked",
            cost="免费账户/免费层可用，完整覆盖建议配置 token",
            delay="免费层通常为延迟/日终；账户权限决定刷新频率",
            coverage="美股与 ETF 期权链、IV、Greeks、OI、成交量",
            notes="配置 MARKETDATA_APP_TOKEN 或 MARKETDATA_APP_API_KEY 后启用；未配置时自动跳过，避免无效 401 请求。",
        ),
        OptionsSourceStatus(
            provider="nasdaq_public",
            name="Nasdaq Public Option Chain",
            status="fallback",
            cost="无 key 免费公共网页源",
            delay="延迟快照",
            coverage="美股与 ETF 期权链、OI、部分成交量/价格",
            notes="适合作为无 token 兜底；通常缺 IV/Greeks。",
        ),
        OptionsSourceStatus(
            provider="tradier",
            name="Tradier Options Chain",
            status="ready" if has_tradier_token else "blocked",
            cost="开发者账户可申请 token",
            delay="取决于账户和行情权限",
            coverage="美股期权链、Greeks、报价、OI",
            notes="配置 TRADIER_ACCESS_TOKEN 或 TRADIER_TOKEN 后启用；请求 chains 时使用 greeks=true。",
        ),
        OptionsSourceStatus(
            provider="yahoo_public",
            name="Yahoo Finance Public Chain",
            status="fallback",
            cost="无 key 公共接口，无官方 SLA",
            delay="延迟快照",
            coverage="美股期权链、IV、OI、成交量",
            notes="地区和风控策略可能导致 403，仅作最后兜底。",
        ),
    ]


def _has_marketdata_token() -> bool:
    return bool(os.getenv("MARKETDATA_APP_TOKEN") or os.getenv("MARKETDATA_APP_API_KEY"))


def _marketdata_auth_headers() -> dict[str, str]:
    token = os.getenv("MARKETDATA_APP_TOKEN") or os.getenv("MARKETDATA_APP_API_KEY")
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def _has_tradier_token() -> bool:
    return bool(os.getenv("TRADIER_ACCESS_TOKEN") or os.getenv("TRADIER_TOKEN"))


def _tradier_auth_headers() -> dict[str, str]:
    token = os.getenv("TRADIER_ACCESS_TOKEN") or os.getenv("TRADIER_TOKEN")
    if not token:
        return {}
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }


def _select_expirations(
    expirations: Iterable[str],
    horizon_days: int,
    max_expirations: int,
) -> list[str]:
    today = date.today()
    parsed: list[tuple[date, str]] = []
    for item in expirations:
        exp_date = _parse_iso_date(item)
        if exp_date:
            parsed.append((exp_date, item))
    future = sorted((exp, raw) for exp, raw in parsed if exp >= today)
    within_horizon = [
        raw for exp, raw in future
        if (exp - today).days <= horizon_days
    ]
    if within_horizon:
        return within_horizon[:max_expirations]
    return [raw for _exp, raw in future[:max_expirations]]


def _array_value(payload: dict[str, Any], key: str, index: int) -> Any:
    value = payload.get(key)
    if isinstance(value, list):
        return value[index] if index < len(value) else None
    return value
def _to_int(value: Any) -> Optional[int]:
    number = to_float(value)
    if number is None:
        return None
    return int(round(number))


def _normalize_iv(value: Optional[float]) -> Optional[float]:
    if value is None or value <= 0:
        return None
    return value / 100 if value > 3 else value


def _mid_from_prices(
    bid: Optional[float],
    ask: Optional[float],
    last: Optional[float],
) -> Optional[float]:
    if bid is not None and ask is not None and bid >= 0 and ask >= 0 and ask >= bid:
        return (bid + ask) / 2
    if last is not None and last > 0:
        return last
    return None


def _safe_ratio(numerator: float, denominator: Optional[float]) -> Optional[float]:
    if denominator is None or denominator <= 0:
        return None
    return numerator / denominator


def _sum_field(contracts: list[OptionContract], field: str) -> float:
    return float(sum(getattr(contract, field) or 0 for contract in contracts))


def _mean(values: Iterable[Optional[float]]) -> Optional[float]:
    cleaned = [value for value in values if value is not None and math.isfinite(value)]
    if not cleaned:
        return None
    return sum(cleaned) / len(cleaned)


def _first_number(values: Iterable[Optional[float]]) -> Optional[float]:
    for value in values:
        if value is not None and math.isfinite(value) and value > 0:
            return value
    return None


def _first_int(values: Iterable[Optional[int]]) -> Optional[int]:
    for value in values:
        if value is not None:
            return value
    return None


def _parse_last_trade_price(value: Any) -> Optional[float]:
    if not value:
        return None
    match = re.search(r"\$([0-9,.]+)", str(value))
    if not match:
        return None
    return to_float(match.group(1))


def _parse_nasdaq_expiration(value: str) -> Optional[str]:
    try:
        return datetime.strptime(value.strip(), "%B %d, %Y").date().isoformat()
    except ValueError:
        return None


def _date_from_timestamp(value: Any) -> Optional[str]:
    number = to_float(value)
    if number is None:
        return None
    try:
        return datetime.fromtimestamp(number, tz=timezone.utc).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _timestamp_to_iso(value: Any) -> Optional[str]:
    number = to_float(value)
    if number is None:
        return None
    try:
        return datetime.fromtimestamp(number, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _timestamp_from_date(value: str) -> Optional[int]:
    parsed = _parse_iso_date(value)
    if not parsed:
        return None
    return int(datetime(parsed.year, parsed.month, parsed.day, tzinfo=timezone.utc).timestamp())


def _parse_iso_date(value: Any) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _days_to_expiration(value: str) -> Optional[int]:
    parsed = _parse_iso_date(value)
    if not parsed:
        return None
    return max((parsed - date.today()).days, 0)