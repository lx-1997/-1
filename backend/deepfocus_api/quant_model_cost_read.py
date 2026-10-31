"""Owner-only bounded projections; no math replay, schema write or raw quotes."""
from contextlib import contextmanager
from datetime import datetime
import json
from . import quant_model_cost_store as store
from .quant_share_read import _cursor

MAX_PAGE_BYTES=32*1024**2


@contextmanager
def opened():
    path=store.jobs.db.data_path('.quant_research.sqlite3','DEEPFOCUS_QUANT_RESEARCH_DB_PATH').resolve()
    if not path.is_file():yield None;return
    with store.connect_ro() as c:
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'qr_model_cost_%'")}
        if not tables:yield None;return
        if not {'qr_model_cost_archive','qr_model_cost_input'}<=tables:raise ValueError('Incomplete exact model archive schema')
        yield c


def value(c,row):
    if len(row['payload'].encode())>store.MAX_REPORT:raise ValueError('Archive exceeds view budget')
    result=json.loads(row['payload'])
    if (store.engine.digest(result)!=row['fingerprint'] or result.get('version')!=store.VERSION
            or result.get('recorded_at')!=row['created'] or result.get('input_sha256')!=row['input_sha']
            or result.get('request_hash')!=row['input_hash']):raise ValueError('Archive metadata changed')
    datetime.fromisoformat(row['created'])
    pins=result['source_pins']
    if row['input_hash']!=store.engine.digest({'label':result['label'],'input_sha':row['input_sha'],'source_pins':pins}):raise ValueError('Archive source-bound request changed')
    size=c.execute('SELECT length(body) FROM qr_model_cost_input WHERE owner=? AND sha=?',(row['owner'],row['input_sha'])).fetchone()
    if size is None or not 0<size[0]<=store.MAX_INPUT:raise ValueError('Owner-bound snapshot missing/oversized')
    source=c.execute('SELECT body FROM qr_model_cost_input WHERE owner=? AND sha=?',(row['owner'],row['input_sha'])).fetchone()
    if source is None or store.hashlib.sha256(source['body']).hexdigest()!=row['input_sha']:raise ValueError('Owner-bound snapshot missing/corrupt')
    current=pins==store.LOADED_PINS and store.pins()==store.LOADED_PINS
    if current:result,body=store.verify_row(c,row)
    else:body=source['body']
    return result,current,body


def project(row,v,current):
    return {'id':row['id'],'fingerprint':row['fingerprint'],'label':v['label'],'recorded_at':row['created'],
            'source_version_current':current,'verification_level':'stored_inputs_and_report_integrity' if current else 'metadata_only',
            'experiment_count':len(v['report']['results']),'promotion_allowed':False,'commercial_ready':False}


def listing(owner,*,cursor=None,limit=10):
    store.owner_id(owner);after=_cursor(cursor)
    if type(limit)is not int or not 1<=limit<=10:raise ValueError('Bounded archive page required')
    with opened() as c:
        if c is None:return {'items':[],'next_cursor':None}
        where='owner=?';args=[owner]
        if after:where+=' AND (created<? OR (created=? AND id<?))';args.extend([after[0],after[0],after[1]])
        rows=c.execute('SELECT * FROM qr_model_cost_archive WHERE '+where+' ORDER BY created DESC,id DESC LIMIT ?',(*args,limit+1))
        items=[];size=0;last=None;more=False
        for row in rows:
            n=len(row['payload'].encode())
            if len(items)==limit or items and size+n>MAX_PAGE_BYTES:more=True;break
            v,current,_=value(c,row);items.append(project(row,v,current));size+=n;last=row['created']+'~'+row['id']
        return {'items':items,'next_cursor':last if more else None}


def detail(owner,identity):
    store.owner_id(owner)
    with opened() as c:
        if c is None:raise LookupError('Archive not found')
        row=c.execute('SELECT * FROM qr_model_cost_archive WHERE owner=? AND id=?',(owner,identity)).fetchone()
        if row is None:raise LookupError('Archive not found')
        v,current,body=value(c,row);result=project(row,v,current);result['experiments']=None
        if not current:return result
        experiments=store.parse(body);out=[]
        for source,record in zip(experiments,v['report']['results']):
            if record['original_gate']!=source['result']['gate']:raise ValueError('Original strategy gate linkage differs')
            book=record['cost_book'];differences=book['legacy_differences']
            out.append({'experiment':record['experiment'],'original_oos_return_pct':source['result']['oos']['return_pct'],
                        'original_gate':{'passed':record['original_gate']['passed'],'reasons':record['original_gate']['reasons']},
                        'exact_conservation_verified':book['exact_conservation_verified'],'fills':len(book['receipts']),'days':len(book['daily']),
                        'legacy_strict_status':record['legacy_strict']['status'],'legacy_difference_count':len(differences),
                        'differences':[{'date':d['date'],'symbol':d['symbol'],'legacy_cents':d['legacy_cents'],
                                        'exact_model_rounded_cents':d['exact_model_rounded_cents'],'legacy_minus_exact_cents':d['legacy_minus_exact_cents']} for d in differences[:20]],
                        'differences_truncated':len(differences)>20,'rounding_residual':book['daily'][-1]['rounding_residual'],
                        'restore':{'date':record['restore']['date'],'full_state_and_outputs_equal':record['restore']['full_state_and_outputs_equal']}})
        result['experiments']=out
        return result
