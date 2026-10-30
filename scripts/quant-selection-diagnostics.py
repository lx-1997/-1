#!/usr/bin/env python3
"""Run a frozen selector/family diagnostic on seen data, never pick a winner."""
import argparse,hashlib,json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from deepfocus_api.quant_selection_diagnostics import run


def read_bound(root,name):
    target=(root/name).resolve()
    if target.parent!=root or not target.is_file() or target.stat().st_size>10*1024*1024:
        raise ValueError('Missing or out-of-scope evidence/source')
    return target.read_bytes()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('experiments','events','reference','reference-source-dir','evidence-dir','protocol','output'):
        p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    if a.output.exists():p.error('Use a new output; retain all previous attempts')
    try:
        files=(a.experiments,a.events,a.reference,a.protocol)
        frozen={f:hashlib.sha256(f.read_bytes()).hexdigest() for f in files}
        protocol=json.loads(a.protocol.read_text());runner=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        if protocol['runner_sha256']!=runner or protocol['reference_sha256']!=frozen[a.reference]:
            raise ValueError('Unregistered runner or historical reference bytes')
        manifest=json.loads(a.events.read_text());reference=json.loads(a.reference.read_text())
        sources={sha:read_bound(a.evidence_dir.resolve(),n) for sha,n in manifest['source_files'].items()}
        if any(not b.startswith(b'%PDF-') or hashlib.sha256(b).hexdigest()!=sha for sha,b in sources.items()):
            raise ValueError('Retained PDF bytes changed')
        archive={n:read_bound(a.reference_source_dir.resolve(),n) for n in reference['protocol']['bindings']['source_pins']}
        result=run(protocol,json.loads(a.experiments.read_text()),manifest,reference,archive,sources,
                   progress=lambda exp,kind,params:print(json.dumps({'experiment':exp,'kind':kind,'parameters':params}),flush=True))
        if (hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=runner
                or any(hashlib.sha256(f.read_bytes()).hexdigest()!=sha for f,sha in frozen.items())
                or any(read_bound(a.evidence_dir.resolve(),n)!=sources[sha] for sha,n in manifest['source_files'].items())
                or any(read_bound(a.reference_source_dir.resolve(),n)!=b for n,b in archive.items())):
            raise ValueError('Input/source changed during execution')
        a.output.parent.mkdir(parents=True,exist_ok=True)
        with a.output.open('x') as f:json.dump(result,f,ensure_ascii=False,indent=2,allow_nan=False)
        print(json.dumps({'ok':True,'promotion_allowed':False,'nomination':None}));return 0
    except (ValueError,KeyError,TypeError,AssertionError,OSError) as exc:
        print(json.dumps({'ok':False,'error':str(exc)},ensure_ascii=False));return 1


if __name__=='__main__':raise SystemExit(main())
