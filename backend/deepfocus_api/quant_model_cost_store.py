"""Immutable owner-bound exact model-cost archives, operator ingestion only.

Fresh original-engine/new-book verification precedes insertion. Read/cold-copy
validation checks storage provenance; explicit replay recomputes the math.
No broker, order, migration, strategy qualification or public upload endpoint.
"""
from contextlib import contextmanager
from datetime import datetime,timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import uuid

from . import quant_research_store as jobs,quant_research_engine as engine
from . import quant_exact_model_cost as exact,quant_execution_cost_book as legacy
from . import quant_model_cost_integrity as integrity
from .quant_research_analysis import replay_oos

VERSION='qr-exact-model-archive-v1'
MAX_INPUT=16*1024**2;MAX_REPORT=32*1024**2
MAX_OWNER_BYTES=128*1024**2;MAX_GLOBAL_BYTES=512*1024**2;MAX_ARCHIVES=50


def pins():
    names=['quant_model_cost_store.py','quant_exact_model_cost.py','quant_execution_cost_book.py',
           'quant_research_analysis.py','quant_research_engine.py','ai_fund_ledger.py','ai_fund_policy.py','quant_research_store.py','db.py','quant_model_cost_integrity.py']
    return {n:hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest() for n in names}


LOADED_PINS=pins()


def require_source():
    if (pins()!=LOADED_PINS or exact.LOADED_SHA!=LOADED_PINS['quant_exact_model_cost.py']
            or integrity.LOADED_SHA!=LOADED_PINS['quant_model_cost_integrity.py']):raise ValueError('Loaded model archive source changed')


def owner_id(owner):
    if not isinstance(owner,str) or not 1<=len(owner.strip())<=200:raise ValueError('Explicit bounded owner required')
    return owner


def parse(body):
    if not isinstance(body,bytes) or not 0<len(body)<=MAX_INPUT:raise ValueError('Bounded original snapshot bytes required')
    def pairs(values):
        result={}
        for k,v in values:
            if k in result:raise ValueError('Duplicate JSON key')
            result[k]=v
        return result
    def integer(value):
        if len(value.lstrip('-'))>40:raise ValueError('Oversized integer')
        return int(value)
    def real(value):
        result=float(value)
        if not math.isfinite(result):raise ValueError('Nonfinite snapshot number')
        return result
    def invalid(_):raise ValueError('Nonfinite snapshot constant')
    try:experiments=json.loads(body,object_pairs_hook=pairs,parse_int=integer,parse_float=real,parse_constant=invalid)
    except (RecursionError,UnicodeError) as exc:raise ValueError('Invalid bounded snapshot JSON') from exc
    allowed={'id','status','request','kernel','dataset','quality','result','result_digest'}
    request_keys={'name','symbols','benchmark','start_date','end_date','capital'}
    if not isinstance(experiments,list) or not 1<=len(experiments)<=10:raise ValueError('Scoped 1..10 experiment snapshot required')
    ids=set()
    for e in experiments:
        if (not isinstance(e,dict) or set(e)-allowed or not {'id','request','dataset','result','result_digest'}<=set(e)
                or not isinstance(e['id'],str) or not 1<=len(e['id'])<=100 or e['id'] in ids
                or e.get('status','completed')!='completed' or not isinstance(e['request'],dict)
                or set(e['request'])-request_keys):raise ValueError('Invalid or unscoped experiment export')
        ids.add(e['id'])
    return experiments


def compute(experiments):
    require_source();rows=[]
    for e in experiments:
        r,req,data=e['result'],e['request'],e['dataset']
        if engine.digest(r)!=e['result_digest'] or engine.digest(data)!=r['dataset_digest']:raise ValueError('Original result/data changed')
        reproduced=engine.walk_forward(data,req['symbols'],req['benchmark'],req['capital'])
        if engine.digest(reproduced)!=e['result_digest']:raise ValueError('Original result did not reproduce')
        path=replay_oos(data,req['symbols'],r,req['capital'])
        full=exact.run(data,path,req['capital'],scope=e['id'])
        buys=[f['date'] for f in path['fills'] if f['side']=='buy']
        if not buys:raise ValueError('Held-share restore proof required')
        prefix=exact.run(data,path,req['capital'],scope=e['id'],through=buys[0])
        suffix=exact.run(data,path,req['capital'],scope=e['id'],state=json.loads(json.dumps(prefix['state'])))
        if not prefix['state']['positions'] or suffix['state']!=full['state'] or any(prefix[k]+suffix[k]!=full[k] for k in ['daily','receipts','legacy_differences']):raise ValueError('Held-share restore differs')
        old={'status':'strictly_reconciled'}
        try:legacy.reconcile(data,path,req['capital'])
        except ValueError as exc:old={'status':'rejected','error_type':type(exc).__name__,'error':str(exc),'details':getattr(exc,'details',{})}
        rows.append({'experiment':e['id'],'original_result_digest':e['result_digest'],'original_reproduced_exactly':True,
                     'original_gate':r['gate'],'cost_book':full,'legacy_strict':old,
                     'restore':{'date':buys[0],'held_shares':sum(p['qty'] for p in prefix['state']['positions'].values()),'full_state_and_outputs_equal':True}})
    require_source()
    return {'version':VERSION,'source_pins':LOADED_PINS,'results':rows,'mode':'fixed_seen_historical_reconstruction',
            'promotion_allowed':False,'commercial_ready':False,'execution_verified':False,'tax_basis_verified':False,
            'legacy_receipts_rewritten':False,'source_authentication':'operator_supplied_snapshot_not_externally_authenticated'}


@contextmanager
def connect():
    with jobs.connect() as c:
        c.execute('CREATE TABLE IF NOT EXISTS qr_model_cost_input(owner TEXT NOT NULL,sha TEXT NOT NULL,body BLOB NOT NULL,PRIMARY KEY(owner,sha))')
        c.execute('CREATE TABLE IF NOT EXISTS qr_model_cost_archive(id TEXT PRIMARY KEY,owner TEXT NOT NULL,input_hash TEXT NOT NULL,input_sha TEXT NOT NULL,fingerprint TEXT NOT NULL,created TEXT NOT NULL,payload TEXT NOT NULL,UNIQUE(owner,input_hash))')
        for table in ['qr_model_cost_input','qr_model_cost_archive']:
            for operation in ['UPDATE','DELETE']:
                c.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_{operation.lower()} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'immutable exact model archive'); END")
        c.commit();yield c


@contextmanager
def connect_ro():
    path=jobs.db.data_path('.quant_research.sqlite3','DEEPFOCUS_QUANT_RESEARCH_DB_PATH').resolve()
    c=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2);c.row_factory=sqlite3.Row
    try:c.execute('PRAGMA query_only=ON');c.execute('BEGIN');yield c
    finally:c.close()


def verify_row(c,row):
    require_source();value,body=integrity.verify_row(c,row)
    if value['source_pins']!=LOADED_PINS:raise ValueError('Historical source requires metadata/storage review, not current replay')
    return value,body


def summary(row,value):
    return {'id':row['id'],'fingerprint':row['fingerprint'],'label':value['label'],'recorded_at':row['created'],
            'input_sha256':row['input_sha'],'model_version':exact.VERSION,'promotion_allowed':False,'commercial_ready':False,
            'external_clock_attestation':False,'results':[{'experiment':r['experiment'],'fills':len(r['cost_book']['receipts']),
                'days':len(r['cost_book']['daily']),'legacy_difference_count':len(r['cost_book']['legacy_differences']),
                'original_strategy_gate_passed':r['original_gate']['passed'],
                'legacy_strict_status':r['legacy_strict']['status'],'exact_conservation_verified':r['cost_book']['exact_conservation_verified']} for r in value['report']['results']]}


def usage(c,owner=None):
    extra=' WHERE owner=?' if owner is not None else '';args=(owner,) if owner is not None else ()
    return sum(c.execute('SELECT coalesce(sum(length(CAST('+col+' AS BLOB))),0) FROM '+table+extra,args).fetchone()[0]
               for table,col in [('qr_model_cost_input','body'),('qr_model_cost_archive','payload')])


def _now():return datetime.now(timezone.utc)


def put(owner,label,body):
    require_source();owner_id(owner)
    if not isinstance(label,str) or not 1<=len(label.strip())<=80:raise ValueError('Bounded archive label required')
    label=label.strip();experiments=parse(body);report=compute(experiments)
    sha=hashlib.sha256(body).hexdigest();request=engine.digest({'label':label,'input_sha':sha,'source_pins':LOADED_PINS})
    if len(json.dumps(report,ensure_ascii=False).encode())>MAX_REPORT-4096:raise ValueError('Computed model report exceeds quota')
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        old=c.execute('SELECT * FROM qr_model_cost_archive WHERE owner=? AND input_hash=?',(owner,request)).fetchone()
        if old:
            value,retained=verify_row(c,old)
            if retained!=body or value['report']!=report:raise ValueError('Existing model archive differs from fresh replay')
            return summary(old,value)
        recorded=_now().isoformat();last=c.execute('SELECT max(created) FROM qr_model_cost_archive WHERE owner=?',(owner,)).fetchone()[0]
        if last and last>recorded:raise ValueError('Archive recording clock moved backwards')
        if c.execute('SELECT count(*) FROM qr_model_cost_archive WHERE owner=?',(owner,)).fetchone()[0]>=MAX_ARCHIVES:raise RuntimeError('Model archive count quota exhausted')
        value={'version':VERSION,'source_pins':LOADED_PINS,'recorded_at':recorded,'label':label,
               'input_sha256':sha,'request_hash':request,'report':report}
        raw=json.dumps(value,ensure_ascii=False,allow_nan=False);known=c.execute('SELECT body FROM qr_model_cost_input WHERE owner=? AND sha=?',(owner,sha)).fetchone()
        if known is not None and known['body']!=body:raise ValueError('Existing original snapshot corrupted')
        added=len(raw.encode())+(0 if known else len(body))
        if len(raw.encode())>MAX_REPORT or usage(c,owner)+added>MAX_OWNER_BYTES or usage(c)+added>MAX_GLOBAL_BYTES:raise RuntimeError('Model archive byte quota exhausted')
        require_source()
        if known is None:c.execute('INSERT INTO qr_model_cost_input VALUES(?,?,?)',(owner,sha,sqlite3.Binary(body)))
        identity=uuid.uuid4().hex
        c.execute('INSERT INTO qr_model_cost_archive VALUES(?,?,?,?,?,?,?)',(identity,owner,request,sha,engine.digest(value),recorded,raw))
        row=c.execute('SELECT * FROM qr_model_cost_archive WHERE owner=? AND id=?',(owner,identity)).fetchone();verify_row(c,row)
        return summary(row,value)


def inspect(owner,identity,*,recompute=False):
    owner_id(owner)
    with connect_ro() as c:
        row=c.execute('SELECT * FROM qr_model_cost_archive WHERE owner=? AND id=?',(owner,identity)).fetchone()
        if row is None:raise LookupError('Model archive not found')
        value,body=verify_row(c,row);result=summary(row,value)
        if recompute:
            if compute(parse(body))!=value['report']:raise ValueError('Fresh model replay differs from immutable archive')
            result['fresh_replay_identical']=True
        return result


def list_records(owner):
    owner_id(owner)
    with connect_ro() as c:
        return [summary(row,verify_row(c,row)[0]) for row in c.execute('SELECT * FROM qr_model_cost_archive WHERE owner=? ORDER BY created DESC,id LIMIT 50',(owner,))]
