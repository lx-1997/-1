#!/usr/bin/env python3
"""Internal operator import/review of immutable QR share receipts.

The database supplies recording times. No --recorded-at override, network,
broker order, public evidence upload or implicit migration of prior receipts.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db',type=Path,required=True);p.add_argument('--owner',required=True)
    sub=p.add_subparsers(dest='command',required=True)
    ingest=sub.add_parser('import');ingest.add_argument('--plan',type=Path,required=True);ingest.add_argument('--label',required=True)
    inspect=sub.add_parser('inspect');inspect.add_argument('--id',required=True)
    review=sub.add_parser('review')
    for k in ('id','key','as-of'):review.add_argument('--'+k,required=True)
    review.add_argument('--mode',choices=['as_recorded','historical_reconstruction'],required=True)
    review.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if not a.db.is_absolute() or not a.db.parent.is_dir():p.error('Explicit QR database path with existing parent required')
    if a.command!='import' and not a.db.is_file():p.error('Existing QR database required')
    if a.command=='review' and (not a.output.is_absolute() or a.output.exists() or not a.output.parent.is_dir()):p.error('NEW absolute report required')
    os.environ['DEEPFOCUS_QUANT_RESEARCH_DB_PATH']=str(a.db)
    root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root/'backend'))
    from deepfocus_api import quant_share_store as store
    # Reuse the bounded strict plan reader, not source bytes in any archive.
    spec=importlib.util.spec_from_file_location('share_plan_reader',root/'scripts/quant-share-inventory-review.py')
    reader=importlib.util.module_from_spec(spec);spec.loader.exec_module(reader)
    try:
        if a.command=='import':
            if not a.plan.is_absolute():raise ValueError('Absolute operator plan required')
            raw=reader.bounded_read(a.plan,2*1024**2);plan=reader.load(raw)
            if not isinstance(plan,dict) or not isinstance(plan.get('sources'),dict):raise ValueError('Explicit evidence source map required')
            sources={}
            for sha,name in plan.pop('sources').items():
                path=Path(name)
                if not path.is_absolute():raise ValueError('Absolute evidence paths required')
                sources[sha]=reader.bounded_read(path,10*1024**2)
                if sum(map(len,sources.values()))>25*1024**2:raise ValueError('Evidence bundle exceeds 25MiB')
            if raw!=reader.bounded_read(a.plan,2*1024**2):raise ValueError('Operator plan changed during import')
            result=store.put(a.owner,a.label,plan,sources)
        elif a.command=='inspect':result=store.inspect(a.owner,a.id)
        else:
            result=store.review(a.owner,a.id,a.key,as_of=a.as_of,mode=a.mode)
            with a.output.open('x') as out:json.dump(result,out,ensure_ascii=False,indent=2,allow_nan=False)
            result={'id':result['id'],'fingerprint':result['fingerprint'],'recorded_at':result['recorded_at'],
                    'status':result['result']['status'],'promotion_allowed':False}
        print(json.dumps(result,ensure_ascii=False,allow_nan=False))
        return 2 if a.command=='review' and result['status']!='quantity_model_completed' else 0
    except (ValueError,TypeError,KeyError,OSError,LookupError,RuntimeError,sqlite3.DatabaseError) as exc:
        print(json.dumps({'ok':False,'error_type':type(exc).__name__,'promotion_allowed':False}));return 2


if __name__=='__main__':raise SystemExit(main())
