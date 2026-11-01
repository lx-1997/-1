"""Operator-only financial evidence receipts in the owner-scoped QR ledger.

An imported historical report is not a past system observation. Stored research
uses a conservative next-day visibility boundary, not caller-supplied clocks.
This is application immutability, not an externally signed timestamp service.
"""
import copy
from contextlib import contextmanager
from datetime import datetime, time, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import time as monotonic_time
import uuid

from . import quant_research_store as jobs, quant_financial_vintages as vintages
from .quant_corporate_actions import BJ, timestamp
from .quant_research_engine import digest

VERSION = 'qr-financial-receipt-v1'
VALIDATOR_VERSION = 'qr-financial-source-validator-v6'
# Exact reviewed source identity of the original immutable v1 archive (af7e1a8).
# This is a data/source whitelist, never permission to execute archived code.
KNOWN_V1_SOURCE_PINS = {
    'quant_financial_vintages.py':'7977ab0af0dde49cd0688d838e3d7090e98357a1e7295d439dffb7f2b0ee9278',
    'quant_corporate_actions.py':'89fadf0d5369ef7d7c8a0aeec368124cef8eaca5dcb5371298c4204206ee3996',
    'quant_research_engine.py':'b3852688c331aebfe06515f54fc2b7a35191e90a587697f4424e771eea9893d5',
    'quant_financial_store.py':'da99b1317c8aa7c81fabbe3ebfa3da4318a2857082be1e353575251c391471cd',
}

# Exact ddc3fc3 source identity: exact numeric bounds, before metric page metadata.
KNOWN_V2_SOURCE_PINS = {
    'quant_financial_vintages.py':'4d60703b7c55a429f19c7f30bbfee2c379bdfc00587f21f03b2098d1af1e2bf3',
    'quant_corporate_actions.py':'89fadf0d5369ef7d7c8a0aeec368124cef8eaca5dcb5371298c4204206ee3996',
    'quant_research_engine.py':'b3852688c331aebfe06515f54fc2b7a35191e90a587697f4424e771eea9893d5',
    'quant_financial_store.py':'bef824969eb52e969f9a8ea00e1126177a48a3807dccf3f4dc11edbf0e78edd2',
}

# Exact cebde90 source family, with per-metric pages and numeric-only org IDs.
KNOWN_V3_SOURCE_PINS = {'quant_financial_vintages.py': '3a04a0a6a71489ba81f5836620e136a10044c32d8d0954ab0184b0cbca46e6ce', 'quant_corporate_actions.py': '89fadf0d5369ef7d7c8a0aeec368124cef8eaca5dcb5371298c4204206ee3996', 'quant_research_engine.py': 'b3852688c331aebfe06515f54fc2b7a35191e90a587697f4424e771eea9893d5', 'quant_financial_store.py': 'c827e38760fd7815e60f7d234f2fc6416653bcc46709ee5ab05fc40b267ae18b'}

# Exact pre-path-upgrade source: ASCII org IDs, uppercase-only PDF URLs.
KNOWN_V4_SOURCE_PINS = {'quant_financial_vintages.py': '0eb2193a9a4531264b8b0bdfa9194ac38a5940e40078a0d0da2cd6d4dd994a9b', 'quant_corporate_actions.py': '89fadf0d5369ef7d7c8a0aeec368124cef8eaca5dcb5371298c4204206ee3996', 'quant_research_engine.py': 'b3852688c331aebfe06515f54fc2b7a35191e90a587697f4424e771eea9893d5', 'quant_financial_store.py': 'b28cfb10f735fb8a29cb7624c0ee04ac886597c538eb7ff6299ebf8d2baa9998'}

# Exact pre-capacity-upgrade source; its URL/identity domain already permits both PDF cases.
KNOWN_V5_SOURCE_PINS = {'quant_financial_vintages.py': '16481fb39bd8ec6a49b7109b3aa85f50e189db8e93a5969aceaab1091ab43c7c', 'quant_corporate_actions.py': '89fadf0d5369ef7d7c8a0aeec368124cef8eaca5dcb5371298c4204206ee3996', 'quant_research_engine.py': 'b3852688c331aebfe06515f54fc2b7a35191e90a587697f4424e771eea9893d5', 'quant_financial_store.py': 'd387866948aa5e82180aec2f1ffd409d789d63aaebe53400f53d48cbb35cde8f'}

VISIBILITY = 'next_beijing_calendar_day_after_transaction_recording'
# Aggregate retained source budget for the declared 64-company internal cohort.
# Per-import, PDF, identity, clock and strategy eligibility limits are unchanged.
MAX_OWNER_BYTES = 512 * 1024 * 1024
MAX_BUNDLES = 100


def _pins():
    return {**vintages.source_pins(), __name__.split('.')[-1]+'.py':
            hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


LOADED_PINS = _pins()


def require_current_source():
    vintages.require_current_source()
    if _pins() != LOADED_PINS:
        raise ValueError('Loaded financial store source changed')


def _now():
    return datetime.now(timezone.utc)


@contextmanager
def connect():
    with jobs.connect() as c:
        c.execute('CREATE TABLE IF NOT EXISTS qr_financial_source(owner TEXT NOT NULL,sha TEXT NOT NULL,body BLOB NOT NULL,PRIMARY KEY(owner,sha))')
        c.execute('CREATE TABLE IF NOT EXISTS qr_financial_bundle(id TEXT PRIMARY KEY,owner TEXT NOT NULL,input_hash TEXT NOT NULL,fingerprint TEXT NOT NULL,created TEXT NOT NULL,payload TEXT NOT NULL,UNIQUE(owner,input_hash))')
        for table in ('qr_financial_source', 'qr_financial_bundle'):
            for op in ('UPDATE', 'DELETE'):
                c.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_{op.lower()} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,'immutable financial receipt'); END")
        c.commit()
        yield c


def _facts(record):
    return {k:v for k,v in record.items() if k not in {'observed_at','recorded_at','index_sha256'}
            and not (k=='metric_source_pages' and v is None)}


def source_profile(value):
    archived=value.get('source_pins')
    if archived==LOADED_PINS:return 'current'
    if archived==KNOWN_V1_SOURCE_PINS:return 'known_v1_revalidated'
    if archived==KNOWN_V2_SOURCE_PINS:return 'known_v2_revalidated'
    if archived==KNOWN_V3_SOURCE_PINS:return 'known_v3_revalidated'
    if archived==KNOWN_V4_SOURCE_PINS:return 'known_v4_revalidated'
    if archived==KNOWN_V5_SOURCE_PINS:return 'known_v5_revalidated'
    raise ValueError('Financial receipt source identity is unknown')


def verify_row(c, row):
    """Read and validate a receipt and owner-bound sources; no schema/write calls."""
    require_current_source()
    value = json.loads(row['payload'])
    if (digest(value) != row['fingerprint'] or value.get('schema') != VERSION
            or value.get('visibility_policy') != VISIBILITY):
        raise ValueError('Financial receipt identity changed')
    profile=source_profile(value)
    incoming, effective = value['input'], value['effective_manifest']
    if digest(incoming) != row['input_hash'] or incoming['label'] != value['label']:
        raise ValueError('Financial input fingerprint changed')
    if profile in {'known_v1_revalidated','known_v2_revalidated','known_v3_revalidated'}:
        # Revalidation must not broaden the identity domain accepted by these
        # exact legacy source families. Recognition is not a schema migration.
        import re
        if any(not re.fullmatch(r'cninfo:[0-9]{5,20}',r['issuer_id'])
               for manifest in (incoming['manifest'],effective) for r in manifest['vintages']):
            raise ValueError('Legacy financial source requires numeric issuer identity')
    if profile not in {'current','known_v5_revalidated'} and any(not record['source_url'].endswith('.PDF')
                                    for manifest in (incoming['manifest'],effective) for record in manifest['vintages']):
        raise ValueError('Legacy financial source requires uppercase PDF URL')
    created = timestamp(row['created'])
    boundary = datetime.combine(created.astimezone(BJ).date()+timedelta(days=1),time(),BJ)
    if value['recorded_at'] != row['created'] or timestamp(value['stored_visible_at']) != boundary:
        raise ValueError('Financial receipt clock changed')
    expected = copy.deepcopy(incoming['manifest'])
    for record in expected['vintages']:
        record['recorded_at'] = row['created']
    if effective != expected:
        raise ValueError('Effective financial manifest changed')
    sources = {}
    for sha in effective['source_files']:
        found = c.execute('SELECT body FROM qr_financial_source WHERE owner=? AND sha=?', (row['owner'],sha)).fetchone()
        if found is None:
            raise ValueError('Owner-bound financial evidence missing')
        sources[sha] = found['body']
    # Original declared clocks remain auditable, but do not establish storage time.
    # Revalidate every recognized source version with the current exact bounds;
    # recognizing old provenance never exempts a stored value from validation.
    vintages.validate_manifest(incoming['manifest'],sources,now=row['created'])
    vintages.validate_manifest(effective,sources)
    return value, sources


def _summary(row, value):
    return {'id':row['id'],'fingerprint':row['fingerprint'],'input_digest':row['input_hash'],
            'recorded_at':row['created'],'stored_visible_at':value['stored_visible_at'],
            'label':value['label'],'vintage_count':len(value['effective_manifest']['vintages']),
            'source_count':len(value['effective_manifest']['source_files']),
            'visibility_policy':VISIBILITY,'coverage_verified':False,
            'externally_attested_time':False,'promotion_allowed':False,
            'validator_version':VALIDATOR_VERSION,'archive_source_status':source_profile(value),
            'archive_source_current':value['source_pins']==LOADED_PINS,
            'archive_source_digest':digest(value['source_pins']),
            'validated_source_digest':digest(LOADED_PINS),'archive_rewritten':False}


def put(owner, label, manifest, sources):
    require_current_source()
    if not isinstance(owner,str) or not 1 <= len(owner.strip()) <= 200:
        raise ValueError('Explicit bounded owner required')
    if not isinstance(label,str) or not 1 <= len(label.strip()) <= 80:
        raise ValueError('Explicit bounded label required')
    # Freeze caller-owned objects before validation or waiting for a write lock.
    incoming = json.loads(json.dumps({'label':label.strip(),'manifest':manifest},allow_nan=False))
    if len(json.dumps(incoming).encode()) > 2*1024*1024:
        raise ValueError('Financial metadata exceeds 2MiB')
    sources = dict(sources)
    vintages.validate_manifest(incoming['manifest'],sources)
    input_hash = digest(incoming)
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        old = c.execute('SELECT * FROM qr_financial_bundle WHERE owner=? AND input_hash=?',(owner,input_hash)).fetchone()
        if old:
            value, retained = verify_row(c,old)
            if retained != sources:
                raise ValueError('Retained financial bytes differ')
            return _summary(old,value)
        started = monotonic_time.monotonic()
        recorded = _now()
        stamp = recorded.isoformat()
        vintages.validate_manifest(incoming['manifest'],sources,now=stamp)
        rows = c.execute('SELECT * FROM qr_financial_bundle WHERE owner=?',(owner,)).fetchall()
        if len(rows) >= MAX_BUNDLES:
            raise RuntimeError('Financial bundle quota exhausted')
        known_revisions, known_documents = {}, {}
        key = ('issuer_id','symbol','period_start','period_end','scope','basis','currency','revision')
        for row in rows:
            value,_ = verify_row(c,row)
            if recorded < timestamp(row['created']):
                raise ValueError('Financial recording clock moved backwards')
            for previous in value['input']['manifest']['vintages']:
                known_revisions[tuple(previous[k] for k in key)] = _facts(previous)
                known_documents[previous['document_id']] = _facts(previous)
        for new in incoming['manifest']['vintages']:
            for previous in (known_revisions.get(tuple(new[k] for k in key)),known_documents.get(new['document_id'])):
                if previous is not None and previous != _facts(new):
                    raise ValueError('Conflicting retained financial fact; explicit extraction revisions are not supported')
        stored = {r['sha']:r['n'] for r in c.execute('SELECT sha,length(body) n FROM qr_financial_source WHERE owner=?',(owner,))}
        if sum(stored.values())+sum(len(b) for h,b in sources.items() if h not in stored) > MAX_OWNER_BYTES:
            raise RuntimeError('Financial source quota exhausted')
        for sha,body in sources.items():
            if sha in stored:
                if c.execute('SELECT body FROM qr_financial_source WHERE owner=? AND sha=?',(owner,sha)).fetchone()['body'] != body:
                    raise ValueError('Retained financial source corrupt')
            else:
                c.execute('INSERT INTO qr_financial_source VALUES(?,?,?)',(owner,sha,sqlite3.Binary(body)))
        effective = copy.deepcopy(incoming['manifest'])
        for record in effective['vintages']:
            record['recorded_at'] = stamp
        boundary = datetime.combine(recorded.astimezone(BJ).date()+timedelta(days=1),time(),BJ)
        value = {'schema':VERSION,'label':incoming['label'],'input':incoming,'effective_manifest':effective,
                 'recorded_at':stamp,'stored_visible_at':boundary.isoformat(),
                 'visibility_policy':VISIBILITY,'source_pins':LOADED_PINS}
        identity = uuid.uuid4().hex
        c.execute('INSERT INTO qr_financial_bundle VALUES(?,?,?,?,?,?)',
                  (identity,owner,input_hash,digest(value),stamp,json.dumps(value,ensure_ascii=False,allow_nan=False)))
        row = c.execute('SELECT * FROM qr_financial_bundle WHERE owner=? AND id=?',(owner,identity)).fetchone()
        verify_row(c,row)
        require_current_source()
        current = _now()
        if current < recorded or current >= boundary or monotonic_time.monotonic()-started > 60:
            raise ValueError('Financial ingestion clock or transaction budget exceeded')
        # Visibility remains next day; no claim of a subsecond SQLite commit clock.
        c.commit()
        return _summary(row,value)


def review(owner, identity, *, as_of, mode, **kwargs):
    require_current_source()
    path = jobs.db.data_path('.quant_research.sqlite3','DEEPFOCUS_QUANT_RESEARCH_DB_PATH').resolve()
    c = sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2)
    c.row_factory = sqlite3.Row
    try:
        c.execute('PRAGMA query_only=ON')
        c.execute('BEGIN')
        row = c.execute('SELECT * FROM qr_financial_bundle WHERE owner=? AND id=?',(owner,identity)).fetchone()
        if row is None:
            raise LookupError('Financial receipt not found')
        value,sources = verify_row(c,row)
        result = vintages.select_panel(value['effective_manifest'],sources,as_of=as_of,mode=mode,**kwargs)
        if mode == 'as_recorded' and timestamp(as_of) < timestamp(value['stored_visible_at']):
            result.update(status='unavailable',panel=None,metrics=None,missing_metrics=list(kwargs['required_metrics']))
        result.update(receipt=_summary(row,value),stored_visibility_policy=VISIBILITY,
                      declared_operator_clocks_are_storage_proof=False)
        require_current_source()
        return result
    finally:
        c.close()
