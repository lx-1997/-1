"""Preregistered parameter-family/selector diagnostic, never a strategy gate.

Continuous fixed-parameter accounts retain permanent risk state. Separately
capitalized fold probes are counterfactual labels, NEVER linked into a curve.
"""
import copy
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from statistics import mean

from . import quant_event_replay as engine, quant_event_training as training
from .quant_calendar import require_aligned
from .quant_corporate_actions import CashDividend, timestamp, validate_evidence
from .quant_dividend_book import INVESTOR, BOOK_KERNEL
from .quant_execution_controls import verify_reference, financial_compatibility
from .quant_research_analysis import diagnose
from .quant_research_engine import CANDIDATES, digest
from .quant_signal_diagnostics import rank_ic, ranks
from .quant_training_eligibility import select_plan

RULES = {
    'candidates': list(CANDIDATES),
    'continuous_arms': 'frozen_refit_and_all_six_fixed_parameters',
    'continuous_account': 'original_capital_original_535_or_declared_fold_sessions_no_risk_reset',
    'fold_probe': 'fresh_cash_each_original_fold_diagnostic_only_never_stitched',
    'training': 'reuse_all_frozen_six_trial_rows_no_new_fit_or_ranking',
    'training_score': 'original_return_pct_plus_max_drawdown_pct',
    'labels': 'same_shared_event_engine_net_return_each_fresh_fold',
    'rank_ic': 'six_parameters_spearman_average_ties_round_12_decimals',
    'summary_windows': ['all_original_folds', 'folds_entered_before_reference_permanent_pause'],
    'oracle': 'hindsight_diagnostic_only_not_executable_not_alpha',
    'missing_constant_correlation': 'null_not_zero_no_significance_claim',
    'holdout': 'not_reassessed', 'costs_limits_risk': 'unchanged',
    'nomination': 'none_no_best_historical_arm_selection',
    'decision': 'report_family_failures_and_ranking_transfer_separately_no_threshold_search',
    'baseline_book_version_comparison': 'verify_old_and_current_book_source_identity_then_compare_all_financial_fields',
}


def source_pins():
    from .quant_execution_controls import source_pins as inherited
    return {**inherited(), **{n: hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest()
            for n in ('quant_selection_diagnostics.py', 'quant_signal_diagnostics.py')}}


def bindings(experiments, manifest, reference, archive):
    verify_reference(reference, experiments, manifest, archive)
    return {'inputs': training.bindings(experiments, manifest), 'reference': digest(reference),
            'source_pins': source_pins(), 'rules': digest(RULES)}


def validate_protocol(protocol, experiments, manifest, reference, archive):
    if (protocol.get('schema') != 'parameter-family-selector-diagnostic-v1'
            or protocol.get('rules') != RULES
            or protocol.get('purpose') != 'seen_development_diagnostic_no_selection'
            or protocol.get('promotion_allowed') is not False):
        raise ValueError('Unregistered selector diagnostic or promotion request')
    if timestamp(protocol['registered_at']) > datetime.now(timezone.utc):
        raise ValueError('Future registration')
    if protocol.get('bindings') != bindings(experiments, manifest, reference, archive):
        raise ValueError('Registered input/source/rule changed')
    parents = {r['experiment']: r for r in reference['results']}
    for exp in experiments:
        require_aligned(exp['dataset'], expected_symbols=list(exp['dataset']))
        row = parents[exp['id']];plan = row['schedules']['refit_expanded'];fits = row['training']['refit_expanded']
        # Validate the complete original ranking table; do not adopt the strict
        # eligibility selection or silently remove its all-paused folds.
        select_plan(fits, plan)
        for fit in fits:
            prefix = {s: {**p, 'bars': [b for b in p['bars'] if b['date']<=fit['train_end']]}
                      for s, p in exp['dataset'].items()}
            if fit['prefix_digest'] != digest(prefix):
                raise ValueError('Frozen training prefix does not match cutoff')


def summarize(replay):
    return {'metrics': copy.deepcopy(replay['metrics']), 'fills': len(replay['fills']),
            'fees': sum(f['fee'] for f in replay['fills']), 'risk_paused': replay['state']['paused'],
            'first_pause': next((r['date'] for r in replay['curve'] if r['risk_paused']), None),
            'sessions': len(replay['curve']), 'replay_digest': digest(replay)}


def baseline_equal(current, old, historical_pins):
    names=('quant_dividend_book.py','quant_corporate_actions.py','quant_research_engine.py',
           'ai_fund_ledger.py','ai_fund_policy.py')
    historical_book=digest({n:historical_pins[n] for n in names})
    if current['state']['book']['kernel']!=BOOK_KERNEL or old['state']['book']['kernel']!=historical_book:
        raise ValueError('Unverified historical/current book source identity')
    a,b=copy.deepcopy(current),copy.deepcopy(old)
    # This is a diagnostic comparison across verified source versions, NEVER
    # acceptance of an old book by the current live/restore execution engine.
    for r in (a,b):r['state']['book']['kernel']='verified_source_version_metadata'
    return financial_compatibility(a,b)


def fold_diagnostic(fit, probes):
    if (len(probes)!=len(CANDIDATES)
            or [p['parameters'] for p in probes]!=list(CANDIDATES)):
        raise ValueError('Complete ordered original candidate probes required')
    trials={digest(t['parameters']):t for t in fit['trials']}
    if len(trials)!=len(CANDIDATES) or set(trials)!={digest(p) for p in CANDIDATES}:
        raise ValueError('Incomplete original training trials')
    scores=[trials[digest(p['parameters'])]['score'] for p in probes]
    labels=[p['metrics']['return_pct'] for p in probes]
    # ranks() also enforces finite numeric inputs, retaining average tied ranks.
    performance_ranks=ranks([-v for v in labels]);ranks(scores)
    chosen=next(i for i,p in enumerate(probes) if p['parameters']==fit['parameters'])
    ordered=sorted(scores,reverse=True)
    return {'train_end':fit['train_end'],'selected':copy.deepcopy(fit['parameters']),
            'training_top_two_score_gap_pp':ordered[0]-ordered[1],
            'selected_training_paused':trials[digest(fit['parameters'])]['risk_paused'],
            'train_score_to_next_fold_net_return_rank_ic':rank_ic(scores,labels),
            'selected_next_fold_return_pct':labels[chosen],
            'selected_next_fold_average_rank':performance_ranks[chosen],
            'all_candidate_mean_next_fold_return_pct':mean(labels),
            'selected_minus_candidate_mean_pp':labels[chosen]-mean(labels),
            'hindsight_best_minus_selected_pp':max(labels)-labels[chosen],
            'hindsight_best_return_pct':max(labels),
            'unpaused_probe_count':sum(not p['risk_paused'] for p in probes),
            'probes':copy.deepcopy(probes)}


def aggregate(rows):
    correlations=[r['train_score_to_next_fold_net_return_rank_ic'] for r in rows
                  if r['train_score_to_next_fold_net_return_rank_ic'] is not None]
    fields=('selected_next_fold_average_rank','selected_minus_candidate_mean_pp',
            'hindsight_best_minus_selected_pp','training_top_two_score_gap_pp')
    return {'folds':len(rows),'defined_rank_correlations':len(correlations),
            'mean_rank_ic':mean(correlations) if correlations else None,
            'positive_rank_ic_folds':sum(x>0 for x in correlations),
            'selected_training_paused_folds':sum(r['selected_training_paused'] for r in rows),
            **{'mean_'+k:mean(r[k] for r in rows) if rows else None for k in fields},
            'not_independent_p_value':None,'linked_return_curve':None}


def run(protocol, experiments, manifest, reference, archive, sources, *, progress=None):
    validate_protocol(protocol, experiments, manifest, reference, archive)
    initial=bindings(experiments,manifest,reference,archive)
    all_events=[CashDividend(**e) for e in manifest['events']];validate_evidence(all_events,sources)
    parents={r['experiment']:r for r in reference['results']};output=[]
    for exp in experiments:
        diagnose(exp)
        parent=parents[exp['id']];req=exp['request'];plan=parent['schedules']['refit_expanded']
        fits=parent['training']['refit_expanded'];events=[e for e in all_events if e.symbol in req['symbols']]
        # Physical cutoff: no future fold or old holdout is given to a probe.
        def replay(params,start,end,schedule=None):
            prefix={s:{**p,'bars':[b for b in p['bars'] if b['date']<=end]} for s,p in exp['dataset'].items()}
            return engine.replay(prefix,req['symbols'],params,events,sources,investor=INVESTOR,
                                 mode='historical_reconstruction',capital=req['capital'],
                                 start=start,end=end,schedule=schedule)
        baseline=replay(plan[0]['parameters'],plan[0]['test_start'],plan[-1]['test_end'],plan)
        if not baseline_equal(baseline,parent['cases']['refit_expanded'],reference['protocol']['bindings']['source_pins']):
            raise ValueError('Current baseline financial path changed')
        continuous=[]
        for params in CANDIDATES:
            if progress:progress(exp['id'],'continuous',params)
            result=replay(params,plan[0]['test_start'],plan[-1]['test_end'])
            continuous.append({'parameters':dict(params),'summary':summarize(result),'result':result})
        folds=[];first_pause=summarize(baseline)['first_pause']
        for i,(fold,fit) in enumerate(zip(plan,fits),1):
            probes=[]
            for params in CANDIDATES:
                if progress:progress(exp['id'],'fresh_fold_'+str(i),params)
                r=replay(params,fold['test_start'],fold['test_end'])
                probes.append({'parameters':dict(params),**summarize(r)})
            row=fold_diagnostic(fit,probes)
            row.update(fold=i,test_start=fold['test_start'],test_end=fold['test_end'],
                       reference_not_paused_at_entry=first_pause is None or fold['test_start']<=first_pause)
            folds.append(row)
        output.append({'experiment':exp['id'],'original_reproduced_exactly':True,
                       'baseline':{'summary':summarize(baseline),'result':baseline},
                       'continuous_fixed':continuous,'fresh_fold_diagnostics':folds,
                       'summaries':{'all_original_folds':aggregate(folds),
                           'folds_entered_before_reference_permanent_pause':aggregate([r for r in folds if r['reference_not_paused_at_entry']])}})
    if initial!=bindings(experiments,manifest,reference,archive):
        raise ValueError('Inputs/source changed during diagnostic')
    return {'schema':protocol['schema'],'protocol':protocol,'protocol_digest':digest(protocol),
            'results':output,'nomination':None,'promotion_allowed':False,'oos_validation':False,
            'commercial_ready':False,'limitations':['seen surviving pools and partial cash history; not PIT',
                'fresh fold accounts are counterfactuals with no carry or earlier risk history',
                'no linked fold-oracle performance; hindsight cannot select an executable strategy',
                'six related candidates and thirteen sequential folds are not independent samples',
                'no new objective, parameter, candidate nomination or original gate change']}
