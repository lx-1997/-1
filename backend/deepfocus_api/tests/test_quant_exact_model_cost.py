import copy
from decimal import localcontext
from fractions import Fraction as F
import json
from pathlib import Path
import subprocess
import sys

import pytest
from deepfocus_api import quant_exact_model_cost as exact
from deepfocus_api.tests.test_quant_execution_cost_book import path_fixture, fill


@pytest.mark.parametrize('value,expected',[(F(1,2),0),(F(3,2),2),(F(5,2),2),(F(-1,2),0),(F(-3,2),-2),(F(-5,2),-2),(F(149,100),1),(F(-151,100),-2)])
def test_half_even_rounding_is_symmetric_and_exact(value,expected):
    assert exact.rounded(value)==expected


def test_active_partial_sale_conserves_basis_pnl_and_restore_full_state():
    data,path=path_fixture();before=copy.deepcopy((data,path))
    full=exact.run(data,path,10000,scope='unit')
    prefix=exact.run(data,path,10000,scope='unit',through='2023-04-25')
    suffix=exact.run(data,path,10000,scope='unit',state=json.loads(json.dumps(prefix['state'])))
    assert prefix['state']['positions'] and suffix['state']==full['state']
    for key in ['daily','receipts','legacy_differences']:assert prefix[key]+suffix[key]==full[key]
    assert (data,path)==before and full['exact_conservation_verified']
    for row in full['daily']:
        assert exact.unpack(row['economic_pnl'])==exact.unpack(row['realized_exact'])+exact.unpack(row['unrealized_exact'])
        assert exact.unpack(row['realized_exact'])==F(row['realized_rounded_cents_sum'],100)+exact.unpack(row['rounding_residual'])


def test_decimal_context_does_not_change_new_receipts_or_checkpoints():
    data,path=path_fixture();expected=exact.run(data,path,10000,scope='unit')
    with localcontext() as context:
        context.prec=3
        assert exact.run(data,path,10000,scope='unit')==expected


def test_repeating_partial_basis_closes_without_lost_fraction_or_cash_adjustment():
    data,path=path_fixture();buy=fill(qty=300);sell=fill('sell',9.99,100);last=fill('sell',9.99,200)
    sell.update(date='2023-04-26',signal_date='2023-04-25',realized_pnl=-9.19)
    last.update(date='2023-04-27',signal_date='2023-04-26',realized_pnl=-13.37)
    cash=10000;path['fills']=[buy,sell,last]
    for i,f in enumerate(path['fills']):
        cash=round(cash+(-1 if f['side']=='buy' else 1)*f['qty']*f['price']-f['fee'],2)
        qty=[300,200,0][i];path['curve'][i].update(cash=cash,nav=round(cash+qty*10,2))
    path['state']={'cash':cash,'positions':{}}
    whole=exact.run(data,path,10000,scope='unit')
    cut=exact.run(data,path,10000,scope='unit',through='2023-04-26')
    assert exact.unpack(cut['state']['positions']['000333']['basis']).denominator%3==0
    assert whole['state']['positions']=={} and whole['legacy_differences']==[]
    assert exact.unpack(whole['state']['realized_exact'])==F(whole['state']['cash_cents']-1000000,100)
    assert exact.unpack(whole['daily'][-1]['rounding_residual'])==0


def test_same_closed_checkpoint_is_idempotent_and_cannot_rewind():
    data,path=path_fixture();full=exact.run(data,path,10000,scope='unit')
    repeat=exact.run(data,path,10000,scope='unit',state=full['state'])
    assert repeat['state']==full['state'] and repeat['receipts']==[] and repeat['daily']==[]
    with pytest.raises(ValueError,match='rewind'):exact.run(data,path,10000,scope='unit',state=full['state'],through='2023-04-25')


@pytest.mark.parametrize('value',[['1','0'],['2','4'],['1','-2'],['0','00'],['1'*1026,'1']])
def test_noncanonical_or_unbounded_fraction_state_is_rejected(value):
    with pytest.raises(ValueError):exact.unpack(value)


def test_known_legacy_half_cent_is_corrected_in_new_receipt_and_old_value_retained():
    data,path=path_fixture()
    for bar in data['000333']['bars']:bar.update(open=33.2,high=34.,low=32.,close=33.2)
    buy=fill(price=33.24,qty=200);sell=fill('sell',33.16,100)
    sell.update(date='2023-04-26',signal_date='2023-04-25',realized_pnl=-17.23)
    cash1=round(10000-200*33.24-buy['fee'],2);cash2=round(cash1+100*33.16-sell['fee'],2)
    path['fills']=[buy,sell]
    for i,row in enumerate(path['curve']):
        row['cash']=cash1 if i==0 else cash2;row['nav']=round(row['cash']+(200 if i==0 else 100)*33.2,2)
    path['state']['cash']=cash2
    r=exact.run(data,path,10000,scope='unit')
    assert r['legacy_reconciliation_passed'] is False
    assert r['legacy_differences'][0]['legacy_cents']==-1723
    assert r['legacy_differences'][0]['exact_model_rounded_cents']==-1722
    assert exact.unpack(r['daily'][-1]['rounding_residual'])==F(-1,200)
    assert path['fills'][-1]['realized_pnl']==-17.23 and r['promotion_allowed'] is False


@pytest.mark.parametrize('damage',['version','source','scope','price','cash','digest','prefix'])
def test_restore_refuses_incompatible_or_corrupt_state_without_migration(damage):
    data,path=path_fixture();state=exact.run(data,path,10000,scope='unit',through='2023-04-25')['state']
    scope='unit'
    if damage=='version':state['version']='fixed-path-cost-book-v1'
    if damage=='source':state['source_sha256']='0'*64
    if damage=='scope':scope='other'
    if damage=='price':data['000333']['bars'][-1]['close']=10.01
    if damage=='cash':state['cash_cents']+=1;exact.seal(state)
    if damage=='digest':state['positions']['000333']['qty']+=100
    if damage=='prefix':state['fill_prefix_digest']='0'*64;exact.seal(state)
    with pytest.raises(ValueError):exact.run(data,path,10000,scope=scope,state=state)


@pytest.mark.parametrize('damage',['nav','fee','price','legacy_pnl_nonfinite','quantity','duplicate','empty','date'])
def test_new_model_rejects_malformed_economics_instead_of_repairing_cash(damage):
    data,path=path_fixture()
    if damage=='nav':path['curve'][1]['nav']+=.01
    if damage=='fee':path['fills'][0]['fee']+=.01
    if damage=='price':path['fills'][0]['price']+=.01
    if damage=='legacy_pnl_nonfinite':path['fills'][1]['realized_pnl']=float('nan')
    if damage=='quantity':path['fills'][0]['qty']=True
    if damage=='duplicate':path['fills'].append(copy.deepcopy(path['fills'][-1]))
    if damage=='empty':path['curve']=[]
    if damage=='date':path['curve'].reverse()
    with pytest.raises(ValueError):exact.run(data,path,10000,scope='unit')


@pytest.mark.parametrize('damage',['input','source','overwrite'])
def test_exact_model_cli_refuses_changed_protocol_bindings(tmp_path,damage):
    script=Path(__file__).resolve().parents[3]/'scripts/quant-exact-model-cost-review.py'
    data,_=path_fixture();source=tmp_path/'input.json';protocol=tmp_path/'protocol.json';output=tmp_path/'result.json'
    source.write_text(json.dumps([{'id':'unit','dataset':data,'result_digest':'unit'}]))
    args=['--input',str(source),'--protocol',str(protocol)]
    run=subprocess.run([sys.executable,str(script),'register']+args,capture_output=True,text=True,timeout=10)
    assert run.returncode==0,run.stderr
    if damage=='input':source.write_text(source.read_text()+'\n')
    if damage=='source':
        p=json.loads(protocol.read_text());p['bindings']['source_pins']['backend/deepfocus_api/quant_exact_model_cost.py']='0'*64;protocol.write_text(json.dumps(p))
    if damage=='overwrite':output.write_text('retain')
    run=subprocess.run([sys.executable,str(script),'run']+args+['--output',str(output)],capture_output=True,text=True,timeout=10)
    assert run.returncode==2
    if damage=='overwrite':assert output.read_text()=='retain'
    else:assert not output.exists() and 'Protocol/input/source changed' in run.stderr
