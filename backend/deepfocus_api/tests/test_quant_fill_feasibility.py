import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest
from deepfocus_api import quant_fill_feasibility as audit


def fixture():
    data={'000333':{'source':'unit_fixture','adjustment':'none','volume_unit':'shares','is_synthetic':False,
                    'fetched_at':'2026-10-31T00:00:00Z','bars':[
                        {'date':'2023-04-24','open':10.,'high':11.,'low':9.,'close':10.,'volume':100000},
                        {'date':'2023-04-25','open':10.,'high':11.,'low':9.,'close':10.,'volume':100000}]}}
    fill={'date':'2023-04-25','signal_date':'2023-04-24','symbol':'000333','side':'buy','qty':100,'price':10.01}
    return data,fill


def test_nonempty_path_checks_do_not_claim_execution_or_capacity():
    data,fill=fixture();before=copy.deepcopy(data);r=audit.audit(data,[fill])
    assert r['necessary_conditions_all_met'] and not r['flag_counts']
    assert r['max_daily_model_participation']=='0.001'
    assert not r['execution_verified'] and not r['open_auction_capacity_verified'] and not r['promotion_allowed']
    assert data==before


def test_range_is_inclusive_and_one_cent_adverse_price_is_flagged_not_clamped():
    data,fill=fixture();fill['price']=11.
    assert audit.audit(data,[fill])['necessary_conditions_all_met']
    fill['price']=11.01;r=audit.audit(data,[fill])
    assert r['flag_counts']=={'modeled_price_outside_daily_range':1}
    assert r['rows'][0]['price']=='11.01' and fill['price']==11.01
    fill.update(side='sell',price=8.99)
    assert audit.audit(data,[fill])['flag_counts']=={'modeled_price_outside_daily_range':1}


def test_daily_all_side_aggregation_cannot_evade_previous_volume_limit():
    data,fill=fixture();fill['qty']=600;sell={**fill,'side':'sell','price':9.99}
    r=audit.audit(data,[fill,sell])
    assert r['symbol_days']==1 and r['flag_counts']=={'aggregate_exceeds_declared_prior_volume_cap':2}
    assert r['rows'][0]['aggregate_daily_model_qty']==1200


def test_full_day_volume_is_diagnostic_not_a_replacement_order_cap():
    data,fill=fixture();data['000333']['bars'][-1]['volume']=99
    r=audit.audit(data,[fill])
    assert 'aggregate_exceeds_entire_daily_volume' in r['flag_counts']
    assert r['rows'][0]['previous_volume_cap_shares']==1000
    data['000333']['bars'][-1]['volume']=0;r=audit.audit(data,[fill])
    assert r['flag_counts']=={'fill_on_zero_daily_volume':1}
    assert r['max_daily_model_participation'] is None


def test_individual_cap_detected_and_empty_path_cannot_certify():
    data,fill=fixture();fill['qty']=1001;r=audit.audit(data,[fill])
    assert r['flag_counts']=={'aggregate_exceeds_declared_prior_volume_cap':1,'fill_exceeds_declared_prior_volume_cap':1}
    assert not audit.audit(data,[])['necessary_conditions_all_met']


@pytest.mark.parametrize('damage',['duplicate','no_bar','signal','zero','bool','nonfinite','bad_side','nonfinite_bar'])
def test_malformed_path_refuses_instead_of_omitting_a_fill(damage):
    data,fill=fixture();fills=[fill]
    if damage=='duplicate':fills.append(dict(fill))
    if damage=='no_bar':fill['date']='2023-04-26'
    if damage=='signal':fill['signal_date']=fill['date']
    if damage=='zero':fill['qty']=0
    if damage=='bool':fill['qty']=True
    if damage=='nonfinite':fill['price']=float('nan')
    if damage=='bad_side':fill['side']='short'
    if damage=='nonfinite_bar':data['000333']['bars'][-1]['volume']=float('inf')
    with pytest.raises(ValueError):audit.audit(data,fills)


@pytest.mark.parametrize('damage',['input','source','overwrite'])
def test_actual_cli_registration_and_binding_refusal(tmp_path,damage):
    root=Path(__file__).resolve().parents[3];script=root/'scripts/quant-fill-feasibility-review.py'
    source=tmp_path/'input.json';protocol=tmp_path/'protocol.json';out=tmp_path/'out.json'
    data,_=fixture();source.write_text(json.dumps([{'id':'unit','result_digest':'unit','dataset':data}]))
    cmd=[sys.executable,str(script)]
    args=['--input',str(source),'--protocol',str(protocol)]
    r=subprocess.run(cmd+['register']+args,capture_output=True,text=True,timeout=10)
    assert r.returncode==0,r.stderr
    if damage=='input':source.write_text(source.read_text()+'\n')
    if damage=='source':
        p=json.loads(protocol.read_text());p['bindings']['source_pins']['scripts/quant-fill-feasibility-review.py']='0'*64;protocol.write_text(json.dumps(p))
    if damage=='overwrite':out.write_text('retained')
    r=subprocess.run(cmd+['run']+args+['--output',str(out)],capture_output=True,text=True,timeout=10)
    assert r.returncode!=0
    if damage=='overwrite':assert out.read_text()=='retained'
    else:assert not out.exists() and 'Protocol/input/source changed' in r.stderr
