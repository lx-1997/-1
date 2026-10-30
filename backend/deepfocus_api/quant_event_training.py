"""Registered return-basis controls on seen history, never a promotion path.

Refit only the original six candidates, with the original expanding training
window and score. Each fit receives a physically truncated price prefix. The
event replay supplies both training and execution accounting without a second
implementation of fills, dividend tax, or risk.
"""
from datetime import datetime, timezone
import hashlib
from pathlib import Path

from . import quant_event_replay as engine
from .quant_corporate_actions import CashDividend, timestamp, validate_evidence
from .quant_dividend_book import INVESTOR
from .quant_event_controls import schedule, source_pins as control_pins
from .quant_research_analysis import diagnose
from .quant_research_engine import ASSUMPTIONS, CANDIDATES, digest, validate_dataset

ARMS = ('frozen_no_events', 'frozen_expanded', 'refit_no_events', 'refit_expanded')
RULES = {
    'candidates': list(CANDIDATES),
    'window': 'expanding_from_original_snapshot',
    'score': 'return_pct_plus_max_drawdown_pct',
    'tie_break': 'shorter_lookback_then_lower_threshold',
    'gap_days': 5,
    'training_account': 'fresh_cash_per_trial_original_capital',
    'evaluation_account': 'continuous_across_original_folds_no_risk_reset',
    'mode': 'historical_reconstruction',
    'holdout': 'excluded_not_reassessed',
    'nomination': 'none_method_control_only',
}


def source_pins():
    return {**control_pins(), 'quant_event_training.py':
            hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def bindings(experiments, manifest):
    if not experiments or len({e['id'] for e in experiments}) != len(experiments):
        raise ValueError('A nonempty unique experiment scope is required')
    return {
        'experiments': [{'id': e['id'], 'request': digest(e['request']),
                         'dataset': digest(e['dataset']), 'result': e['result_digest'],
                         'schedule': digest(schedule(e))} for e in experiments],
        'events': digest(manifest), 'rules': digest(RULES),
        'assumptions': digest(ASSUMPTIONS), 'kernel': engine.KERNEL,
        'source_pins': source_pins(),
    }


def validate_protocol(protocol, experiments, manifest):
    if (protocol.get('schema') != 'event-training-basis-controls-v1'
            or protocol.get('arms') != list(ARMS)
            or protocol.get('rules') != RULES
            or protocol.get('purpose') != 'seen_development_method_control'
            or protocol.get('promotion_allowed') is not False):
        raise ValueError('Unregistered training control or promotion request')
    if timestamp(protocol['registered_at']) > datetime.now(timezone.utc):
        raise ValueError('Registration cannot be in the future')
    if protocol.get('bindings') != bindings(experiments, manifest):
        raise ValueError('Registered inputs or source changed')
    for exp in experiments:
        if (digest(exp['result']) != exp['result_digest']
                or digest(exp['dataset']) != exp['result']['dataset_digest']):
            raise ValueError('Sealed experiment was modified')
        dates = validate_dataset(exp['dataset'])
        previous_end = ''
        plan = schedule(exp)
        if not plan:
            raise ValueError('Missing original fold schedule')
        for fold in plan:
            a, b, c = (fold[k] for k in ('train_end', 'test_start', 'test_end'))
            if (not previous_end < b <= c or not a < b
                    or any(d not in dates for d in (a, b, c))
                    or sum(a < d < b for d in dates) != RULES['gap_days']):
                raise ValueError('Original training/evaluation dates or gap changed')
            if previous_end and dates.index(b) != dates.index(previous_end) + 1:
                raise ValueError('Evaluation must retain all original fold sessions')
            previous_end = c


def select(dataset, symbols, events, sources, train_end, capital):
    prefix = {s: {**p, 'bars': [dict(b) for b in p['bars'] if b['date'] <= train_end]}
              for s, p in dataset.items()}
    dates = validate_dataset(prefix)
    if not dates or dates[-1] != train_end:
        raise ValueError('Training cutoff must be a common observed session')
    trials = []
    for params in CANDIDATES:
        result = engine.replay(prefix, symbols, params, events, sources,
                               investor=INVESTOR, mode=RULES['mode'],
                               end=train_end, capital=capital)
        m = result['metrics']
        trials.append({'parameters': dict(params), 'train_end': train_end,
                       'metrics': m, 'score': m['return_pct'] + m['max_drawdown_pct'],
                       'fills': len(result['fills']), 'risk_paused': result['state']['paused'],
                       'fees': sum(f['fee'] for f in result['fills']),
                       'replay_digest': digest(result)})
    winner = max(trials, key=lambda t: (t['score'], -t['parameters']['lookback'],
                                        -t['parameters']['threshold']))
    return {'parameters': dict(winner['parameters']), 'trials': trials,
            'prefix_digest': digest(prefix), 'train_end': train_end}


def run_controls(protocol, experiments, manifest, sources, *, progress=None):
    validate_protocol(protocol, experiments, manifest)
    initial = bindings(experiments, manifest)
    all_events = [CashDividend(**v) for v in manifest['events']]
    validate_evidence(all_events, sources)
    rows = []
    for exp in experiments:
        diagnose(exp)
        req, original = exp['request'], schedule(exp)
        events = [e for e in all_events if e.symbol in req['symbols']]
        plans, fits = {}, {}
        for arm in ARMS:
            plan, fitting = [], []
            selected_events = events if arm.endswith('expanded') else []
            for i, fold in enumerate(original):
                chosen = fold['parameters']
                if arm.startswith('refit'):
                    if progress:
                        progress(exp['id'], arm, i + 1, len(original))
                    fit = select(exp['dataset'], req['symbols'], selected_events, sources,
                                 fold['train_end'], req['capital'])
                    fitting.append(fit)
                    chosen = fit['parameters']
                plan.append({**fold, 'parameters': dict(chosen)})
            plans[arm], fits[arm] = plan, fitting
        results = {}
        for arm in ARMS:
            if progress:
                progress(exp['id'], arm, len(original), len(original))
            results[arm] = engine.replay(
                exp['dataset'], req['symbols'], plans[arm][0]['parameters'],
                events if arm.endswith('expanded') else [], sources,
                investor=INVESTOR, mode=RULES['mode'], capital=req['capital'],
                start=original[0]['test_start'], end=original[-1]['test_end'],
                schedule=plans[arm])
            if results[arm]['gate']['passed']:
                raise ValueError('A seen-history control cannot be promoted')
        rows.append({'experiment': exp['id'], 'original_reproduced_exactly': True,
                     'schedules': plans, 'training': fits, 'cases': results,
                     'changed_folds': {arm: sum(a['parameters'] != b['parameters']
                                               for a, b in zip(original, plans[arm]))
                                       for arm in ARMS}})
    if initial != bindings(experiments, manifest):
        raise ValueError('Inputs or source changed while computing')
    return {'schema': protocol['schema'], 'protocol': protocol,
            'protocol_digest': digest(protocol), 'results': rows,
            'created_at': datetime.now(timezone.utc).isoformat(),
            'promotion_allowed': False, 'oos_validation': False, 'commercial_ready': False,
            'limitations': ['seen development history', 'partial cash-event coverage',
                            'current surviving user-selected universe',
                            'holdout not reassessed; no challenger nominated',
                            'current captured historical data, not point-in-time prices']}
