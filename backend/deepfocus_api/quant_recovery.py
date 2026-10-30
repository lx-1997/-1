"""Offline QR recovery into a NEW quarantined workspace; never modifies source.

Storage preservation is not economic correctness, strategy qualification, or
permission to activate a restored service. No archived application code runs.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from datetime import datetime,timezone
from .quant_research_engine import digest

VERSION='qr-quarantined-recovery-v1'
TABLES={'qr_experiment','qr_paper','qr_worker_slot','qr_evidence_source','qr_evidence_bundle','qr_calendar_source','qr_calendar_bundle'}
MESSAGE='恢复副本中的未完成任务已隔离，禁止自动重跑'


def load_json(value):
    def invalid(_):raise ValueError('Non-finite recovery JSON')
    return json.loads(value,parse_constant=invalid)


def pins():
    root=Path(__file__).resolve()
    paths=[root,root.with_name('quant_research_engine.py'),root.parents[2]/'scripts/quant-recovery.py']
    return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def file_hash(path):
    sha=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):sha.update(block)
    return sha.hexdigest()


def connect_ro(path):
    c=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)
    c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON')
    return c


def schema(c):
    rows=c.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
    tables={r['name'] for r in rows if r['type']=='table'}
    if not {'qr_experiment','qr_paper'}<=tables or tables-TABLES:raise ValueError('Unsupported research schema')
    # Never execute unexpected triggers while quarantining mutable tables.
    if any(r['type']=='trigger' and r['tbl_name'] in {'qr_experiment','qr_paper','qr_worker_slot'} for r in rows):
        raise ValueError('Unexpected recovery mutation trigger')
    for name in tables:
        info=c.execute('PRAGMA table_info('+name+')').fetchall()
        if not any(r['pk'] for r in info):raise ValueError('Recovery tables need stable primary keys')
    for name,column in (('qr_experiment','payload'),('qr_paper','payload'),('qr_evidence_source','body'),('qr_calendar_source','body')):
        if name in tables and c.execute('SELECT max(length(CAST('+column+' AS BLOB))) FROM '+name).fetchone()[0] is not None:
            if c.execute('SELECT max(length(CAST('+column+' AS BLOB))) FROM '+name).fetchone()[0]>32*1024*1024:
                raise ValueError('Recovery record exceeds supported 32MiB budget')
    return sorted(tables),digest([list(r) for r in rows])


def ordered(c,table):
    info=c.execute('PRAGMA table_info('+table+')').fetchall()
    keys=[r['name'] for r in sorted(info,key=lambda r:r['pk']) if r['pk']]
    if any(not k.replace('_','').isalnum() for k in keys):raise ValueError('Unsupported key')
    return c.execute('SELECT * FROM '+table+' ORDER BY '+','.join('"'+k+'"' for k in keys))


def encode(row):
    out=[]
    for key,value in dict(row).items():
        if isinstance(value,bytes):value={'blob_sha256':hashlib.sha256(value).hexdigest(),'bytes':len(value)}
        out.append([key,value])
    return json.dumps(out,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()


def validate_row(c,table,row,tables):
    if table=='qr_experiment':
        p=load_json(row['payload'])
        if not isinstance(p,dict) or not isinstance(p.get('request'),dict) or digest(p['request'])!=row['input_hash']:
            raise ValueError('Research request fingerprint mismatch')
        if row['status'] not in {'running','queued','failed','cancelled','completed'}:raise ValueError('Unknown research state')
        if row['status']=='completed':
            if not isinstance(p.get('result'),dict) or digest(p['result'])!=p.get('result_digest'):raise ValueError('Research result fingerprint mismatch')
            if 'dataset' in p and 'dataset_digest' in p['result'] and digest(p['dataset'])!=p['result']['dataset_digest']:
                raise ValueError('Research dataset fingerprint mismatch')
    elif table=='qr_paper':
        p=load_json(row['payload']);state=p.get('state') if isinstance(p,dict) else None
        if not isinstance(state,dict) or type(state.get('cash')) not in (int,float) or not math.isfinite(state['cash']) or state['cash']<0:
            raise ValueError('Invalid paper cash frame')
        if row['status'] not in {'active','paused'} or type(row['revision']) is not int or row['revision']<0:
            raise ValueError('Invalid paper operational state')
    elif table in {'qr_calendar_source','qr_evidence_source'}:
        if not isinstance(row['body'],bytes) or hashlib.sha256(row['body']).hexdigest()!=row['sha']:
            raise ValueError('Retained evidence blob mismatch')
    elif table in {'qr_calendar_bundle','qr_evidence_bundle'}:
        value=load_json(row['payload'] if table=='qr_calendar_bundle' else row['manifest'])
        if digest(value)!=row['fingerprint']:raise ValueError('Evidence manifest fingerprint mismatch')
        if table=='qr_calendar_bundle':
            required={r['source_sha256'] for r in value['calendar']['records']};source='qr_calendar_source'
        else:required={r['source_sha256'] for r in value['events']};source='qr_evidence_source'
        if source not in tables:raise ValueError('Evidence source table missing')
        for sha in required:
            if c.execute('SELECT 1 FROM '+source+' WHERE owner=? AND sha=?',(row['owner'],sha)).fetchone() is None:
                raise ValueError('Owner-bound evidence source missing')


def inventory(c,*,restore_at=None):
    tables,shape=schema(c);result={};changes={'unfinished_jobs':0,'active_papers':0,'worker_slots':0}
    for table in tables:
        actual=hashlib.sha256();expected=hashlib.sha256();n=0
        for row in ordered(c,table):
            validate_row(c,table,row,tables)
            original=encode(row);actual.update(len(original).to_bytes(8,'big')+original);n+=1
            future=dict(row)
            if restore_at is not None:
                if table=='qr_experiment' and row['status'] in ('queued','running'):
                    future.update(status='failed',stage=MESSAGE,updated=restore_at);changes['unfinished_jobs']+=1
                elif table=='qr_paper' and row['status']=='active':
                    future.update(status='paused',revision=row['revision']+1,updated=restore_at);changes['active_papers']+=1
                elif table=='qr_worker_slot':changes['worker_slots']+=1;continue
            value=encode(future);expected.update(len(value).to_bytes(8,'big')+value)
        result[table]={'rows':n,'digest':actual.hexdigest(),'expected_digest':expected.hexdigest(),
                       'expected_rows':0 if restore_at is not None and table=='qr_worker_slot' else n}
    return {'schema_digest':shape,'tables':result,'quarantine_changes':changes}


def verify(workspace):
    workspace=Path(workspace)
    if not workspace.is_absolute() or (workspace/'FAILED.json').exists():raise ValueError('Invalid or failed recovery workspace')
    receipt_path=workspace/'receipt.json';receipt=load_json(receipt_path.read_text());db=workspace/'research.sqlite3'
    if receipt['version']!=VERSION or receipt['source_pins']!=pins():raise ValueError('Recovery verifier version changed')
    before=file_hash(db)
    if before!=receipt['database_sha256']:raise ValueError('Recovery database bytes changed')
    ready_path=workspace/'READY.json'
    if ready_path.exists():
        ready=load_json(ready_path.read_text());proof_path=workspace/'verification.json'
        if (ready.get('receipt_sha256')!=hashlib.sha256(receipt_path.read_bytes()).hexdigest()
                or ready.get('database_sha256')!=before
                or ready.get('verification_sha256')!=file_hash(proof_path)
                or ready.get('activation_authorized') is not False
                or ready.get('quarantined_for_review_only') is not True):
            raise ValueError('Recovery readiness chain changed')
        proof=load_json(proof_path.read_text())
        if (proof.get('verified') is not True or proof.get('activation_authorized') is not False
                or proof.get('receipt_sha256')!=ready['receipt_sha256']
                or proof.get('database_sha256')!=before):
            raise ValueError('Independent recovery proof changed')
    c=connect_ro(db)
    try:
        if [r[0] for r in c.execute('PRAGMA integrity_check')]!=['ok']:raise ValueError('Recovery integrity check failed')
        current=inventory(c)
        if current['schema_digest']!=receipt['baseline']['schema_digest']:raise ValueError('Recovery schema changed')
        for name,old in receipt['baseline']['tables'].items():
            got=current['tables'][name]
            if got['rows']!=old['expected_rows'] or got['digest']!=old['expected_digest']:
                raise ValueError('Recovery changed protected records')
        if c.execute("SELECT count(*) FROM qr_paper WHERE status='active'").fetchone()[0]:raise ValueError('Recovered paper still active')
        if c.execute("SELECT count(*) FROM qr_experiment WHERE status IN ('queued','running')").fetchone()[0]:raise ValueError('Recovered jobs still runnable')
    finally:c.close()
    if before!=file_hash(db):raise ValueError('Recovery database changed during verification')
    return {'verified':True,'database_sha256':before,'receipt_sha256':hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
            'tables':{n:{'rows':r['rows'],'digest':r['digest']} for n,r in current['tables'].items()},
            'quarantined':True,'activation_authorized':False,'source_pins':pins()}


def prepare(source,workspace,*,max_bytes=1024*1024*1024,timeout=60):
    source,workspace=Path(source),Path(workspace)
    if not source.is_absolute() or not source.is_file() or not workspace.is_absolute() or workspace.exists():
        raise ValueError('Explicit existing source and NEW absolute workspace required')
    if not 0<timeout<=60 or max_bytes<=0:raise ValueError('Invalid backup budget')
    workspace.mkdir(mode=0o700,parents=False)
    target=workspace/'research.sqlite3';staged=workspace/'snapshot.partial.sqlite3';src=dst=None
    try:
        frozen=pins();started=time.monotonic();stamp=datetime.now(timezone.utc).isoformat()
        src=connect_ro(source)
        # Establish a stable read snapshot; backup includes committed WAL data.
        src.execute('BEGIN');schema(src)
        pages=src.execute('PRAGMA page_count').fetchone()[0];size=src.execute('PRAGMA page_size').fetchone()[0]
        if pages*size>max_bytes:raise ValueError('Backup exceeds explicit byte budget')
        free=os.statvfs(workspace).f_bavail*os.statvfs(workspace).f_frsize
        if free<pages*size+64*1024*1024:raise ValueError('Insufficient recovery disk budget')
        fd=os.open(staged,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
        dst=sqlite3.connect(staged,timeout=2);dst.row_factory=sqlite3.Row
        def progress(status,left,total):
            if total*size>max_bytes or time.monotonic()-started>timeout:raise TimeoutError('Backup budget exhausted')
        src.backup(dst,pages=128,progress=progress,sleep=.01)
        src.close();src=None
        if [r[0] for r in dst.execute('PRAGMA integrity_check')]!=['ok']:raise ValueError('Snapshot integrity failed')
        baseline=inventory(dst,restore_at=stamp)
        dst.execute('BEGIN IMMEDIATE')
        dst.execute("UPDATE qr_experiment SET status='failed',stage=?,updated=? WHERE status IN ('queued','running')",(MESSAGE,stamp))
        dst.execute("UPDATE qr_paper SET status='paused',revision=revision+1,updated=? WHERE status='active'",(stamp,))
        if 'qr_worker_slot' in baseline['tables']:dst.execute('DELETE FROM qr_worker_slot')
        dst.commit();dst.execute('PRAGMA journal_mode=DELETE');dst.close();dst=None
        os.link(staged,target);staged.unlink()
        receipt={'version':VERSION,'created_at':stamp,'source_open_mode':'ro','source_sql_mutations':False,
                 'source_snapshot_not_claimed_current_after_backup':True,'baseline':baseline,
                 'database_sha256':file_hash(target),'source_pins':frozen,
                 'state':'quarantined_snapshot','activation_authorized':False}
        with (workspace/'receipt.json').open('x') as f:json.dump(receipt,f,ensure_ascii=False,indent=2)
        # Its first independent SQLite reader is a NEW read-only process.
        command=[sys.executable,str(Path(__file__).resolve().parents[2]/'scripts/quant-recovery.py'),'verify','--workspace',str(workspace)]
        child=subprocess.run(command,capture_output=True,text=True,timeout=60)
        if child.returncode:raise ValueError('Independent recovery verification failed')
        proof=load_json(child.stdout)
        if proof.get('verified') is not True or pins()!=frozen:raise ValueError('Recovery verification or source changed')
        with (workspace/'verification.json').open('x') as f:json.dump(proof,f,ensure_ascii=False,indent=2)
        ready={'receipt_sha256':proof['receipt_sha256'],'database_sha256':proof['database_sha256'],
               'verification_sha256':hashlib.sha256((workspace/'verification.json').read_bytes()).hexdigest(),
               'quarantined_for_review_only':True,'activation_authorized':False}
        with (workspace/'READY.json').open('x') as f:json.dump(ready,f,indent=2)
        return {'verified':True,'quarantine_changes':baseline['quarantine_changes'],'database_sha256':proof['database_sha256'],
                'activation_authorized':False}
    except BaseException as exc:
        with (workspace/'FAILED.json').open('x') as f:json.dump({'version':VERSION,'error_type':type(exc).__name__,'activation_authorized':False},f)
        raise
    finally:
        if dst is not None:dst.close()
        if src is not None:src.close()
