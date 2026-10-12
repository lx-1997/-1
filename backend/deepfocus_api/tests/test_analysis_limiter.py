import asyncio

import pytest

from deepfocus_api.analysis_limiter import AnalysisBusy, AnalysisLimiter


@pytest.mark.asyncio
async def test_background_reserves_foreground_slot_and_does_not_queue():
    gate = AnalysisLimiter(2)
    async with gate.background():
        with pytest.raises(AnalysisBusy):
            async with gate.background():
                pytest.fail("a second background analysis must be skipped")
        async with gate:
            assert gate.locked()
    assert not gate.locked()


@pytest.mark.asyncio
async def test_foreground_queue_is_bounded_and_times_out():
    gate = AnalysisLimiter(1, foreground_wait_seconds=0.03, max_waiters=1)
    async with gate:
        async def waiting():
            async with gate:
                pytest.fail("the occupied slot must not be granted")
        task = asyncio.create_task(waiting())
        await asyncio.sleep(0)
        with pytest.raises(AnalysisBusy, match="队列已满"):
            async with gate:
                pass
        with pytest.raises(AnalysisBusy, match="排队超时"):
            await task
    assert gate._waiting == 0
    async with gate:
        assert gate.locked()


@pytest.mark.asyncio
async def test_cancelled_waiter_and_holder_release_capacity():
    gate = AnalysisLimiter(2)
    async with gate:
        async with gate:
            async def wait():
                async with gate:
                    await asyncio.Event().wait()
            task = asyncio.create_task(wait())
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert gate._waiting == 0
            with pytest.raises(AnalysisBusy):
                async with gate.background():
                    pass
    assert not gate.locked()
