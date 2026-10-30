"""Immutable owner-owned calendar notices, backed up with the research ledger."""
from contextlib import contextmanager
import json
import sqlite3
import uuid

from . import quant_research_store as jobs
from .quant_exchange_schedule import validate_manifest
from .quant_research_engine import digest

MAX_SOURCE = 2*1024*1024
MAX_BUNDLE = 8*1024*1024
MAX_OWNER = 32*1024*1024


@contextmanager
def connect():
    with jobs.connect() as c:
        c.execute('CREATE TABLE IF NOT EXISTS qr_calendar_source(owner TEXT NOT NULL,sha TEXT NOT NULL,body BLOB NOT NULL,PRIMARY KEY(owner,sha))')
        c.execute('CREATE TABLE IF NOT EXISTS qr_calendar_bundle(id TEXT PRIMARY KEY,owner TEXT NOT NULL,fingerprint TEXT NOT NULL,created TEXT NOT NULL,payload TEXT NOT NULL,UNIQUE(owner,fingerprint))')
        c.execute('CREATE INDEX IF NOT EXISTS qr_calendar_owner ON qr_calendar_bundle(owner,created)')
        for table in ('qr_calendar_source', 'qr_calendar_bundle'):
            for op in ('UPDATE', 'DELETE'):
                c.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_{op.lower()} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,'immutable calendar evidence'); END")
        c.commit()
        yield c


def _summary(row):
    if row is None:
        return None
    payload = json.loads(row['payload'])
    if digest(payload) != row['fingerprint']:
        raise ValueError('日历证据包完整性核对失败')
    calendar = payload['calendar']
    return {'id': row['id'], 'fingerprint': row['fingerprint'], 'created': row['created'],
            'label': payload['label'], 'years': calendar['years'],
            'source_count': len({r['source_sha256'] for r in calendar['records']}),
            'scope': 'annual_planned_sessions_only', 'usage': 'internal_research',
            'actual_exchange_sessions_certified': False, 'security_status_verified': False,
            'promotion_allowed': False}


def put(owner, label, manifest, sources):
    if not isinstance(owner, str) or not owner.strip():
        raise ValueError('须指定日历证据所有者')
    if not isinstance(label, str) or not 1 <= len(label.strip()) <= 80:
        raise ValueError('日历证据名称无效')
    required = {r['source_sha256'] for r in manifest['records']}
    if (set(sources) != required or any(not isinstance(b, bytes) or len(b)>MAX_SOURCE for b in sources.values())
            or sum(map(len, sources.values())) > MAX_BUNDLE):
        raise ValueError('日历原文范围或大小不符合限额')
    validate_manifest(manifest, sources, as_of=jobs.now())
    payload = {'label': label.strip(), 'calendar': manifest}
    fingerprint = digest(payload)
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        old = c.execute('SELECT * FROM qr_calendar_bundle WHERE owner=? AND fingerprint=?', (owner, fingerprint)).fetchone()
        if old:
            return _summary(old)
        if c.execute('SELECT count(*) FROM qr_calendar_bundle WHERE owner=?', (owner,)).fetchone()[0] >= 100:
            raise RuntimeError('每个用户最多保留100个日历证据包')
        stored = {r['sha']: r['n'] for r in c.execute('SELECT sha,length(body) n FROM qr_calendar_source WHERE owner=?', (owner,))}
        if sum(stored.values())+sum(len(sources[h]) for h in required-set(stored)) > MAX_OWNER:
            raise RuntimeError('日历证据存储额度已满')
        for sha, raw in sources.items():
            if sha in stored:
                retained = c.execute('SELECT body FROM qr_calendar_source WHERE owner=? AND sha=?', (owner, sha)).fetchone()['body']
                if retained != raw:
                    raise ValueError('已有日历原文与指纹不一致')
            else:
                c.execute('INSERT INTO qr_calendar_source VALUES(?,?,?)', (owner, sha, sqlite3.Binary(raw)))
        identity = uuid.uuid4().hex
        c.execute('INSERT INTO qr_calendar_bundle VALUES(?,?,?,?,?)',
                  (identity, owner, fingerprint, jobs.now(), json.dumps(payload, ensure_ascii=False, allow_nan=False)))
        return _summary(c.execute('SELECT * FROM qr_calendar_bundle WHERE owner=? AND id=?', (owner, identity)).fetchone())


def listing(owner):
    with connect() as c:
        return [_summary(r) for r in c.execute('SELECT * FROM qr_calendar_bundle WHERE owner=? ORDER BY created DESC LIMIT 100', (owner,))]


def summary(owner, identity):
    with connect() as c:
        return _summary(c.execute('SELECT * FROM qr_calendar_bundle WHERE owner=? AND id=?', (owner, identity)).fetchone())


def load(owner, identity):
    with connect() as c:
        row = c.execute('SELECT * FROM qr_calendar_bundle WHERE owner=? AND id=?', (owner, identity)).fetchone()
        if row is None:
            raise LookupError('日历证据包不存在')
        info, manifest = _summary(row), json.loads(row['payload'])['calendar']
        sources = {}
        for sha in {r['source_sha256'] for r in manifest['records']}:
            raw = c.execute('SELECT body FROM qr_calendar_source WHERE owner=? AND sha=?', (owner, sha)).fetchone()
            if raw is None:
                raise ValueError('日历原文缺失')
            sources[sha] = raw['body']
    validate_manifest(manifest, sources, as_of=jobs.now())
    return info, manifest, sources
