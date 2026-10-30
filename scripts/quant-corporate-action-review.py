#!/usr/bin/env python3
"""Verify retained announcement bytes and audit gross dividend omissions offline."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from deepfocus_api.quant_corporate_actions import CashDividend, fixed_path_overlay
from deepfocus_api.quant_research_analysis import diagnose, replay_oos
from deepfocus_api.quant_research_engine import digest

def source_pins():
    root=Path(__file__).resolve().parents[1]
    paths=[root/'backend/deepfocus_api'/name for name in (
        'quant_corporate_actions.py','quant_research_analysis.py','quant_research_engine.py',
        'ai_fund_ledger.py','ai_fund_policy.py')]+[Path(__file__).resolve()]
    return {str(f.relative_to(root)):hashlib.sha256(f.read_bytes()).hexdigest() for f in paths}

code_pins=source_pins()

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--experiments',type=Path,required=True)
p.add_argument('--events',type=Path,required=True)
p.add_argument('--evidence-dir',type=Path,required=True)
p.add_argument('--mode',choices=['as_recorded','historical_reconstruction'],default='as_recorded')
p.add_argument('--output',type=Path,required=True)
a=p.parse_args()
if a.output.exists():p.error('拒绝覆盖既有研究证据')
event_manifest=json.loads(a.events.read_text());events=[CashDividend(**x) for x in event_manifest['events']]
root=a.evidence_dir.resolve();sources={}
for sha in {e.source_sha256 for e in events}:
    file=(root/event_manifest['source_files'][sha]).resolve()
    if root not in file.parents or not file.is_file() or file.stat().st_size>10*1024*1024:
        p.error('原文路径越界、缺失或超过10MiB')
    body=file.read_bytes()
    if not body.startswith(b'%PDF-'):p.error('证据不是PDF原文')
    sources[sha]=body
out=[]
for x in json.loads(a.experiments.read_text()):
    diagnose(x)  # Verify both sealed data and the original exact result first.
    req=x['request'];selected=[e for e in events if e.symbol in req['symbols']]
    run=replay_oos(x['dataset'],req['symbols'],x['result'],req['capital'])
    result=fixed_path_overlay(x['dataset'],req['symbols'],run,selected,sources,starts_flat=True,mode=a.mode)
    result.update(experiment=x['id'],original_result_digest=x['result_digest'],capital=req['capital'])
    out.append(result)
if source_pins()!=code_pins:raise RuntimeError('核验过程中源码改变，拒绝输出混合版本证据')
result={'created_at':datetime.now(timezone.utc).isoformat(),'event_manifest_digest':digest(event_manifest),'source_pins':code_pins,
        'mode':a.mode,'results':out,'commercial_ready':False,'promotion_allowed':False}
a.output.parent.mkdir(parents=True,exist_ok=True)
with a.output.open('x') as f:json.dump(result,f,ensure_ascii=False,indent=2,allow_nan=False)
print(json.dumps({'output':str(a.output),'gross_payments':[r['gross_cash_paid'] for r in out],
                  'complete_action_coverage':False,'commercial_ready':False},ensure_ascii=False))
