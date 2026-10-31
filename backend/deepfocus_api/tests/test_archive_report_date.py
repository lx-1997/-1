import json
import sqlite3
import pytest
from deepfocus_api import main, research_archive
from deepfocus_api.bounded_cache import ByteBoundedTTLCache


@pytest.fixture
def archive(tmp_path, monkeypatch):
    path = tmp_path/'archive.sqlite3'
    c = sqlite3.connect(path)
    try:
        c.execute('CREATE TABLE research_prewarm_seen(file_id TEXT PRIMARY KEY,payload TEXT)')
        c.execute('CREATE TABLE research_archive(payload TEXT)')
        c.execute('INSERT INTO research_prewarm_seen VALUES(?,?)', ('report', json.dumps({'date':'2026-10-01'})))
        c.execute('INSERT INTO research_archive VALUES(?)', (json.dumps({'file_id':'report','date':'2026-09-01'}),))
        c.commit()
    finally: c.close()
    monkeypatch.setattr(research_archive, 'DB_PATH', path)
    original_connect = main._archive_date_connection
    clock = [0.0]; opened = []
    def connect():
        c = original_connect(); opened.append(c); return c
    monkeypatch.setattr(main, '_archive_date_connection', connect)
    monkeypatch.setattr(main, '_ARCHIVE_DATE_CACHE', ByteBoundedTTLCache(
        max_entries=2048, max_bytes=512*1024, max_item_bytes=4096,
        ttl_seconds=600, clock=lambda:clock[0]))
    return path, clock, opened, connect


def test_priority_cached_hit_and_ttl_refresh_close_connections(archive):
    path, clock, opened, _ = archive
    assert main._archive_report_date('report') == '2026-10-01'
    assert main._archive_report_date(' report ') == '2026-10-01'
    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError): opened[0].execute('SELECT 1')
    c = sqlite3.connect(path)
    try:
        c.execute('UPDATE research_prewarm_seen SET payload=?', (json.dumps({'date':'2026-10-02'}),)); c.commit()
    finally: c.close()
    clock[0] = 599.9
    assert main._archive_report_date('report') == '2026-10-01'
    clock[0] = 600
    assert main._archive_report_date('report') == '2026-10-02'
    assert len(opened) == 2


def test_unknown_date_not_cached_and_archive_fallback_available(archive):
    path, _, opened, _ = archive
    assert main._archive_report_date('later') == ''
    c = sqlite3.connect(path)
    try:
        c.execute('INSERT INTO research_archive VALUES(?)', (json.dumps({'file_id':'later','date':'2026-10-03'}),)); c.commit()
    finally: c.close()
    assert main._archive_report_date('later') == '2026-10-03'
    assert len(opened) == 2


def test_busy_lookup_not_cached_or_allowed_free(archive, monkeypatch):
    _, _, _, connect = archive
    def busy(): raise sqlite3.OperationalError('database locked')
    monkeypatch.setattr(main, '_archive_date_connection', busy)
    date = main._archive_report_date('report')
    assert date == '' and not main._legacy_free_read(date, True)
    monkeypatch.setattr(main, '_archive_date_connection', connect)
    assert main._archive_report_date('report') == '2026-10-01'


def test_empty_id_does_not_open_database(archive):
    assert main._archive_report_date('  ') == ''
    assert archive[2] == []


def test_missing_archive_is_not_created(archive, tmp_path, monkeypatch):
    path = tmp_path/'missing.sqlite3'
    monkeypatch.setattr(research_archive, 'DB_PATH', path)
    assert main._archive_report_date('report') == ''
    assert not path.exists()


def test_archive_date_connection_is_read_only(archive):
    c = main._archive_date_connection()
    try:
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            c.execute('CREATE TABLE unintended_write(id INTEGER)')
    finally: c.close()
