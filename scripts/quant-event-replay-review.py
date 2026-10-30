#!/usr/bin/env python3
"""Run pre-registered integration controls, never select/promote a strategy."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from deepfocus_api.quant_event_replay import replay,KERNEL
from deepfocus_api.quant_dividend_book import INVESTOR
from deepfocus_api.quant_corporate_actions import CashDividend
from deepfocus_api.quant_research_analysis import diagnose
from deepfocus_api.quant_research_engine import digest

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--experiments',type=Path,required=True)
p.add_argument('--events',type=Path,required=True)
p.add_argument('--evidence-dir',type=Path,required=True)
p.add_argument('--protocol',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
a=p.parse_args()
if a.output.exists():p.error('拒绝覆盖既有实验报告')
project=Path(__file__).resolve().parents[1];protocol=json.loads(a.protocol.read_text())
if hashlib.sha256(a.experiments.read_bytes()).hexdigest()!=protocol['input_digest']:p.error('输入不符合预登记')
for name,pin in protocol['new_kernel_files'].items():
    if hashlib.sha256((project/'backend/deepfocus_api'/name).read_bytes()).hexdigest()!=pin:p.error('源码不符合预登记')
manifest=json.loads(a.events.read_text());events=[CashDividend(**e) for e in manifest['events']]
root=a.evidence_dir.resolve();sources={}
for sha in {e.source_sha256 for e in events}:
    f=(root/manifest['source_files'][sha]).resolve()
    if root not in f.parents or not f.is_file() or f.stat().st_size>10*1024*1024:p.error('原文路径无效')
    body=f.read_bytes()
    if not body.startswith(b'%PDF-'):p.error('须保留 PDF 原文')
    sources[sha]=body
xs=json.loads(a.experiments.read_text())
if [{'id':x['id'],'result_digest':x['result_digest']} for x in xs]!=protocol['experiments']:p.error('实验身份改变')
rows=[]
for x in xs:
    diagnose(x);req=x['request'];schedule=[{k:f[k] for k in ('train_end','test_start','test_end','parameters')} for f in x['result']['folds']]
    selected=[e for e in events if e.symbol in req['symbols']];results={}
    cases=[('new_engine_no_events_control',[],'historical_reconstruction'),
           ('new_engine_historical_reconstruction_two_sample_events',selected,'historical_reconstruction'),
           ('new_engine_as_recorded_two_sample_events',selected,'as_recorded')]
    if [c[0] for c in cases]!=protocol['cases']:p.error('对照组改变')
    for label,ev,mode in cases:
        results[label]=replay(x['dataset'],req['symbols'],schedule[0]['parameters'],ev,sources,
            investor=INVESTOR,mode=mode,capital=req['capital'],start=schedule[0]['test_start'],
            end=schedule[-1]['test_end'],schedule=schedule)
    # Restore across an actual registration close, before the next session's
    # receivable is created. Same plan and data: no parameter search is added.
    restore=None
    if selected:
        checkpoint=selected[0].record_date
        if schedule[0]['test_start']<=checkpoint<schedule[-1]['test_end']:
            common=dict(investor=INVESTOR,mode='historical_reconstruction',capital=req['capital'],
                        start=schedule[0]['test_start'],schedule=schedule)
            prefix=replay(x['dataset'],req['symbols'],schedule[0]['parameters'],selected,sources,end=checkpoint,**common)
            resumed=replay(x['dataset'],req['symbols'],schedule[0]['parameters'],selected,sources,
                           end=schedule[-1]['test_end'],state=json.loads(json.dumps(prefix['state'])),**common)
            complete=results[cases[1][0]]
            if prefix['fills']+resumed['fills']!=complete['fills'] or prefix['curve']+resumed['curve']!=complete['curve'] or resumed['state']!=complete['state']:
                raise ValueError('真实登记日恢复与一次性重放不一致')
            restore={'checkpoint':checkpoint,'registration_checkpoint_restore_equal':True}
    physical=lambda r:[{k:v for k,v in f.items() if k!='id'} for f in r['fills']]
    raw=results[cases[0][0]];stored=results[cases[2][0]]
    if raw['curve']!=stored['curve'] or physical(raw)!=physical(stored):raise ValueError('后补公告泄漏到了当时实存对照')
    rows.append({'experiment':x['id'],'original_result_digest':x['result_digest'],'symbols':req['symbols'],
                 'original_price_only_oos':{k:v for k,v in x['result']['oos'].items() if k!='curve'},
                 'as_recorded_control_equivalent':True,'restore_check':restore,'cases':results})
for name,pin in protocol['new_kernel_files'].items():
    if hashlib.sha256((project/'backend/deepfocus_api'/name).read_bytes()).hexdigest()!=pin:raise RuntimeError('运行时源码改变')
out={'registered_protocol':protocol,'protocol_digest':digest(protocol),'kernel':KERNEL,
     'created_at':datetime.now(timezone.utc).isoformat(),'event_manifest_digest':digest(manifest),
     'results':rows,'commercial_ready':False,'promotion_allowed':False}
a.output.parent.mkdir(parents=True,exist_ok=True)
with a.output.open('x') as f:json.dump(out,f,ensure_ascii=False,indent=2,allow_nan=False)
print(json.dumps({'output':str(a.output),'results':[{'experiment':r['experiment'],'cases':{
    name:{'return_pct':v['metrics']['return_pct'],'fills':len(v['fills']),'paused':v['state']['paused'],
          'gross_paid':v['state']['book']['paid_gross'],'tax_assessed':v['state']['book']['tax_assessed']} for name,v in r['cases'].items()}} for r in rows],
    'commercial_ready':False},ensure_ascii=False))
