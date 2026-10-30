#!/usr/bin/env python3
"""Offline full-range universe consistency review; not an order permission."""
import argparse,json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from deepfocus_api import quant_historical_universe as u


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('capture-dir','experiments','calendar','calendar-dir','output'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args()
    if a.output.exists():p.error('New output required; retain all source/review attempts')
    try:
        root=a.capture_dir.resolve();paths=(root/'manifest.json',root/'capture-plan.json',a.experiments,a.calendar)
        original={p:p.read_bytes() for p in paths};m=json.loads(original[paths[0]]);plan=json.loads(original[paths[1]])
        calendar=json.loads(original[a.calendar]);sources={};calroot=a.calendar_dir.resolve()
        for sha,name in calendar['source_files'].items():
            file=(calroot/name).resolve()
            if file.parent!=calroot or not file.is_file() or file.stat().st_size>2*1024*1024:raise ValueError('Missing calendar evidence')
            sources[sha]=file.read_bytes()
        scope=u.collection_scope(json.loads(original[a.experiments]),calendar,sources)
        if m['plan_digest']!=u.digest(plan) or plan['scope']!=scope:raise ValueError('Capture plan binding changed')
        result=u.review(root,m,scope)
        # Recheck the exact compressed files, not current re-fetched membership.
        for entry in m['results']:
            for frame in entry['frames']:u.read_frame(root,frame)
        if any(p.read_bytes()!=b for p,b in original.items()):raise ValueError('Input changed during universe review')
        for sha,name in calendar['source_files'].items():
            if (calroot/name).read_bytes()!=sources[sha]:raise ValueError('Calendar source changed during review')
        u.require_current_source()
        with a.output.open('x') as f:json.dump(result,f,ensure_ascii=False,indent=2,allow_nan=False)
        print(json.dumps({'days':result['days'],'daily_joins_consistent':result['all_daily_joins_consistent'],'promotion_allowed':False}))
        return 0 if result['all_daily_joins_consistent'] else 2
    except (ValueError,KeyError,TypeError,OSError) as exc:
        print(json.dumps({'ok':False,'error':str(exc)},ensure_ascii=False));return 1


if __name__=='__main__':raise SystemExit(main())
