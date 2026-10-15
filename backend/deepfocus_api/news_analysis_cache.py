"""Bounded, cancellation-safe coordination for cached news analysis."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from .analysis_limiter import AnalysisBusy


T = TypeVar("T")


@dataclass
class _Entry:
    lock: asyncio.Lock
    users: int = 0


class NewsAnalysisCoordinator(Generic[T]):
    """Serialize one source's cache check/generation without detached work.

    The factory rechecks the canonical cache after acquiring the lock. It runs
    inside the awaited request, so quota ContextVars retain their request scope
    and cancellation stops the model before that scope releases its leases.
    Refresh runs independently and cannot consume an older ordinary flight.
    """

    def __init__(self, *, max_keys: int = 512) -> None:
        self._entries: dict[str, _Entry] = {}
        self._max_keys = max(1, int(max_keys))

    async def run(
        self, key: str, factory: Callable[[], Awaitable[T]], *,
        refresh: bool = False, timeout_seconds: float = 150.0,
    ) -> T:
        if refresh or not key:
            return await asyncio.wait_for(factory(), timeout=timeout_seconds)
        entry = self._entries.get(key)
        if entry is None:
            # Active keys cannot be evicted while they have waiters; reject an
            # exceptional distinct-source burst instead of retaining unbounded
            # locks or falling back to duplicate uncoordinated generation.
            if len(self._entries) >= self._max_keys:
                raise AnalysisBusy("文章解读队列已满，请稍后重试")
            entry = _Entry(asyncio.Lock())
            self._entries[key] = entry
        entry.users += 1

        async def locked() -> T:
            async with entry.lock:
                return await factory()

        try:
            # One budget covers lock wait, global queue and model execution.
            # Unlike shielded shared tasks, wait_for joins cancellation before
            # the caller's quota scope can close.
            return await asyncio.wait_for(locked(), timeout=timeout_seconds)
        finally:
            entry.users -= 1
            if entry.users == 0 and self._entries.get(key) is entry:
                self._entries.pop(key, None)

    @property
    def in_flight_count(self) -> int:
        return len(self._entries)
