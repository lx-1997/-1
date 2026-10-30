"""Private immutable tick evidence in the same transaction as paper fills.

No HTTP endpoint, credentials, broker access or historical data backfill.
Content addressing shares repeated snapshots; quotas never silently prune facts.
"""
import hashlib
import json
import math
from pathlib import Path
import platform
import sqlite3
import zlib

SCHEMA='paper-tick-audit-v2'
MAX_RAW_BLOB=4*1024*1024
MAX_TICK_RAW=12*1024*1024
MAX_STORED_BYTES=512*1024*1024
SOURCE_NAMES=('ai_fund.py','ai_fund_policy.py','ai_fund_execution.py','ai_fund_ledger.py',
              'ai_fund_audit.py','ai_fund_market.py','ai_fund_replay.py','ai_fund_replay_worker.py',
              'ai_fund_evolve.py','bull_playbook.py','emotion_cycle.py','shared_utils.py')
PINNED_SOURCE={'python':platform.python_version(),'sqlite':sqlite3.sqlite_version,'files':{
    n:Path(__file__).with_name(n).read_text() for n in SOURCE_NAMES}}


class AuditError(RuntimeError):pass


def _safe(value,depth=0):
    if depth>40:raise AuditError('Audit input nesting limit exceeded')
    if value is None or isinstance(value,(str,bool,int)):return value
    if isinstance(value,float):
        return value if math.isfinite(value) else {'__audit_nonfinite_float__':str(value)}
    if isinstance(value,(list,tuple)):return [_safe(v,depth+1) for v in value]
    if isinstance(value,dict) and all(isinstance(k,str) for k in value):
        return {k:_safe(v,depth+1) for k,v in value.items()}
    raise AuditError('Unsupported audit input type')


def encode(value):return json.dumps(_safe(value),ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def digest(value):return hashlib.sha256(encode(value)).hexdigest()
KERNEL=digest(PINNED_SOURCE)


def initialize(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS aif_audit_blob(sha TEXT PRIMARY KEY,body BLOB NOT NULL,raw_size INTEGER NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS aif_tick_audit(id TEXT PRIMARY KEY,fund_id TEXT NOT NULL,sequence INTEGER NOT NULL,ts TEXT NOT NULL,status TEXT NOT NULL,digest TEXT NOT NULL UNIQUE,manifest TEXT NOT NULL,UNIQUE(fund_id,sequence))')
    conn.execute('CREATE INDEX IF NOT EXISTS aif_audit_fund ON aif_tick_audit(fund_id,sequence DESC)')
    for table in ('aif_audit_blob','aif_tick_audit'):
        for operation in ('UPDATE','DELETE'):
            conn.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_{operation.lower()} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'immutable paper tick audit'); END")


def account(conn,fund_id):
    st=conn.execute('SELECT fund_id,cash,started_nav,last_tick_at FROM aif_state WHERE fund_id=?',(fund_id,)).fetchone()
    risk=conn.execute('SELECT peak,worst FROM aif_risk_state WHERE fund_id=?',(fund_id,)).fetchone()
    positions=[dict(p) for p in conn.execute('SELECT * FROM aif_position WHERE fund_id=? ORDER BY symbol',(fund_id,))]
    marks={p['symbol']:dict(r) if r else None for p in positions
           for r in [conn.execute('SELECT price,ts FROM aif_marks WHERE symbol=?',(p['symbol'],)).fetchone()]}
    bases={p['symbol']:r['amount'] if r else None for p in positions
           for r in [conn.execute('SELECT amount FROM aif_position_basis WHERE fund_id=? AND symbol=?',(fund_id,p['symbol'])).fetchone()]}
    return {'state':dict(st) if st else None,'risk':dict(risk) if risk else None,'positions':positions,'marks':marks,'position_basis':bases}


def append(conn,identity,fund_id,ts,status,sections):
    """Caller owns BEGIN IMMEDIATE + commit/rollback, including the fills."""
    if not conn.in_transaction:raise AuditError('Tick audit requires the fill transaction')
    if status not in {'committed','observation_only','no_quotes','unpriced_holdings','failed'}:raise AuditError('Unknown audit status')
    sections=dict(sections)
    frame=sections.get('execution_history')
    if frame and 'rows' in frame:
        names=[]
        for i in range(0,len(frame['rows']),256):
            name='_financial_history_'+str(i//256)
            if name in sections:raise AuditError('Reserved financial history section')
            sections[name]=frame['rows'][i:i+256];names.append(name)
        sections['execution_history']={'columns':frame['columns'],'count':len(frame['rows']),'chunks':names}
    packed={k:encode(v) for k,v in {**sections,'source':PINNED_SOURCE}.items()}
    if any(len(b)>MAX_RAW_BLOB for b in packed.values()) or sum(map(len,packed.values()))>MAX_TICK_RAW:
        raise AuditError('Tick audit input exceeds storage limit; fills not committed')
    refs={k:hashlib.sha256(b).hexdigest() for k,b in packed.items()}
    previous=conn.execute('SELECT id,sequence,digest FROM aif_tick_audit WHERE fund_id=? ORDER BY sequence DESC LIMIT 1',(fund_id,)).fetchone()
    existing=conn.execute('SELECT * FROM aif_tick_audit WHERE id=?',(identity,)).fetchone()
    if existing:
        old=json.loads(existing['manifest'])
        if old['refs']!=refs or old['fund_id']!=fund_id or old['status']!=status or old['ts']!=ts:
            raise AuditError('Audit identity reused with different facts')
        load(conn,fund_id,identity)
        return {'id':identity,'digest':existing['digest'],'sequence':existing['sequence']}
    if previous:load(conn,fund_id,previous['id'])
    manifest={'schema':SCHEMA,'id':identity,'fund_id':fund_id,'ts':ts,'status':status,'kernel':KERNEL,
              'sequence':previous['sequence']+1 if previous else 1,'previous_digest':previous['digest'] if previous else None,'refs':refs,
              'scope':'internal_research_only','historical_backfill':False,'input_truth':'captured_observations_not_vendor_accuracy_attestation'}
    encoded=encode(manifest)
    stored=conn.execute('SELECT coalesce(sum(length(body)),0) FROM aif_audit_blob').fetchone()[0]
    stored+=conn.execute('SELECT coalesce(sum(length(manifest)),0) FROM aif_tick_audit').fetchone()[0]
    fresh={}
    for key,raw in packed.items():
        sha=refs[key]
        row=conn.execute('SELECT body,raw_size FROM aif_audit_blob WHERE sha=?',(sha,)).fetchone()
        if row:
            if _unpack(row,sha)!=raw:raise AuditError('Stored audit blob differs from source facts')
        else:fresh[sha]=(zlib.compress(raw),len(raw))
    if stored+len(encoded)+sum(len(b) for b,_ in fresh.values())>MAX_STORED_BYTES:
        raise AuditError('Tick audit capacity reached; archive required; fills not committed')
    for sha,(body,size) in fresh.items():
        conn.execute('INSERT INTO aif_audit_blob VALUES(?,?,?)',(sha,sqlite3.Binary(body),size))
    hashed=hashlib.sha256(encoded).hexdigest()
    conn.execute('INSERT INTO aif_tick_audit VALUES(?,?,?,?,?,?,?)',
                 (identity,fund_id,manifest['sequence'],ts,status,hashed,encoded.decode()))
    return {'id':identity,'digest':hashed,'sequence':manifest['sequence']}


def _unpack(row,sha):
    if not 0<row['raw_size']<=MAX_RAW_BLOB:raise AuditError('Invalid audit blob size')
    try:
        decoder=zlib.decompressobj();raw=decoder.decompress(row['body'],MAX_RAW_BLOB+1)
    except zlib.error as e:raise AuditError('Invalid compressed audit blob') from e
    if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail or len(raw)!=row['raw_size'] or hashlib.sha256(raw).hexdigest()!=sha:
        raise AuditError('Audit blob integrity mismatch')
    return raw


def load(conn,fund_id,identity):
    row=conn.execute('SELECT * FROM aif_tick_audit WHERE id=? AND fund_id=?',(identity,fund_id)).fetchone()
    if row is None:raise LookupError('Tick audit not found')
    manifest=json.loads(row['manifest'])
    if digest(manifest)!=row['digest'] or any(manifest[k]!=row[k] for k in ('id','fund_id','sequence','ts','status')):
        raise AuditError('Tick audit manifest integrity mismatch')
    previous=conn.execute('SELECT digest FROM aif_tick_audit WHERE fund_id=? AND sequence=?',(fund_id,row['sequence']-1)).fetchone()
    if manifest['previous_digest']!=(previous['digest'] if previous else None) or (row['sequence']>1 and not previous):
        raise AuditError('Tick audit chain is incomplete')
    sections={}
    for name,sha in manifest['refs'].items():
        blob=conn.execute('SELECT body,raw_size FROM aif_audit_blob WHERE sha=?',(sha,)).fetchone()
        if blob is None:raise AuditError('Tick audit blob is missing')
        sections[name]=json.loads(_unpack(blob,sha))
    if digest(sections['source'])!=manifest['kernel']:raise AuditError('Frozen source package integrity mismatch')
    return {'manifest':manifest,'digest':row['digest'],'sections':sections}


def verify_chain(conn,fund_id):
    rows=conn.execute('SELECT id FROM aif_tick_audit WHERE fund_id=? ORDER BY sequence',(fund_id,)).fetchall()
    for row in rows:load(conn,fund_id,row['id'])
    return {'fund_id':fund_id,'verified_ticks':len(rows),'execution_replayed':False}


def verify_analysis(record):
    """Recompute scores from captured facts, never fetch today's reactions/prices.

    This verifies the analysis layer only; it is not a new OOS test or a full
    replay of execution. Frozen source is evidence, never dynamically executed.
    """
    if record['manifest']['kernel']!=KERNEL:raise AuditError('Frozen source/runtime differs; use the archived version')
    def invalid(value):
        if isinstance(value,dict):return '__audit_nonfinite_float__' in value or any(invalid(v) for v in value.values())
        return isinstance(value,list) and any(invalid(v) for v in value)
    if invalid(record['sections']):raise AuditError('Nonfinite captured input is not eligible for analysis proof')
    from datetime import datetime
    from .ai_fund import AgentConfig,_analyze
    sections=record['sections'];inputs=sections['inputs'];cfg=AgentConfig(**sections['configuration'])
    matched=[]
    for code,expected in inputs.get('analyses',{}).items():
        if 'sentiment_input' not in expected:raise AuditError('Captured sentiment input missing')
        actual=_analyze(inputs['universe'][code],inputs['quotes'][code],inputs['news'][code],
            sections['market_data'].get(code),cfg,inputs['learned_mult'],inputs['regime'].get('regime'),
            as_of=datetime.fromisoformat(inputs['analysis_at'][code]),sentiment_override=expected['sentiment_input'])
        if digest(actual)!=digest(expected):raise AuditError('Captured analysis does not reproduce: '+code)
        matched.append(code)
    return {'matched_analyses':matched,'analysis_count':len(matched),'execution_replayed':False,
            'oos_validation':False,'status':'matched' if matched else 'no_completed_analysis'}


def coverage(conn,fund_id):
    """Public metadata only; never expose private snapshots or source bundles."""
    rows=conn.execute('SELECT count(*) total,sum(CASE WHEN EXISTS(SELECT 1 FROM aif_tick_audit a WHERE a.id=t.audit_id AND a.fund_id=t.fund_id) THEN 1 ELSE 0 END) linked FROM aif_trade t WHERE fund_id=?',(fund_id,)).fetchone()
    latest=conn.execute('SELECT id,ts,status,digest FROM aif_tick_audit WHERE fund_id=? ORDER BY sequence DESC LIMIT 1',(fund_id,)).fetchone()
    linked=int(rows['linked'] or 0)
    return {'schema':SCHEMA,'linked_trades':linked,'unlinked_trades':rows['total']-linked,
            'latest_tick':dict(latest) if latest else None,'raw_access':'operator_only',
            'scope':'from_installation_only_no_historical_backfill'}


def storage(conn):
    blobs=conn.execute('SELECT count(*) n,coalesce(sum(length(body)),0) bytes FROM aif_audit_blob').fetchone()
    manifests=conn.execute('SELECT count(*) n,coalesce(sum(length(manifest)),0) bytes FROM aif_tick_audit').fetchone()
    used=blobs['bytes']+manifests['bytes']
    return {'used_bytes':used,'limit_bytes':MAX_STORED_BYTES,'blobs':blobs['n'],'ticks':manifests['n'],
            'status':'near_capacity' if used>=MAX_STORED_BYTES*.8 else 'ok',
            'capacity_policy':'no_silent_pruning_no_unjournaled_fills'}


HISTORY_COLUMNS=('rowid','id','fund_id','ts','symbol','name','side','qty','price','amount',
                 'pnl_pct','reason','fee','realized_pnl','policy_version','audit_id')


def history(conn,fund_id):
    return {'columns':list(HISTORY_COLUMNS),'rows':[list(r) for r in conn.execute(
        'SELECT '+','.join(HISTORY_COLUMNS)+' FROM aif_trade WHERE fund_id=? ORDER BY rowid',(fund_id,))]}


def history_rows(sections):
    value=sections.get('execution_history')
    if not value or value.get('columns')!=list(HISTORY_COLUMNS):raise AuditError('Financial execution history frame missing')
    rows=[]
    for name in value['chunks']:
        block=sections.get(name)
        if not isinstance(block,list) or any(len(r)!=len(HISTORY_COLUMNS) for r in block):raise AuditError('Financial execution history block missing')
        rows.extend(block)
    if len(rows)!=value['count']:raise AuditError('Financial execution history count mismatch')
    return [dict(zip(HISTORY_COLUMNS,r)) for r in rows]


def capture_frame(conn,fund_id,universe,execution_at):
    from datetime import datetime,timedelta,timezone
    day=datetime.fromisoformat(execution_at).astimezone(timezone(timedelta(hours=8))).date().isoformat()
    before=account(conn,fund_id)
    symbols=set(universe)|{p['symbol'] for p in before['positions']}
    marks={s:dict(r) if r else None for s in sorted(symbols)
           for r in [conn.execute('SELECT price,ts FROM aif_marks WHERE symbol=?',(s,)).fetchone()]}
    daily=conn.execute('SELECT * FROM aif_nav_daily WHERE fund_id=? AND date=?',(fund_id,day)).fetchone()
    return {'executed_at':execution_at,'before':before,'execution_history':history(conn,fund_id),
            'marks_before':marks,'daily_before':dict(daily) if daily else None}


def observations(conn,fund_id,ts,audit_id):
    from datetime import datetime,timedelta,timezone
    day=datetime.fromisoformat(ts).astimezone(timezone(timedelta(hours=8))).date().isoformat()
    nav=conn.execute('SELECT * FROM aif_nav WHERE fund_id=? AND ts=?',(fund_id,ts)).fetchone()
    daily=conn.execute('SELECT * FROM aif_nav_daily WHERE fund_id=? AND date=?',(fund_id,day)).fetchone()
    outbox=[dict(r) for r in conn.execute('SELECT o.* FROM aif_trade_outbox o JOIN aif_trade t ON o.trade_id=t.id WHERE t.fund_id=? AND t.audit_id=? ORDER BY o.trade_id',(fund_id,audit_id))]
    return {'nav':dict(nav) if nav else None,'daily':dict(daily) if daily else None,'outbox_at_commit':outbox}
