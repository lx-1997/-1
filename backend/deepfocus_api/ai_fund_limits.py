"""Dated STANDARD percentage limits, not a historical security-status feed.

IPO/relisting/no-limit sessions, intraday halts, tick-rounded bands and queue
fillability still need explicit data. This function cannot certify any of them.
"""
from datetime import date, datetime, timedelta, timezone
import re

BJ = timezone(timedelta(hours=8))
VERSION = 'standard-board-limits-20260706-v1'
MAIN_ST_CHANGE = date(2026, 7, 6)


def trading_date(value):
    if isinstance(value, datetime):
        if value.tzinfo is None: raise ValueError('Limit-rule execution clock needs a timezone')
        return value.astimezone(BJ).date()
    if type(value) is date: return value
    if isinstance(value, str):
        if len(value)==10: return date.fromisoformat(value)
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return trading_date(stamp)
    raise ValueError('Explicit execution date required for price-limit rules')


def standard_pct(code, name, *, at):
    day = trading_date(at)
    if day < date(2023, 4, 10):
        raise ValueError('Standard limit model does not cover pre-registration historical rules')
    if not isinstance(code, str) or not re.fullmatch(r'\d{6}', code):
        raise ValueError('Six-digit A-share code required')
    # Board regimes take priority over risk-warning name heuristics.
    if code.startswith(('688', '689', '300', '301')): return 20.0
    if code.startswith(('8', '4', '920')): return 30.0
    if not code.startswith(('600', '601', '603', '605', '000', '001', '002', '003')):
        raise ValueError('Unknown standard stock-board regime')
    if 'ST' in (name or '').upper() and day < MAIN_ST_CHANGE: return 5.0
    return 10.0


def fill_allowed(latest, change_pct, effective_price, side, *, limit_pct, buffer_pct):
    """Recheck adverse fill against the SAME conservative quote-change proxy.

    A reported change ratio is not an exchange-published tick-rounded band.
    This prevents impact from escaping the existing guard; it certifies no band.
    """
    from decimal import Decimal, InvalidOperation
    try:
        price, change, fill, limit, buffer = map(lambda v: Decimal(str(v)),
            (latest, change_pct, effective_price, limit_pct, buffer_pct))
    except (InvalidOperation, ValueError): return False
    if (side not in ('buy', 'sell') or not all(v.is_finite() for v in (price, change, fill, limit, buffer))
            or price <= 0 or fill <= 0 or change <= -100 or not 0 <= buffer < limit): return False
    projected = (fill/price*(1+change/100)-1)*100
    return projected < limit-buffer if side=='buy' else projected > -(limit-buffer)
