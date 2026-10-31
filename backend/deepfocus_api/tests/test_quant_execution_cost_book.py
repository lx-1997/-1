import copy
import importlib.util
import json
from decimal import Decimal
from pathlib import Path
import subprocess
import sys

import pytest
from deepfocus_api import quant_execution_cost_book as book, quant_research_engine as engine
from deepfocus_api.ai_fund_ledger import fee
from deepfocus_api.tests.test_quant_fill_feasibility import fixture


def fill(side='buy',price=10.01,qty=100):
    return {'date':'2023-04-25','signal_date':'2023-04-24','symbol':'000333',
            'side':side,'price':price,'qty':qty,'fee':fee(qty*price,side),'realized_pnl':None}


@pytest.mark.parametrize('side,opening,price',[('buy',10.,10.01),('sell',10.,9.99),('buy',10.11,10.13),('sell',10.11,10.09)])
def test_reference_slippage_tick_fee_exact_cash_identity(side,opening,price):
    f=fill(side,price);r=book.decompose(f,opening)
    assert Decimal(r['model_cash_delta'])==Decimal(r['separated_cash_delta'])
    assert Decimal(r['adverse_slippage_cost'])>=0 and Decimal(r['adverse_tick_cost'])>=0
    assert not r['execution_verified'] and not r['tax_basis_verified']
    assert Decimal(r['original_fee'])==Decimal(str(f['fee']))


def test_model_price_outside_range_is_not_clipped_or_called_execution():
    f=fill(price=10.03);r=book.decompose(f,10.01)
    assert r['reference_open']=='10.01' and r['model_cashflow_price']=='10.03'
    assert r['model_price_role']=='adverse_cost_adjusted_cashflow_unit_price'
    assert r['reference_role']=='provider_daily_open_unverified_execution'


@pytest.mark.parametrize('damage',['favorable','missing_slip','price_clipped','fee_changed','qty_bool','negative_open'])
def test_model_mismatch_refuses_instead_of_adjusting_components(damage):
    f=fill();opening=10.
    if damage=='favorable':f['price']=9.99
    if damage=='missing_slip':f['price']=10.
    if damage=='price_clipped':f['price']=10.02
    if damage=='fee_changed':f['fee']+=.01
    if damage=='qty_bool':f['qty']=True
    if damage=='negative_open':opening=-10.
    with pytest.raises(ValueError):book.decompose(f,opening)


def path_fixture():
    data,_=fixture()
    # This remains isolated fixture data, not a real supplier/account record.
    data['000333']['bars'] += [dict(data['000333']['bars'][-1],date='2023-04-26'),dict(data['000333']['bars'][-1],date='2023-04-27')]
    buy=fill(qty=200);sell=fill('sell',9.99,100);sell.update(date='2023-04-26',signal_date='2023-04-25')
    # Entry fee5.02 allocates2.51; sale fee5.51 -> 999-5.51-1003.51=-10.02.
    sell['realized_pnl']=-10.02
    cash1=10000-200*10.01-buy['fee'];cash2=cash1+999-sell['fee']
    path={'fills':[buy,sell],'curve':[
        {'date':'2023-04-25','cash':round(cash1,2),'nav':round(cash1+2000,2)},
        {'date':'2023-04-26','cash':round(cash2,2),'nav':round(cash2+1000,2)},
        {'date':'2023-04-27','cash':round(cash2,2),'nav':round(cash2+1000,2)}],
          'state':{'cash':round(cash2,2),'positions':{'000333':{'qty':100}}}}
    return data,path


def test_partial_sale_and_no_trade_day_reconcile_and_do_not_mutate_inputs():
    data,path=path_fixture();before=copy.deepcopy((data,path));r=book.reconcile(data,path,10000)
    assert r['fills_reconciled']==2 and r['daily_phases_reconciled']==3
    assert r['all_cash_nav_realized_pnl_and_quantities_equal']
    assert (data,path)==before
    assert r['final_model_positions']['000333']['qty']==100


def test_current_close_changes_valuation_only_not_reference_cost_receipts():
    data,path=path_fixture();before=book.reconcile(data,path,10000)
    data['000333']['bars'][2]['close']=10.5;path['curve'][1]['nav']+=50
    after=book.reconcile(data,path,10000)
    assert before['receipts']==after['receipts']
    assert Decimal(after['daily'][1]['nav'])-Decimal(before['daily'][1]['nav'])==50


def test_cost_components_cannot_be_spent_as_available_cash():
    data,path=path_fixture()
    with pytest.raises(ValueError,match='negative'):
        book.reconcile(data,path,2000)


def test_binary_half_cent_realized_pnl_difference_is_exposed_and_refused():
    data,path=path_fixture()
    for bar in data['000333']['bars']:bar.update(open=33.2,high=34.,low=32.,close=33.2)
    buy=fill(price=33.24,qty=200);sell=fill('sell',33.16,100)
    sell.update(date='2023-04-26',signal_date='2023-04-25')
    original_float_cost=(200*33.24+buy['fee'])/200
    sell['realized_pnl']=round(100*33.16-sell['fee']-100*original_float_cost,2)
    assert sell['realized_pnl']==-17.23
    cash1=round(10000-200*33.24-buy['fee'],2);cash2=round(cash1+100*33.16-sell['fee'],2)
    path['fills']=[buy,sell]
    for i,row in enumerate(path['curve']):
        row['cash']=cash1 if i==0 else cash2;row['nav']=round(row['cash']+(200 if i==0 else 100)*33.2,2)
    path['state']['cash']=cash2
    with pytest.raises(book.ReconciliationError) as error:book.reconcile(data,path,10000)
    assert error.value.details['unrounded_decimal_pnl']=='-17.225'
    assert error.value.details['decimal_half_even_pnl']=='-17.22'
    assert error.value.details['original_reported_pnl']=='-17.23'


@pytest.mark.parametrize('damage',['cash','nav','realized','final_qty','duplicate','signal','unheld','order'])
def test_any_cash_nav_or_cost_basis_discrepancy_rejects(damage):
    data,path=path_fixture()
    if damage=='cash':path['curve'][0]['cash']+=.01
    if damage=='nav':path['curve'][1]['nav']+=.01
    if damage=='realized':path['fills'][1]['realized_pnl']+=.01
    if damage=='final_qty':path['state']['positions']['000333']['qty']+=100
    if damage=='duplicate':path['fills'].append(copy.deepcopy(path['fills'][-1]))
    if damage=='signal':path['fills'][0]['signal_date']=path['fills'][0]['date']
    if damage=='unheld':path['fills'][0]=fill('sell',9.99)
    if damage=='order':path['fills'].reverse()
    with pytest.raises(ValueError):book.reconcile(data,path,10000)


@pytest.mark.parametrize('damage',['input','source','overwrite'])
def test_cost_cli_frozen_protocol_and_exclusive_output(tmp_path,damage):
    root=Path(__file__).resolve().parents[3];script=root/'scripts/quant-execution-cost-review.py'
    source=tmp_path/'input.json';protocol=tmp_path/'protocol.json';out=tmp_path/'out.json'
    data,_=fixture();source.write_text(json.dumps([{'id':'unit','result_digest':'unit','dataset':data}]))
    args=['--input',str(source),'--protocol',str(protocol)]
    run=subprocess.run([sys.executable,str(script),'register']+args,capture_output=True,text=True,timeout=10)
    assert run.returncode==0,run.stderr
    if damage=='input':source.write_text(source.read_text()+'\n')
    if damage=='source':
        p=json.loads(protocol.read_text());p['bindings']['source_pins']['backend/deepfocus_api/quant_execution_cost_book.py']='0'*64;protocol.write_text(json.dumps(p))
    if damage=='overwrite':out.write_text('retain')
    run=subprocess.run([sys.executable,str(script),'run']+args+['--output',str(out)],capture_output=True,text=True,timeout=10)
    assert run.returncode==2
    if damage=='overwrite':assert out.read_text()=='retain'
    else:assert not out.exists() and 'Protocol/input/source changed' in run.stderr


def test_failure_report_preserves_completed_case_and_refuses_overall_success(tmp_path,monkeypatch):
    # Wrapper serialization fixture only; real original-engine failure is kept
    # separately in the scoped retained-data review, never bypassed in runtime.
    root=Path(__file__).resolve().parents[3];script=root/'scripts/quant-execution-cost-review.py'
    spec=importlib.util.spec_from_file_location('cost_report_test',script);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    data,_=fixture();sealed={'dataset_digest':engine.digest(data),'gate':{'passed':False},'oos':{'return_pct':-1}}
    experiments=[{'id':name,'dataset':data,'result':sealed,'result_digest':engine.digest(sealed),
                  'request':{'symbols':['000333'],'benchmark':'000300','capital':10000}} for name in ['first','second']]
    source=tmp_path/'input.json';source.write_text(json.dumps(experiments));protocol=tmp_path/'protocol.json';output=tmp_path/'result.json'
    monkeypatch.setattr(module.engine,'walk_forward',lambda *a,**kw:sealed)
    monkeypatch.setattr(module,'replay_oos',lambda *a,**kw:{'wrapper_fixture':True})
    calls=[]
    def compute(*args):
        calls.append(True)
        if len(calls)==2:raise book.ReconciliationError('unit half-cent mismatch',{'original_reported_pnl':'1.01','decimal_half_even_pnl':'1.00'})
        return {'wrapper_fixture_completed':True}
    monkeypatch.setattr(module.audit,'reconcile',compute)
    args=['cost-review','register','--input',str(source),'--protocol',str(protocol)]
    monkeypatch.setattr(sys,'argv',args);assert module.main()==0
    monkeypatch.setattr(sys,'argv',[args[0],'run']+args[2:]+['--output',str(output)])
    assert module.main()==2
    report=json.loads(output.read_text())
    assert report['status']=='rejected' and report['failure']['experiment']=='second'
    assert report['results'][0]['cost_book']=={'wrapper_fixture_completed':True}
    assert report['results'][1]['cost_book'] is None
    assert report['failure']['details']['decimal_half_even_pnl']=='1.00'
    assert report['promotion_allowed'] is False
