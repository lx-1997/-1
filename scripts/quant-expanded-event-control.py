#!/usr/bin/env python3
"""Run already registered expanded-event controls; never choose a strategy."""
import argparse,json,hashlib
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from deepfocus_api.quant_event_controls import run_controls

p=argparse.ArgumentParser(description=__doc__)
for flag in ('experiments','events','baseline-events','evidence-dir','protocol','output'):p.add_argument('--'+flag,type=Path,required=True)
a=p.parse_args()
if a.output.exists():p.error('Use a new output file; preserve failed controls')
try:
    protocol=json.loads(a.protocol.read_text());expanded=json.loads(a.events.read_text());baseline=json.loads(a.baseline_events.read_text())
    runner_sha=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if runner_sha!=protocol['runner_sha256']:raise ValueError('Unregistered control runner')
    root=a.evidence_dir.resolve();sources={}
    for manifest in (expanded,baseline):
        for sha,name in manifest['source_files'].items():
            target=(root/name).resolve()
            if target.parent!=root or not target.is_file() or target.stat().st_size>10*1024*1024:raise ValueError('Missing or out-of-scope evidence file')
            body=target.read_bytes()
            if not body.startswith(b'%PDF-') or hashlib.sha256(body).hexdigest()!=sha:raise ValueError('Evidence bytes changed')
            sources[sha]=body
    out=run_controls(protocol,json.loads(a.experiments.read_text()),expanded,baseline,sources,
                     progress=lambda exp,arm:print(json.dumps({'experiment':exp,'arm':arm}),flush=True))
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=runner_sha:raise ValueError('Control runner changed during replay')
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('x') as f:json.dump(out,f,ensure_ascii=False,indent=2,allow_nan=False)
    summary=[{'experiment':r['experiment'],'cases':{name:{'return_pct':v['metrics']['return_pct'],
             'fills':len(v['fills']),'paused':v['state']['paused'],'paid_gross':v['state']['book']['paid_gross'],
             'tax_assessed':v['state']['book']['tax_assessed']} for name,v in r['cases'].items()}} for r in out['results']]
    print(json.dumps({'results':summary,'promotion_allowed':False},ensure_ascii=False))
except (ValueError,KeyError,TypeError,AssertionError,OSError) as exc:
    print(json.dumps({'ok':False,'error':str(exc)},ensure_ascii=False));sys.exit(1)
