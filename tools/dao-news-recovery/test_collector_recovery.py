#!/usr/bin/env python3
"""Run isolated collector regressions without importing its private module.

Usage: python3 tools/dao-news-recovery/test_collector_recovery.py CANDIDATE.py -v

Only selected function definitions are compiled. Module imports, credentials,
startup code, real HTTP calls, notifications, and state writes never execute.
"""

from __future__ import annotations

import argparse
import ast
import copy
from datetime import datetime
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


CANDIDATE_FILE: Path | None = None
FUNCTIONS = {
    "get_api_token",
    "_is_auth_error",
    "_get_json_with_token",
    "_realinfo_timestamp",
    "fetch_realinfo_items",
    "_drain_pending_all_queue",
    "main",
}


def load_functions(candidate: Path, namespace: dict) -> dict:
    """Execute definitions only; never import or execute the collector module."""
    tree = ast.parse(candidate.read_text(encoding="utf-8"))
    definitions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS
    ]
    missing = FUNCTIONS - {node.name for node in definitions}
    if missing:
        raise AssertionError(f"Candidate is missing functions: {sorted(missing)}")
    module = ast.Module(body=definitions, type_ignores=[])
    # A synthetic filename prevents tracebacks from displaying private source.
    exec(compile(module, "<isolated-collector-functions>", "exec"), namespace)
    return namespace


class FakeClock:
    def __init__(self, now: float = 1000.0):
        self.now = now

    def time(self) -> float:
        return self.now

    def localtime(self):
        return time.localtime(self.now)

    def strftime(self, pattern: str, value=None) -> str:
        return time.strftime(pattern, self.localtime() if value is None else value)


class StopMainLoop(BaseException):
    """End the simulated infinite loop without resembling a fetch failure."""


class CollectorRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if CANDIDATE_FILE is None:
            raise unittest.SkipTest("Pass the collector candidate path on the command line")

    def namespace(self, **overrides) -> dict:
        values = {"print": Mock(), "datetime": datetime}
        load_functions(CANDIDATE_FILE, values)
        values.update(overrides)
        return values

    def queue_namespace(self, delivered=False, now=1000.0) -> tuple[dict, FakeClock]:
        clock = FakeClock(now)
        values = self.namespace(
            time=clock,
            send_to_dingtalk=Mock(return_value=delivered),
            save_pending_all_queue=Mock(),
            DINGTALK_WEBHOOK_ALL="test-webhook",
            DINGTALK_SECRET_ALL="test-secret",
            PENDING_ALL_RETRY_SECONDS=300.0,
        )
        return values, clock

    @staticmethod
    def due_queue(count=1000) -> list:
        return [
            {"content": f"test-message-{index}", "send_at": 900.0, "test_id": index}
            for index in range(count)
        ]

    def test_get_json_reloads_rotated_token_file_instead_of_old_cache(self):
        response = SimpleNamespace(
            status_code=200,
            json=Mock(return_value={"code": 200, "data": {}}),
            raise_for_status=Mock(),
        )
        request_get = Mock(return_value=response)
        with tempfile.TemporaryDirectory(prefix="collector-token-test-") as directory:
            token_file = Path(directory) / "token.txt"
            token_file.write_text("test-token-one", encoding="utf-8")
            values = self.namespace(
                os=SimpleNamespace(getenv=lambda *_: "", path=os.path),
                requests=SimpleNamespace(get=request_get),
                FUTOU_API_TOKEN_PATH=str(token_file),
                DEFAULT_FUTOU_API_TOKEN="test-fallback",
                _api_token_cache="test-stale-cache",
                refresh_api_token=Mock(return_value=None),
            )
            headers = {"token": "test-stale-header", "Accept": "application/json"}
            expected = {"code": 200, "data": {}}
            self.assertEqual(values["_get_json_with_token"]("test-endpoint", headers=headers), expected)
            token_file.write_text("test-token-two", encoding="utf-8")
            self.assertEqual(values["_get_json_with_token"]("test-endpoint", headers=headers), expected)

        self.assertEqual(
            [call.kwargs["headers"]["token"] for call in request_get.call_args_list],
            ["test-token-one", "test-token-two"],
        )
        self.assertEqual(headers["token"], "test-stale-header")
        self.assertEqual(values["_api_token_cache"], "test-token-two")
        values["refresh_api_token"].assert_not_called()

    def feed_namespace(self, grouped_data: dict) -> tuple[dict, dict]:
        frozen_now = datetime(2026, 10, 15, 12, 0, 0)

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen_now if tz is None else frozen_now.astimezone(tz)

        payload = {"code": 200, "data": {"grouped_data": grouped_data}}
        values = self.namespace(
            datetime=FrozenDateTime,
            time=FakeClock(frozen_now.timestamp()),
            _get_json_with_token=Mock(return_value=payload),
            refresh_api_token=Mock(return_value=None),
            REALINFO_SOURCE="futou",
            REALINFO_ROWS=100,
            REALINFO_REQUIRE_TODAY=True,
            REALINFO_MAX_AGE_SECONDS=0,
            API_REALINFO_URL="test-endpoint",
            HEADERS={},
            PUSH_DEBUG=False,
        )
        return values, payload

    def test_yesterday_news_is_rejected_without_rewriting_source_dates(self):
        yesterday = datetime(2026, 10, 14, 11, 59, 0)
        values, payload = self.feed_namespace({
            "2026-10-14": [{
                "id": "1", "name": "Yesterday test item",
                "createtime": int(yesterday.timestamp()),
                "createtime_text": "2026-10-14 11:59:00",
            }],
        })
        original = copy.deepcopy(payload)
        self.assertEqual(values["fetch_realinfo_items"](), [])
        self.assertEqual(payload, original)
        values["refresh_api_token"].assert_called_once()

    def test_today_group_cannot_relabel_explicit_yesterday_timestamp(self):
        values, payload = self.feed_namespace({
            "2026-10-15": [{
                "id": "1", "name": "Yesterday in mislabeled source group",
                "createtime_text": "2026-10-14 11:59:00",
            }],
        })
        original = copy.deepcopy(payload)
        self.assertEqual(values["fetch_realinfo_items"](), [])
        self.assertEqual(payload, original)

    def test_today_news_keeps_original_dates_and_sorts_newest_first(self):
        values, payload = self.feed_namespace({
            "2026-10-15": [
                {"id": "1", "name": "Earlier test item", "createtime_text": "2026-10-15 11:58:00"},
                {"id": "2", "name": "Latest test item", "createtime_text": "2026-10-15 11:59:00"},
            ],
            "2026-10-14": [
                {"id": "3", "name": "Old test item", "createtime_text": "2026-10-14 11:59:00"},
            ],
        })
        original = copy.deepcopy(payload)
        result = values["fetch_realinfo_items"]()
        self.assertEqual([item["id"] for item in result], ["2", "1"])
        self.assertEqual(result[0]["createtime_text"], "2026-10-15 11:59:00")
        self.assertEqual(payload, original)
        values["refresh_api_token"].assert_not_called()

    def test_failed_thousand_item_queue_attempts_once_then_cools_for_300_seconds(self):
        values, clock = self.queue_namespace(delivered=False)
        queue = self.due_queue()
        original_ids = [id(item) for item in queue]
        deadline = values["_drain_pending_all_queue"](queue, 0.0)
        self.assertEqual(deadline, 1300.0)
        self.assertEqual(queue[-1]["send_at"], 1300.0)
        self.assertEqual([id(item) for item in queue], original_ids[1:] + original_ids[:1])
        self.assertEqual(len(queue), 1000)
        self.assertEqual(values["send_to_dingtalk"].call_count, 1)
        values["save_pending_all_queue"].assert_called_once_with(queue)

        for now in (1000.0, 1100.0, 1299.999):
            clock.now = now
            self.assertEqual(values["_drain_pending_all_queue"](queue, deadline), deadline)
        self.assertEqual(values["send_to_dingtalk"].call_count, 1)
        self.assertEqual(len(queue), 1000)

        clock.now = 1300.0
        self.assertEqual(values["_drain_pending_all_queue"](queue, deadline), 1600.0)
        self.assertEqual(values["send_to_dingtalk"].call_count, 2)
        self.assertEqual(len(queue), 1000)
        self.assertEqual([item["test_id"] for item in queue[-2:]], [0, 1])
        self.assertEqual(queue[-1]["send_at"], 1600.0)
        first_text, second_text = [call.args[0] for call in values["send_to_dingtalk"].call_args_list]
        self.assertIn("test-message-0\n", first_text)
        self.assertIn("test-message-1\n", second_text)

    def test_success_deletes_only_one_item_and_waits_five_seconds(self):
        values, clock = self.queue_namespace(delivered=True)
        queue = self.due_queue()
        expected_remaining = copy.deepcopy(queue[1:])
        deadline = values["_drain_pending_all_queue"](queue, 0.0)
        self.assertEqual(queue, expected_remaining)
        self.assertEqual(deadline, 1005.0)
        self.assertEqual(values["send_to_dingtalk"].call_count, 1)

        clock.now = 1004.999
        self.assertEqual(values["_drain_pending_all_queue"](queue, deadline), deadline)
        self.assertEqual(len(queue), 999)
        self.assertEqual(values["send_to_dingtalk"].call_count, 1)
        clock.now = 1005.0
        self.assertEqual(values["_drain_pending_all_queue"](queue, deadline), 1010.0)
        self.assertEqual(len(queue), 998)
        self.assertEqual(values["send_to_dingtalk"].call_count, 2)
        self.assertEqual(values["save_pending_all_queue"].call_count, 2)

    def test_future_items_are_never_sent_or_removed(self):
        values, _ = self.queue_namespace(delivered=True)
        queue = [{"content": "Future test item", "send_at": 1001.0}]
        original = copy.deepcopy(queue)
        self.assertEqual(values["_drain_pending_all_queue"](queue, 0.0), 0.0)
        self.assertEqual(queue, original)
        values["send_to_dingtalk"].assert_not_called()
        values["save_pending_all_queue"].assert_not_called()

    def test_future_item_does_not_hide_due_item_later_in_queue(self):
        values, _ = self.queue_namespace(delivered=True)
        future = {"content": "Future test item", "send_at": 1001.0}
        queue = [future, {"content": "Due test item", "send_at": 900.0}]
        self.assertEqual(values["_drain_pending_all_queue"](queue, 0.0), 1005.0)
        self.assertEqual(queue, [future])
        self.assertIn("Due test item", values["send_to_dingtalk"].call_args.args[0])
        self.assertEqual(values["send_to_dingtalk"].call_count, 1)

    def main_namespace(self) -> tuple[dict, FakeClock, list]:
        values, clock = self.queue_namespace(delivered=False, now=601.0)
        queue = self.due_queue()
        for item in queue:
            item["send_at"] = 500.0
        values.update(
            acquire_lock_or_exit=Mock(),
            trim_current_stdout_log=Mock(),
            load_last_sent_key=Mock(return_value="test-existing-key"),
            load_sent_news_keys=Mock(return_value={"test-existing-key"}),
            load_max_sent_news_id=Mock(return_value=0),
            _next_interval=Mock(return_value=600.0),
            _load_pending_all_state=Mock(return_value=queue),
            load_last_realinfo_summary_slot=Mock(return_value=None),
            get_current_realinfo_summary_slot=Mock(return_value=None),
            fetch_realinfo_items=Mock(return_value=[]),
            NEWS_INTERVAL=600,
            _NEWS_OFFHOURS_MIN=600,
            _NEWS_OFFHOURS_MAX=900,
            PUSH_VERBOSE=False,
            BASE_SLEEP=5.0,
            LOCK_PATH="test-lock-path",
            os=SimpleNamespace(path=SimpleNamespace(exists=Mock(return_value=False)), remove=Mock()),
        )
        return values, clock, queue

    def test_main_fetches_on_first_iteration_after_600_seconds(self):
        values, _, queue = self.main_namespace()
        values["main"](once=True, use_ai=False)
        values["fetch_realinfo_items"].assert_called_once_with()
        self.assertEqual(values["send_to_dingtalk"].call_count, 1)
        self.assertEqual(len(queue), 1000)

    def test_failed_queue_does_not_block_later_main_fetch_iterations(self):
        values, clock, queue = self.main_namespace()
        events = []
        values["fetch_realinfo_items"].side_effect = lambda: events.append("fetch") or []
        values["send_to_dingtalk"].side_effect = lambda *_: events.append("notify") or False
        sleep_count = 0

        def sleep(seconds):
            nonlocal sleep_count
            self.assertEqual(seconds, 5.0)
            events.append("sleep")
            sleep_count += 1
            if sleep_count == 2:
                raise StopMainLoop()
            clock.now += 600.0

        clock.sleep = sleep
        with self.assertRaises(StopMainLoop):
            values["main"](once=False, use_ai=False)
        self.assertEqual(events, ["fetch", "notify", "sleep", "fetch", "notify", "sleep"])
        self.assertEqual(values["fetch_realinfo_items"].call_count, 2)
        self.assertEqual(values["send_to_dingtalk"].call_count, 2)
        self.assertEqual(len(queue), 1000)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path, help="Path to the private collector candidate")
    options, unittest_arguments = parser.parse_known_args()
    CANDIDATE_FILE = options.candidate
    unittest.main(argv=[__file__, *unittest_arguments])
