"""Storage clocks, owner isolation, immutable facts, rollback and cold recovery."""
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading

import pytest
from deepfocus_api import quant_financial_store as store, quant_recovery as recovery
from deepfocus_api.tests.test_quant_financial_vintages import fixture


@pytest.fixture
def ledger(tmp_path,monkeypatch):
    path=tmp_path/'research.sqlite3'
    monkeypatch.setenv('DEEPFOCUS_QUANT_RESEARCH_DB_PATH',str(path))
    monkeypatch.setattr(store,'_now',lambda:datetime.fromisoformat('2026-10-01T08:00:00+00:00'))
    return path


def read(identity,owner='a',clock='2026-10-02T00:00:00+08:00',mode='as_recorded'):
    return store.review(owner,identity,issuer_id='cninfo:9900002009',symbol='002109',period_end='2025-03-31',
                        as_of=clock,mode=mode,required_metrics=['parent_net_profit'])


def counts(path):
    c=sqlite3.connect(path)
    try:return tuple(c.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('qr_financial_source','qr_financial_bundle'))
    finally:c.close()


def test_declared_old_clock_cannot_backdate_system_storage_and_idempotence_is_stable(ledger,monkeypatch):
    m,s=fixture();before=copy.deepcopy(m);first=store.put('a','first',m,s)
    assert first['recorded_at']=='2026-10-01T08:00:00+00:00'
    assert first['stored_visible_at']=='2026-10-02T00:00:00+08:00'
    for clock in ['2025-06-01T12:00:00+08:00','2026-09-02T12:00:00+08:00','2026-10-01T23:59:59+08:00']:
        result=read(first['id'],clock=clock)
        assert result['status']=='unavailable' and result['panel'] is None and result['metrics'] is None
    after=read(first['id']);assert after['metrics']['parent_net_profit']=='-118.00'
    assert after['panel']['recorded_at']==first['recorded_at'] and not after['promotion_allowed']
    assert read(first['id'],clock='2025-06-01T12:00:00+08:00',mode='historical_reconstruction')['metrics']['parent_net_profit']=='-95.00'
    monkeypatch.setattr(store,'_now',lambda:datetime.fromisoformat('2026-10-03T08:00:00+00:00'))
    assert store.put('a','first',m,s)==first and counts(ledger)==(4,1) and m==before


def test_owner_isolation_and_identical_sources_not_shared_across_owners(ledger):
    m,s=fixture();a=store.put('a','first',m,s)
    with pytest.raises(LookupError):read(a['id'],owner='b')
    b=store.put('b','first',m,s)
    assert a['id']!=b['id'] and counts(ledger)==(8,2)
    assert read(b['id'],owner='b')['metrics']['parent_net_profit']=='-118.00'


@pytest.mark.parametrize('table,op',[('qr_financial_source','UPDATE'),('qr_financial_source','DELETE'),('qr_financial_bundle','UPDATE'),('qr_financial_bundle','DELETE')])
def test_immutable_tables_refuse_ordinary_mutations(ledger,table,op):
    store.put('a','first',*fixture());c=sqlite3.connect(ledger)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(('DELETE FROM '+table) if op=='DELETE' else ('UPDATE '+table+' SET owner=owner'))
    finally:c.close()
    assert counts(ledger)==(4,1)


@pytest.mark.parametrize('damage',['clock_backwards','cross_midnight','transaction_budget','source_change','owner_quota','bundle_quota'])
def test_failure_rolls_back_sources_and_receipt(ledger,monkeypatch,damage):
    m,s=fixture();first=store.put('a','first',m,s);before=counts(ledger)
    # New valid index captures force actual new BLOB inserts before late failure.
    for r in m['vintages']:
        old=r['index_sha256'];body=s.pop(old)+b'\n';sha=hashlib.sha256(body).hexdigest()
        s[sha]=body;m['source_files'][sha]=m['source_files'].pop(old);r['index_sha256']=sha
    if damage=='clock_backwards':monkeypatch.setattr(store,'_now',lambda:datetime.fromisoformat('2026-09-30T08:00:00+00:00'))
    if damage=='cross_midnight':
        times=iter([datetime.fromisoformat('2026-10-02T15:59:59+00:00'),datetime.fromisoformat('2026-10-02T16:00:00+00:00')])
        monkeypatch.setattr(store,'_now',lambda:next(times))
    if damage=='transaction_budget':
        times=iter([0,61]);monkeypatch.setattr(store.monotonic_time,'monotonic',lambda:next(times))
    if damage=='source_change':
        actual=store.require_current_source;n=[0]
        def check():
            n[0]+=1
            if n[0]>=4:raise ValueError('injected source identity change')
            actual()
        monkeypatch.setattr(store,'require_current_source',check)
    if damage=='owner_quota':monkeypatch.setattr(store,'MAX_OWNER_BYTES',1)
    if damage=='bundle_quota':monkeypatch.setattr(store,'MAX_BUNDLES',1)
    with pytest.raises((ValueError,RuntimeError)):store.put('a','new',m,s)
    assert counts(ledger)==before and first['id']


@pytest.mark.parametrize('damage',['value','document','pdf'])
def test_same_retained_revision_cannot_silently_change_facts(ledger,damage):
    m,s=fixture();store.put('a','first',m,s)
    if damage=='value':m['vintages'][0]['metrics']['parent_net_profit']='1.00'
    elif damage=='document':m['vintages'][0]['source_page']=1
    else:
        old=m['vintages'][0]['source_sha256'];body=s.pop(old)+b'extra';sha=hashlib.sha256(body).hexdigest()
        s[sha]=body;m['source_files'][sha]=m['source_files'].pop(old);m['vintages'][0]['source_sha256']=sha
    with pytest.raises(ValueError,match='Conflicting'):store.put('a','new',m,s)
    assert counts(ledger)==(4,1)


@pytest.mark.parametrize('damage',['payload','blob','owner_source','recomputed_fingerprint_clock'])
def test_corruption_is_detected_even_when_receipt_fingerprint_is_recomputed(ledger,damage):
    receipt=store.put('a','first',*fixture());c=sqlite3.connect(ledger)
    try:
        if damage in {'payload','recomputed_fingerprint_clock'}:
            c.execute('DROP TRIGGER qr_financial_bundle_update')
            v=json.loads(c.execute('SELECT payload FROM qr_financial_bundle').fetchone()[0])
            if damage=='payload':v['label']='changed'
            else:v['stored_visible_at']='2026-09-01T00:00:00+08:00'
            c.execute('UPDATE qr_financial_bundle SET payload=?,fingerprint=?',(json.dumps(v),store.digest(v)))
        else:
            c.execute('DROP TRIGGER qr_financial_source_update')
            c.execute('UPDATE qr_financial_source SET '+('body=?' if damage=='blob' else 'owner=?'),(b'changed' if damage=='blob' else 'other',))
        c.commit()
    finally:c.close()
    with pytest.raises(ValueError):read(receipt['id'])
    with pytest.raises(ValueError):recovery.prepare(ledger,ledger.parent/('bad-'+damage))


def test_review_is_read_only_and_recovery_keeps_raw_and_receipt_bytes(ledger):
    receipt=store.put('a','first',*fixture());before=read(receipt['id']);c=recovery.connect_ro(ledger)
    try:inventory=recovery.inventory(c)
    finally:c.close()

    def sha():return hashlib.sha256(ledger.read_bytes()).hexdigest()
    old=sha();assert read(receipt['id'])==before and sha()==old
    result=recovery.prepare(ledger,ledger.parent/'recovered')
    assert result['verified'] and not result['activation_authorized']
    c=recovery.connect_ro(ledger.parent/'recovered'/'research.sqlite3')
    try:
        after=recovery.inventory(c)
        for t in ('qr_financial_bundle','qr_financial_source'):assert inventory['tables'][t]==after['tables'][t]
    finally:c.close()


def test_concurrent_identical_imports_share_one_immutable_receipt(ledger):
    m,s=fixture()
    with ThreadPoolExecutor(max_workers=2) as executor:
        results=list(executor.map(lambda _:store.put('a','concurrent',m,s),range(2)))
    assert results[0]==results[1] and counts(ledger)==(4,1)


def test_recording_clock_is_taken_after_database_lock_wait(ledger,monkeypatch):
    store.put('a','first',*fixture())
    held=sqlite3.connect(ledger);held.execute('BEGIN IMMEDIATE')
    sampled=threading.Event();clock=[datetime.fromisoformat('2026-10-01T08:00:00+00:00')]
    def now():sampled.set();return clock[0]
    monkeypatch.setattr(store,'_now',now)
    with ThreadPoolExecutor(max_workers=1) as executor:
        f=executor.submit(store.put,'b','after-lock',*fixture())
        try:
            assert not sampled.wait(.15)
            clock[0]=datetime.fromisoformat('2026-10-02T08:00:00+00:00')
        finally:held.rollback();held.close()
        result=f.result(timeout=10)
    assert result['recorded_at']=='2026-10-02T08:00:00+00:00'
    assert result['stored_visible_at']=='2026-10-03T00:00:00+08:00'


def test_loaded_source_guard_does_not_accept_a_different_current_file_identity(ledger,monkeypatch):
    monkeypatch.setattr(store,'_pins',lambda:{**store.LOADED_PINS,'quant_financial_store.py':'0'*64})
    with pytest.raises(ValueError,match='source changed'):store.put('a','first',*fixture())
    assert not ledger.exists()


def test_actual_cli_import_review_isolated_database_and_no_cross_owner_output(ledger,tmp_path):
    m,s=fixture();e=tmp_path/'evidence';e.mkdir()
    for sha,name in m['source_files'].items():(e/name).write_bytes(s[sha])
    p=tmp_path/'manifest.json';p.write_text(json.dumps(m))
    script=Path(__file__).resolve().parents[3]/'scripts/quant-financial-receipt.py'
    base=[sys.executable,str(script),'--db',str(ledger),'--owner','operator']
    run=subprocess.run(base+['import','--manifest',str(p),'--evidence-dir',str(e),'--label','CLI sample'],capture_output=True,text=True)
    assert run.returncode==0,run.stdout+run.stderr
    receipt=json.loads(run.stdout)
    args=['review','--id',receipt['id'],'--issuer-id','cninfo:9900002009','--symbol','002109','--period-end','2025-03-31',
          '--as-of','2025-06-01T12:00:00+08:00','--mode','as_recorded','--metrics','parent_net_profit']
    out=subprocess.run(base+args,capture_output=True,text=True);assert out.returncode==0
    assert json.loads(out.stdout)['status']=='unavailable'
    foreign=subprocess.run(base[:-1]+['other']+args,capture_output=True,text=True)
    assert foreign.returncode==1 and '002109' not in foreign.stdout and receipt['id'] not in foreign.stdout
