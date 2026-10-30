#!/usr/bin/env python3
"""Read-only, bounded research-worker occupancy diagnostics; never reap or migrate."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from deepfocus_api.quant_worker_slots import process_scope


def observation(pid,recorded_scope,current_scope):
    if current_scope is None or recorded_scope!=current_scope or type(pid) is not int or pid<=0:
        return 'unknown_or_foreign_scope'
    try:os.kill(pid,0);return 'live_pid_or_pid_reused'
    except ProcessLookupError:return 'dead_pid_same_scope'
    except (PermissionError,OSError):return 'liveness_unknown'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,required=True)
    args=parser.parse_args()
    if not args.db.is_absolute() or not args.db.is_file():parser.error('Explicit existing absolute database path required')
    c=sqlite3.connect(args.db.resolve().as_uri()+'?mode=ro',uri=True)
    c.row_factory=sqlite3.Row
    try:
        c.execute('PRAGMA query_only=ON')
        c.execute('BEGIN')
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'qr_experiment' not in tables:raise ValueError('Not a research database')
        scope=process_scope();rows=[]
        if 'qr_worker_slot' in tables:
            total=c.execute('SELECT count(*) FROM qr_worker_slot').fetchone()[0]
            legacy=c.execute("SELECT count(*) FROM qr_experiment e WHERE status IN ('queued','running') AND NOT EXISTS(SELECT 1 FROM qr_worker_slot s WHERE s.experiment=e.id)").fetchone()[0]
            for row in c.execute("SELECT s.*,e.status job_status FROM qr_worker_slot s LEFT JOIN qr_experiment e ON e.id=s.experiment ORDER BY s.experiment LIMIT 256"):
                state=observation(row['pid'],row['scope'],scope)
                child='not_recorded_in_legacy_schema'
                if {'child_pid','child_scope'}<=set(row.keys()):
                    child='no_attached_child' if row['child_pid'] is None and row['child_scope'] is None else observation(row['child_pid'],row['child_scope'],scope)
                rows.append({'experiment':row['experiment'],'owner_bucket':hashlib.sha256(row['owner'].encode()).hexdigest(),
                             'phase':row['phase'],'job_status':row['job_status'], 'process_observation':state,
                             'child_process_observation':child})
        else:
            total=0;legacy=c.execute("SELECT count(*) FROM qr_experiment WHERE status IN ('queued','running')").fetchone()[0]
        print(json.dumps({'occupied_slots':total,'legacy_active_without_slot':legacy,'items':rows,'truncated':total>len(rows),
                          'read_only':True,'worker_thread_liveness_certified':False,'automatic_recovery_performed':False},ensure_ascii=False))
        return 0
    except (ValueError,sqlite3.DatabaseError):
        print(json.dumps({'ok':False,'error':'Cannot inspect research worker schema safely'}));return 1
    finally:c.close()


if __name__=='__main__':raise SystemExit(main())
