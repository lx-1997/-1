"""A single preregistered paused-training exclusion, with explicit no-fit failure.

Reuse the frozen six-trial training table, verify its parent protocol and selected
boundary fit, and replay financial paths through the same event engine. This is
a diagnostic of seen history, not a new OOS result or live strategy default.
"""
import copy
from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path

from . import quant_event_training as training
from . import quant_event_replay as engine
from .quant_corporate_actions import CashDividend, timestamp, validate_evidence
from .quant_dividend_book import INVESTOR
from .quant_research_analysis import diagnose
from .quant_research_engine import CANDIDATES, digest

RULES = {
    'exclude': 'training_risk_paused_only',
    'ranking': 'unchanged_return_plus_drawdown_then_shorter_lookback_lower_threshold',
    'zero_trade_trials': 'retained_if_not_paused',
    'no_eligible': 'stop_before_undefined_fold_no_fallback_no_window_skip',
    'incomplete_path': 'prefix_diagnostic_only_no_full_period_metrics',
    'verification_fit': 'first_no_eligible_else_first_excluded_winner_else_first_fold',
    'holdout': 'not_reassessed', 'nomination': 'none',
}


def source_pins():
    return {**training.source_pins(), 'quant_training_eligibility.py':
            hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def bindings(parent, experiments, manifest):
    return {'parent_report': digest(parent), 'parent_protocol': digest(parent['protocol']),
            'inputs': training.bindings(experiments, manifest), 'rules': digest(RULES),
            'source_pins': source_pins()}


def validate_protocol(protocol, parent, experiments, manifest):
    training.validate_protocol(parent['protocol'], experiments, manifest)
    if (protocol.get('schema') != 'paused-training-exclusion-v1'
            or protocol.get('rules') != RULES or protocol.get('promotion_allowed') is not False
            or protocol.get('purpose') != 'seen_development_single_hypothesis'):
        raise ValueError('Unregistered eligibility rule or promotion request')
    if timestamp(protocol['registered_at']) > datetime.now(timezone.utc):
        raise ValueError('Future registration')
    if protocol.get('bindings') != bindings(parent, experiments, manifest):
        raise ValueError('Frozen parent, inputs or source changed')
    ids = [r['experiment'] for r in parent['results']]
    if len(ids) != len(set(ids)) or set(ids) != {e['id'] for e in experiments}:
        raise ValueError('Missing, extra or duplicate parent experiment')
    if parent.get('protocol_digest') != digest(parent['protocol']):
        raise ValueError('Invalid parent protocol digest')


def _rank(trial):
    return trial['score'], -trial['parameters']['lookback'], -trial['parameters']['threshold']


def select_plan(fits, schedule):
    if not fits or len(fits) != len(schedule):
        raise ValueError('Incomplete training table')
    plan, decisions, stopped = [], [], False
    for i, (fit, fold) in enumerate(zip(fits, schedule)):
        trials = fit['trials']
        if (fit['train_end'] != fold['train_end'] or len(trials) != len(CANDIDATES)
                or sorted(digest(t['parameters']) for t in trials)
                != sorted(digest(p) for p in CANDIDATES)):
            raise ValueError('Incomplete or altered original candidate set')
        for t in trials:
            if (type(t['risk_paused']) is not bool or type(t['score']) not in (int, float)
                    or not math.isfinite(t['score']) or t['train_end'] != fold['train_end']
                    or t['score'] != t['metrics']['return_pct'] + t['metrics']['max_drawdown_pct']):
                raise ValueError('Invalid frozen training trial')
        baseline = max(trials, key=_rank)
        if baseline['parameters'] != fit['parameters'] or fit['parameters'] != fold['parameters']:
            raise ValueError('Parent ranking or schedule does not match its trials')
        eligible = [t for t in trials if not t['risk_paused']]
        selected = max(eligible, key=_rank) if eligible else None
        reachable = not stopped
        decisions.append({'fold': i+1, 'train_end': fold['train_end'],
                          'test_start': fold['test_start'], 'test_end': fold['test_end'],
                          'baseline': dict(baseline['parameters']),
                          'baseline_training_paused': baseline['risk_paused'],
                          'selected': dict(selected['parameters']) if selected else None,
                          'eligible_count': len(eligible), 'execution_reachable': reachable,
                          'rejections': [{'parameters': dict(t['parameters']),
                                          'reason': 'training_drawdown_pause'}
                                         for t in trials if t['risk_paused']]})
        if not selected:
            stopped = True
        if not stopped:
            plan.append({**fold, 'parameters': dict(selected['parameters'])})
    return plan, decisions


def _financial_equal(a, b):
    # Order IDs and the state plan digest identify the whole frozen schedule;
    # changing a future choice changes those identifiers, not historical cash.
    def canonical(result):
        ids = {f['id']: f'fill-{i}' for i, f in enumerate(result['fills'])}
        def normalize(value):
            if isinstance(value, dict):
                return {ids.get(k, k): normalize(v) for k, v in value.items()}
            if isinstance(value, list):
                return [normalize(v) for v in value]
            return ids.get(value, value) if isinstance(value, str) else value
        state = copy.deepcopy(result['state'])
        state.pop('plan')
        stored = state['book']['fill_ids']
        if stored != {f['id']: digest(f) for f in result['fills']}:
            raise ValueError('Result contains inconsistent fill identities')
        state['book']['fill_ids'] = {ids[f['id']]: digest(normalize(f)) for f in result['fills']}
        # This idempotency fingerprint includes the original fill identifiers.
        # Economic fields, FIFO links, all taxes and cash remain compared.
        state['book'].pop('last_input_digest', None)
        return normalize({'fills': result['fills'], 'state': state,
                          'curve': result['curve'], 'blocked': result['blocked']})
    return canonical(a) == canonical(b)


def run_control(protocol, parent, experiments, manifest, sources, *, progress=None):
    validate_protocol(protocol, parent, experiments, manifest)
    frozen = bindings(parent, experiments, manifest)
    events = [CashDividend(**e) for e in manifest['events']]
    validate_evidence(events, sources)
    parents = {r['experiment']: r for r in parent['results']}
    output = []
    for exp in experiments:
        diagnose(exp)
        row, req = parents[exp['id']], exp['request']
        fits, original = row['training']['refit_expanded'], row['schedules']['refit_expanded']
        plan, decisions = select_plan(fits, original)
        # Recompute the most informative frozen fit through the actual trainer.
        boundary = next((i for i, d in enumerate(decisions) if not d['eligible_count']), None)
        if boundary is None:
            boundary = next((i for i, d in enumerate(decisions) if d['baseline_training_paused']), 0)
        selected_events = [e for e in events if e.symbol in req['symbols']]
        if progress:
            progress(exp['id'], 'verify_boundary_training', boundary+1)
        reproduced = training.select(exp['dataset'], req['symbols'], selected_events, sources,
                                     fits[boundary]['train_end'], req['capital'])
        if reproduced != fits[boundary]:
            raise ValueError('Frozen boundary training could not be reproduced')
        def replay(schedule, end):
            return engine.replay(exp['dataset'], req['symbols'], schedule[0]['parameters'],
                                 selected_events, sources, investor=INVESTOR,
                                 mode='historical_reconstruction', capital=req['capital'],
                                 start=original[0]['test_start'], end=end, schedule=schedule)
        if progress:
            progress(exp['id'], 'replay_controls', len(plan))
        baseline = replay(original, original[-1]['test_end'])
        if baseline != row['cases']['refit_expanded']:
            raise ValueError('Frozen baseline replay changed')
        result = replay(plan, plan[-1]['test_end']) if plan else None
        full_period = len(plan) == len(original)
        paired = replay(original, plan[-1]['test_end']) if result and not full_period else baseline
        rejection = next((d for d in decisions if not d['eligible_count']), None)
        output.append({'experiment': exp['id'], 'original_reproduced_exactly': True,
                       'baseline_reproduced_exactly': True, 'boundary_fit_reproduced': boundary+1,
                       'decisions': decisions, 'schedule': plan, 'no_eligible': rejection,
                       'full_period_defined': full_period,
                       'full_period_result': result if full_period else None,
                       'prefix_diagnostic': result if not full_period else None,
                       'paired_same_period_baseline': paired if result else None,
                       'financial_paths_equal_over_defined_period':
                           _financial_equal(result, paired) if result else None,
                       'full_period_baseline': baseline,
                       'gate': {'passed': False, 'promotion_allowed': False,
                                'reasons': ['seen development data', 'no challenger nomination']
                                + ([] if full_period else ['no eligible training candidate'])}})
    if frozen != bindings(parent, experiments, manifest):
        raise ValueError('Source or inputs changed during control')
    return {'schema': protocol['schema'], 'protocol': copy.deepcopy(protocol),
            'protocol_digest': digest(protocol), 'results': output,
            'created_at': datetime.now(timezone.utc).isoformat(),
            'promotion_allowed': False, 'oos_validation': False, 'commercial_ready': False}
