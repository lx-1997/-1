"""Tick evidence correctness/atomicity; synthetic fixtures never enter production."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime,timedelta,timezone
import json
import sqlite3
import subprocess
import sys
import zlib
import pytest
from deepfocus_api import ai_fund as af,ai_fund_audit as audit
from deepfocus_api.tests.test_ai_fund_execution_guards import simulation,config,hold
from deepfocus_api.tests.market_fixture import source_fixture,source_map

REAL_ANALYZE=af._analyze


@pytest.fixture
def research(simulation,monkeypatch):
    monkeypatch.setattr(af,'_analyze',REAL_ANALYZE)
    monkeypatch.setattr(af,'_sentiment',lambda items:.5)
    simulation.update(pe_ttm=20,pb=2,turnoverRatio=5)
    today=datetime.now(af.BJ_TZ).date()
    rows=[dict(d=(today-timedelta(days=40-i)).isoformat(),o=90+i*.2,c=90+i*.2,h=91+i*.2,l=89+i*.2,v=1000000) for i in range(40)]
    monkeypatch.setattr(af,'_market_data',lambda *a,**k:{'002594':source_fixture('002594',{'ohlc':rows,'flow5':300000000})})
    monkeypatch.setattr(af,'_our_content',lambda name:[dict(id='fixture-news',title='比亚迪中标大订单，业绩超预期',age_h=1,src='快讯')] if name=='比亚迪' else [])
    return simulation


def record(identity,fund='main'):
    with af._connect() as c:return audit.load(c,fund,identity)


def test_committed_tick_captures_all_candidates_facts_and_links_fills(research,monkeypatch):
    cfg=replace(config(),pool={'002594':'比亚迪','600000':'无报价测试'})
    out=af.run_tick(cfg=cfg);assert out['traded']
    r=record(out['audit']['id']);s=r['sections']
    assert set(s['candidates'])==set(cfg.pool)
    assert s['candidates']['600000']['actions'][0]['reason']=='missing_or_zero_quote'
    assert s['fills'][0]['audit_id']==out['audit']['id']
    assert s['inputs']['analyses']['002594']['sentiment_input']==.5
    assert s['before']['positions']==[] and s['after']['positions']
    assert r['manifest']['kernel']==audit.KERNEL and not r['manifest']['historical_backfill']
    monkeypatch.setattr(af,'_sentiment',lambda *a:pytest.fail('Offline verification fetched current reactions'))
    proof=audit.verify_analysis(r)
    assert proof['matched_analyses']==['002594'] and not proof['execution_replayed'] and not proof['oos_validation']
    with af._connect() as c:assert audit.verify_chain(c,'main')['verified_ticks']==1


def test_missing_quotes_and_unpriced_holdings_are_journaled(research,monkeypatch):
    monkeypatch.setattr(af,'_quote',lambda code:None)
    out=af.run_tick(cfg=config());assert out['reason']=='no_quotes'
    r=record(out['audit']['id']);assert r['manifest']['status']=='no_quotes'
    assert not r['sections']['after']['positions']
    assert audit.verify_analysis(r)['status']=='no_completed_analysis'
    with af._connect() as c:assert c.execute('SELECT count(*) FROM aif_nav').fetchone()[0]==0


def test_unpriced_account_does_not_create_ghost_fills(research):
    with af._connect() as c:
        c.execute('INSERT INTO aif_position VALUES(?,?,?,?,?,?,?,?)',('main','600000','缺失估值',100,100,'2026-01-01','2026-01-01',100));c.commit()
    out=af.run_tick(cfg=config());assert out['reason']=='unpriced_holdings'
    assert record(out['audit']['id'])['manifest']['status']=='unpriced_holdings'
    with af._connect() as c:assert c.execute('SELECT count(*) FROM aif_trade').fetchone()[0]==0


@pytest.mark.parametrize('fault',['append','quota','size'])
def test_audit_storage_failure_rolls_back_fills_cash_nav_and_outbox(research,monkeypatch,fault):
    if fault=='append':
        def fail(*a,**k):raise audit.AuditError('injected audit failure')
        monkeypatch.setattr(audit,'append',fail)
    elif fault=='quota':monkeypatch.setattr(audit,'MAX_STORED_BYTES',1)
    else:monkeypatch.setattr(audit,'MAX_RAW_BLOB',100)
    with pytest.raises(audit.AuditError):af.run_tick(cfg=config())
    with af._connect() as c:
        for table in ('aif_trade','aif_position','aif_nav','aif_trade_outbox','aif_tick_audit','aif_audit_blob'):
            assert c.execute('SELECT count(*) FROM '+table).fetchone()[0]==0
        assert af._state(c)['cash']==af._state(c)['started_nav']


def test_pre_commit_failure_has_failed_evidence_but_no_fills(research,monkeypatch):
    original=af.ledger.record;calls=[]
    def fail_after_fills(*a,**k):
        calls.append(1)
        if len(calls)==2:raise RuntimeError('injected final NAV failure')
        return original(*a,**k)
    monkeypatch.setattr(af.ledger,'record',fail_after_fills)
    with pytest.raises(RuntimeError,match='injected final NAV'):af.run_tick(cfg=config())
    with af._connect() as c:
        row=c.execute('SELECT id,status FROM aif_tick_audit').fetchone()
        assert row['status']=='failed' and c.execute('SELECT count(*) FROM aif_trade').fetchone()[0]==0
        r=audit.load(c,'main',row['id'])
        assert r['sections']['failure']=={'exception_type':'RuntimeError'}
        assert r['sections']['after_rollback']['positions']==[]


def test_post_commit_narration_failure_cannot_relabel_committed_fills(research,monkeypatch):
    def fail(*a,**k):raise RuntimeError('ancillary narration failed')
    monkeypatch.setattr(af,'_llm_narratives',fail)
    with pytest.raises(RuntimeError,match='ancillary'):af.run_tick(cfg=config())
    with af._connect() as c:
        rows=c.execute('SELECT status FROM aif_tick_audit').fetchall()
        assert [r['status'] for r in rows]==['committed']
        assert c.execute('SELECT count(*) FROM aif_trade').fetchone()[0]==1


def test_two_concurrent_ticks_have_one_fill_two_chained_records_and_shared_source(research):
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:af.run_tick(cfg=config()),range(2)))
    assert sum(len(r['traded']) for r in results)==1
    with af._connect() as c:
        assert audit.verify_chain(c,'main')['verified_ticks']==2
        assert c.execute('SELECT count(*) FROM aif_audit_blob WHERE sha=?',(audit.digest(audit.PINNED_SOURCE),)).fetchone()[0]==1
        assert c.execute('SELECT count(*) FROM aif_tick_audit').fetchone()[0]==2


def test_immutability_scope_and_idempotence(research):
    out=af.run_tick(cfg=config());r=record(out['audit']['id'])
    with af._connect() as c:
        for table in ('aif_tick_audit','aif_audit_blob'):
            for stmt in ('DELETE FROM '+table,'UPDATE '+table+' SET '+('status=status' if table=='aif_tick_audit' else 'body=body')):
                with pytest.raises(sqlite3.IntegrityError,match='immutable'):c.execute(stmt)
        with pytest.raises(LookupError):audit.load(c,'rock',out['audit']['id'])
        c.commit();c.execute('BEGIN IMMEDIATE')
        same=audit.append(c,out['audit']['id'],'main',r['manifest']['ts'],r['manifest']['status'],r['sections'])
        assert same['digest']==out['audit']['digest']
        with pytest.raises(audit.AuditError,match='different facts'):
            audit.append(c,out['audit']['id'],'main',r['manifest']['ts'],'failed',r['sections'])


def test_backup_restore_and_readonly_cli(research,tmp_path):
    out=af.run_tick(cfg=config());backup=tmp_path/'retained.sqlite3'
    with af._connect() as c:
        target=sqlite3.connect(backup)
        try:
            c.backup(target)
            assert target.execute('PRAGMA journal_mode=DELETE').fetchone()[0]=='delete'
        finally:target.close()
    cli=[sys.executable,'scripts/ai-fund-audit.py','verify-analysis','--db',str(backup),'--fund','main','--id',out['audit']['id']]
    response=subprocess.run(cli,capture_output=True,text=True)
    assert response.returncode==0,response.stdout+response.stderr
    assert json.loads(response.stdout)['analysis_count']==1
    with sqlite3.connect(backup) as c:
        c.row_factory=sqlite3.Row
        assert audit.verify_chain(c,'main')['verified_ticks']==1


def test_corrupt_compression_and_changed_source_are_rejected(research):
    out=af.run_tick(cfg=config());r=record(out['audit']['id'])
    r['manifest']['kernel']='different-source'
    with pytest.raises(audit.AuditError,match='differs'):audit.verify_analysis(r)
    with pytest.raises(audit.AuditError):audit._unpack({'raw_size':1,'body':zlib.compress(b'x'*(audit.MAX_RAW_BLOB+2))},'bad')
    with af._connect() as c:
        c.execute('DROP TRIGGER aif_audit_blob_update')  # isolated, disposable fault injection
        c.execute('UPDATE aif_audit_blob SET body=?',(b'injected corruption',))
        with pytest.raises(audit.AuditError):audit.load(c,'main',out['audit']['id'])


def test_manifest_corruption_and_missing_chain_predecessor_are_rejected(research):
    first=af.run_tick(cfg=config());second=af.run_tick(cfg=config())
    with af._connect() as c:
        c.execute('DROP TRIGGER aif_tick_audit_delete')
        c.execute('DELETE FROM aif_tick_audit WHERE id=?',(first['audit']['id'],))
        with pytest.raises(audit.AuditError,match='incomplete'):audit.load(c,'main',second['audit']['id'])
        c.execute('DROP TRIGGER aif_tick_audit_update')
        c.execute('UPDATE aif_tick_audit SET manifest=?',('{}',))
        with pytest.raises(audit.AuditError,match='integrity'):audit.load(c,'main',second['audit']['id'])


@pytest.mark.parametrize('fund',[cfg.fund_id for cfg in af.ROSTER])
def test_each_real_strategy_analysis_reproduces_with_captured_inputs(research,fund):
    out=af.run_tick(cfg=config(fund))
    r=record(out['audit']['id'],fund)
    assert audit.verify_analysis(r)['analysis_count']==1


def test_observation_only_and_risk_blocked_candidates_have_actual_reasons(research):
    out=af.run_tick(trade=False,cfg=config())
    r=record(out['audit']['id'])
    assert r['manifest']['status']=='observation_only'
    assert r['sections']['candidates']['002594']['actions']==[{'side':'buy','reason':'observation_only'}]
    with af._connect() as c:
        af.ledger.record(c,'main','2026-01-01T00:00:00+00:00',900000,1000000);c.commit()
    blocked=af.run_tick(cfg=config())
    actions=record(blocked['audit']['id'])['sections']['candidates']['002594']['actions']
    assert actions==[{'side':'buy','reason':'historical_drawdown_pause'}]


def test_losers_to_rank_and_daily_limit_are_retained(research,monkeypatch):
    source_quote=dict(research)
    monkeypatch.setattr(af,'_quote',lambda code:dict(source_quote))
    monkeypatch.setattr(af,'_market_data',lambda codes,**k:source_map({s:{} for s in codes}))
    monkeypatch.setattr(af,'_analyze',lambda *a,**k:dict(entry_ok=True,entry_threshold=0,score=.9,confidence=.9,
        thinking=[],scores={},tech={},buy_point='fixture',fresh=False,regime_adapt={}))
    cfg=replace(config(),pool={'002594':'比亚迪','600000':'第二候选'},max_daily_buys=1)
    out=af.run_tick(cfg=cfg);r=record(out['audit']['id'])
    assert len(out['traded'])==1 and r['sections']['inputs']['ranked_candidates']==['002594','600000']
    assert r['sections']['candidates']['600000']['actions']==[{'side':'buy','reason':'daily_buy_limit'}]


def test_export_is_private_and_never_overwrites(research,tmp_path):
    out=af.run_tick(cfg=config());dest=tmp_path/'private-proof.json'
    cmd=[sys.executable,'scripts/ai-fund-audit.py','export','--db',str(af._db_path()),'--fund','main','--id',out['audit']['id'],'--output',str(dest)]
    response=subprocess.run(cmd,capture_output=True,text=True)
    assert response.returncode==0,response.stdout+response.stderr
    assert dest.stat().st_mode&0o777==0o600
    assert json.loads(dest.read_text())['digest']==out['audit']['digest']
    again=subprocess.run(cmd,capture_output=True,text=True)
    assert again.returncode!=0 and json.loads(dest.read_text())['digest']==out['audit']['digest']


def test_nonfinite_input_is_preserved_as_invalid_and_cannot_claim_reproduction(research):
    out=af.run_tick(cfg=config());r=record(out['audit']['id'])
    assert audit.encode({'x':float('nan')})==b'{"x":{"__audit_nonfinite_float__":"nan"}}'
    r['sections']['inputs']['quotes']['002594']['latest']={'__audit_nonfinite_float__':'nan'}
    with pytest.raises(audit.AuditError,match='Nonfinite'):audit.verify_analysis(r)


def test_public_metadata_counts_unknown_history_without_exporting_inputs(research):
    with af._connect() as c:
        c.execute('INSERT INTO aif_trade(id,fund_id,ts,symbol,side,qty,price,amount,reason) VALUES(?,?,?,?,?,?,?,?,?)',
                  ('unversioned-fixture','main','2026-01-01','600000','buy',100,100,10000,'old fixture'))
        c.commit()
    out=af.run_tick(cfg=config())
    with af._connect() as c:
        public=audit.coverage(c,'main')
        assert public['linked_trades']==1 and public['unlinked_trades']==1
        assert public['latest_tick']['id']==out['audit']['id']
        assert 'sections' not in public and 'source' not in public and 'quotes' not in public
        old=c.execute('SELECT policy_version,audit_id FROM aif_trade WHERE id=?',('unversioned-fixture',)).fetchone()
        assert old['policy_version'] is None and old['audit_id'] is None
        assert audit.coverage(c,'rock')['latest_tick'] is None


def test_corrupt_latest_evidence_blocks_further_fills(research,monkeypatch):
    af.run_tick(cfg=config())
    with af._connect() as c:
        c.execute('DROP TRIGGER aif_audit_blob_update')
        c.execute('UPDATE aif_audit_blob SET body=?',(b'isolated corruption',));c.commit()
    monkeypatch.setattr(af,'_quote',lambda s:dict(research))
    monkeypatch.setattr(af,'_market_data',lambda codes,**k:source_map({s:{} for s in codes}))
    monkeypatch.setattr(af,'_analyze',lambda *a,**k:dict(entry_ok=True,score=.9,confidence=.9,thinking=[],scores={},
        tech={},buy_point='fixture',fresh=False,regime_adapt={}))
    cfg=replace(config(),pool={'002594':'比亚迪','600000':'第二候选'})
    with pytest.raises(audit.AuditError):af.run_tick(cfg=cfg)
    with af._connect() as c:
        assert c.execute('SELECT count(*) FROM aif_trade').fetchone()[0]==1
        assert c.execute('SELECT count(*) FROM aif_tick_audit').fetchone()[0]==1
        assert c.execute('SELECT count(*) FROM aif_position').fetchone()[0]==1


def test_capacity_metadata_warns_before_hard_limit(research,monkeypatch):
    af.run_tick(cfg=config())
    with af._connect() as c:
        usage=audit.storage(c)
        assert usage['used_bytes']>0 and usage['ticks']==1 and usage['status']=='ok'
        monkeypatch.setattr(audit,'MAX_STORED_BYTES',usage['used_bytes']+1)
        assert audit.storage(c)['status']=='near_capacity'
