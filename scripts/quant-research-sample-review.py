#!/usr/bin/env python3
"""Register/run retained pilot-data quality, not a strategy backtest."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from deepfocus_api import quant_historical_universe as universe, quant_research_sample as sampler, quant_research_sample_quality as quality
from deepfocus_api.quant_reference_prices import strict_json, FILES as REFERENCE_FILES
from deepfocus_api.quant_corporate_actions import timestamp

FILES = sorted({'backend/deepfocus_api/' + name for name in (*universe.NAMES, *REFERENCE_FILES,
    'quant_research_sample.py', 'quant_research_sample_quality.py')} | {'scripts/' + name for name in
    ['quant-research-sample-review.py', 'plan-quant-research-sample.py', 'capture-quant-research-sample.py']})


def pins(): return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in FILES}


LOADED_PINS = pins()


def read(path, limit):
    with path.open('rb') as stream: body = stream.read(limit + 1)
    if len(body) > limit: raise ValueError('Input exceeds byte budget')
    return body


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=['register', 'run'])
    for name in ('plan', 'capture', 'universe-dir', 'protocol'): p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--output', type=Path); a = p.parse_args()
    destination = a.protocol if a.mode == 'register' else a.output
    if destination is None or destination.exists() or not destination.parent.is_dir(): p.error('New output required')
    inputs = {}
    def load(name, path, limit):
        body = read(path, limit); inputs[name] = (path, body); return strict_json(body)
    plan = load('plan', a.plan, 2 * 1024**2); queries = sampler.validate_plan(plan)
    manifest = load('capture', a.capture, 32 * 1024**2)
    historical = load('historical', a.universe_dir / 'manifest.json', 8 * 1024**2)
    if hashlib.sha256(inputs['historical'][1]).hexdigest() != plan['historical_membership_manifest_sha256']:
        raise ValueError('Historical source changed')
    if manifest['scope']['sample_plan_sha256'] != hashlib.sha256(inputs['plan'][1]).hexdigest():
        raise ValueError('Capture belongs to another registered plan')
    if (plan['collector_sha256'] != LOADED_PINS['scripts/capture-quant-research-sample.py']
            or plan['sample_source_sha256'] != LOADED_PINS['backend/deepfocus_api/quant_research_sample.py']):
        raise ValueError('Sample or collector source changed')
    if not isinstance(manifest['captures'], list) or len(manifest['captures']) != len(queries) + 2:
        raise ValueError('Incomplete response count')
    sources = {}
    for i, c in enumerate(manifest['captures']):
        if c['file'] != 'sdk-response-' + str(i) + '.bin': raise ValueError('Unsafe evidence path')
        path = a.capture.parent / c['file']
        if path.is_symlink(): raise ValueError('Symlinked raw response')
        body = read(path, 4 * 1024**2); inputs[c['file']] = (path, body); sources[c['file']] = body
    if sum(map(len, sources.values())) > 32 * 1024**2: raise ValueError('Total evidence budget exceeded')
    # Original full manifest is hash-bound, and these specific frames are
    # independently decoded again. No current-status filter enters selection.
    basic_entry = historical['results'][0]
    basic = universe.parse_basic(universe.verify_entry(a.universe_dir, basic_entry))
    for frame in basic_entry['frames']:
        path = a.universe_dir / frame['file']; inputs['catalogue:' + frame['file']] = (path, read(path, 4 * 1024**2))
    days = historical['scope']['days']
    expected = {q['code']: [d for d in days if q['start_date'] <= d <= q['end_date'] and basic[q['code']]['ipo_date'] <= d
                 and (not basic[q['code']]['out_date'] or d < basic[q['code']]['out_date'])] for q in queries}
    bindings = {'sha256': {name: hashlib.sha256(body).hexdigest() for name, (_, body) in inputs.items()},
                'source_pins': LOADED_PINS, 'rules': quality.RULES, 'expected_dates_digest': universe.digest(expected)}
    if a.mode == 'register':
        result = {'schema': 'sample-quality-protocol-v1', 'registered_at': datetime.now(timezone.utc).isoformat(), 'bindings': bindings}
    else:
        protocol = load('protocol', a.protocol, 1024**2)
        if (protocol.get('schema') != 'sample-quality-protocol-v1' or protocol['bindings'] != bindings
                or timestamp(protocol['registered_at']) > datetime.now(timezone.utc)):
            raise ValueError('Registered input, rule or source changed')
        result = {'protocol': protocol, 'quality': quality.review(plan, manifest, sources, expected),
                  'selection_audit': {'population': len(plan['eligible_codes']), 'selected': len(queries),
                    'selected_now_delisted_by_provider': [q['code'] for q in queries if basic[q['code']]['current_status'] == '0'],
                    'current_status_used_for_selection': False, 'replacements': 0}}
    for path, body in inputs.values():
        if read(path, len(body)) != body: raise ValueError('Input changed during review')
    if pins() != LOADED_PINS: raise ValueError('Loaded source changed')
    with destination.open('x') as stream: json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'output': str(destination), 'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(), 'strategy_evaluations': 0}))


if __name__ == '__main__':
    try: main()
    except (ValueError, TypeError, KeyError, OSError, AssertionError, OverflowError) as exc:
        print(json.dumps({'status': 'rejected', 'error': str(exc)}), file=sys.stderr)
        raise SystemExit(2)
