import copy
from dataclasses import replace
from datetime import date, timedelta
import hashlib
import json
import pytest
from deepfocus_api import quant_event_replay as e, quant_dividend_book as book
from deepfocus_api.quant_corporate_actions import CashDividend

BODY=b'unit-test-fixture-not-an-actual-disclosure';SHA=hashlib.sha256(BODY).hexdigest();SOURCE={SHA:BODY}
PARAM={'lookback':10,'threshold':0.0}


def fixture(count=90,dividend=10):
    days=[];d=date(2024,1,2)
    while len(days)<count:
        if d.weekday()<5:days.append(d.isoformat())
        d+=timedelta(days=1)
    rows=[]
    for i,d in enumerate(days):
        p=100+i*.2-(dividend if i>=31 else 0)
        rows.append(dict(date=d,open=p,close=p+.1,high=p+1,low=p-1,volume=10_000_000))
    def payload(bars):return {'source':'unit_test_fixture','fetched_at':'2024-06-01','is_synthetic':False,
                            'adjustment':'none','volume_unit':'shares','bars':bars}
    data={'000333':payload(rows),'000300':payload([{**b,'open':100.,'close':100.,'high':101.,'low':99.} for b in rows])}
    event=CashDividend(event_id='fixture',revision=1,symbol='000333',record_date=days[30],ex_date=days[31],pay_date=days[35],
        cash_per_share_gross=str(dividend),price_adjustment_per_share=str(dividend),
        available_at=days[20]+'T18:00:00+08:00',observed_at=days[20]+'T18:01:00+08:00',
        source_url='https://example.invalid/fixture.pdf',source_sha256=SHA)
    return data,event,days


def replay(ds,events,**kw):
    return e.replay(ds,['000333'],PARAM,events,SOURCE,investor=book.INVESTOR,**kw)


def test_event_replay_has_active_signal_orders_and_cash_receipts():
    ds,event,days=fixture();r=replay(ds,[event])
    assert r['fills'] and any(x['kind']=='dividend_receivable' for x in r['state']['book']['events'])
    assert float(r['state']['book']['paid_gross'])>0
    assert all(f['signal_date']<f['date'] and f['qty']%100==0 for f in r['fills'])
    assert not r['gate']['passed'] and not r['gate']['commercial_ready']
    assert r['signal_basis']=='prior_completed_gross_return_index'
    assert float(r['state']['book']['cash'])>=0


def test_future_prices_and_current_close_cannot_drive_current_fills():
    ds,event,days=fixture();cut=days[50];a=replay(ds,[event],end=cut)
    other=copy.deepcopy(ds)
    for row in other['000333']['bars'][51:]:
        for key in ('open','close','high','low'):row[key]*=.5
    assert replay(other,[event],end=cut)==a
    first=days[11];before=replay(ds,[event],end=first)
    other=copy.deepcopy(ds);other['000333']['bars'][11].update(close=125.,high=126.)
    after=replay(other,[event],end=first)
    assert before['fills'] and before['fills']==after['fills']


def test_prefix_append_and_json_restore_match_one_shot_exactly():
    ds,event,days=fixture();cut=days[50]
    prefix={s:{**p,'bars':p['bars'][:51]} for s,p in ds.items()}
    a=replay(prefix,[event],end=cut);state=json.loads(json.dumps(a['state']))
    b=replay(ds,[event],state=state);whole=replay(ds,[event])
    assert a['fills']+b['fills']==whole['fills']
    assert a['curve']+b['curve']==whole['curve'] and b['state']==whole['state']
    assert replay(ds,[event],state=b['state'])['fills']==[]
    assert replay(ds,[event],state=b['state'])['state']==b['state']


def test_historical_revisions_and_changed_plan_cannot_resume():
    ds,event,days=fixture();a=replay(ds,[event],end=days[50]);snapshot=copy.deepcopy(a['state'])
    changed=copy.deepcopy(ds);changed['000333']['bars'][20]['volume']+=1
    with pytest.raises(ValueError,match='修订'):replay(changed,[event],state=a['state'])
    with pytest.raises(ValueError,match='修订'):replay(ds,[replace(event,cash_per_share_gross='11')],state=a['state'])
    with pytest.raises(ValueError,match='计划'):e.replay(ds,['000333'],{'lookback':20,'threshold':0.},[event],SOURCE,investor=book.INVESTOR,state=a['state'])
    assert a['state']==snapshot


def test_ex_date_execution_uses_reference_adjustment_and_avoids_false_raw_limit():
    ds,event,days=fixture(dividend=25);r=replay(ds,[event]);m={x['date']:x for x in ds['000333']['bars']}
    assert m[event.ex_date]['open']/m[event.record_date]['close']-1 < -.10
    assert any(f['date']==event.ex_date for f in r['fills'])
    assert not any(b['date']==event.ex_date and b['reason']=='price_limit' for b in r['blocked'])
    assert not r['state']['paused']


def test_drawdown_pause_is_permanent_after_rebound_and_blocks_new_risk():
    ds,event,days=fixture();crash=days[45]
    for row in ds['000333']['bars'][45:46]:
        for key in ('open','close','high','low'):row[key]*=.4
    r=replay(ds,[event]);assert r['state']['paused']
    assert not any(f['side']=='buy' and f['date']>=crash for f in r['fills'])
    assert any(b['date']==crash and b['reason']=='price_limit' for b in r['blocked'])


def test_weekend_payment_can_advance_without_a_new_market_session():
    ds,event,days=fixture();fri=next(d for d in days[35:] if date.fromisoformat(d).weekday()==4)
    saturday=(date.fromisoformat(fri)+timedelta(days=1)).isoformat()
    monday=(date.fromisoformat(fri)+timedelta(days=3)).isoformat();event=replace(event,pay_date=saturday)
    a=replay(ds,[event],end=fri);assert a['state']['book']['paid_gross']=='0.00'
    b=replay(ds,[event],state=a['state'],end=saturday)
    assert b['curve']==[] and b['state']['book']['cursor']==saturday and float(b['state']['book']['paid_gross'])>0
    c=replay(ds,[event],state=b['state'],end=monday);whole=replay(ds,[event],end=monday)
    assert c['state']==whole['state'] and a['fills']+b['fills']+c['fills']==whole['fills']


def test_unobserved_weekend_event_never_changes_past_state_or_fills():
    ds,event,days=fixture();sat=next(date.fromisoformat(d)+timedelta(days=1) for d in days[35:] if date.fromisoformat(d).weekday()==4)
    unknown=replace(event,pay_date=sat.isoformat(),observed_at='2026-10-30T12:00:00+08:00')
    assert replay(ds,[unknown])==replay(ds,[])


def test_schedules_need_past_training_and_original_gap():
    ds,event,days=fixture();schedule=[dict(train_end=days[20],test_start=days[26],test_end=days[-1],parameters=PARAM)]
    assert replay(ds,[event],start=days[26],schedule=schedule)['fills']
    schedule[0]['train_end']=days[25]
    with pytest.raises(ValueError,match='五日'):replay(ds,[event],start=days[26],schedule=schedule)


def test_book_open_never_credits_close_payment_and_phases_remain_atomic():
    ds,event,days=fixture();event=replace(event,pay_date=event.ex_date)
    r=replay(ds,[event],end=event.record_date);previous=r['state']['book'];before=copy.deepcopy(previous)
    opens={'000333':ds['000333']['bars'][31]['open']}
    opened=book.open_session(previous,event.ex_date,opens,[event],SOURCE,days)
    assert opened['paid_gross']=='0.00' and float(opened['valuation']['dividend_receivable'])>0
    closed=book.close_session(opened,opens,[event],SOURCE,days)
    assert float(closed['paid_gross'])>0 and closed['valuation']['dividend_receivable']=='0.00'
    assert previous==before
    with pytest.raises(ValueError,match='尚未收盘'):book.open_session(opened,days[32],opens,[event],SOURCE,days)
    with pytest.raises(ValueError,match='开盘'):book.execute_fills(closed,[])


def test_only_received_after_tax_cash_can_fund_later_orders():
    ds,event,days=fixture(dividend=45)
    symbols=['000001','000333','600036','600519','600900']
    for s in symbols:
        if s not in ds:ds[s]=copy.deepcopy(ds['000333'])
    early=[replace(event,event_id=s,symbol=s,pay_date=event.ex_date) for s in symbols]
    late=[replace(v,pay_date=days[35]) for v in early]
    kw=dict(investor=book.INVESTOR,end=days[32])
    a=e.replay(ds,symbols,PARAM,early,SOURCE,**kw);b=e.replay(ds,symbols,PARAM,late,SOURCE,**kw)
    assert not a['state']['paused'] and not b['state']['paused']
    before=lambda r:[{k:v for k,v in f.items() if k!='id'} for f in r['fills'] if f['date']<=event.ex_date]
    assert before(a)==before(b), 'same-day closing payment must not finance opening orders'
    bought=lambda r:sum(f['qty'] for f in r['fills'] if f['side']=='buy' and f['date']==days[32])
    assert bought(a)>bought(b)
    assert float(a['state']['book']['paid_gross'])>0 and b['state']['book']['paid_gross']=='0.00'
    assert all(float(f['available_cash_before'])>=f['qty']*f['price']+f['fee']-.001 for f in a['fills'] if f['side']=='buy')


def test_blocked_exit_does_not_allow_new_orders_to_exceed_gross_cap():
    ds,event,days=fixture(dividend=1);symbols=['000001','000333','600036','600519','600900']
    for s in symbols:
        if s not in ds:ds[s]=copy.deepcopy(ds['000333'])
    # A declining close removes A from tomorrow's targets; tomorrow's further
    # limit-down open blocks its exit while the remaining four seek 20% each.
    a=ds['000001']['bars'];previous=a[39]['close']
    a[40].update(open=previous,close=previous*.91,high=previous+1,low=previous*.90)
    p=a[40]['close'];a[41].update(open=p*.90,close=p*.90,high=p*.91,low=p*.89)
    r=e.replay(ds,symbols,PARAM,[],{},investor=book.INVESTOR,end=days[41])
    assert any(b['date']==days[41] and b['symbol']=='000001' and b['side']=='sell' and b['reason']=='price_limit' for b in r['blocked'])
    today=[f for f in r['fills'] if f['date']==days[41]];assert today
    gross=sum(l['qty']*next(b['open'] for b in ds[l['symbol']]['bars'] if b['date']==days[41]) for l in r['state']['book']['lots'])
    assert gross<=float(today[0]['opening_risk_nav'])*.80+.01
