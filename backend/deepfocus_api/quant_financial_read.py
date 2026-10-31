"""Bounded owner-only financial projections; never expose raw PDFs or code."""
from contextlib import contextmanager
import sqlite3
from . import quant_financial_fact_store as facts,quant_financial_fact_derived as derived
from .quant_share_read import _cursor


def _owner(owner):
    if not isinstance(owner,str) or not 1<=len(owner.strip())<=200:raise ValueError('Bounded owner required')


@contextmanager
def opened():
    path=facts.base.jobs.db.data_path('.quant_research.sqlite3','DEEPFOCUS_QUANT_RESEARCH_DB_PATH').resolve()
    if not path.is_file():yield None;return
    c=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2);c.row_factory=sqlite3.Row
    try:
        c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'qr_financial_%'")}
        if 'qr_financial_fact_packet' not in tables:yield None;return
        if not {'qr_financial_bundle','qr_financial_source'}<=tables:raise ValueError('Incomplete financial parent schema')
        yield c
    finally:c.close()


def project(row,v):
    return {'id':row['id'],'label':v['input']['label'],'revision':row['revision'],'recorded_at':row['created'],
            'stored_visible_at':v['stored_visible_at'],'supersedes_packet_id':v['input']['supersedes_packet_id'],
            'fact_count':len(v['input']['facts']),
            'archive_source_status':facts.source_profile(v),'archive_source_current':v['source_pins']==facts.LOADED_PINS,
            'promotion_allowed':False,'commercial_ready':False}


def listing(owner,*,cursor=None,limit=5):
    _owner(owner);after=_cursor(cursor)
    if type(limit)is not int or not 1<=limit<=5:raise ValueError('Bounded financial page required')
    with opened() as c:
        if c is None:return {'items':[],'next_cursor':None}
        where='owner=?';args=[owner]
        if after:where+=' AND (created<? OR (created=? AND id<?))';args.extend([after[0],after[0],after[1]])
        rows=c.execute('SELECT * FROM qr_financial_fact_packet WHERE '+where+' ORDER BY created DESC,id DESC LIMIT ?',(*args,limit+1)).fetchall()
        cache={};items=[];size=0
        for row in rows[:limit]:
            size+=len(row['payload'].encode())
            if size>10*1024**2:raise ValueError('Financial page exceeds metadata budget')
            v=facts.verify_row(c,row,cache);items.append(project(row,v))
        last=rows[limit-1] if len(rows)>limit else None
        return {'items':items,'next_cursor':last['created']+'~'+last['id'] if last is not None else None}


def detail(owner,identity):
    _owner(owner)
    with opened() as c:
        if c is None:raise LookupError('Financial extraction not found')
        row=c.execute('SELECT * FROM qr_financial_fact_packet WHERE owner=? AND id=?',(owner,identity)).fetchone()
        if row is None:raise LookupError('Financial extraction not found')
        cache={};v=facts.verify_row(c,row,cache);parent=facts._parent(c,owner,row['parent_id'],cache)
        docs={d['document_id']:d for d in parent['effective_manifest']['vintages']}
        securities=[]
        for issuer,symbol in sorted({(d['issuer_id'],d['symbol']) for d in docs.values()}):
            periods=sorted({d['period_end'] for d in docs.values() if (d['issuer_id'],d['symbol'])==(issuer,symbol)},reverse=True)
            fs=[f for f in v['input']['facts'] if (docs[f['document_id']]['issuer_id'],docs[f['document_id']]['symbol'])==(issuer,symbol)]
            stocks=sorted({(docs[f['document_id']]['period_end'],f['column'],f['period_end']) for f in fs if f['metric']=='total_assets'})
            securities.append({'issuer_id':issuer,'symbol':symbol,'report_periods':periods,
                'flow_metrics':sorted({f['metric'] for f in fs if f['metric'] in facts.FLOW_METRICS}),
                'asset_sources':[{'report_period_end':period,'column':column,'point_date':point} for period,column,point in stocks]})
        return {**project(row,v),'fingerprint':row['fingerprint'],'parent_fingerprint':v['parent_fingerprint'],
                'securities':securities,'fact_amounts_in_metadata':False,'uses_actual_extraction_clock':True,
                'accounting_comparability_verified':False,'historical_pit_feed_certified':False}


def query(owner,identity,**kwargs):
    _owner(owner)
    # Schema-only preflight gives a missing archive 404 without initializing a DB.
    # All data validation/selection/arithmetic is then one independent RO snapshot.
    with opened() as c:
        if c is None:raise LookupError('Financial extraction not found')
    value=derived.review(owner,identity,**kwargs)
    return {k:v for k,v in value.items() if k!='source_pins'}


def profitability(owner,identity,**kwargs):
    _owner(owner)
    with opened() as c:
        if c is None:raise LookupError('Financial extraction not found')
    from .quant_profitability_feature import review
    value=review(owner,identity,**kwargs)
    # Keep the exact underlying derivation from this same query, without code
    # identities or source payloads crossing the authenticated read boundary.
    return {**{k:v for k,v in value.items() if k not in {'source_pins','inputs'}},
            'inputs':{k:v for k,v in value['inputs'].items() if k!='source_pins'}}
