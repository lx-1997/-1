"""Explicit reference-price and friction ledger for a fixed legacy model path.

This is a reconciliation, not a fill simulator, tax book or executable account.
Reference open is a provider daily field, not a certified auction fill.
"""
from collections import defaultdict
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN
import math

from . import quant_research_engine as engine
from .ai_fund_ledger import fee

RULES = {
    'path': 'all_original_oos_fills_no_refit_or_new_orders',
    'reference_price': 'retained_provider_daily_open_not_verified_execution',
    'cashflow_price': 'original_adverse_10bps_and_cent_tick_model_price',
    'decomposition': 'reference_notional_minus_adverse_slippage_minus_tick_cost_minus_original_fee',
    'fee_basis': 'original_model_cashflow_notional_not_reference_notional',
    'position_basis': 'legacy_weighted_average_model_cost_not_FIFO_or_tax_basis',
    'money_rounding': 'cent_half_even_no_reconciliation_tolerance',
    'comparison': 'every_daily_cash_and_nav_every_realized_pnl_final_quantities',
    'price_clipping': 'never', 'gate_changes': 'none', 'selection': 'none',
}
CENT = Decimal('.01')


class ReconciliationError(ValueError):
    def __init__(self, message, details):
        super().__init__(message)
        self.details = details


def decimal(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Finite numeric model value required')
    return Decimal(str(value))


def money(value):
    return value.quantize(CENT, rounding=ROUND_HALF_EVEN)


def decompose(fill, reference_open):
    if not isinstance(fill, dict) or fill.get('side') not in ('buy', 'sell') or type(fill.get('qty')) is not int or fill['qty'] <= 0:
        raise ValueError('Positive integer buy/sell fill required')
    reference, price, charge = decimal(reference_open), decimal(fill.get('price')), decimal(fill.get('fee'))
    if reference <= 0 or price <= 0 or charge < 0:
        raise ValueError('Invalid reference/price/fee')
    sign = Decimal(1 if fill['side'] == 'buy' else -1)
    qty = Decimal(fill['qty'])
    unrounded = reference * (1 + sign * Decimal('.001'))
    expected = unrounded.quantize(CENT, rounding=ROUND_CEILING if sign > 0 else ROUND_FLOOR)
    if price != expected:
        raise ValueError('Cashflow price differs from declared adverse slip/tick model')
    # Retain the original tariff base and amount, do not lower fees by switching
    # the notional to the reference open when splitting components.
    if charge != Decimal(str(fee(fill['qty'] * fill['price'], fill['side']))):
        raise ValueError('Fee differs from original model-notional tariff')
    slip = qty * reference * Decimal('.001')
    tick = sign * qty * (price - unrounded)
    modeled_notional = qty * price
    reference_notional = qty * reference
    delta = -sign * modeled_notional - charge
    separated = -sign * reference_notional - slip - tick - charge
    if slip < 0 or tick < 0 or delta != separated or money(delta) != delta:
        raise ValueError('Cost decomposition does not reconcile exactly to cents')
    return {'reference_open': str(reference), 'reference_role': 'provider_daily_open_unverified_execution',
            'model_cashflow_price': str(price), 'model_price_role': 'adverse_cost_adjusted_cashflow_unit_price',
            'reference_notional': str(reference_notional), 'modeled_notional': str(modeled_notional),
            'adverse_slippage_cost': str(slip), 'adverse_tick_cost': str(tick), 'original_fee': str(charge),
            'model_cash_delta': str(delta), 'separated_cash_delta': str(separated),
            'execution_verified': False, 'tax_basis_verified': False}


def reconcile(dataset, path, capital):
    engine.validate_dataset(dataset)
    cash = decimal(capital)
    if cash <= 0 or not isinstance(path, dict) or not isinstance(path.get('fills'), list) or not isinstance(path.get('curve'), list):
        raise ValueError('Nonempty bounded model path required')
    fills, curve = path['fills'], path['curve']
    if not curve or len(fills) > 100000 or len(curve) > 20000:
        raise ValueError('Nonempty bounded model path required')
    maps = {s: {b['date']: b for b in p['bars']} for s, p in dataset.items()}
    by_day = defaultdict(list)
    previous = ''
    for row in curve:
        day = row['date']
        if day <= previous or any(day not in m for m in maps.values()):
            raise ValueError('Curve requires unique ordered complete dates')
        previous = day
    days = {r['date'] for r in curve}
    seen = set()
    fill_previous = ''
    for f in fills:
        day, symbol = f['date'], f['symbol']
        identity = engine.digest(f)
        if day not in days or symbol not in maps or day < fill_previous or identity in seen:
            raise ValueError('Duplicate/out-of-order/uncovered fill')
        seen.add(identity); fill_previous = day
        prior = [b['date'] for b in dataset[symbol]['bars'] if b['date'] < day]
        if not prior or f.get('signal_date') != prior[-1]:
            raise ValueError('Fill requires prior completed signal date')
        by_day[day].append(f)
    positions = {}
    receipts, daily = [], []
    totals = {'adverse_slippage_cost': Decimal(0), 'adverse_tick_cost': Decimal(0), 'original_fee': Decimal(0)}
    for row in curve:
        day = row['date']
        for f in by_day[day]:
            s, qty = f['symbol'], f['qty']
            receipt = decompose(f, maps[s][day]['open'])
            price, charge = Decimal(receipt['model_cashflow_price']), Decimal(receipt['original_fee'])
            pos = positions.get(s, {'qty': 0, 'basis': Decimal(0)})
            realized = None
            if f['side'] == 'buy':
                if f.get('realized_pnl') is not None:
                    raise ValueError('Buy cannot realize model profit')
                positions[s] = {'qty': pos['qty'] + qty, 'basis': pos['basis'] + qty * price + charge}
            else:
                if qty > pos['qty']:
                    raise ValueError('Cannot sell unheld model shares')
                basis = pos['basis'] * Decimal(qty) / Decimal(pos['qty'])
                realized = money(qty * price - charge - basis)
                if realized != decimal(f.get('realized_pnl')):
                    raise ReconciliationError('Realized model profit differs after explicit cost split',
                        {'date':day, 'symbol':s, 'fill_digest':engine.digest(f), 'allocated_model_basis':str(basis),
                         'unrounded_decimal_pnl':str(qty * price - charge - basis),
                         'decimal_half_even_pnl':str(realized), 'original_reported_pnl':str(f.get('realized_pnl'))})
                if qty == pos['qty']: positions.pop(s)
                else: positions[s] = {'qty': pos['qty'] - qty, 'basis': pos['basis'] - basis}
            cash = money(cash + Decimal(receipt['separated_cash_delta']))
            if cash < 0: raise ValueError('Separated model cash is negative')
            for key in totals: totals[key] += Decimal(receipt[key])
            receipts.append({'fill_digest': engine.digest(f), 'date': day, 'symbol': s, 'side': f['side'], 'qty': qty,
                             **receipt, 'realized_model_pnl': str(realized) if realized is not None else None, 'cash_after': str(cash)})
        nav = money(cash + sum((p['qty'] * decimal(maps[s][day]['close']) for s, p in positions.items()), Decimal(0)))
        if cash != decimal(row['cash']) or nav != decimal(row['nav']):
            raise ValueError('Daily cash/NAV differs after explicit cost split')
        daily.append({'date': day, 'cash': str(cash), 'nav': str(nav), 'model_quantities': {s:p['qty'] for s,p in positions.items()}})
    final = path['state']
    if cash != decimal(final['cash']) or {s:p['qty'] for s,p in positions.items()} != {s:p['qty'] for s,p in final['positions'].items()}:
        raise ValueError('Final model account differs')
    return {'schema': 'fixed-path-cost-book-v1', 'fills_reconciled': len(receipts), 'daily_phases_reconciled': len(daily),
            'all_cash_nav_realized_pnl_and_quantities_equal': True, 'total_costs': {k:str(v) for k,v in totals.items()},
            'final_cash': str(cash), 'final_model_positions': {s:{'qty':p['qty'], 'weighted_model_basis':str(p['basis'])} for s,p in positions.items()},
            'reference_is_execution_price': False, 'execution_verified': False, 'promotion_allowed': False,
            'tax_basis_verified': False, 'order_sizes_or_risk_path_changed': False, 'receipts': receipts, 'daily': daily}
