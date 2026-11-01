import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest
from deepfocus_api import quant_cohort_coverage as coverage
from deepfocus_api.tests.test_quant_feature_panel import plan
from deepfocus_api.tests.test_quant_financial_fact_derived import clock, ledger, setup


def cohort():
    codes = ['sz.002109'] + ['sh.'+str(600000+i) for i in range(63)]
    result = {'schema': 'historical-research-sample-v1', 'rules': coverage.sample.RULES,
              'eligible_codes': codes, 'start': '2023-10-26', 'end': '2026-10-29'}
    result['requests'] = coverage.sample.sample(codes, result['start'], result['end'])
    return result


def one(packet):
    result = plan(packet)
    result['cells'] = result['cells'][:1]
    return result


def test_recompute_owned_panel_keep_all64_and_separate_absence_from_unavailability(clock):
    _, packet, _ = setup(); sample = cohort(); request = one(packet)
    original = copy.deepcopy((sample, request))
    available = coverage.build('a', sample, request)
    assert (sample, request) == original
    assert available['coverage_fraction'] == {'numerator': 1, 'denominator': 64}
    assert available['not_declared_members'] == 63 and available['declared_unavailable_members'] == 0
    assert [r['code'] for r in available['rows']] == [r['code'] for r in sample['requests']]
    assert [r['sample_rank'] for r in available['rows']] == list(range(64))
    assert sum(available['missing_mask']) == 63
    request['cells'][0]['as_of'] = '2026-10-02T23:59:59+08:00'
    missing = coverage.build('a', sample, request)
    assert missing['coverage_fraction'] == {'numerator': 0, 'denominator': 64}
    assert missing['not_declared_members'] == 63 and missing['declared_unavailable_members'] == 1
    assert missing['missing_mask'] == [True]*64
    for result in (available, missing):
        assert result['independent_sample_size'] is None
        assert all(result[k] is False for k in ('historical_membership_source_verified', 'accounting_comparability_verified', 'usable_for_strategy', 'promotion_allowed', 'commercial_ready'))
        raw = copy.deepcopy(result); fingerprint = raw.pop('report_digest')
        assert coverage.digest(raw) == fingerprint
    with pytest.raises(LookupError): coverage.build('b', sample, request)


@pytest.mark.parametrize('damage', ['sample-order', 'sample-rules', 'outside', 'multi-period', 'multi-clock', 'symbol-duplicate', 'issuer-conflict'])
def test_scope_ambiguity_and_outcome_changed_sample_fail_before_DB_read(monkeypatch, damage):
    sample = cohort(); request = one({'id': 'test-packet'})
    if damage == 'sample-order': sample['requests'].reverse()
    if damage == 'sample-rules': sample['rules'] = {**sample['rules'], 'current_status_filter': True}
    if damage == 'outside': request['cells'][0]['symbol'] = '600999'
    if damage in ('multi-period', 'multi-clock', 'symbol-duplicate', 'issuer-conflict'):
        cell = copy.deepcopy(request['cells'][0]); cell.update(symbol='600000', issuer_id='cninfo:other001')
        if damage == 'multi-period': cell['period_end'] = '2025-06-30'
        if damage == 'multi-clock': cell['as_of'] = '2026-10-04T00:00:00+08:00'
        if damage == 'symbol-duplicate': cell['symbol'] = '002109'
        if damage == 'issuer-conflict': cell['issuer_id'] = request['cells'][0]['issuer_id']
        request['cells'].append(cell)
    def forbidden(*args): raise AssertionError('must reject before reading owned packets')
    monkeypatch.setattr(coverage.panel, 'build', forbidden)
    with pytest.raises(ValueError): coverage.build('a', sample, request)


def test_canonical_clock_aliases_and_panel_order_do_not_change_cohort_order(monkeypatch):
    sample = cohort(); request = one({'id': 'p1'})
    other = copy.deepcopy(request['cells'][0]); other.update(symbol='600000', issuer_id='cninfo:other001', packet_id='p2', as_of='2026-10-02T16:00:00Z')
    request['cells'].insert(0, other)
    def compute(owner, frozen):
        return {'rows': [{'request': c, 'observation': {'status': 'arithmetic_available' if c['symbol'] == '002109' else 'unavailable'}} for c in frozen['cells']],
                'plan_digest': 'projection-only', 'report_digest': 'projection-only', 'mode': frozen['mode']}
    monkeypatch.setattr(coverage.panel, 'build', compute)
    result = coverage.build('a', sample, request)
    assert result['as_of_utc'] == '2026-10-02T16:00:00+00:00'
    assert result['coverage_fraction'] == {'numerator': 1, 'denominator': 64}
    assert result['declared_unavailable_members'] == 1 and result['not_declared_members'] == 62
    assert [r['code'] for r in result['rows']] == [r['code'] for r in sample['requests']]


def test_source_guard_and_sample_budget_before_read(monkeypatch):
    request = one({'id': 'p'})
    sample = cohort(); sample['oversized'] = 'x'*1024**2
    with pytest.raises(ValueError, match='1MiB'): coverage.build('a', sample, request)
    monkeypatch.setattr(coverage, '_pins', lambda: {})
    with pytest.raises(ValueError, match='source changed'): coverage.build('a', cohort(), request)


def cli_module():
    script = Path(__file__).resolve().parents[3]/'scripts/quant-cohort-coverage-review.py'
    spec = importlib.util.spec_from_file_location('cohort_cli', script)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return script, module


def test_actual_CLI_owner_no_overwrite_and_full_denominator(clock, tmp_path):
    _, packet, _ = setup(); script, _ = cli_module()
    sample = tmp_path/'sample.json'; sample.write_text(json.dumps(cohort()))
    declaration = tmp_path/'plan.json'; declaration.write_text(json.dumps(one(packet)))
    out = tmp_path/'report.json'
    cmd = [sys.executable, str(script), '--db', str(clock.resolve()), '--owner', 'a', '--sample', str(sample), '--plan', str(declaration), '--output', str(out)]
    result = subprocess.run(cmd, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    saved = out.read_bytes(); assert json.loads(saved)['coverage_fraction']['denominator'] == 64
    assert subprocess.run(cmd, capture_output=True, timeout=20).returncode == 2 and out.read_bytes() == saved
    cmd[cmd.index('--owner')+1] = 'b'; cmd[-1] = str(tmp_path/'foreign.json')
    assert subprocess.run(cmd, capture_output=True, timeout=20).returncode == 1 and not Path(cmd[-1]).exists()


@pytest.mark.parametrize('failure', ['sample-change', 'plan-change', 'fsync', 'race'])
def test_CLI_never_publishes_changed_input_or_partial_report(clock, tmp_path, monkeypatch, failure):
    _, packet, _ = setup(); script, cli = cli_module()
    sample = tmp_path/'sample.json'; sample.write_text(json.dumps(cohort()))
    declaration = tmp_path/'plan.json'; declaration.write_text(json.dumps(one(packet)))
    out = tmp_path/'out.json'; value = coverage.build('a', cohort(), one(packet))
    def compute(*args):
        if failure == 'sample-change': sample.write_bytes(b'changed')
        if failure == 'plan-change': declaration.write_bytes(b'changed')
        return value
    monkeypatch.setattr(cli.coverage, 'build', compute)
    if failure == 'fsync':
        def fail(*args): raise OSError('synthetic fsync failure')
        monkeypatch.setattr(cli.os, 'fsync', fail)
    if failure == 'race':
        def fail(*args): out.write_bytes(b'racing-writer'); raise FileExistsError()
        monkeypatch.setattr(cli.os, 'link', fail)
    monkeypatch.setattr(sys, 'argv', [str(script), '--db', str(clock.resolve()), '--owner', 'a', '--sample', str(sample), '--plan', str(declaration), '--output', str(out)])
    with pytest.raises((ValueError, OSError)): cli.main()
    assert out.read_bytes() == b'racing-writer' if failure == 'race' else not out.exists()
    assert not list(tmp_path.glob('.quant-cohort-*'))
