import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
from deepfocus_api import quant_reference_prices as audit
from deepfocus_api.quant_research_engine import digest
from deepfocus_api.tests.test_quant_fill_feasibility import fixture as data_fixture


def fixture(reference='9.00', adjustment='1.00'):
    data, _ = data_fixture()
    result = {'dataset_digest': digest(data)}
    experiments = [{'id': 'unit', 'request': {'symbols': ['000333']}, 'dataset': data,
                    'result': result, 'result_digest': digest(result)}]
    _, bindings, queries = audit.scope(experiments); q = queries[0]
    rows = [['2023-04-24', q['code'], '10.00', '10.00', '3', '1', '0'],
            ['2023-04-25', q['code'], reference, '10.00', '3', '1', '0']]
    header = '00.9.10\x0196\x010000000000'
    protocol = ['0', 'success', 'query_history_k_data_plus', 'anonymous', '1', '10000', json.dumps({'record': rows}),
                q['code'], q['fields'], q['start_date'], q['end_date'], 'd', '3']
    sources = {'sdk-response-0.bin': b'00.9.10\x0101\x0100000000430\x01success\x01login\x01anonymous\x0120261030154745922\x011<![CDATA[]]>\n',
               'sdk-response-1.bin': (header + '\x01'.join(protocol)).encode(),
               'sdk-response-2.bin': b'00.9.10\x0103\x0100000000440\x01success\x01logout\x01anonymous\x0120261030154751822\x011<![CDATA[]]>\n'}
    captures = [dict(file=name, sha256=hashlib.sha256(body).hexdigest(), bytes=len(body), observed_at='2026-10-30T10:00:00Z',
                     request=q if i == 1 else None, capture_layer='SDK_decoded_protocol_not_wire', request_sha256='0'*64)
                for i, (name, body) in enumerate(sources.items())]
    m = dict(schema='baostock-historical-reference-prices-v1', errors=[], sdk_version='0.9.1',
             scope=dict(experiments=bindings, requests=queries, automatic_retries=0, parent_timeout_seconds=60,
                        maximum_sdk_requests=3, collector_sha256='0'*64),
             captures=captures, results=[dict(request=q, fields=audit.FIELDS.split(','), error_code='0', rows=rows, capture_indices=[1])],
             capture_layer='SDK_decoded_protocol_not_wire', created_at='2026-10-30T10:00:01Z', sdk_requests=3,
             collector_sha256='0'*64, sdk_source_pins={'fixture.py': '0'*64}, exchange_reference_certified=False,
             preopen_knowledge_verified=False, data_rights_verified=False, promotion_allowed=False, strategy_replays=0, production_writes=0)
    events = [dict(event_id='cash', symbol='000333', ex_date='2023-04-25', price_adjustment_per_share=adjustment)]
    return m, sources, experiments, events


def rebind(m, sources, rows):
    q = m['results'][0]['request']; parts = sources['sdk-response-1.bin'].decode()[21:].split('\x01')
    parts[6] = json.dumps({'record': rows})
    body = sources['sdk-response-1.bin'][:21] + '\x01'.join(parts).encode()
    sources['sdk-response-1.bin'] = body; m['captures'][1].update(sha256=hashlib.sha256(body).hexdigest(), bytes=len(body))
    m['results'][0]['rows'] = rows


def test_exact_match_and_first_day_unknown_and_no_mutation():
    args = fixture(); before = copy.deepcopy(args); result = audit.review(*args)
    assert result['counts']['supplied_cash_reference_match'] == 1
    assert result['rows'][0]['matches_exact_cash_reference']
    assert result['transitions'] == 1 and result['original_stock_days'] == 2
    assert len(result['first_days_unknown']) == 1
    assert not result['promotion_allowed'] and not result['exchange_reference_certified']
    assert args == before


def test_noncent_reference_preserved_and_exact_vs_cent_reported_separately():
    r = audit.review(*fixture('8.995', '1.005'))
    assert r['non_cent_references'] == 1 and r['rows'][0]['preclose'] == '8.995'
    assert r['rows'][0]['matches_exact_cash_reference'] and not r['rows'][0]['matches_cent_cash_reference']
    r = audit.review(*fixture('9.00', '1.005'))
    assert not r['rows'][0]['matches_exact_cash_reference'] and r['rows'][0]['matches_cent_cash_reference']


def test_conflicts_missing_fact_and_unchanged_cash_date_are_not_repaired():
    m, s, e, f = fixture('9.01'); r = audit.review(m, s, e, f)
    assert r['counts']['supplied_cash_reference_conflict'] == 1
    assert r['rows'][0]['preclose'] == '9.01'
    assert audit.review(m, s, e, [])['counts']['changed_without_supplied_cash_fact'] == 1
    assert audit.review(*fixture('10.00'))['counts']['supplied_cash_reference_conflict'] == 1


@pytest.mark.parametrize('change', ['rows', 'body', 'fields', 'scope', 'pagination', 'inventory', 'clock', 'source', 'sdk', 'certification', 'auth'])
def test_tampering_refused(change):
    m, s, e, f = fixture()
    if change == 'rows': m['results'][0]['rows'][1][2] = '8'
    if change == 'body': s['sdk-response-1.bin'] += b' '
    if change == 'fields': m['results'][0]['fields'] = ['date']
    if change == 'scope': m['scope']['requests'][0]['adjustflag'] = '2'
    if change == 'pagination': m['results'][0]['capture_indices'] = [0, 1]
    if change == 'inventory': s['extra'] = b'bad'
    if change == 'clock': m['created_at'] = '2099-01-01T00:00:00Z'
    if change == 'source': m['collector_sha256'] = '1'*64
    if change == 'sdk': m['sdk_requests'] = 4
    if change == 'certification': m['preopen_knowledge_verified'] = True
    if change == 'auth':
        body = s['sdk-response-0.bin'].replace(b'success', b'failure'); s['sdk-response-0.bin'] = body
        m['captures'][0].update(sha256=hashlib.sha256(body).hexdigest(), bytes=len(body))
    with pytest.raises(ValueError): audit.review(m, s, e, f)


@pytest.mark.parametrize('column,value', [(0, '2023-04-24'), (1, 'sz.000001'), (2, 'NaN'), (2, '0'), (2, '9e0'), (3, '10.01'), (4, '2'), (5, ''), (6, 'unknown')])
def test_semantic_rows_refused_even_with_matching_hash_and_sdk(column, value):
    m, s, e, f = fixture(); rows = copy.deepcopy(m['results'][0]['rows']); rows[1][column] = value
    rebind(m, s, rows)
    with pytest.raises(ValueError): audit.review(m, s, e, f)


def test_changed_original_and_duplicate_fact_refused():
    m, s, e, f = fixture(); e[0]['dataset']['000333']['bars'][0]['close'] = 11
    with pytest.raises(ValueError): audit.review(m, s, e, f)
    m, s, e, f = fixture()
    with pytest.raises(ValueError): audit.review(m, s, e, f + f)


def test_duplicate_protocol_key_refused_even_if_sdk_rows_match():
    m, s, e, f = fixture(); body = s['sdk-response-1.bin'].replace(b'{"record":', b'{"record": [], "record":')
    s['sdk-response-1.bin'] = body; m['captures'][1].update(sha256=hashlib.sha256(body).hexdigest(), bytes=len(body))
    with pytest.raises(ValueError, match='Duplicate JSON'): audit.review(m, s, e, f)


def test_first_day_event_stays_unknown_instead_of_borrowing_external_close():
    m, s, e, f = fixture(); f[0]['ex_date'] = '2023-04-24'
    r = audit.review(m, s, e, f)
    assert r['cash_facts_without_comparable_prior_day'] == ['cash']


def test_cli_symlink_path_registers_and_runs_without_losing_source_binding(tmp_path):
    root = Path(__file__).resolve().parents[3]
    script = root / 'scripts/quant-reference-price-review.py'
    alias = tmp_path / 'script-alias.py'; alias.symlink_to(script)
    m, sources, experiments, events = fixture()
    snapshot = tmp_path / 'experiments.json'; snapshot.write_text(json.dumps(experiments))
    facts = tmp_path / 'facts.json'; facts.write_text(json.dumps({'events': events}))
    m['scope']['snapshot_sha256'] = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    collector = hashlib.sha256((root / 'scripts/capture-quant-reference-prices.py').read_bytes()).hexdigest()
    m['scope']['collector_sha256'] = m['collector_sha256'] = collector
    capture = tmp_path / 'manifest.json'; capture.write_text(json.dumps(m))
    for name, body in sources.items(): (tmp_path / name).write_bytes(body)
    protocol = tmp_path / 'protocol.json'
    protocol.write_text(json.dumps(dict(schema='reference-price-coverage-protocol-v1', promotion_allowed=False,
        strategy_changes=0, registered_at='2026-10-30T11:00:00Z', **{name + '_sha256': hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in [('experiments', snapshot), ('cash_facts', facts), ('capture', capture)]})))
    lock = tmp_path / 'lock.json'; output = tmp_path / 'report.json'
    args = ['--experiments', str(snapshot), '--cash-facts', str(facts), '--capture', str(capture),
            '--protocol', str(protocol), '--lock', str(lock)]
    for mode, extra in [('register', []), ('run', ['--output', str(output)])]:
        result = subprocess.run([sys.executable, str(alias), mode] + args + extra, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(output.read_text())
    assert report['review']['counts']['supplied_cash_reference_match'] == 1
    assert 'scripts/quant-reference-price-review.py' in report['lock']['bindings']['source_pins']
