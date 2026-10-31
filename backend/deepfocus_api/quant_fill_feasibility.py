"""Ex-post necessary-condition audit, never an execution or capacity certificate.

Today's range/volume can diagnose a completed model path, never size its orders.
Adverse modeled fill prices may include friction rather than observed prints.
"""
from collections import Counter, defaultdict
from decimal import Decimal
import math

from . import quant_research_engine as engine


RULES = {
    'scope': 'all_original_oos_fills_seen_development_only',
    'path': 'exact_original_frozen_parameters_no_refit_no_counterfactual_fills',
    'prior_volume_cap': 'floor_previous_session_shares_times_original_participation',
    'daily_aggregation': 'all_sides_sum_by_symbol_and_date',
    'range': 'inclusive_daily_low_high_zero_tolerance',
    'today_range_volume': 'ex_post_diagnostic_only_never_order_inputs',
    'outside_range': 'modeled_fill_not_supported_as_observed_print_not_alpha_loss_estimate',
    'capacity': 'daily_volume_upper_bound_not_open_auction_or_order_book_capacity',
    'selection': 'none_all_results_retained_original_gates_unchanged',
}


def number(value, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Finite numeric value required')
    result = Decimal(str(value))
    if result < 0 or (positive and result == 0):
        raise ValueError('Invalid numeric sign')
    return result


def audit(dataset, fills):
    engine.validate_dataset(dataset)
    if not isinstance(fills, list) or len(fills) > 100000:
        raise ValueError('Bounded fill list required')
    bars = {s: {b['date']: (i, b) for i, b in enumerate(p['bars'])} for s, p in dataset.items()}
    totals = defaultdict(int)
    seen = set()
    for fill in fills:
        if (not isinstance(fill, dict) or fill.get('side') not in ('buy', 'sell')
                or type(fill.get('qty')) is not int or fill['qty'] <= 0):
            raise ValueError('Valid positive integer fill required')
        key = (fill.get('date'), fill.get('symbol'))
        if not all(isinstance(x, str) for x in key) or key[1] not in bars or key[0] not in bars[key[1]]:
            raise ValueError('Fill missing its raw daily bar')
        identity = engine.digest(fill)
        if identity in seen:
            raise ValueError('Duplicate fill cannot certify a model path')
        seen.add(identity)
        number(fill.get('price'), positive=True)
        totals[key] += fill['qty']
    rows = []
    for index, fill in enumerate(fills):
        day, symbol = fill['date'], fill['symbol']
        pos, bar = bars[symbol][day]
        if pos == 0 or fill.get('signal_date') != dataset[symbol]['bars'][pos-1]['date']:
            raise ValueError('Fill must reference its preceding completed session')
        prior = dataset[symbol]['bars'][pos-1]
        price, low, high = number(fill['price'], positive=True), number(bar['low'], positive=True), number(bar['high'], positive=True)
        volume, previous_volume = number(bar['volume']), number(prior['volume'])
        cap = int(previous_volume * Decimal(str(engine.ASSUMPTIONS['volume_participation'])))
        flags = []
        if not low <= price <= high: flags.append('modeled_price_outside_daily_range')
        if fill['qty'] > cap: flags.append('fill_exceeds_declared_prior_volume_cap')
        if totals[(day, symbol)] > cap: flags.append('aggregate_exceeds_declared_prior_volume_cap')
        if volume == 0: flags.append('fill_on_zero_daily_volume')
        elif totals[(day, symbol)] > volume: flags.append('aggregate_exceeds_entire_daily_volume')
        rows.append({'fill_index': index, 'date': day, 'symbol': symbol, 'side': fill['side'],
                     'qty': fill['qty'], 'price': str(price), 'daily_low': str(low), 'daily_high': str(high),
                     'previous_session': prior['date'], 'previous_volume_cap_shares': cap,
                     'aggregate_daily_model_qty': totals[(day, symbol)],
                     'aggregate_daily_participation': str(Decimal(totals[(day, symbol)])/volume) if volume else None,
                     'flags': flags})
    counts = Counter(flag for row in rows for flag in row['flags'])
    ratios = [Decimal(row['aggregate_daily_participation']) for row in rows if row['aggregate_daily_participation'] is not None]
    return {'fills': len(rows), 'symbol_days': len(totals), 'flagged_fills': sum(bool(r['flags']) for r in rows),
            'flag_counts': dict(sorted(counts.items())), 'necessary_conditions_all_met': bool(rows) and not counts,
            'max_daily_model_participation': str(max(ratios)) if ratios else None,
            'execution_verified': False, 'open_auction_capacity_verified': False,
            'halt_and_limit_regime_verified': False, 'promotion_allowed': False, 'rows': rows}
