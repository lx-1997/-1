from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional
from uuid import uuid4

from .quant_lab import run_quant_lab
from .schemas import QuantLabRequest


JobStatus = Literal["pending", "running", "completed", "failed", "cancelled"]
_JOB_TTL = timedelta(hours=1)
_MAX_JOBS = 32


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


@dataclass
class QuantJob:
    job_id: str
    owner: str
    status: JobStatus = "pending"
    progress: int = 0
    stage: str = "任务已创建"
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)
    completed_at: Optional[datetime] = None
    result: Optional[dict[str, Any]] = None
    error: str = ""
    task: Optional[asyncio.Task[None]] = field(default=None, repr=False)


_jobs: dict[str, QuantJob] = {}


def _prune() -> None:
    cutoff = _now() - _JOB_TTL
    expired = [
        job_id
        for job_id, job in _jobs.items()
        if job.status in {"completed", "failed", "cancelled"} and job.updated_at < cutoff
    ]
    for job_id in expired:
        _jobs.pop(job_id, None)

    overflow = len(_jobs) - _MAX_JOBS + 1
    if overflow <= 0:
        return
    removable = sorted(
        (job for job in _jobs.values() if job.status in {"completed", "failed", "cancelled"}),
        key=lambda job: job.updated_at,
    )
    for job in removable[:overflow]:
        _jobs.pop(job.job_id, None)


def _public(job: QuantJob) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "status": job.status,
        "progress": job.progress,
        "stage": job.stage,
        "created_at": _iso(job.created_at),
        "updated_at": _iso(job.updated_at),
        "completed_at": _iso(job.completed_at),
        "result": job.result,
        "error": job.error,
    }


async def _execute(job: QuantJob, request: QuantLabRequest) -> None:
    job.status = "running"
    job.stage = "正在启动量化研究"
    job.updated_at = _now()

    def update_progress(progress: int, stage: str) -> None:
        job.progress = max(job.progress, min(100, int(progress)))
        job.stage = stage
        job.updated_at = _now()

    try:
        job.result = await run_quant_lab(request, progress_callback=update_progress)
        job.status = "completed"
        job.progress = 100
        job.stage = "量化研究已完成"
    except asyncio.CancelledError:
        job.status = "cancelled"
        job.stage = "任务已取消"
    except Exception as exc:  # 异步任务通过轮询返回结构化错误
        job.status = "failed"
        job.stage = "量化研究失败"
        job.error = str(exc) or exc.__class__.__name__
    finally:
        job.completed_at = _now()
        job.updated_at = job.completed_at


async def create_quant_job(owner: str, request: QuantLabRequest) -> dict[str, Any]:
    _prune()
    if len(_jobs) >= _MAX_JOBS:
        raise RuntimeError("量化任务队列已满，请稍后重试")
    job = QuantJob(job_id=uuid4().hex, owner=owner)
    _jobs[job.job_id] = job
    job.task = asyncio.create_task(_execute(job, request.model_copy(deep=True)))
    return _public(job)


def get_quant_job(owner: str, job_id: str) -> Optional[dict[str, Any]]:
    _prune()
    job = _jobs.get(job_id)
    if not job or job.owner != owner:
        return None
    return _public(job)


async def cancel_quant_job(owner: str, job_id: str) -> Optional[dict[str, Any]]:
    job = _jobs.get(job_id)
    if not job or job.owner != owner:
        return None
    if job.status in {"pending", "running"} and job.task:
        job.task.cancel()
        try:
            await job.task
        except asyncio.CancelledError:
            pass
    return _public(job)


async def reset_quant_jobs_for_tests() -> None:
    tasks = [job.task for job in _jobs.values() if job.task and not job.task.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _jobs.clear()
