"""Synthetic accounting invariants, not market data or strategy validation."""
import copy
from dataclasses import replace
from decimal import Decimal,localcontext,Context
import hashlib
import json
import pytest
from deepfocus_api import quant_capitalization_book as v,quant_dividend_book as cash,quant_share_entitlements as shares
from deepfocus_api.quant_corporate_actions import CashDividend
from deepfocus_api.ai_fund_ledger import fee

PDF=b'%PDF-1.7 UNIT FIXTURE NOT AN ISSUER NOTICE';SHA=hashlib.sha256(PDF).hexdigest();SOURCES={SHA:PDF}
SCOPE='unit-only-account';DAYS=['2023-04-24','2023-04-25','2023-04-26','2023-04-27','2023-04-28']
ACTION=shares.Capitalization('cap-unit','300750','capital_reserve',DAYS[1],DAYS[2],DAYS[2],DAYS[2],'0.8',
    DAYS[0]+'T08:00:00+08:00',DAYS[0]+'T08:00:00+08:00','https://example.invalid/unit.pdf',SHA)
CASH=CashDividend('cash-unit',1,'300750',DAYS[1],DAYS[2],DAYS[3],'2.52','2.52',
    DAYS[0]+'T08:00:00+08:00',DAYS[0]+'T08:00:00+08:00','https://example.invalid/unit.pdf',SHA)

def initial(mode='historical_reconstruction'):
    return v.initial(50000,'2023-04-23',scope=SCOPE,investor=cash.INVESTOR,mode=mode)

def fill(d,qty=100,side='buy',symbol='300750',price=182.52,identity='buy-unit'):
    return {'id':identity,'date':d,'symbol':symbol,'side':side,'qty':qty,'price':price,'fee':fee(Decimal(str(price))*qty,side)}

def step(b,d,p,*,fills=None,events=None,actions=None,allotments=None):
    events=[CASH] if events is None else events;actions=[ACTION] if actions is None else actions
    prices=p if isinstance(p,dict) else {'300750':p}
    opened=v.open_session(b,d,prices,events,actions,SOURCES,DAYS,scope=SCOPE)
    filled=v.execute_fills(opened,fills or [],scope=SCOPE)
    return v.close_session(filled,prices,events,actions,SOURCES,DAYS,scope=SCOPE,allotments=allotments)

def registered(qty=100):
    b=step(initial(),DAYS[0],182.52,fills=[fill(DAYS[0],qty)])
    return step(b,DAYS[1],182.52)

def test_cash_receivable_and_new_stock_prevent_false_ex_date_loss_without_cash_creation():
    b=registered();before=copy.deepcopy(b);opened=v.open_session(b,DAYS[2],{'300750':100},[CASH],[ACTION],SOURCES,DAYS,scope=SCOPE)
    assert opened['shares']['actions'][ACTION.event_id]['status']=='receivable'
    gross=before['valuation']['composite_gross_nav_exact']
    assert opened['valuation']['composite_gross_nav_exact']==gross
    assert Decimal(gross)-Decimal(opened['valuation']['cash_book']['gross_nav'])==8000
    assert Decimal(gross)-Decimal(opened['valuation']['cash_dividend_tax_reserved_nav_lower'])==Decimal('50.40')
    assert opened['valuation']['available_cash']==before['valuation']['available_cash']
    assert opened['cash']['cash']==b['cash']['cash'] and b==before
    closed=v.close_session(opened,{'300750':100},[CASH],[ACTION],SOURCES,DAYS,scope=SCOPE)
    assert closed['valuation']['composite_gross_nav_exact']==opened['valuation']['composite_gross_nav_exact']
    assert closed['shares']['actions'][ACTION.event_id]['credited']['unrestricted']==80
    paid=step(closed,DAYS[3],100)
    assert paid['cash']['paid_gross']=='252.00' and paid['valuation']['cash_book']['dividend_receivable']=='0.00'
    assert paid['valuation']['composite_gross_nav_exact']==gross
    assert Decimal(paid['valuation']['available_cash'])-Decimal(closed['valuation']['available_cash'])==Decimal('201.60')
    assert paid['valuation']['strategy_risk_nav'] is None and paid['valuation']['complete_after_tax_nav'] is None
    assert not paid['strategy_execution_ready'] and not paid['promotion_allowed']

def test_fractional_receivable_is_bounded_not_rounded_and_json_resume_is_identical():
    b=registered(1);a=step(b,DAYS[2],100);item=a['shares']['actions'][ACTION.event_id]
    assert item['status']=='receivable' and item['credited'] is None
    val=a['valuation'];assert val['new_share_value_exact']=='80.0'
    assert val['new_share_value_lower']=='0' and val['new_share_value_upper']=='100'
    assert Decimal(val['composite_gross_nav_upper'])-Decimal(val['composite_gross_nav_lower'])==100
    restored=v.restore(json.dumps(a),scope=SCOPE)
    assert step(restored,DAYS[3],100)==step(a,DAYS[3],100)

@pytest.mark.parametrize('side',['buy','sell'])
def test_affected_symbol_fills_reject_before_cash_or_inventory_changes(side):
    b=registered();o=v.open_session(b,DAYS[2],{'300750':100},[CASH],[ACTION],SOURCES,DAYS,scope=SCOPE);before=copy.deepcopy(o)
    with pytest.raises(ValueError,match='inheritance'):v.execute_fills(o,[fill(DAYS[2],side=side,price=100)],scope=SCOPE)
    assert o==before

def test_unaffected_symbol_can_trade_with_same_cash_ledger():
    b=step(registered(),DAYS[2],100)
    c=step(b,DAYS[3],{'300750':100,'600036':10},fills=[fill(DAYS[3],symbol='600036',price=10,identity='other')])
    assert any(l['symbol']=='600036' for l in c['cash']['lots'])
    assert c['valuation']['new_share_value_exact']=='8000'

def test_no_action_composition_matches_existing_cash_ledger_exactly():
    b=initial();legacy=copy.deepcopy(b['cash'])
    for d in DAYS:
        fills=[fill(d)] if d==DAYS[0] else []
        b=step(b,d,182.52,fills=fills,events=[],actions=[])
        legacy=cash.apply_day(legacy,d,fills,{'300750':182.52},[],SOURCES,DAYS,mode='historical_reconstruction')
        assert b['cash']==legacy and b['valuation']['composite_gross_nav_exact']==legacy['valuation']['gross_nav']

def test_later_cash_registration_on_new_shares_is_rejected_not_dropped():
    b=step(registered(),DAYS[2],100);later=replace(CASH,event_id='later',record_date=DAYS[3],ex_date=DAYS[4],pay_date=DAYS[4])
    with pytest.raises(ValueError,match='inheritance'):step(b,DAYS[3],100,events=[CASH,later])

def test_missing_position_mark_is_rejected_instead_of_zero_valued():
    b=step(registered(),DAYS[2],100)
    with pytest.raises(ValueError):step(b,DAYS[3],{})

def test_dividend_or_capitalization_inputs_cannot_change_inside_session():
    b=registered();o=v.open_session(b,DAYS[2],{'300750':100},[CASH],[ACTION],SOURCES,DAYS,scope=SCOPE)
    with pytest.raises(ValueError,match='changed'):v.close_session(o,{'300750':100},[],[ACTION],SOURCES,DAYS,scope=SCOPE)
    with pytest.raises(ValueError,match='revised'):v.open_session(b,DAYS[2],{'300750':100},[CASH],[],SOURCES,DAYS,scope=SCOPE)

def test_skipped_or_late_registration_requires_explicit_rebuild():
    b=step(initial(),DAYS[0],182.52,actions=[])
    b=step(b,DAYS[1],182.52,actions=[])
    with pytest.raises(ValueError,match='registration'):step(b,DAYS[2],100)

def test_duplicate_economic_action_cannot_double_entitlements():
    with pytest.raises(ValueError,match='overlapping'):step(initial(),DAYS[0],182.52,actions=[ACTION,replace(ACTION,event_id='renamed')])

def test_unknown_listing_date_has_value_but_no_new_sellable_inventory():
    e=replace(ACTION,unrestricted_trade_date=None);b=initial()
    for d,p in zip(DAYS[:3],[182.52,182.52,100]):b=step(b,d,p,actions=[e],fills=[fill(d)] if d==DAYS[0] else [])
    assert b['valuation']['new_share_value_exact']=='8000'
    assert b['valuation']['new_shares'][0]['available_new_shares']==0

def test_checkpoint_scope_tamper_and_source_mutation_fail_closed(monkeypatch):
    b=registered()
    with pytest.raises(ValueError):v.restore(json.dumps(b),scope='foreign')
    altered=copy.deepcopy(b);altered['cash']['cash']='999999'
    with pytest.raises(ValueError,match='changed'):v.restore(json.dumps(altered),scope=SCOPE)
    monkeypatch.setattr(v,'pins',lambda:{})
    with pytest.raises(ValueError,match='source'):step(b,DAYS[2],100)

def test_adverse_decimal_context_does_not_change_composed_result():
    b=registered();expected=step(b,DAYS[2],100)
    with localcontext(Context(prec=3)):assert step(b,DAYS[2],100)==expected

def test_unknown_or_unused_allotment_is_rejected():
    b=registered()
    with pytest.raises(ValueError,match='Unknown'):step(b,DAYS[2],100,allotments={'foreign':None})
    c=step(b,DAYS[2],100)
    with pytest.raises(ValueError,match='validated'):step(c,DAYS[3],100,allotments={ACTION.event_id:None})

def test_fill_needs_current_raw_mark_not_implicit_execution_price():
    o=v.open_session(initial(),DAYS[0],{},[],[],SOURCES,DAYS,scope=SCOPE)
    with pytest.raises(ValueError,match='raw mark'):v.execute_fills(o,[fill(DAYS[0])],scope=SCOPE)

def test_empty_registration_cannot_create_phantom_entitlements_or_block_unrelated_trades():
    b=initial()
    for d in DAYS[:3]:b=step(b,d,100)
    assert b['valuation']['new_share_value_exact']=='0'
    assert b['valuation']['composite_gross_nav_exact']=='50000.00'
    c=step(b,DAYS[3],100,fills=[fill(DAYS[3],price=100)])
    assert sum(l['qty'] for l in c['cash']['lots'])==100

def test_late_visibility_cannot_backfill_a_registration():
    late=replace(ACTION,observed_at=DAYS[2]+'T08:00:00+08:00');b=initial('as_recorded')
    b=step(b,DAYS[0],182.52,actions=[late],fills=[fill(DAYS[0])]);b=step(b,DAYS[1],182.52,actions=[late])
    with pytest.raises(ValueError,match='registration'):step(b,DAYS[2],100,actions=[late])

def test_chained_capitalizations_use_original_and_credited_shares_exactly_once():
    later=replace(ACTION,event_id='second',record_date=DAYS[3],ex_date=DAYS[4],share_credit_date=DAYS[4],unrestricted_trade_date=DAYS[4])
    b=initial()
    for d,p in zip(DAYS,[182.52,182.52,100,100,100]):
        b=step(b,d,p,actions=[ACTION,later],fills=[fill(d)] if d==DAYS[0] else [])
    item=b['shares']['actions']['second'];assert item['registered_old_shares']['unrestricted']==180
    assert item['credited']['unrestricted']==144
    assert b['valuation']['new_share_value_exact']=='22400'

def test_unresolved_fraction_cannot_be_used_as_whole_stock_in_next_registration():
    later=replace(ACTION,event_id='second',record_date=DAYS[3],ex_date=DAYS[4],share_credit_date=DAYS[4],unrestricted_trade_date=DAYS[4])
    b=initial()
    for d,p in zip(DAYS[:3],[182.52,182.52,100]):b=step(b,d,p,actions=[ACTION,later],fills=[fill(d,1)] if d==DAYS[0] else [])
    with pytest.raises(ValueError,match='Unallocated'):step(b,DAYS[3],100,actions=[ACTION,later])

@pytest.mark.parametrize('price',['0.001','1e-1000000','NaN','100000001',True])
def test_invalid_raw_marks_cannot_enter_a_composite_snapshot(price):
    with pytest.raises(ValueError):step(initial(),DAYS[0],price)

def test_fractional_confirmation_changes_quantity_once_and_releases_no_cash():
    receipt=b'%PDF-1.7 UNIT ALLOTMENT NOT A REAL ACCOUNT';sha=hashlib.sha256(receipt).hexdigest()
    sources={**SOURCES,sha:receipt};b=registered(1)
    o=v.open_session(b,DAYS[2],{'300750':100},[CASH],[ACTION],sources,DAYS,scope=SCOPE)
    confirmation=shares.Allotment('unit-allotment',SCOPE,ACTION.fingerprint,DAYS[2],1,0,DAYS[2]+'T15:00:00+08:00',DAYS[2]+'T15:00:00+08:00',sha)
    c=v.close_session(o,{'300750':100},[CASH],[ACTION],sources,DAYS,scope=SCOPE,allotments={ACTION.event_id:confirmation})
    assert c['valuation']['new_share_value_lower']==c['valuation']['new_share_value_upper']=='100'
    assert c['cash']['cash']==o['cash']['cash']
    assert c['valuation']['new_shares'][0]['available_new_shares']==1
    next_open=v.open_session(c,DAYS[3],{'300750':100},[CASH],[ACTION],sources,DAYS,scope=SCOPE)
    with pytest.raises(ValueError,match='eligible'):v.close_session(next_open,{'300750':100},[CASH],[ACTION],sources,DAYS,scope=SCOPE,allotments={ACTION.event_id:confirmation})

def test_intraday_late_observation_cannot_disappear_from_close_valuation():
    late=replace(ACTION,observed_at=DAYS[2]+'T16:00:00+08:00');b=initial('as_recorded')
    b=step(b,DAYS[0],182.52,actions=[late],fills=[fill(DAYS[0])]);b=step(b,DAYS[1],182.52,actions=[late])
    o=v.open_session(b,DAYS[2],{'300750':100},[CASH],[late],SOURCES,DAYS,scope=SCOPE)
    with pytest.raises(ValueError,match='Late close'):v.close_session(o,{'300750':100},[CASH],[late],SOURCES,DAYS,scope=SCOPE)
