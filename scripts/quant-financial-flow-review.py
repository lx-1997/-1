#!/usr/bin/env python3
"""Derive exact TTM flow arithmetic from retained reviewed financial vintages.

Missing components are retained with null metrics and exit 2. No provider or
application calls, strategy evaluation, PDF fact extraction or overwritten report.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from deepfocus_api.quant_financial_flows import derive_ttm,require_current_source


def strict_json(raw):
    def pairs(items):
        value={}
        for key,item in items:
            if key in value:raise ValueError('Duplicate JSON key')
            value[key]=item
        return value
    def invalid(value):raise ValueError('Nonfinite JSON')
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=invalid)


def read(path,limit):
    with path.open('rb') as f:body=f.read(limit+1)
    if len(body)>limit:raise ValueError('Retained input exceeds byte budget')
    return body


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('manifest','evidence-dir','output'):p.add_argument('--'+name,type=Path,required=True)
    for name in ('issuer-id','symbol','period-end','as-of'):p.add_argument('--'+name,required=True)
    p.add_argument('--mode',choices=['as_recorded','historical_reconstruction'],required=True)
    p.add_argument('--metrics',nargs='+',required=True);a=p.parse_args()
    if a.output.exists():p.error('New output required; preserve every prior result')
    try:
        runner=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        raw=read(a.manifest,2*1024**2);manifest=strict_json(raw);root=a.evidence_dir.resolve();sources={};paths={}
        for sha,name in manifest['source_files'].items():
            declared=root/name;path=declared.resolve()
            if declared.is_symlink() or path.parent!=root or not path.is_file():raise ValueError('Retained source missing or outside declared directory')
            body=read(path,10*1024**2);sources[sha]=body;paths[sha]=path
            if sum(map(len,sources.values()))>25*1024**2:raise ValueError('Retained source total exceeds byte budget')
        report=derive_ttm(manifest,sources,issuer_id=a.issuer_id,symbol=a.symbol,period_end=a.period_end,
            as_of=a.as_of,mode=a.mode,required_metrics=a.metrics)
        if raw!=read(a.manifest,2*1024**2) or any(read(paths[sha],10*1024**2)!=body for sha,body in sources.items()):
            raise ValueError('Retained financial source changed')
        require_current_source()
        if hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=runner:raise ValueError('Runner changed')
        report.update(runner_sha256=runner,input_manifest_sha256=hashlib.sha256(raw).hexdigest())
        with a.output.open('x') as f:json.dump(report,f,ensure_ascii=False,indent=2,allow_nan=False)
        print(json.dumps({'status':report['status'],'metrics_available':report['metrics'] is not None,
                          'usable_for_strategy':False,'promotion_allowed':False}))
        return 0 if report['status']=='arithmetic_available' else 2
    except (ValueError,TypeError,KeyError,OSError) as e:
        print(json.dumps({'status':'rejected','error':str(e)},ensure_ascii=False));return 2


if __name__=='__main__':raise SystemExit(main())
