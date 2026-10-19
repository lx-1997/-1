"""Read-path isolation, source failures and shared-request cancellation."""
import asyncio
import json
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from deepfocus_api import ai_fund_factors as factors


def available(score=0):
    return {"status": "available", "score": score}


class FactorReadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        factors._CACHE.clear()
        factors._PENDING.clear()
        factors._SLOTS = asyncio.Semaphore(4)

    async def asyncTearDown(self):
        tasks = list(factors._PENDING.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        factors._CACHE.clear()
        factors._PENDING.clear()

    async def test_invalid_symbols_never_reach_source(self):
        with patch.object(factors, "_source_json", new_callable=AsyncMock) as source:
            for value in ("", "600000/", "600000?x=1", "https://evil", "AAPL", "900901", "１２３４５６"):
                with self.assertRaises(ValueError):
                    await factors.get_stock_factors(value)
            source.assert_not_awaited()
        self.assertEqual(factors.normalize_symbol(" 300760 "), "300760")

    async def test_section_status_and_actual_zero_are_preserved(self):
        now = datetime(2026, 10, 19, tzinfo=timezone.utc)
        payload = {"data": {"code": "600000", "klines": [f"2026-10-{day},0" for day in (13, 14, 15, 16, 19)]}}
        with patch.object(factors, "_source_json", new=AsyncMock(return_value=payload)):
            result = await factors._capital("600000", "1.600000", now)
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["score"], 50)
        self.assertEqual(result["metrics"][0]["value"], 0)
        self.assertEqual(factors._public_section({"status": "missing", "score": None})["status"], "insufficient")

    async def test_identity_mismatch_is_missing_not_another_stock_score(self):
        payload = {"data": {"code": "000001", "klines": ["2026-10-19,100000000"]}}
        with patch.object(factors, "_source_json", new=AsyncMock(return_value=payload)):
            result = await factors._capital("600000", "1.600000", datetime.now(timezone.utc))
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["score"])

    async def test_partial_failure_keeps_success_and_uses_success_ttl(self):
        with patch.object(factors, "_capital", new=AsyncMock(return_value=available(0))), patch.object(
            factors, "_chips", new=AsyncMock(return_value=factors._missing("chips"))
        ):
            result = await factors.get_stock_factors("600000")
        self.assertEqual(result["capital"]["score"], 0)
        self.assertIsNone(result["chips"]["score"])
        self.assertGreater(factors._CACHE["600000"][0] - time.monotonic(), 55)
        json.dumps(result, allow_nan=False)

    async def test_failed_sources_are_cached_briefly_without_default_scores(self):
        with patch.object(factors, "_source_json", new=AsyncMock(side_effect=OSError("offline"))) as source:
            first = await factors.get_stock_factors("600000")
            second = await factors.get_stock_factors("600000")
        self.assertEqual(source.await_count, 2)
        self.assertEqual(first, second)
        for key in ("capital", "chips"):
            self.assertIsNone(first[key]["score"])
        self.assertLessEqual(factors._CACHE["600000"][0] - time.monotonic(), 15)

    async def test_cache_returns_independent_copies_and_expires(self):
        with patch.object(factors, "_capital", new=AsyncMock(return_value=available(17))) as capital, patch.object(
            factors, "_chips", new=AsyncMock(return_value=available(24))
        ):
            first = await factors.get_stock_factors("600000")
            first["capital"]["score"] = 99
            self.assertEqual((await factors.get_stock_factors("600000"))["capital"]["score"], 17)
            factors._CACHE["600000"] = (0, first)
            self.assertEqual((await factors.get_stock_factors("600000"))["capital"]["score"], 17)
            self.assertEqual(capital.await_count, 2)

    async def test_same_symbol_single_flight_survives_one_client_cancelling(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def capital(*args):
            started.set()
            await release.wait()
            return available(31)

        with patch.object(factors, "_capital", new=AsyncMock(side_effect=capital)) as source, patch.object(
            factors, "_chips", new=AsyncMock(return_value=available(41))
        ):
            first = asyncio.create_task(factors.get_stock_factors("600000"))
            await started.wait()
            second = asyncio.create_task(factors.get_stock_factors("600000"))
            await asyncio.sleep(0)
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            release.set()
            result = await second
            self.assertEqual(result["capital"]["score"], 31)
            self.assertEqual(source.await_count, 1)
        await asyncio.sleep(0)
        self.assertNotIn("600000", factors._PENDING)

    async def test_symbols_have_separate_request_identity(self):
        async def capital(code, secid, now):
            return available(11 if code == "600000" else 22)

        with patch.object(factors, "_capital", new=capital), patch.object(
            factors, "_chips", new=AsyncMock(return_value=factors._missing("chips"))
        ):
            left, right = await asyncio.gather(factors.get_stock_factors("600000"), factors.get_stock_factors("300760"))
        self.assertEqual((left["symbol"], left["capital"]["score"]), ("600000", 11))
        self.assertEqual((right["symbol"], right["capital"]["score"]), ("300760", 22))

    async def test_concurrency_saturation_returns_prompt_missing(self):
        factors._SLOTS = asyncio.Semaphore(0)
        with patch.object(factors, "_source_json", new_callable=AsyncMock) as source:
            result = await asyncio.wait_for(factors.get_stock_factors("600000"), timeout=.3)
        source.assert_not_awaited()
        self.assertIsNone(result["capital"]["score"])
        self.assertIsNone(result["chips"]["score"])

    async def test_section_timeout_does_not_remove_the_other_section(self):
        original = asyncio.wait_for

        async def fast_deadline(coro, timeout):
            return await original(coro, timeout=.01 if timeout == 4.8 else timeout)

        async def hanging(*args):
            await asyncio.sleep(10)

        with patch.object(factors.asyncio, "wait_for", new=fast_deadline), patch.object(
            factors, "_capital", new=hanging
        ), patch.object(factors, "_chips", new=AsyncMock(return_value=available(42))):
            result = await factors.get_stock_factors("600000")
        self.assertIsNone(result["capital"]["score"])
        self.assertEqual(result["chips"]["score"], 42)

    async def test_cache_is_bounded(self):
        with patch.object(factors, "_capital", new=AsyncMock(return_value=available(1))), patch.object(
            factors, "_chips", new=AsyncMock(return_value=available(2))
        ):
            for index in range(70):
                await factors.get_stock_factors(f"600{index:03d}")
        self.assertEqual(len(factors._CACHE), 64)
        self.assertNotIn("600000", factors._CACHE)
        self.assertIn("600069", factors._CACHE)


if __name__ == "__main__":
    unittest.main()
