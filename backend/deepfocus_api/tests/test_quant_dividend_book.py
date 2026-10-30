import copy
import hashlib
import json
from decimal import Decimal
from dataclasses import replace
import pytest
from deepfocus_api.quant_dividend_book import (initial_book,apply_day,tax_rate,INVESTOR,available_cash)
from deepfocus_api.quant_corporate_actions import CashDividend
from deepfocus_api.ai_fund_ledger import fee

BODY=b'unit-test-event-only';SHA=hashlib.sha256(BODY).hexdigest()
SESSIONS=['2024-05-13','2024-05-14','2024-05-15','2024-05-16','2024-05-17','2024-05-20']


def event(**kw):
    return CashDividend(**{**dict(event_id='fixture',revision=1,symbol='000333',record_date='2024-05-14',
        ex_date='2024-05-15',pay_date='2024-05-16',cash_per_share_gross='3',price_adjustment_per_share='2.9791154',
        available_at='2024-05-09T23:59:59+08:00',observed_at='2024-05-10T12:00:00+08:00',
        source_url='https://example.invalid/fixture.pdf',source_sha256=SHA),**kw})


def fill(d,side,qty=100,price=100,identity=None):
    return {'id':identity or d+side,'date':d,'symbol':'000333','side':side,'qty':qty,'price':price,'fee':fee(Decimal(str(price))*qty,side)}


def day(book,d,fills=(),events=None,**kw):
    return apply_day(book,d,list(fills),{'000333':100},events if events is not None else [event()],{SHA:BODY},SESSIONS,**kw)


def book():return initial_book('100000','2024-05-13',investor=INVESTOR)


@pytest.mark.parametrize('buy,sell,expected',[
    ('2023-01-08','2023-02-08','.20'),('2023-01-08','2023-02-09','.10'),
    ('2023-01-31','2023-02-28','.20'),('2023-01-31','2023-03-01','.10'),
    ('2023-01-08','2024-01-08','.10'),('2023-01-08','2024-01-09','0'),
    ('2024-02-29','2025-02-28','.10'),('2024-02-29','2025-03-01','0')])
def test_calendar_month_year_anniversaries_are_inclusive(buy,sell,expected):
    assert tax_rate(buy,sell,buy)==Decimal(expected)


def test_receivable_payment_fifo_tax_liability_and_next_session_settlement():
    b=day(book(),'2024-05-14',[fill('2024-05-14','buy')]);b=day(b,'2024-05-15',[fill('2024-05-15','sell')])
    assert b['claims']['fixture']['gross']=='300.00' and not b['claims']['fixture']['paid']
    assert b['tax_assessed']=='60.00' and b['tax_settled']=='0.00'
    assert b['valuation']['pending_dividend_tax']=='60.00'
    assert b['valuation']['dividend_receivable']=='300.00'
    assert b['valuation']['gross_nav']==str(Decimal(b['cash'])+300)
    b=day(b,'2024-05-16')
    assert b['paid_gross']=='300.00' and b['tax_settled']=='60.00'
    assert b['valuation']['dividend_receivable']==b['valuation']['pending_dividend_tax']=='0.00'
    assert b['valuation']['nav_after_assessed_dividend_tax']==b['cash']


def test_paid_dividend_not_spendable_without_future_tax_reserve():
    ev=event(pay_date='2024-05-15');b=day(book(),'2024-05-14',[fill('2024-05-14','buy')],events=[ev]);b=day(b,'2024-05-15',events=[ev])
    assert b['valuation']['unrealized_dividend_tax_reserve']=='60.00'
    assert available_cash(b,'2024-05-15')==Decimal(b['cash'])-60
    assert Decimal(b['valuation']['gross_nav'])-Decimal(b['valuation']['conservative_risk_nav'])==60


def test_fifo_uses_oldest_lots_for_partial_disposal():
    ds=['2023-01-09','2024-05-13','2024-05-14','2024-05-15']
    b=initial_book(100000,'2023-01-08',investor=INVESTOR)
    def step(b,d,fs):return apply_day(b,d,fs,{'000333':100},[event()],{SHA:BODY},ds)
    b=step(b,'2023-01-09',[fill('2023-01-09','buy',identity='old')])
    b=step(b,'2024-05-13',[]);b=step(b,'2024-05-14',[fill('2024-05-14','buy',identity='new')])
    b=step(b,'2024-05-15',[fill('2024-05-15','sell',qty=150)])
    assert b['tax_assessed']=='30.00'  # first100 tax-free, next50 20%
    assert [(l['id'],l['qty']) for l in b['lots']]==[('new',50)]


def test_day_is_atomic_idempotent_and_serializable_across_restart():
    initial=book();before=copy.deepcopy(initial);f=fill('2024-05-14','buy');b=day(initial,'2024-05-14',[f])
    assert initial==before
    assert day(b,'2024-05-14',[f])==b
    recovered=json.loads(json.dumps(b));assert day(recovered,'2024-05-15')==day(b,'2024-05-15')
    snapshot=copy.deepcopy(b)
    with pytest.raises(ValueError,match='改写'):day(b,'2024-05-14',[])
    with pytest.raises(ValueError,match=r'T\+1'):day(b,'2024-05-15',[fill('2024-05-15','sell',qty=200)])
    assert b==snapshot


def test_unsupported_intraday_netting_and_bad_fees_cannot_partially_commit():
    b=day(book(),'2024-05-14',[fill('2024-05-14','buy')]);before=copy.deepcopy(b)
    with pytest.raises(ValueError,match='反向'):day(b,'2024-05-15',[fill('2024-05-15','sell'),fill('2024-05-15','buy')])
    assert b==before
    f=fill('2024-05-15','sell');f['fee']=0
    with pytest.raises(ValueError,match='费用'):day(b,'2024-05-15',[f])


def test_bitemporal_mode_and_event_revision_never_rewrite_posted_rights():
    late=event(observed_at='2026-10-30T12:00:00+08:00')
    b=day(book(),'2024-05-14',[fill('2024-05-14','buy')],events=[late]);b=day(b,'2024-05-15',events=[late])
    assert not b['claims']
    recon=initial_book(100000,'2024-05-13',investor=INVESTOR,mode='historical_reconstruction')
    b=day(recon,'2024-05-14',[fill('2024-05-14','buy')],events=[late],mode='historical_reconstruction')
    b=day(b,'2024-05-15',events=[late],mode='historical_reconstruction');assert b['claims']['fixture']['gross']=='300.00'
    revised=replace(event(),revision=2,cash_per_share_gross='4',available_at='2024-05-15T12:00:00+08:00',observed_at='2024-05-15T13:00:00+08:00')
    original=day(book(),'2024-05-14',[fill('2024-05-14','buy')]);copy_before=copy.deepcopy(original)
    with pytest.raises(ValueError,match='修订'):day(original,'2024-05-15',events=[event(),revised])
    assert original==copy_before


def test_missing_calendar_snapshots_and_valuation_fail_closed():
    with pytest.raises(ValueError,match='跳过交易日'):day(book(),'2024-05-15')
    with pytest.raises(ValueError,match='估值'):apply_day(book(),'2024-05-14',[fill('2024-05-14','buy')],{},[event()],{SHA:BODY},SESSIONS)
    with pytest.raises(ValueError,match='个人'):initial_book(1000,'2024-05-13',investor='institution')
    with pytest.raises(ValueError,match='核验'):day(book(),'2027-01-01')


def test_sell_before_record_does_not_get_dividend_and_ex_day_buy_does_not_get_old_rights():
    b=initial_book(100000,'2024-05-12',investor=INVESTOR);b=day(b,'2024-05-13',[fill('2024-05-13','buy')]);b=day(b,'2024-05-14',[fill('2024-05-14','sell')]);b=day(b,'2024-05-15',[fill('2024-05-15','buy')]);b=day(b,'2024-05-16')
    assert b['paid_gross']=='0.00' and b['tax_assessed']=='0.00'


def test_nontrading_payment_day_must_be_observed_and_cannot_change_marks():
    ev=event(pay_date='2024-05-18');b=day(book(),'2024-05-14',[fill('2024-05-14','buy')],events=[ev])
    for d in ('2024-05-15','2024-05-16','2024-05-17'):b=day(b,d,events=[ev])
    with pytest.raises(ValueError,match='支付日'):day(b,'2024-05-20',events=[ev])
    with pytest.raises(ValueError,match='收盘估值'):day(b,'2024-05-18',events=[ev])
    b=apply_day(b,'2024-05-18',[],{},[ev],{SHA:BODY},SESSIONS)
    assert b['paid_gross']=='300.00' and b['prices']['000333']=='100'


def test_buy_cannot_spend_reserved_tax_even_with_sufficient_gross_cash():
    ev=event(pay_date='2024-05-15');b=day(book(),'2024-05-14',[fill('2024-05-14','buy')],events=[ev]);b=day(b,'2024-05-15',events=[ev])
    f=fill('2024-05-16','buy',price=902.60)
    assert Decimal(str(f['price']))*f['qty']+Decimal(str(f['fee']))<Decimal(b['cash'])
    with pytest.raises(ValueError,match='预留'):day(b,'2024-05-16',[f],events=[ev])


def test_partial_sales_do_not_multiply_rounding_of_dividend_tax():
    ev=event(cash_per_share_gross='0.00025');b=day(book(),'2024-05-14',[fill('2024-05-14','buy',qty=200)],events=[ev])
    b=day(b,'2024-05-15',[fill('2024-05-15','sell',identity='partial1'),fill('2024-05-15','sell',identity='partial2')],events=[ev])
    assert b['claims']['fixture']['gross']=='0.05' and b['tax_assessed']=='0.01'


def test_restored_book_cannot_change_code_or_knowledge_mode():
    b=day(book(),'2024-05-14',[fill('2024-05-14','buy')])
    with pytest.raises(ValueError,match='模式'):day(b,'2024-05-15',mode='historical_reconstruction')
    b['kernel']='unrelated-version'
    with pytest.raises(ValueError,match='版本'):day(b,'2024-05-15')
