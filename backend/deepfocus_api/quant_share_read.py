"""Bounded read-only projections for share archives; never expose source bytes.

Old pinned versions remain metadata-readable, with quantity verification disabled.
Only current receipts may undergo the existing full read-only evidence/model check.
"""
import json
import re
import sqlite3
from . import quant_share_store as store

MAX_PAGE_BYTES=16*1024**2


def _open():
    path=store.jobs.db.data_path('.quant_research.sqlite3','DEEPFOCUS_QUANT_RESEARCH_DB_PATH').resolve()
    if not path.is_file():return None
    c=store.connect_ro()
    tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'qr_share_%'")}
    if not tables:c.close();return None
    if not {'qr_share_bundle','qr_share_source','qr_share_review'}<=tables:
        c.close();raise ValueError('Incomplete share archive schema')
    return c


def _value(row,kind):
    if len(row['payload'].encode())>16*1024**2:raise ValueError('Archive record exceeds view budget')
    def pairs(items):
        result={}
        for k,v in items:
            if k in result:raise ValueError('Duplicate archive key')
            result[k]=v
        return result
    value=json.loads(row['payload'],object_pairs_hook=pairs)
    expected=store.VERSION if kind=='bundle' else store.REVIEW_VERSION
    if store.digest(value)!=row['fingerprint'] or value.get('schema')!=expected or value.get('recorded_at')!=row['created']:
        raise ValueError('Archive metadata fingerprint changed')
    store.shares.stamp(row['created'])
    key='input' if kind=='bundle' else 'request'
    if store.digest(value[key])!=row['input_hash']:raise ValueError('Archive input fingerprint changed')
    if kind=='bundle':
        if value['stored_visible_at']!=store.boundary(row['created']) or value['visibility_policy']!=store.VISIBILITY:
            raise ValueError('Archive clock policy changed')
        expected_parts=store.components(value['input']['plan'])
        if set(expected_parts)!=set(value['components']):raise ValueError('Archive provenance inventory changed')
        for k,fp in expected_parts.items():
            p=value['components'][k]
            if p['digest']!=fp or p['visible_at']!=store.boundary(p['first_recorded_at']) or store.shares.stamp(p['first_recorded_at'])>store.shares.stamp(row['created']):
                raise ValueError('Archive component clock changed')
            if p['first_receipt_id']==row['id'] and p['first_recorded_at']!=row['created']:raise ValueError('Archive first clock changed')
    elif value['request']['mode'] not in ('as_recorded','historical_reconstruction') or value['request']['bundle_id']!=row['bundle_id']:
        raise ValueError('Archive review declaration changed')
    return value


def _current(value):
    return value.get('source_pins')==store.LOADED_PINS and store.pins()==store.LOADED_PINS


def _provenance(c,row,value):
    for name,part in value['components'].items():
        if part['first_receipt_id']==row['id']:continue
        prior=c.execute('SELECT * FROM qr_share_bundle WHERE owner=? AND id=?',(row['owner'],part['first_receipt_id'])).fetchone()
        if prior is None:raise ValueError('First archive is missing or foreign')
        original=_value(prior,'bundle')
        if original['components'].get(name)!=part or part['first_receipt_id']!=prior['id']:raise ValueError('First archive reference changed')


def _parent(c,row,value):
    parent=c.execute('SELECT * FROM qr_share_bundle WHERE owner=? AND id=?',(row['owner'],row['bundle_id'])).fetchone()
    if parent is None:raise ValueError('Owner-bound parent archive missing')
    pv=_value(parent,'bundle');_provenance(c,parent,pv)
    if value['request']['bundle_id']!=parent['id'] or value['request']['bundle_fingerprint']!=parent['fingerprint']:raise ValueError('Review archive reference changed')
    return parent,pv


def _bundle_summary(row,value):
    plan=value['input']['plan'];action=plan['action']
    return {'id':row['id'],'fingerprint':row['fingerprint'],'label':value['input']['label'],
            'recorded_at':row['created'],'symbol':action['symbol'],'kind':action['kind'],
            'position_role':plan['position_role'],'record_date':action['record_date'],
            'component_visibility':{k:v['visible_at'] for k,v in value['components'].items()},
            'source_version_current':_current(value),'verification_level':'metadata_only',
            'promotion_allowed':False,'strategy_execution_ready':False}


def _review_summary(row,value):
    return {'id':row['id'],'fingerprint':row['fingerprint'],'bundle_id':row['bundle_id'],
            'recorded_at':row['created'],'as_of':value['request']['as_of'],'mode':value['request']['mode'],
            'status':value['result']['status'],'source_version_current':_current(value),
            'verification_level':'metadata_only','promotion_allowed':False,'strategy_execution_ready':False}


def _cursor(value):
    if not value:return None
    if not isinstance(value,str) or len(value)>128 or value.count('~')!=1:raise ValueError('Invalid archive cursor')
    created,identity=value.split('~');store.shares.stamp(created)
    if not re.fullmatch('[0-9a-f]{32}',identity):raise ValueError('Invalid archive cursor')
    return created,identity


def listing(owner,*,kind='bundle',bundle_id=None,cursor=None,limit=10):
    store.identity(owner)
    if kind not in ('bundle','review') or type(limit) is not int or not 1<=limit<=10:raise ValueError('Invalid bounded archive page')
    after=_cursor(cursor);c=_open()
    if c is None:return {'items':[],'next_cursor':None}
    try:
        if bundle_id is not None and c.execute('SELECT 1 FROM qr_share_bundle WHERE owner=? AND id=?',(owner,bundle_id)).fetchone() is None:raise LookupError('Archive not found')
        table='qr_share_bundle' if kind=='bundle' else 'qr_share_review';where='owner=?';args=[owner]
        if kind=='review' and bundle_id is not None:where+=' AND bundle_id=?';args.append(bundle_id)
        if after:where+=' AND (created<? OR (created=? AND id<?))';args.extend([after[0],after[0],after[1]])
        rows=c.execute('SELECT * FROM '+table+' WHERE '+where+' ORDER BY created DESC,id DESC LIMIT ?',(*args,limit+1))
        out=[];size=0;last=None;more=False
        for row in rows:
            n=len(row['payload'].encode())
            if len(out)==limit or out and size+n>MAX_PAGE_BYTES:more=True;break
            value=_value(row,kind)
            if kind=='bundle':
                _provenance(c,row,value);item=_bundle_summary(row,value)
            else:
                _,parent_value=_parent(c,row,value);item=_review_summary(row,value)
                item['source_version_current']=item['source_version_current'] and _current(parent_value)
            out.append(item)
            size+=n;last=row['created']+'~'+row['id']
        return {'items':out,'next_cursor':last if more else None}
    finally:c.close()


def bundle(owner,identity):
    store.identity(owner);c=_open()
    if c is None:raise LookupError('Archive not found')
    try:
        row=c.execute('SELECT * FROM qr_share_bundle WHERE owner=? AND id=?',(owner,identity)).fetchone()
        if row is None:raise LookupError('Archive not found')
        value=_value(row,'bundle');result=_bundle_summary(row,value)
        _provenance(c,row,value)
        if result['source_version_current']:
            value,_=store.verify_row(c,row);result['verification_level']='evidence_and_provenance_checked'
        plan=value['input']['plan'];action=plan['action']
        result.update(action={k:action[k] for k in ('symbol','kind','record_date','ex_date','share_credit_date','unrestricted_trade_date','new_shares_per_old_share','available_at')},
                      planned_times={k:plan[k] for k in ('register_asof','recognize_asof','credit_asof')},
                      declared_old_shares=sum(r['qty'] for r in plan['closing_lots']),
                      source_count=1+int(plan.get('allotment') is not None),
                      unresolved_scope=['tax_basis','cash_NAV_orders','complete_economic_coverage','broker_authentication'])
        return result
    finally:c.close()


def review(owner,identity):
    store.identity(owner);c=_open()
    if c is None:raise LookupError('Archive not found')
    try:
        row=c.execute('SELECT * FROM qr_share_review WHERE owner=? AND id=?',(owner,identity)).fetchone()
        if row is None:raise LookupError('Archive not found')
        value=_value(row,'review');parent,pv=_parent(c,row,value)
        result=_review_summary(row,value);result['source_version_current']=_current(value) and _current(pv)
        result.update(label=pv['input']['label'],symbol=pv['input']['plan']['action']['symbol'],position_role=pv['input']['plan']['position_role'],quantity=None)
        if result['source_version_current']:
            verified=store.verify_review_row(c,row);result['verification_level']='evidence_and_quantity_checked'
            record=verified['result'];book=record['final_book']
            result['unavailable_components']=record.get('unavailable_components',[])
            if book:
                item=book['actions'][pv['input']['plan']['action']['event_id']]
                result['quantity']={'registered_old_shares':sum(item['registered_old_shares'].values()),
                    'exact_new_shares':item['exact_new_shares'],'credited':item['credited'],
                    'available_new_unrestricted_shares':record['available_new_shares'],'credit_basis':item.get('credit_basis')}
        return result
    finally:c.close()
