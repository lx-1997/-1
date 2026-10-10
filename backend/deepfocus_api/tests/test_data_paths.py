from pathlib import Path

from deepfocus_api import db, storage


def test_persistent_paths_respect_volume_and_explicit_override(monkeypatch, tmp_path):
    monkeypatch.setenv('DEEPFOCUS_DATA_DIR', str(tmp_path / 'volume'))
    monkeypatch.delenv('STORE_OVERRIDE', raising=False)
    assert db.data_path('.test.sqlite3', 'STORE_OVERRIDE') == tmp_path / 'volume' / '.test.sqlite3'
    monkeypatch.setenv('STORE_OVERRIDE', str(tmp_path / 'legacy.sqlite3'))
    assert db.data_path('.test.sqlite3', 'STORE_OVERRIDE') == tmp_path / 'legacy.sqlite3'
    monkeypatch.setenv('STORE_OVERRIDE', '')
    assert db.data_path('.test.sqlite3', 'STORE_OVERRIDE') == tmp_path / 'volume' / '.test.sqlite3'
    monkeypatch.delenv('DEEPFOCUS_DATA_DIR')
    assert db.data_path('.test.sqlite3') == Path(db.__file__).resolve().parents[1] / '.test.sqlite3'


def test_account_database_uses_same_data_volume(monkeypatch, tmp_path):
    monkeypatch.delenv('DEEPFOCUS_DATABASE_URL', raising=False)
    monkeypatch.delenv('DEEPFOCUS_CORE_DB_PATH', raising=False)
    monkeypatch.setenv('DEEPFOCUS_DATA_DIR', str(tmp_path))
    assert storage.database_url() == f'sqlite:///{tmp_path}/.deepfocus_core.sqlite3'


def test_connect_creates_store_directory_without_touching_source(monkeypatch, tmp_path):
    monkeypatch.setenv('DEEPFOCUS_DATA_DIR', str(tmp_path / 'fresh'))
    path = db.data_path('proof.sqlite3')
    conn = db.connect(path)
    conn.execute('create table proof(value text)')
    conn.execute('insert into proof values (?)', ('persisted',))
    conn.commit()
    conn.close()
    conn = db.connect(path)
    assert conn.execute('select value from proof').fetchone()[0] == 'persisted'
    conn.close()
