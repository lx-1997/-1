"""Incremental-only queue tests against real temporary SQLite databases."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import types

import pytest


@pytest.fixture
def load_archive(tmp_path, monkeypatch):
    package_name = "_incremental_archive_candidate"
    package = types.ModuleType(package_name)
    package.__path__ = []
    db = types.ModuleType(package_name + ".db")
    db.data_path = lambda *_: tmp_path / "archive.sqlite3"
    db.connect = lambda path, **kwargs: sqlite3.connect(path, **kwargs)
    shared = types.ModuleType(package_name + ".shared_utils")
    shared.utc_now_iso = lambda: datetime.now(timezone.utc).isoformat()
    package.db = db
    package.shared_utils = shared
    monkeypatch.setitem(sys.modules, package_name, package)
    monkeypatch.setitem(sys.modules, db.__name__, db)
    monkeypatch.setitem(sys.modules, shared.__name__, shared)
    counter = [0]
    def load():
        counter[0] += 1
        name = package_name + ".archive_" + str(counter[0])
        spec = importlib.util.spec_from_file_location(name, Path(__import__("deepfocus_api.research_archive", fromlist=["x"]).__file__))
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        return module
    return load


@pytest.fixture
def archive(load_archive):
    return load_archive()


def report(fid, *, source_id=None, title=None, date="2026-10-15"):
    return {"id": source_id or fid, "file_id": fid, "title": title or f"行业研究{fid}", "filename": f"{fid}.pdf", "date": date}


def read_state(archive, fid):
    with sqlite3.connect(archive.DB_PATH) as conn:
        return conn.execute("SELECT status,payload FROM research_prewarm_seen WHERE file_id=?", (fid,)).fetchone()


def test_existing_archive_and_first_feed_are_baseline_not_backfill(archive):
    old = [report(f"old-{i:04d}", date="2026-10-01") for i in range(2507)]
    assert archive.upsert(old) == 2507
    result = archive.observe_prewarm_feed([old[-1], report("first-feed-only")])
    assert result == {"initialized": True, "baseline": 2508, "enqueued": 0}
    assert archive.list_prewarm_pending() == []
    assert read_state(archive, "old-0000") == ("baseline", None)
    assert read_state(archive, "first-feed-only") == ("baseline", None)
    assert not archive.mark_prewarm_done("old-0000")


def test_only_later_first_seen_feed_file_id_is_enqueued(archive):
    archive.observe_prewarm_feed([report("baseline")])
    result = archive.observe_prewarm_feed([report("baseline"), report("new"), report("new", source_id="duplicate-source-id")])
    assert result == {"initialized": False, "baseline": 0, "enqueued": 1}
    assert archive.list_prewarm_pending() == [report("new")]


def test_upsert_never_touches_queue_or_marks_future_arrivals_seen(archive):
    archive.upsert([report("historic-before-start")])
    with sqlite3.connect(archive.DB_PATH) as conn:
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name LIKE 'research_prewarm_%'").fetchall()
    archive.observe_prewarm_feed([report("baseline")])
    assert archive.upsert([report("searched-history", date="2025-01-01"), report("new-api-before-scheduler")]) == 2
    assert archive.list_prewarm_pending() == []
    assert read_state(archive, "searched-history") is None
    assert read_state(archive, "new-api-before-scheduler") is None
    archive.observe_prewarm_feed([report("baseline"), report("new-api-before-scheduler")])
    assert [item["file_id"] for item in archive.list_prewarm_pending()] == ["new-api-before-scheduler"]
    assert read_state(archive, "searched-history") is None


@pytest.mark.parametrize("empty", [[], [None, {}, {"file_id": "  "}]])
def test_empty_first_snapshot_does_not_initialize_and_recovery_stays_baseline(archive, empty):
    # Reads before first successful feed can create empty queue tables only.
    assert archive.list_prewarm_pending() == []
    assert archive.observe_prewarm_feed(empty) == {"initialized": False, "baseline": 0, "enqueued": 0}
    with sqlite3.connect(archive.DB_PATH) as conn:
        assert conn.execute("SELECT COUNT(*) FROM research_prewarm_state").fetchone()[0] == 0
    recovered = archive.observe_prewarm_feed([report("restored-old-feed")])
    assert recovered["initialized"] and recovered["enqueued"] == 0
    assert archive.list_prewarm_pending() == []
    archive.observe_prewarm_feed([report("restored-old-feed"), report("new-after-recovery")])
    assert [item["file_id"] for item in archive.list_prewarm_pending()] == ["new-after-recovery"]


def test_pending_restarts_and_done_seen_survive_restarts(load_archive):
    first = load_archive()
    first.observe_prewarm_feed([report("baseline")])
    first.observe_prewarm_feed([report("new")])
    second = load_archive()
    assert second.list_prewarm_pending() == [report("new")]
    assert second.mark_prewarm_done("new")
    third = load_archive()
    assert third.list_prewarm_pending() == []
    assert third.observe_prewarm_feed([report("new", title="重新出现")])["enqueued"] == 0
    assert read_state(third, "new") == ("done", None)


@pytest.mark.parametrize("status", ["done", "excluded"])
def test_terminal_rows_release_payload_and_can_never_requeue(archive, status):
    archive.observe_prewarm_feed([report("baseline")])
    archive.observe_prewarm_feed([report("new")])
    assert archive.mark_prewarm_done("new", status=status)
    assert not archive.mark_prewarm_done("new", status=status)
    assert read_state(archive, "new") == (status, None)
    assert archive.observe_prewarm_feed([report("new")])["enqueued"] == 0
    assert archive.list_prewarm_pending() == []


def test_invalid_completion_status_cannot_destroy_retryable_pending(archive):
    archive.observe_prewarm_feed([report("baseline")])
    archive.observe_prewarm_feed([report("new")])
    with pytest.raises(ValueError):
        archive.mark_prewarm_done("new", "failed")
    assert archive.list_prewarm_pending() == [report("new")]
    assert not archive.mark_prewarm_done("unknown")
    assert not archive.mark_prewarm_done("")


def test_pending_metadata_can_refresh_without_requeue(archive):
    archive.observe_prewarm_feed([report("baseline")])
    archive.observe_prewarm_feed([report("new", title="原始标题")])
    assert archive.observe_prewarm_feed([report("new", title="修正标题")])["enqueued"] == 0
    assert archive.list_prewarm_pending() == [report("new", title="修正标题")]


def test_archive_pruning_does_not_forget_baseline_pending_or_done(archive, monkeypatch):
    monkeypatch.setattr(archive, "_MAX_ARCHIVE", 2)
    archive.upsert([report("old", date="2020-01-01")])
    archive.observe_prewarm_feed([report("baseline")])
    archive.observe_prewarm_feed([report("pending"), report("done")])
    archive.mark_prewarm_done("done")
    archive.upsert([report(f"recent-{i}") for i in range(5)])
    assert archive.count() == 2
    assert "old" not in {item["file_id"] for item in archive.query()}
    assert archive.observe_prewarm_feed([report("old"), report("baseline"), report("pending"), report("done")])["enqueued"] == 0
    assert archive.list_prewarm_pending() == [report("pending")]


def test_observation_rolls_back_all_new_rows_if_one_payload_is_invalid(archive):
    archive.observe_prewarm_feed([report("baseline")])
    invalid = {**report("invalid"), "not_json": {"a", "set"}}
    with pytest.raises(TypeError):
        archive.observe_prewarm_feed([report("would-have-been-first"), invalid])
    assert archive.list_prewarm_pending() == []
    assert read_state(archive, "would-have-been-first") is None
    assert archive.observe_prewarm_feed([report("would-have-been-first")])["enqueued"] == 1


def test_complete_pending_queue_exceeds_2000_without_ui_limit(archive):
    archive.observe_prewarm_feed([report("baseline")])
    snapshot = [report(f"new-{i:04d}") for i in range(2507)]
    assert archive.observe_prewarm_feed(snapshot)["enqueued"] == 2507
    assert len(archive.list_prewarm_pending()) == 2507
    assert len(archive.list_prewarm_pending(limit=10)) == 10
    assert archive.list_prewarm_pending(limit=0) == []


def test_concurrent_first_observations_establish_exactly_one_baseline(archive):
    archive.upsert([report("archived")])
    def observe(i):
        return archive.observe_prewarm_feed([report("common"), report(f"competing-{i}")])
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(observe, range(12)))
    assert sum(result["initialized"] for result in results) == 1
    assert sum(result["enqueued"] for result in results) == 11
    assert len(archive.list_prewarm_pending()) == 11
    assert read_state(archive, "archived") == ("baseline", None)
    assert read_state(archive, "common") == ("baseline", None)


def test_concurrent_duplicate_observations_only_enqueue_once(archive):
    archive.observe_prewarm_feed([report("baseline")])
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: archive.observe_prewarm_feed([report("new")]), range(20)))
    assert sum(result["enqueued"] for result in results) == 1
    assert archive.list_prewarm_pending() == [report("new")]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: archive.mark_prewarm_done("new") if i % 2 else archive.observe_prewarm_feed([report("new")]), range(20)))
    assert archive.list_prewarm_pending() == []
    assert read_state(archive, "new") == ("done", None)


def test_baseline_skips_corrupt_archive_payload_and_normalizes_file_ids(archive):
    archive.upsert([report("archived")])
    with sqlite3.connect(archive.DB_PATH) as conn:
        conn.execute("INSERT INTO research_archive VALUES (?,?,?,?,?)", ("broken", "2025-01-01", "bad", "{bad", "today"))
    result = archive.observe_prewarm_feed([report(" feed "), report("feed")])
    assert result == {"initialized": True, "baseline": 2, "enqueued": 0}
    archive.observe_prewarm_feed([report(" new ")])
    assert archive.list_prewarm_pending()[0]["file_id"] == "new"
