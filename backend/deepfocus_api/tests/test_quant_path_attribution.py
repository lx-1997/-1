import copy
from fractions import Fraction
import pytest
from deepfocus_api.quant_path_attribution import decompose


def path():
    data = {'x': {'bars': [{'date': '2026-01-01', 'open': 100, 'close': 102}, {'date': '2026-01-02', 'open': 103, 'close': 104}]},
            'y': {'bars': [{'date': '2026-01-01', 'open': 200, 'close': 198}, {'date': '2026-01-02', 'open': 198, 'close': 200}]}}
    replay = {'curve': [{'date': '2026-01-01', 'nav': 10009}, {'date': '2026-01-02', 'nav': 10022}],
              'fills': [{'date': '2026-01-01', 'symbol': 'x', 'qty': 10, 'side': 'buy', 'price': 101, 'fee': 1},
                        {'date': '2026-01-02', 'symbol': 'x', 'qty': 4, 'side': 'sell', 'price': 102.5, 'fee': 1}]}
    return data, ['x', 'y'], replay, 10000


def test_active_path_reconciles_gaps_partial_sale_friction_and_reference():
    args = path(); before = copy.deepcopy(args); out = decompose(*args)
    totals = {k: Fraction(v) for k, v in out['exact_yuan'].items()}
    assert totals['net'] == 22
    assert totals['overnight_actual'] == 10
    assert totals['intraday_actual'] == 26
    assert totals['cost'] == 14
    assert totals['nav_rounding_residual'] == 0
    first = {k: Fraction(v) for k, v in out['daily'][0]['exact_yuan'].items()}
    assert first['same_exposure_reference'] == 5
    assert first['allocation_residual'] == 15
    assert totals['same_exposure_reference'] + totals['allocation_residual'] - totals['cost'] == 22
    assert args == before
    assert not out['causal_counterfactual'] and not out['executable_reference_strategy'] and not out['promotion_allowed']


def test_cash_only_path_has_no_direction_or_allocation_pnl():
    data, symbols, replay, capital = path(); replay['fills'] = []
    for row in replay['curve']: row['nav'] = capital
    assert all(Fraction(v) == 0 for v in decompose(data, symbols, replay, capital)['exact_yuan'].values())


def test_cent_rounding_is_explicit_not_assigned_to_stock_selection():
    data, symbols, replay, capital = path(); replay['curve'][-1]['nav'] += .01
    out = decompose(data, symbols, replay, capital)
    assert Fraction(out['exact_yuan']['nav_rounding_residual']) == Fraction(1, 100)


@pytest.mark.parametrize('damage', ['nav', 'short', 'outside', 'duplicate_date', 'zero_price', 'duplicate_symbol'])
def test_invalid_path_cannot_produce_attribution(damage):
    data, symbols, replay, capital = path()
    if damage == 'nav': replay['curve'][-1]['nav'] += 1
    elif damage == 'short': replay['fills'][0]['side'] = 'sell'
    elif damage == 'outside': replay['fills'][0]['symbol'] = 'z'
    elif damage == 'duplicate_date': replay['curve'][1]['date'] = replay['curve'][0]['date']
    elif damage == 'zero_price': data['x']['bars'][0]['open'] = 0
    else: symbols.append('x')
    with pytest.raises(ValueError): decompose(data, symbols, replay, capital)
