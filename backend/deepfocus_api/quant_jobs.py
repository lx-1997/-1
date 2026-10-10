from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional
from uuid import uuid4

from . import db
from .quant_lab import run_quant_lab
from .schemas import QuantLabRequest

JobStatus = Literal['pending', 'running', 'completed', 'failed', 'cancelled']
_JOB_TTL = timedelta(hours=1)
_MAX_JOBS = 32
_TERMINAL = {'completed', 'failed', 'cancelled'}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _db_path():
    return db.data_path('.quant_jobs.sqlite3', 'DEEPFOCUS_QUANT_JOBS_DB_PATH')


def _connect():
    conn = db.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute('CREATE TABLE IF NOT EXISTS quant_jobs (job_id TEXT PRIMARY KEY, owner TEXT NOT NULL, status TEXT NOT NULL, updated_at TEXT NOT NULL, payload TEXT NOT NULL)')
    return conn


@dataclass
class QuantJob:
    job_id: str
    owner: str
    status: JobStatus = 'pending'
    progress: int = 0
    stage: str = '任务已创建'
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)
    completed_at: Optional[datetime] = None
    result: Optional[dict[str, Any]] = None
    error: str = ''
    task: Optional[asyncio.Task[None]] = field(default=None, repr=False)


_jobs: dict[str, QuantJob] = {}


def _public(job: QuantJob) -> dict[str, Any]:
    return {'job_id': job.job_id, 'status': job.status, 'progress': job.progress,
            'stage': job.stage, 'created_at': _iso(job.created_at), 'updated_at': _iso(job.updated_at),
            'completed_at': _iso(job.completed_at), 'result': job.result, 'error': job.error}


def _save(job: QuantJob) -> None:
    with _connect() as conn:
        conn.execute('INSERT INTO quant_jobs VALUES (?, ?, ?, ?, ?) ON CONFLICT(job_id) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at, payload=excluded.payload',
                     (job.job_id, job.owner, job.status, _iso(job.updated_at), json.dumps(_public(job), ensure_ascii=False)))


def _load(row) -> QuantJob:
    data = json.loads(row['payload'])
    for key in ('created_at', 'updated_at', 'completed_at'):
        if data.get(key):
            data[key] = datetime.fromisoformat(data[key])
    return QuantJob(owner=row['owner'], **data)


def init_quant_jobs() -> None:
    """Single API worker startup: retain results; fail jobs interrupted by restart.

    Running costly research is never automatically replayed. The owner sees an
    explicit interruption and can choose to submit a fresh job.
    """
    with _connect() as conn:
        rows = conn.execute('SELECT * FROM quant_jobs').fetchall()
    for row in rows:
        live = _jobs.get(row['job_id'])
        if live and live.task and not live.task.done():
            continue
        job = _load(row)
        if job.status not in _TERMINAL:
            job.status = 'failed'
            job.stage = '服务重启，任务已中断，请重新提交'
            job.error = 'worker_interrupted'
            job.completed_at = job.updated_at = _now()
            _save(job)
    _prune()


def _prune() -> None:
    cutoff = (_now() - _JOB_TTL).isoformat()
    with _connect() as conn:
        conn.execute("DELETE FROM quant_jobs WHERE status IN ('completed','failed','cancelled') AND updated_at < ?", (cutoff,))
        count = conn.execute('SELECT COUNT(*) FROM quant_jobs').fetchone()[0]
        if count >= _MAX_JOBS:
            conn.execute("DELETE FROM quant_jobs WHERE job_id IN (SELECT job_id FROM quant_jobs WHERE status IN ('completed','failed','cancelled') ORDER BY updated_at LIMIT ?)", (count - _MAX_JOBS + 1,))
        retained = {row[0] for row in conn.execute('SELECT job_id FROM quant_jobs')}
    for job_id in list(_jobs):
        if job_id not in retained and (_jobs[job_id].task is None or _jobs[job_id].task.done()):
            _jobs.pop(job_id, None)


async def _execute(job: QuantJob, request: QuantLabRequest) -> None:
    if job.status == 'cancelled':
        return
    job.status = 'running'
    job.stage = '正在启动量化研究'
    job.updated_at = _now()
    _save(job)

    def update_progress(progress: int, stage: str) -> None:
        if job.status == 'cancelled':
            return
        job.progress = max(job.progress, min(100, int(progress)))
        job.stage = stage
        job.updated_at = _now()
        _save(job)

    try:
        result = await run_quant_lab(request, progress_callback=update_progress)
        if job.status != 'cancelled':
            job.result = result
            job.status = 'completed'
            job.progress = 100
            job.stage = '量化研究已完成'
    except asyncio.CancelledError:
        job.status = 'cancelled'
        job.stage = '任务已取消'
    except Exception as exc:
        if job.status != 'cancelled':
            job.status = 'failed'
            job.stage = '量化研究失败'
            job.error = str(exc) or exc.__class__.__name__
    finally:
        job.completed_at = job.updated_at = _now()
        _save(job)


async def create_quant_job(owner: str, request: QuantLabRequest) -> dict[str, Any]:
    _prune()
    with _connect() as conn:
        if conn.execute('SELECT COUNT(*) FROM quant_jobs').fetchone()[0] >= _MAX_JOBS:
            raise RuntimeError('量化任务队列已满，请稍后重试')
    job = QuantJob(job_id=uuid4().hex, owner=owner)
    _save(job)
    _jobs[job.job_id] = job
    job.task = asyncio.create_task(_execute(job, request.model_copy(deep=True)))
    return _public(job)


def get_quant_job(owner: str, job_id: str) -> Optional[dict[str, Any]]:
    _prune()
    with _connect() as conn:
        row = conn.execute('SELECT * FROM quant_jobs WHERE job_id=? AND owner=?', (job_id, owner)).fetchone()
    return _public(_load(row)) if row else None


async def cancel_quant_job(owner: str, job_id: str) -> Optional[dict[str, Any]]:
    public = get_quant_job(owner, job_id)
    if public is None:
        return None
    job = _jobs.get(job_id)
    if not job:
        with _connect() as conn:
            job = _load(conn.execute('SELECT * FROM quant_jobs WHERE job_id=? AND owner=?', (job_id, owner)).fetchone())
    if job.status not in _TERMINAL:
        # Set and persist first: a task cancelled before its first await never
        # enters _execute's try/finally, and otherwise stayed pending forever.
        job.status = 'cancelled'
        job.stage = '任务已取消'
        job.completed_at = job.updated_at = _now()
        _save(job)
        if job.task and not job.task.done():
            job.task.cancel()
            await asyncio.gather(job.task, return_exceptions=True)
    return get_quant_job(owner, job_id)


async def shutdown_quant_jobs() -> None:
    for job in list(_jobs.values()):
        if job.status not in _TERMINAL:
            await cancel_quant_job(job.owner, job.job_id)


async def reset_quant_jobs_for_tests() -> None:
    await shutdown_quant_jobs()
    _jobs.clear()
    with _connect() as conn:
        conn.execute('DELETE FROM quant_jobs')
