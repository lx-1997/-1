#!/usr/bin/env python3
"""Recompute one owned panel and report its sample structure, never its Alpha."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from deepfocus_api import quant_panel_structure as structure
from deepfocus_api.quant_reference_prices import strict_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--owner', required=True)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    a = parser.parse_args()
    if not a.db.is_absolute() or not a.db.is_file() or a.db.is_symlink():
        parser.error('Existing absolute nonsymlink database required')
    if not a.plan.is_file() or a.plan.is_symlink():
        parser.error('Existing nonsymlink plan required')
    if a.output.exists() or not a.output.parent.is_dir():
        parser.error('New output under existing parent required')
    with a.plan.open('rb') as f:
        raw = f.read(262145)
    if len(raw) > 262144:
        raise ValueError('Panel plan exceeds 256KiB')
    os.environ['DEEPFOCUS_QUANT_RESEARCH_DB_PATH'] = str(a.db)
    result = structure.build(a.owner, strict_json(raw))
    with a.plan.open('rb') as f:
        if f.read(262145) != raw:
            raise ValueError('Panel declaration changed during read')
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2).encode()
    if len(encoded) > 4*1024**2:
        raise ValueError('Structure report exceeds 4MiB')
    staging = None
    try:
        with tempfile.NamedTemporaryFile(mode='wb', prefix='.quant-structure-', dir=a.output.parent, delete=False) as f:
            staging = Path(f.name)
            f.write(encoded); f.flush(); os.fsync(f.fileno())
        os.link(staging, a.output)
    finally:
        if staging is not None:
            staging.unlink(missing_ok=True)
    # Success means structural arithmetic only, even for all-missing panels.
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, LookupError, OSError, TypeError, sqlite3.Error) as exc:
        print(json.dumps({'status': 'rejected', 'error_type': type(exc).__name__}), file=sys.stderr)
        raise SystemExit(1)
