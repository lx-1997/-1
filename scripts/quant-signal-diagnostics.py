#!/usr/bin/env python3
"""Run a frozen signal-information diagnostic; no strategy selection or deployment."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'backend'))
from deepfocus_api.quant_signal_diagnostics import run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('experiments', 'events', 'reference', 'evidence-dir', 'protocol', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Use a new output; retain every prior diagnostic')
    try:
        inputs = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in
                  (args.experiments, args.events, args.reference, args.protocol)}
        protocol = json.loads(args.protocol.read_text())
        runner = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        if protocol['runner_sha256'] != runner or protocol['reference_sha256'] != inputs[args.reference]:
            raise ValueError('Unregistered runner or reference report')
        manifest = json.loads(args.events.read_text())
        root, sources = args.evidence_dir.resolve(), {}
        for sha, name in manifest['source_files'].items():
            target = (root/name).resolve()
            if target.parent != root or not target.is_file() or target.stat().st_size > 10*1024*1024:
                raise ValueError('Missing or out-of-scope cash-event evidence')
            body = target.read_bytes()
            if not body.startswith(b'%PDF-') or hashlib.sha256(body).hexdigest() != sha:
                raise ValueError('Cash-event source bytes changed')
            sources[sha] = body
        result = run(protocol, json.loads(args.experiments.read_text()), manifest,
                     json.loads(args.reference.read_text()), sources,
                     progress=lambda exp, window, lookback, horizon: print(json.dumps(
                         {'experiment': exp, 'window': window, 'lookback': lookback, 'horizon': horizon}), flush=True))
        if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != runner or any(
                hashlib.sha256(p.read_bytes()).hexdigest() != sha for p, sha in inputs.items()):
            raise ValueError('Runner or input file changed during diagnostic')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        print(json.dumps({'ok': True, 'promotion_allowed': False, 'nomination': None}))
        return 0
    except (ValueError, KeyError, TypeError, AssertionError, OSError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
