import importlib.util
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock


SPEC = importlib.util.spec_from_file_location("recover_events", Path(__file__).with_name("recover_events.py"))
recovery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recovery)
NOW = datetime(2026, 10, 15, 15, 20, tzinfo=recovery.BJ_TZ)


def item(source_id, when="2026-10-15 15:12:00", name="private content"):
    return {"id": source_id, "name": name, "createtime_text": when}


def payload(items):
    return {"code": 200, "data": {"grouped_data": {"day": items}}}


STORE = '''import sqlite3
DB_PATH = None
def record_event(event_type, *, source_id='', title='', content='', url='', source_time='', raw=None):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT OR IGNORE INTO events(event_key,type,source_id,title,content,url,source_time) VALUES(?,?,?,?,?,?,?)", (event_type+':'+source_id,event_type,source_id,title,content,url,source_time))
        return conn.execute("SELECT id FROM events WHERE event_key=?",(event_type+':'+source_id,)).fetchone()[0]
'''


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dao_dir = Path(self.tmp.name)
        (self.dao_dir / ".api_token").write_text("private-token")
        self.db = self.dao_dir / ".dao_events.sqlite3"
        with sqlite3.connect(str(self.db)) as conn:
            conn.execute("CREATE TABLE events(id INTEGER PRIMARY KEY, event_key TEXT UNIQUE, type TEXT, source_id TEXT, title TEXT, content TEXT, url TEXT, source_time TEXT)")
            conn.execute("INSERT INTO events(event_key,type,source_id,source_time) VALUES('realinfo:base','realinfo','base','2026-10-15 14:00:00')")
        (self.dao_dir / "dao_event_store.py").write_text(STORE)

    def run_recovery(self, pages, **kwargs):
        return recovery.recover(self.dao_dir, now=NOW, fetch_page=Mock(side_effect=pages), **kwargs)

    def count(self):
        with sqlite3.connect(str(self.db)) as conn:
            return conn.execute("SELECT count(*) FROM events").fetchone()[0]

    def test_default_dry_run_never_imports_or_writes_or_exposes_content(self):
        loader = Mock(side_effect=AssertionError("must not import"))
        result = self.run_recovery([payload([item(123)])], store_loader=loader)
        self.assertEqual(result["mode"], "dry-run")
        self.assertEqual(result["candidates"], 1)
        self.assertEqual(self.count(), 1)
        loader.assert_not_called()
        output = json.dumps(result)
        self.assertNotIn("private-token", output)
        self.assertNotIn("private content", output)

    def test_dates_cutoff_and_future_boundary_are_preserved(self):
        rows = [item("old", "2026-10-14 15:12:00"), item("same", "2026-10-15 14:00:00"),
                item("invalid", "invalid"), item("future", "2026-10-15 15:25:01"),
                item("boundary", "2026-10-15 15:25:00"), item("new")]
        original = json.dumps(rows)
        result = self.run_recovery([payload(rows)])
        self.assertEqual([x["source_id"] for x in result["items"]], ["new", "boundary"])
        self.assertEqual(result["items"][1]["source_time"], "2026-10-15 15:25:00")
        self.assertEqual(result["rejected"], {"invalid": 1, "other_day": 1, "future": 1, "at_or_before_cutoff": 1})
        self.assertEqual(json.dumps(rows), original)

    def test_natural_pagination_stops_at_cutoff_and_deduplicates_source_ids(self):
        first = [item(i) for i in range(1, 101)]
        second = [item(100), item(101), item("cutoff", "2026-10-15 14:00:00")]
        fetch = Mock(side_effect=[payload(first), payload(second)])
        result = recovery.recover(self.dao_dir, now=NOW, fetch_page=fetch)
        self.assertEqual(result["pages"], 2)
        self.assertEqual(result["candidates"], 101)
        self.assertEqual([call.args[1:] for call in fetch.call_args_list], [(1, 100), (2, 100)])

    def test_at_most_five_pages(self):
        fetch = Mock(return_value=payload([item(i + 1) for i in range(100)]))
        result = recovery.recover(self.dao_dir, now=NOW, fetch_page=fetch)
        self.assertEqual(fetch.call_count, 5)
        self.assertTrue(result["page_limit_reached"])

    def test_second_page_failure_does_not_import_or_write(self):
        loader = Mock()
        with self.assertRaises(recovery.RecoveryError):
            self.run_recovery([payload([item(i + 1) for i in range(100)]), {"code": 401}], apply=True, store_loader=loader)
        loader.assert_not_called()
        self.assertEqual(self.count(), 1)

    def test_http_exception_does_not_import_or_write(self):
        loader = Mock()
        with self.assertRaises(OSError):
            self.run_recovery([OSError("mock failure")], apply=True, store_loader=loader)
        loader.assert_not_called()
        self.assertEqual(self.count(), 1)

    def test_apply_writes_in_source_order_and_is_idempotent(self):
        rows = [item(124, "2026-10-15 15:13:00"), item(123), item(123)]
        result = self.run_recovery([payload(rows)], apply=True)
        self.assertEqual(result["write_attempts"], 2)
        self.assertEqual([x["source_id"] for x in result["items"]], ["123", "124"])
        self.assertEqual(self.count(), 3)
        again = self.run_recovery([payload(rows)], apply=True)
        self.assertEqual(again["write_attempts"], 0)
        self.assertEqual(self.count(), 3)

    def test_record_event_natural_key_remains_idempotent_when_timestamp_changes(self):
        self.run_recovery([payload([item(123)])], apply=True)
        result = self.run_recovery([payload([item(123, "2026-10-15 15:13:00")])], apply=True)
        self.assertEqual(result["already_present"], 1)
        self.assertEqual(result["missing"], 0)
        self.assertEqual(result["write_attempts"], 0)
        self.assertEqual(self.count(), 2)

    def test_only_same_day_realinfo_affects_cutoff(self):
        with sqlite3.connect(str(self.db)) as conn:
            conn.executemany("INSERT INTO events(event_key,type,source_time) VALUES(?,?,?)", [
                ("article", "article", "2026-10-15 15:19:00"),
                ("yesterday", "realinfo", "2026-10-14 23:59:59"),
                ("later", "realinfo", "2026-10-15T14:30:00+08:00"),
            ])
        result = self.run_recovery([payload([item(123)])])
        self.assertEqual(result["cutoff"], "2026-10-15T14:30:00+08:00")

    def test_missing_database_is_not_created(self):
        self.db.unlink()
        with self.assertRaises(recovery.RecoveryError):
            self.run_recovery([payload([item(123)])])
        self.assertFalse(self.db.exists())

    def test_since_audits_a_gap_after_the_live_cutoff_has_advanced(self):
        with sqlite3.connect(str(self.db)) as conn:
            conn.execute("INSERT INTO events(event_key,type,source_id,source_time) VALUES('realinfo:live','realinfo','live','2026-10-15 15:19:00')")
        rows = [item(123), item(124, "2026-10-15 14:05:34")]
        result = self.run_recovery([payload(rows)], since="2026-10-15 14:05:34")
        self.assertEqual(result["cutoff"], "2026-10-15T14:05:34+08:00")
        self.assertEqual(result["latest_existing_source_time"], "2026-10-15T15:19:00+08:00")
        self.assertEqual(result["cutoff_source"], "since")
        self.assertEqual([x["source_id"] for x in result["items"]], ["123"])
        self.assertEqual(result["missing"], 1)
        self.assertEqual(result["write_attempts"], 0)
        self.assertEqual(self.count(), 2)

    def test_since_historical_apply_is_rejected_before_network_import_or_writes(self):
        fetch = Mock()
        loader = Mock()
        with self.assertRaisesRegex(recovery.RecoveryError, "bridge sorts by ingestion time"):
            recovery.recover(
                self.dao_dir, now=NOW, since="2026-10-15 13:59:59",
                fetch_page=fetch, store_loader=loader, apply=True,
            )
        fetch.assert_not_called()
        loader.assert_not_called()
        self.assertEqual(self.count(), 1)

    def test_since_rejects_other_day_future_and_invalid_before_network_or_writes(self):
        for since in ("2026-10-14 14:00:00", "2026-10-15 15:20:01", "invalid"):
            fetch = Mock()
            loader = Mock()
            with self.subTest(since=since), self.assertRaises(recovery.RecoveryError):
                recovery.recover(self.dao_dir, now=NOW, since=since, fetch_page=fetch, store_loader=loader, apply=True)
            fetch.assert_not_called()
            loader.assert_not_called()
            self.assertEqual(self.count(), 1)


if __name__ == "__main__":
    unittest.main()
