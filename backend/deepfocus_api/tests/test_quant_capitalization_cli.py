"""Actual offline subprocess entry point; all fixture prices/positions synthetic."""
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import pytest
from deepfocus_api.tests.test_quant_capitalization_book import ACTION,CASH,DAYS,SCOPE,PDF,SHA,fill
from deepfocus_api import quant_dividend_book as cash

ROOT=Path(__file__).resolve().parents[3]

def plan(tmp_path,qty=100):
    source=tmp_path/'unit.pdf';source.write_bytes(PDF)
    return {'position_role':'counterfactual_model','price_role':'illustrative_raw_marks','scope':SCOPE,
            'capital':50000,'start_after':'2023-04-23','mode':'historical_reconstruction','investor':cash.INVESTOR,
            'actions':[asdict(ACTION)],'cash_events':[asdict(CASH)],'sources':{SHA:str(source)},'sessions':DAYS,
            'days':[{'date':d,'open_prices':{'300750':p},'close_prices':{'300750':p},'fills':[fill(d,qty)] if i==0 else []}
                    for i,(d,p) in enumerate(zip(DAYS,[182.52,182.52,100,100,100]))]}

def run(tmp_path,value):
    path=tmp_path/'plan.json';path.write_text(json.dumps(value));workspace=tmp_path/'review'
    result=subprocess.run([sys.executable,str(ROOT/'scripts/quant-capitalization-review.py'),'--plan',str(path),'--workspace',str(workspace)],capture_output=True,text=True,timeout=20)
    assert 'Traceback' not in result.stderr,result.stderr
    return result,workspace,json.loads((workspace/'result.json').read_text())

def test_cli_executes_composition_and_retains_exact_inputs_and_checkpoint(tmp_path):
    value=plan(tmp_path);result,w,r=run(tmp_path,value)
    assert result.returncode==0 and r['completed_days']==5 and r['status']=='composite_quantity_valuation_completed'
    assert (w/'input-plan.json').read_bytes()==(tmp_path/'plan.json').read_bytes()
    checkpoint=json.loads((w/'last-closed-checkpoint.json').read_text())
    assert checkpoint['cash']['paid_gross']=='252.00' and checkpoint['valuation']['new_share_value_exact']=='8000'
    assert not r['strategy_execution_ready'] and r['strategy_replays']==r['production_writes']==r['network_requests']==0

def test_cli_rejected_trade_retains_last_closed_checkpoint_not_partial_open_state(tmp_path):
    value=plan(tmp_path);value['days'][2]['fills']=[fill(DAYS[2],side='sell',price=100)]
    result,w,r=run(tmp_path,value)
    assert result.returncode==2 and r['failed_stage']=='fills' and r['completed_days']==2
    checkpoint=json.loads((w/'last-closed-checkpoint.json').read_text())
    assert checkpoint['cash']['phase']=='closed' and checkpoint['cash']['cursor']==DAYS[1]
    assert checkpoint['shares']['actions'][ACTION.event_id]['status']=='registered'

def test_cli_pending_allocation_remains_non_success_and_is_recoverable(tmp_path):
    result,w,r=run(tmp_path,plan(tmp_path,1))
    assert result.returncode==2 and r['status']=='awaiting_allotment' and r['completed_days']==5
    b=json.loads((w/'last-closed-checkpoint.json').read_text());assert b['shares']['actions'][ACTION.event_id]['credited'] is None

def test_cli_changed_original_rejects_without_opening_an_account(tmp_path):
    value=plan(tmp_path);(tmp_path/'unit.pdf').write_bytes(PDF+b'changed')
    result,w,r=run(tmp_path,value)
    assert result.returncode==2 and r['failed_stage']=='evidence' and r['completed_days']==0
    assert not (w/'last-closed-checkpoint.json').exists()

@pytest.mark.parametrize('field',['position_role','price_role'])
def test_cli_provenance_cannot_be_implicit(tmp_path,field):
    value=plan(tmp_path);value.pop(field)
    result,w,r=run(tmp_path,value)
    assert result.returncode==2 and r['failed_stage']=='plan' and r['completed_days']==0

def test_cli_unknown_historical_evidence_is_not_a_completed_entitlement_review(tmp_path):
    value=plan(tmp_path);value['mode']='as_recorded'
    value['actions'][0]['observed_at']='2026-10-31T08:00:00+08:00';value['cash_events'][0]['observed_at']='2026-10-31T08:00:00+08:00'
    result,w,r=run(tmp_path,value)
    assert result.returncode==2 and r['status']=='not_available_as_recorded'
    assert len(r['unavailable_declared_actions'])==2
