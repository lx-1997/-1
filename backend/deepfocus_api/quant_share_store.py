"""Immutable owner-scoped share research receipts, with system recording clocks.

No broker authentication, cash/tax decisions or orders. Operator-declared clocks
remain in the input; independent first-storage clocks fence as-recorded use.
"""
import copy
from contextlib import contextmanager
from datetime import datetime, time, timedelta, timezone
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3
import time as elapsed_time
import uuid

from . import quant_research_store as jobs, quant_share_entitlements as shares

VERSION='qr-share-receipt-v1'
REVIEW_VERSION='qr-share-review-v1'
VISIBILITY='next_beijing_calendar_day_after_transaction_recording'
MAX_BUNDLES=100
MAX_REVIEWS=200
MAX_OWNER_SOURCE=100*1024**2
MAX_OWNER_METADATA=50*1024**2
MAX_OWNER_REVIEWS=100*1024**2
MAX_GLOBAL_BYTES=512*1024**2
digest=shares.digest


def pins():
    return {n:hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest()
            for n in ('quant_share_store.py','quant_share_entitlements.py','quant_research_store.py','db.py')}


LOADED_PINS=pins()


def require_source():
    if pins()!=LOADED_PINS or LOADED_PINS['quant_share_entitlements.py']!=shares.SOURCE_SHA:
        raise ValueError('Loaded share receipt source changed')


def _now():return datetime.now(timezone.utc)


def boundary(stamp):
    return datetime.combine(shares.stamp(stamp).astimezone(shares.BJ).date()+timedelta(days=1),time(),shares.BJ).isoformat()


def identity(value):
    if not isinstance(value,str) or not 1<=len(value.strip())<=200:raise ValueError('Explicit bounded owner required')
    return value


@contextmanager
def connect():
    with jobs.connect() as c:
        c.execute('CREATE TABLE IF NOT EXISTS qr_share_source(owner TEXT NOT NULL,sha TEXT NOT NULL,body BLOB NOT NULL,PRIMARY KEY(owner,sha))')
        c.execute('CREATE TABLE IF NOT EXISTS qr_share_bundle(id TEXT PRIMARY KEY,owner TEXT NOT NULL,input_hash TEXT NOT NULL,fingerprint TEXT NOT NULL,created TEXT NOT NULL,payload TEXT NOT NULL,UNIQUE(owner,input_hash))')
        c.execute('CREATE TABLE IF NOT EXISTS qr_share_review(id TEXT PRIMARY KEY,owner TEXT NOT NULL,bundle_id TEXT NOT NULL,request_key TEXT NOT NULL,input_hash TEXT NOT NULL,fingerprint TEXT NOT NULL,created TEXT NOT NULL,payload TEXT NOT NULL,UNIQUE(owner,request_key))')
        for table in ('qr_share_source','qr_share_bundle','qr_share_review'):
            for op in ('UPDATE','DELETE'):
                c.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_{op.lower()} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,'immutable share receipt'); END")
        c.commit();yield c


def connect_ro():
    path=jobs.db.data_path('.quant_research.sqlite3','DEEPFOCUS_QUANT_RESEARCH_DB_PATH').resolve()
    c=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2)
    c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
    return c


def _objects(plan):
    action=shares.Capitalization(**plan['action'])
    allot=shares.Allotment(**plan['allotment']) if plan.get('allotment') is not None else None
    return action,allot


def _compute(plan,sources,mode):
    action,allot=_objects(plan);scope=plan['scope'];checkpoints={}
    book=shares.initial(scope,plan['start_after'],mode=mode)
    for stage,func,clock in [('registration',shares.register,'register_asof'),('recognition',shares.recognize,'recognize_asof'),('credit',shares.credit,'credit_asof')]:
        try:
            args=(book,action,plan['closing_lots'],sources) if stage=='registration' else (book,action,sources)
            kw={'allotment':allot} if stage=='credit' else {}
            book=func(*args,asof=plan[clock],scope=scope,**kw);checkpoints[stage]=book['state_digest']
        except shares.PendingAllotment:
            return {'status':'awaiting_allotment','final_book':book,'checkpoints':checkpoints,'available_new_shares':0}
    return {'status':'quantity_model_completed','final_book':book,'checkpoints':checkpoints,
            'available_new_shares':shares.available(book,action.event_id,asof=plan['credit_asof'],scope=scope)}


def normalize(plan,sources):
    allowed={'action','allotment','scope','position_role','start_after','mode','closing_lots','register_asof','recognize_asof','credit_asof'}
    if not isinstance(plan,dict) or set(plan)-allowed or allowed-{'allotment'}-set(plan):raise ValueError('Invalid share plan schema')
    plan=json.loads(json.dumps(plan,ensure_ascii=False,allow_nan=False));plan.setdefault('allotment',None)
    if len(json.dumps(plan,ensure_ascii=False).encode())>2*1024**2:raise ValueError('Share metadata exceeds 2MiB')
    if plan['position_role'] not in ('counterfactual_model','operator_supplied_account_close') or plan['mode'] not in ('as_recorded','historical_reconstruction'):
        raise ValueError('Explicit position role and knowledge mode required')
    action,allot=_objects(plan)
    plan['action']=asdict(action)
    plan['allotment']=asdict(allot) if allot else None
    expected={action.source_sha256}|({allot.source_sha256} if allot else set())
    if not isinstance(sources,dict) or set(sources)!=expected or sum(map(len,sources.values()))>25*1024**2:raise ValueError('Share evidence scope/budget mismatch')
    for sha in expected:shares.pdf_source(sha,sources)
    # Validate structure and operations without pretending today's observations
    # were present in the historical model. Fractions may remain unresolved.
    _compute(plan,sources,'historical_reconstruction')
    return plan


def components(plan):
    action,allot=_objects(plan)
    fact={k:v for k,v in plan['action'].items() if k not in ('observed_at','source_url')}
    action_digest=digest(fact)
    snapshot={'scope':plan['scope'],'position_role':plan['position_role'],'action_fact':action_digest,
              'lots':sorted(plan['closing_lots'],key=lambda r:r['id'])}
    parts={'action':action_digest,'position_snapshot':digest(snapshot)}
    if allot:
        parts['allotment']=digest({**{k:v for k,v in plan['allotment'].items() if k not in ('observed_at','event_fingerprint')},'action_fact':action_digest})
    return parts


def _bytes(c,table,column,owner=None):
    query='SELECT coalesce(sum(length(CAST('+column+' AS BLOB))),0) FROM '+table
    return c.execute(query+(' WHERE owner=?' if owner is not None else ''),(owner,) if owner is not None else ()).fetchone()[0]


def _global(c,extra):
    total=sum(_bytes(c,t,col) for t,col in [('qr_share_source','body'),('qr_share_bundle','payload'),('qr_share_review','payload')])
    if total+extra>MAX_GLOBAL_BYTES:raise RuntimeError('Share receipt global storage quota exhausted')


def verify_row(c,row,*,memo=None,stack=None,source_cache=None):
    """Read-only validation of immutable data and direct first-receipt links."""
    require_source();memo={} if memo is None else memo;stack=set() if stack is None else stack;source_cache={} if source_cache is None else source_cache
    key=(row['owner'],row['id'])
    if key in memo:return memo[key]
    if key in stack or len(stack)>MAX_BUNDLES:raise ValueError('Cyclic share receipt provenance')
    stack.add(key)
    try:
        value=json.loads(row['payload'])
        if digest(value)!=row['fingerprint'] or value.get('schema')!=VERSION or value.get('source_pins')!=LOADED_PINS or value.get('visibility_policy')!=VISIBILITY:
            raise ValueError('Share receipt fingerprint/version changed')
        if digest(value['input'])!=row['input_hash'] or value['recorded_at']!=row['created'] or value['stored_visible_at']!=boundary(row['created']):raise ValueError('Share input/recording clock changed')
        sources={};plan=value['input']['plan'];action,allot=_objects(plan)
        for fact in (action,allot):
            if fact and (shares.stamp(fact.available_at)>shares.stamp(row['created']) or shares.stamp(fact.observed_at)>shares.stamp(row['created'])):
                raise ValueError('Share evidence recorded before declared observation')
        for sha in {action.source_sha256}|({allot.source_sha256} if allot else set()):
            cache_key=(row['owner'],sha)
            if cache_key not in source_cache:
                blob=c.execute('SELECT body FROM qr_share_source WHERE owner=? AND sha=?',cache_key).fetchone()
                if blob is None:raise ValueError('Owner-bound share original missing')
                source_cache[cache_key]=blob['body']
            sources[sha]=source_cache[cache_key]
        if normalize(plan,sources)!=plan:raise ValueError('Share plan normalization changed')
        expected=components(plan)
        if set(value['components'])!=set(expected):raise ValueError('Share provenance inventory changed')
        for name,fp in expected.items():
            part=value['components'][name]
            if part['digest']!=fp or part['visible_at']!=boundary(part['first_recorded_at']):raise ValueError('Share component identity/visibility changed')
            if shares.stamp(part['first_recorded_at'])>shares.stamp(row['created']):raise ValueError('Share component acquired after receipt')
            if part['first_receipt_id']==row['id']:
                if part['first_recorded_at']!=row['created']:raise ValueError('Self-recording clock changed')
            else:
                prior=c.execute('SELECT * FROM qr_share_bundle WHERE owner=? AND id=?',(row['owner'],part['first_receipt_id'])).fetchone()
                if prior is None:raise ValueError('First receipt missing or foreign owner')
                prev,_=verify_row(c,prior,memo=memo,stack=stack,source_cache=source_cache)
                if prev['components'].get(name)!=part or part['first_receipt_id']!=prior['id']:raise ValueError('First-receipt linkage changed')
        memo[key]=(value,sources);return value,sources
    finally:stack.remove(key)


def _summary(row,value):
    p=value['input']['plan']
    return {'id':row['id'],'fingerprint':row['fingerprint'],'label':value['input']['label'],'recorded_at':row['created'],
            'stored_visible_at':value['stored_visible_at'],'symbol':p['action']['symbol'],'position_role':p['position_role'],
            'component_visibility':{k:v['visible_at'] for k,v in value['components'].items()},
            'fact_review':'operator_supplied_not_automatically_authenticated','externally_attested_time':False,'promotion_allowed':False}


def put(owner,label,plan,sources):
    require_source();identity(owner)
    if not isinstance(label,str) or not 1<=len(label.strip())<=80:raise ValueError('Bounded share receipt label required')
    sources=dict(sources);plan=normalize(plan,sources);incoming={'label':label.strip(),'plan':plan};ih=digest(incoming)
    with connect() as c:
        c.execute('BEGIN IMMEDIATE');old=c.execute('SELECT * FROM qr_share_bundle WHERE owner=? AND input_hash=?',(owner,ih)).fetchone()
        if old:
            value,retained=verify_row(c,old)
            if retained!=sources:raise ValueError('Retained share original differs')
            return _summary(old,value)
        started=elapsed_time.monotonic();recorded=_now();stamp=recorded.isoformat();edge=boundary(stamp)
        action,allot=_objects(plan)
        for fact in (action,allot):
            if fact and (shares.stamp(fact.available_at)>recorded or shares.stamp(fact.observed_at)>recorded):raise ValueError('Future declared evidence cannot establish an observation')
        rows=c.execute('SELECT * FROM qr_share_bundle WHERE owner=? ORDER BY created,id',(owner,)).fetchall()
        last_review=c.execute('SELECT max(created) FROM qr_share_review WHERE owner=?',(owner,)).fetchone()[0]
        if last_review and shares.stamp(last_review)>recorded:raise ValueError('Share recording clock moved backwards')
        if len(rows)>=MAX_BUNDLES:raise RuntimeError('Share bundle quota exhausted')
        first={};memo={};cache={};current_parts=components(plan)
        for row in rows:
            prev,_=verify_row(c,row,memo=memo,source_cache=cache)
            if shares.stamp(row['created'])>recorded:raise ValueError('Share recording clock moved backwards')
            oldplan=prev['input']['plan'];oldaction=oldplan['action']
            same_action=(oldaction['event_id']==action.event_id or (oldaction['symbol'],oldaction['kind'],oldaction['record_date'])==(action.symbol,action.kind,action.record_date))
            if same_action and prev['components']['action']['digest']!=current_parts['action']:raise ValueError('Conflicting retained share fact; explicit revision not supported')
            if (same_action and oldplan['scope']==plan['scope'] and oldplan['position_role']==plan['position_role']=='operator_supplied_account_close'
                    and prev['components']['position_snapshot']['digest']!=current_parts['position_snapshot']):raise ValueError('Conflicting actual account close snapshot')
            if allot and oldplan.get('allotment') and (oldplan['allotment']['account_scope'],oldplan['allotment']['receipt_id'])==(allot.account_scope,allot.receipt_id) and prev['components']['allotment']['digest']!=current_parts['allotment']:
                raise ValueError('Conflicting account allotment confirmation')
            for name,part in prev['components'].items():first.setdefault((name,part['digest']),part)
        rid=uuid.uuid4().hex
        refs={name:copy.deepcopy(first.get((name,fp),{'digest':fp,'first_receipt_id':rid,'first_recorded_at':stamp,'visible_at':edge})) for name,fp in current_parts.items()}
        value={'schema':VERSION,'input':incoming,'recorded_at':stamp,'stored_visible_at':edge,
               'components':refs,'visibility_policy':VISIBILITY,'source_pins':LOADED_PINS}
        raw=json.dumps(value,ensure_ascii=False,allow_nan=False)
        if _bytes(c,'qr_share_bundle','payload',owner)+len(raw.encode())>MAX_OWNER_METADATA:raise RuntimeError('Share metadata quota exhausted')
        known={r['sha']:r['n'] for r in c.execute('SELECT sha,length(body) n FROM qr_share_source WHERE owner=?',(owner,))}
        added=sum(len(b) for h,b in sources.items() if h not in known)
        if sum(known.values())+added>MAX_OWNER_SOURCE:raise RuntimeError('Share evidence quota exhausted')
        _global(c,added+len(raw.encode()))
        for sha,body in sources.items():
            if sha in known:
                if c.execute('SELECT body FROM qr_share_source WHERE owner=? AND sha=?',(owner,sha)).fetchone()['body']!=body:raise ValueError('Retained share original corrupt')
            else:c.execute('INSERT INTO qr_share_source VALUES(?,?,?)',(owner,sha,sqlite3.Binary(body)))
        c.execute('INSERT INTO qr_share_bundle VALUES(?,?,?,?,?,?)',(rid,owner,ih,digest(value),stamp,raw))
        row=c.execute('SELECT * FROM qr_share_bundle WHERE id=? AND owner=?',(rid,owner)).fetchone();verify_row(c,row)
        require_source();now=_now()
        if now<recorded or now>=shares.stamp(edge) or elapsed_time.monotonic()-started>60:raise ValueError('Share transaction clock/budget exceeded')
        c.commit();return _summary(row,value)


def compute_review(value,sources,*,mode,as_of):
    if mode not in ('as_recorded','historical_reconstruction'):raise ValueError('Explicit share review mode required')
    plan=value['input']['plan'];asof=shares.stamp(as_of)
    if any(shares.stamp(plan[k])>asof for k in ('register_asof','recognize_asof','credit_asof')):raise ValueError('Review excludes requested operation dates')
    if mode=='as_recorded':
        gates=[('action','register_asof'),('position_snapshot','register_asof')]+([('allotment','credit_asof')] if plan.get('allotment') else [])
        missing=[name for name,clock in gates if shares.stamp(plan[clock])<shares.stamp(value['components'][name]['visible_at'])]
        if missing:
            return {'status':'not_available_as_recorded','final_book':None,'checkpoints':{},'available_new_shares':0,'unavailable_components':missing}
        # A recapture must neither backdate new knowledge nor erase an earlier
        # immutable observation of identical facts. Preserve input declarations;
        # only this derived model uses each component's first-storage boundary.
        plan=copy.deepcopy(plan)
        plan['action']['observed_at']=value['components']['action']['visible_at']
        if plan.get('allotment'):
            plan['allotment']['observed_at']=value['components']['allotment']['visible_at']
            plan['allotment']['event_fingerprint']=shares.Capitalization(**plan['action']).fingerprint
    return _compute(plan,sources,mode)


def verify_review_row(c,row,*,bundle_memo=None):
    require_source();value=json.loads(row['payload']);request=value['request']
    if value.get('schema')!=REVIEW_VERSION or value.get('source_pins')!=LOADED_PINS or digest(value)!=row['fingerprint'] or digest(request)!=row['input_hash'] or value['recorded_at']!=row['created']:
        raise ValueError('Share review receipt changed')
    if request['bundle_id']!=row['bundle_id'] or shares.stamp(request['as_of'])>shares.stamp(row['created']):raise ValueError('Invalid share review provenance/clock')
    prior=c.execute('SELECT * FROM qr_share_bundle WHERE owner=? AND id=?',(row['owner'],row['bundle_id'])).fetchone()
    if prior is None:raise ValueError('Owner-bound share bundle missing')
    bundle,sources=verify_row(c,prior,memo=bundle_memo)
    if request['bundle_fingerprint']!=prior['fingerprint'] or value['result']!=compute_review(bundle,sources,mode=request['mode'],as_of=request['as_of']):raise ValueError('Stored quantity review not reproducible')
    return value


def review(owner,bundle_id,key,*,mode,as_of):
    require_source();identity(owner);shares.identifier(key)
    c=connect_ro()
    try:
        row=c.execute('SELECT * FROM qr_share_bundle WHERE owner=? AND id=?',(owner,bundle_id)).fetchone()
        if row is None:raise LookupError('Share bundle not found')
        value,sources=verify_row(c,row);fingerprint=row['fingerprint']
    finally:c.close()
    if shares.stamp(as_of)>_now():raise ValueError('Cannot certify a future research view')
    request={'bundle_id':bundle_id,'bundle_fingerprint':fingerprint,'mode':mode,'as_of':as_of}
    result=compute_review(value,sources,mode=mode,as_of=as_of)
    with connect() as c:
        c.execute('BEGIN IMMEDIATE');old=c.execute('SELECT * FROM qr_share_review WHERE owner=? AND request_key=?',(owner,key)).fetchone()
        if old:
            if old['input_hash']!=digest(request):raise ValueError('Share review key reused with changed input')
            stored=verify_review_row(c,old);return {'id':old['id'],'fingerprint':old['fingerprint'],'recorded_at':old['created'],'result':stored['result'],'promotion_allowed':False}
        started=elapsed_time.monotonic();stamp=_now().isoformat()
        if shares.stamp(as_of)>shares.stamp(stamp):raise ValueError('Share review clock moved backwards')
        prior=c.execute('SELECT * FROM qr_share_bundle WHERE owner=? AND id=?',(owner,bundle_id)).fetchone()
        if prior is None or prior['fingerprint']!=fingerprint:raise ValueError('Share bundle changed during review')
        verify_row(c,prior)
        if c.execute('SELECT count(*) FROM qr_share_review WHERE owner=?',(owner,)).fetchone()[0]>=MAX_REVIEWS:raise RuntimeError('Share review quota exhausted')
        last=c.execute('SELECT max(created) FROM qr_share_review WHERE owner=?',(owner,)).fetchone()[0]
        if last and shares.stamp(last)>shares.stamp(stamp):raise ValueError('Share review clock moved backwards')
        last_bundle=c.execute('SELECT max(created) FROM qr_share_bundle WHERE owner=?',(owner,)).fetchone()[0]
        if last_bundle and shares.stamp(last_bundle)>shares.stamp(stamp):raise ValueError('Share review clock moved backwards')
        payload={'schema':REVIEW_VERSION,'request':request,'result':result,'recorded_at':stamp,'source_pins':LOADED_PINS}
        raw=json.dumps(payload,ensure_ascii=False,allow_nan=False)
        if len(raw.encode())>16*1024**2 or _bytes(c,'qr_share_review','payload',owner)+len(raw.encode())>MAX_OWNER_REVIEWS:raise RuntimeError('Share review storage quota exhausted')
        _global(c,len(raw.encode()));rid=uuid.uuid4().hex
        c.execute('INSERT INTO qr_share_review VALUES(?,?,?,?,?,?,?,?)',(rid,owner,bundle_id,key,digest(request),digest(payload),stamp,raw))
        saved=c.execute('SELECT * FROM qr_share_review WHERE id=? AND owner=?',(rid,owner)).fetchone();verify_review_row(c,saved)
        require_source()
        if _now()<shares.stamp(stamp) or elapsed_time.monotonic()-started>60:raise ValueError('Share review commit clock/budget exceeded')
        c.commit();return {'id':rid,'fingerprint':digest(payload),'recorded_at':stamp,'result':result,'promotion_allowed':False}


def inspect(owner,bundle_id):
    require_source();identity(owner);c=connect_ro()
    try:
        row=c.execute('SELECT * FROM qr_share_bundle WHERE owner=? AND id=?',(owner,bundle_id)).fetchone()
        if row is None:raise LookupError('Share bundle not found')
        value,_=verify_row(c,row);return _summary(row,value)
    finally:c.close()
