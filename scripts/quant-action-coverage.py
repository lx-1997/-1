#!/usr/bin/env python3
"""Offline catalog/implementation-filing gap review. No trading or network."""
import argparse,json,hashlib
from datetime import datetime,timezone
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from deepfocus_api.quant_action_coverage import reconcile
from deepfocus_api.quant_corporate_actions import CashDividend
from deepfocus_api.quant_research_engine import digest

code_root=Path(__file__).resolve().parents[1]
def source_pins():
    files=[Path(__file__).resolve()]+[code_root/'backend/deepfocus_api'/name for name in
        ('quant_action_coverage.py','quant_corporate_actions.py','quant_research_engine.py')]
    return {str(f.relative_to(code_root)):hashlib.sha256(f.read_bytes()).hexdigest() for f in files}
pins=source_pins()

p=argparse.ArgumentParser(description=__doc__)
for flag in ('captures','experiments','events','output'):p.add_argument('--'+flag,type=Path,required=True)
a=p.parse_args()
if a.output.exists():p.error('Use a new output path; preserve earlier reports')
def retained(root,name,limit):
    target=(root/name).resolve()
    if target.parent!=root.resolve() or not target.is_file() or target.stat().st_size>limit:
        raise ValueError('Evidence must be a bounded retained file in the manifest directory')
    return target.read_bytes()
try:
    manifest=json.loads(a.captures.read_text());event_manifest=json.loads(a.events.read_text())
    if manifest.get('schema')!='action-catalog-captures-v1' or manifest.get('errors'):
        raise ValueError('Catalog capture incomplete or unknown schema')
    captures=manifest['captures']
    bodies={c['sha256']:retained(a.captures.parent,c['body_file'],2*1024*1024) for c in captures}
    sources={sha:retained(a.events.parent,name,10*1024*1024) for sha,name in event_manifest['source_files'].items()}
    events=[CashDividend(**e) for e in event_manifest['events']]
    experiments=json.loads(a.experiments.read_text());ranges={}
    for exp in experiments:
        if digest(exp['result'])!=exp['result_digest'] or digest(exp['dataset'])!=exp['result']['dataset_digest']:
            raise ValueError('Sealed experiment content modified')
        req=exp['request']
        for symbol in req['symbols']:
            old=ranges.get(symbol,(req['start_date'],req['end_date']))
            ranges[symbol]=(min(old[0],req['start_date']),max(old[1],req['end_date']))
    report=reconcile(captures,bodies,events,sources,ranges,as_of=datetime.now(timezone.utc).isoformat())
    report['inputs']={'captures_digest':digest(manifest),'event_manifest_digest':digest(event_manifest),
                      'experiment_results':[e['result_digest'] for e in experiments]}
    if source_pins()!=pins:raise ValueError('Review source changed during execution')
    report['source_pins']=pins
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('x') as f:json.dump(report,f,ensure_ascii=False,indent=2,allow_nan=False)
    print(json.dumps({k:report[k] for k in ('in_range_catalog_rows','status_counts','economic_event_coverage_verified')},ensure_ascii=False))
except (ValueError,KeyError,TypeError,OSError) as exc:
    print(json.dumps({'ok':False,'error':str(exc)},ensure_ascii=False));sys.exit(1)
