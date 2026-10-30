"""Frozen 2x2 exposure/cadence execution diagnostic on already seen history.

A v2 source archive is verified as bytes, never imported/executed. Financial
compatibility is explicit; v3 IDs/kernel metadata are not claimed byte-equal.
"""
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from . import quant_event_replay as engine, quant_event_training as training
from .quant_event_policy import POLICIES
from .quant_corporate_actions import CashDividend, timestamp, validate_evidence
from .quant_calendar import require_aligned
from .quant_dividend_book import INVESTOR
from .quant_research_analysis import diagnose
from .quant_research_engine import digest
from .quant_training_eligibility import _financial_equal

ARMS = list(POLICIES)
RULES = {
    'arms': copy.deepcopy(POLICIES), 'parameter_selection': 'frozen_reference_refit_expanded_no_new_fit',
    'market': 'official_H00300_prior_completed_close_strictly_above_last_60_completed_closes_mean',
    'cadence_anchor': 'first_original_evaluation_session_continuous_across_folds_and_restore',
    'mandatory_daily_exits': ['stop_loss', 'permanent_drawdown_pause', 'market_off'],
    'market_reentry': 'only_on_due_rebalance_session',
    'costs_limits_capital_risk': 'unchanged', 'pause_reset': 'never',
    'window': 'all_original_evaluation_sessions_no_old_holdout',
    'interpretation': 'seen_development_execution_diagnostic_no_selection_no_alpha',
    'nomination': 'none', 'original_gates': 'unchanged_not_replaced_by_this_control',
    'compatibility_ignored_metadata': ['root_state.kernel', 'root_state.rebalance_anchor',
                                     'root_state.market_prefix', 'root_state.plan',
                                     'book.last_input_digest', 'fill_identity_and_its_FIFO_references'],
}


def source_pins():
    names = ('quant_execution_controls.py', 'quant_event_policy.py', 'quant_benchmark.py',
             'quant_calendar.py', 'quant_training_eligibility.py')
    return {**training.source_pins(), **{n: hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest() for n in names}}


def verify_reference(reference, experiments, manifest, archive):
    parent = reference['protocol']
    # Old versions remain bound to their own bytes. Do NOT loosen the current
    # training validator to accept a prior execution kernel.
    if (parent['schema'] != 'event-training-basis-controls-v1' or parent['rules'] != training.RULES
            or parent['arms'] != list(training.ARMS) or parent['promotion_allowed'] is not False
            or parent['purpose'] != 'seen_development_method_control'
            or reference['protocol_digest'] != digest(parent)):
        raise ValueError('Invalid historical reference protocol')
    pins = parent['bindings']['source_pins']
    if set(archive) != set(pins) or any(hashlib.sha256(archive[n]).hexdigest() != sha for n, sha in pins.items()):
        raise ValueError('Historical source archive changed or incomplete')
    kernel_names = ('quant_event_replay.py', 'quant_dividend_book.py', 'quant_corporate_actions.py',
                    'quant_research_engine.py', 'ai_fund_ledger.py', 'ai_fund_policy.py')
    old_kernel = digest({n: pins[n] for n in kernel_names})
    if old_kernel != parent['bindings']['kernel']:
        raise ValueError('Historical kernel digest mismatch')
    now = training.bindings(experiments, manifest)
    if any(parent['bindings'][k] != now[k] for k in ('experiments', 'events', 'rules', 'assumptions')):
        raise ValueError('Historical reference inputs changed')
    rows = {r['experiment']: r for r in reference['results']}
    if len(rows) != len(reference['results']) or set(rows) != {e['id'] for e in experiments}:
        raise ValueError('Reference scope mismatch')
    for exp in experiments:
        row = rows[exp['id']]; result = row['cases']['refit_expanded']
        if result['version'] != 'event-replay-development-v2' or result['state']['kernel'] != old_kernel:
            raise ValueError('Reference is not a verified v2 result')
        plan = row['schedules']['refit_expanded']
        original = exp['result']['folds']
        if len(plan) != len(original) or any(
                any(a[k] != b[k] for k in ('train_end', 'test_start', 'test_end'))
                for a, b in zip(plan, original)):
            raise ValueError('Reference evaluation windows changed')
    return rows


def bindings(experiments, manifest, reference, archive, market_curve):
    verify_reference(reference, experiments, manifest, archive)
    return {'inputs': training.bindings(experiments, manifest), 'reference': digest(reference),
            'historical_source_pins': reference['protocol']['bindings']['source_pins'],
            'market_curve': digest(market_curve), 'rules': digest(RULES), 'source_pins': source_pins()}


def financial_compatibility(current, old):
    a, b = copy.deepcopy(current), copy.deepcopy(old)
    for r in (a, b):
        r['state'].pop('kernel', None)
        r['state'].pop('rebalance_anchor', None)
        r['state'].pop('market_prefix', None)
    return _financial_equal(a, b)


def validate_protocol(protocol, experiments, manifest, reference, archive, market_curve):
    if (protocol.get('schema') != 'execution-exposure-cadence-controls-v1'
            or protocol.get('rules') != RULES or protocol.get('arms') != ARMS
            or protocol.get('promotion_allowed') is not False):
        raise ValueError('Unregistered execution control or promotion request')
    if timestamp(protocol['registered_at']) > datetime.now(timezone.utc):
        raise ValueError('Future registration')
    if protocol.get('bindings') != bindings(experiments, manifest, reference, archive, market_curve):
        raise ValueError('Frozen input/source/rules changed')
    if set(protocol['restore_dates']) != {e['id'] for e in experiments}:
        raise ValueError('Every experiment requires one frozen restore date')
    for exp in experiments:
        require_aligned(exp['dataset'], expected_symbols=list(exp['dataset']))
        days = [b['date'] for b in exp['dataset'][exp['request']['symbols'][0]]['bars']]
        first, last = exp['result']['folds'][0]['test_start'], exp['result']['folds'][-1]['test_end']
        cut = protocol['restore_dates'][exp['id']]
        if not first < cut < last or cut not in days:
            raise ValueError('Restore point is outside evaluation sessions')


def run(protocol, experiments, manifest, reference, archive, market_curve, sources, *, progress=None):
    validate_protocol(protocol, experiments, manifest, reference, archive, market_curve)
    frozen = bindings(experiments, manifest, reference, archive, market_curve)
    all_events = [CashDividend(**e) for e in manifest['events']]
    validate_evidence(all_events, sources)
    parents = {r['experiment']: r for r in reference['results']}; results = []
    for exp in experiments:
        diagnose(exp)
        req, parent = exp['request'], parents[exp['id']]
        plan = parent['schedules']['refit_expanded']
        events = [e for e in all_events if e.symbol in req['symbols']]
        cases, restores = {}, {}
        for policy in ARMS:
            if progress: progress(exp['id'], policy)
            def replay(**kwargs):
                return engine.replay(exp['dataset'], req['symbols'], plan[0]['parameters'], events, sources,
                    investor=INVESTOR, mode='historical_reconstruction', capital=req['capital'],
                    start=plan[0]['test_start'], schedule=plan, policy=policy,
                    market_curve=market_curve if POLICIES[policy]['market_lookback'] else None, **kwargs)
            full = replay(end=plan[-1]['test_end'])
            if policy == 'daily' and not financial_compatibility(full, parent['cases']['refit_expanded']):
                raise ValueError('v3 default changed v2 financial behavior')
            cut = protocol['restore_dates'][exp['id']]
            prefix = replay(end=cut)
            suffix = replay(end=plan[-1]['test_end'], state=json.loads(json.dumps(prefix['state'])))
            checks = {k: prefix[k]+suffix[k] == full[k] for k in ('fills', 'curve', 'blocked', 'policy_audit')}
            checks['final_state'] = suffix['state'] == full['state']
            if not all(checks.values()): raise ValueError('JSON restore changed execution path')
            cases[policy] = full
            restores[policy] = {'date': cut, 'checks': checks, 'prefix_fills': len(prefix['fills']),
                                'prefix_held_shares': sum(l['qty'] for l in prefix['state']['book']['lots'])}
        results.append({'experiment': exp['id'], 'original_reproduced_exactly': True,
                        'default_v2_financial_compatibility': True, 'cases': cases, 'restores': restores})
    if frozen != bindings(experiments, manifest, reference, archive, market_curve):
        raise ValueError('Input/source changed during execution')
    return {'schema': protocol['schema'], 'protocol': protocol, 'protocol_digest': digest(protocol),
            'results': results, 'promotion_allowed': False, 'oos_validation': False, 'nomination': None,
            'commercial_ready': False,
            'limitations': ['seen history and tiny surviving pools', 'partial cash events and non-PIT market/prices',
                            'fixed reference training can select paused trials; this control does not repair selection',
                            'same original gates; no new holdout, no best arm nomination, no live migration']}
