#!/usr/bin/env python3
"""Read one explicitly reviewed financial panel at a declared knowledge clock."""
import argparse,json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from deepfocus_api.quant_financial_vintages import select_panel,require_current_source


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('manifest','evidence-dir','output'):p.add_argument('--'+name,type=Path,required=True)
    for name in ('issuer-id','symbol','period-end','as-of'):p.add_argument('--'+name,required=True)
    p.add_argument('--mode',choices=['as_recorded','historical_reconstruction'],required=True)
    p.add_argument('--metrics',nargs='+',required=True);a=p.parse_args()
    if a.output.exists():p.error('Use a new output file; preserve all reviews')
    try:
        raw=a.manifest.read_bytes();manifest=json.loads(raw);root=a.evidence_dir.resolve();sources={};paths={}
        for sha,name in manifest['source_files'].items():
            target=(root/name).resolve()
            if target.parent!=root or not target.is_file() or target.stat().st_size>10*1024*1024:raise ValueError('Missing or out-of-scope retained source')
            sources[sha]=target.read_bytes();paths[sha]=target
        report=select_panel(manifest,sources,issuer_id=a.issuer_id,symbol=a.symbol,period_end=a.period_end,
                            as_of=a.as_of,mode=a.mode,required_metrics=a.metrics)
        if raw!=a.manifest.read_bytes() or any(paths[sha].read_bytes()!=b for sha,b in sources.items()):
            raise ValueError('Source changed during financial review')
        require_current_source()
        with a.output.open('x') as f:json.dump(report,f,ensure_ascii=False,indent=2,allow_nan=False)
        print(json.dumps({'status':report['status'],'promotion_allowed':False}));return 0
    except (ValueError,TypeError,KeyError,OSError) as exc:
        print(json.dumps({'ok':False,'error':str(exc)},ensure_ascii=False));return 1


if __name__=='__main__':raise SystemExit(main())
