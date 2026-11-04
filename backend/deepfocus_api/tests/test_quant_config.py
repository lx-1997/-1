"""Active-path configuration, costs, leakage and immutable validation contracts."""
import copy
from datetime import date,timedelta
import pytest
from deepfocus_api import quant_research_config as c, quant_research_engine as e


def data(count=420):
    bars=[];d=date(2023,1,2)
    while len(bars)<count:
        if d.weekday()<5:
            i=len(bars);p=20+i*.04
            bars.append(dict(date=d.isoformat(),open=p,high=p+.3,low=p-.2,close=p+.1,volume=1_000_000))
        d+=timedelta(days=1)
    def payload(rows):return dict(bars=rows,source='isolated_test_only',fetched_at='2026-01-01',is_synthetic=False,adjustment='none',volume_unit='shares')
    return {'600519':payload(bars),'000300':payload([{**b,'open':20+i*.005,'close':20+i*.005,'high':20.1+i*.005,'low':19.9+i*.005} for i,b in enumerate(bars)])}


def replay(config,ds=None,**kw):
    return e.replay(ds or data(),['600519'],{'lookback':10,'threshold':0.},configuration=config,benchmark='000300',**kw)


@pytest.mark.parametrize('changes',[{'holdout_days':1},{'test_days':2},{'drawdown_pause':.09},{'stop_loss':.09},{'gross_weight':.9},{'single_weight':.3},{'slippage_bps':0},{'commission_rate':0},{'minimum_commission':0},{'volume_participation':.1},{'rebalance_days':True},{'lookbacks':[10,10]},{'thresholds':[float('nan')]},{'vol_target':float('inf')},{'max_holdings':2.5},{'strategy':'shell'},{'version':'v2'}])
def test_invalid_or_relaxed_parameters_rejected(changes):
    with pytest.raises(ValueError):c.validate(changes)


def test_default_is_exact_legacy_and_inputs_not_mutated():
    ds=data();before=copy.deepcopy(ds);cfg=copy.deepcopy(c.DEFAULT)
    a=e.walk_forward(ds,['600519'],'000300')
    b=e.walk_forward(ds,['600519'],'000300',configuration=cfg)
    assert a==b and ds==before and cfg==c.DEFAULT
    assert a['holdout']['fills'] and a['protocol']['holdout_days']==63 and a['protocol']['gap_days']==5


def test_configured_candidates_and_locked_gates():
    r=e.walk_forward(data(),['600519'],'000300',configuration={'lookbacks':[5,15],'thresholds':[.01],'rebalance_days':5})
    assert r['protocol']['candidates']==[{'lookback':5,'threshold':.01},{'lookback':15,'threshold':.01}]
    assert len(r['selection_trials'])==2 and all(x['parameters']['lookback'] in (5,15) for x in r['folds'])
    assert (r['protocol']['train_min'],r['protocol']['test_days'],r['protocol']['gap_days'],r['protocol']['holdout_days'])==(126,42,5,63)
    assert not r['gate']['passed'] and any('自定义' in x for x in r['gate']['reasons'])
    assert r['protocol']['configuration_digest']==e.digest(c.validate({'lookbacks':[5,15],'thresholds':[.01],'rebalance_days':5}))


def test_config_changes_real_fills_costs_and_filters():
    base=replay({'rebalance_days':2});slow=replay({'rebalance_days':10,'rebalance_band':.02})
    assert base['fills'] and slow['fills'] and len(slow['fills'])<len(base['fills'])
    expensive=replay({'rebalance_days':2,'minimum_commission':50,'slippage_bps':50})
    assert expensive['fills'][0]['price']>base['fills'][0]['price'] and expensive['fills'][0]['fee']>=50
    assert expensive['state']['cash']!=base['state']['cash']
    tight=replay({'single_weight':.1,'gross_weight':.1})
    assert tight['fills'][0]['qty']<base['fills'][0]['qty']
    assert not replay({'min_price':1000})['fills']
    assert not replay({'min_turnover':1e10})['fills']
    assert c.charge(100000,'sell',c.validate({'sell_stamp_rate':.001,'transfer_rate':.0001}))==135.


@pytest.mark.parametrize('strategy',['momentum','low_turnover','trend_guard','risk_scaled'])
def test_templates_incremental_no_lookahead_and_config_state_binding(strategy):
    ds=data();cfg={'strategy':strategy,'rebalance_days':5,'rebalance_band':.02};cut=ds['600519']['bars'][200]['date']
    a=replay(cfg,ds,end=cut);b=replay(cfg,ds,state=a['state']);whole=replay(cfg,ds)
    assert a['fills'] and whole['state']==b['state'] and whole['fills']==a['fills']+b['fills']
    changed=copy.deepcopy(ds)
    for bar in changed['600519']['bars'][201:]:
        for key in ('open','high','low','close'):bar[key]*=.5
    assert replay(cfg,changed,end=cut)==a
    changed=copy.deepcopy(ds);changed['600519']['bars'][200].update(close=100,high=101)
    assert replay(cfg,changed,end=cut)['fills']==a['fills']
    with pytest.raises(ValueError):replay({**cfg,'rebalance_days':6},ds,state=a['state'])
    with pytest.raises(ValueError):e.replay(ds,['600519'],e.CANDIDATES[0],state=a['state'])


def test_max_holdings_and_total_cap_survive_blocked_sale():
    ds=data();ds['000001']=copy.deepcopy(ds['600519'])
    cfg={'max_holdings':1,'gross_weight':.1,'single_weight':.1}
    r=e.replay(ds,['600519','000001'],e.CANDIDATES[0],benchmark='000300',configuration=cfg)
    assert r['fills'] and all(f['symbol']=='000001' for f in r['fills'])
    assert max(row['exposure_pct'] for row in r['curve'])<10.2
    state=e.initial_state();state.update(cash=900000.,positions={'600519':{'qty':5000,'cost':20.,'opened':'2022-01-01'}},cursor=ds['600519']['bars'][10]['date'],configuration_digest=e.digest(c.validate(cfg)))
    day=ds['600519']['bars'][11]['date'];ds['600519']['bars'][11]['volume']=0
    blocked=e.replay(ds,['600519','000001'],e.CANDIDATES[0],benchmark='000300',configuration=cfg,state=state,end=day)
    assert not blocked['fills'] and list(blocked['state']['positions'])==['600519']


def test_risk_exit_not_delayed_to_rebalance():
    ds=data(100);cfg=c.validate({'rebalance_days':20,'stop_loss':.03,'drawdown_pause':.04})
    r=replay(cfg,ds,end=ds['600519']['bars'][40]['date']);assert r['fills']
    state=r['state'];day=ds['600519']['bars'][41]['date'];state['peak']=2_000_000
    x=replay(cfg,ds,state=state,end=day)
    assert x['state']['paused'] and any(f['reason']=='risk_pause' for f in x['fills'])
    assert not x['state']['positions']
