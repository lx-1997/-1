"""Selector labels must remain distinct from a continuous investable account."""
import copy
from datetime import datetime,timezone
import hashlib,json
from pathlib import Path
import subprocess,sys

import pytest
from deepfocus_api import quant_selection_diagnostics as diagnostic,quant_event_training as training
from deepfocus_api import quant_event_replay as engine
from deepfocus_api.quant_research_engine import CANDIDATES,digest
from deepfocus_api.quant_dividend_book import INVESTOR
from deepfocus_api.tests.test_quant_execution_controls import registered_fixture


def inputs():
    _,exps,manifest,parent,archive,_=registered_fixture()
    exp=exps[0];row=parent['results'][0];plan=row['schedules']['refit_expanded'];fold=plan[0]
    fit=training.select(exp['dataset'],exp['request']['symbols'],[],{},fold['train_end'],1000000)
    fold['parameters']=fit['parameters'];exp['result']['folds'][0]['parameters']=fit['parameters']
    exp['result_digest']=digest(exp['result'])
    row['training']={'refit_expanded':[fit]}
    r=engine.replay(exp['dataset'],exp['request']['symbols'],fold['parameters'],[],{},investor=INVESTOR,
                    mode='historical_reconstruction',start=fold['test_start'],end=fold['test_end'],schedule=plan)
    r['version']='event-replay-development-v2';r['state']['kernel']=parent['protocol']['bindings']['kernel']
    row['cases']['refit_expanded']=r
    current=training.bindings(exps,manifest)
    for k in ('experiments','events','rules','assumptions'):parent['protocol']['bindings'][k]=current[k]
    parent['protocol_digest']=digest(parent['protocol'])
    protocol={'schema':'parameter-family-selector-diagnostic-v1','rules':copy.deepcopy(diagnostic.RULES),
              'purpose':'seen_development_diagnostic_no_selection','promotion_allowed':False,
              'registered_at':datetime.now(timezone.utc).isoformat(),
              'bindings':diagnostic.bindings(exps,manifest,parent,archive)}
    return protocol,exps,manifest,parent,archive


def test_tied_ranks_oracle_regret_and_constant_ic_are_not_linked_performance():
    scores=[6,5,4,3,2,1];returns=[0,1,2,3,4,5]
    fit={'train_end':'2024-01-01','parameters':CANDIDATES[0],
         'trials':[{'parameters':p,'score':s,'risk_paused':False} for p,s in zip(CANDIDATES,scores)]}
    probes=[{'parameters':p,'metrics':{'return_pct':r},'risk_paused':False} for p,r in zip(CANDIDATES,returns)]
    row=diagnostic.fold_diagnostic(fit,probes)
    assert row['train_score_to_next_fold_net_return_rank_ic']==-1
    assert row['selected_next_fold_average_rank']==6 and row['hindsight_best_minus_selected_pp']==5
    for p in probes:p['metrics']['return_pct']=1
    row=diagnostic.fold_diagnostic(fit,probes)
    assert row['train_score_to_next_fold_net_return_rank_ic'] is None
    assert row['selected_next_fold_average_rank']==3.5
    combined=diagnostic.aggregate([row]);assert combined['mean_rank_ic'] is None and combined['linked_return_curve'] is None
    assert diagnostic.aggregate([])['mean_selected_next_fold_average_rank'] is None


@pytest.mark.parametrize('damage',['missing','duplicate','nan','bool'])
def test_missing_or_nonfinite_trials_cannot_enter_ranking(damage):
    fit={'train_end':'2024-01-01','parameters':CANDIDATES[0],
         'trials':[{'parameters':p,'score':1,'risk_paused':False} for p in CANDIDATES]}
    probes=[{'parameters':p,'metrics':{'return_pct':1},'risk_paused':False} for p in CANDIDATES]
    if damage=='missing':probes.pop()
    elif damage=='duplicate':probes[-1]=copy.deepcopy(probes[0])
    elif damage=='nan':probes[0]['metrics']['return_pct']=float('nan')
    else:fit['trials'][0]['score']=True
    with pytest.raises(ValueError):diagnostic.fold_diagnostic(fit,probes)


@pytest.mark.parametrize('damage',['rule','source','archive','reference','future','promotion','missing_fit','prefix','score'])
def test_registration_and_frozen_training_rows_are_enforced(damage):
    args=list(inputs());protocol,exps,manifest,parent,archive=args
    diagnostic.validate_protocol(*args)
    if damage=='rule':protocol['rules']['nomination']='winner'
    elif damage=='source':protocol['bindings']['source_pins']['quant_selection_diagnostics.py']='0'*64
    elif damage=='archive':archive['quant_event_replay.py']+=b'changed'
    elif damage=='reference':parent['results'][0]['cases']['refit_expanded']['state']['cash']=0
    elif damage=='future':protocol['registered_at']='2099-01-01T00:00:00+00:00'
    elif damage=='promotion':protocol['promotion_allowed']=True
    else:
        fit=parent['results'][0]['training']['refit_expanded'][0]
        if damage=='missing_fit':fit['trials'].pop()
        elif damage=='prefix':fit['prefix_digest']='0'*64
        else:fit['trials'][0]['score']+=1
        # Even a newly bound bad training row is rejected structurally.
        protocol['bindings']=diagnostic.bindings(exps,manifest,parent,archive)
    with pytest.raises(ValueError):diagnostic.validate_protocol(*args)


def test_complete_pipeline_reuses_engine_and_physically_cuts_probe_data(monkeypatch):
    args=inputs();protocol,exps,manifest,parent,archive=args
    monkeypatch.setattr(diagnostic,'diagnose',lambda exp:None) # Real sealed reproduction covered by retained-data run.
    actual=engine.replay;seen=[]
    def inspect(data,symbols,params,events,sources,**kwargs):
        assert all(b['date']<=kwargs['end'] for p in data.values() for b in p['bars'])
        assert kwargs.get('state') is None
        result=actual(data,symbols,params,events,sources,**kwargs)
        seen.append((kwargs,result));return result
    monkeypatch.setattr(engine,'replay',inspect)
    before=copy.deepcopy(args);report=diagnostic.run(*args,{})
    assert args==before and len(seen)==13 # baseline + six continuous + six fresh probes
    row=report['results'][0];assert len(row['continuous_fixed'])==6
    assert any(r['summary']['fills'] for r in row['continuous_fixed'])
    assert all(r['summary']['sessions']==row['baseline']['summary']['sessions'] for r in row['continuous_fixed'])
    assert not report['oos_validation'] and not report['promotion_allowed'] and report['nomination'] is None
    assert row['summaries']['all_original_folds']['linked_return_curve'] is None


@pytest.mark.parametrize('damage',['cash','tax','risk','fee','book_version'])
def test_version_comparison_does_not_ignore_financial_or_unverified_version_changes(damage):
    _,_,_,parent,_=inputs();old=parent['results'][0]['cases']['refit_expanded'];current=copy.deepcopy(old)
    pins=parent['protocol']['bindings']['source_pins']
    assert diagnostic.baseline_equal(current,old,pins)
    if damage=='cash':current['state']['book']['cash']='1.00'
    elif damage=='tax':current['state']['book']['tax_assessed']='10.00'
    elif damage=='risk':current['state']['paused']=not current['state']['paused']
    elif damage=='fee':current['fills'][0]['fee']+=1
    else:
        current['state']['book']['kernel']='unverified'
        with pytest.raises(ValueError):diagnostic.baseline_equal(current,old,pins)
        return
    if damage=='fee':
        with pytest.raises(ValueError):diagnostic.baseline_equal(current,old,pins)
    else:assert not diagnostic.baseline_equal(current,old,pins)


def test_future_fold_prices_cannot_change_earlier_probe_and_ongoing_pause_is_preserved():
    _,exps,_,_,_=inputs();data=exps[0]['dataset'];days=[b['date'] for b in data['000333']['bars']]
    symbols=['000333','000001','600519']
    for s in symbols[1:]:data[s]=copy.deepcopy(data['000333'])
    def replay(d,**kwargs):return engine.replay(d,symbols,CANDIDATES[0],[],{},investor=INVESTOR,
                                               mode='historical_reconstruction',start=days[60],**kwargs)
    first=replay(data,end=days[70]);changed=copy.deepcopy(data)
    for s in symbols:
        for b in changed[s]['bars'][71:]:
            for k in ('open','high','low','close'):b[k]*=.3
    assert first==replay(changed,end=days[70]) and first['fills']
    whole=replay(changed,end=days[-1]);prefix=replay(changed,end=days[75])
    suffix=replay(changed,end=days[-1],state=json.loads(json.dumps(prefix['state'])))
    assert prefix['fills']+suffix['fills']==whole['fills'] and suffix['state']==whole['state']
    assert prefix['state']['paused'] and suffix['state']['paused']
    assert not any(f['side']=='buy' for f in suffix['fills'])


@pytest.mark.parametrize('damage',['runner','path','overwrite'])
def test_cli_rejects_changed_runner_path_escape_and_overwrite(tmp_path,damage):
    script=Path(__file__).resolve().parents[3]/'scripts/quant-selection-diagnostics.py'
    files={k:tmp_path/(k+'.json') for k in ('experiments','events','reference','protocol','output')}
    files['experiments'].write_text('[]');files['reference'].write_text('{}')
    files['events'].write_text(json.dumps({'source_files':{'0'*64:'../outside.pdf'}}))
    files['protocol'].write_text(json.dumps({'runner_sha256':'0'*64 if damage=='runner' else hashlib.sha256(script.read_bytes()).hexdigest(),
                                           'reference_sha256':hashlib.sha256(files['reference'].read_bytes()).hexdigest()}))
    if damage=='overwrite':files['output'].write_text('keep prior failure')
    args=[sys.executable,str(script),'--reference-source-dir',str(tmp_path),'--evidence-dir',str(tmp_path)]
    for k,p in files.items():args.extend(['--'+k,str(p)])
    r=subprocess.run(args,capture_output=True,text=True);assert r.returncode!=0
    if damage=='overwrite':assert files['output'].read_text()=='keep prior failure'
    else:assert not files['output'].exists()
