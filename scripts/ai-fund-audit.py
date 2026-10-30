#!/usr/bin/env python3
"""Read-only operator access to private tick evidence; no HTTP calls or trading.

Raw inputs are internal research material. Export only to an explicit new local
file; historical trades without a journal are not reconstructed or relabelled.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from deepfocus_api import ai_fund_audit as audit

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('mode',choices=['list','verify-chain','verify-analysis','verify-execution','storage','export'])
p.add_argument('--db',type=Path,required=True)
p.add_argument('--fund',choices=['main','mammoth','rock','falcon','contra','tide'],required=True)
p.add_argument('--id')
p.add_argument('--output',type=Path)
p.add_argument('--limit',type=int,default=20)
a=p.parse_args()
if not a.db.is_absolute() or not a.db.is_file():p.error('An existing absolute database path is required')
if not 1<=a.limit<=100:p.error('Limit must be 1..100')
if a.mode in ('export','verify-analysis','verify-execution') and not a.id:p.error('--id is required')
if a.mode=='export' and (not a.output or not a.output.is_absolute() or a.output.exists()):p.error('Export requires a new absolute --output file')
c=sqlite3.connect(a.db.resolve().as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON')
try:
    if a.mode=='list':
        result={'items':[dict(r) for r in c.execute('SELECT id,sequence,ts,status,digest FROM aif_tick_audit WHERE fund_id=? ORDER BY sequence DESC LIMIT ?',(a.fund,a.limit))]}
    elif a.mode=='verify-chain':result=audit.verify_chain(c,a.fund)
    elif a.mode=='storage':result=audit.storage(c)
    else:
        record=audit.load(c,a.fund,a.id)
        if a.mode=='verify-analysis':result=audit.verify_analysis(record)
        elif a.mode=='verify-execution':
            from deepfocus_api.ai_fund_replay import verify_execution
            result=verify_execution(record)
        else:
            fd=os.open(a.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w',encoding='utf-8') as f:json.dump(record,f,ensure_ascii=False,indent=2,allow_nan=False)
            result={'output':str(a.output),'digest':record['digest'],'scope':'private_internal_research'}
    print(json.dumps(result,ensure_ascii=False))
    if a.mode=='verify-execution' and not result.get('accepted'):sys.exit(1)
except (audit.AuditError,LookupError,sqlite3.Error) as exc:
    print(json.dumps({'ok':False,'error':str(exc)},ensure_ascii=False));sys.exit(1)
finally:c.close()
