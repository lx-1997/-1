import copy
from dataclasses import replace
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from deepfocus_api import quant_event_training as training
from deepfocus_api import quant_research_engine as legacy
from deepfocus_api.tests.test_quant_event_replay import fixture, SOURCE
from deepfocus_api.tests.test_quant_event_controls import registered


def test_active_dividend_training_uses_cash_and_saves_every_original_trial():
    data, event, days = fixture()
    original = copy.deepcopy(data)
    with_event = training.select(data, ['000333'], [event], SOURCE, days[70], 1_000_000)
    without = training.select(data, ['000333'], [], SOURCE, days[70], 1_000_000)
    assert len(with_event['trials']) == len(legacy.CANDIDATES) == 6
    assert with_event['trials'][0]['fills'] > 0
    assert any(t['fills'] == 0 for t in with_event['trials'])  # Unqualified signals stay in cash.
    assert any(a['score'] != b['score'] for a, b in zip(with_event['trials'], without['trials']))
    winner = max(with_event['trials'], key=lambda t: (t['score'], -t['parameters']['lookback'],
                                                    -t['parameters']['threshold']))
    assert with_event['parameters'] == winner['parameters']
    assert data == original


def test_training_physically_excludes_gap_test_and_holdout_prices():
    data, event, days = fixture()
    expected = training.select(data, ['000333'], [event], SOURCE, days[60], 1_000_000)
    changed = copy.deepcopy(data)
    for payload in changed.values():
        for row in payload['bars'][61:]:
            for key in ('open', 'high', 'low', 'close'):
                row[key] *= 10
    assert training.select(changed, ['000333'], [event], SOURCE, days[60], 1_000_000) == expected
    future = replace(event, event_id='not-yet-announced', record_date=days[80],
                     ex_date=days[81], pay_date=days[82], available_at=days[75]+'T18:00:00+08:00',
                     observed_at=days[75]+'T18:01:00+08:00')
    assert training.select(data, ['000333'], [event, future], SOURCE, days[60], 1_000_000) == expected


def protocol_for(experiments, manifest):
    return {'schema': 'event-training-basis-controls-v1', 'arms': list(training.ARMS),
            'rules': copy.deepcopy(training.RULES), 'purpose': 'seen_development_method_control',
            'registered_at': (datetime.now(timezone.utc)-timedelta(seconds=2)).isoformat(),
            'promotion_allowed': False, 'bindings': training.bindings(experiments, manifest)}


def test_complete_registered_control_has_original_dates_all_trials_and_no_promotion(registered):
    _, experiments, full, _, sources = registered
    protocol = protocol_for(experiments, full)
    report = training.run_controls(protocol, experiments, full, sources)
    row = report['results'][0]
    assert row['original_reproduced_exactly']
    assert not report['promotion_allowed'] and not report['oos_validation']
    for arm in training.ARMS:
        result = row['cases'][arm]
        assert result['fills'] and not result['gate']['passed']
        assert result['curve'][0]['date'] == experiments[0]['result']['folds'][0]['test_start']
        assert result['curve'][-1]['date'] == experiments[0]['result']['folds'][-1]['test_end']
        assert result['state']['book']['initial'] == '1000000.00'
        if arm.startswith('refit'):
            assert len(row['training'][arm]) == len(experiments[0]['result']['folds'])
            assert all(len(f['trials']) == 6 for f in row['training'][arm])
    assert any(a['trials'] != b['trials'] for a, b in zip(
        row['training']['refit_no_events'], row['training']['refit_expanded']))
    # Frozen arms never refit and keep the same original per-fold parameters.
    assert row['changed_folds']['frozen_expanded'] == 0
    assert not row['training']['frozen_expanded']


@pytest.mark.parametrize('damage', ['events', 'source', 'rule', 'promotion', 'empty', 'duplicate', 'gap', 'future'])
def test_registration_refuses_input_method_and_scope_changes(registered, damage):
    _, exps, full, _, _ = copy.deepcopy(registered)
    protocol = protocol_for(exps, full)
    if damage == 'events':
        full['events'][0]['cash_per_share_gross'] = '99'
    elif damage == 'source':
        protocol['bindings']['source_pins']['quant_event_training.py'] = '0'*64
    elif damage == 'rule':
        protocol['rules']['score'] = 'pick_after_seeing_test'
    elif damage == 'promotion':
        protocol['promotion_allowed'] = True
    elif damage == 'empty':
        exps.clear()
    elif damage == 'duplicate':
        exps.append(copy.deepcopy(exps[0]))
    elif damage == 'future':
        protocol['registered_at'] = '2099-01-01T00:00:00+00:00'
    else:
        exps[0]['result']['folds'][0]['train_end'] = exps[0]['result']['folds'][0]['test_start']
        exps[0]['result_digest'] = legacy.digest(exps[0]['result'])
        protocol['bindings'] = training.bindings(exps, full)
    with pytest.raises(ValueError):
        training.validate_protocol(protocol, exps, full)


def test_corrupted_evidence_rejected_before_original_replay(registered, monkeypatch):
    _, exps, full, _, sources = copy.deepcopy(registered)
    protocol = protocol_for(exps, full)
    sources[next(iter(sources))] = b'changed source'
    def unexpected_replay(*args):
        raise AssertionError('Evidence should be checked before computation')
    monkeypatch.setattr(training, 'diagnose', unexpected_replay)
    with pytest.raises(ValueError, match='原始公告'):
        training.run_controls(protocol, exps, full, sources)


@pytest.mark.parametrize('damage', ['runner', 'path', 'overwrite'])
def test_cli_fails_closed(tmp_path, damage):
    script = Path(__file__).resolve().parents[3]/'scripts/quant-event-training-control.py'
    files = {k: tmp_path/(k+'.json') for k in ('experiments', 'events', 'protocol', 'output')}
    files['experiments'].write_text('[]')
    files['events'].write_text(json.dumps({'source_files': {'0'*64: '../escape.pdf'}}))
    files['protocol'].write_text(json.dumps({'runner_sha256': '0'*64 if damage == 'runner'
                                           else hashlib.sha256(script.read_bytes()).hexdigest()}))
    if damage == 'overwrite':
        files['output'].write_text('retained failure')
    args = [sys.executable, str(script)]
    for k, v in files.items():
        args.extend(['--'+k, str(v)])
    result = subprocess.run(args+['--evidence-dir', str(tmp_path)], capture_output=True, text=True)
    assert result.returncode != 0
    if damage == 'overwrite':
        assert files['output'].read_text() == 'retained failure'
    else:
        assert not files['output'].exists()
