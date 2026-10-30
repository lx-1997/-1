"""Owner-scoped immutable experiments and CAS-updated paper ledgers."""
import json
import uuid
from datetime import datetime, timezone
from contextlib import contextmanager
from . import db, quant_worker_slots as slots
from .quant_research_engine import digest


def now(): return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect():
    c = db.connect(db.data_path('.quant_research.sqlite3', 'DEEPFOCUS_QUANT_RESEARCH_DB_PATH'))
    c.row_factory = __import__('sqlite3').Row
    try:
        c.execute('CREATE TABLE IF NOT EXISTS qr_experiment(id TEXT PRIMARY KEY,owner TEXT NOT NULL,request_key TEXT NOT NULL,input_hash TEXT NOT NULL,status TEXT NOT NULL,progress INTEGER NOT NULL,stage TEXT NOT NULL,created TEXT NOT NULL,updated TEXT NOT NULL,payload TEXT NOT NULL,UNIQUE(owner,request_key))')
        c.execute('CREATE TABLE IF NOT EXISTS qr_paper(id TEXT PRIMARY KEY,owner TEXT NOT NULL,experiment TEXT NOT NULL,revision INTEGER NOT NULL,status TEXT NOT NULL,updated TEXT NOT NULL,payload TEXT NOT NULL,UNIQUE(owner,experiment))')
        slots.schema(c)
        c.execute('CREATE INDEX IF NOT EXISTS qr_owner ON qr_experiment(owner,created)')
        c.execute('CREATE INDEX IF NOT EXISTS qr_running ON qr_experiment(status,updated)')
        c.commit()
        yield c
        c.commit()
    except BaseException:
        c.rollback(); raise
    finally: c.close()


def public(row):
    if row is None: return None
    payload = json.loads(row['payload'])
    return {**payload, **{k: row[k] for k in row.keys() if k not in {'payload', 'owner', 'request_key', 'input_hash'}}}


def get(owner, identity, paper=False):
    table = 'qr_paper' if paper else 'qr_experiment'
    with connect() as c:
        return public(c.execute(f'SELECT * FROM {table} WHERE owner=? AND id=?', (owner, identity)).fetchone())


def list_records(owner, paper=False):
    table = 'qr_paper' if paper else 'qr_experiment'
    with connect() as c:
        columns = 'id,experiment,revision,status,updated' if paper else 'id,status,progress,stage,created,updated'
        rows = c.execute(f"SELECT {columns},json_extract(payload,'$.request') AS request FROM {table} WHERE owner=? ORDER BY updated DESC LIMIT 100", (owner,)).fetchall()
    return [{**dict(r), 'request':json.loads(r['request'] or '{}')} for r in rows]


def create(owner, key, request, kernel):
    fingerprint = digest(request)
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        old = c.execute('SELECT * FROM qr_experiment WHERE owner=? AND request_key=?', (owner, key)).fetchone()
        if old:
            if old['input_hash'] != fingerprint: raise ValueError('同一提交标识对应了不同参数')
            return public(old), False
        # Expiry closes the user-visible job, never a still-live worker slot.
        # Work is not silently retried; callbacks/drain release active slots.
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc)-timedelta(minutes=5)).isoformat()
        c.execute("UPDATE qr_experiment SET status='failed',stage='执行进程中断或超时，请重新提交',updated=? WHERE status='running' AND updated<?", (now(), cutoff))
        slots.retire_unclaimed(c)
        slots.reap_dead(c,now())
        own,total=slots.counts(c,owner)
        if own >= 2 or total >= 4: raise RuntimeError('研究任务已满，取消后的后台计算退出前仍占用名额')
        identity, stamp = uuid.uuid4().hex, now()
        payload = {'request': request, 'kernel': kernel, 'events': [{'at': stamp, 'stage': '已登记不可变研究参数'}]}
        c.execute('INSERT INTO qr_experiment VALUES(?,?,?,?,?,?,?,?,?,?)', (identity, owner, key, fingerprint, 'running', 1, '等待真实行情', stamp, stamp, json.dumps(payload, ensure_ascii=False)))
        slots.reserve(c,owner,identity)
        return get_in(c, owner, identity), True


def get_in(c, owner, identity):
    return public(c.execute('SELECT * FROM qr_experiment WHERE owner=? AND id=?', (owner, identity)).fetchone())


def update(owner, identity, *, status='running', progress=0, stage='', extra=None):
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT * FROM qr_experiment WHERE owner=? AND id=?', (owner, identity)).fetchone()
        if not row or row['status'] != 'running': return False
        p = json.loads(row['payload']); p.update(extra or {})
        p['events'] = (p.get('events', []) + [{'at': now(), 'stage': stage}])[-100:]
        c.execute('UPDATE qr_experiment SET status=?,progress=?,stage=?,updated=?,payload=? WHERE owner=? AND id=? AND status=?',
                  (status, progress, stage, now(), json.dumps(p, ensure_ascii=False, allow_nan=False), owner, identity, 'running'))
        if status != 'running': slots.retire_unclaimed(c)
        return True


def cancel(owner, identity):
    update(owner, identity, status='cancelled', stage='已取消，未完成结果不会发布；后台计算退出前仍占名额')
    return get(owner, identity)


def create_paper(owner, experiment, payload, *, guard=None):
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        if guard is not None: guard(c)
        old = c.execute('SELECT * FROM qr_paper WHERE owner=? AND experiment=?', (owner, experiment)).fetchone()
        if old: return public(old)
        if c.execute("SELECT count(*) FROM qr_paper WHERE owner=? AND status='active'", (owner,)).fetchone()[0] >= 5:
            raise RuntimeError('每个账户最多同时跟踪五个模拟组合')
        if c.execute("SELECT count(*) FROM qr_paper WHERE status='active'").fetchone()[0] >= 100:
            raise RuntimeError('当前模拟跟踪容量已满，请稍后重试')
        identity = uuid.uuid4().hex
        c.execute('INSERT INTO qr_paper VALUES(?,?,?,?,?,?,?)', (identity, owner, experiment, 0, 'active', now(), json.dumps(payload, ensure_ascii=False, allow_nan=False)))
        return public(c.execute('SELECT * FROM qr_paper WHERE id=?', (identity,)).fetchone())


def save_paper(owner, identity, revision, payload, *, status='active', guard=None):
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        if guard is not None: guard(c)
        result = c.execute('UPDATE qr_paper SET revision=revision+1,status=?,updated=?,payload=? WHERE owner=? AND id=? AND revision=?',
                           (status, now(), json.dumps(payload, ensure_ascii=False, allow_nan=False), owner, identity, revision))
        if result.rowcount != 1: raise RuntimeError('模拟账户已被另一请求更新，请刷新')
    return get(owner, identity, True)


def active_papers():
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT id,owner,payload FROM qr_paper WHERE status='active' ORDER BY updated LIMIT 100").fetchall()]

def expire_interrupted():
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc)-timedelta(minutes=5)).isoformat()
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute("UPDATE qr_experiment SET status='failed',stage='执行进程中断或超时，请重新提交',updated=? WHERE status='running' AND updated<?", (now(),cutoff))
        slots.retire_unclaimed(c)
        slots.reap_dead(c,now())


def claim_worker(owner,identity,token):
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        return slots.claim(c,owner,identity,token)


def finish_worker(owner,identity,token):
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        return slots.finish(c,owner,identity,token,now())
