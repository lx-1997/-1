import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone, timedelta

import pytest

from deepfocus_api import quant_signal_diagnostics as diagnostic
from deepfocus_api.tests.test_quant_event_replay import fixture, SOURCE


def test_spearman_average_ties_and_undefined_are_not_fake_zero_information():
    assert diagnostic.rank_ic([1, 2, 3], [3, 2, 1]) == -1
    assert diagnostic.rank_ic([1, 2, 3], [1, 2, 3]) == 1
    assert diagnostic.ranks([1, 1, 2]) == [1.5, 1.5, 3]
    assert diagnostic.ranks([1, 1+1e-14, 2]) == [1.5, 1.5, 3]
    assert diagnostic.rank_ic([1, 1, 1], [1, 2, 3]) is None
    with pytest.raises(ValueError):
        diagnostic.rank_ic([float('nan')], [1])


@pytest.mark.parametrize('damage', ['rules', 'source', 'promotion', 'windows', 'events', 'future', 'empty'])
def test_protocol_rejects_post_registration_changes(damage):
    from deepfocus_api import quant_event_training as training
    from deepfocus_api.quant_research_engine import digest
    data, _, days = fixture()
    for symbol in ('600519', '000001'):
        data[symbol] = copy.deepcopy(data['000333'])
    result = {'dataset_digest': digest(data), 'folds': [{'train_end': days[45], 'test_start': days[51],
              'test_end': days[80], 'parameters': {'lookback': 10, 'threshold': 0.0}}]}
    exp = {'id': 'explicit-synthetic-fixture', 'request': {'symbols': ['000333', '600519', '000001']},
           'dataset': data, 'result': result, 'result_digest': digest(result)}
    experiments, manifest = [exp], {'events': []}
    parent = {'schema': 'event-training-basis-controls-v1', 'arms': list(training.ARMS),
              'rules': copy.deepcopy(training.RULES), 'purpose': 'seen_development_method_control',
              'promotion_allowed': False, 'registered_at': (datetime.now(timezone.utc)-timedelta(seconds=5)).isoformat(),
              'bindings': training.bindings(experiments, manifest)}
    reference = {'protocol': parent, 'protocol_digest': digest(parent), 'results': [{'experiment': exp['id'],
        'cases': {'refit_expanded': {'curve': [{'date': d, 'risk_paused': i>=20} for i, d in enumerate(days[51:81])]}}}]}
    protocol = {'schema': 'signal-information-diagnostic-v1', 'rules': copy.deepcopy(diagnostic.RULES),
                'purpose': 'seen_development_diagnostic_no_selection', 'promotion_allowed': False,
                'registered_at': parent['registered_at'], 'bindings': diagnostic.bindings(experiments, manifest, reference)}
    diagnostic.validate_protocol(protocol, experiments, manifest, reference)
    if damage == 'rules':
        protocol['rules']['horizons'] = [1, 5, 20]
    elif damage == 'source':
        protocol['bindings']['source_pins']['quant_signal_diagnostics.py'] = '0'*64
    elif damage == 'promotion':
        protocol['promotion_allowed'] = True
    elif damage == 'windows':
        protocol['bindings']['windows'][exp['id']]['full_seen_development'][1] = days[89]
    elif damage == 'events':
        manifest['events'].append({'unregistered': True})
    elif damage == 'future':
        protocol['registered_at'] = '2099-01-01T00:00:00+00:00'
    else:
        experiments.clear()
    with pytest.raises(ValueError):
        diagnostic.validate_protocol(protocol, experiments, manifest, reference)


def test_signals_use_previous_close_and_labels_stay_inside_scope():
    data, event, days = fixture()
    rows = diagnostic.samples(data, ['000333'], [event], days[25], days[65], 10, 20)
    assert rows and all(r['signal_date'] < r['date'] <= r['label_end'] <= days[65] for r in rows)
    assert rows[-1]['label_end'] == days[65]
    changed = copy.deepcopy(data)
    for p in changed.values():
        for b in p['bars'][66:]:
            for key in ('open', 'close', 'high', 'low'):
                b[key] *= 5
    assert diagnostic.samples(changed, ['000333'], [event], days[25], days[65], 10, 20) == rows
    changed = copy.deepcopy(data)
    changed['000333']['bars'][25].update(close=125., high=126.)
    after = diagnostic.samples(changed, ['000333'], [event], days[25], days[65], 10, 20)
    assert rows[0]['stocks'][0]['score'] == after[0]['stocks'][0]['score']


def test_ex_date_entry_label_excludes_dividend_not_earned_by_new_buyer():
    data, event, days = fixture(dividend=10)
    rows = diagnostic.samples(data, ['000333'], [event], days[31], days[50], 10, 5)
    first = rows[0]['stocks'][0]
    expected = (data['000333']['bars'][35]['close']/data['000333']['bars'][31]['open']-1)*100
    assert first['label_return_pct'] == pytest.approx(expected)
    without = diagnostic.samples(data, ['000333'], [], days[31], days[50], 10, 5)
    assert first['label_return_pct'] == pytest.approx(without[0]['stocks'][0]['label_return_pct'])
    # A buyer before ex-date sees the subsequent cash in the gross index label.
    before = diagnostic.samples(data, ['000333'], [event], days[30], days[50], 10, 5)
    no_cash = diagnostic.samples(data, ['000333'], [], days[30], days[50], 10, 5)
    assert before[0]['stocks'][0]['label_return_pct'] > no_cash[0]['stocks'][0]['label_return_pct']


def test_pool_demeaning_zero_volume_and_all_offset_counts_are_explicit():
    data, event, days = fixture()
    for symbol, scale in [('600519', 1.3), ('000001', .7)]:
        data[symbol] = copy.deepcopy(data['000333'])
        for b in data[symbol]['bars']:
            for key in ('open', 'high', 'low', 'close'):
                b[key] *= scale
    data['000333']['bars'][25]['volume'] = 0
    rows = diagnostic.samples(data, ['000333', '600519', '000001'], [event], days[25], days[65], 10, 5)
    assert all(sum(s['relative_to_pool_pct'] for s in r['stocks']) == pytest.approx(0) for r in rows)
    summary = diagnostic.summarize(rows, 5)
    assert summary['all_overlapping_dates']['zero_volume_stock_labels'] == 1
    assert len(summary['all_nonoverlap_offsets']) == 5
    assert sum(s['entry_dates'] for s in summary['all_nonoverlap_offsets']) == len(rows)
    assert summary['p_value'] is None and not summary['alpha_certified']
    assert [c['threshold'] for c in summary['all_overlapping_dates']['conditions']] == [0., .03]


def test_empty_or_tied_samples_never_claim_predictive_success():
    result = diagnostic.summarize([], 20)
    assert result['all_overlapping_dates']['mean_rank_ic'] is None
    assert all(s['mean_rank_ic'] is None for s in result['all_nonoverlap_offsets'])


@pytest.mark.parametrize('damage', ['lookback', 'horizon', 'calendar'])
def test_unregistered_parameters_and_missing_calendar_rejected(damage):
    data, event, days = fixture()
    if damage == 'calendar':
        data['000300']['bars'].pop(40)
    with pytest.raises(ValueError):
        diagnostic.samples(data, ['000333'], [event], days[25], days[65],
                           11 if damage == 'lookback' else 10, 6 if damage == 'horizon' else 5)


@pytest.mark.parametrize('damage', ['runner', 'path', 'overwrite'])
def test_cli_refuses_unregistered_or_outside_sources_and_overwrite(tmp_path, damage):
    script = Path(__file__).resolve().parents[3]/'scripts/quant-signal-diagnostics.py'
    paths = {k: tmp_path/(k+'.json') for k in ('experiments', 'events', 'reference', 'protocol', 'output')}
    paths['experiments'].write_text('[]'); paths['reference'].write_text('{}')
    paths['events'].write_text(json.dumps({'source_files': {'0'*64: '../outside.pdf'}}))
    paths['protocol'].write_text(json.dumps({'runner_sha256': '0'*64 if damage == 'runner'
        else hashlib.sha256(script.read_bytes()).hexdigest(), 'reference_sha256': hashlib.sha256(paths['reference'].read_bytes()).hexdigest()}))
    if damage == 'overwrite':
        paths['output'].write_text('preserved failed report')
    args = [sys.executable, str(script), '--evidence-dir', str(tmp_path)]
    for k, v in paths.items():
        args.extend(['--'+k, str(v)])
    result = subprocess.run(args, capture_output=True, text=True)
    assert result.returncode != 0
    if damage == 'overwrite':
        assert paths['output'].read_text() == 'preserved failed report'
    else:
        assert not paths['output'].exists()
