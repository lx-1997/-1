"""Replays real financial code on explicitly synthetic, isolated input frames."""
from dataclasses import replace
from datetime import datetime,timedelta,timezone
import copy
import pytest
from deepfocus_api import ai_fund as af,ai_fund_audit as audit,ai_fund_market as market
from deepfocus_api.ai_fund_replay import verify_execution
from deepfocus_api.tests.test_ai_fund_audit import research,record
from deepfocus_api.tests.test_ai_fund_execution_guards import simulation,config,hold
from deepfocus_api.tests.market_fixture import source_map

REAL_SESSION=af._in_session


@pytest.fixture
def frozen(research,monkeypatch):
    clock=[datetime(2026,10,30,2,tzinfo=timezone.utc)]
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return clock[0].astimezone(tz) if tz else clock[0].replace(tzinfo=None)
    monkeypatch.setattr(af,'datetime',Clock);monkeypatch.setattr(market,'datetime',Clock)
    monkeypatch.setattr(af,'utc_now_iso',lambda:clock[0].isoformat())
    monkeypatch.setattr(af,'_in_session',REAL_SESSION)
    research.update(latest=100.12,time=clock[0].isoformat())
    return research,clock


def verify(out,fund='main'):
    result=verify_execution(record(out['audit']['id'],fund))
    assert result['matched'],result
    assert result['accepted'] and result['financial_checks']['passed'],result
    assert result['execution_replayed'] and not result['production_modified'] and result['network_attempts']==0
    assert not result['oos_validation']
    return result


def test_buy_replays_actual_orders_ledger_and_outbox_without_changing_source_db(frozen):
    out=af.run_tick(cfg=config());assert len(out['traded'])==1
    with af._connect() as c:
        before=(af._state(c)['cash'],c.execute('SELECT count(*) FROM aif_tick_audit').fetchone()[0])
    result=verify(out);assert result['fills_reproduced']==1
    with af._connect() as c:assert before==(af._state(c)['cash'],c.execute('SELECT count(*) FROM aif_tick_audit').fetchone()[0])
    # A repeatable engine must also report independent record-level inconsistencies.
    assert result['financial_checks']['recorded_price_quantity_matches_amount'] is True


def test_candidate_tie_preserves_original_pool_order(frozen,monkeypatch):
    q,clock=frozen
    monkeypatch.setattr(af,'_quote',lambda symbol:dict(q))
    monkeypatch.setattr(af,'_market_data',lambda codes,**k:source_map({s:{'closes':[90+i*.2 for i in range(40)],'flow5':300000000} for s in codes}))
    monkeypatch.setattr(af,'_our_content',lambda name:[dict(id=name,title=name+'中标大订单，业绩超预期',age_h=1,src='快讯')])
    cfg=replace(config(),pool={'600000':'浦发银行','002594':'比亚迪'},max_daily_buys=1)
    out=af.run_tick(cfg=cfg);assert out['traded'][0]['symbol']=='600000'
    result=verify(out);assert result['comparisons']['inputs.ranked_candidates']


@pytest.mark.parametrize('fund',[c.fund_id for c in af.ROSTER])
def test_six_strategy_hard_stop_exits_reproduce_actual_fills(frozen,fund):
    q,clock=frozen;hold(fund_id=fund)
    q.update(latest=80,changeRatio=-3)
    out=af.run_tick(cfg=config(fund));assert [t['side'] for t in out['traded']]==['sell']
    assert verify(out,fund)['fills_reproduced']==1


def test_t_plus_one_then_next_day_exit_and_prior_trade_history_replay(frozen):
    q,clock=frozen;first=af.run_tick(cfg=config());assert first['traded']
    clock[0]+=timedelta(minutes=1);q.update(latest=80,changeRatio=-3,time=clock[0].isoformat())
    same_day=af.run_tick(cfg=config());assert same_day['traded']==[];verify(same_day)
    clock[0]=datetime(2026,11,2,2,tzinfo=timezone.utc);q['time']=clock[0].isoformat()
    next_day=af.run_tick(cfg=config());assert [t['side'] for t in next_day['traded']]==['sell']
    verify(next_day)
    r=record(next_day['audit']['id'])
    assert r['sections']['before']['position_basis']['002594'] is not None
    assert verify_execution(r)['financial_checks']['legacy_cost_basis_unverified']==0


def test_pause_and_lower_limit_cannot_be_turned_into_invented_exits(frozen):
    q,clock=frozen;hold(qty=10000);q.update(latest=80,changeRatio=-10)
    out=af.run_tick(cfg=config());assert out['traded']==[] and out['risk_control']['paused']
    assert verify(out)['fills_reproduced']==0


def test_source_clock_after_quote_acquisition_cannot_trade_outside_session(frozen,monkeypatch):
    q,clock=frozen;clock[0]=datetime(2026,10,30,3,29,tzinfo=timezone.utc)
    original=af._market_data
    def cross_lunch(*a,**k):
        value=original(*a,**k);clock[0]=datetime(2026,10,30,3,31,tzinfo=timezone.utc)
        q['time']='2026-10-30T03:30:00+00:00';return value
    monkeypatch.setattr(af,'_market_data',cross_lunch)
    out=af.run_tick(cfg=config());assert out['traded']==[]
    r=record(out['audit']['id']);assert r['sections']['entry_session_allowed'] and not r['sections']['effective_trade']
    verify(out)


def test_history_blocks_are_deduplicated_and_keep_sql_tie_order(frozen):
    with af._connect() as c:
        for i in range(257):
            c.execute('INSERT INTO aif_trade(id,fund_id,ts,symbol,name,side,qty,price,amount,pnl_pct,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                ('prior-'+str(i),'main','2025-01-02T02:00:00+00:00','600000','隔离历史','sell',100,100,10000,.1,'fixture'))
        c.commit()
    out=af.run_tick(cfg=config());r=record(out['audit']['id'])
    frame=r['sections']['execution_history'];assert frame['count']==257 and len(frame['chunks'])==2
    assert audit.history_rows(r['sections'])[0]['id']=='prior-0'
    verify(out)
    second=af.run_tick(trade=False,cfg=config());r2=record(second['audit']['id'])
    assert r['manifest']['refs']['_financial_history_0']==r2['manifest']['refs']['_financial_history_0']
    verify(second)


def test_unpriced_holdings_and_no_quotes_replay_without_fills(frozen,monkeypatch):
    with af._connect() as c:
        c.execute('INSERT INTO aif_position VALUES(?,?,?,?,?,?,?,?)',('main','600000','缺失估值',100,100,'2026-01-01','2026-01-01',100));c.commit()
    unpriced=af.run_tick(cfg=config());assert unpriced['reason']=='unpriced_holdings';verify(unpriced)
    monkeypatch.setattr(af,'_quote',lambda s:None)
    missing=af.run_tick(cfg=config());assert missing['reason']=='no_quotes';verify(missing)


def test_modified_record_and_missing_frame_are_rejected(frozen):
    out=af.run_tick(cfg=config());r=record(out['audit']['id'])
    changed=copy.deepcopy(r);changed['sections']['before']['state']['cash']+=1
    with pytest.raises(audit.AuditError,match='content'):verify_execution(changed)
    changed=copy.deepcopy(r);changed['sections'].pop('execution_history')
    changed['manifest']['refs'].pop('execution_history');changed['digest']=audit.digest(changed['manifest'])
    with pytest.raises(audit.AuditError,match='frame'):verify_execution(changed)


@pytest.mark.parametrize('damage',['price','fee','cash','quantity','basis'])
def test_independent_accounting_rejects_inconsistent_records(frozen,damage):
    from deepfocus_api.ai_fund_replay_worker import financial_checks
    out=af.run_tick(cfg=config());s=copy.deepcopy(record(out['audit']['id'])['sections'])
    assert financial_checks(s)['passed']
    if damage=='price':s['fills'][0]['price']+=.001
    elif damage=='fee':s['fills'][0]['fee']+=.01
    elif damage=='cash':s['after']['state']['cash']+=.01
    elif damage=='quantity':s['after']['positions'][0]['qty']+=1
    else:s['after']['position_basis']['002594']='1.00'
    assert not financial_checks(s)['passed']


def test_adverse_tick_and_preserved_legacy_cash_remainder(frozen):
    from decimal import Decimal
    from deepfocus_api import ai_fund_execution as execution
    assert execution.fill_price(100.12,5,'buy')==100.18
    assert execution.fill_price(100.12,5,'sell')==100.06
    with af._connect() as c:
        c.execute('UPDATE aif_state SET cash=1000000.000123 WHERE fund_id="main"');c.commit()
    out=af.run_tick(cfg=config());s=record(out['audit']['id'])['sections'];f=s['fills'][0]
    assert Decimal(str(s['before']['state']['cash']))-Decimal(str(s['after']['state']['cash']))==Decimal(str(f['amount']))+Decimal(str(f['fee']))
    assert s['after']['position_basis']['002594']==str(Decimal(str(f['amount']))+Decimal(str(f['fee'])))
    verify(out)


def test_legacy_basis_stays_unknown_and_replay_cli_survives_cold_backup(frozen,tmp_path):
    import sqlite3,subprocess,sys,json
    from pathlib import Path
    hold();out=af.run_tick(trade=False,cfg=config());r=record(out['audit']['id'])
    assert r['sections']['before']['position_basis']['002594'] is None
    assert not verify(out)['financial_checks']['opening_position_basis_complete']
    target=tmp_path/'cold.sqlite3'
    source=af._connect();backup=sqlite3.connect(target)
    try:
        source.backup(backup);backup.execute('PRAGMA journal_mode=DELETE')
    finally:backup.close();source.close()
    command=Path(__file__).resolve().parents[3]/'scripts/ai-fund-audit.py'
    run=subprocess.run([sys.executable,str(command),'verify-execution','--db',str(target),'--fund','main','--id',r['manifest']['id']],capture_output=True,text=True)
    assert run.returncode==0,run.stderr+run.stdout
    assert json.loads(run.stdout)['accepted']


def test_independent_realized_pnl_check_uses_saved_exact_basis(frozen):
    from deepfocus_api.ai_fund_replay_worker import financial_checks
    q,clock=frozen;af.run_tick(cfg=config())
    clock[0]=datetime(2026,11,2,2,tzinfo=timezone.utc);q.update(latest=80,changeRatio=-3,time=clock[0].isoformat())
    out=af.run_tick(cfg=config());s=copy.deepcopy(record(out['audit']['id'])['sections'])
    assert financial_checks(s)['passed'] and s['fills'][0]['side']=='sell'
    s['fills'][0]['realized_pnl']+=.01
    assert not financial_checks(s)['known_cost_basis_and_realized_pnl_match']


def test_holding_added_after_initial_pool_capture_is_audited_without_key_error(frozen,monkeypatch):
    q,clock=frozen;original=af._market_data
    def concurrent_holding(*a,**k):
        with af._connect() as c:
            c.execute('INSERT INTO aif_position VALUES(?,?,?,?,?,?,?,?)',('main','600000','并发持仓',100,100,'2026-01-01','2026-01-01',100))
            c.execute('INSERT INTO aif_marks VALUES(?,?,?)',('600000',100,clock[0].isoformat()));c.commit()
        return original(*a,**k)
    monkeypatch.setattr(af,'_market_data',concurrent_holding)
    out=af.run_tick(cfg=config());r=record(out['audit']['id'])
    assert '600000' not in r['sections']['inputs']['universe_order']
    assert r['sections']['candidates']['600000']['late_holding']
    assert r['sections']['candidates']['600000']['actions'][0]['reason']=='quote_clock_or_status_rejected'
    verify(out)


@pytest.mark.parametrize('day,expected_fills',[('2026-07-03',0),('2026-07-06',1)])
def test_dated_ST_limit_and_quote_name_replay_without_current_wall_clock(frozen,monkeypatch,day,expected_fills):
    from deepfocus_api.tests.market_fixture import source_fixture
    q,clock=frozen
    clock[0]=datetime.fromisoformat(day+'T02:00:00+00:00')
    q.update(name='*ST synthetic quote',changeRatio=6,time=clock[0].isoformat())
    today=clock[0].date()
    rows=[dict(d=(today-timedelta(days=40-i)).isoformat(),o=90+i*.2,c=90+i*.2,
               h=91+i*.2,l=89+i*.2,v=1000000) for i in range(40)]
    monkeypatch.setattr(af,'_market_data',lambda *a,**k:{'002594':source_fixture('002594',{'ohlc':rows,'flow5':300000000})})
    out=af.run_tick(cfg=config())
    assert len(out['traded'])==expected_fills,out
    result=verify(out)
    assert result['fills_reproduced']==expected_fills
    assert result['financial_checks']['passed']
