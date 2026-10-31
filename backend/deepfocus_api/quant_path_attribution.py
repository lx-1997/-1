"""Exact fixed-path price P&L decomposition; no strategy or causal replay."""
from collections import defaultdict
from fractions import Fraction


def decompose(dataset, symbols, replay, capital):
    symbols = list(symbols)
    if not symbols or len(set(symbols)) != len(symbols):
        raise ValueError('Unique nonempty basket required')
    number = lambda x: Fraction(str(x))
    initial = number(capital)
    if initial <= 0:
        raise ValueError('Positive initial capital required')
    bars = {s: {b['date']: b for b in dataset[s]['bars']} for s in symbols}
    by_day = defaultdict(list)
    dates = [r['date'] for r in replay['curve']]
    if not dates or dates != sorted(set(dates)):
        raise ValueError('Ordered unique curve dates required')
    for f in replay['fills']:
        if f['date'] not in dates or f['symbol'] not in symbols or f['side'] not in ('buy', 'sell') or type(f['qty']) is not int or f['qty'] <= 0:
            raise ValueError('Fill outside registered path')
        by_day[f['date']].append(f)
    qty = dict.fromkeys(symbols, 0)
    previous = None
    prior_nav = initial
    totals = defaultdict(Fraction)
    daily = []
    for row in replay['curve']:
        today = {s: bars[s][row['date']] for s in symbols}
        op = {s: number(today[s]['open']) for s in symbols}
        cl = {s: number(today[s]['close']) for s in symbols}
        if any(x <= 0 for x in [*op.values(), *cl.values()]):
            raise ValueError('Positive prices required')
        overnight = sum((qty[s] * (op[s] - previous[s]) for s in symbols), Fraction()) if previous else Fraction()
        gross_prior = sum((qty[s] * previous[s] for s in symbols), Fraction()) if previous else Fraction()
        ref_night = gross_prior * sum((op[s] / previous[s] - 1 for s in symbols), Fraction()) / len(symbols) if previous else Fraction()
        cost = Fraction()
        for f in by_day[row['date']]:
            sign = 1 if f['side'] == 'buy' else -1
            cost += number(f['fee']) + sign * f['qty'] * (number(f['price']) - op[f['symbol']])
            qty[f['symbol']] += sign * f['qty']
            if qty[f['symbol']] < 0:
                raise ValueError('Negative fixed-path holding')
        intraday = sum((qty[s] * (cl[s] - op[s]) for s in symbols), Fraction())
        gross_open = sum((qty[s] * op[s] for s in symbols), Fraction())
        ref_day = gross_open * sum((cl[s] / op[s] - 1 for s in symbols), Fraction()) / len(symbols)
        delta = number(row['nav']) - prior_nav
        rounding = delta - overnight - intraday + cost
        if abs(rounding) > Fraction(21, 1000):
            raise ValueError('Fixed-path NAV does not reconcile')
        values = dict(net=delta, overnight_actual=overnight, intraday_actual=intraday,
                      same_exposure_reference=ref_night + ref_day,
                      allocation_residual=overnight + intraday - ref_night - ref_day,
                      cost=cost, nav_rounding_residual=rounding)
        assert values['net'] == values['same_exposure_reference'] + values['allocation_residual'] - cost + rounding
        daily.append({'date': row['date'], 'exact_yuan': {k: str(v) for k, v in values.items()}})
        for k, v in values.items(): totals[k] += v
        previous, prior_nav = cl, number(row['nav'])
    return {'schema': 'fixed-path-direction-allocation-attribution-v1',
            'exact_yuan': {k: str(v) for k, v in totals.items()},
            'pct_initial': {k: float(v / initial * 100) for k, v in totals.items()},
            'daily': daily, 'causal_counterfactual': False, 'executable_reference_strategy': False,
            'price_only_not_total_return': True, 'promotion_allowed': False}
