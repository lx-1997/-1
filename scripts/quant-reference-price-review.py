#!/usr/bin/env python3
"""Register/run a strictly bound offline supplier-reference coverage audit."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from deepfocus_api import quant_reference_prices as audit
from deepfocus_api.quant_corporate_actions import timestamp


def read(path, limit):
    with path.open('rb') as stream: body = stream.read(limit + 1)
    if len(body) > limit: raise ValueError('Bounded input exceeds byte limit')
    return body


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=['register', 'run'])
    for name in ['experiments', 'cash-facts', 'capture', 'protocol', 'lock']:
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--output', type=Path)
    a = p.parse_args(); destination = a.lock if a.mode == 'register' else a.output
    if destination is None or destination.exists() or not destination.parent.is_dir():
        p.error('New output under existing parent required')
    inputs = {}
    def load(label, path, limit):
        body = read(path, limit); inputs[label] = (path, body)
        return audit.strict_json(body)
    experiments = load('experiments', a.experiments, 16 * 1024**2)
    facts = load('cash_facts', a.cash_facts, 4 * 1024**2)
    manifest = load('capture', a.capture, 4 * 1024**2)
    protocol = load('protocol', a.protocol, 1024**2)
    if (protocol.get('schema') != 'reference-price-coverage-protocol-v1' or protocol.get('promotion_allowed') is not False
            or protocol.get('strategy_changes') != 0 or timestamp(protocol['registered_at']) > datetime.now(timezone.utc)):
        raise ValueError('Unsupported protocol or clock')
    for name in ['experiments', 'cash_facts', 'capture']:
        if protocol[name + '_sha256'] != hashlib.sha256(inputs[name][1]).hexdigest():
            raise ValueError('Protocol input changed')
    if manifest['scope']['snapshot_sha256'] != protocol['experiments_sha256']:
        raise ValueError('Capture belongs to a different snapshot')
    if not isinstance(manifest['captures'], list) or not 3 <= len(manifest['captures']) <= 12:
        raise ValueError('Evidence response count exceeds budget')
    sources = {}
    for i, c in enumerate(manifest['captures']):
        if c['file'] != 'sdk-response-' + str(i) + '.bin': raise ValueError('Noncanonical evidence path')
        path = a.capture.parent / c['file']
        if path.is_symlink(): raise ValueError('Evidence symlink not permitted')
        body = read(path, 4 * 1024**2); sources[c['file']] = body; inputs[c['file']] = (path, body)
    if len(sources) > 12 or sum(map(len, sources.values())) > 16 * 1024**2:
        raise ValueError('Evidence scope exceeds budget')
    source_paths = [Path(__file__).resolve(), ROOT / 'scripts/capture-quant-reference-prices.py']
    source_pins = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths}
    if source_pins['scripts/capture-quant-reference-prices.py'] != manifest['collector_sha256']:
        raise ValueError('Collector no longer matches capture')
    bindings = {'input_sha256': {k: hashlib.sha256(b).hexdigest() for k, (_, b) in inputs.items()},
                'source_pins': {**audit.pins(), **source_pins}, 'rules': 'exact-and-cent-half-up-no-tolerance-v1',
                'strategy_changes': 0, 'promotion_allowed': False}
    if a.mode == 'register':
        result = {'schema': 'reference-source-lock-v1', 'registered_at': datetime.now(timezone.utc).isoformat(), 'bindings': bindings}
    else:
        lock = load('source_lock', a.lock, 1024**2)
        if (lock.get('schema') != 'reference-source-lock-v1' or lock.get('bindings') != bindings
                or timestamp(lock['registered_at']) > datetime.now(timezone.utc)):
            raise ValueError('Source/input lock changed')
        result = {'protocol': protocol, 'lock': lock, 'review': audit.review(manifest, sources, experiments, facts['events'])}
    for path, body in inputs.values():
        if read(path, len(body)) != body: raise ValueError('Input changed during review')
    if audit.pins() != audit.LOADED_PINS or any(hashlib.sha256(path.read_bytes()).hexdigest() != source_pins[str(path.relative_to(ROOT))] for path in source_paths):
        raise ValueError('Source changed during review')
    with destination.open('x') as stream: json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({'mode': a.mode, 'output': str(destination), 'sha256': hashlib.sha256(destination.read_bytes()).hexdigest()}))


if __name__ == '__main__':
    try: main()
    except (ValueError, TypeError, KeyError, OSError, AssertionError, OverflowError) as exc:
        print(json.dumps({'status': 'rejected', 'type': type(exc).__name__, 'error': str(exc)}), file=sys.stderr)
        raise SystemExit(2)
