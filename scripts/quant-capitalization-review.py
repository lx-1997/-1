#!/usr/bin/env python3
"""Offline composite cash/share accounting review. Never generate orders/signals.

Requires explicit model position/price provenance and NEW isolated workspace.
Retains rejected inputs and last fully closed checkpoint. No supplier or DB calls.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from deepfocus_api import quant_capitalization_book as book
from deepfocus_api.quant_corporate_actions import CashDividend
spec=importlib.util.spec_from_file_location('share_input',ROOT/'scripts/quant-share-inventory-review.py')
inputs=importlib.util.module_from_spec(spec);spec.loader.exec_module(inputs)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--plan',type=Path,required=True);p.add_argument('--workspace',type=Path,required=True);a=p.parse_args()
    if not a.plan.is_absolute() or not a.plan.is_file() or not a.workspace.is_absolute() or a.workspace.exists() or not a.workspace.parent.is_dir():p.error('Absolute plan and NEW isolated workspace required')
    a.workspace.mkdir(mode=0o700);w=a.workspace;stage='plan';current=None;closed=None;rows=[];size=0
    report={'schema':'composite-accounting-review-v1','status':'rejected','source_pins':book.LOADED_PINS,
            'reviewer_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'input_reader_sha256':hashlib.sha256((ROOT/'scripts/quant-share-inventory-review.py').read_bytes()).hexdigest(),
            'strategy_execution_ready':False,'promotion_allowed':False,'commercial_ready':False,
            'broker_clearing_verified':False,'complete_after_tax_nav_verified':False,
            'strategy_replays':0,'network_requests':0,'production_writes':0,'new_paper_accounts':0,
            'credit_timing':'scheduled_close_model_not_broker_confirmation'}
    try:
        raw=inputs.bounded_read(a.plan,2*1024**2);(w/'input-plan.json').write_bytes(raw);plan=inputs.load(raw)
        report['plan_sha256']=hashlib.sha256(raw).hexdigest()
        if not isinstance(plan,dict):raise ValueError('Plan object required')
        if plan['position_role'] not in ('counterfactual_model','operator_supplied_account_close') or plan['price_role'] not in ('illustrative_raw_marks','operator_supplied_retained_raw_marks'):
            raise ValueError('Explicit position and price provenance required')
        report.update(position_role=plan['position_role'],price_role=plan['price_role'],facts_role='operator_supplied_not_automatically_authenticated')
        if not isinstance(plan['days'],list) or not 1<=len(plan['days'])<=5000:raise ValueError('Bounded daily plan required')
        dates=[r['date'] for r in plan['days']]
        if dates!=sorted(set(dates)):raise ValueError('Daily plan must be unique and increasing')
        actions=[book.shares.Capitalization(**r) for r in plan['actions']];events=[CashDividend(**r) for r in plan['cash_events']]
        confirmations={d['date']:{k:book.shares.Allotment(**v) for k,v in d.get('allotments',{}).items()} for d in plan['days']}
        required={e.source_sha256 for e in actions+events}|{v.source_sha256 for values in confirmations.values() for v in values.values()}
        if not isinstance(plan['sources'],dict) or set(plan['sources'])!=required:raise ValueError('Exact original source inventory required')
        stage='evidence';sources={};(w/'evidence').mkdir();total=0
        for sha,name in plan['sources'].items():
            path=Path(name)
            if not path.is_absolute() or not path.is_file():raise ValueError('Absolute retained original required')
            body=inputs.bounded_read(path,10*1024**2);book.shares.pdf_source(sha,{sha:body});total+=len(body)
            if total>25*1024**2:raise ValueError('Evidence bundle byte budget exceeded')
            sources[sha]=body;(w/'evidence'/(sha+'.pdf')).write_bytes(body)
        (w/'source').mkdir()
        for name in book.FILES:(w/'source'/name).write_bytes((ROOT/'backend/deepfocus_api'/name).read_bytes())
        stage='initial';closed=book.initial(plan['capital'],plan['start_after'],scope=plan['scope'],investor=plan['investor'],mode=plan['mode'])
        for day in plan['days']:
            current=day['date'];stage='open'
            opened=book.open_session(closed,current,day['open_prices'],events,actions,sources,plan['sessions'],scope=plan['scope'])
            stage='fills';filled=book.execute_fills(opened,day['fills'],scope=plan['scope'])
            stage='close';candidate=book.close_session(filled,day['close_prices'],events,actions,sources,plan['sessions'],scope=plan['scope'],allotments=confirmations[current])
            row={'date':current,'open':opened['valuation'],'close':candidate['valuation'],'checkpoint_digest':candidate['state_digest']}
            size+=len(json.dumps(row,ensure_ascii=False,allow_nan=False).encode())
            if size>10*1024**2:raise ValueError('Review journal byte budget exceeded')
            rows.append(row);closed=candidate
        stage='restore';assert book.restore(json.dumps(closed),scope=plan['scope'])==closed
        unresolved=any(i['status']=='receivable' and book._outstanding(i) for i in closed['shares']['actions'].values())
        unavailable=[e.event_id for e in actions if plan['start_after']<e.record_date<=closed['cash']['cursor'] and e.event_id not in closed['shares']['actions']]
        unavailable+=['cash:'+e.event_id for e in events if plan['start_after']<e.record_date<=closed['cash']['cursor'] and e.event_id not in closed['cash']['registrations']]
        report['unavailable_declared_actions']=sorted(set(unavailable))
        report['status']='not_available_as_recorded' if unavailable else 'awaiting_allotment' if unresolved else 'composite_quantity_valuation_completed'
    except (ValueError,TypeError,KeyError,OSError,AssertionError) as exc:
        report.update(failed_stage=stage,failed_date=current,error_type=type(exc).__name__,error=str(exc))
    report.update(completed_days=len(rows),last_completed_date=rows[-1]['date'] if rows else None)
    inputs.dump(w/'daily-valuations.json',rows)
    if closed is not None:inputs.dump(w/'last-closed-checkpoint.json',closed);report['last_closed_digest']=closed['state_digest']
    inputs.dump(w/'result.json',report)
    print(json.dumps({'status':report['status'],'completed_days':len(rows),'failed_stage':report.get('failed_stage')}))
    return 0 if report['status']=='composite_quantity_valuation_completed' else 2


if __name__=='__main__':raise SystemExit(main())
