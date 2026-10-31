import copy
import hashlib
import json

import pytest
from deepfocus_api import quant_research_sample as sampling, quant_research_sample_quality as audit
from deepfocus_api.tests.test_quant_reference_prices import fixture as reference_fixture


def fixture():
    original, old_sources, _, _ = reference_fixture()
    plan = dict(schema='historical-research-sample-v1', registered_at='2026-10-30T09:00:00Z', rules=copy.deepcopy(sampling.RULES),
                eligible_codes=['sz.' + str(i).zfill(6) for i in range(100)], start='2023-10-26', end='2026-10-29', collector_sha256='0'*64)
    plan['requests'] = sampling.sample(plan['eligible_codes'], plan['start'], plan['end'])
    m = copy.deepcopy(original); sources = {}; captures = []; results = []; expected = {}
    for i in range(66):
        name = 'sdk-response-' + str(i) + '.bin'; q = plan['requests'][i-1] if 1 <= i <= 64 else None
        if q:
            rows = [[day, q['code'], '10', '11', '9', '10', '10', '100', '1000', '3', '0.1', '1', '0'] for day in ['2023-10-26', '2023-10-27']]
            payload = ['0', 'success', 'query_history_k_data_plus', 'anonymous', '1', '10000', json.dumps({'record': rows}),
                       q['code'], q['fields'], q['start_date'], q['end_date'], 'd', '3']
            raw = ('00.9.10\x0196\x010000000000' + '\x01'.join(payload)).encode()
            results.append(dict(request=q, fields=sampling.FIELDS.split(','), rows=rows, error_code='0', capture_indices=[i]))
            expected[q['code']] = [r[0] for r in rows]
        else: raw = old_sources['sdk-response-0.bin' if i == 0 else 'sdk-response-2.bin']
        sources[name] = raw
        captures.append(dict(file=name, request=q, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), request_sha256='0'*64,
                             capture_layer='SDK_decoded_protocol_not_wire', observed_at='2026-10-30T10:00:00Z'))
    m.update(schema='baostock-historical-research-sample-v1', sdk_requests=66, results=results, captures=captures)
    m['scope'].update(requests=plan['requests'], rules=plan['rules'], maximum_sdk_requests=66, parent_timeout_seconds=180)
    return plan, m, sources, expected


def replace_rows(m, sources, rows):
    name = 'sdk-response-1.bin'; raw = sources[name]; parts = raw.decode()[21:].split('\x01')
    parts[6] = json.dumps({'record': rows}); body = raw[:21] + '\x01'.join(parts).encode()
    sources[name] = body; m['results'][0]['rows'] = rows
    m['captures'][1].update(bytes=len(body), sha256=hashlib.sha256(body).hexdigest())


def test_nonempty_inventory_not_performance_or_pit():
    args = fixture(); before = copy.deepcopy(args); r = audit.review(*args)
    assert r['provider_rows'] == r['expected_member_rows'] == 128 and r['all_lifecycle_dates_equal']
    assert r['selected_stocks'] == 64 and not r['returns_computed'] and not r['promotion_allowed']
    assert args == before


def test_missing_or_post_delisting_rows_preserved_as_quality_gaps_not_intersection():
    p, m, s, e = fixture(); code = p['requests'][0]['code']; e[code] = e[code][:1]
    r = audit.review(p, m, s, e)
    assert not r['all_lifecycle_dates_equal'] and r['stocks'][0]['outside_member_dates'] == ['2023-10-27']
    p, m, s, e = fixture(); replace_rows(m, s, m['results'][0]['rows'][:1]); r = audit.review(p, m, s, e)
    assert not r['all_lifecycle_dates_equal'] and r['stocks'][0]['missing_member_dates'] == ['2023-10-27']


def test_suspended_unknown_prices_remain_unknown_not_zero_returns():
    p, m, s, e = fixture(); rows = copy.deepcopy(m['results'][0]['rows'])
    rows[1][2:9] = ['', '', '', '', '', '', '']; rows[1][10:] = ['', '0', '1']
    replace_rows(m, s, rows); r = audit.review(p, m, s, e); stock = r['stocks'][0]
    assert stock['unknown_price_dates'] == stock['provider_suspended_dates'] == stock['provider_st_dates'] == ['2023-10-27']
    assert not r['returns_computed'] and not stock['reference_change_dates_requiring_event_review']


def test_reference_change_inventory_is_not_auto_event_creation():
    p, m, s, e = fixture(); rows = copy.deepcopy(m['results'][0]['rows']); rows[1][6] = '9.1234'
    replace_rows(m, s, rows); r = audit.review(p, m, s, e)
    assert r['stocks'][0]['reference_change_dates_requiring_event_review'] == ['2023-10-27']
    assert not r['economic_event_completeness_verified'] and not r['promotion_allowed']


@pytest.mark.parametrize('column,value', [(0, '2023-10-26'), (1, 'sh.600000'), (2, ''), (3, '9'), (6, 'NaN'), (7, '0.1'), (9, '2'), (11, ''), (12, 'unknown')])
def test_rebound_protocol_bad_values_refused(column, value):
    p, m, s, e = fixture(); rows = copy.deepcopy(m['results'][0]['rows']); rows[1][column] = value
    replace_rows(m, s, rows)
    with pytest.raises(ValueError): audit.review(p, m, s, e)


@pytest.mark.parametrize('change', ['raw', 'rows', 'count', 'certify', 'clock', 'scope', 'inventory'])
def test_capture_tampering_refused(change):
    p, m, s, e = fixture()
    if change == 'raw': s['sdk-response-1.bin'] += b' '
    if change == 'rows': m['results'][0]['rows'][0][2] = '11'
    if change == 'count': m['sdk_requests'] = 65
    if change == 'certify': m['exchange_reference_certified'] = True
    if change == 'clock': m['created_at'] = '2099-01-01T00:00:00Z'
    if change == 'scope': m['results'][0]['capture_indices'] = [2]
    if change == 'inventory': s['extra'] = b'bad'
    with pytest.raises(ValueError): audit.review(p, m, s, e)


def test_sample_registration_after_capture_is_refused():
    p, m, s, e = fixture(); p['registered_at'] = '2026-10-30T10:00:01Z'
    with pytest.raises(ValueError, match='registered before'): audit.review(p, m, s, e)
