"""Isolated execution counterexamples, not evidence of strategy profitability."""
from dataclasses import replace
from datetime import datetime,timedelta,timezone
import pytest
from deepfocus_api import ai_fund as af,ai_fund_policy as policy,ai_fund_ledger as ledger


@pytest.fixture
def simulation(monkeypatch,tmp_path):
    monkeypatch.setattr(af,'_db_path',lambda:tmp_path/'paper.sqlite3')
    for name,value in [('_market_data',{}),('_our_content',[]),('_llm_narratives',{}),('_deposit_musing',None),
                       ('_run_debate',None),('_benchmark',None),('_latest_wire',[]),('_diverse_ammo',[])]:
        monkeypatch.setattr(af,name,lambda *a,_v=value,**k:_v)
    monkeypatch.setattr(af,'_market_regime_now',lambda:{'regime':'bull'})
    monkeypatch.setattr(af,'_in_session',lambda:True)
    monkeypatch.setattr(af.emotion_cycle,'current_cycle',lambda **kw:{'stage':'neutral'})
    monkeypatch.setattr(af.data_store,'record_confirmed',lambda *a,**k:True)
    monkeypatch.setattr(af,'_analyze',lambda *a,**k:dict(entry_ok=True,entry_threshold=0,score=.9,confidence=.9,
        thinking=[],scores={},tech={},buy_point='isolated fixture signal',fresh=False,regime_adapt={},model_ready=True))
    q=dict(latest=100,changeRatio=1,time=datetime.now(timezone.utc).isoformat())
    monkeypatch.setattr(af,'_quote',lambda code:q if code=='002594' else None)
    af.init_ai_fund_db()
    return q


def config(identity='main'):
    return replace(af.cfg_for(identity),pool={'002594':'比亚迪'},muse=False,debate=False)


def hold(fund_id='main',qty=100,avg=100):
    with af._connect() as c:
        c.execute('INSERT INTO aif_position VALUES(?,?,?,?,?,?,?,?)',
            (fund_id,'002594','比亚迪',qty,avg,'2026-01-01T01:00:00+00:00','2026-01-01T01:00:00+00:00',avg))
        c.execute('UPDATE aif_state SET cash=cash-? WHERE fund_id=?',(qty*avg,fund_id))
        c.execute('INSERT OR REPLACE INTO aif_marks VALUES(?,?,?)',('002594',avg,'2026-01-01T01:00:00+00:00'))
        c.commit()


@pytest.mark.parametrize('identity',[c.fund_id for c in af.ROSTER])
def test_historical_drawdown_breach_cannot_resume_after_rebound(simulation,identity):
    with af._connect() as c:
        ledger.record(c,identity,'2026-01-02T01:00:00+00:00',900000,1000000)
        ledger.record(c,identity,'2026-01-03T01:00:00+00:00',1000000,1000000)
        c.commit()
    out=af.run_tick(cfg=config(identity))
    assert out['traded']==[]
    with af._connect() as c:
        assert not af._positions(c,identity)
        assert c.execute('SELECT worst FROM aif_risk_state WHERE fund_id=?',(identity,)).fetchone()[0]<=-8


@pytest.mark.parametrize('invalid_time', ['future','stale'])
def test_unexecutable_quote_cannot_raise_trailing_high(simulation,invalid_time):
    hold();simulation['latest']=250
    delta=timedelta(hours=1) if invalid_time=='future' else -timedelta(days=1)
    simulation['time']=(datetime.now(timezone.utc)+delta).isoformat()
    af.run_tick(trade=False,cfg=config())
    with af._connect() as c:assert af._positions(c)[0]['high_water']==100


def test_older_quote_cannot_sell_against_newer_trusted_mark(simulation):
    hold();simulation.update(latest=80,changeRatio=-3,time=(datetime.now(timezone.utc)-timedelta(seconds=30)).isoformat())
    with af._connect() as c:
        c.execute('UPDATE aif_marks SET price=100,ts=?',(datetime.now(timezone.utc).isoformat(),));c.commit()
    assert af.run_tick(cfg=config())['traded']==[]
    with af._connect() as c:assert af._positions(c)[0]['qty']==100


def test_paused_account_can_still_reduce_risk(simulation):
    hold();simulation.update(latest=88,changeRatio=-3)
    with af._connect() as c:
        ledger.record(c,'main','2026-01-02T01:00:00+00:00',900000,1000000);c.commit()
    out=af.run_tick(cfg=config())
    assert [t['side'] for t in out['traded']]==['sell']
    with af._connect() as c:assert not af._positions(c)


def test_same_clock_conflicting_price_is_neither_executable_nor_valuation(simulation):
    hold();stamp=simulation['time']
    with af._connect() as c:
        c.execute('UPDATE aif_marks SET price=100,ts=?',(stamp,));c.commit()
    simulation['latest']=250
    assert af.run_tick(cfg=config())['traded']==[]
    with af._connect() as c:
        assert af._positions(c)[0]['high_water']==100
        assert c.execute('SELECT price FROM aif_marks').fetchone()[0]==100
        assert policy.valuation_price(c,'main',af._positions(c)[0],simulation)==(100,True,'last_trusted_mark')


def test_exact_boundary_sticks_but_one_cent_above_does_not(simulation):
    from deepfocus_api import ai_fund_execution as execution
    with af._connect() as c:
        assert not execution.risk_status(c,'main',920000.01,1000000)['paused']
        assert execution.risk_status(c,'main',920000,1000000)['paused']
        ledger.record(c,'main','2026-01-02T01:00:00+00:00',920000,1000000)
        assert execution.risk_status(c,'main',1000000,1000000)['paused']
        c.commit()
    af.init_ai_fund_db()  # Restart/initialization cannot remove the historical breach.
    assert af.run_tick(cfg=config())['risk_control']['paused']


@pytest.mark.parametrize('sealed,t_plus_one', [(True,False),(False,True)])
def test_pause_cannot_override_sell_constraints(simulation,sealed,t_plus_one):
    hold(qty=10000);simulation.update(latest=80,changeRatio=-10 if sealed else -3)
    with af._connect() as c:
        if t_plus_one:
            c.execute('UPDATE aif_position SET opened_at=?',(datetime.now(timezone.utc).isoformat(),))
        c.commit()
    out=af.run_tick(cfg=config())
    assert out['risk_control']['paused'] and out['traded']==[]
    with af._connect() as c:assert af._positions(c)[0]['qty']==10000


def test_pre_fill_observation_is_not_erased_by_exit_fees(simulation):
    # New pre-fill peak would disappear if only the post-fee account were recorded.
    hold(qty=10000);simulation.update(latest=110,changeRatio=3)
    with af._connect() as c:
        c.execute('UPDATE aif_position SET high_water=140');c.commit()
    out=af.run_tick(cfg=config())
    assert [t['side'] for t in out['traded']]==['sell']
    with af._connect() as c:
        assert c.execute('SELECT peak FROM aif_risk_state WHERE fund_id=?',('main',)).fetchone()[0]==1100000
        assert af._state(c)['cash']<1100000


def test_patterns_cannot_consume_today_partial_or_future_daily_bars(monkeypatch):
    today=datetime.now(policy.BJ).date()
    rows=[dict(d=(today+timedelta(days=offset)).isoformat(),o=100,h=101,l=99,c=100,v=10000)
          for offset in (-2,-1,0,1)]
    seen=[]
    def pattern(bars):seen.append(list(bars));return {'detected':False}
    monkeypatch.setattr(af.bull_playbook,'cup_with_handle',pattern)
    monkeypatch.setattr(af.bull_playbook,'livermore_pivot',pattern)
    af._analyze('某股',dict(latest=100,changeRatio=0),[],{'ohlc':rows})
    assert len(seen)==2 and all(bars==rows[:2] for bars in seen)


def test_completed_history_is_chronological_and_duplicates_fail_closed():
    now=datetime(2026,10,30,2,tzinfo=timezone.utc)
    bars=[dict(d='2026-10-29',c=102),dict(d='2026-10-28',c=101),dict(d='bad',c=500)]
    assert policy.completed_closes({'ohlc':bars},now)==[101,102]
    with pytest.raises(ValueError,match='Duplicate'):
        policy.completed_bars({'ohlc':bars+[bars[0]]},now)


def test_impact_uses_last_completed_bar_not_partial_or_future(simulation,monkeypatch):
    today=datetime.now(policy.BJ).date()
    rows=[dict(d=(today+timedelta(days=offset)).isoformat(),c=100,v=100000)
          for offset in (-2,-1,0,1)]
    seen=[];original=af._impact_cost_bps
    monkeypatch.setattr(af,'_market_data',lambda *a,**k:{'002594':{'ohlc':rows}})
    def impact(bar,amount):seen.append(bar);return original(bar,amount)
    monkeypatch.setattr(af,'_impact_cost_bps',impact)
    assert af.run_tick(cfg=config())['traded']
    assert seen==[rows[1]]


def test_newly_committed_losses_are_rechecked_before_opening_risk(simulation,monkeypatch):
    def meanwhile(*args,**kwargs):
        with af._connect() as c:
            for i in range(2):
                c.execute('INSERT INTO aif_trade(id,fund_id,ts,symbol,name,side,qty,price,amount,pnl_pct,reason) '
                          'VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                          ('fixture-loss-'+str(i),'main',datetime.now(timezone.utc).isoformat(),'600000','隔离测试',
                           'sell',100,95,9500,-5,'fixture closed loss'))
            c.commit()
        return {}
    monkeypatch.setattr(af,'_market_data',meanwhile)
    assert af.run_tick(cfg=config())['traded']==[]
