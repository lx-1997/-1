"""Read-only owner correspondence for quarantined research and cold auth copies.

Matching identifiers is necessary, but does not prove historical identity
continuity, atomic cross-database backup, JWT/session recovery or activation.
"""
import hashlib
from pathlib import Path
import sqlite3
import time
from . import quant_recovery as recovery

SOURCE_SHA = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def require_current_source():
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != SOURCE_SHA:
        raise ValueError('Loaded identity checker source changed')


def links(research, auth):
    tables, _ = recovery.schema(research)
    owners = set()
    counts = {}
    for table in tables:
        columns = {r['name'] for r in research.execute('PRAGMA table_info('+table+')')}
        if 'owner' not in columns:
            continue
        if research.execute('SELECT 1 FROM '+table+" WHERE typeof(owner)!='text' OR length(owner)>200 OR length(owner)=0 LIMIT 1").fetchone():
            raise ValueError('Invalid or oversized archived owner')
        rows = research.execute('SELECT owner,count(*) AS n FROM '+table+' GROUP BY owner LIMIT 10001').fetchall()
        if len(rows) > 10000:
            raise ValueError('Owner inventory exceeds supported limit')
        for row in rows:
            owner = row['owner']
            if not isinstance(owner, str) or not 1 <= len(owner.strip()) <= 200:
                raise ValueError('Invalid archived owner')
            owners.add(owner)
        if len(owners) > 10000:
            raise ValueError('Owner inventory exceeds supported limit')
        counts[table] = sum(row['n'] for row in rows)
    missing = inactive = 0
    for owner in sorted(owners):
        # Do not select passwords, contacts, membership, secrets or sessions.
        rows = auth.execute('SELECT id,is_active FROM auth_users WHERE id=? LIMIT 2', (owner,)).fetchall()
        if len(rows) > 1:
            raise ValueError('Ambiguous auth identity')
        if not rows:
            missing += 1
        else:
            if rows[0]['id'] != owner or type(rows[0]['is_active']) is not int or rows[0]['is_active'] not in (0, 1):
                raise ValueError('Invalid auth identity state')
            inactive += rows[0]['is_active'] == 0
    return {'archived_owner_count': len(owners), 'missing_auth_owner_count': missing,
            'inactive_auth_owner_count': inactive, 'owner_rows_by_table': counts,
            'all_identifiers_present': bool(owners) and missing == 0,
            'all_owner_logins_active': bool(owners) and missing == 0 and inactive == 0,
            'correspondence_check_passed': bool(owners) and missing == 0 and inactive == 0}


def _cold(path):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink() or not path.is_file() or path.stat().st_size > 1024**3:
        raise ValueError('Bounded absolute cold database required')
    if any(Path(str(path)+suffix).exists() for suffix in ('-wal', '-journal')):
        raise ValueError('Use a closed DELETE-mode snapshot without live journals')
    return path


def review(workspace, auth_snapshot):
    require_current_source()
    workspace = Path(workspace)
    research_path = _cold(workspace/'research.sqlite3')
    auth_path = _cold(auth_snapshot)
    if research_path.resolve() == auth_path.resolve():
        raise ValueError('Distinct research and auth snapshots required')
    before = {str(p): recovery.file_hash(p) for p in (research_path, auth_path)}
    proof = recovery.verify(workspace)
    connections = []
    started = time.monotonic()
    try:
        for path in (research_path, auth_path):
            c = recovery.connect_ro(path)
            connections.append(c)
            c.set_progress_handler(lambda: int(time.monotonic()-started > 10), 1000)
            if c.execute('PRAGMA journal_mode').fetchone()[0] != 'delete':
                raise ValueError('Cold DELETE-mode snapshots required')
            c.execute('BEGIN')
        result = links(*connections)
    finally:
        for c in connections: c.close()
    if any(recovery.file_hash(Path(p)) != h for p, h in before.items()):
        raise ValueError('Cold snapshot changed during identity check')
    for p in (research_path, auth_path): _cold(p)
    require_current_source()
    return {'schema': 'quarantined-recovery-owner-correspondence-v1', **result,
            'research_database_sha256': proof['database_sha256'],
            'auth_snapshot_sha256': before[str(auth_path)], 'source_sha256': SOURCE_SHA,
            'research_recovery_verified': True, 'snapshots_unchanged': True,
            'identity_continuity_verified': False, 'atomic_cross_database_backup_verified': False,
            'JWT_and_sessions_restored': False, 'owner_rewritten': False,
            'activation_authorized': False, 'commercial_ready': False}
