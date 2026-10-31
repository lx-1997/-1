"""Versioned storage/provenance validation, independent of live model source.

Known historical v1 envelopes may be copied intact. This never imports archived
code, replays math, grants execution or declares the stored source current.
"""
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re

VERSION='model-cost-storage-integrity-v1'
SCHEMA='qr-exact-model-archive-v1'
MAX_INPUT=16*1024**2;MAX_REPORT=32*1024**2
REQUIRED_PINS={'quant_model_cost_store.py','quant_exact_model_cost.py','quant_execution_cost_book.py',
               'quant_research_analysis.py','quant_research_engine.py','ai_fund_ledger.py','ai_fund_policy.py','quant_research_store.py','db.py'}
LOADED_SHA=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def sha(value):return isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value) is not None


def load(body,limit):
    if not isinstance(body,(str,bytes)) or not 0<len(body.encode() if isinstance(body,str) else body)<=limit:raise ValueError('Bounded v1 record required')
    def pairs(items):
        result={}
        for k,v in items:
            if k in result:raise ValueError('Duplicate v1 key')
            result[k]=v
        return result
    def integer(value):
        if len(value.lstrip('-'))>40:raise ValueError('Oversized v1 integer')
        return int(value)
    def real(value):
        result=float(value)
        if not math.isfinite(result):raise ValueError('Nonfinite v1 float')
        return result
    def invalid(_):raise ValueError('Nonfinite v1 constant')
    try:return json.loads(body,object_pairs_hook=pairs,parse_int=integer,parse_float=real,parse_constant=invalid)
    except (RecursionError,UnicodeError) as exc:raise ValueError('Invalid v1 JSON') from exc


def verify_row(c,row):
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=LOADED_SHA:raise ValueError('Loaded storage validator changed')
    value=load(row['payload'],MAX_REPORT)
    if (not isinstance(value,dict) or value.get('version')!=SCHEMA or digest(value)!=row['fingerprint']
            or value.get('recorded_at')!=row['created'] or value.get('input_sha256')!=row['input_sha']
            or value.get('request_hash')!=row['input_hash']):raise ValueError('Historical envelope identity/fingerprint differs')
    stamp=datetime.fromisoformat(row['created'])
    if stamp.tzinfo is None or stamp.utcoffset() is None:raise ValueError('Aware original recording clock required')
    if not isinstance(value.get('label'),str) or not 1<=len(value['label'].strip())<=80 or not isinstance(row['owner'],str) or not row['owner'].strip():raise ValueError('Bounded original label/owner required')
    pins=value.get('source_pins')
    if (not isinstance(pins,dict) or not REQUIRED_PINS<=set(pins) or set(pins)-REQUIRED_PINS-{'quant_model_cost_integrity.py'}
            or any(not sha(v) for v in pins.values())):raise ValueError('Unknown v1 source inventory')
    if value['request_hash']!=digest({'label':value['label'],'input_sha':row['input_sha'],'source_pins':pins}):raise ValueError('Original source-bound request differs')
    size=c.execute('SELECT length(body) FROM qr_model_cost_input WHERE owner=? AND sha=?',(row['owner'],row['input_sha'])).fetchone()
    if size is None or not 0<size[0]<=MAX_INPUT:raise ValueError('Owner-bound original missing/oversized')
    source=c.execute('SELECT body FROM qr_model_cost_input WHERE owner=? AND sha=?',(row['owner'],row['input_sha'])).fetchone()
    body=source['body']
    if not isinstance(body,bytes) or hashlib.sha256(body).hexdigest()!=row['input_sha']:raise ValueError('Original bytes changed')
    experiments=load(body,MAX_INPUT);report=value['report']
    if (not isinstance(experiments,list) or not 1<=len(experiments)<=10 or not isinstance(report,dict)
            or report.get('version')!=SCHEMA or report.get('source_pins')!=pins
            or report.get('mode')!='fixed_seen_historical_reconstruction'
            or report.get('source_authentication')!='operator_supplied_snapshot_not_externally_authenticated'
            or any(report.get(k)is not False for k in ['promotion_allowed','commercial_ready','execution_verified','tax_basis_verified','legacy_receipts_rewritten'])):
        raise ValueError('Historical report schema/scope differs')
    results=report['results']
    if not isinstance(results,list) or len(results)!=len(experiments):raise ValueError('Historical experiment inventory differs')
    ids=set()
    for e,r in zip(experiments,results):
        if (not isinstance(e,dict) or not isinstance(e.get('id'),str) or e['id'] in ids
                or r['experiment']!=e['id'] or r['original_result_digest']!=e['result_digest']
                or digest(e['result'])!=e['result_digest'] or digest(e['dataset'])!=e['result']['dataset_digest']
                or r['original_gate']!=e['result']['gate'] or r.get('original_reproduced_exactly')is not True):
            raise ValueError('Original experiment/gate/source linkage differs')
        ids.add(e['id']);book=r['cost_book'];state=book['state'];days=book['daily'];receipts=book['receipts']
        if (book.get('version')!='exact-model-cost-v1' or state.get('version')!='exact-model-cost-v1'
                or state.get('scope')!=e['id'] or state.get('source_sha256')!=pins['quant_exact_model_cost.py']
                or state.get('state_digest')!=digest({k:v for k,v in state.items() if k!='state_digest'})
                or not sha(state.get('plan_digest')) or not sha(state.get('fill_prefix_digest'))):raise ValueError('Stored model identity/checkpoint differs')
        if (not isinstance(days,list) or not 1<=len(days)<=20000 or not isinstance(receipts,list) or len(receipts)>100000
                or type(state.get('cursor'))is not int or state['cursor']!=len(days)-1
                or type(state.get('fills_processed'))is not int or state['fills_processed']!=len(receipts)
                or [d['date'] for d in days]!=[d['date'] for d in e['result']['oos']['curve']]):raise ValueError('Stored path inventory differs')
        if any(book.get(k)is not False for k in ['legacy_receipts_rewritten','promotion_allowed','execution_verified','tax_basis_verified']):raise ValueError('Stored model scope differs')
        if r['legacy_strict']['status'] not in ['strictly_reconciled','rejected']:raise ValueError('Unknown stored strict result')
        # These flags are preserved historical claims, NOT a fresh math verdict.
        if type(book.get('exact_conservation_verified'))is not bool or type(book.get('legacy_reconciliation_passed'))is not bool:raise ValueError('Invalid stored verification metadata')
    return value,body
