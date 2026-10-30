#!/usr/bin/env python3
"""Run a preregistered exposure/cadence diagnostic without fitting or promotion."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'backend'))
from deepfocus_api.quant_execution_controls import run
from deepfocus_api.quant_benchmark import parse_capture


def read_bound(root, name, *, limit=10*1024*1024):
    target = (root/name).resolve()
    if target.parent != root or not target.is_file() or target.stat().st_size > limit:
        raise ValueError('Missing or out-of-scope source')
    return target.read_bytes()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('experiments', 'events', 'reference', 'evidence-dir', 'reference-source-dir',
                 'benchmark-dir', 'protocol', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists(): parser.error('Use a new output; preserve all prior runs')
    try:
        inputs = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in
                  (args.experiments, args.events, args.reference, args.protocol, args.benchmark_dir/'manifest.json')}
        protocol = json.loads(args.protocol.read_text())
        runner = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        if (protocol['runner_sha256'] != runner or protocol['reference_sha256'] != inputs[args.reference]
                or protocol['benchmark_manifest_sha256'] != inputs[args.benchmark_dir/'manifest.json']):
            raise ValueError('Unregistered runner/reference/benchmark')
        manifest = json.loads(args.events.read_text()); reference = json.loads(args.reference.read_text())
        sources = {}
        for sha, name in manifest['source_files'].items():
            body = read_bound(args.evidence_dir.resolve(), name)
            if not body.startswith(b'%PDF-') or hashlib.sha256(body).hexdigest() != sha:
                raise ValueError('Event evidence changed')
            sources[sha] = body
        archive = {n: read_bound(args.reference_source_dir.resolve(), n)
                   for n in reference['protocol']['bindings']['source_pins']}
        benchmark = json.loads((args.benchmark_dir/'manifest.json').read_text())
        captures = [c for c in benchmark['captures'] if c['index_code']=='H00300']
        if len(captures) != 1: raise ValueError('One official H00300 capture required')
        capture = captures[0]; raw = read_bound(args.benchmark_dir.resolve(), capture['file'])
        market = parse_capture(capture, raw, as_of=datetime.now(timezone.utc).isoformat())['rows']
        result = run(protocol, json.loads(args.experiments.read_text()), manifest, reference, archive,
                     market, sources, progress=lambda exp, arm: print(json.dumps({'experiment': exp, 'arm': arm}), flush=True))
        if (hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != runner
                or any(hashlib.sha256(p.read_bytes()).hexdigest() != sha for p, sha in inputs.items())
                or any(read_bound(args.reference_source_dir.resolve(), n) != b for n, b in archive.items())
                or read_bound(args.benchmark_dir.resolve(), capture['file']) != raw
                or any(read_bound(args.evidence_dir.resolve(), n) != sources[sha] for sha, n in manifest['source_files'].items())):
            raise ValueError('Input/source changed while computing')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        print(json.dumps({'ok': True, 'nomination': None, 'promotion_allowed': False}))
        return 0
    except (ValueError, KeyError, TypeError, AssertionError, OSError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
