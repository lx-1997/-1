import asyncio
import math
from datetime import datetime, timedelta
import pytest
from deepfocus_api.market_regime import BJ, UNIVERSE, normalize, daily_scores, build_gauge, simulate, backtest
from deepfocus_api import market_regime_data as data

NOW = datetime(2026,10,28,16,tzinfo=BJ)

def rows(n=450):
    out=[];day=datetime(2024,1,1)
    while len(out)<n:
        if day.weekday()<5:
            i=len(out);c=100*math.exp(.0003*i+.04*math.sin(i/17))
            out.append({'date':day.strftime('%Y-%m-%d'),'open':c*.998,'close':c,'volume':1000+100*math.cos(i/7)})
        day+=timedelta(days=1)
    return out

def series(): return {k:rows() for k in UNIVERSE}

def test_normalization_fails_closed_and_excludes_live_bar():
    r={'date':'2026-10-28','open':100,'close':101,'volume':1000}
    assert not normalize([r],NOW.replace(hour=14))
    assert normalize([r],NOW)==[r]
    assert not normalize([r,r],NOW)
    assert not normalize([{**r,'close':float('nan')}],NOW)
    assert not normalize([{**r,'volume':0}],NOW)
    assert not normalize([{**r,'date':'2026-10-29'}],NOW)

def test_prefix_invariance_no_future_features():
    s=series(); full=daily_scores(s)
    prefix=daily_scores({k:r[:350] for k,r in s.items()})
    assert prefix==full[:len(prefix)]
    for r in s['csi500'][350:]: r['close']*=3
    assert daily_scores(s)[:len(prefix)]==prefix

def test_missing_companion_not_silently_reweighted():
    s=series();s['sse']=[]
    assert daily_scores(s)==[]
    assert build_gauge(s,[],NOW)['available'] is False

def test_same_composite_trend_and_delta_stale_disclosure():
    g=build_gauge(series(),[],NOW)
    assert g['temperature']==g['temperature_trend'][-1]['temperature']
    assert g['temp_delta_1d']==g['temperature_trend'][-1]['temperature']-g['temperature_trend'][-2]['temperature']
    assert g['position_pct'] is None and g['signal_usable'] is False
    assert g['freshness']=='lagging'
    assert g['backtest']['adopted_for_advice'] is False

def test_conflict_blocks_current_signal():
    s=series();now=datetime.strptime(s['csi300'][-1]['date'],'%Y-%m-%d').replace(hour=16,tzinfo=BJ)
    g=build_gauge(s,[{'conflict':True}],now)
    assert g['signal_usable'] is False and g['regime_label']=='来源待核对'

def test_flat_roundtrip_both_sides_cost():
    d=[{'date':str(i),'open':100,'close':100,'temperature':100} for i in range(10)]
    r=simulate(d,1,len(d),10,1.)
    assert r['return_pct']==pytest.approx(((1-.001)/(1+.001)-1)*100,abs=.001)
    assert r['max_drawdown_pct']<0 and r['cost_pct_initial']>0
    assert simulate(d,1,len(d),0,1.)['return_pct']==0

def test_next_open_not_prior_close_and_no_same_day_signal():
    d=[{'date':str(i),'open':100,'close':100,'temperature':0} for i in range(4)]
    d[1].update(open=200,close=200,temperature=100)
    d[2].update(open=200,close=200)
    first=simulate(d,1,2,0)
    assert first['return_pct']==0 # cannot earn the gap before initial next-open fill
    assert first['avg_exposure_pct']==25 # prior close signal, not today's 100

def test_no_overnight_gain_when_initially_cash():
    d=[{'date':'a','open':100,'close':100,'temperature':100}, {'date':'b','open':200,'close':220,'temperature':100}]
    assert simulate(d,1,2,0,1.)['return_pct']==10

def test_cost_sensitivity_and_same_period_benchmark():
    d=daily_scores(series());bt=backtest(d)
    assert bt['available']
    assert bt['holdout']['strategy']['days']==bt['holdout']['benchmark']['days']
    assert bt['cost_sensitivity'][0]['return_pct']>=bt['cost_sensitivity'][-1]['return_pct']
    assert bt['holdout']['strategy']['curve'][0]['date']==bt['split_date']
    assert len({r['date'] for r in bt['holdout']['strategy']['curve']})==bt['holdout']['strategy']['days']

def test_exact_common_date_only_no_forward_fill():
    s=series();missing=s['sse'][200]['date'];s['sse'].pop(200)
    assert missing not in {r['date'] for r in daily_scores(s)}
    assert backtest(daily_scores(s))['available'] is False

@pytest.mark.asyncio
async def test_provider_selection_uses_freshest_not_first(monkeypatch):
    old=rows(440);new=rows(450)
    async def fake(client,vendor,key,now):return old if vendor=='eastmoney' else new
    monkeypatch.setattr(data,'vendor_series',fake)
    s,ss=await data.acquire(NOW)
    assert all(x['provider']=='sina' for x in ss)
    assert all(len(r)==450 for r in s.values())

@pytest.mark.asyncio
async def test_shared_refresh_and_cancelled_waiter(monkeypatch):
    calls=[];done=asyncio.Event()
    async def fake(now):
        calls.append(1);await done.wait();return series(),[]
    monkeypatch.setattr(data,'acquire',fake);monkeypatch.setattr(data,'_cache',(0,None));monkeypatch.setattr(data,'_inflight',None)
    a=asyncio.create_task(data.market_gauge_v4());b=asyncio.create_task(data.market_gauge_v4());await asyncio.sleep(.01)
    a.cancel()
    with pytest.raises(asyncio.CancelledError):await a
    done.set();result=await b
    assert result['available'] and len(calls)==1
