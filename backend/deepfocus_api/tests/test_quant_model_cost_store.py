"""Actual synthetic engine/book imports, owner isolation and cold recovery."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading

import pytest
from deepfocus_api import quant_model_cost_store as store,quant_research_engine as engine,quant_recovery as recovery
from deepfocus_api.tests.test_quant_research_loop import dataset,request


@pytest.fixture(scope='module')
def original():
    data=dataset(320)
    for payload in data.values():
        for bar in payload['bars']:
            for key in ['open','high','low','close']:bar[key]=round(bar[key],2)
    req=request();result=engine.walk_forward(data,req['symbols'],req['benchmark'],req['capital'])
    return json.dumps([{'id':'unit-original','status':'completed','request':req,'dataset':data,'result':result,'result_digest':engine.digest(result)}]).encode()


@pytest.fixture
def db(tmp_path,monkeypatch):
    path=tmp_path/'research.sqlite3';monkeypatch.setenv('DEEPFOCUS_QUANT_RESEARCH_DB_PATH',str(path));return path


def counts():
    with store.connect_ro() as c:return tuple(c.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ['qr_model_cost_input','qr_model_cost_archive'])


def test_actual_math_archive_is_idempotent_and_read_only_replay_preserves_db(db,original):
    receipt=store.put('a','same',original)
    assert store.put('a','same',original)==receipt and counts()==(1,1)
    assert receipt['results'][0]['fills']>0 and receipt['results'][0]['exact_conservation_verified']
    before=db.read_bytes();replayed=store.inspect('a',receipt['id'],recompute=True)
    assert replayed['fresh_replay_identical'] and db.read_bytes()==before
    assert not {'owner','body','report','dataset'}&set(receipt)
    assert store.list_records('a')==[receipt]


def test_owner_isolation_includes_original_inputs_and_report_ids(db,original):
    a=store.put('a','same',original)
    assert store.list_records('b')==[]
    with pytest.raises(LookupError):store.inspect('b',a['id'],recompute=True)
    b=store.put('b','same',original)
    assert a['id']!=b['id'] and counts()==(2,2)


@pytest.mark.parametrize('table,operation',[(t,o) for t in ['qr_model_cost_input','qr_model_cost_archive'] for o in ['UPDATE','DELETE']])
def test_ordinary_mutations_refuse(db,original,table,operation):
    store.put('a','unit',original)
    c=sqlite3.connect(db)
    try:
        with pytest.raises(sqlite3.IntegrityError):c.execute('DELETE FROM '+table if operation=='DELETE' else 'UPDATE '+table+' SET owner=owner')
    finally:c.close()
    assert counts()==(1,1)


@pytest.mark.parametrize('damage',['snapshot','report','owner_link'])
def test_bypassed_trigger_corruption_and_cross_owner_source_link_refuse(db,original,damage):
    receipt=store.put('a','unit',original);c=sqlite3.connect(db)
    try:
        if damage=='snapshot':
            c.execute('DROP TRIGGER qr_model_cost_input_update');c.execute('UPDATE qr_model_cost_input SET body=?',(b'[]',))
        else:
            c.execute('DROP TRIGGER qr_model_cost_archive_update')
            c.execute('UPDATE qr_model_cost_archive SET '+('payload=?' if damage=='report' else 'owner=?'),('{}' if damage=='report' else 'b',))
        c.commit()
    finally:c.close()
    with pytest.raises((ValueError,KeyError)):store.inspect('b' if damage=='owner_link' else 'a',receipt['id'])


@pytest.mark.parametrize('quota',['MAX_ARCHIVES','MAX_OWNER_BYTES','MAX_GLOBAL_BYTES'])
def test_storage_quotas_abort_without_partial_input_row(db,original,monkeypatch,quota):
    monkeypatch.setattr(store,quota,0)
    with pytest.raises(RuntimeError):store.put('a','unit',original)
    assert counts()==(0,0)


def test_failed_archive_insert_rolls_back_new_original_snapshot(db,original):
    with store.connect() as c:
        c.execute("CREATE TRIGGER unit_fail BEFORE INSERT ON qr_model_cost_archive BEGIN SELECT RAISE(ABORT,'unit insertion failure'); END")
    with pytest.raises(sqlite3.IntegrityError):store.put('a','unit',original)
    assert counts()==(0,0)


def test_concurrent_same_snapshot_import_commits_one_receipt(db,original):
    with store.connect():pass
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:store.put('a','unit',original),range(2)))
    assert results[0]==results[1] and counts()==(1,1)


def test_source_change_and_clock_regression_refuse_new_receipts(db,original,monkeypatch):
    first=store.put('a','unit',original)
    monkeypatch.setattr(store,'_now',lambda:datetime(2020,1,1,tzinfo=timezone.utc))
    with pytest.raises(ValueError,match='backwards'):store.put('a','second',original)
    assert counts()==(1,1)
    monkeypatch.setattr(store,'pins',lambda:{})
    with pytest.raises(ValueError,match='source'):store.inspect('a',first['id'])


def test_recording_clock_is_observed_after_writer_lock_wait(db,original,monkeypatch):
    with store.connect():pass
    entered=threading.Event();actual=store.compute
    def computed(experiments):
        result=actual(experiments);entered.set();return result
    monkeypatch.setattr(store,'compute',computed)
    monkeypatch.setattr(store,'_now',lambda:datetime(2026,1,1,tzinfo=timezone.utc))
    blocker=sqlite3.connect(db);blocker.execute('BEGIN IMMEDIATE')
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(store.put,'a','waited',original)
            assert entered.wait(5)
            assert not future.done()
            monkeypatch.setattr(store,'_now',lambda:datetime(2026,1,2,tzinfo=timezone.utc))
            blocker.rollback()
            receipt=future.result(timeout=10)
    finally:blocker.close()
    assert receipt['recorded_at']=='2026-01-02T00:00:00+00:00'


@pytest.mark.parametrize('body',[b'[{"id":1,"id":2}]',b'[NaN]',b'[1e999]',b'{}',b'[]',b'['*1100+b'0'+b']'*1100])
def test_invalid_snapshot_cannot_create_database(db,body):
    with pytest.raises(ValueError):store.put('a','unit',body)
    assert not db.exists()


def test_original_changed_result_or_unscoped_metadata_cannot_archive(db,original):
    experiments=json.loads(original);experiments[0]['result']['oos']['return_pct']+=1
    with pytest.raises(ValueError):store.put('a','unit',json.dumps(experiments).encode())
    experiments=json.loads(original);experiments[0]['owner']='foreign'
    with pytest.raises(ValueError):store.put('a','unit',json.dumps(experiments).encode())
    assert not db.exists()


def test_cold_recovery_copies_both_immutable_tables_and_replays_without_source_mutation(db,original,tmp_path,monkeypatch):
    receipt=store.put('a','unit',original);before=db.read_bytes();workspace=tmp_path/'recovered'
    proof=recovery.prepare(db,workspace)
    assert proof['verified'] and not proof['activation_authorized'] and db.read_bytes()==before
    validation=recovery.verify(workspace)
    assert validation['tables']['qr_model_cost_archive']['rows']==1 and validation['tables']['qr_model_cost_input']['rows']==1
    monkeypatch.setenv('DEEPFOCUS_QUANT_RESEARCH_DB_PATH',str(workspace/'research.sqlite3'))
    assert store.inspect('a',receipt['id'],recompute=True)['fresh_replay_identical']
    assert store.list_records('b')==[]


def test_actual_operator_cli_inspect_replay_foreign_owner_and_relative_db_refusal(db,original,tmp_path):
    source=tmp_path/'original.json';source.write_bytes(original)
    script=Path(__file__).resolve().parents[3]/'scripts/quant-model-cost-archive.py'
    base=[sys.executable,str(script),'--db',str(db),'--owner','a']
    imported=subprocess.run(base+['import','--experiments',str(source),'--label','unit'],capture_output=True,text=True,timeout=20)
    assert imported.returncode==0,imported.stdout+imported.stderr
    identity=json.loads(imported.stdout)['id']
    replayed=subprocess.run(base+['replay','--id',identity],capture_output=True,text=True,timeout=20)
    assert replayed.returncode==0 and json.loads(replayed.stdout)['fresh_replay_identical']
    denied=subprocess.run(base[:-1]+['b','inspect','--id',identity],capture_output=True,text=True,timeout=10)
    assert denied.returncode==2 and json.loads(denied.stdout)['error_type']=='LookupError'
    relative=subprocess.run([sys.executable,str(script),'--db','relative.sqlite3','--owner','a','list'],capture_output=True,text=True,timeout=10)
    assert relative.returncode==2
