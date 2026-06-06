"""持久化数据层（data_store）的回归守卫：记录/读最新/TTL/历史/统计 + 优雅降级。

每个测试用独立临时库（monkeypatch DB_PATH），互不污染、不触真实库。
"""
from __future__ import annotations

import time

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


def test_record_triggers_periodic_prune(store, monkeypatch):
    monkeypatch.setattr(store, "_PRUNE_EVERY", 5)
    monkeypatch.setattr(store, "KEEP_PER_SERIES", 2)
    monkeypatch.setattr(store, "_write_count", 0)
    for i in range(5):  # 第 5 次写入触发 prune()（缺省 keep 运行时读 KEEP_PER_SERIES=2）
        store.record("verdict", "AAPL", {"i": i})
        time.sleep(0.002)
    assert len(store.history("verdict", "AAPL")) == 2
