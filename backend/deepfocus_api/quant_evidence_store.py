"""Owner-scoped immutable internal-research evidence, in the backed-up QR ledger.

Only an operator import may create a bundle. Public APIs expose metadata, never
arbitrary server paths, raw PDF bytes or an unverified claim of complete coverage.
"""
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import sqlite3
import uuid
from . import quant_research_store as jobs
from .quant_corporate_actions import CashDividend,validate_evidence
from .quant_research_engine import digest

MAX_DOCUMENT=10*1024*1024
MAX_BUNDLE=25*1024*1024
MAX_OWNER_BYTES=200*1024*1024


@contextmanager
def connect():
    with jobs.connect() as c:
        c.execute('CREATE TABLE IF NOT EXISTS qr_evidence_source(owner TEXT NOT NULL,sha TEXT NOT NULL,body BLOB NOT NULL,PRIMARY KEY(owner,sha))')
        c.execute('CREATE TABLE IF NOT EXISTS qr_evidence_bundle(id TEXT PRIMARY KEY,owner TEXT NOT NULL,fingerprint TEXT NOT NULL,created TEXT NOT NULL,manifest TEXT NOT NULL,UNIQUE(owner,fingerprint))')
        c.execute('CREATE INDEX IF NOT EXISTS qr_evidence_owner ON qr_evidence_bundle(owner,created)')
        for table in ('qr_evidence_source','qr_evidence_bundle'):
            for op in ('UPDATE','DELETE'):
                c.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_{op.lower()} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,'immutable research evidence'); END")
        c.commit()
        yield c


def put(owner,label,events,sources,*,completeness_attested=False):
    if not isinstance(owner,str) or not owner.strip():raise ValueError('须指定证据所有者')
    if not isinstance(label,str) or not 1<=len(label.strip())<=80:raise ValueError('证据名称无效')
    if completeness_attested is not False:raise ValueError('当前导入仅支持抽样资料，不接受未经验证的完整覆盖声明')
    if not 1<=len(events)<=1000:raise ValueError('事件数量须在1至1000之间')
    required={e.source_sha256 for e in events}
    if set(sources)!=required:raise ValueError('原文范围必须与事件指纹一致')
    if any(not isinstance(b,bytes) or not b.startswith(b'%PDF-') or len(b)>MAX_DOCUMENT for b in sources.values()):raise ValueError('原文须为不超过10MiB的PDF')
    if sum(map(len,sources.values()))>MAX_BUNDLE:raise ValueError('单证据包超过25MiB')
    validate_evidence(events,sources)
    manifest={'schema':'qr-evidence-v1','label':label.strip(),
              'events':[asdict(e) for e in sorted(events,key=lambda e:(e.event_id,e.revision))],
              'coverage_kind':'sample','coverage_verified':False,
              'fact_review':'operator_supplied_not_automatically_verified',
              'usage':'internal_research','promotion_allowed':False}
    fingerprint=digest(manifest)
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        old=c.execute('SELECT * FROM qr_evidence_bundle WHERE owner=? AND fingerprint=?',(owner,fingerprint)).fetchone()
        if old:return _summary(old)
        if c.execute('SELECT count(*) FROM qr_evidence_bundle WHERE owner=?',(owner,)).fetchone()[0]>=100:raise RuntimeError('每个用户最多保留100个证据包')
        stored={r['sha']:r['n'] for r in c.execute('SELECT sha,length(body) n FROM qr_evidence_source WHERE owner=?',(owner,))}
        if sum(stored.values())+sum(len(sources[h]) for h in required-set(stored))>MAX_OWNER_BYTES:raise RuntimeError('证据存储额度已满')
        for sha,body in sources.items():
            if sha in stored:
                retained=c.execute('SELECT body FROM qr_evidence_source WHERE owner=? AND sha=?',(owner,sha)).fetchone()['body']
                if retained!=body:raise ValueError('已有原文与指纹不一致，禁止覆盖')
            else:c.execute('INSERT INTO qr_evidence_source VALUES(?,?,?)',(owner,sha,sqlite3.Binary(body)))
        identity=uuid.uuid4().hex
        c.execute('INSERT INTO qr_evidence_bundle VALUES(?,?,?,?,?)',(identity,owner,fingerprint,jobs.now(),json.dumps(manifest,ensure_ascii=False,allow_nan=False)))
        # Read within this transaction, not via another connection before commit.
        row=c.execute('SELECT * FROM qr_evidence_bundle WHERE id=? AND owner=?',(identity,owner)).fetchone()
        return _summary(row)


def _summary(row):
    if row is None:return None
    manifest=json.loads(row['manifest'])
    if digest(manifest)!=row['fingerprint']:raise ValueError('证据包完整性核对失败')
    return {'id':row['id'],'fingerprint':row['fingerprint'],'created':row['created'],
            **{k:v for k,v in manifest.items() if k!='events'},'event_count':len(manifest['events']),
            'symbols':dict(Counter(e['symbol'] for e in manifest['events'])),
            'source_count':len({e['source_sha256'] for e in manifest['events']})}


def summary(owner,identity):
    with connect() as c:return _summary(c.execute('SELECT * FROM qr_evidence_bundle WHERE id=? AND owner=?',(identity,owner)).fetchone())


def listing(owner):
    with connect() as c:return [_summary(r) for r in c.execute('SELECT * FROM qr_evidence_bundle WHERE owner=? ORDER BY created DESC LIMIT 100',(owner,)).fetchall()]


def load(owner,identity):
    with connect() as c:
        row=c.execute('SELECT * FROM qr_evidence_bundle WHERE id=? AND owner=?',(identity,owner)).fetchone()
        if row is None:raise LookupError('证据包不存在')
        info=_summary(row);manifest=json.loads(row['manifest']);events=[CashDividend(**e) for e in manifest['events']]
        sources={}
        for sha in {e.source_sha256 for e in events}:
            raw=c.execute('SELECT body FROM qr_evidence_source WHERE owner=? AND sha=?',(owner,sha)).fetchone()
            if raw is None:raise ValueError('证据原文缺失')
            sources[sha]=raw['body']
        validate_evidence(events,sources)
        return info,events,sources
