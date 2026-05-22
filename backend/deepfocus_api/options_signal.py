from __future__ import annotations

import asyncio
import math
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable, Optional

import httpx

from .schemas import (
    OptionsExpirationSignal,
    OptionsKeyStrike,
    OptionsSignal,
    OptionsSignalResponse,
    OptionsSourceStatus,
    OptionsUnusualFlow,
)


MAX_SYMBOLS = 12
REQUEST_TIMEOUT = httpx.Timeout(10.0, connect=4.0)


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
    underlying_price: Optional[float] = None
    updated_at: Optional[str] = None


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
    generated_at = _utc_now()
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

    signals: list[OptionsSignal] = []
    for signal, signal_warnings in analyses:
        signals.append(signal)
        warnings.extend(signal_warnings)

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
        warnings=_dedupe_text(warnings),
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
        return [], provider_name, delay_note, [f"MarketData.app expirations failed for {symbol}: {_safe_error(exc)}."]

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
            warnings.append(f"MarketData.app chain failed for {symbol} {expiration}: {_safe_error(exc)}.")
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
        return [], provider_name, delay_note, [f"Nasdaq public option chain failed for {symbol}: {_safe_error(exc)}."]

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
        return [], provider_name, delay_note, [f"Yahoo options chain failed for {symbol}: {_safe_error(exc)}."]

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
            warnings.append(f"Yahoo options chain failed for {symbol} {expiration}: {_safe_error(exc)}.")
            continue
        result = ((payload.get("optionChain") or {}).get("result") or [])
        if result:
            contracts.extend(_contracts_from_yahoo_result(symbol, result[0]))
    return contracts, provider_name, delay_note, warnings


def _contracts_from_marketdata(symbol: str, selected_expiration: str, payload: dict[str, Any]) -> list[OptionContract]:
    option_symbols = payload.get("optionSymbol") or []
    contracts: list[OptionContract] = []
    for index in range(len(option_symbols)):
        side = str(_array_value(payload, "side", index) or "").lower()
        if side not in {"call", "put"}:
            continue
        strike = _to_float(_array_value(payload, "strike", index))
        if strike is None:
            continue
        expiration = _date_from_timestamp(_array_value(payload, "expiration", index)) or selected_expiration
        mid = _to_float(_array_value(payload, "mid", index))
        bid = _to_float(_array_value(payload, "bid", index))
        ask = _to_float(_array_value(payload, "ask", index))
        last = _to_float(_array_value(payload, "last", index))
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
                volume=_to_float(_array_value(payload, "volume", index)),
                open_interest=_to_float(_array_value(payload, "openInterest", index)),
                iv=_normalize_iv(_to_float(_array_value(payload, "iv", index))),
                delta=_to_float(_array_value(payload, "delta", index)),
                gamma=_to_float(_array_value(payload, "gamma", index)),
                underlying_price=_to_float(_array_value(payload, "underlyingPrice", index)),
                updated_at=_timestamp_to_iso(_array_value(payload, "updated", index)),
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
        strike = _to_float(row.get("strike"))
        if strike is None:
            continue
        for side, prefix in (("call", "c"), ("put", "p")):
            last = _to_float(row.get(f"{prefix}_Last"))
            bid = _to_float(row.get(f"{prefix}_Bid"))
            ask = _to_float(row.get(f"{prefix}_Ask"))
            volume = _to_float(row.get(f"{prefix}_Volume"))
            open_interest = _to_float(row.get(f"{prefix}_Openinterest"))
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
    underlying_price = _to_float(quote.get("regularMarketPrice"))
    options = result.get("options") or []
    if not options:
        return []
    payload = options[0]
    expiration = _date_from_timestamp(payload.get("expirationDate")) or ""
    contracts: list[OptionContract] = []
    for side, key in (("call", "calls"), ("put", "puts")):
        for row in payload.get(key, []) or []:
            strike = _to_float(row.get("strike"))
            if strike is None or not expiration:
                continue
            bid = _to_float(row.get("bid"))
            ask = _to_float(row.get("ask"))
            last = _to_float(row.get("lastPrice"))
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
                    volume=_to_float(row.get("volume")),
                    open_interest=_to_float(row.get("openInterest")),
                    iv=_normalize_iv(_to_float(row.get("impliedVolatility"))),
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
        underlying_price,
        unusual_flows,
        unusual_flow_count,
    )
    risk_flags = _risk_flags(contracts, provider, data_quality, avg_iv, call_volume + put_volume, delay_note, unusual_flow_candidates)
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
        fetched_at=_utc_now(),
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
        fetched_at=_utc_now(),
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
    return int(round(_clamp((near_oi / total_oi) * 180, 0, 100)))


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

        final_score = int(round(_clamp(score, 0, 100)))
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
    return _clamp(((call_weight - put_weight) / total) * 8, -8, 8)


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
    return int(round(_clamp(score, 0, 100)))


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
    if any(contract.bid is not None and contract.ask is not None for contract in contracts):
        score += 8
    if underlying_price:
        score += 5
    if provider == "nasdaq_public":
        score -= 8
    return int(round(_clamp(score, 0, 100)))


def _risk_flags(
    contracts: list[OptionContract],
    provider: str,
    data_quality: int,
    avg_iv: Optional[float],
    total_volume: float,
    delay_note: str,
    unusual_flows: list[OptionsUnusualFlow],
) -> list[str]:
    flags = [delay_note]
    if provider == "nasdaq_public":
        flags.append("Nasdaq 免费快照缺少 IV/Greeks，不能计算真实波动率曲面和 Gamma Exposure。")
    if data_quality < 45:
        flags.append("期权链字段较稀疏，方向分数只适合做观察线索。")
    if avg_iv is None:
        flags.append("缺少有效 IV，预期波动和偏斜信号会降权。")
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
    return _dedupe_text(flags)[:6]


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
    risk = risk_flags[1] if len(risk_flags) > 1 else risk_flags[0] if risk_flags else "请结合价格、成交量和基本面确认。"
    return f"{lead} 置信度{conviction}；{horizon_days}日窗口内更适合作为{direction}观察线索。{risk}"


def _source_profile() -> list[OptionsSourceStatus]:
    has_marketdata_token = _has_marketdata_token()
    has_tradier_token = bool(os.getenv("TRADIER_ACCESS_TOKEN") or os.getenv("TRADIER_TOKEN"))
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
            notes="接口位已预留，后续可接入 TRADIER_ACCESS_TOKEN 做更完整链路。",
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


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if math.isnan(value) or math.isinf(value):
            return None
        return float(value)
    text = str(value).strip()
    if not text or text in {"--", "N/A", "nan", "None", "null"}:
        return None
    text = text.replace("$", "").replace(",", "").replace("%", "")
    try:
        parsed = float(text)
    except ValueError:
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def _to_int(value: Any) -> Optional[int]:
    number = _to_float(value)
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
    return _to_float(match.group(1))


def _parse_nasdaq_expiration(value: str) -> Optional[str]:
    try:
        return datetime.strptime(value.strip(), "%B %d, %Y").date().isoformat()
    except ValueError:
        return None


def _date_from_timestamp(value: Any) -> Optional[str]:
    number = _to_float(value)
    if number is None:
        return None
    try:
        return datetime.fromtimestamp(number, tz=timezone.utc).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _timestamp_to_iso(value: Any) -> Optional[str]:
    number = _to_float(value)
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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _dedupe_text(items: Iterable[str]) -> list[str]:
    deduped: list[str] = []
    for item in items:
        cleaned = item.strip()
        if cleaned and cleaned not in deduped:
            deduped.append(cleaned)
    return deduped


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        return f"HTTP {response.status_code}"
    message = str(exc).strip() or exc.__class__.__name__
    return message[:240]
