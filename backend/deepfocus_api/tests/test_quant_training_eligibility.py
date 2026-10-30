import copy
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from deepfocus_api import quant_training_eligibility as control
from deepfocus_api import quant_event_training as training
from deepfocus_api import quant_event_replay as engine
from deepfocus_api.quant_dividend_book import INVESTOR
from deepfocus_api.quant_research_engine import CANDIDATES, digest
from deepfocus_api import quant_research_engine as legacy
from deepfocus_api.tests.test_quant_event_controls import registered
from deepfocus_api.tests.test_quant_event_training import protocol_for
from deepfocus_api.tests.test_quant_event_replay import fixture, SOURCE


def table(paused_sets):
    fits, schedule = [], []
    for n, paused in enumerate(paused_sets):
        cutoff = f'2024-0{n+1}-01'
        trials = [{'parameters': dict(p), 'risk_paused': i in paused, 'train_end': cutoff,
                   'metrics': {'return_pct': 10-i, 'max_drawdown_pct': -1}, 'score': 9-i}
                  for i, p in enumerate(CANDIDATES)]
        fits.append({'trials': trials, 'train_end': cutoff, 'parameters': dict(CANDIDATES[0])})
        schedule.append({'train_end': cutoff, 'test_start': cutoff, 'test_end': cutoff,
                         'parameters': dict(CANDIDATES[0])})
    return fits, schedule


def test_strict_filter_changes_only_paused_eligibility_and_keeps_zero_trade_candidates():
    fits, schedule = table([{0}, set()])
    fits[0]['trials'][1]['fills'] = 0
    original = copy.deepcopy((fits, schedule))
    plan, decisions = control.select_plan(fits, schedule)
    assert plan[0]['parameters'] == CANDIDATES[1]
    assert decisions[0]['eligible_count'] == 5
    assert decisions[0]['rejections'][0]['reason'] == 'training_drawdown_pause'
    assert (fits, schedule) == original


@pytest.mark.parametrize('first', [True, False])
def test_all_paused_stops_without_fallback_or_later_window_skip(first):
    paused = set(range(6))
    fits, schedule = table([paused, set()] if first else [set(), paused, set()])
    plan, decisions = control.select_plan(fits, schedule)
    assert len(plan) == (0 if first else 1)
    assert decisions[0 if first else 1]['selected'] is None
    assert decisions[-1]['selected'] is not None
    assert decisions[-1]['execution_reachable'] is False


@pytest.mark.parametrize('damage', ['missing', 'duplicate', 'nonfinite', 'bool', 'score', 'winner'])
def test_corrupt_trial_table_is_rejected(damage):
    fits, schedule = table([set()])
    trial = fits[0]['trials'][0]
    if damage == 'missing':
        fits[0]['trials'].pop()
    elif damage == 'duplicate':
        fits[0]['trials'][1]['parameters'] = dict(trial['parameters'])
    elif damage == 'nonfinite':
        trial['score'] = float('nan')
    elif damage == 'bool':
        trial['risk_paused'] = 'false'
    elif damage == 'score':
        trial['score'] += 1
    else:
        fits[0]['parameters'] = dict(CANDIDATES[1])
    with pytest.raises(ValueError):
        control.select_plan(fits, schedule)


def test_financial_comparison_preserves_fifo_cash_claims_and_costs():
    data, event, days = fixture()
    first = {'train_end': days[19], 'test_start': days[25], 'test_end': days[60],
             'parameters': dict(CANDIDATES[0])}
    second = {'train_end': days[60], 'test_start': days[66], 'test_end': days[85],
              'parameters': dict(CANDIDATES[3])}
    def replay(plan):
        return engine.replay(data, ['000333'], CANDIDATES[0], [event], SOURCE,
                             investor=INVESTOR, mode='historical_reconstruction',
                             start=days[25], end=days[60], schedule=plan)
    a, b = replay([first]), replay([first, second])
    assert a['fills'] and a['fills'][0]['id'] != b['fills'][0]['id']
    assert control._financial_equal(a, b)
    for key in ('cash', 'fees'):
        bad = copy.deepcopy(b)
        bad['state']['book'][key] = str(float(bad['state']['book'][key])+1)
        assert not control._financial_equal(a, bad)
    bad = copy.deepcopy(b)
    bad['state']['book']['lots'][0]['qty'] += 1
    assert not control._financial_equal(a, bad)
    bad = copy.deepcopy(b)
    bad['state']['book']['claims'][event.event_id]['parts'][0]['remaining'] += 1
    assert not control._financial_equal(a, bad)
    bad = copy.deepcopy(b)
    bad['state']['book']['fill_ids'][bad['fills'][0]['id']] = 'corrupt'
    with pytest.raises(ValueError, match='identities'):
        control._financial_equal(a, bad)


@pytest.mark.parametrize('all_paused', [False, True])
def test_actual_registered_parent_replay_and_boundary_fit(registered, all_paused):
    _, experiments, manifest, _, sources = registered
    if all_paused:
        from deepfocus_api.tests.test_quant_research_loop import dataset, request
        data, req = dataset(320, step=.5), request()
        for k in ('open', 'high', 'low', 'close'):
            data['600519']['bars'][80][k] *= .4
        raw = legacy.walk_forward(data, req['symbols'], req['benchmark'], req['capital'])
        experiments = [{'id': 'synthetic-all-paused', 'request': req, 'dataset': data,
                        'result': raw, 'result_digest': digest(raw)}]
        manifest, sources = {'events': []}, {}
    parent = training.run_controls(protocol_for(experiments, manifest), experiments, manifest, sources)
    protocol = {'schema': 'paused-training-exclusion-v1', 'rules': copy.deepcopy(control.RULES),
                'registered_at': (datetime.now(timezone.utc)-timedelta(seconds=2)).isoformat(),
                'purpose': 'seen_development_single_hypothesis', 'promotion_allowed': False,
                'bindings': control.bindings(parent, experiments, manifest)}
    result = control.run_control(protocol, parent, experiments, manifest, sources)
    row = result['results'][0]
    assert row['baseline_reproduced_exactly'] and row['boundary_fit_reproduced'] == 1
    assert row['prefix_diagnostic'] is None
    if all_paused:
        assert not row['full_period_defined'] and row['full_period_result'] is None
        assert row['schedule'] == [] and row['no_eligible']['fold'] == 1
        assert row['paired_same_period_baseline'] is None
        assert row['financial_paths_equal_over_defined_period'] is None
    else:
        assert row['full_period_defined'] and row['financial_paths_equal_over_defined_period']
    assert not row['gate']['passed'] and not result['promotion_allowed']
    changed = copy.deepcopy(parent)
    changed['results'][0]['training']['refit_expanded'][0]['trials'][0]['score'] += 1
    with pytest.raises(ValueError, match='changed'):
        control.validate_protocol(protocol, changed, experiments, manifest)


@pytest.mark.parametrize('damage', ['runner', 'parent', 'path', 'overwrite'])
def test_cli_preserves_previous_results_and_rejects_unbound_inputs(tmp_path, damage):
    script = Path(__file__).resolve().parents[3]/'scripts/quant-training-eligibility-control.py'
    paths = {k: tmp_path/(k+'.json') for k in ('parent', 'experiments', 'events', 'protocol', 'output')}
    paths['parent'].write_text('{}')
    paths['experiments'].write_text('[]')
    paths['events'].write_text(json.dumps({'source_files': {'0'*64: '../escape.pdf'}}))
    paths['protocol'].write_text(json.dumps({'runner_sha256': '0'*64 if damage == 'runner'
        else hashlib.sha256(script.read_bytes()).hexdigest(), 'parent_report_sha256': '0'*64
        if damage == 'parent' else hashlib.sha256(paths['parent'].read_bytes()).hexdigest()}))
    if damage == 'overwrite':
        paths['output'].write_text('retain prior failure')
    args = [sys.executable, str(script)]
    for k, v in paths.items():
        args.extend(['--'+k, str(v)])
    result = subprocess.run(args+['--evidence-dir', str(tmp_path)], capture_output=True, text=True)
    assert result.returncode != 0
    if damage == 'overwrite':
        assert paths['output'].read_text() == 'retain prior failure'
    else:
        assert not paths['output'].exists()
