#!/usr/bin/env python3
"""Retain official CSI300 PR/GTR/NTR index references for internal research."""
import argparse,hashlib,json,sys
from datetime import datetime,timezone
from pathlib import Path
import httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from deepfocus_api.quant_benchmark import parse_capture,VARIANTS
from deepfocus_api.quant_research_engine import digest

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--experiments',type=Path,required=True)
p.add_argument('--workspace',type=Path,required=True)
a=p.parse_args()
if not a.workspace.is_absolute() or a.workspace.exists():p.error('Use a new absolute workspace')
experiments=json.loads(a.experiments.read_text())
for e in experiments:
    if e['request']['benchmark']!='000300' or digest(e['result'])!=e['result_digest'] or digest(e['dataset'])!=e['result']['dataset_digest']:
        p.error('Unsupported benchmark or changed sealed experiment')
start=min(e['request']['start_date'] for e in experiments).replace('-','');end=max(e['request']['end_date'] for e in experiments).replace('-','')
a.workspace.mkdir(parents=True,mode=0o700);captures=[];errors=[]
pins={str(f.name):hashlib.sha256(f.read_bytes()).hexdigest() for f in [Path(__file__),Path(__file__).parents[1]/'backend/deepfocus_api/quant_benchmark.py']}
with httpx.Client(trust_env=False,timeout=20,headers={'User-Agent':'Mozilla/5.0','Referer':'https://www.csindex.com.cn/'}) as c:
    for code in VARIANTS:
        try:
            params={'indexCode':code,'startDate':start,'endDate':end};raw=bytearray()
            with c.stream('GET','https://www.csindex.com.cn/csindex-home/perf/index-perf',params=params) as r:
                r.raise_for_status();url=str(r.url)
                for chunk in r.iter_bytes():
                    raw.extend(chunk)
                    if len(raw)>4*1024*1024:raise ValueError('Benchmark response exceeds 4MiB')
            body=bytes(raw);name=code+'.json'
            with (a.workspace/name).open('xb') as f:f.write(body)
            item={'index_code':code,'url':url,'observed_at':datetime.now(timezone.utc).isoformat(),
                  'sha256':hashlib.sha256(body).hexdigest(),'file':name}
            captures.append(item);parsed=parse_capture(item,body,as_of=datetime.now(timezone.utc).isoformat())
            print(json.dumps({'index_code':code,'rows':len(parsed['rows']),'all_ohlc_present':parsed['all_ohlc_present']}),flush=True)
        except Exception as exc:errors.append({'index_code':code,'error':type(exc).__name__})
for f in [Path(__file__),Path(__file__).parents[1]/'backend/deepfocus_api/quant_benchmark.py']:
    if hashlib.sha256(f.read_bytes()).hexdigest()!=pins[f.name]:errors.append({'stage':'source_changed','file':f.name})
manifest={'schema':'csi-reference-captures-v1','captures':captures,'errors':errors,'source_pins':pins,
          'usage':'internal_research','historical_point_in_time_verified':False,'rights_verified':False}
with (a.workspace/'manifest.json').open('x') as f:json.dump(manifest,f,ensure_ascii=False,indent=2)
sys.exit(1 if errors else 0)
