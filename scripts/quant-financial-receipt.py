#!/usr/bin/env python3
"""Operator import/read of immutable financial receipts; no network or public upload."""
import argparse
import json
import os
import sqlite3
from pathlib import Path
import sys


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db',type=Path,required=True)
    p.add_argument('--owner',required=True)
    sub=p.add_subparsers(dest='command',required=True)
    ingest=sub.add_parser('import')
    ingest.add_argument('--manifest',type=Path,required=True)
    ingest.add_argument('--evidence-dir',type=Path,required=True)
    ingest.add_argument('--label',required=True)
    for command in ('review','ttm'):
        review=sub.add_parser(command)
        for key in ('id','issuer-id','symbol','period-end','as-of'):
            review.add_argument('--'+key,required=True)
        review.add_argument('--mode',choices=['as_recorded','historical_reconstruction'],required=True)
        review.add_argument('--metrics',nargs='+',required=True)
    a=p.parse_args()
    if not a.db.is_absolute():p.error('Explicit absolute QR database path required')
    if a.command in ('review','ttm') and not a.db.is_file():p.error('Existing QR database required')
    os.environ['DEEPFOCUS_QUANT_RESEARCH_DB_PATH']=str(a.db)
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
    from deepfocus_api import quant_financial_store as store
    try:
        if a.command=='import':
            if a.manifest.stat().st_size>2*1024*1024:raise ValueError('Financial metadata exceeds 2MiB')
            raw=a.manifest.read_bytes();manifest=json.loads(raw);root=a.evidence_dir.resolve();sources={};paths={}
            for sha,name in manifest['source_files'].items():
                target=(root/name).resolve()
                if target.parent!=root or not target.is_file() or target.stat().st_size>10*1024*1024:
                    raise ValueError('Missing or out-of-scope financial source')
                sources[sha]=target.read_bytes();paths[sha]=target
            if sum(map(len,sources.values()))>25*1024*1024:raise ValueError('Financial sources exceed 25MiB')
            if raw!=a.manifest.read_bytes() or any(paths[h].read_bytes()!=b for h,b in sources.items()):
                raise ValueError('Financial input changed during import')
            result=store.put(a.owner,a.label,manifest,sources)
        else:
            if a.command=='ttm':
                from deepfocus_api.quant_financial_ttm_store import review as query
            else:query=store.review
            result=query(a.owner,a.id,issuer_id=a.issuer_id,symbol=a.symbol,period_end=a.period_end,
                                as_of=a.as_of,mode=a.mode,required_metrics=a.metrics)
        print(json.dumps(result,ensure_ascii=False,allow_nan=False))
        return 2 if a.command=='ttm' and result['status']!='arithmetic_available' else 0
    except (ValueError,TypeError,KeyError,OSError,LookupError,RuntimeError,sqlite3.DatabaseError) as exc:
        print(json.dumps({'ok':False,'error_type':type(exc).__name__,'promotion_allowed':False}));return 1


if __name__=='__main__':raise SystemExit(main())
