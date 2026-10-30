#!/usr/bin/env python3
"""Run a registered internal return-basis comparison, without changing a strategy."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from deepfocus_api.quant_event_training import run_controls


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('experiments', 'events', 'evidence-dir', 'protocol', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Use a new output; preserve all prior results')
    try:
        protocol = json.loads(args.protocol.read_text())
        runner = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        if runner != protocol['runner_sha256']:
            raise ValueError('Unregistered runner')
        manifest = json.loads(args.events.read_text())
        root, sources = args.evidence_dir.resolve(), {}
        for sha, name in manifest['source_files'].items():
            target = (root / name).resolve()
            if target.parent != root or not target.is_file() or target.stat().st_size > 10*1024*1024:
                raise ValueError('Missing or out-of-scope evidence')
            raw = target.read_bytes()
            if not raw.startswith(b'%PDF-') or hashlib.sha256(raw).hexdigest() != sha:
                raise ValueError('Retained evidence changed')
            sources[sha] = raw
        report = run_controls(
            protocol, json.loads(args.experiments.read_text()), manifest, sources,
            progress=lambda exp, arm, done, total: print(json.dumps(
                {'experiment': exp, 'arm': arm, 'fold': done, 'total': total}), flush=True))
        if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != runner:
            raise ValueError('Runner changed during calculation')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        print(json.dumps({'ok': True, 'promotion_allowed': False}))
    except (ValueError, KeyError, TypeError, AssertionError, OSError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
