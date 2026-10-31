#!/usr/bin/env python3
"""Read one owner-bound profitability feature without opening an account."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from deepfocus_api import quant_profitability_feature as feature


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    for key in ('owner', 'id', 'issuer-id', 'symbol', 'period-end', 'as-of',
                'begin-report-period-end', 'begin-column', 'end-report-period-end', 'end-column'):
        p.add_argument('--' + key, required=True)
    p.add_argument('--mode', choices=['as_recorded', 'historical_reconstruction'], default='as_recorded')
    a = p.parse_args()
    if not a.db.is_absolute() or not a.db.is_file() or a.db.is_symlink():
        p.error('Existing absolute nonsymlink research database required')
    if a.output.exists() or not a.output.parent.is_dir():
        p.error('New output under existing parent required')
    os.environ['DEEPFOCUS_QUANT_RESEARCH_DB_PATH'] = str(a.db)
    endpoints = {'beginning': {'report_period_end': a.begin_report_period_end, 'column': a.begin_column},
                 'ending': {'report_period_end': a.end_report_period_end, 'column': a.end_column}}
    result = feature.review(a.owner, a.id, issuer_id=a.issuer_id, symbol=a.symbol,
        period_end=a.period_end, as_of=a.as_of, mode=a.mode, asset_endpoints=endpoints)
    with a.output.open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, allow_nan=False, indent=2)
    return 0 if result['status'] == 'arithmetic_available' else 2


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, LookupError, OSError, TypeError) as exc:
        print(json.dumps({'status': 'rejected', 'error_type': type(exc).__name__}), file=sys.stderr)
        raise SystemExit(1)
