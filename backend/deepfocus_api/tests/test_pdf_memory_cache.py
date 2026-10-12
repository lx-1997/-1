"""PDF 内存缓存容量/驱逐/并发守卫；不改动去水印测试。"""
from concurrent.futures import ThreadPoolExecutor

import pytest

from deepfocus_api import pdf_brand as pb


@pytest.fixture(autouse=True)
def small_cache(monkeypatch):
    pb._MEM_CACHE.clear()
    monkeypatch.setattr(pb, "_MEM_CACHE_MAX", 3)
    monkeypatch.setattr(pb, "_MEM_CACHE_MAX_BYTES", 12)
    monkeypatch.setattr(pb, "_MEM_CACHE_MAX_ITEM_BYTES", 8)
    yield
    pb._MEM_CACHE.clear()


def test_byte_budget_evicts_least_recently_used():
    pb._mem_put("a", b"12345")
    pb._mem_put("b", b"12345")
    assert pb._mem_get("a") == b"12345"
    pb._mem_put("c", b"12345")
    assert pb._mem_get("b") is None
    assert pb._mem_get("a") == b"12345"
    assert pb._mem_get("c") == b"12345"
    assert pb._MEM_CACHE.bytes_used == 10


def test_replacement_accounting_and_oversized_pdf_do_not_evict_hot_files():
    pb._mem_put("a", b"12345678")
    pb._mem_put("a", b"12")
    pb._mem_put("b", b"12345678")
    pb._mem_put("large", b"123456789")
    assert pb._mem_get("large") is None
    assert pb._mem_get("a") == b"12"
    assert pb._mem_get("b") == b"12345678"
    assert pb._MEM_CACHE.bytes_used == 10
    pb._MEM_CACHE.clear()
    assert pb._MEM_CACHE.bytes_used == 0


def test_count_limit_and_disabled_cache(monkeypatch):
    for key in "abcd":
        pb._mem_put(key, b"1")
    assert len(pb._MEM_CACHE) == 3
    assert pb._mem_get("a") is None
    monkeypatch.setattr(pb, "_MEM_CACHE_MAX", 0)
    pb._MEM_CACHE.clear()
    pb._mem_put("a", b"1")
    assert len(pb._MEM_CACHE) == 0


def test_threaded_put_get_remain_within_budget():
    def exercise(index):
        for turn in range(100):
            key = str((index + turn) % 8)
            pb._mem_put(key, b"12345")
            pb._mem_get(key)

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(exercise, range(4)))
    assert len(pb._MEM_CACHE) <= 3
    assert pb._MEM_CACHE.bytes_used <= 12


def test_large_raw_pdf_stays_on_disk(monkeypatch, tmp_path):
    monkeypatch.setattr(pb, "_CACHE_DIR", tmp_path)
    raw = b"123456789"
    pb.store_raw_pdf("large-file", raw)
    assert pb._mem_get("fid:raw:large-file") is None
    assert pb.get_raw_by_file_id("large-file") == raw
    assert pb._MEM_CACHE.bytes_used == 0
