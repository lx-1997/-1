"""Quarantine new copies, preserve evidence/financial frames and source writes."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
from deepfocus_api import quant_research_store as jobs,quant_research_engine as engine,quant_recovery as recovery
from deepfocus_api import quant_calendar_store as calendar,quant_evidence_store as evidence
from deepfocus_api.tests.test_quant_exchange_schedule import reviewed
from deepfocus_api.tests.test_quant_research_loop import dataset,request
from deepfocus_api.tests.test_quant_event_api import sample,PDF,SHA


@pytest.fixture
def source_db(tmp_path,monkeypatch,reviewed):
    source=tmp_path/'source.sqlite3';monkeypatch.setenv('DEEPFOCUS_QUANT_RESEARCH_DB_PATH',str(source))
    data=dataset();result=engine.walk_forward(data,['600519'],'000300')
    parent,_=jobs.create('a','completed',request(),'test-kernel')
    jobs.update('a',parent['id'],status='completed',extra={'dataset':data,'result':result,'result_digest':engine.digest(result)})
    # Active portfolio from actual synthetic replay; retain an actual drawdown
    # pause with unsold shares at a final gap, never a strategy promotion.
    crashed=copy.deepcopy(data)
    for k in ('open','high','low','close'):crashed['600519']['bars'][-1][k]*=.3
    book=engine.replay(crashed,['600519'],engine.CANDIDATES[0])
    assert book['state']['paused'] and book['state']['positions'] and book['fills']
    paper=jobs.create_paper('a',parent['id'],{'state':book['state'],'fills':book['fills'],'curve':book['curve'],
        'dataset':crashed,'parameters':engine.CANDIDATES[0],'kernel':'test-kernel','request':request()})
    running,_=jobs.create('a','active-worker',request(),'test-kernel');assert jobs.claim_worker('a',running['id'],'held-test-worker')
    cancelled,_=jobs.create('a','cancelled-worker',request(),'test-kernel');assert jobs.claim_worker('a',cancelled['id'],'held-cancelled')
    jobs.cancel('a',cancelled['id'])
    calendar.put('a','calendar fixture',*reviewed)
    evidence.put('a','cash fixture',[sample()],{SHA:PDF})
    return source,parent,paper,running,cancelled


def rows(path,table):
    c=recovery.connect_ro(path)
    try:return [tuple(r) for r in c.execute('SELECT * FROM '+table+' ORDER BY 1')]
    finally:c.close()


def test_new_copy_pauses_operations_but_preserves_financial_payload_and_all_source_rows(source_db,tmp_path,monkeypatch):
    source,parent,paper,running,cancelled=source_db
    original={t:rows(source,t) for t in ('qr_experiment','qr_paper','qr_worker_slot','qr_calendar_source','qr_evidence_source')}
    workspace=tmp_path/'restored'
    actual=subprocess.run;calls=[]
    def independent(*args,**kwargs):calls.append(args[0]);return actual(*args,**kwargs)
    monkeypatch.setattr(recovery.subprocess,'run',independent)
    result=recovery.prepare(source,workspace)
    assert result['verified'] and result['quarantine_changes']=={'unfinished_jobs':1,'active_papers':1,'worker_slots':2}
    assert calls and calls[0][0]==sys.executable and 'verify' in calls[0]
    assert all(rows(source,t)==value for t,value in original.items())
    restored=workspace/'research.sqlite3';c=recovery.connect_ro(restored)
    try:
        p=c.execute('SELECT * FROM qr_paper WHERE id=?',(paper['id'],)).fetchone()
        assert p['status']=='paused' and p['revision']==paper['revision']+1
        assert json.loads(p['payload'])['state']==paper['state']
        assert json.loads(p['payload'])['state']['paused'] and json.loads(p['payload'])['state']['positions']
        assert c.execute('SELECT payload FROM qr_paper WHERE id=?',(paper['id'],)).fetchone()[0]==original['qr_paper'][0][-1]
        assert c.execute('SELECT status FROM qr_experiment WHERE id=?',(parent['id'],)).fetchone()[0]=='completed'
        assert c.execute('SELECT status FROM qr_experiment WHERE id=?',(running['id'],)).fetchone()[0]=='failed'
        assert c.execute('SELECT status FROM qr_experiment WHERE id=?',(cancelled['id'],)).fetchone()[0]=='cancelled'
        assert c.execute('SELECT count(*) FROM qr_worker_slot').fetchone()[0]==0
    finally:c.close()
    assert recovery.verify(workspace)['verified'] and not recovery.verify(workspace)['activation_authorized']
    assert (workspace/'READY.json').exists() and not (workspace/'FAILED.json').exists()
    assert workspace.stat().st_mode&0o077==0 and restored.stat().st_mode&0o077==0


@pytest.mark.parametrize('damage',['result','blob','owner_reference','unknown_table','mutation_trigger','paper_nan'])
def test_corruption_or_unknown_schema_refuses_ready_and_preserves_source(source_db,tmp_path,damage):
    source,parent,paper,_,_=source_db
    c=sqlite3.connect(source)
    try:
        if damage=='result':
            p=json.loads(c.execute('SELECT payload FROM qr_experiment WHERE id=?',(parent['id'],)).fetchone()[0]);p['result_digest']='0'*64
            c.execute('UPDATE qr_experiment SET payload=? WHERE id=?',(json.dumps(p),parent['id']))
        elif damage=='blob':
            c.execute('DROP TRIGGER qr_calendar_source_update');c.execute('UPDATE qr_calendar_source SET body=?',(b'changed',))
        elif damage=='owner_reference':
            c.execute('DROP TRIGGER qr_evidence_source_delete');c.execute('DELETE FROM qr_evidence_source')
        elif damage=='unknown_table':c.execute('CREATE TABLE unreviewed(value TEXT)')
        elif damage=='mutation_trigger':c.execute("CREATE TRIGGER unexpected BEFORE UPDATE ON qr_paper BEGIN SELECT RAISE(ABORT,'unreviewed');END")
        else:
            p=json.loads(c.execute('SELECT payload FROM qr_paper').fetchone()[0]);p['state']['cash']=float('nan')
            c.execute('UPDATE qr_paper SET payload=?',(json.dumps(p),))
        c.commit()
    finally:c.close()
    before=rows(source,'qr_paper');workspace=tmp_path/('failure-'+damage)
    with pytest.raises((ValueError,sqlite3.DatabaseError)):recovery.prepare(source,workspace)
    assert (workspace/'FAILED.json').exists() and not (workspace/'READY.json').exists()
    assert rows(source,'qr_paper')==before


@pytest.mark.parametrize('damage',['database','receipt','verifier_version','ready','verification','receipt_metadata'])
def test_verification_rejects_tampering_after_preparation(source_db,tmp_path,monkeypatch,damage):
    source,*_=source_db;workspace=tmp_path/'verified';recovery.prepare(source,workspace)
    if damage=='database':
        c=sqlite3.connect(workspace/'research.sqlite3');c.execute("UPDATE qr_paper SET status='active'");c.commit();c.close()
    elif damage=='receipt':
        p=workspace/'receipt.json';v=json.loads(p.read_text());v['baseline']['tables']['qr_paper']['expected_digest']='0'*64;p.write_text(json.dumps(v))
    elif damage=='verifier_version':monkeypatch.setattr(recovery,'pins',lambda:{'wrong':'version'})
    elif damage=='ready':
        p=workspace/'READY.json';v=json.loads(p.read_text());v['activation_authorized']=True;p.write_text(json.dumps(v))
    elif damage=='verification':
        p=workspace/'verification.json';v=json.loads(p.read_text());v['verified']=False;p.write_text(json.dumps(v))
    else:
        p=workspace/'receipt.json';v=json.loads(p.read_text());v['created_at']='changed metadata';p.write_text(json.dumps(v))
    with pytest.raises(ValueError):recovery.verify(workspace)


@pytest.mark.parametrize('damage',['overwrite','relative','budget','timeout'])
def test_explicit_paths_new_workspace_and_byte_budget_are_required(source_db,tmp_path,damage):
    source,*_=source_db;workspace=tmp_path/'target'
    if damage=='overwrite':workspace.mkdir();(workspace/'preserve').write_text('keep')
    args={'max_bytes':1} if damage=='budget' else {'timeout':.000001} if damage=='timeout' else {}
    with pytest.raises((ValueError,TimeoutError)):recovery.prepare(Path('relative.sqlite3') if damage=='relative' else source,workspace,**args)
    if damage=='overwrite':assert (workspace/'preserve').read_text()=='keep'
    assert not (workspace/'READY.json').exists()


def test_sqlite_wal_snapshot_contains_committed_rows_without_checkpointing_source(source_db,tmp_path):
    source,*_=source_db;writer=sqlite3.connect(source);writer.execute('PRAGMA journal_mode=WAL');writer.execute('PRAGMA wal_autocheckpoint=0')
    try:
        # A terminal row in a live WAL is part of the backup, not just .sqlite3.
        writer.execute("UPDATE qr_experiment SET stage='committed-in-wal' WHERE status='completed'");writer.commit()
        assert Path(str(source)+'-wal').exists()
        workspace=tmp_path/'wal-restored';recovery.prepare(source,workspace)
        c=recovery.connect_ro(workspace/'research.sqlite3')
        try:assert c.execute("SELECT stage FROM qr_experiment WHERE status='completed'").fetchone()[0]=='committed-in-wal'
        finally:c.close()
    finally:writer.close()


def test_cli_uses_existing_absolute_source_and_never_activates(source_db,tmp_path):
    source,*_=source_db;workspace=tmp_path/'cli'
    script=Path(__file__).resolve().parents[3]/'scripts/quant-recovery.py'
    result=subprocess.run([sys.executable,str(script),'prepare','--source',str(source),'--workspace',str(workspace)],capture_output=True,text=True,check=True)
    report=json.loads(result.stdout);assert report['verified'] and not report['activation_authorized']
    result=subprocess.run([sys.executable,str(script),'verify','--workspace',str(workspace)],capture_output=True,text=True,check=True)
    assert json.loads(result.stdout)['verified']
