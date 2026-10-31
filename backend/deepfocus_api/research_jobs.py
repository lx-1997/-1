"""Bounded, private, temporary jobs for the isolated research runtime."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
from typing import Any, Optional
import uuid

RETENTION_SECONDS = 1800
MAX_JOBS = 20
MAX_PENDING = 6
MAX_INPUT_BYTES = 3 * 1024 * 1024
MAX_RESULT_BYTES = 4 * 1024 * 1024
MAX_STDERR_BYTES = 1024 * 1024
TERMINAL = {"completed", "failed", "cancelled"}


class ResearchJobError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def runtime_python() -> str:
    configured = os.getenv("DEEPFOCUS_RESEARCH_PYTHON")
    if configured:
        path = Path(configured)
        if not path.is_absolute() or not path.is_file():
            raise ResearchJobError("研究计算环境路径无效，请管理员检查配置", 503)
        return str(path)
    deployed = Path("/opt/deepfocus/research-runtime/bin/python")
    return str(deployed) if deployed.is_file() else sys.executable


def worker_environment(temporary: str) -> dict[str, str]:
    # Analysis libraries receive no JWT, account DB or paid-provider keys.
    allowed = ("PATH", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
               "DEEPFOCUS_SEC_IDENTITY", "SEC_USER_AGENT", "DEEPFOCUS_RESEARCH_CONFIG")
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env.update(PYTHONPATH=str(Path(__file__).resolve().parents[1]), PYTHONUNBUFFERED="1",
               OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1",
               NUMEXPR_NUM_THREADS="1", MPLBACKEND="Agg", MPLCONFIGDIR=temporary,
               XDG_CACHE_HOME=temporary, TMPDIR=temporary, EDGAR_LOCAL_DATA_DIR=temporary + "/edgar",
               MLFLOW_DISABLE_AGENT_HINT="1",
               MLFLOW_TRACKING_URI="sqlite:///" + temporary + "/tracking.sqlite3")
    return env


async def _terminate(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=3)
    except asyncio.TimeoutError:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()


async def _worker_io(process, body: bytes) -> bytes:
    async def send():
        try:
            process.stdin.write(body)
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            process.stdin.close()

    async def read_bounded(reader, limit, message, status):
        output = bytearray()
        while True:
            chunk = await reader.read(min(65536, limit + 1 - len(output)))
            if not chunk:
                return bytes(output)
            output.extend(chunk)
            if len(output) > limit:
                raise ResearchJobError(message, status)

    tasks = [asyncio.create_task(send()),
             asyncio.create_task(read_bounded(process.stdout, MAX_RESULT_BYTES,
                 "计算结果超过体积限制，请缩小分析范围", 422)),
             asyncio.create_task(read_bounded(process.stderr, MAX_STDERR_BYTES,
                 "研究计算日志超过体积限制，请缩小范围或联系管理员", 503))]
    try:
        values = await asyncio.gather(*tasks)
        await process.wait()
        return values[1]
    finally:
        for task in tasks:
            if not task.done(): task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _finish_worker_cleanup(task):
    # A repeated cancellation of the caller must not abandon its real child.
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    task.result()
    if cancelled:
        raise asyncio.CancelledError()


async def run_worker(payload: dict, *, timeout: float = 120) -> dict:
    script = Path(__file__).with_name("research_tools_worker.py")
    body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
    if len(body) > MAX_INPUT_BYTES:
        raise ResearchJobError("研究输入超过体积限制，请缩小分析范围")
    with tempfile.TemporaryDirectory(prefix="df-research-") as temporary:
        launch = asyncio.create_task(asyncio.create_subprocess_exec(
            runtime_python(), str(script), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=worker_environment(temporary), cwd=temporary, start_new_session=True,
        ))
        process = None
        exchange = None

        async def cleanup():
            owned = process
            if owned is None:
                try:
                    owned = await asyncio.shield(launch)
                except Exception:
                    return  # Spawn failed; no child was acquired.
            if exchange is not None:
                if not exchange.done(): exchange.cancel()
                await asyncio.gather(exchange, return_exceptions=True)
            # Cancel the normal readers first, then discard during termination.
            # A paused full PIPE must not prevent Process.wait from completing.
            async def discard(reader):
                while await reader.read(65536):
                    pass
            drains = [asyncio.create_task(discard(reader)) for reader in (owned.stdout, owned.stderr)]
            try:
                await _terminate(owned)
            finally:
                if owned.stdin is not None: owned.stdin.close()
                for task in drains:
                    if not task.done(): task.cancel()
                await asyncio.gather(*drains, return_exceptions=True)

        try:
            process = await asyncio.shield(launch)
            exchange = asyncio.create_task(_worker_io(process, body))
            output = await asyncio.wait_for(asyncio.shield(exchange), timeout=timeout)
            if process.returncode:
                raise ResearchJobError("研究计算进程异常结束，请重试或联系管理员", 503)
            def finite_float(value):
                number = float(value)
                if not math.isfinite(number): raise ValueError("Nonfinite worker number")
                return number
            def reject_constant(value):
                raise ValueError("Invalid worker constant")
            try:
                result = json.loads(output, parse_float=finite_float, parse_constant=reject_constant)
            except (ValueError, UnicodeDecodeError):
                raise ResearchJobError("研究计算进程异常结束，请重试或联系管理员", 503)
            if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
                raise ResearchJobError("研究计算结果格式无效", 503)
            if not result["ok"]:
                error = str(result.get("error") or "研究计算失败")[:500]
                status = result.get("status", 422)
                if type(status) is not int or status not in {422, 429, 503, 504}:
                    raise ResearchJobError("研究计算结果格式无效", 503)
                raise ResearchJobError(error, status)
            if not isinstance(result.get("result"), dict):
                raise ResearchJobError("研究计算结果格式无效", 503)
            return result["result"]
        except asyncio.TimeoutError:
            raise ResearchJobError("任务达到计算时限，已停止；请缩小范围后重试", 504)
        finally:
            await _finish_worker_cleanup(asyncio.create_task(cleanup()))


@dataclass
class Job:
    id: str
    owner: str
    tool: str
    request_id: str
    digest: str
    state: str = "queued"
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    touched: float = field(default_factory=time.monotonic)
    result: Optional[dict] = None
    error: Optional[str] = None
    task: Optional[asyncio.Task] = None

    def public(self) -> dict:
        out = {name: getattr(self, name) for name in ("id", "tool", "state", "created_at", "updated_at")}
        if self.result is not None:
            out["result"] = self.result
        if self.error:
            out["error"] = self.error
        return out


class ResearchJobs:
    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self.slot: Optional[asyncio.Semaphore] = None
        self.closing = False

    def purge(self) -> None:
        now = time.monotonic()
        for identifier, job in list(self.jobs.items()):
            if job.state in TERMINAL and now - job.touched > RETENTION_SECONDS:
                del self.jobs[identifier]

    def get(self, identifier: str, owner: str) -> Job:
        self.purge()
        job = self.jobs.get(identifier)
        if job is None or job.owner != owner:
            raise ResearchJobError("任务不存在或已过期，请重新提交", 404)
        return job

    async def create(self, owner: str, tool: str, data: dict, request_id: str) -> dict:
        self.purge()
        if self.closing:
            raise ResearchJobError("研究服务正在更新，请稍后重试", 503)
        try:
            encoded = json.dumps({"tool": tool, "input": data}, ensure_ascii=False, allow_nan=False).encode()
        except (ValueError, TypeError, RecursionError):
            raise ResearchJobError("输入必须是有效 JSON，数值不能是 NaN 或无穷大")
        if len(encoded) > MAX_INPUT_BYTES:
            raise ResearchJobError("输入过大，文件最大 2 MiB，请缩小数据范围", 413)
        digest = hashlib.sha256(encoded).hexdigest()
        for job in self.jobs.values():
            if job.owner == owner and job.request_id == request_id:
                if job.digest != digest:
                    raise ResearchJobError("同一请求标识不能用于不同输入", 409)
                return job.public()
        if any(j.owner == owner and j.state not in TERMINAL for j in self.jobs.values()):
            raise ResearchJobError("已有进行中的研究任务，请先完成或取消", 409)
        if sum(j.state not in TERMINAL for j in self.jobs.values()) >= MAX_PENDING:
            raise ResearchJobError("研究计算队列已满，请稍后再试", 429)
        if len(self.jobs) >= MAX_JOBS:
            finished = sorted((j for j in self.jobs.values() if j.state in TERMINAL), key=lambda j: j.touched)
            if finished:
                del self.jobs[finished[0].id]
            else:
                raise ResearchJobError("研究任务数量已达上限，请稍后再试", 429)
        job = Job(uuid.uuid4().hex, owner, tool, request_id, digest)
        self.jobs[job.id] = job
        job.task = asyncio.create_task(self._execute(job, data))
        return job.public()

    async def _execute(self, job: Job, data: dict) -> None:
        try:
            if self.slot is None:
                self.slot = asyncio.Semaphore(1)
            async with self.slot:
                if job.state == "cancelled":
                    return
                job.state = "running"
                job.updated_at = _now()
                from .research_data import resolve_platform_history
                prepared, sources, warnings = await asyncio.wait_for(resolve_platform_history(job.tool, data), timeout=60)
                job.result = await run_worker({"tool": job.tool, "input": prepared})
                if sources:
                    job.result["sources"] = sources
                    job.result["warnings"] = list(dict.fromkeys(job.result.get("warnings", []) + warnings))
                job.state = "completed"
        except asyncio.CancelledError:
            job.state = "cancelled"
            job.result = None
        except ResearchJobError as error:
            job.state = "failed"
            job.error = str(error)
        except (ValueError, RuntimeError) as error:
            job.state = "failed"
            job.error = str(error)[:500]
        except asyncio.TimeoutError:
            job.state = "failed"
            job.error = "行情加载超过时限，请减少资产或导入数据"
        except Exception:
            job.state = "failed"
            job.error = "研究计算失败，请检查数据并重试"
        finally:
            # Raw input is not stored on Job and leaves scope after execution.
            job.updated_at = _now()
            job.touched = time.monotonic()

    async def delete(self, identifier: str, owner: str) -> dict:
        job = self.get(identifier, owner)
        if job.task is not None and not job.task.done():
            job.task.cancel()
            try:
                await job.task
            except asyncio.CancelledError:
                pass
        job.state = "cancelled"
        job.result = None
        job.error = None
        job.updated_at = _now()
        job.touched = time.monotonic()
        return job.public()

    async def shutdown(self) -> None:
        self.closing = True
        tasks = [j.task for j in self.jobs.values() if j.task and not j.task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for job in self.jobs.values():
            job.result = None
        self.jobs.clear()


manager = ResearchJobs()
