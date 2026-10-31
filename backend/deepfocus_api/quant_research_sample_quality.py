"""Retained sample capture validation and explicit lifecycle/gap inventory.

This inventories price inputs, not strategy performance, total returns, or PIT.
"""
from datetime import date, datetime, time, timezone
from fractions import Fraction as F
import hashlib
import re

from . import quant_research_sample as sampling
from .quant_corporate_actions import BJ, timestamp
from .quant_reference_prices import strict_json
from .quant_security_status import decode_query

RULES = {'date_expectation': 'bound_provider_start_members_until_exclusive_outDate_on_original_sessions',
         'missing': 'report_no_intersection_no_fill_no_replacement',
         'suspension': 'preserve_zero_or_missing_prices_as_unknown_not_trade_permission',
         'reference_changes': 'inventory_only_adjacent_expected_days_with_positive_close_and_preclose',
         'prices': 'plain_decimal_preserved_no_rounding', 'returns': 'not_computed',
         'economic_events': 'required_not_inferred_from_reference_changes',
         'promotion_allowed': False, 'strategy_evaluations': 0}


def number(value, *, empty=False):
    if empty and value == '': return None
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,16}(?:\.[0-9]{1,10})?', value):
        raise ValueError('Invalid bounded numeric field')
    return F(value)


def review(plan, manifest, sources, expected_dates):
    queries = sampling.validate_plan(plan); captures = manifest['captures']; results = manifest['results']
    if (manifest.get('schema') != 'baostock-historical-research-sample-v1' or manifest.get('errors') != []
            or manifest.get('sdk_version') != '0.9.1' or manifest.get('sdk_requests') != len(queries) + 2
            or manifest['scope']['requests'] != queries or manifest['scope']['rules'] != sampling.RULES
            or manifest['scope']['automatic_retries'] != 0 or manifest['scope']['parent_timeout_seconds'] != 180
            or manifest['scope']['maximum_sdk_requests'] != len(queries) + 2
            or manifest['collector_sha256'] != plan['collector_sha256']
            or manifest['scope']['collector_sha256'] != plan['collector_sha256']
            or len(captures) != len(queries) + 2 or len(results) != len(queries)):
        raise ValueError('Incomplete or changed registered capture')
    if any(manifest.get(k) is not False for k in ['exchange_reference_certified', 'preopen_knowledge_verified', 'data_rights_verified', 'promotion_allowed']):
        raise ValueError('Unsupported certification claim')
    if manifest.get('strategy_replays') != 0 or manifest.get('production_writes') != 0:
        raise ValueError('Capture outside read-only scope')
    if not manifest.get('sdk_source_pins') or manifest['capture_layer'] != 'SDK_decoded_protocol_not_wire':
        raise ValueError('Missing inspected SDK evidence')
    names = ['sdk-response-' + str(i) + '.bin' for i in range(len(captures))]
    if set(sources) != set(names) or [c['file'] for c in captures] != names: raise ValueError('Response inventory changed')
    if timestamp(plan['registered_at']) > timestamp(captures[0]['observed_at']):
        raise ValueError('Sample was not registered before capture')
    if any(not isinstance(b, bytes) or len(b) > 4 * 1024**2 for b in sources.values()) or sum(map(len, sources.values())) > 32 * 1024**2:
        raise ValueError('Response byte budget exceeded')
    created = timestamp(manifest['created_at']); previous = None
    if created > datetime.now(timezone.utc): raise ValueError('Future capture')
    for c in captures:
        observed = timestamp(c['observed_at']); body = sources[c['file']]
        if (len(body) != c['bytes'] or hashlib.sha256(body).hexdigest() != c['sha256']
                or c['capture_layer'] != manifest['capture_layer'] or observed > created
                or (previous is not None and observed < previous)
                or not re.fullmatch('[0-9a-f]{64}', c['request_sha256'])):
            raise ValueError('Response bytes, scope or clock changed')
        previous = observed
    for c, kind, operation in [(captures[0], '01', 'login'), (captures[-1], '03', 'logout')]:
        pattern = (r'00\.9\.10\x01' + kind + r'\x01[0-9]{10}0\x01success\x01' + operation
                   + r'\x01anonymous\x01[0-9]{17}\x01[0-9]+<!\[CDATA\[\]\]>\n')
        if c['request'] is not None or not re.fullmatch(pattern, sources[c['file']].decode()):
            raise ValueError('Unverified anonymous session')
    if set(expected_dates) != {q['code'] for q in queries}: raise ValueError('Lifecycle scope differs')
    stocks = []
    for i, (q, result) in enumerate(zip(queries, results), 1):
        c = captures[i]
        if (result['request'] != q or c['request'] != q or result['capture_indices'] != [i]
                or result['fields'] != sampling.FIELDS.split(',') or result['error_code'] != '0'):
            raise ValueError('Query, fields or pagination changed')
        raw = sources[c['file']]; parts = raw.decode()[21:].split('\x01')
        if len(parts) != 13 or set(strict_json(parts[6])) != {'record'}: raise ValueError('Ambiguous protocol payload')
        rows = decode_query(raw, q)
        if rows != result['rows']: raise ValueError('SDK rows differ from retained protocol')
        expected = expected_dates[q['code']]
        if (not isinstance(expected, list) or not expected or len(expected) > 800 or expected != sorted(set(expected))
                or any(not q['start_date'] <= d <= q['end_date'] for d in expected)):
            raise ValueError('Invalid bound lifecycle dates')
        parsed = {}; prior_day = ''; suspended = []; unknown_price = []; st_days = []; reference_changes = []
        for values in rows:
            if not isinstance(values, list) or len(values) != 13 or any(not isinstance(v, str) for v in values):
                raise ValueError('Malformed research row')
            day, code, opening, high, low, close, ref, volume, amount, adjustment, turn, trade, st = values
            if (not prior_day < day or not q['start_date'] <= day <= q['end_date'] or code != q['code']
                    or adjustment != '3' or trade not in {'0', '1'} or st not in {'0', '1'}
                    or timestamp(c['observed_at']) < datetime.combine(date.fromisoformat(day), time(16, 30), BJ)):
                raise ValueError('Wrong day, identity, adjustment, status or clock')
            prices = [number(v, empty=trade == '0') for v in [opening, high, low, close, ref]]
            v = number(volume, empty=trade == '0'); number(amount, empty=trade == '0'); number(turn, empty=trade == '0')
            if v is not None and v.denominator != 1: raise ValueError('Volume must be integral shares')
            valid_prices = all(p is not None and p > 0 for p in prices)
            if trade == '1' and not valid_prices: raise ValueError('Normal session with unknown/nonpositive price')
            if valid_prices and not prices[2] <= min(prices[0], prices[3]) <= max(prices[0], prices[3]) <= prices[1]:
                raise ValueError('Inconsistent OHLC range')
            if trade == '0': suspended.append(day)
            if st == '1': st_days.append(day)
            if not valid_prices: unknown_price.append(day)
            parsed[day] = (prices[3], prices[4]); prior_day = day
        for previous_day, day in zip(expected, expected[1:]):
            before, current = parsed.get(previous_day), parsed.get(day)
            if before and current and before[0] is not None and current[1] is not None and before[0] > 0 and current[1] > 0 and before[0] != current[1]:
                reference_changes.append(day)
        stocks.append({'code': q['code'], 'expected_member_dates': len(expected), 'provider_rows': len(rows),
                       'missing_member_dates': sorted(set(expected) - set(parsed)), 'outside_member_dates': sorted(set(parsed) - set(expected)),
                       'provider_suspended_dates': suspended, 'provider_st_dates': st_days, 'unknown_price_dates': unknown_price,
                       'reference_change_dates_requiring_event_review': reference_changes, 'response_sha256': c['sha256']})
    return {'schema': 'historical-research-sample-quality-v1', 'rules': RULES, 'stocks': stocks,
            'selected_stocks': len(stocks), 'provider_rows': sum(r['provider_rows'] for r in stocks),
            'expected_member_rows': sum(r['expected_member_dates'] for r in stocks),
            'all_lifecycle_dates_equal': all(not r['missing_member_dates'] and not r['outside_member_dates'] for r in stocks),
            'strategy_evaluations': 0, 'returns_computed': False, 'new_unseen_validation': False,
            'economic_event_completeness_verified': False, 'data_rights_verified': False,
            'promotion_allowed': False, 'commercial_ready': False}
