from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as N
import sqlite3
import pytest
from deepfocus_api import ai_fund as af, ai_fund_policy as policy, ai_fund_ledger as ledger


def quote(price=100, **kw):
    return dict(latest=price, changeRatio=1, time=datetime.now(timezone.utc).isoformat(), **kw)


@pytest.mark.parametrize('changes', [dict(latest=0),dict(latest=-1),dict(latest=float('nan')),
    dict(time=None),dict(time=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()),
    dict(time=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()),
    dict(suspended=True),dict(changeRatio=None)])
def test_unexecutable_quotes(changes):
    assert not policy.executable({**quote(),**changes})


def test_quote_exchange_clock_is_used_not_fetch_clock():
    now=datetime(2026,10,29,2,tzinfo=timezone.utc)
    assert policy.executable(dict(latest=10,changeRatio=1,time='2026-10-29 10:00:00'),now)
    assert not policy.executable(dict(latest=10,changeRatio=1,time='2026-10-29 09:40:00'),now)


def test_intraday_bar_is_not_counted_twice_and_future_bars_are_excluded():
    now=datetime(2026,10,29,2,tzinfo=timezone.utc)
    md={'ohlc':[{'d':'2026-10-27','c':100},{'d':'2026-10-28','c':99},
                {'d':'2026-10-29','c':98},{'d':'2026-10-30','c':500}]}
    assert policy.completed_closes(md,now)==[100,99]


def test_no_previous_close_fill(monkeypatch):
    af._FREE_Q_CACHE.clear()
    monkeypatch.setattr('httpx.get',lambda *a,**k:N(text='var hq_str="测试股票,0,100,0,0,0";'))
    assert af._free_quote('002594') is None


def test_after_hours_valuation_prefers_timestamped_fallback(monkeypatch):
    stamp=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()
    monkeypatch.setattr(af,'cached_single_quote',lambda *a:{'latest':100,'pe_ttm':20})
    monkeypatch.setattr(af,'_free_quote',lambda *a:{'latest':90,'time':stamp,'changeRatio':-1})
    q=af._quote('002594')
    assert q['latest']==90 and q['time']==stamp and not policy.executable(q)


def test_target_event_recall_is_not_causation():
    items=[dict(title='盾安环境业绩超预期',age_h=1),dict(title='中芯国际：业绩超预期',age_h=1),
           dict(title='中芯国际：业绩超预期',age_h=2),dict(title='中芯国际大订单',age_h=-1),
           dict(title='中芯国际大订单',age_h=float('nan')),dict(title='中芯国际大订单',age_h=999)]
    selected=policy.direct_events('中芯国际',items,24)
    assert len(selected)==1 and selected[0]['relation']=='explicit_title_mention'


@pytest.mark.parametrize('title',['宁德时代供应商嘉元科技利润增长','嘉元科技：宁德时代产业链涨价'])
def test_supply_chain_mentions_are_not_direct_target_events(title):
    assert policy.direct_events('宁德时代',[dict(title=title,age_h=1)],24)==[]


def test_reverse_and_value_do_not_inherit_trend_exit():
    a=dict(tech=dict(ma20=105.7,vol=.02),score=.529,model_ready=True,scores={'质量':.5})
    assert policy.exit_reason(af.cfg_for('contra'),90,90,90,a,age_days=1) is None
    assert policy.exit_reason(af.cfg_for('rock'),90,90,90,a,age_days=1) is None
    assert '跌破20日线' in policy.exit_reason(af.MAIN_CFG,90,90,90,a,age_days=1)
    assert '硬止损' in policy.exit_reason(af.cfg_for('contra'),80,90,90,a)
    assert '企稳失效' in policy.exit_reason(af.cfg_for('contra'),89,90,90,{**a,'model_ready':False},age_days=2)
    assert '均值回归' in policy.exit_reason(af.cfg_for('contra'),106,90,106,a)


def test_risk_budget_counts_related_holdings_and_drawdown():
    holdings=[dict(symbol='688981',qty=3400)]
    # Only 1% of equity remains in the semiconductor bucket, despite free cash.
    assert policy.risk_budget('688256',1e6,660000,holdings,{'688981':100},-.1,1e6)==10000
    assert policy.risk_budget('002594',900000,900000,[],{},-.08,1e6)==0
    assert policy.risk_budget('002594',1e6,1e6,[],{},-.1,1e6)==50000


@pytest.mark.parametrize('code,budget,expected',[('688981',1999,0),('688981',2010,201),('002594',2010,200)])
def test_board_quantity_rules(code,budget,expected):
    assert policy.order_quantity(code,budget,10)==expected


def database():
    c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row
    c.executescript('CREATE TABLE aif_state(fund_id TEXT,started_nav REAL);'
                   'CREATE TABLE aif_trade(id TEXT,fund_id TEXT,symbol TEXT,side TEXT,ts TEXT,price REAL);'
                   'CREATE TABLE aif_nav(fund_id TEXT,ts TEXT,nav REAL);'
                   'CREATE TABLE aif_marks(symbol TEXT PRIMARY KEY,price REAL,ts TEXT);')
    c.execute("INSERT INTO aif_state VALUES('a',1000000)")
    ledger.initialize(c);return c


def test_nav_archive_and_drawdown_survive_raw_pruning():
    c=database()
    ledger.record(c,'a','2026-10-27T07:00:00+00:00',1100000,1000000)
    ledger.record(c,'a','2026-10-28T07:00:00+00:00',880000,1000000)
    ledger.record(c,'a','2026-10-29T07:00:00+00:00',1000000,1000000)
    c.execute('DELETE FROM aif_nav')
    ledger.initialize(c)
    assert c.execute('SELECT COUNT(*) FROM aif_nav_daily').fetchone()[0]==3
    assert ledger.drawdown(c,'a',1000000,1000000)==pytest.approx(-20)


def test_non_trading_days_do_not_inflate_risk_sample_size():
    c=database()
    ledger.record(c,'a','2026-10-30T07:00:00+00:00',1000000,1000000)
    ledger.record(c,'a','2026-10-31T07:00:00+00:00',1000000,1000000,trading_day=False)
    assert c.execute('SELECT COUNT(*) FROM aif_nav_daily').fetchone()[0]==1


def test_missing_quotes_keep_trusted_mark_and_never_average_cost():
    c=database();p=dict(symbol='002594',avg_cost=100)
    c.execute("INSERT INTO aif_marks VALUES('002594',80,'2026-10-29T07:00:00+00:00')")
    assert policy.valuation_price(c,'a',p,None)==(80,True,'last_trusted_mark')
    assert policy.valuation_price(c,'a',dict(symbol='600000',avg_cost=100),None)==(None,True,'unpriced')


def test_fee_model_is_explicit_and_sell_cost_is_higher():
    assert ledger.fee(10000,'buy')==5.10
    assert ledger.fee(10000,'sell')==10.10
    assert not policy.LEARNING_ACTIVE
    assert policy.audit_metadata()['live_trading_enabled'] is False


@pytest.fixture
def paper(monkeypatch,tmp_path):
    monkeypatch.setattr(af,'_db_path',lambda:tmp_path/'paper.sqlite3')
    monkeypatch.setattr(af,'AIF_REPORT_PAGE',tmp_path/'isolated-report.html')
    for name,result in [('_our_content',[]),('_market_data',{}),('_llm_narratives',{}),('_benchmark',None),
                        ('_latest_wire',[]),('_diverse_ammo',[]),('_run_debate',None),('_free_quote',None)]:
        monkeypatch.setattr(af,name,lambda *a,_r=result,**k:_r)
    monkeypatch.setattr(af,'_sentiment',lambda *a:None)
    monkeypatch.setattr(af,'_in_session',lambda *a:True)
    monkeypatch.setattr(af,'_market_regime_now',lambda:{'regime':'bull'})
    monkeypatch.setattr(af.emotion_cycle,'current_cycle',lambda **kw:{'stage':'neutral'})
    monkeypatch.setattr(af.data_store,'record_confirmed',lambda *a,**k:True)
    monkeypatch.setattr(af,'ifind_enabled',lambda:True)
    q=quote(pe_ttm=20,turnoverRatio=5)
    monkeypatch.setattr(af,'cached_single_quote',lambda s:q if str(s).startswith('002594') else None)
    monkeypatch.setattr(af,'_our_content',lambda name:[dict(id='n',title='比亚迪中标大订单，业绩超预期',age_h=1,src='快讯')] if name=='比亚迪' else [])
    af.init_ai_fund_db()
    return q


def test_net_ledger_and_policy_version_on_round_trip(paper):
    out=af.run_tick();assert len(out['traded'])==1
    with af._connect() as c:
        buy=c.execute("SELECT * FROM aif_trade WHERE side='buy'").fetchone()
        initial=af._state(c)['started_nav']
        cash=af._state(c)['cash'];p=af._positions(c)[0]
        assert cash+buy['amount']+buy['fee']==pytest.approx(initial)
        assert p['qty']*p['avg_cost']==pytest.approx(buy['amount']+buy['fee'])
        assert buy['policy_version']==policy.VERSION
        assert buy['amount']+buy['fee'] <= initial*policy.RISK_PER_TRADE/abs(af.HARD_STOP)
        c.execute("UPDATE aif_position SET opened_at='2026-10-01T01:00:00+00:00'")
        c.execute("UPDATE aif_state SET learned_weights=?", ('{"消息面":1.5}',));c.commit()
    paper['latest']=88;paper['changeRatio']=-5
    paper['time']=datetime.now(timezone.utc).isoformat()  # a new exchange observation
    af.run_tick()
    with af._connect() as c:
        sell=c.execute("SELECT * FROM aif_trade WHERE side='sell'").fetchone()
        assert sell is not None and sell['fee']>0
        assert sell['realized_pnl']==pytest.approx(sell['amount']-sell['fee']-buy['amount']-buy['fee'])
        assert af._state(c)['cash']==pytest.approx(initial+sell['realized_pnl'])
        assert af._state(c)['learned_weights']=='{"消息面":1.5}'
        assert not af._positions(c)


def test_old_or_unknown_clock_cannot_place_any_orders(paper):
    paper['time']='2020-01-01T01:00:00+00:00'
    assert af.run_tick()['traded']==[]
    with af._connect() as c:
        assert c.execute('SELECT COUNT(*) FROM aif_trade').fetchone()[0]==0


def test_stale_account_blocks_new_risk_without_nav_reset(paper,monkeypatch):
    af.run_tick()
    with af._connect() as c:
        prev=af._state(c)['cash'];last=c.execute('SELECT nav FROM aif_nav ORDER BY ts DESC LIMIT 1').fetchone()[0]
    monkeypatch.setattr(af,'cached_single_quote',lambda s:None)
    # Zero fresh quotes: do not manufacture a new mark or any fills.
    assert af.run_tick()['reason']=='no_quotes'
    with af._connect() as c:
        assert af._state(c)['cash']==prev
        assert c.execute('SELECT nav FROM aif_nav ORDER BY ts DESC LIMIT 1').fetchone()[0]==last
    snap=af.get_snapshot()
    assert snap['positions'][0]['valuation_stale']
    assert snap['positions'][0]['valuation_basis']=='last_trusted_mark'


def test_concurrent_ticks_do_not_duplicate_fills_or_restore_old_cash(paper):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as workers:
        results=list(workers.map(lambda _: af.run_tick(),range(2)))
    assert sum(len(r['traded']) for r in results)==1
    with af._connect() as c:
        buy=c.execute("SELECT * FROM aif_trade WHERE side='buy'").fetchone()
        assert af._state(c)['cash']+buy['amount']+buy['fee']==pytest.approx(af._state(c)['started_nav'])


def test_failed_nav_commit_never_publishes_ghost_fills(paper,monkeypatch):
    sent=[]
    monkeypatch.setattr(af.data_store,'record_confirmed',lambda *a,**k:sent.append(a) or True)
    def fail(*a,**k): raise RuntimeError('injected NAV failure')
    monkeypatch.setattr(ledger,'record',fail)
    with pytest.raises(RuntimeError,match='injected NAV failure'):
        af.run_tick()
    with af._connect() as c:
        assert c.execute('SELECT COUNT(*) FROM aif_trade').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM aif_trade_outbox').fetchone()[0]==0
        assert af._state(c)['cash']==af._state(c)['started_nav']
    assert sent==[]


def test_pending_ancillary_delivery_retries_without_another_fill():
    c=database();ledger.enqueue(c,'trade1','a','002594',{'side':'buy'});c.commit()
    def fail(*a,**k): raise RuntimeError('sink unavailable')
    ledger.flush_events(c,'a',fail)
    assert c.execute('SELECT delivered FROM aif_trade_outbox').fetchone()[0]==0
    sent=[]
    ledger.flush_events(c,'a',lambda *a,**k:sent.append(a) or True)
    ledger.flush_events(c,'a',lambda *a,**k:sent.append(a) or True)
    assert len(sent)==1 and sent[0][2]['trade_id']=='trade1'


def test_negative_write_ack_keeps_outbox_pending():
    c=database();ledger.enqueue(c,'trade1','a','002594',{'side':'buy'});c.commit()
    ledger.flush_events(c,'a',lambda *a,**k:False)
    assert c.execute('SELECT delivered FROM aif_trade_outbox').fetchone()[0]==0


def test_data_store_ack_matches_durable_write_result(monkeypatch):
    from deepfocus_api import data_store
    monkeypatch.setattr(data_store,'_record_values',lambda *a:False)
    assert data_store.record_confirmed('simulation_trade','002594',{'trade_id':'t'}) is False
    monkeypatch.setattr(data_store,'_record_values',lambda *a:True)
    assert data_store.record_confirmed('simulation_trade','002594',{'trade_id':'t'}) is True


def test_policy_upgrade_preserves_chronological_risk_archive():
    c=database()
    for stamp,nav in [('2026-10-27T07:00:00+00:00',900000),('2026-10-28T07:00:00+00:00',1100000)]:
        c.execute('INSERT INTO aif_nav VALUES(?,?,?)',('a',stamp,nav))
        ledger.record(c,'a',stamp,nav,1000000)
    before=dict(c.execute('SELECT peak,worst FROM aif_risk_state WHERE fund_id=?',('a',)).fetchone())
    assert before['worst']==pytest.approx(-10)
    c.execute('DELETE FROM aif_policy_epoch')
    c.execute('INSERT INTO aif_policy_epoch VALUES(?,?,?)',('a','paper-v2.0','2026-10-01T00:00:00+00:00'))
    ledger.initialize(c)
    assert dict(c.execute('SELECT peak,worst FROM aif_risk_state WHERE fund_id=?',('a',)).fetchone())==before
    assert c.execute('SELECT count(*) FROM aif_nav_daily').fetchone()[0]==2
    ledger.initialize(c)
    assert dict(c.execute('SELECT peak,worst FROM aif_risk_state WHERE fund_id=?',('a',)).fetchone())==before
