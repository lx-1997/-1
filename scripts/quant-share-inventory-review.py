#!/usr/bin/env python3
"""Offline operator capitalization quantity review; never orders or public API.

Explicit operator-reviewed event/evidence and declared position role required.
Unresolved fractions retain a receivable checkpoint and return 2, never rounded.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from deepfocus_api import quant_share_entitlements as shares


def load(raw):
    def pairs(items):
        d={}
        for k,v in items:
            if k in d:raise ValueError('Duplicate plan key')
            d[k]=v
        return d
    def invalid(value):raise ValueError('Non-finite plan value')
    def integer(value):
        if len(value)>30:raise ValueError('Plan integer exceeds precision budget')
        return int(value)
    def floating(value):
        if len(value)>64:raise ValueError('Plan floating value exceeds precision budget')
        result=float(value)
        if not math.isfinite(result):raise ValueError('Non-finite plan value')
        return result
    try:return json.loads(raw,object_pairs_hook=pairs,parse_constant=invalid,parse_int=integer,parse_float=floating)
    except RecursionError as exc:raise ValueError('Plan nesting exceeds parser budget') from exc


def bounded_read(path,maximum):
    with path.open('rb') as source:raw=source.read(maximum+1)
    if len(raw)>maximum:raise ValueError('Input grew beyond byte budget')
    return raw


def dump(path,value):
    with path.open('x') as stream:json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--workspace',type=Path,required=True)
    args=parser.parse_args();w=args.workspace
    if not args.plan.is_absolute() or not args.plan.is_file() or args.plan.stat().st_size>2*1024**2 or not w.is_absolute() or w.exists() or not w.parent.is_dir():
        parser.error('Bounded absolute input plan and NEW absolute workspace required')
    try:w.mkdir(mode=0o700)
    except OSError:parser.error('Cannot create isolated workspace')
    stage='plan'
    report={'schema':'capitalization-quantity-review-v1','status':'failed',
            'promotion_allowed':False,'commercial_ready':False,'strategy_execution_ready':False,
            'tax_basis_verified':False,'broker_statement_automatically_authenticated':False,
            'full_economic_coverage_verified':False,'strategy_replays':0,'production_writes':0,
            'network_requests':0,'new_paper_accounts':0,'module_sha256':shares.SOURCE_SHA,
            'reviewer_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    try:
        raw=bounded_read(args.plan,2*1024**2);(w/'input-plan.json').write_bytes(raw);plan=load(raw)
        report['plan_sha256']=hashlib.sha256(raw).hexdigest()
        if not isinstance(plan,dict):raise ValueError('Plan must be a JSON object')
        if plan['position_role'] not in ('counterfactual_model','operator_supplied_account_close'):
            raise ValueError('Explicit position provenance required')
        report['position_role']=plan['position_role']
        report['facts_role']='operator_supplied_not_automatically_verified'
        event=shares.Capitalization(**plan['action'])
        allotment=shares.Allotment(**plan['allotment']) if plan.get('allotment') is not None else None
        required={event.source_sha256}|({allotment.source_sha256} if allotment else set())
        if not isinstance(plan['sources'],dict) or set(plan['sources'])!=required:raise ValueError('Evidence paths must exactly match declared action/allotment')
        stage='evidence';sources={};(w/'evidence').mkdir()
        for sha,path in plan['sources'].items():
            source=Path(path)
            if not source.is_absolute() or not source.is_file() or source.stat().st_size>10*1024**2:
                raise ValueError('Bounded absolute PDF original required')
            sources[sha]=bounded_read(source,10*1024**2);shares.pdf_source(sha,sources)
            if sum(map(len,sources.values()))>25*1024**2:raise ValueError('Evidence bundle byte budget exceeded')
            (w/'evidence'/(sha+'.pdf')).write_bytes(sources[sha])
        (w/'module-source-archive.py').write_bytes((ROOT/'backend/deepfocus_api/quant_share_entitlements.py').read_bytes())
        stage='registration'
        book=shares.initial(plan['scope'],plan['start_after'],mode=plan['mode'])
        book=shares.register(book,event,plan['closing_lots'],sources,asof=plan['register_asof'],scope=plan['scope'])
        dump(w/'registered.json',book)
        stage='recognition'
        book=shares.recognize(book,event,sources,asof=plan['recognize_asof'],scope=plan['scope'])
        dump(w/'receivable.json',book)
        stage='credit'
        book=shares.credit(book,event,sources,asof=plan['credit_asof'],scope=plan['scope'],allotment=allotment)
        dump(w/'credited.json',book)
        report.update(status='quantity_model_completed',final_state_digest=book['state_digest'],
                      available_new_unrestricted_shares=shares.available(book,event.event_id,asof=plan['credit_asof'],scope=plan['scope']))
    except (ValueError,TypeError,KeyError,OSError) as exc:
        report.update(failed_stage=stage,error_type=type(exc).__name__,error=str(exc))
    dump(w/'result.json',report)
    print(json.dumps({'status':report['status'],'failed_stage':report.get('failed_stage'),'strategy_execution_ready':False}))
    return 0 if report['status']=='quantity_model_completed' else 2


if __name__=='__main__':raise SystemExit(main())
