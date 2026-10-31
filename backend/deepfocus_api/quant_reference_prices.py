"""Offline retrospective supplier reference coverage; never an execution certificate."""
from datetime import date, datetime, time, timezone
from fractions import Fraction as F
import hashlib
import json
from pathlib import Path
import re

from .quant_calendar import require_aligned
from .quant_corporate_actions import BJ, timestamp
from .quant_research_engine import digest
from .quant_security_status import decode_query

FIELDS = 'date,code,preclose,close,adjustflag,tradestatus,isST'
VERSION = 'retained-reference-coverage-v1'
FILES = ('quant_reference_prices.py', 'quant_security_status.py', 'quant_calendar.py',
         'quant_corporate_actions.py', 'quant_research_engine.py')


def pins():
    return {n: hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest() for n in FILES}


LOADED_PINS = pins()


def strict_json(raw):
    def pairs(items):
        out = {}
        for k, v in items:
            if k in out: raise ValueError('Duplicate JSON key')
            out[k] = v
        return out
    def reject(value): raise ValueError('Nonfinite JSON number')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=reject)


def number(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,12}(?:\.[0-9]{1,10})?', value):
        raise ValueError('Positive bounded plain decimal required')
    result = F(value)
    if result <= 0: raise ValueError('Positive reference or close required')
    return result


def fraction(value):
    return str(value.numerator) + '/' + str(value.denominator)


def cent_half_up(value):
    if value <= 0: raise ValueError('Nonpositive cash-adjusted reference')
    units = value * 100
    return F((2 * units.numerator + units.denominator) // (2 * units.denominator), 100)


def scope(experiments):
    if not isinstance(experiments, list) or not 1 <= len(experiments) <= 10:
        raise ValueError('Explicit sealed experiments required')
    if len({e['id'] for e in experiments}) != len(experiments): raise ValueError('Duplicate experiment')
    histories = {}; bindings = []
    for e in experiments:
        if digest(e['result']) != e['result_digest'] or digest(e['dataset']) != e['result']['dataset_digest']:
            raise ValueError('Modified sealed experiment')
        require_aligned(e['dataset'], expected_symbols=list(e['dataset']))
        symbols = e['request']['symbols']
        if not symbols or len(set(symbols)) != len(symbols): raise ValueError('Invalid stock scope')
        bindings.append({'experiment': e['id'], 'dataset_digest': e['result']['dataset_digest'], 'result_digest': e['result_digest']})
        for s in symbols:
            if not re.fullmatch(r'[036][0-9]{5}', s): raise ValueError('Unsupported scoped stock')
            bars = e['dataset'][s]['bars']
            if s in histories and histories[s] != bars: raise ValueError('Conflicting stock histories')
            histories[s] = bars
    if not 1 <= len(histories) <= 10: raise ValueError('Stock scope exceeds budget')
    queries = [dict(code=('sh.' if s.startswith('6') else 'sz.') + s, fields=FIELDS,
                    start_date=b[0]['date'], end_date=b[-1]['date'], frequency='d', adjustflag='3')
               for s, b in sorted(histories.items())]
    return histories, bindings, queries


def normalize(manifest, sources, experiments):
    if pins() != LOADED_PINS: raise ValueError('Loaded source changed')
    histories, bindings, queries = scope(experiments)
    spec = manifest['scope']; captures = manifest['captures']; results = manifest['results']
    if (manifest.get('schema') != 'baostock-historical-reference-prices-v1' or manifest.get('errors') != []
            or manifest.get('sdk_version') != '0.9.1' or spec['experiments'] != bindings or spec['requests'] != queries
            or manifest.get('capture_layer') != 'SDK_decoded_protocol_not_wire'
            or spec.get('automatic_retries') != 0 or spec.get('parent_timeout_seconds') != 60
            or spec.get('maximum_sdk_requests') != len(queries) + 2
            or manifest.get('sdk_requests') != len(queries) + 2
            or len(captures) != len(queries) + 2 or len(results) != len(queries)):
        raise ValueError('Incomplete or differently scoped capture')
    for flag in ('exchange_reference_certified', 'preopen_knowledge_verified', 'data_rights_verified', 'promotion_allowed'):
        if manifest.get(flag) is not False: raise ValueError('Unsupported certification claim')
    if manifest.get('strategy_replays') != 0 or manifest.get('production_writes') != 0:
        raise ValueError('Capture exceeds read-only scope')
    if manifest['collector_sha256'] != spec['collector_sha256'] or not re.fullmatch('[0-9a-f]{64}', manifest['collector_sha256']):
        raise ValueError('Collector binding changed')
    if not manifest.get('sdk_source_pins') or any(not re.fullmatch('[0-9a-f]{64}', x) for x in manifest['sdk_source_pins'].values()):
        raise ValueError('Missing SDK source inventory')
    names = ['sdk-response-' + str(i) + '.bin' for i in range(len(captures))]
    if [c['file'] for c in captures] != names or set(sources) != set(names): raise ValueError('Capture inventory changed')
    now = datetime.now(timezone.utc); created = timestamp(manifest['created_at']); previous = None
    if created > now: raise ValueError('Future capture clock')
    if sum(len(b) for b in sources.values()) > 16 * 1024**2: raise ValueError('Capture exceeds total byte budget')
    for c in captures:
        body = sources[c['file']]; observed = timestamp(c['observed_at'])
        if (not isinstance(body, bytes) or len(body) > 4 * 1024**2 or len(body) != c['bytes']
                or hashlib.sha256(body).hexdigest() != c['sha256']
                or c['capture_layer'] != 'SDK_decoded_protocol_not_wire' or observed > created
                or (previous is not None and observed < previous) or not re.fullmatch('[0-9a-f]{64}', c['request_sha256'])):
            raise ValueError('Changed response or invalid capture clock')
        previous = observed
    if captures[0]['request'] is not None or captures[-1]['request'] is not None:
        raise ValueError('Unexpected login/logout query scope')
    for c, kind, operation in [(captures[0], '01', 'login'), (captures[-1], '03', 'logout')]:
        pattern = (r'00\.9\.10\x01' + kind + r'\x01[0-9]{10}0\x01success\x01' + operation
                   + r'\x01anonymous\x01[0-9]{17}\x01[0-9]+<!\[CDATA\[\]\]>\n')
        if not re.fullmatch(pattern, sources[c['file']].decode('utf-8')):
            raise ValueError('Unverified anonymous login/logout response')
    normalized = {}
    for i, (result, q) in enumerate(zip(results, queries), 1):
        c = captures[i]
        if (result['request'] != q or c['request'] != q or result['capture_indices'] != [i]
                or result['fields'] != FIELDS.split(',') or result['error_code'] != '0'):
            raise ValueError('Query, pagination or SDK fields differ')
        raw = sources[c['file']]
        # Reject ambiguous JSON before the independent inspected protocol decoder.
        parts = raw.decode('utf-8')[21:].split('\x01')
        if len(parts) != 13: raise ValueError('Malformed protocol')
        if set(strict_json(parts[6])) != {'record'}: raise ValueError('Unexpected response payload')
        rows = decode_query(raw, q)
        if rows != result['rows']: raise ValueError('SDK rows differ from retained protocol')
        s = q['code'][3:]; bars = histories[s]
        if len(rows) != len(bars): raise ValueError('Missing or extra original dates')
        items = []
        for values, bar in zip(rows, bars):
            if not isinstance(values, list) or len(values) != 7 or any(not isinstance(v, str) for v in values):
                raise ValueError('Malformed reference row')
            day, code, reference, close, adjustment, trade, st = values
            if (day != bar['date'] or code != q['code'] or adjustment != '3' or trade not in {'0', '1'} or st not in {'0', '1'}
                    or timestamp(c['observed_at']) < datetime.combine(date.fromisoformat(day), time(16, 30), BJ)):
                raise ValueError('Wrong day, security, adjustment, status or observation clock')
            number(reference); number(close)
            if number(close) != number(str(bar['close'])): raise ValueError('Original close mismatch; reference conclusions refused')
            items.append({'date': day, 'preclose': reference, 'close': close, 'reference_cent_aligned': (number(reference) * 100).denominator == 1,
                          'provider_trade_status': trade, 'provider_is_st': st, 'observed_at': c['observed_at'], 'response_sha256': c['sha256']})
        normalized[s] = items
    return histories, normalized


def review(manifest, sources, experiments, events):
    histories, normalized = normalize(manifest, sources, experiments)
    if not isinstance(events, list) or len(events) > 1000: raise ValueError('Bounded explicit cash facts required')
    facts = {}; identities = set()
    for e in events:
        key = (e['symbol'], e['ex_date'])
        if key in facts or e['event_id'] in identities: raise ValueError('Ambiguous cash event date or identity')
        date.fromisoformat(e['ex_date']); number(e['price_adjustment_per_share'])
        if e['symbol'] not in histories: raise ValueError('Cash fact outside original stock scope')
        if not histories[e['symbol']][0]['date'] <= e['ex_date'] <= histories[e['symbol']][-1]['date']:
            raise ValueError('Cash fact outside frozen interval')
        facts[key] = e; identities.add(e['event_id'])
    transitions = []; first = []; consumed = set()
    for s, rows in normalized.items():
        first.append({'symbol': s, 'date': rows[0]['date'], 'status': 'prior_raw_close_outside_frozen_window_unknown'})
        for prior, current in zip(histories[s], rows[1:]):
            key = (s, current['date']); e = facts.get(key); reference = number(current['preclose']); previous = number(str(prior['close']))
            changed = reference != previous
            row = {'symbol': s, 'date': current['date'], 'prior_frozen_raw_close': str(prior['close']),
                   **current, 'changed_from_prior_raw_close': changed, 'cash_event_id': e['event_id'] if e else None,
                   'coverage_status': 'changed_without_supplied_cash_fact' if changed else 'unchanged_without_supplied_cash_fact'}
            if e:
                consumed.add(key); expected = previous - number(e['price_adjustment_per_share']); rounded = cent_half_up(expected)
                row.update(cash_adjustment=e['price_adjustment_per_share'], exact_expected_reference=fraction(expected),
                           cent_half_up_expected_reference=fraction(rounded), matches_exact_cash_reference=reference == expected,
                           matches_cent_cash_reference=reference == rounded,
                           coverage_status='supplied_cash_reference_match' if reference in {expected, rounded} else 'supplied_cash_reference_conflict')
            transitions.append(row)
    counts = {name: sum(r['coverage_status'] == name for r in transitions) for name in
              ['changed_without_supplied_cash_fact', 'unchanged_without_supplied_cash_fact', 'supplied_cash_reference_match', 'supplied_cash_reference_conflict']}
    if pins() != LOADED_PINS: raise ValueError('Source changed during audit')
    return {'version': VERSION, 'source_pins': dict(LOADED_PINS), 'capture_digest': digest(manifest),
            'original_stock_days': sum(map(len, normalized.values())), 'transitions': len(transitions), 'counts': counts,
            'reference_changes': sum(r['changed_from_prior_raw_close'] for r in transitions),
            'non_cent_references': sum(not r['reference_cent_aligned'] for rows in normalized.values() for r in rows),
            'first_days_unknown': first, 'cash_facts_without_comparable_prior_day': [facts[k]['event_id'] for k in sorted(set(facts) - consumed)],
            'original_closes_and_dates_match': True, 'rows': transitions,
            'economic_event_completeness_verified': False, 'exchange_reference_certified': False,
            'preopen_knowledge_verified': False, 'strategy_replays': 0, 'strategy_changes': 0,
            'promotion_allowed': False, 'commercial_ready': False, 'production_writes': 0}
