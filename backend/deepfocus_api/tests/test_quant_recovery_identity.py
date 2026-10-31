import json
import sqlite3
import pytest
from deepfocus_api.quant_recovery_identity import links, _cold
from deepfocus_api import quant_recovery_identity as identity


@pytest.fixture
def databases():
    research, auth = sqlite3.connect(':memory:'), sqlite3.connect(':memory:')
    for c in (research, auth): c.row_factory = sqlite3.Row
    for table in ('qr_experiment', 'qr_paper'):
        research.execute('CREATE TABLE '+table+'(id TEXT PRIMARY KEY, owner TEXT, payload TEXT)')
    auth.execute('CREATE TABLE auth_users(id TEXT, is_active INTEGER, username TEXT, password_hash TEXT)')
    try: yield research, auth
    finally: research.close(); auth.close()


def seed(research, auth, owner='archive-A', active=1):
    research.execute("INSERT INTO qr_experiment VALUES('one',?,'{}')", (owner,))
    auth.execute("INSERT INTO auth_users VALUES(?,?,?,?)", (owner, active, 'other-name', 'NEVER-SELECT-THIS'))


def test_all_owner_tables_checked_without_secrets_or_mutation(databases):
    research, auth = databases; seed(research, auth)
    research.execute("INSERT INTO qr_paper VALUES('two','archive-B','{}')")
    trace = []; auth.set_trace_callback(trace.append)
    before = research.total_changes, auth.total_changes
    result = links(research, auth)
    assert result['archived_owner_count'] == 2 and result['missing_auth_owner_count'] == 1
    assert not result['correspondence_check_passed']
    assert result['owner_rows_by_table'] == {'qr_experiment': 1, 'qr_paper': 1}
    assert (research.total_changes, auth.total_changes) == before
    assert all('password_hash' not in sql and 'username' not in sql and 'SELECT *' not in sql for sql in trace)
    assert 'archive-A' not in json.dumps(result) and 'NEVER-SELECT' not in json.dumps(result)


def test_exact_ids_present_and_active_pass_correspondence_only(databases):
    seed(*databases); result = links(*databases)
    assert result['all_identifiers_present'] and result['all_owner_logins_active']
    assert result['correspondence_check_passed']


def test_matching_username_cannot_replace_missing_original_id(databases):
    research, auth = databases; seed(research, auth)
    auth.execute("UPDATE auth_users SET id='replacement',username='archive-A'")
    result = links(research, auth)
    assert result['missing_auth_owner_count'] == 1 and not result['correspondence_check_passed']


def test_disabled_owner_is_not_silently_reactivated(databases):
    research, auth = databases; seed(research, auth, active=0)
    result = links(research, auth)
    assert result['all_identifiers_present'] and result['inactive_auth_owner_count'] == 1
    assert not result['correspondence_check_passed']
    assert auth.execute('SELECT is_active FROM auth_users').fetchone()[0] == 0


def test_ambiguous_auth_id_refused(databases):
    research, auth = databases; seed(research, auth)
    auth.execute("INSERT INTO auth_users VALUES('archive-A',1,'duplicate','secret')")
    with pytest.raises(ValueError, match='Ambiguous'): links(research, auth)


@pytest.mark.parametrize('state', [None, 2, 'unknown'])
def test_unknown_active_state_refused(databases, state):
    seed(*databases, active=state)
    with pytest.raises(ValueError, match='state'): links(*databases)


def test_empty_archive_does_not_prove_correspondence(databases):
    result = links(*databases)
    assert result['archived_owner_count'] == 0 and not result['correspondence_check_passed']


def test_unknown_research_owner_table_not_ignored(databases):
    research, auth = databases; seed(research, auth)
    research.execute('CREATE TABLE qr_new_archive(id TEXT PRIMARY KEY,owner TEXT)')
    with pytest.raises(ValueError, match='schema'): links(research, auth)


@pytest.mark.parametrize('owner', [None, '', ' '*4])
def test_invalid_archived_owner_refused(databases, owner):
    research, auth = databases; seed(research, auth, owner=owner)
    with pytest.raises(ValueError, match='owner'): links(research, auth)


def test_cold_snapshot_rejects_live_journal_and_symlink(tmp_path):
    path = tmp_path/'auth.sqlite3'; path.write_bytes(b'not opened by this preflight')
    assert _cold(path) == path
    wal = tmp_path/'auth.sqlite3-wal'; wal.write_bytes(b'')
    with pytest.raises(ValueError, match='journals'): _cold(path)
    wal.unlink()
    link = tmp_path/'link.sqlite3'; link.symlink_to(path)
    with pytest.raises(ValueError, match='cold'): _cold(link)


def test_loaded_checker_source_change_refused_before_snapshot_access(tmp_path, monkeypatch):
    changed = tmp_path/'changed.py'; changed.write_text('# changed bytes; never executed')
    monkeypatch.setattr(identity, '__file__', str(changed))
    with pytest.raises(ValueError, match='source changed'):
        identity.review(tmp_path/'missing', tmp_path/'missing-auth')


def test_oversized_padding_rejected_before_grouped_owner_materialization(databases):
    research, auth = databases; seed(research, auth, owner=' '*100000+'A')
    trace = []; research.set_trace_callback(trace.append)
    with pytest.raises(ValueError, match='oversized'): links(research, auth)
    assert not any('GROUP BY' in sql for sql in trace)
