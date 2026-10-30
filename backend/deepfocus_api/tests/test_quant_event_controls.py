"""Frozen comparative controls, using synthetic active financial paths."""
import copy,hashlib
from dataclasses import asdict
from datetime import datetime,timezone,timedelta
import pytest
from deepfocus_api import quant_event_controls as controls,quant_research_engine as engine
from deepfocus_api.quant_corporate_actions import CashDividend
from deepfocus_api.tests.test_quant_research_loop import dataset,request


@pytest.fixture
def registered():
    ds=dataset();req=request();result=engine.walk_forward(ds,req['symbols'],req['benchmark'],req['capital'])
    exp={'id':'synthetic-control','request':req,'dataset':ds,'result':result,'result_digest':engine.digest(result)}
    raw=b'Explicit synthetic test source, never a real disclosure';sha=hashlib.sha256(raw).hexdigest()
    days=[b['date'] for b in ds['600519']['bars']];events=[]
    for i,n in enumerate((240,270,300)):
        events.append(asdict(CashDividend('synthetic-'+str(i),1,'600519',days[n],days[n+1],days[n+2],str(.25+i*.1),str(.25+i*.1),
            days[n-5]+'T23:59:59+08:00','2026-10-30T20:00:00+08:00','https://example.invalid/source.pdf',sha)))
    baseline={'events':copy.deepcopy(events[:2])};expanded={'events':events}
    protocol={'schema':'expanded-cash-event-controls-v1','cases':list(controls.CASES),
              'registered_at':(datetime.now(timezone.utc)-timedelta(seconds=5)).isoformat(),
              'purpose':'seen_development_integration_no_selection','promotion_allowed':False,
              'restore_at':{exp['id']:days[300]},'bindings':controls.bindings([exp],expanded,baseline)}
    return protocol,[exp],expanded,baseline,{sha:raw}


def test_registered_active_controls_and_restore_keep_as_recorded_equal(registered):
    proof=controls.run_controls(*registered);row=proof['results'][0]
    assert not proof['promotion_allowed'] and not proof['oos_validation'] and not proof['commercial_ready']
    assert row['original_reproduced_exactly'] and row['restore']['fills_curve_final_state_equal']
    assert not row['parameters_refit'] and row['as_recorded_control_equal']
    assert row['cases']['expanded_events']['fills']
    assert float(row['cases']['expanded_events']['state']['book']['paid_gross'])>float(row['cases']['original_two_events']['state']['book']['paid_gross'])>0
    assert all(not arm['gate']['passed'] for arm in row['cases'].values())


@pytest.mark.parametrize('damage',['events','schedule','request','dataset','source','arm','promotion','checkpoint','future_registration'])
def test_registered_bindings_refuse_changes_before_replay(registered,damage):
    p,exps,full,old,sources=copy.deepcopy(registered)
    if damage=='events':full['events'][2]['cash_per_share_gross']='99'
    elif damage=='schedule':exps[0]['result']['folds'][0]['parameters']={'lookback':40,'threshold':.03}
    elif damage=='request':exps[0]['request']['capital']=2000000
    elif damage=='dataset':exps[0]['dataset']['600519']['bars'][0]['close']+=1
    elif damage=='source':p['bindings']['source_pins']['quant_event_replay.py']='0'*64
    elif damage=='arm':p['cases'].append('unregistered_candidate')
    elif damage=='promotion':p['promotion_allowed']=True
    elif damage=='checkpoint':p['restore_at'][exps[0]['id']]='2099-01-01'
    else:p['registered_at']='2099-01-01T00:00:00+00:00'
    with pytest.raises(ValueError):controls.validate_protocol(p,exps,full,old)


def test_changed_filing_bytes_or_original_two_facts_are_rejected(registered):
    p,exps,full,old,sources=copy.deepcopy(registered)
    sources[next(iter(sources))]=b'corrupted retained source'
    with pytest.raises(ValueError,match='原始公告'):controls.run_controls(p,exps,full,old,sources)
    p,exps,full,old,sources=copy.deepcopy(registered)
    old['events'][0]['cash_per_share_gross']='9'
    p['bindings']=controls.bindings(exps,full,old)
    with pytest.raises(ValueError,match='changed or removed'):controls.run_controls(p,exps,full,old,sources)


def test_control_rejects_an_actual_as_recorded_cash_path_difference(registered):
    p,exps,full,old,sources=copy.deepcopy(registered)
    full['events'][2]['observed_at']=full['events'][2]['available_at']
    p['bindings']=controls.bindings(exps,full,old)
    with pytest.raises(ValueError,match='leaked'):controls.run_controls(p,exps,full,old,sources)


@pytest.mark.parametrize('damage',['runner','path','overwrite'])
def test_cli_refuses_wrong_runner_outside_evidence_and_overwrite(tmp_path,damage):
    from pathlib import Path
    import subprocess,sys,json
    script=Path(__file__).resolve().parents[3]/'scripts/quant-expanded-event-control.py'
    protocol=tmp_path/'protocol.json';events=tmp_path/'events.json';baseline=tmp_path/'baseline.json';experiments=tmp_path/'experiments.json';output=tmp_path/'report.json'
    protocol.write_text(json.dumps({'runner_sha256':'0'*64 if damage=='runner' else hashlib.sha256(script.read_bytes()).hexdigest()}))
    events.write_text(json.dumps({'source_files':{'0'*64:'../escape.pdf'}}));baseline.write_text('{}');experiments.write_text('[]')
    if damage=='overwrite':output.write_bytes(b'preserved prior report')
    run=subprocess.run([sys.executable,str(script),'--experiments',str(experiments),'--events',str(events),
        '--baseline-events',str(baseline),'--evidence-dir',str(tmp_path),'--protocol',str(protocol),'--output',str(output)],capture_output=True,text=True)
    assert run.returncode!=0
    if damage=='overwrite':assert output.read_bytes()==b'preserved prior report'
    else:assert not output.exists()
