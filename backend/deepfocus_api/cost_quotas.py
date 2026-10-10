"""Transactional cost quotas, independent of best-effort analytics counters."""
from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from contextlib import closing
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Callable, Optional

import anyio
from starlette.exceptions import HTTPException
from starlette.responses import StreamingResponse

from .db import connect, data_path


class QuotaExceeded(Exception):
    pass


class QuotaLease(str):
    def __new__(cls, key: str, lease_id: str, path: str, ttl_seconds: float = 900):
        obj = super().__new__(cls, key)
        obj.lease_id, obj.path = lease_id, path
        obj.ttl_seconds = ttl_seconds
        return obj


_request_leases: ContextVar[Optional[list[QuotaLease]]] = ContextVar("cost_quota_leases", default=None)
_HEARTBEAT_SECONDS = 60.0


def _day() -> str:
    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


def _open(path: Optional[str] = None):
    conn = connect(path or data_path(".cost_quotas.sqlite3", "DEEPFOCUS_QUOTA_DB_PATH"))
    conn.execute("CREATE TABLE IF NOT EXISTS quota_daily (day TEXT, key TEXT, consumed INTEGER NOT NULL, PRIMARY KEY(day,key))")
    conn.execute("CREATE TABLE IF NOT EXISTS quota_leases (id TEXT PRIMARY KEY, day TEXT NOT NULL, key TEXT NOT NULL, expires_at REAL NOT NULL, status TEXT NOT NULL)")
    conn.execute("CREATE INDEX IF NOT EXISTS quota_pending ON quota_leases(day,key,status,expires_at)")
    conn.commit()
    return conn


def reserve(key: str, limit: int, *, initial_consumed: int = 0, ttl_seconds: float = 900) -> QuotaLease:
    """Reserve before starting work; serialized across processes by SQLite."""
    path = str(data_path(".cost_quotas.sqlite3", "DEEPFOCUS_QUOTA_DB_PATH"))
    day, now, lease_id = _day(), time.time(), uuid.uuid4().hex
    with closing(_open(path)) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT OR IGNORE INTO quota_daily VALUES (?,?,?)", (day, key, max(0, initial_consumed)))
            conn.execute("UPDATE quota_leases SET status='expired' WHERE status='pending' AND expires_at<=?", (now,))
            consumed = conn.execute("SELECT consumed FROM quota_daily WHERE day=? AND key=?", (day, key)).fetchone()[0]
            pending = conn.execute("SELECT count(*) FROM quota_leases WHERE day=? AND key=? AND status='pending'", (day, key)).fetchone()[0]
            if consumed + pending >= limit:
                raise QuotaExceeded(key)
            conn.execute("INSERT INTO quota_leases VALUES (?,?,?,?, 'pending')", (lease_id, day, key, now + ttl_seconds))
            conn.execute("DELETE FROM quota_leases WHERE day<? AND status!='pending'", ((datetime.now(timezone(timedelta(hours=8))).date() - timedelta(days=7)).isoformat(),))
    lease = QuotaLease(key, lease_id, path, ttl_seconds)
    leases = _request_leases.get()
    if leases is not None:
        leases.append(lease)
    return lease


def complete(lease: QuotaLease) -> bool:
    """Idempotent consumption: success is charged once, failure is released."""
    with closing(_open(lease.path)) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT day,key,status,expires_at FROM quota_leases WHERE id=?", (lease.lease_id,)).fetchone()
            if not row or row[2] == "consumed":
                return False
            if row[2] not in {"pending", "expired"}:
                return False
            if row[2] == "expired" or row[3] <= time.time():
                raise HTTPException(status_code=503, detail="额度预留已失效，请稍后重试")
            conn.execute("UPDATE quota_daily SET consumed=consumed+1 WHERE day=? AND key=?", row[:2])
            conn.execute("UPDATE quota_leases SET status='consumed' WHERE id=?", (lease.lease_id,))
            return True


def release(lease: QuotaLease) -> None:
    with closing(_open(lease.path)) as conn:
        with conn:
            conn.execute("UPDATE quota_leases SET status='released' WHERE id=? AND status='pending'", (lease.lease_id,))


def renew(lease: QuotaLease) -> None:
    """Keep active work reserved; an expired lease must never resume execution."""
    now = time.time()
    with closing(_open(lease.path)) as conn:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT status,expires_at FROM quota_leases WHERE id=?", (lease.lease_id,)).fetchone()
            if row and row[0] in {"consumed", "released"}:
                return
            if not row or row[0] != "pending" or row[1] <= now:
                raise HTTPException(status_code=503, detail="额度预留已失效，请稍后重试")
            conn.execute("UPDATE quota_leases SET expires_at=? WHERE id=? AND status='pending'", (now + lease.ttl_seconds, lease.lease_id))


def used(key: str) -> int:
    with closing(_open()) as conn:
        row = conn.execute("SELECT consumed FROM quota_daily WHERE day=? AND key=?", (_day(), key)).fetchone()
        pending = conn.execute("SELECT count(*) FROM quota_leases WHERE day=? AND key=? AND status='pending' AND expires_at>?", (_day(), key, time.time())).fetchone()[0]
        return int(row[0] if row else 0) + pending


class _QuotaScope:
    def __init__(self):
        self.leases: list[QuotaLease] = []
        self.execution_task = asyncio.current_task()
        self.failure: Optional[BaseException] = None
        self.closed = False
        self.heartbeat = asyncio.create_task(self._maintain())

    async def _maintain(self):
        try:
            while True:
                await asyncio.sleep(_HEARTBEAT_SECONDS)
                for lease in list(self.leases):
                    renew(lease)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.failure = exc
            if self.execution_task and not self.execution_task.done():
                self.execution_task.cancel()

    def check(self):
        if self.failure is not None:
            raise HTTPException(status_code=503, detail="额度服务暂时不可用，请稍后重试") from self.failure

    async def close(self):
        if self.closed:
            return
        self.closed = True
        self.execution_task = None
        self.heartbeat.cancel()
        # Starlette closes streams inside an AnyIO cancellation scope.
        with anyio.CancelScope(shield=True):
            await asyncio.gather(self.heartbeat, return_exceptions=True)
        for lease in self.leases:
            release(lease)


class _QuotaIterator:
    """An explicit close method also runs before the first body iteration."""
    def __init__(self, original, scope: _QuotaScope):
        self.original = original.__aiter__()
        self.scope = scope
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.closed:
            raise StopAsyncIteration
        previous_task = self.scope.execution_task
        self.scope.execution_task = asyncio.current_task()
        token = _request_leases.set(self.scope.leases)
        try:
            self.scope.check()
            return await self.original.__anext__()
        except BaseException as exc:
            await self.aclose()
            if isinstance(exc, asyncio.CancelledError):
                self.scope.check()
            raise
        finally:
            _request_leases.reset(token)
            if not self.scope.closed:
                self.scope.execution_task = previous_task

    async def aclose(self):
        if self.closed:
            return
        self.closed = True
        token = _request_leases.set(self.scope.leases)
        try:
            with anyio.CancelScope(shield=True):
                if hasattr(self.original, "aclose"):
                    await self.original.aclose()
        finally:
            _request_leases.reset(token)
            await self.scope.close()


class _QuotaStreamingResponse(StreamingResponse):
    def __init__(self, response: StreamingResponse, scope: _QuotaScope):
        # Preserve status, headers, background callbacks and response metadata.
        self.__dict__.update(response.__dict__)
        self.body_iterator = _QuotaIterator(response.body_iterator, scope)
        self._quota_scope = scope

    async def __call__(self, scope, receive, send):
        self._quota_scope.execution_task = asyncio.current_task()
        try:
            self._quota_scope.check()
            await super().__call__(scope, receive, send)
        finally:
            await self.body_iterator.aclose()


def quota_scope(func: Callable):
    """Renew active reservations and release them on errors or response close."""
    @wraps(func)
    async def wrapped(*args, **kwargs):
        scope = _QuotaScope()
        token = _request_leases.set(scope.leases)
        try:
            result = await func(*args, **kwargs)
            scope.check()
        except BaseException as exc:
            await scope.close()
            if isinstance(exc, asyncio.CancelledError):
                scope.check()
            raise
        finally:
            _request_leases.reset(token)
        if isinstance(result, StreamingResponse):
            scope.execution_task = None
            return _QuotaStreamingResponse(result, scope)
        await scope.close()
        return result

    # FastAPI inspects the wrapper's globals when resolving postponed types.
    # Resolve annotations against the original module before registration.
    sig = inspect.signature(func)
    def resolved(value):
        return eval(value, func.__globals__) if isinstance(value, str) else value
    wrapped.__signature__ = sig.replace(parameters=[p.replace(annotation=resolved(p.annotation)) for p in sig.parameters.values()], return_annotation=resolved(sig.return_annotation))
    return wrapped
