#!/usr/bin/env python3
"""Reproduce direction/allocation diagnostics for the two sealed failed paths."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
INPUT_SHA = '25cb54bea8b6795443cbd4d1b0ae56cfab5bfe0921fc780c6a5fcf2da210566f'
PROTOCOL_SHA = 'f6280bd20b6f34c4690e92b48cde95074b998408f3f44abd3cbed3223f961f05'
ENGINE_SHA = 'b3852688c331aebfe06515f54fc2b7a35191e90a587697f4424e771eea9893d5'


def retained(path, expected):
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 4*1024**2:
        raise ValueError('Bounded regular retained input required')
    body = path.read_bytes()
    if len(body) > 4*1024**2 or hashlib.sha256(body).hexdigest() != expected:
        raise ValueError('Retained input identity differs')
    return body


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--protocol', type=Path, default=ROOT/'docs/quant-path-attribution-protocol-20261101.json')
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args(argv)
    if not a.output.is_absolute() or not a.output.parent.is_dir() or a.output.exists():
        p.error('NEW absolute output in existing directory required')
    body = retained(a.input, INPUT_SHA)
    protocol = retained(a.protocol, PROTOCOL_SHA)
    paths = ['backend/deepfocus_api/'+n for n in ['quant_research_engine.py', 'quant_research_analysis.py', 'quant_path_attribution.py']]
    paths.append('scripts/quant-path-attribution-review.py')
    pins = {n: hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in paths}
    if pins[paths[0]] != ENGINE_SHA:
        raise ValueError('Original research engine identity differs')
    sys.path.insert(0, str(ROOT/'backend'))
    from deepfocus_api import quant_research_engine as engine, quant_research_analysis as analysis
    from deepfocus_api.quant_path_attribution import decompose
    results = []
    for e in json.loads(body):
        req = e['request']
        if engine.digest(e['result']) != e['result_digest'] or engine.digest(e['dataset']) != e['result']['dataset_digest']:
            raise ValueError('Sealed experiment differs')
        original = engine.walk_forward(e['dataset'], req['symbols'], req['benchmark'], req['capital'])
        if engine.digest(original) != e['result_digest']:
            raise ValueError('Original experiment failed exact reproduction')
        replay = analysis.replay_oos(e['dataset'], req['symbols'], e['result'], req['capital'])
        results.append({'experiment_id': e['id'], 'original_result_digest': e['result_digest'],
                        'fills': len(replay['fills']), 'original_result_reproduced_exactly': True,
                        'attribution': decompose(e['dataset'], req['symbols'], replay, req['capital'])})
    if retained(a.input, INPUT_SHA) != body or retained(a.protocol, PROTOCOL_SHA) != protocol:
        raise ValueError('Input changed during review')
    if any(hashlib.sha256((ROOT/n).read_bytes()).hexdigest() != h for n, h in pins.items()):
        raise ValueError('Source changed during review')
    report = {'schema': 'sealed-fixed-path-attribution-review-v1', 'input_sha256': INPUT_SHA,
              'protocol_sha256': PROTOCOL_SHA, 'source_pins': pins, 'results': results,
              'new_strategy_evaluations': 0, 'production_writes': 0, 'promotion_allowed': False}
    with a.output.open('x') as f:
        json.dump(report, f, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({'output': str(a.output), 'original_experiments_reproduced': len(results), 'promotion_allowed': False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
