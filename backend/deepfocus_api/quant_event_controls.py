"""Registered development controls on frozen original parameter paths.

No refitting, winner selection, OOS relabelling or promotion. The same actual
event execution engine is used for all arms; only the declared event input varies.
"""
from dataclasses import asdict
from datetime import datetime,timezone
import hashlib,json
from pathlib import Path
from . import quant_event_replay as engine
from .quant_corporate_actions import CashDividend,validate_evidence,timestamp
from .quant_dividend_book import INVESTOR
from .quant_research_engine import digest,ASSUMPTIONS
from .quant_research_analysis import diagnose

CASES=('no_events','original_two_events','expanded_events','expanded_as_recorded')
FILES=('quant_event_controls.py','quant_event_replay.py','quant_dividend_book.py',
       'quant_corporate_actions.py','quant_research_engine.py','quant_research_analysis.py',
       'ai_fund_ledger.py','ai_fund_policy.py','quant_calendar.py')


def source_pins():
    return {name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in FILES}


def schedule(experiment):
    return [{k:f[k] for k in ('train_end','test_start','test_end','parameters')} for f in experiment['result']['folds']]


def bindings(experiments,expanded,baseline):
    if len(baseline['events'])!=2:raise ValueError('The original two-event control must stay unchanged')
    return {'experiments':[{'id':e['id'],'result_digest':e['result_digest'],'request_digest':digest(e['request']),
              'dataset_digest':digest(e['dataset']),'schedule_digest':digest(schedule(e))} for e in experiments],
            'expanded_manifest_digest':digest(expanded),'baseline_manifest_digest':digest(baseline),
            'expanded_count':len(expanded['events']),'baseline_count':len(baseline['events']),
            'assumptions_digest':digest(ASSUMPTIONS),'engine_kernel':engine.KERNEL,'source_pins':source_pins()}


def validate_protocol(protocol,experiments,expanded,baseline):
    if protocol.get('schema')!='expanded-cash-event-controls-v1' or protocol.get('cases')!=list(CASES):raise ValueError('Unregistered control arms')
    if timestamp(protocol['registered_at'])>datetime.now(timezone.utc):raise ValueError('Registration timestamp is in the future')
    if protocol.get('bindings')!=bindings(experiments,expanded,baseline):raise ValueError('Frozen control inputs, assumptions or source changed')
    if protocol.get('purpose')!='seen_development_integration_no_selection' or protocol.get('promotion_allowed') is not False:
        raise ValueError('Controls cannot select or promote a strategy')
    if set(protocol.get('restore_at',{}))!={e['id'] for e in experiments}:raise ValueError('Missing registered restore checkpoints')
    for e in experiments:
        plan=schedule(e);cut=protocol['restore_at'][e['id']]
        if not plan[0]['test_start']<=cut<plan[-1]['test_end']:raise ValueError('Restore checkpoint outside the fixed evaluation period')
        if digest(e['result'])!=e['result_digest'] or digest(e['dataset'])!=e['result']['dataset_digest']:
            raise ValueError('Sealed original experiment modified')


def physical(fills):return [{k:v for k,v in f.items() if k!='id'} for f in fills]


def run_controls(protocol,experiments,expanded,baseline,sources,*,progress=None):
    validate_protocol(protocol,experiments,expanded,baseline)
    initial=bindings(experiments,expanded,baseline)
    full=[CashDividend(**e) for e in expanded['events']];old=[CashDividend(**e) for e in baseline['events']]
    validate_evidence(full,sources);validate_evidence(old,sources)
    # The original two events must be identical members, not similarly named
    # replacement facts that happen to produce a convenient control result.
    if any(asdict(e) not in [asdict(v) for v in full] for e in old):raise ValueError('Original two events were changed or removed')
    rows=[]
    for experiment in experiments:
        diagnose(experiment)  # Reproduce original exact result before new controls.
        req=experiment['request'];plan=schedule(experiment)
        selected=lambda ev:[e for e in ev if e.symbol in req['symbols']]
        common={'investor':INVESTOR,'capital':req['capital'],'start':plan[0]['test_start'],'schedule':plan}
        results={}
        for name,events,mode in [('no_events',[],'historical_reconstruction'),('original_two_events',selected(old),'historical_reconstruction'),
                                 ('expanded_events',selected(full),'historical_reconstruction'),('expanded_as_recorded',selected(full),'as_recorded')]:
            if progress:progress(experiment['id'],name)
            results[name]=engine.replay(experiment['dataset'],req['symbols'],plan[0]['parameters'],events,sources,
                                       mode=mode,end=plan[-1]['test_end'],**common)
            if results[name]['gate']['passed']:raise ValueError('A development arm unexpectedly became promotable')
        if (results['no_events']['curve']!=results['expanded_as_recorded']['curve'] or
            physical(results['no_events']['fills'])!=physical(results['expanded_as_recorded']['fills'])):
            raise ValueError('Late collected evidence leaked into the as-recorded control')
        cut=protocol['restore_at'][experiment['id']]
        prefix=engine.replay(experiment['dataset'],req['symbols'],plan[0]['parameters'],selected(full),sources,
                             mode='historical_reconstruction',end=cut,**common)
        resumed=engine.replay(experiment['dataset'],req['symbols'],plan[0]['parameters'],selected(full),sources,
                              mode='historical_reconstruction',end=plan[-1]['test_end'],
                              state=json.loads(json.dumps(prefix['state'])),**common)
        whole=results['expanded_events']
        if prefix['fills']+resumed['fills']!=whole['fills'] or prefix['curve']+resumed['curve']!=whole['curve'] or resumed['state']!=whole['state']:
            raise ValueError('JSON restored event replay differs from the full run')
        rows.append({'experiment':experiment['id'],'original_result_digest':experiment['result_digest'],
                     'original_reproduced_exactly':True,'cases':results,'as_recorded_control_equal':True,
                     'restore':{'checkpoint':cut,'fills_curve_final_state_equal':True},'parameters_refit':False})
    if initial!=bindings(experiments,expanded,baseline):raise ValueError('Control inputs/source changed during replay')
    return {'schema':protocol['schema'],'registered_protocol':protocol,'protocol_digest':digest(protocol),
            'created_at':datetime.now(timezone.utc).isoformat(),'results':rows,'oos_validation':False,
            'promotion_allowed':False,'commercial_ready':False,'data_scope':'partial_cash_events_on_seen_history',
            'limitation':'original raw-price trained parameter schedule frozen; no total-return refitting or complete benchmark'}
