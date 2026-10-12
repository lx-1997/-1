from __future__ import annotations

import gc
import json
import sys
import threading
import weakref
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import pytest
from pydantic import BaseModel, PrivateAttr

from deepfocus_api.bounded_cache import (
    ByteBoundedTTLCache,
    clear_caches,
    get_cache_stats,
    prune_caches,
    register_cache,
    retained_size_bytes,
)


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


def cache(**overrides):
    config = {"max_entries": 4, "max_bytes": 4096, "max_item_bytes": 2048}
    config.update(overrides)
    return ByteBoundedTTLCache(**config)


def test_mapping_operations_and_lru_access():
    values = cache(max_entries=2)
    values["old"] = {"n": 1}
    values["warm"] = {"n": 2}
    assert len(values) == 2 and bool(values)
    assert values.get("old") == {"n": 1}
    values["new"] = {"n": 3}
    assert "warm" not in values
    assert list(values) == ["old", "new"]
    assert values.pop("old") == {"n": 1}
    assert values.pop("missing", "default") == "default"
    with pytest.raises(KeyError):
        values.pop("missing")
    assert values.stats()["evictions"] == 1
    values.clear()
    assert not values and values.stats()["bytes"] == 0


def test_mapping_preserves_falsey_values_and_default_lookup_behavior():
    values = cache()
    values["none"] = None
    values["empty"] = b""
    assert values.get("none", "default") is None
    assert values["none"] is None
    assert values.get("empty", "default") == b""
    assert "none" in values and bool(values)
    assert values.get("absent", "default") == "default"
    assert values.pop("none", "default") is None


def test_byte_budget_and_replacement_accounting():
    values = cache(max_entries=10, max_bytes=1200, max_item_bytes=1200)
    assert values.set("a", b"x" * 100)
    first_bytes = values.stats()["bytes"]
    assert values.set("a", b"x" * 200)
    assert values.stats()["bytes"] == first_bytes + 100
    assert values.set("b", b"x" * 200)
    assert values.get("a") == b"x" * 200
    assert values.set("c", b"x" * 200)
    assert values.get("b") is None
    assert values.get("a") is not None and values.get("c") is not None
    assert values.stats()["entries"] == 2
    assert values.stats()["bytes"] <= 1200


def test_oversized_replacement_drops_stale_without_evicting_other_keys():
    values = cache(max_item_bytes=800)
    assert values.set("a", "previous")
    assert values.set("b", "hot")
    assert not values.set("a", b"x" * 2000)
    assert values.get("a") is None
    assert values.get("b") == "hot"
    stats = values.stats()
    assert stats["rejected"] == 1 and stats["evictions"] == 0


def test_key_storage_counts_towards_budget_and_disabled_cache():
    values = cache(max_item_bytes=800)
    assert not values.set("secret" * 1000, b"tiny")
    assert values.stats()["entries"] == 0
    assert not cache(max_entries=0).set("a", 1)
    assert not cache(max_bytes=0).set("a", 1)
    assert not cache(max_item_bytes=0).set("a", 1)


def test_absolute_ttl_and_physical_expiry():
    clock = Clock()
    values = cache(ttl_seconds=10, clock=clock)
    assert values.set("a", "data")
    clock.now += 9
    assert values.get("a") == "data"
    clock.now += 1
    # Hits change LRU order but cannot extend the expiration deadline.
    assert values.get("a") is None
    assert values.stats()["entries"] == 0 and values.stats()["bytes"] == 0
    assert values.stats()["expired"] == 1
    assert values.stats()["hits"] == 1 and values.stats()["misses"] == 1


def test_per_entry_ttl_overrides_none_and_idle_pruning():
    clock = Clock()
    values = cache(ttl_seconds=10, clock=clock)
    assert values.set("default", 1)
    assert values.set("short", 2, ttl_seconds=3)
    assert values.set("forever", 3, ttl_seconds=None)
    clock.now += 4
    assert values.prune_expired() == 1
    clock.now += 6
    assert values.prune_expired() == 1
    assert list(values) == ["forever"]
    assert values.get("forever") == 3
    assert values.prune_expired() == 0
    assert not values.set("forever", 4, ttl_seconds=0)
    assert values.get("forever") is None


def test_len_bool_iteration_and_pop_ignore_expired_entries():
    clock = Clock()
    values = cache(ttl_seconds=1, clock=clock)
    values["a"] = 1
    clock.now += 1
    assert len(values) == 0 and not values
    values["b"] = 2
    clock.now += 1
    assert list(values) == []
    values["c"] = 3
    clock.now += 1
    assert values.pop("c", "expired") == "expired"
    assert values.stats()["expired"] == 3


def test_cache_stats_only_contain_fixed_numeric_fields_and_are_read_only():
    clock = Clock()
    values = cache(ttl_seconds=1, clock=clock)
    values["https://private.example/token=SECRET"] = {"password": "SECRET"}
    clock.now += 2
    stats = values.stats()
    assert stats["entries"] == 1  # Diagnostic reads do not mutate cache occupancy.
    assert "SECRET" not in json.dumps(stats) and "example" not in json.dumps(stats)
    assert set(stats) == {"entries", "bytes", "max_entries", "max_bytes", "max_item_bytes",
                          "hits", "misses", "evictions", "expired", "rejected"}
    assert all(isinstance(value, int) for value in stats.values())
    stats["bytes"] = 0
    assert values.stats()["bytes"] > 0
    assert values.prune_expired() == 1


def test_unicode_and_bytes_are_charged_at_python_retained_size():
    ascii_text = "a" * 5000
    unicode_text = "😀" * 5000
    assert retained_size_bytes(unicode_text) > retained_size_bytes(ascii_text) * 3
    assert retained_size_bytes(unicode_text) >= sys.getsizeof(unicode_text)
    assert retained_size_bytes(b"x" * 5000) >= 5000
    # A memoryview slice keeps the entire underlying byte buffer alive.
    assert retained_size_bytes(memoryview(b"x" * 5000)[:4]) >= 5000


def test_nested_models_dataclasses_and_slots_include_payloads():
    @dataclass
    class Article:
        paragraphs: list[str]
        details: dict

    class Model(BaseModel):
        article: Article
        _private: dict = PrivateAttr(default_factory=dict)

        class Config:
            arbitrary_types_allowed = True
            extra = "allow"

    class Slotted:
        __slots__ = ("payload",)

        def __init__(self, payload):
            self.payload = payload

    body = "财经😀" * 3000
    article = Article([body], {"full": body})
    model = Model(article=article, extra_payload=b"z" * 6000)
    model._private["extra"] = b"y" * 5000
    assert retained_size_bytes(article) >= sys.getsizeof(body)
    assert retained_size_bytes(model) >= retained_size_bytes(article) + 11000
    assert retained_size_bytes(Slotted(body)) >= sys.getsizeof(body)
    assert not cache(max_bytes=4096).set("article", model)


def test_pydantic_v1_nested_models_and_private_attributes_are_counted():
    from pydantic.v1 import BaseModel as V1Model, PrivateAttr as V1PrivateAttr

    class Child(V1Model):
        text: str

    class Parent(V1Model):
        child: Child
        _private: dict = V1PrivateAttr(default_factory=dict)

    Parent.update_forward_refs(Child=Child)
    model = Parent(child=Child(text="text" * 3000))
    baseline = retained_size_bytes(model)
    model._private["extra"] = b"x" * 5000
    assert retained_size_bytes(model) >= baseline + 5000
    assert not cache(max_bytes=4096).set("pydantic-v1", model)


def test_cycles_alias_deduplication_and_deep_nesting():
    shared = ["large" * 1000]
    duplicate = {"a": shared, "b": shared}
    distinct = {"a": shared, "b": list(shared)}
    assert retained_size_bytes(distinct) > retained_size_bytes(duplicate)
    duplicate["self"] = duplicate
    size = retained_size_bytes(duplicate)
    assert size >= 5000 and size < 20000
    assert cache(max_bytes=30000, max_item_bytes=30000).set("cyclic", duplicate)
    nested = 1
    for _ in range(2000):
        nested = [nested]
    assert retained_size_bytes(nested) > 100000


def test_custom_size_estimation_errors_fail_open_and_remove_previous_value():
    def sizeof(value):
        if value == "broken":
            raise RuntimeError("SECRET must never be logged")
        return len(value)

    values = cache(sizeof=sizeof)
    assert values.set("a", "good")
    assert not values.set("a", "broken")
    assert values.get("a") is None
    assert values.stats()["rejected"] == 1
    assert not values.set([], "good")  # Invalid keys cannot turn a successful fetch into 502.
    assert not cache(sizeof=lambda value: -1).set("a", "good")


def test_cache_policy_error_fails_open():
    clock = Clock()
    values = cache(clock=clock)
    assert values.set("a", "old")

    def fail_clock():
        raise RuntimeError("policy failure")

    values._clock = fail_clock
    assert not values.set("a", "new")
    assert values.stats()["entries"] == 0 and values.stats()["rejected"] == 1


def test_weak_registry_prunes_and_clears_without_retaining_unused_instances():
    clock = Clock()
    first = cache(name="test-bounded-first", ttl_seconds=1, clock=clock)
    second = register_cache("test-bounded-second", cache(clock=clock))
    first["secret-key"] = "secret-value"
    second["b"] = 2
    stats = get_cache_stats()
    assert stats["test-bounded-first"]["entries"] == 1
    assert "secret" not in json.dumps(stats)
    clock.now += 1
    assert prune_caches()["test-bounded-first"] == 1
    assert clear_caches()["test-bounded-second"] == 1
    reference = weakref.ref(second)
    del second
    gc.collect()
    assert reference() is None
    assert "test-bounded-second" not in get_cache_stats()


def test_concurrent_mapping_and_registry_operations_stay_bounded():
    values = cache(name="test-bounded-concurrent", max_entries=16,
                   max_bytes=6000, max_item_bytes=1000)
    barrier = threading.Barrier(6)

    def worker(index):
        barrier.wait(timeout=3)
        for turn in range(200):
            key = str((index + turn) % 30)
            values[key] = b"x" * (30 + turn % 20)
            values.get(key)
            if turn % 3 == 0:
                values.pop(str((index + turn + 1) % 30), None)
            if turn % 13 == 0:
                values.clear()
            temporary = cache(name=f"test-bounded-temporary-{index}")
            temporary["temp"] = turn
            get_cache_stats()
            prune_caches()
            stats = values.stats()
            assert stats["entries"] <= 16
            assert 0 <= stats["bytes"] <= 6000

    with ThreadPoolExecutor(max_workers=6) as executor:
        list(executor.map(worker, range(6)))
    stats = values.stats()
    assert stats["entries"] <= 16 and 0 <= stats["bytes"] <= 6000
