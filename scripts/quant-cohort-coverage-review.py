#!/usr/bin/env python3
"""Recompute owned financial coverage against every declared blind-cohort member."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from deepfocus_api import quant_cohort_coverage as coverage
from deepfocus_api.quant_reference_prices import strict_json


def bounded(path, limit):
    with path.open('rb') as f:
        raw = f.read(limit+1)
    if len(raw) > limit:
        raise ValueError('Coverage input exceeds byte budget')
    return raw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('db', 'sample', 'plan', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--owner', required=True)
    a = parser.parse_args()
    if not a.db.is_absolute() or not a.db.is_file() or a.db.is_symlink():
        parser.error('Existing absolute nonsymlink database required')
    if any(not p.is_file() or p.is_symlink() for p in (a.sample, a.plan)):
        parser.error('Existing nonsymlink declarations required')
    if a.output.exists() or not a.output.parent.is_dir():
        parser.error('New output under existing parent required')
    sample_raw, plan_raw = bounded(a.sample, 1024**2), bounded(a.plan, 262144)
    os.environ['DEEPFOCUS_QUANT_RESEARCH_DB_PATH'] = str(a.db)
    result = coverage.build(a.owner, strict_json(sample_raw), strict_json(plan_raw))
    if bounded(a.sample, 1024**2) != sample_raw or bounded(a.plan, 262144) != plan_raw:
        raise ValueError('Coverage declarations changed during read')
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2).encode()
    if len(encoded) > 1024**2:
        raise ValueError('Coverage report exceeds 1MiB')
    staging = None
    try:
        with tempfile.NamedTemporaryFile(mode='wb', prefix='.quant-cohort-', dir=a.output.parent, delete=False) as f:
            staging = Path(f.name)
            f.write(encoded); f.flush(); os.fsync(f.fileno())
        os.link(staging, a.output)
    finally:
        if staging is not None:
            staging.unlink(missing_ok=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, LookupError, OSError, TypeError, KeyError, sqlite3.Error) as exc:
        print(json.dumps({'status': 'rejected', 'error_type': type(exc).__name__}), file=sys.stderr)
        raise SystemExit(1)
