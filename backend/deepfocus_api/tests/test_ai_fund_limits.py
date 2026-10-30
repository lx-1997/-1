"""Dated rule and active execution fixtures, not historical security coverage."""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import pytest
from deepfocus_api import ai_fund as af, ai_fund_policy as policy, ai_fund_limits as limits
from deepfocus_api import ai_fund_market as market, ai_fund_ledger as ledger, ai_fund_audit as audit
from deepfocus_api.tests.market_fixture import source_fixture


@pytest.mark.parametrize('code,name,day,pct',[
    ('600519','ST fixture','2026-07-03',5.),('002883','*ST fixture','2026-07-05',5.),
    ('600519','ST fixture','2026-07-06',10.),('002883','*ST fixture','2026-10-30',10.),
    ('300368','ST fixture','2024-05-14',20.),('301001','*ST fixture','2026-07-03',20.),
    ('688001','*ST fixture','2024-05-14',20.),('689009','ST fixture','2026-07-06',20.),
    ('830799','ST fixture','2026-07-06',30.),('920001','*ST fixture','2026-07-06',30.),
    ('000001','ordinary fixture','2024-07-01',10.),('600519','','2026-10-30',10.)])
def test_standard_rules_use_board_then_effective_date(code,name,day,pct):
    assert limits.standard_pct(code,name,at=day)==pct


@pytest.mark.parametrize('at',[None,'invalid',datetime(2026,7,6), '2023-04-09'])
def test_missing_naive_or_uncovered_clock_fails(at):
    with pytest.raises(ValueError):limits.standard_pct('600519','ST fixture',at=at)


def test_beijing_date_controls_effective_boundary_not_utc_date_or_wall_clock():
    assert limits.standard_pct('600519','ST fixture',at='2026-07-05T15:59:59+00:00')==5
    assert limits.standard_pct('600519','ST fixture',at='2026-07-05T16:00:00+00:00')==10
    assert limits.standard_pct('600519','ST fixture',at=date(2026,7,3))==5


@pytest.mark.parametrize('code',['60051','600519.SH','HK00700','abcdef','510300'])
def test_unknown_security_type_is_not_assumed_main_board(code):
    with pytest.raises(ValueError):limits.standard_pct(code,'ST fixture',at='2026-07-06')


@pytest.fixture
def controlled(tmp_path,monkeypatch):
    clock=[datetime(2026,7,6,2,tzinfo=timezone.utc)]
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return clock[0].astimezone(tz) if tz else clock[0].replace(tzinfo=None)
    monkeypatch.setattr(af,'datetime',Clock);monkeypatch.setattr(policy,'datetime',Clock);monkeypatch.setattr(market,'datetime',Clock)
    monkeypatch.setattr(af,'utc_now_iso',lambda:clock[0].isoformat())
    monkeypatch.setattr(af,'_db_path',lambda:tmp_path/'paper.sqlite3')
    for name,value in [('_our_content',[]),('_llm_narratives',{}),('_deposit_musing',None),
                       ('_run_debate',None),('_benchmark',None),('_latest_wire',[]),('_diverse_ammo',[])]:
        monkeypatch.setattr(af,name,lambda *a,_v=value,**k:_v)
    monkeypatch.setattr(af,'_market_regime_now',lambda:{'regime':'bull'})
    monkeypatch.setattr(af,'_in_session',lambda *a:True)
    monkeypatch.setattr(af,'_is_trading_day',lambda *a:True)
    monkeypatch.setattr(af.emotion_cycle,'current_cycle',lambda **k:{'stage':'neutral'})
    monkeypatch.setattr(af.data_store,'record_confirmed',lambda *a,**k:True)
    monkeypatch.setattr(af,'_analyze',lambda *a,**k:dict(entry_ok=True,entry_threshold=0,score=.9,confidence=.9,
        thinking=[],scores={},tech={},buy_point='synthetic fixture',fresh=False,regime_adapt={},model_ready=True))
    def configure(code,day,change,*,side='buy',quote_name='*ST fixture',pool_name='ordinary pool',same_day=False,paused=False):
        clock[0]=datetime.fromisoformat(day+'T02:00:00+00:00')
        previous=clock[0]-timedelta(days=1)
        while previous.weekday()>=5:previous-=timedelta(days=1)
        q={'latest':100*(1+change/100),'changeRatio':change,'time':clock[0].isoformat()}
        if quote_name is not None:q['name']=quote_name
        data=source_fixture(code,{'ohlc':[{'d':previous.date().isoformat(),'c':100.,'v':1000000}]})
        monkeypatch.setattr(af,'_quote',lambda s:q if s==code else None)
        monkeypatch.setattr(af,'_market_data',lambda *a,**k:{code:data})
        cfg=replace(af.cfg_for('main'),pool={code:pool_name},muse=False,debate=False)
        af.init_ai_fund_db()
        if side=='sell':
            acquired=clock[0] if same_day else previous
            with af._connect() as c:
                c.execute('INSERT INTO aif_position VALUES(?,?,?,?,?,?,?,?)',
                    ('main',code,pool_name,100,110,acquired.isoformat(),acquired.isoformat(),110))
                c.execute('UPDATE aif_state SET cash=cash-11000 WHERE fund_id=?',('main',))
                c.execute('INSERT INTO aif_position_basis VALUES(?,?,?)',('main',code,'11000.00'))
                c.execute('INSERT OR REPLACE INTO aif_marks VALUES(?,?,?)',(code,100,previous.isoformat()))
        if paused:
            with af._connect() as c:ledger.record(c,'main',previous.isoformat(),900000,1000000)
        return cfg,q
    return configure


@pytest.mark.parametrize('side,code,day,change,expected',[
    ('buy','002883','2026-07-03',6,False),('buy','002883','2026-07-06',6,True),
    ('sell','002883','2026-07-03',-6,False),('sell','002883','2026-07-06',-6,True),
    ('buy','300368','2026-07-03',6,True),('sell','300368','2026-07-03',-6,True),
    ('buy','688001','2026-07-03',6,True),('sell','688001','2026-07-03',-6,True),
    ('buy','300368','2026-07-03',19.9,False),('sell','300368','2026-07-03',-19.9,False),
    ('buy','002883','2026-07-06',9.9,False),('sell','002883','2026-07-06',-9.9,False)])
def test_real_tick_uses_dated_current_quote_name_for_buy_and_sell(controlled,side,code,day,change,expected):
    cfg,q=controlled(code,day,change,side=side)
    out=af.run_tick(cfg=cfg)
    assert bool(out['traded'])==expected,out
    if expected:assert [t['side'] for t in out['traded']]==[side]
    with af._connect() as c:
        stored=audit.load(c,'main',out['audit']['id'])
        assert stored['sections']['inputs']['quotes'][code]['name']==q['name']
        assert 'ai_fund_limits.py' in stored['sections']['source']['files']
        assert bool(af._positions(c))==(expected if side=='buy' else not expected)
        if out['traded']:
            assert c.execute('SELECT policy_version FROM aif_trade').fetchone()[0]==policy.VERSION
    assert out['policy']['historical_security_status_verified'] is False


@pytest.mark.parametrize('same_day,paused,expected',[(True,False,False),(False,True,True)])
def test_correct_standard_limit_does_not_override_T1_or_resume_paused_risk(controlled,same_day,paused,expected):
    cfg,_=controlled('002883','2026-07-06',-6,side='sell',same_day=same_day,paused=paused)
    out=af.run_tick(cfg=cfg)
    assert bool(out['traded'])==expected
    if paused:
        assert out['risk_control']['paused']
        with af._connect() as c:assert c.execute('SELECT worst FROM aif_risk_state WHERE fund_id=?',('main',)).fetchone()[0]<=-8


def test_missing_quote_name_uses_partial_stored_name_not_false_verified_status(controlled):
    cfg,_=controlled('002883','2026-07-03',6,quote_name=None,pool_name='*ST stored fixture')
    out=af.run_tick(cfg=cfg)
    assert not out['traded'] and not out['policy']['historical_security_status_verified']


@pytest.mark.parametrize('side,price,change,fill,allowed',[
    ('buy',109.6,9.6,109.69,True),('buy',109.6,9.6,109.70,False),
    ('sell',90.4,-9.6,90.31,True),('sell',90.4,-9.6,90.30,False),
    ('buy',100,float('nan'),100,False)])
def test_tick_rounded_adverse_price_cannot_escape_same_conservative_proxy(side,price,change,fill,allowed):
    assert limits.fill_allowed(price,change,fill,side,limit_pct=10,buffer_pct=.3)==allowed


@pytest.mark.parametrize('side,change,expected',[('buy',9.6,False),('sell',-9.6,False),('buy',6,True),('sell',-6,True)])
def test_actual_tick_rechecks_after_impact_before_any_position_or_cash_mutation(controlled,monkeypatch,side,change,expected):
    cfg,_=controlled('002883','2026-07-06',change,side=side)
    monkeypatch.setattr(af,'_impact_cost_bps',lambda *a:100.)
    with af._connect() as c:before=(af._state(c)['cash'],[dict(p) for p in af._positions(c)])
    out=af.run_tick(cfg=cfg)
    assert bool(out['traded'])==expected
    if not expected:
        with af._connect() as c:
            assert af._state(c)['cash']==before[0]
            assert [(p['symbol'],p['qty'],p['avg_cost']) for p in af._positions(c)]==[(p['symbol'],p['qty'],p['avg_cost']) for p in before[1]]
            stored=audit.load(c,'main',out['audit']['id'])
            assert any(a['reason']=='effective_price_limit_proxy' for a in stored['sections']['candidates']['002883']['actions'])
