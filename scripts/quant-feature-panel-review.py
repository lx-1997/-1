#!/usr/bin/env python3
"""Build an explicit read-only research panel; never train or open an account."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from deepfocus_api import quant_feature_panel as panel
from deepfocus_api.quant_reference_prices import strict_json


def publish(result, output):
    raw = json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2).encode()
    if len(raw) > 4*1024**2:
        raise ValueError('Serialized panel exceeds 4MiB')
    staging = None
    try:
        with tempfile.NamedTemporaryFile(mode='wb', prefix='.quant-panel-',
                                         dir=output.parent, delete=False) as f:
            staging = Path(f.name)
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        # Same-directory hard link publishes complete bytes atomically and
        # refuses an existing destination, including a late racing writer.
        os.link(staging, output)
    finally:
        if staging is not None:
            staging.unlink(missing_ok=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db', type=Path, required=True)
    p.add_argument('--owner', required=True)
    p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if not a.db.is_absolute() or not a.db.is_file() or a.db.is_symlink():
        p.error('Existing absolute nonsymlink research database required')
    if not a.plan.is_file() or a.plan.is_symlink():
        p.error('Existing nonsymlink panel plan required')
    if a.output.exists() or not a.output.parent.is_dir():
        p.error('New output under existing parent required')
    with a.plan.open('rb') as f:
        raw = f.read(262145)
    if len(raw) > 262144:
        raise ValueError('Panel plan exceeds 256KiB')
    plan = strict_json(raw)
    os.environ['DEEPFOCUS_QUANT_RESEARCH_DB_PATH'] = str(a.db)
    result = panel.build(a.owner, plan)
    # Do not publish a result if the declaration changed while reading facts.
    with a.plan.open('rb') as f:
        unchanged = f.read(262145) == raw
    if not unchanged:
        raise ValueError('Panel declaration changed during read')
    publish(result, a.output)
    return 0 if result['status'] == 'arithmetic_available' else 2


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, LookupError, OSError, TypeError, sqlite3.Error) as exc:
        print(json.dumps({'status': 'rejected', 'error_type': type(exc).__name__}), file=sys.stderr)
        raise SystemExit(1)
