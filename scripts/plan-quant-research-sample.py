#!/usr/bin/env python3
"""Freeze outcome-blind research pilot queries from reviewed historical membership."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from deepfocus_api import quant_historical_universe as universe, quant_research_sample as sampler
from deepfocus_api.quant_reference_prices import strict_json


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('capture-dir', 'full-review', 'output'): p.add_argument('--' + name, type=Path, required=True)
    a = p.parse_args()
    if a.output.exists() or not a.output.parent.is_dir(): p.error('New output under existing parent required')
    capture = a.capture_dir.resolve(); paths = [capture / 'manifest.json', a.full_review]
    originals = {}
    for path in paths:
        with path.open('rb') as stream: body = stream.read(8 * 1024**2 + 1)
        if len(body) > 8 * 1024**2: raise ValueError('Bounded manifest/review required')
        originals[path] = body
    manifest, review = [strict_json(originals[path]) for path in paths]
    if (hashlib.sha256(originals[paths[0]]).hexdigest() != review['registration']['manifest_sha256']
            or review['full_registered_capture_complete'] is not True or review['capture_errors'] != []
            or review['independent_membership_sets_equal_all_days'] is not True
            or review['source_pins'] != universe.SOURCE_PINS):
        raise ValueError('Full historical membership evidence changed or incomplete')
    start, end = manifest['scope']['start'], manifest['scope']['end']
    entries = manifest['results']
    if entries[0]['query'] != {'method': 'query_stock_basic', 'code': ''} or entries[-1]['query'] != entries[0]['query']:
        raise ValueError('Missing bounding catalogues')
    if entries[1]['query'] != {'method': 'query_all_stock', 'day': start}: raise ValueError('Missing original start membership')
    before = universe.verify_entry(capture, entries[0]); after = universe.verify_entry(capture, entries[-1])
    if before != after: raise ValueError('Historical catalogue changed')
    joined = universe.membership(start, universe.verify_entry(capture, entries[1]), universe.parse_basic(before))
    if not joined['coverage_consistent']: raise ValueError('Historical start membership inconsistent')
    eligible = sorted(r['code'] for r in joined['members'])
    requests = sampler.sample(eligible, start, end)
    source_paths = [Path(__file__).resolve(), ROOT / 'scripts/capture-quant-research-sample.py', ROOT / 'backend/deepfocus_api/quant_research_sample.py']
    pins = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths}
    result = {'schema': 'historical-research-sample-v1', 'registered_at': datetime.now(timezone.utc).isoformat(),
              'start': start, 'end': end, 'rules': sampler.RULES, 'eligible_codes': eligible, 'requests': requests,
              'historical_membership_manifest_sha256': hashlib.sha256(originals[paths[0]]).hexdigest(),
              'full_review_sha256': hashlib.sha256(originals[paths[1]]).hexdigest(),
              'historical_source_pins': universe.SOURCE_PINS, 'source_pins': pins,
              'collector_sha256': pins['scripts/capture-quant-research-sample.py'],
              'sample_source_sha256': pins['backend/deepfocus_api/quant_research_sample.py'],
              'strategy_evaluations': 0, 'new_unseen_validation': False, 'promotion_allowed': False,
              'data_rights_verified': False, 'historical_pit_certified': False}
    sampler.validate_plan(result)
    for entry in [entries[0], entries[1], entries[-1]]:
        for frame in entry['frames']: universe.read_frame(capture, frame)
    if any(path.read_bytes() != body for path, body in originals.items()) or any(hashlib.sha256(path.read_bytes()).hexdigest() != pins[str(path.relative_to(ROOT))] for path in source_paths):
        raise ValueError('Input/source changed while registering')
    universe.require_current_source()
    with a.output.open('x') as stream: json.dump(result, stream, indent=2, ensure_ascii=False)
    print(json.dumps({'eligible': len(eligible), 'selected': len(requests), 'plan_sha256': hashlib.sha256(a.output.read_bytes()).hexdigest(), 'strategy_evaluations': 0}))


if __name__ == '__main__':
    try: main()
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(json.dumps({'status': 'rejected', 'error': str(exc)}), file=sys.stderr)
        raise SystemExit(2)
