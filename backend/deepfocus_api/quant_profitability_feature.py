"""One exact descriptive feature; never a strategy score or trading permission."""
from fractions import Fraction
import hashlib
from pathlib import Path

from . import quant_financial_fact_derived as derived

VERSION = 'qr-consolidated-profitability-feature-v1'
POLICY = {
    'version': VERSION,
    'scope': 'one descriptive research feature, no selection or orders',
    'numerator': 'consolidated_net_profit_TTM_from_one_owned_extraction',
    'denominator': 'explicit_beginning_and_ending_total_assets_simple_mean',
    'positivity': 'both_asset_endpoints_strictly_positive_negative_profit_allowed',
    'arithmetic': 'exact_rational_no_rounding_before_division',
    'display': 'percent_six_decimals_half_even_not_used_in_calculation',
    'default_mode': 'as_recorded',
    'missing': 'no_old_fact_or_packet_fallback_no_partial_ratio',
    'provenance': 'one_underlying_readonly_snapshot_one_clock_one_packet',
    'eligibility': 'not_strategy_usable_or_promotion_ready',
}


def _pins():
    return {**derived.LOADED_PINS,
            'quant_profitability_feature.py': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


LOADED_PINS = _pins()


def require_current_source():
    derived.require_current_source()
    if _pins() != LOADED_PINS:
        raise ValueError('Loaded profitability feature source changed')


def _percent(value):
    scaled = value * 100 * 10**6
    sign = '-' if scaled < 0 else ''
    q, r = divmod(abs(scaled.numerator), scaled.denominator)
    if 2*r > scaled.denominator or (2*r == scaled.denominator and q % 2):
        q += 1
    if q == 0:
        sign = ''
    whole, part = divmod(q, 10**6)
    return f'{sign}{whole}.{part:06}'


def review(owner, identity, *, issuer_id, symbol, period_end, as_of,
           asset_endpoints, mode='as_recorded'):
    require_current_source()
    if not isinstance(asset_endpoints, dict):
        raise ValueError('Explicit asset endpoints required for profitability')
    inputs = derived.review(owner, identity, issuer_id=issuer_id, symbol=symbol,
        period_end=period_end, metric='consolidated_net_profit', as_of=as_of,
        mode=mode, asset_endpoints=asset_endpoints)
    if not all(inputs.get(key) is True for key in (
            'single_readonly_snapshot', 'one_extraction_snapshot_only',
            'same_knowledge_clock_for_all_components')):
        raise ValueError('Feature inputs lost their snapshot or clock contract')
    status = 'unavailable'
    reason = inputs['availability_reason']
    value = None
    if inputs['status'] == 'arithmetic_available':
        assets = inputs['result']['asset_components']
        if len(assets) != 2 or any(n['fact'] is None or n['fact']['value'] is None for n in assets):
            raise ValueError('Available derivation lacks asset endpoints')
        endpoints = [Fraction(n['fact']['value']) for n in assets]
        if any(v <= 0 for v in endpoints):
            reason = 'nonpositive_asset_endpoint'
        else:
            mean = sum(endpoints) / 2
            stored = inputs['endpoint_average_assets']['fraction_yuan']
            if mean != Fraction(int(stored['numerator']), int(stored['denominator'])):
                raise ValueError('Exact asset denominator does not match endpoint mean')
            value = Fraction(inputs['ttm_value']) / mean
            status = 'arithmetic_available'
            reason = 'exact_ratio_from_one_verified_extraction'
    result = {
        'schema': VERSION, 'policy': dict(POLICY), 'status': status,
        'availability_reason': reason,
        'feature': {'code': 'consolidated_profit_ttm_over_endpoint_mean_assets',
                    'unit': 'dimensionless',
                    'exact_fraction': None if value is None else
                        {'numerator': str(value.numerator), 'denominator': str(value.denominator)},
                    'display_percent': None if value is None else _percent(value),
                    'display_rounding_only': True},
        'inputs': inputs, 'source_pins': dict(LOADED_PINS),
        'ratios_generated': value is not None, 'scores_or_orders_generated': False,
        'accounting_comparability_verified': False, 'historical_pit_feed_certified': False,
        'usable_for_strategy': False, 'promotion_allowed': False, 'commercial_ready': False,
    }
    require_current_source()
    return result
