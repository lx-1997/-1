from __future__ import annotations

import asyncio
from contextlib import suppress

from deepfocus_api.async_singleflight import AsyncSingleFlight


def test_same_key_concurrent_calls_share_one_task():
    async def scenario():
        gate = asyncio.Event()
        started = asyncio.Event()
        calls = 0
        flight: AsyncSingleFlight[dict] = AsyncSingleFlight()

        async def factory():
            nonlocal calls
            calls += 1
            started.set()
            await gate.wait()
            return {"ok": True}

        first = asyncio.create_task(flight.run("report-1", factory))
        await started.wait()
        second = asyncio.create_task(flight.run("report-1", factory))
        await asyncio.sleep(0)
        assert flight.in_flight_count == 1
        gate.set()
        results = await asyncio.gather(first, second)
        assert results == [{"ok": True}, {"ok": True}]
        assert calls == 1
        assert flight.in_flight_count == 0

    asyncio.run(scenario())


def test_waiter_cancellation_does_not_cancel_shared_task():
    async def scenario():
        gate = asyncio.Event()
        started = asyncio.Event()
        calls = 0
        flight: AsyncSingleFlight[str] = AsyncSingleFlight()

        async def factory():
            nonlocal calls
            calls += 1
            started.set()
            await gate.wait()
            return "done"

        cancelled_waiter = asyncio.create_task(flight.run("report-2", factory))
        await started.wait()
        surviving_waiter = asyncio.create_task(flight.run("report-2", factory))
        cancelled_waiter.cancel()
        with suppress(asyncio.CancelledError):
            await cancelled_waiter
        gate.set()
        assert await surviving_waiter == "done"
        assert calls == 1

    asyncio.run(scenario())
