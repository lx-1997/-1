"""Collapse concurrent async work for the same cache key into one task."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Generic, TypeVar


T = TypeVar("T")


class AsyncSingleFlight(Generic[T]):
    """Share one in-flight task between callers with the same non-empty key.

    The shared task is shielded from caller cancellation. This matters for HTTP
    requests: a browser timeout must not cancel work that another request is
    waiting for, and the completed result can still be written to cache.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[T]] = {}

    async def run(self, key: str, factory: Callable[[], Awaitable[T]]) -> T:
        if not key:
            return await factory()

        task = self._tasks.get(key)
        if task is None or task.done():
            task = asyncio.create_task(factory())
            self._tasks[key] = task

            def _cleanup(done: asyncio.Task[T]) -> None:
                if self._tasks.get(key) is done:
                    self._tasks.pop(key, None)
                # Retrieve failures even if every HTTP waiter disconnected.
                if not done.cancelled():
                    done.exception()

            task.add_done_callback(_cleanup)

        return await asyncio.shield(task)

    @property
    def in_flight_count(self) -> int:
        return len(self._tasks)
