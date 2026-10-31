#!/usr/bin/env python3
"""Check owner correspondence in existing quarantined/cold SQLite snapshots."""
import argparse
import json
import sqlite3
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'backend'))
from deepfocus_api.quant_recovery_identity import review


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--workspace', type=Path, required=True)
    p.add_argument('--auth-snapshot', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if not a.output.is_absolute() or a.output.exists() or not a.output.parent.is_dir():
        p.error('NEW absolute output required')
    try:
        result = review(a.workspace, a.auth_snapshot)
    except (ValueError, OSError, KeyError, TypeError, sqlite3.DatabaseError) as exc:
        result = {'schema': 'quarantined-recovery-owner-correspondence-v1',
                  'correspondence_check_passed': False, 'error_type': type(exc).__name__,
                  'activation_authorized': False, 'commercial_ready': False}
    with a.output.open('x') as f: json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['correspondence_check_passed'] else 2


if __name__ == '__main__': raise SystemExit(main())
