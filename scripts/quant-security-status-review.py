#!/usr/bin/env python3
"""Offline source-bound status coverage review, never certify order eligibility."""
import argparse,json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from deepfocus_api.quant_security_status import review,require_current_source


def read_bound(root,name):
    path=(root/name).resolve()
    if path.parent!=root or not path.is_file() or path.stat().st_size>4*1024*1024:raise ValueError('Missing or out-of-scope status source')
    return path.read_bytes()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('capture-dir','experiments','calendar','calendar-dir','output'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args()
    if a.output.exists():p.error('Use new output; preserve all capture/review attempts')
    try:
        files=(a.capture_dir/'manifest.json',a.experiments,a.calendar);raw={p:p.read_bytes() for p in files}
        manifest=json.loads(raw[files[0]]);calendar=json.loads(raw[a.calendar]);root=a.capture_dir.resolve();calroot=a.calendar_dir.resolve()
        sources={c['file']:read_bound(root,c['file']) for c in manifest['captures']}
        cal_sources={sha:read_bound(calroot,name) for sha,name in calendar['source_files'].items()}
        result=review(manifest,sources,json.loads(raw[a.experiments]),calendar,cal_sources)
        if (any(p.read_bytes()!=b for p,b in raw.items()) or any(read_bound(root,n)!=b for n,b in sources.items())
                or any(read_bound(calroot,calendar['source_files'][sha])!=b for sha,b in cal_sources.items())):
            raise ValueError('Input changed during status review')
        require_current_source()
        with a.output.open('x') as f:json.dump(result,f,ensure_ascii=False,indent=2,allow_nan=False)
        print(json.dumps({'coverage_matches':result['coverage_matches_original_and_plan'],'promotion_allowed':False}))
        return 0 if result['coverage_matches_original_and_plan'] and result['prices_match_original'] else 2
    except (ValueError,KeyError,TypeError,OSError) as exc:
        print(json.dumps({'ok':False,'error':str(exc)},ensure_ascii=False));return 1


if __name__=='__main__':raise SystemExit(main())
