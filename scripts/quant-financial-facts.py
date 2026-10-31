#!/usr/bin/env python3
"""Operator-only typed financial extraction snapshots; no public API or trading."""
import argparse,json,os,sqlite3,sys
from pathlib import Path


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--db',type=Path,required=True);p.add_argument('--owner',required=True)
    sub=p.add_subparsers(dest='command',required=True);put=sub.add_parser('put')
    put.add_argument('--parent-id',required=True);put.add_argument('--label',required=True);put.add_argument('--input',type=Path,required=True)
    put.add_argument('--supersedes-packet-id')
    get=sub.add_parser('review')
    for key in ('id','issuer-id','symbol','report-period-end','metric','column','as-of'):get.add_argument('--'+key,required=True)
    get.add_argument('--mode',choices=['as_recorded','historical_reconstruction'],required=True)
    a=p.parse_args()
    if not a.db.is_absolute() or not a.db.is_file():p.error('Explicit existing absolute QR database path required')
    os.environ['DEEPFOCUS_QUANT_RESEARCH_DB_PATH']=str(a.db);sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
    from deepfocus_api import quant_financial_fact_store as store
    try:
        if a.command=='put':
            if a.input.stat().st_size>2*1024*1024:raise ValueError('Extraction input exceeds 2MiB')
            raw=a.input.read_bytes();value=json.loads(raw)
            if set(value)!={'facts','correction_notes'}:raise ValueError('Explicit fact snapshot and correction notes required')
            if a.input.read_bytes()!=raw:raise ValueError('Extraction input changed during read')
            result=store.put(a.owner,a.parent_id,a.label,value['facts'],supersedes_packet_id=a.supersedes_packet_id,correction_notes=value['correction_notes'])
        else:
            result=store.review(a.owner,a.id,issuer_id=a.issuer_id,symbol=a.symbol,report_period_end=a.report_period_end,
                              metric=a.metric,column=a.column,as_of=a.as_of,mode=a.mode)
        print(json.dumps(result,ensure_ascii=False,allow_nan=False));return 2 if a.command=='review' and result['status']!='available' else 0
    except (ValueError,TypeError,KeyError,LookupError,OSError,RuntimeError,sqlite3.DatabaseError) as exc:
        print(json.dumps({'ok':False,'error_type':type(exc).__name__,'promotion_allowed':False}));return 1


if __name__=='__main__':raise SystemExit(main())
