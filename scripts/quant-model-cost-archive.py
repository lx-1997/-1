#!/usr/bin/env python3
"""Operator-only owner-scoped exact model archive. No uploads or order endpoint."""
import argparse,json,os,sqlite3,sys
from pathlib import Path


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--db',type=Path,required=True);p.add_argument('--owner',required=True)
    sub=p.add_subparsers(dest='command',required=True)
    put=sub.add_parser('import');put.add_argument('--experiments',type=Path,required=True);put.add_argument('--label',required=True)
    for name in ['inspect','replay']:
        parser=sub.add_parser(name);parser.add_argument('--id',required=True)
    sub.add_parser('list');a=p.parse_args()
    if not a.db.is_absolute() or not a.db.parent.is_dir():p.error('Explicit absolute research DB under existing parent required')
    if a.command!='import' and not a.db.is_file():p.error('Existing research database required')
    os.environ['DEEPFOCUS_QUANT_RESEARCH_DB_PATH']=str(a.db)
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
    from deepfocus_api import quant_model_cost_store as store
    try:
        if a.command=='import':
            if not a.experiments.is_absolute():raise ValueError('Absolute scoped experiment export required')
            with a.experiments.open('rb') as f:body=f.read(store.MAX_INPUT+1)
            result=store.put(a.owner,a.label,body)
        elif a.command=='list':result=store.list_records(a.owner)
        else:result=store.inspect(a.owner,a.id,recompute=a.command=='replay')
        print(json.dumps(result,ensure_ascii=False,allow_nan=False));return 0
    except (ValueError,KeyError,TypeError,OSError,LookupError,RuntimeError,AssertionError,OverflowError,sqlite3.DatabaseError) as exc:
        print(json.dumps({'ok':False,'error_type':type(exc).__name__,'promotion_allowed':False}));return 2


if __name__=='__main__':raise SystemExit(main())
