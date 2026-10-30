#!/usr/bin/env python3
"""Compare sealed internal research dates with retained official annual notices."""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'backend'))
from deepfocus_api.quant_exchange_schedule import validate_manifest, check_dataset
from deepfocus_api.quant_research_analysis import diagnose
from deepfocus_api.quant_research_engine import digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('calendar', 'evidence-dir', 'experiments', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Use a new output; preserve prior reviews')
    try:
        paths = [Path(__file__), Path(__file__).parents[1]/'backend/deepfocus_api/quant_exchange_schedule.py',
                 Path(__file__).parents[1]/'backend/deepfocus_api/quant_research_engine.py',
                 Path(__file__).parents[1]/'backend/deepfocus_api/quant_research_analysis.py']
        pins = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        inputs = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (args.calendar, args.experiments)}
        clock = datetime.now(timezone.utc).isoformat()
        manifest = json.loads(args.calendar.read_text())
        sources, root = {}, args.evidence_dir.resolve()
        for sha, name in manifest['source_files'].items():
            path = (root/name).resolve()
            if path.parent != root or not path.is_file() or path.stat().st_size > 2*1024*1024:
                raise ValueError('Missing or out-of-scope official notice')
            sources[sha] = path.read_bytes()
        validate_manifest(manifest, sources, as_of=clock)
        experiments = json.loads(args.experiments.read_text())
        if not experiments or len(experiments)>32 or len({e['id'] for e in experiments}) != len(experiments):
            raise ValueError('Nonempty, unique, bounded sealed experiment scope required')
        rows = []
        for exp in experiments:
            proof = diagnose(exp)
            req = exp['request']
            quality = check_dataset(manifest, exp['dataset'], req['start_date'], req['end_date'], as_of=clock)
            # A common interior omission would survive the old mutual check.
            dates = [b['date'] for b in exp['dataset'][req['benchmark']]['bars']]
            cut = dates[len(dates)//2]
            changed = copy.deepcopy(exp['dataset'])
            for payload in changed.values():
                payload['bars'] = [b for b in payload['bars'] if b['date'] != cut]
            fault = check_dataset(manifest, changed, req['start_date'], req['end_date'], as_of=clock)
            if fault['status'] != 'rejected' or not all(cut in v['missing_planned_sessions'] for v in fault['symbols'].values()):
                raise ValueError('Common-date omission was not detected independently')
            rows.append({'experiment': exp['id'], 'original_reproduced_exactly': proof['reproduced_exactly'],
                         'original_result_digest': exp['result_digest'], 'quality': quality,
                         'fault_injection': {'kind': 'isolated_common_interior_omission_not_provider_defect',
                                            'removed_date': cut, 'report': fault}})
        if any(hashlib.sha256(p.read_bytes()).hexdigest() != sha for p, sha in inputs.items()):
            raise ValueError('Review input files changed during calculation')
        if any(hashlib.sha256(p.read_bytes()).hexdigest() != pins[p.name] for p in paths):
            raise ValueError('Review source changed during calculation')
        report = {'schema': 'official-planned-session-review-v1', 'created_at': clock,
                  'calendar_manifest_digest': digest(manifest), 'source_pins': pins,
                  'input_sha256': {p.name: sha for p, sha in inputs.items()}, 'results': rows,
                  'promotion_allowed': False, 'production_deployed': False,
                  'actual_exchange_sessions_certified': False,
                  'limitation': 'annual notices only; exceptional closures and security status not certified'}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        matches = all(r['quality']['status']=='matches_plan' for r in rows)
        print(json.dumps({'ok': matches, 'review_completed': True, 'cases': len(rows),
                          'all_match_plan': matches}))
        return 0 if matches else 1
    except (ValueError, KeyError, TypeError, AssertionError, OSError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
