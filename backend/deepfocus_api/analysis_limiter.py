"""Bound document work and reserve capacity for interactive requests."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager


class AnalysisBusy(RuntimeError):
    """No analysis capacity became available within the queue budget."""


class AnalysisLimiter:
    def __init__(self, concurrency: int = 2, *, foreground_wait_seconds: float = 20.0, max_waiters: int = 8):
        self.capacity = max(1, int(concurrency))
        self.wait_seconds = max(0.01, float(foreground_wait_seconds))
        self.max_waiters = max(1, int(max_waiters))
        self._condition = asyncio.Condition()
        self._foreground = 0
        self._background = 0
        self._waiting = 0

    def locked(self) -> bool:
        return self._foreground + self._background >= self.capacity

    async def _acquire(self, background: bool) -> None:
        async with self._condition:
            if background:
                # Background work never queues and cannot consume every slot.
                if self._foreground or self._waiting or self._background or self.capacity < 2:
                    raise AnalysisBusy("后台预热让出在线解读容量")
                self._background += 1
                return
            if self.locked() and self._waiting >= self.max_waiters:
                raise AnalysisBusy("解读队列已满，请稍后重试")
            deadline = asyncio.get_running_loop().time() + self.wait_seconds
            self._waiting += 1
            try:
                while self.locked():
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        raise AnalysisBusy("解读排队超时，请稍后重试")
                    try:
                        await asyncio.wait_for(self._condition.wait(), remaining)
                    except asyncio.TimeoutError as exc:
                        raise AnalysisBusy("解读排队超时，请稍后重试") from exc
                self._foreground += 1
            finally:
                self._waiting -= 1
                self._condition.notify_all()

    async def _release(self, background: bool) -> None:
        async with self._condition:
            if background:
                self._background -= 1
            else:
                self._foreground -= 1
            self._condition.notify_all()

    async def __aenter__(self):
        await self._acquire(False)
        return self

    async def __aexit__(self, *_):
        await self._release(False)

    @asynccontextmanager
    async def background(self):
        await self._acquire(True)
        try:
            yield self
        finally:
            await self._release(True)
