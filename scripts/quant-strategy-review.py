#!/usr/bin/env python3
"""Offline quant study: analyze immutable experiment exports including snapshots.

Input is an operator's scoped snapshot, not the public export without prices.
No credentials, network calls or production mutations are performed here.
"""
import argparse
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from deepfocus_api.quant_research_study import develop, validate_forward
from deepfocus_api.quant_research_engine import digest

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('mode',choices=['develop','forward'])
parser.add_argument('--input',type=Path,required=True,help='Scoped sealed experiments JSON array')
parser.add_argument('--output',type=Path,required=True,help='New output file; never overwrites an earlier study')
parser.add_argument('--protocol',type=Path,default=Path(__file__).resolve().parents[1]/'docs/quant-strategy-v2-protocol-20261030.json')
parser.add_argument('--manifest',type=Path)
parser.add_argument('--extended',type=Path,help='Fresh complete-day datasets JSON array in sealed basket order')
args=parser.parse_args()
if args.output.exists():parser.error('Output already exists; keep the original audit history')
experiments=json.loads(args.input.read_text())
if args.mode=='develop':
    protocol=json.loads(args.protocol.read_text())
    result=develop(experiments,protocol)
else:
    if not args.manifest or not args.extended:parser.error('Forward requires manifest and extended data')
    manifest=json.loads(args.manifest.read_text());extended=json.loads(args.extended.read_text())
    local=datetime.now(timezone(timedelta(hours=8)))
    cutoff=local.date() if local.strftime('%H:%M')>='16:30' else local.date()-timedelta(days=1)
    result=validate_forward(manifest,[r['dataset'] for r in experiments],extended,completed_through=cutoff.isoformat())
envelope={'created_at':datetime.now(timezone.utc).isoformat(),'study_digest':digest(result),'study':result}
args.output.parent.mkdir(parents=True,exist_ok=True)
with args.output.open('x') as f:json.dump(envelope,f,ensure_ascii=False,indent=2,allow_nan=False)
print(json.dumps({'output':str(args.output),'study_digest':envelope['study_digest'],
                  'status':result.get('forward_manifest',{}).get('status',result.get('validated'))},ensure_ascii=False))
