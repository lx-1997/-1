"""Deterministic execution guards for six news portfolios; no selection/alpha tuning."""
import math
from . import ai_fund_policy as policy


def accepted_quote(conn, symbol, quote, now=None):
    """Do not execute or lift a trailing high using older/conflicting marks.

    Vendors without sequence numbers cannot resolve two prices at the same
    exchange timestamp. Keep the first trusted mark and reject the ambiguity.
    """
    if not policy.executable(quote, now):
        return False
    previous = conn.execute('SELECT price,ts FROM aif_marks WHERE symbol=?', (symbol,)).fetchone()
    if previous is None:
        return True
    stamp = policy.quote_time(quote)
    retained = policy.quote_time({'time': previous['ts']})
    if retained is None or policy.number(previous['price']) is None or float(previous['price']) <= 0:
        return False
    if stamp < retained:
        return False
    return stamp != retained or float(quote['latest']) == float(previous['price'])


def risk_status(conn, fund_id, nav, initial):
    previous = conn.execute('SELECT peak,worst FROM aif_risk_state WHERE fund_id=?', (fund_id,)).fetchone()
    values = [policy.number(nav), policy.number(initial)]
    if previous:
        values += [policy.number(previous['peak']), policy.number(previous['worst'])]
    if any(v is None for v in values) or values[1] <= 0 or (previous and values[2] <= 0):
        raise ValueError('Invalid persistent portfolio risk valuation')
    peak = max(initial, nav, float(previous['peak']) if previous else initial)
    worst = min(float(previous['worst']) if previous else 0., (nav/peak-1)*100)
    boundary = -100 * policy.MAX_DRAWDOWN
    # Legacy REAL NAV arithmetic may encode an exact 8% as 7.999999999999996%.
    # This tolerance only closes that machine-precision gap, never forgives a breach.
    paused = worst <= boundary or math.isclose(worst,boundary,rel_tol=0.,abs_tol=1e-10)
    return {'paused':paused,'peak':peak,'worst_drawdown_pct':worst,
            'reason':'historical_drawdown_limit' if paused else None,
            'reset_policy':'no_automatic_resume'}


def fill_id(trace):
    import uuid
    number=trace.get('fills_issued',0)+1
    trace['fills_issued']=number
    return uuid.uuid5(uuid.NAMESPACE_URL,f"paper-fill:{trace['id']}:{number}").hex


def fill_price(price,impact_bps,side):
    from decimal import Decimal,ROUND_CEILING,ROUND_FLOOR
    raw=Decimal(str(price))*(1+(1 if side=='buy' else -1)*Decimal(str(impact_bps))/10000)
    rounded=raw.quantize(Decimal('.01'),rounding=ROUND_CEILING if side=='buy' else ROUND_FLOOR)
    return float(rounded) if rounded>0 else None


def cash_change(cash,amount,fee,side):
    from decimal import Decimal
    # Preserve any legacy sub-cent remainder; never reset an existing account.
    change=Decimal(str(amount))*(1 if side=='sell' else -1)-Decimal(str(fee))
    return float(Decimal(str(cash))+change)


def cost_basis(conn,fund_id,position):
    from decimal import Decimal
    row=conn.execute('SELECT amount FROM aif_position_basis WHERE fund_id=? AND symbol=?',(fund_id,position['symbol'])).fetchone()
    return (Decimal(row['amount']),True) if row else (Decimal(str(position['avg_cost']))*Decimal(str(position['qty'])),False)
