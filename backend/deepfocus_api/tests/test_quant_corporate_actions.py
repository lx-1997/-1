import copy
from dataclasses import asdict, replace
from decimal import Decimal
import hashlib
import pytest
from deepfocus_api.quant_corporate_actions import (CashDividend, visible_events, validate_evidence,
    ex_reference, gross_return_index, fixed_path_overlay)

PDF=b'%PDF-unit-test-only-not-a-real-market-announcement'
SHA=hashlib.sha256(PDF).hexdigest()


def event(**kw):
    d=dict(event_id='fixture-dividend',revision=1,symbol='000333',record_date='2024-05-14',
        ex_date='2024-05-15',pay_date='2024-05-17',cash_per_share_gross='3',
        price_adjustment_per_share='2.9791154',available_at='2024-05-09T23:59:59+08:00',
        observed_at='2024-05-10T09:00:00+08:00',source_url='https://example.invalid/fixture.pdf',source_sha256=SHA)
    return CashDividend(**{**d,**kw})


def data():
    bars=[dict(date=f'2024-05-{i}',open=100.,high=101.,low=90.,close=100.,volume=1_000_000) for i in range(13,18)]
    payload=lambda:{'bars':copy.deepcopy(bars),'source':'unit_test_fixture','fetched_at':'2024-05-18',
                    'is_synthetic':False,'adjustment':'none','volume_unit':'shares'}
    return {'000333':payload(),'000300':payload()}


def run():
    return {'curve':[{'date':f'2024-05-{i}','nav':10000} for i in range(13,18)],
            'fills':[{'id':'buy-before-record','date':'2024-05-14','side':'buy','symbol':'000333','qty':100},
                     {'id':'sell-on-ex','date':'2024-05-15','side':'sell','symbol':'000333','qty':100}]}


def audit(events=None, r=None, **kwargs):
    return fixed_path_overlay(data(),['000333'],r or run(),events or [event()],{SHA:PDF},starts_flat=True,**kwargs)


def test_record_date_entitlement_receivable_and_payment_not_current_position():
    r=audit();rows={x['date']:x for x in r['curve']}
    assert rows['2024-05-14']['fixed_path_nav_before_dividend_tax']=='10000.00'
    assert rows['2024-05-15']['unpaid_gross_receivable']=='300.00'
    assert rows['2024-05-15']['paid_cash_gross']=='0'
    assert rows['2024-05-17']['paid_cash_gross']=='300.00' and rows['2024-05-17']['unpaid_gross_receivable']=='0.00'
    assert r['entitlements']['fixture-dividend']['qty']==100
    assert not r['usable_for_gate'] and not r['action_coverage_complete'] and not r['is_strategy_replay']
    assert r['tax_model']=='unresolved_not_net_return' and audit()==r


def test_buying_on_ex_date_does_not_create_entitlement():
    r=run();r['fills']=[{**r['fills'][0],'date':'2024-05-15'}]
    assert audit(r=r)['gross_cash_paid']=='0.00'


def test_payment_on_ex_day_books_once_without_double_counting_receivable():
    r=audit([event(pay_date='2024-05-15')]);rows={x['date']:x for x in r['curve']}
    assert r['gross_cash_paid']=='300.00' and r['gross_receivable']=='0.00'
    assert rows['2024-05-15']['fixed_path_nav_before_dividend_tax']=='10300.00'
    assert [x['kind'] for x in r['entries']]==['gross_receivable','gross_payment']


def test_bitemporal_revision_does_not_leak_future_revision_or_backfilled_observation():
    original=event(observed_at='2026-10-30T12:00:00+08:00')
    revision=replace(original,revision=2,cash_per_share_gross='4',available_at='2024-05-16T12:00:00+08:00')
    validate_evidence([original,revision],{SHA:PDF})
    assert visible_events([original,revision],'2024-05-15T12:00:00+08:00')==[]
    assert visible_events([original,revision],'2024-05-15T12:00:00+08:00',mode='historical_reconstruction')==[original]
    assert visible_events([original,revision],'2024-05-17T12:00:00+08:00',mode='historical_reconstruction')==[revision]
    assert audit([original])['gross_cash_paid']=='0'
    assert audit([original],mode='historical_reconstruction')['gross_cash_paid']=='300.00'
    with pytest.raises(ValueError,match='修订'):audit([original,revision],mode='historical_reconstruction')


def test_late_event_and_source_or_fill_tampering_fail_closed():
    with pytest.raises(ValueError,match='迟到'):audit([event(observed_at='2024-05-16T12:00:00+08:00')])
    with pytest.raises(ValueError,match='缺失或已被修改'):validate_evidence([event()],{SHA:PDF+b'changed'})
    with pytest.raises(ValueError,match='重复事件'):audit([event(),event()])
    r=run();r['fills']*=2
    with pytest.raises(ValueError,match='成交标识重复'):audit(r=r)
    r=run();r['curve'][1]=r['curve'][0]
    with pytest.raises(ValueError,match='净值日期'):audit(r=r)
    r=run();r['fills']=r['fills'][1:]
    with pytest.raises(ValueError,match='期初持仓'):audit(r=r)


def test_ex_reference_uses_price_adjustment_not_cash_and_not_other_symbols():
    others=event(event_id='other',symbol='600519',price_adjustment_per_share='30.876')
    r=ex_reference('71',[event(),others],'2024-05-15',symbol='000333',asof='2024-05-15T09:00:00+08:00')
    assert r==Decimal('68.02') and r!=Decimal('68.00')
    with pytest.raises(ValueError,match='开盘后'):ex_reference('71',[event()],'2024-05-15',symbol='000333',asof='2024-05-15T10:00:00+08:00')


def test_gross_return_signal_uses_economic_cash_without_future_adjustment():
    bars=[{'date':'2024-05-14','close':100.},{'date':'2024-05-15','close':97.}]
    r=gross_return_index(bars,[event()]);assert r[-1]['gross_return_index']==1.
    future=event(event_id='future',record_date='2024-05-16',ex_date='2024-05-17',pay_date='2024-05-17')
    assert gross_return_index(bars,[event(),future])==r
    with pytest.raises(ValueError,match='重复事件'):gross_return_index(bars,[event(),event()])
    revised=replace(event(),revision=2,cash_per_share_gross='4',available_at='2024-05-16T10:00:00+08:00',observed_at='2024-05-16T11:00:00+08:00')
    with pytest.raises(ValueError,match='修订'):gross_return_index(bars+[{'date':'2024-05-16','close':98.}],[event(),revised])


def test_overlay_never_changes_original_prices_fills_or_nav():
    ds=data();r=run();original=copy.deepcopy((ds,r))
    fixed_path_overlay(ds,['000333'],r,[event()],{SHA:PDF},starts_flat=True)
    assert (ds,r)==original


@pytest.mark.parametrize('kw',[
    {'cash_per_share_gross':3.},{'cash_per_share_gross':'NaN'}, {'cash_per_share_gross':'-1'},
    {'revision':True},{'kind':'stock_split'},{'pay_date':'2024-05-14'},
    {'available_at':'2024-05-09T12:00:00'}, {'source_sha256':'missing'},
    {'source_url':'https://user:secret@example.invalid/a.pdf'},
])
def test_invalid_or_unsupported_events_rejected(kw):
    with pytest.raises(ValueError):event(**kw)
