"""持久化数据层（data_store）的回归守卫：记录/读最新/TTL/历史/统计 + 优雅降级。

每个测试用独立临时库（monkeypatch DB_PATH），互不污染、不触真实库。
"""
from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from contextlib import closing

import pytest

from deepfocus_api import data_store


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(data_store, "DB_PATH", tmp_path / "ds.sqlite3")
    data_store.init_data_store()
    return data_store


def test_record_and_latest_with_symbol_normalization(store):
    store.record("verdict", "aapl", {"verdict": "重点跟踪", "score": 42})
    # 小写写入 → 大写读出（symbol 归一）。
    assert store.latest("verdict", "AAPL") == {"verdict": "重点跟踪", "score": 42}


def test_latest_ttl_window(store):
    store.record("quote", "TSLA", {"price": 250})
    time.sleep(0.05)
    assert store.latest("quote", "TSLA", max_age_seconds=0.01) is None  # 已过窗口
    assert store.latest("quote", "TSLA", max_age_seconds=10) == {"price": 250}  # 窗口内


def test_latest_returns_most_recent(store):
    store.record("verdict", "NVDA", {"score": 1})
    time.sleep(0.01)
    store.record("verdict", "NVDA", {"score": 2})
    assert store.latest("verdict", "NVDA")["score"] == 2


def test_history_newest_first_and_limit(store):
    for i in range(5):
        store.record("verdict", "MSFT", {"score": i})
        time.sleep(0.005)
    items = store.history("verdict", "MSFT", limit=3)
    assert [x["payload"]["score"] for x in items] == [4, 3, 2]  # 新→旧、limit 生效
    assert all("recorded_at" in x for x in items)


def test_stats_groups_by_kind(store):
    store.record("verdict", "AAPL", {"v": 1})
    store.record("verdict", "TSLA", {"v": 1})
    store.record("quote", "AAPL", {"p": 1})
    s = store.stats()
    assert s["total"] == 3
    kinds = {k["kind"]: k for k in s["kinds"]}
    assert kinds["verdict"]["count"] == 2 and kinds["verdict"]["symbols"] == 2
    assert kinds["quote"]["count"] == 1


def test_record_noop_on_empty_symbol_or_none_payload(store):
    store.record("verdict", "", {"x": 1})
    store.record("verdict", "AAPL", None)
    store.record("", "AAPL", {"x": 1})
    assert store.stats()["total"] == 0


def test_missing_returns_none_and_empty(store):
    assert store.latest("verdict", "ZZZZ") is None
    assert store.history("verdict", "ZZZZ") == []
    assert store.stats() == {"total": 0, "kinds": []}


def test_prune_keeps_latest_per_series(store):
    for i in range(10):
        store.record("verdict", "AAPL", {"i": i})
        time.sleep(0.002)
    deleted = store.prune(keep_per_series=3)
    assert deleted == 7
    items = store.history("verdict", "AAPL", limit=100)
    assert [x["payload"]["i"] for x in items] == [9, 8, 7]  # 只留最近 3（新→旧）


def test_prune_independent_per_kind_symbol(store):
    for series in (("verdict", "AAPL"), ("verdict", "TSLA"), ("quote", "AAPL")):
        for i in range(5):
            store.record(series[0], series[1], {"i": i})
            time.sleep(0.002)
    store.prune(keep_per_series=2)
    # 3 个 (kind,symbol) 序列各留 2 → 共 6，互不干扰。
    assert store.stats()["total"] == 6
    assert len(store.history("verdict", "AAPL")) == 2
    assert len(store.history("quote", "AAPL")) == 2


def test_record_caps_current_series_without_full_table_prune(store, monkeypatch):
    monkeypatch.setattr(store, "KEEP_PER_SERIES", 2)
    monkeypatch.setattr(store, "prune", lambda *args: pytest.fail("request did full-table maintenance"))
    for i in range(5):
        store.record("verdict", "AAPL", {"i": i})
        assert len(store.history("verdict", "AAPL")) <= 2
    assert [row["payload"]["i"] for row in store.history("verdict", "AAPL")] == [4, 3]


def test_data_store_closes_connections_and_uses_wal(store, monkeypatch):
    original = store._connect
    connections = []

    def tracked_connect():
        connection = original()
        connections.append(connection)
        return connection

    monkeypatch.setattr(store, "_connect", tracked_connect)
    store.init_data_store()
    store.record("verdict", "AAPL", {"v": 1})
    store.latest("verdict", "AAPL")
    store.history("verdict", "AAPL")
    store.hot_symbols()
    store.stats()
    store.prune()
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")
    with closing(sqlite3.connect(store.DB_PATH)) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_db_connect_honors_timeout_and_closes_if_setup_fails(store, monkeypatch):
    with closing(store.db.connect(store.DB_PATH, timeout=0.05)) as connection:
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 50

    class FailingConnection:
        closed = False

        def execute(self, statement):
            raise RuntimeError("setup failed")

        def close(self):
            self.closed = True

    connection = FailingConnection()
    monkeypatch.setattr(store.db.sqlite3, "connect", lambda *a, **kw: connection)
    with pytest.raises(RuntimeError, match="setup failed"):
        store.db.connect(":memory:")
    assert connection.closed


def test_async_record_runs_off_loop_and_is_visible_before_return(store, monkeypatch):
    original = store._record_values
    entered = threading.Event()
    release = threading.Event()

    def slow_record(*args):
        entered.set()
        assert release.wait(timeout=2)
        return original(*args)

    monkeypatch.setattr(store, "_record_values", slow_record)

    async def run():
        task = asyncio.create_task(store.async_record("quote", "aapl", {"price": 99}))
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(0.005)
        assert entered.is_set()
        # writer 卡住时事件循环仍能推进，且函数还没谎报已提交。
        for _ in range(3):
            await asyncio.sleep(0.005)
            assert not task.done()
        release.set()
        assert await task
        assert store.latest("quote", "AAPL") == {"price": 99}
        assert await store.shutdown_data_store()

    asyncio.run(run())


def test_async_record_respects_deep_context_and_cancelled_request_drains(store, monkeypatch):
    original = store._record_values
    entered = threading.Event()
    release = threading.Event()

    def slow_record(*args):
        entered.set()
        assert release.wait(timeout=2)
        return original(*args)

    monkeypatch.setattr(store, "_record_values", slow_record)

    async def run():
        token = store.DEEP_NO_PERSIST.set(True)
        try:
            assert not await store.async_record("deep", "AAPL", {"secret": 1})
        finally:
            store.DEEP_NO_PERSIST.reset(token)
        assert not entered.is_set()
        task = asyncio.create_task(store.async_record("quote", "AAPL", {"price": 10}))
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(0.005)
        assert entered.is_set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        assert await store.shutdown_data_store()
        assert store.latest("quote", "AAPL") == {"price": 10}
        assert store.latest("deep", "AAPL") is None
        assert not await store.async_record("quote", "AAPL", {"price": 20})

    asyncio.run(run())


def test_writer_bounds_inflight_bytes_and_overload_does_not_block_loop(store, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    monkeypatch.setattr(store, "_WRITE_QUEUE_BYTES", 32)
    monkeypatch.setattr(store, "_WRITE_QUEUE_MAX", 2)

    def slow_record(*args):
        entered.set()
        assert release.wait(timeout=2)
        return True

    monkeypatch.setattr(store, "_record_values", slow_record)

    async def run():
        # JSON "1234" 的保守计费=16，第一条在提交、第二条在队列，容量已满。
        first = asyncio.create_task(store.async_record("quote", "AAPL", 1234))
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(0.005)
        second = asyncio.create_task(store.async_record("quote", "MSFT", 1234))
        await asyncio.sleep(0.01)
        assert store._WRITER.pending_bytes == 32
        assert not await store.async_record("quote", "TSLA", 1234, queue_timeout=0.01)
        assert store._WRITER.pending_bytes == 32
        release.set()
        assert await first and await second
        assert await store.shutdown_data_store()

    asyncio.run(run())


def test_writer_queue_count_is_bounded(store, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    monkeypatch.setattr(store, "_WRITE_QUEUE_BYTES", 256)
    monkeypatch.setattr(store, "_WRITE_QUEUE_MAX", 2)

    def slow_record(*args):
        entered.set()
        assert release.wait(timeout=2)
        return True

    monkeypatch.setattr(store, "_record_values", slow_record)

    async def run():
        active = asyncio.create_task(store.async_record("quote", "AAPL", 1))
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(0.005)
        queued = [asyncio.create_task(store.async_record("quote", symbol, 1))
                  for symbol in ("MSFT", "TSLA")]
        await asyncio.sleep(0.01)
        assert store._WRITER.jobs.qsize() == 2
        assert not await store.async_record("quote", "NVDA", 1, queue_timeout=0)
        assert store._WRITER.jobs.qsize() == 2
        release.set()
        assert await active
        assert all(await asyncio.gather(*queued))
        assert await store.shutdown_data_store()

    asyncio.run(run())
