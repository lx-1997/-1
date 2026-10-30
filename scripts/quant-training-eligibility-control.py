#!/usr/bin/env python3
"""Run one registered paused-training eligibility control; never deploy."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from deepfocus_api.quant_training_eligibility import run_control


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('parent', 'experiments', 'events', 'evidence-dir', 'protocol', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Use a new output; do not replace prior results')
    try:
        protocol = json.loads(args.protocol.read_text())
        runner = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        if protocol['runner_sha256'] != runner:
            raise ValueError('Unregistered runner')
        raw_parent = args.parent.read_bytes()
        if hashlib.sha256(raw_parent).hexdigest() != protocol['parent_report_sha256']:
            raise ValueError('Frozen parent bytes changed')
        manifest = json.loads(args.events.read_text())
        root, sources = args.evidence_dir.resolve(), {}
        for sha, name in manifest['source_files'].items():
            path = (root/name).resolve()
            if path.parent != root or not path.is_file() or path.stat().st_size > 10*1024*1024:
                raise ValueError('Missing or out-of-scope evidence')
            raw = path.read_bytes()
            if not raw.startswith(b'%PDF-') or hashlib.sha256(raw).hexdigest() != sha:
                raise ValueError('Retained evidence changed')
            sources[sha] = raw
        result = run_control(protocol, json.loads(raw_parent),
                             json.loads(args.experiments.read_text()), manifest, sources,
                             progress=lambda exp, stage, fold: print(json.dumps(
                                 {'experiment': exp, 'stage': stage, 'fold': fold}), flush=True))
        if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != runner:
            raise ValueError('Runner changed during computation')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        print(json.dumps({'ok': True, 'promotion_allowed': False}))
        return 0
    except (ValueError, KeyError, TypeError, AssertionError, OSError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
