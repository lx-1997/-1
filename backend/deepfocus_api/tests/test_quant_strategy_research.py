"""Research correctness, leakage barriers and unchanged risk constraints."""
import copy
from datetime import date, timedelta
import pytest
from deepfocus_api import quant_research_engine as e
from deepfocus_api.quant_research_candidates import decision, PROFILES
from deepfocus_api.quant_research_analysis import attribution
from deepfocus_api import quant_research_study as study


def data(count=420):
    bars=[];d=date(2023,1,2)
    for i in range(count):
        p=20+i*.02
        bars.append({'date':(d+timedelta(days=i)).isoformat(),'open':p,'high':p+.2,'low':p-.2,'close':p+.1,'volume':1_000_000})
    def payload(rows):return {'bars':rows,'source':'unit_test_fixture','fetched_at':'2026-01-01','is_synthetic':False,'adjustment':'none','volume_unit':'shares'}
    return {'600519':payload(bars),'000300':payload([{**b,'open':20+i*.005,'high':20.1+i*.005,'low':19.9+i*.005,'close':20+i*.005} for i,b in enumerate(bars)])}


@pytest.mark.parametrize('profile',PROFILES[1:])
def test_profiles_keep_incremental_replay_and_do_not_use_future_or_current_close(profile):
    ds=data();p=e.CANDIDATES[0];kw={'profile':profile,'benchmark':'000300'}
    first=e.replay(ds,['600519'],p,end=ds['600519']['bars'][200]['date'],**kw)
    rest=e.replay(ds,['600519'],p,state=first['state'],**kw)
    whole=e.replay(ds,['600519'],p,**kw)
    assert whole['fills'], 'leakage checks must exercise an active strategy'
    assert whole['state']==rest['state'] and whole['fills']==first['fills']+rest['fills']
    changed=copy.deepcopy(ds)
    for b in changed['600519']['bars'][201:]:
        for k in ('open','high','low','close'):b[k]*=.5
    assert e.replay(changed,['600519'],p,end=ds['600519']['bars'][200]['date'],**kw)==first
    day=ds['600519']['bars'][200]['date'];changed=copy.deepcopy(ds)
    for s in changed:changed[s]['bars'][200].update(close=100.,high=101.)
    assert e.replay(changed,['600519'],p,end=day,**kw)['fills']==first['fills']


def test_market_filter_and_volatility_scale_are_prior_only_and_downward_only():
    ds=data(100);prior={'600519':ds['600519']['bars']};market=copy.deepcopy(ds['000300']['bars'])
    limits=e.ASSUMPTIONS
    for b in market:b['close']=20.
    off=decision('trend_guard',prior,market,{'600519':.1},0.,100,limits)
    assert off['market_risk_off'] and not off['weights']
    market[-1]['close']=21.
    on=decision('risk_scaled',prior,market,{'600519':.1},0.,100,limits)
    assert not on['market_risk_off'] and 0<on['weights']['600519']<=.2
    noisy=copy.deepcopy(prior)
    for i,b in enumerate(noisy['600519']):b['close']=20*(1.2 if i%2 else .8)
    reduced=decision('risk_scaled',noisy,market,{'600519':.1},0.,100,limits)
    assert 0<reduced['weights']['600519']<on['weights']['600519']
    assert e.ASSUMPTIONS['drawdown_pause']==e.ASSUMPTIONS['stop_loss']==.08


def test_small_rebalances_removed_but_risk_exit_works_off_schedule():
    ds=data(100);p=e.CANDIDATES[0]
    old=e.replay(ds,['600519'],p)
    new=e.replay(ds,['600519'],p,profile='low_turnover',benchmark='000300')
    assert len(new['fills'])<len(old['fills'])
    assert sum(f['fee'] for f in new['fills'])<sum(f['fee'] for f in old['fills'])
    start=e.initial_state();start.update(cash=800_000.,positions={'600519':{'qty':10000,'cost':20.,'opened':'2022-01-01'}},peak=1_100_000.)
    ds=data(30);d=ds['600519']['bars'][11]['date']
    r=e.replay(ds,['600519'],p,start=d,end=d,state=start,profile='low_turnover',benchmark='000300')
    assert r['state']['paused'] and not r['state']['positions']
    assert r['fills'][0]['reason']=='risk_pause'


@pytest.mark.parametrize('profile',PROFILES[1:])
def test_seen_candidate_is_never_promotable_and_holdout_never_selects(profile):
    ds=data();a=e.walk_forward(ds,['600519'],'000300',profile=profile)
    start=a['holdout']['curve'][0]['date']
    for b in ds['600519']['bars']:
        if b['date']>=start:
            for k in ('open','high','low','close'):b[k]*=.5
    b=e.walk_forward(ds,['600519'],'000300',profile=profile)
    assert a['parameters']==b['parameters'] and a['folds']==b['folds']
    assert not a['gate']['passed'] and '禁止晋级' in a['gate']['reasons'][-1]
    assert a['protocol']['candidate_rules']['risk_limits'].endswith('permanent_drawdown8')


def test_attribution_reconciles_and_does_not_claim_causal_cost_replay():
    ds=data();run=e.replay(ds,['600519'],e.CANDIDATES[0]);a=attribution(ds,run,1_000_000)
    assert abs(sum(a['pnl_pct_by_symbol'].values())-run['metrics']['return_pct'])<.001
    assert a['fees_pct_initial']>0 and a['cost_addback_is_causal_replay'] is False


def manifest_and_data():
    ds=data(320);dates=e.validate_dataset(ds);params={'parameters':e.CANDIDATES[0]}
    manifest={'profile':'low_turnover','protocol':{'seen_through':dates[-1],
        'new_validation':{'minimum_common_completed_sessions':152}},'sources':study.source_pins(),
        'baskets':[{'symbols':['600519'],'benchmark':'000300','capital':1_000_000,
                    'sealed_data_digest':e.digest(ds),'sealed_cutoff':dates[-1],
                    'baseline':params,'challenger':params}]}
    manifest['manifest_digest']=e.digest(manifest)
    return manifest,ds


def test_forward_fails_closed_on_seen_short_changed_or_revised_data():
    m,ds=manifest_and_data();cutoff=e.validate_dataset(ds)[-1]
    r=study.validate_forward(m,[ds],[ds],completed_through=cutoff)
    assert r['results'][0]['sessions']==0 and not r['validated'] and not r['promotion_allowed']
    wrong=copy.deepcopy(m);wrong['profile']='risk_scaled'
    with pytest.raises(ValueError,match='清单'):study.validate_forward(wrong,[ds],[ds],completed_through=cutoff)
    revised=copy.deepcopy(ds);revised['600519']['bars'][0]['volume']+=1
    with pytest.raises(ValueError,match='修订'):study.validate_forward(m,[ds],[revised],completed_through=cutoff)
    with pytest.raises(ValueError,match='数量'):study.validate_forward(m,[ds],[],completed_through=cutoff)
    changed=copy.deepcopy(m);changed['sources']['quant_research_engine.py']='changed';changed.pop('manifest_digest');changed['manifest_digest']=e.digest(changed)
    with pytest.raises(ValueError,match='源码'):study.validate_forward(changed,[ds],[ds],completed_through=cutoff)


def test_forward_uses_only_completed_fixed_interval_and_frozen_parameters(monkeypatch):
    m,ds=manifest_and_data();extended=data(500);cutoff=e.validate_dataset(extended)[470]
    # The last 29 provider rows cannot make an unfinished observation window complete.
    r=study.validate_forward(m,[ds],[extended],completed_through=cutoff)
    assert r['results'][0]['sessions']==151 and not r['validated']
    def forbidden(*a,**kw):raise AssertionError('no refit in future validation')
    monkeypatch.setattr(study,'freeze_parameters',forbidden)
    cutoff=e.validate_dataset(extended)[471]
    r=study.validate_forward(m,[ds],[extended],completed_through=cutoff)
    assert r['results'][0]['sessions']==152 and r['results'][0]['runs']['challenger']['oos']['observations']==84
    assert r['results'][0]['runs']['challenger']['holdout']['observations']==63
    assert not r['promotion_allowed'] and not r['live_trading_enabled']
    later=study.validate_forward(m,[ds],[extended],completed_through=e.validate_dataset(extended)[-1])
    assert later==r


def test_paired_bootstrap_is_deterministic_and_fails_identical_series():
    a=[{'date':str(i),'nav':1+i*.001} for i in range(65)]
    x=study.paired_block_interval(a,a)
    assert x==study.paired_block_interval(a,a) and x['lower']==x['upper']==0
