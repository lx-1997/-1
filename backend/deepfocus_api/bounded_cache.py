"""Small in-process caches with bounded retained bytes, LRU eviction and absolute TTL.

Byte accounting estimates the retained object graph, not process RSS. Values should be
treated as immutable after insertion; call set() again after changing a cached value.
Shared objects are deduplicated within one entry and conservatively charged per entry.
This module has no model, database or PDF imports and never logs cached data.
"""
from __future__ import annotations

import math
import sys
import threading
import time
import types
import weakref
from collections import OrderedDict
from collections.abc import Mapping, MutableMapping
from dataclasses import fields, is_dataclass
from typing import Any, Callable, Generic, Iterator, TypeVar

K = TypeVar("K")
V = TypeVar("V")
_DEFAULT_TTL = object()
_MISSING = object()


def retained_size_bytes(value: Any) -> int:
    """Estimate owned/retained Python bytes without double-counting cycles or aliases.

    Includes container storage, Unicode/byte buffers, object dictionaries and slots.
    Dataclasses and Pydantic v1/v2 instances are handled without importing Pydantic.
    Class/module/function global graphs are intentionally not traversed. An iterative
    walk also supports deeply nested containers beyond Python's recursion limit.
    """
    seen: set[int] = set()
    pending = [value]
    total = 0
    while pending:
        current = pending.pop()
        identity = id(current)
        if identity in seen:
            continue
        seen.add(identity)
        total += sys.getsizeof(current)
        if isinstance(current, (str, bytes, bytearray, int, float, complex, bool, type(None))):
            continue
        if isinstance(current, memoryview):
            pending.append(current.obj)  # A tiny view can retain a much larger buffer.
            continue
        if isinstance(current, Mapping):
            for key, item in current.items():
                pending.extend((key, item))
            continue
        if isinstance(current, (list, tuple, set, frozenset)):
            pending.extend(current)
            continue
        if isinstance(current, (type, types.ModuleType, types.FunctionType,
                                types.BuiltinFunctionType, types.MethodType)):
            continue
        attributes = getattr(current, "__dict__", None)
        if attributes is not None:
            pending.append(attributes)
        if is_dataclass(current):
            pending.extend(getattr(current, field.name) for field in fields(current))
        # Includes slotted dataclasses and Pydantic's fields-set/private/extra data.
        for cls in type(current).__mro__:
            slots = cls.__dict__.get("__slots__", ())
            if isinstance(slots, str):
                slots = (slots,)
            for slot in slots:
                if slot in {"__dict__", "__weakref__"}:
                    continue
                if slot.startswith("__") and not slot.endswith("__"):
                    slot = f"_{cls.__name__.lstrip('_')}{slot}"
                item = getattr(current, slot, _MISSING)
                if item is not _MISSING:
                    pending.append(item)
    return max(0, total)


class _Entry(Generic[V]):
    __slots__ = ("value", "size", "expires_at")

    def __init__(self, value: V, size: int, expires_at: float | None) -> None:
        self.value = value
        self.size = size
        self.expires_at = expires_at


# Conservative per-entry allowance: slotted entry, size/deadline scalars and
# OrderedDict's key/link storage. The estimated key/value graph is added separately.
_ENTRY_OVERHEAD = sys.getsizeof(_Entry(None, 0, None)) + sys.getsizeof(0) + sys.getsizeof(0.0) + 96


class ByteBoundedTTLCache(MutableMapping[K, V], Generic[K, V]):
    def __init__(
        self, *, max_entries: int, max_bytes: int, max_item_bytes: int | None = None,
        ttl_seconds: float | None = None, name: str | None = None,
        sizeof: Callable[[V], int] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_entries < 0 or max_bytes < 0 or (max_item_bytes is not None and max_item_bytes < 0):
            raise ValueError("cache budgets must be nonnegative")
        if ttl_seconds is not None and math.isnan(float(ttl_seconds)):
            raise ValueError("cache TTL cannot be NaN")
        self.max_entries = int(max_entries)
        self.max_bytes = int(max_bytes)
        self.max_item_bytes = self.max_bytes if max_item_bytes is None else int(max_item_bytes)
        self.ttl_seconds = ttl_seconds
        self.name = name
        self._sizeof = sizeof
        self._clock = clock
        self._entries: OrderedDict[K, _Entry[V]] = OrderedDict()
        self._lock = threading.RLock()
        self._bytes = 0
        self._hits = self._misses = self._evictions = self._expired = self._rejected = 0
        if name is not None:
            register_cache(name, self)

    def _remove_locked(self, key: K) -> _Entry[V]:
        entry = self._entries.pop(key)
        self._bytes -= entry.size
        return entry

    def _prune_locked(self, now: float) -> int:
        stale = [key for key, entry in self._entries.items()
                 if entry.expires_at is not None and entry.expires_at <= now]
        for key in stale:
            self._remove_locked(key)
        self._expired += len(stale)
        return len(stale)

    def _reject(self, key: K) -> bool:
        with self._lock:
            self._rejected += 1
            try:
                if key in self._entries:
                    self._remove_locked(key)
            except Exception:
                pass  # An invalid key or user-defined hash must not break the request.
        return False

    def set(self, key: K, value: V, *, ttl_seconds: Any = _DEFAULT_TTL) -> bool:
        """Cache a value and return acceptance; estimation/policy errors fail open.

        Omitted TTL uses the cache default; explicit None disables expiration for this
        entry. Nonpositive TTL or an oversized entry removes the old same-key value
        and returns False. A hit updates LRU order without extending absolute TTL.
        A custom sizeof callback estimates the value; keys/entry overhead still count.
        """
        try:
            if self._sizeof is None:
                pair = (key, value)
                size = retained_size_bytes(pair) - sys.getsizeof(pair) + _ENTRY_OVERHEAD
            else:
                value_size = int(self._sizeof(value))
                if value_size < 0:
                    return self._reject(key)
                size = retained_size_bytes(key) + value_size + _ENTRY_OVERHEAD
            ttl = self.ttl_seconds if ttl_seconds is _DEFAULT_TTL else ttl_seconds
            if ttl is not None:
                ttl = float(ttl)
                if math.isnan(ttl) or ttl <= 0:
                    return self._reject(key)
            if self.max_entries == 0 or size > min(self.max_bytes, self.max_item_bytes):
                return self._reject(key)
            with self._lock:
                now = self._clock()
                self._prune_locked(now)
                if key in self._entries:
                    self._remove_locked(key)
                while self._entries and (len(self._entries) >= self.max_entries
                                         or self._bytes + size > self.max_bytes):
                    self._remove_locked(next(iter(self._entries)))
                    self._evictions += 1
                self._entries[key] = _Entry(value, size, None if ttl is None else now + ttl)
                self._bytes += size
            return True
        except Exception:
            return self._reject(key)

    def __setitem__(self, key: K, value: V) -> None:
        self.set(key, value)

    def _lookup_locked(self, key: K, *, access: bool = True) -> _Entry[V] | None:
        entry = self._entries.get(key)
        if entry is not None and entry.expires_at is not None and entry.expires_at <= self._clock():
            self._remove_locked(key)
            self._expired += 1
            entry = None
        if access:
            if entry is None:
                self._misses += 1
            else:
                self._hits += 1
                self._entries.move_to_end(key)
        return entry

    def __getitem__(self, key: K) -> V:
        with self._lock:
            entry = self._lookup_locked(key)
            if entry is None:
                raise KeyError(key)
            return entry.value

    def get(self, key: K, default: Any = None) -> Any:
        with self._lock:
            entry = self._lookup_locked(key)
            return default if entry is None else entry.value

    def __contains__(self, key: object) -> bool:
        with self._lock:
            return self._lookup_locked(key, access=False) is not None

    def __delitem__(self, key: K) -> None:
        with self._lock:
            if self._lookup_locked(key, access=False) is None:
                raise KeyError(key)
            self._remove_locked(key)

    def pop(self, key: K, default: Any = _MISSING) -> Any:
        with self._lock:
            entry = self._lookup_locked(key)
            if entry is None:
                if default is _MISSING:
                    raise KeyError(key)
                return default
            self._remove_locked(key)
            return entry.value

    def __iter__(self) -> Iterator[K]:
        with self._lock:
            self._prune_locked(self._clock())
            return iter(tuple(self._entries))

    def __len__(self) -> int:
        with self._lock:
            self._prune_locked(self._clock())
            return len(self._entries)

    def _clear_count(self) -> int:
        with self._lock:
            count = len(self._entries)
            self._entries.clear()
            self._bytes = 0
            return count

    def clear(self) -> None:
        self._clear_count()

    def prune_expired(self) -> int:
        with self._lock:
            return self._prune_locked(self._clock())

    def stats(self) -> dict[str, int]:
        """Read-only physical occupancy and lifetime counters; never keys/values."""
        with self._lock:
            return {"entries": len(self._entries), "bytes": self._bytes,
                    "max_entries": self.max_entries, "max_bytes": self.max_bytes,
                    "max_item_bytes": self.max_item_bytes,
                    "hits": self._hits, "misses": self._misses,
                    "evictions": self._evictions, "expired": self._expired,
                    "rejected": self._rejected}


_REGISTRY: weakref.WeakValueDictionary[str, ByteBoundedTTLCache] = weakref.WeakValueDictionary()
_REGISTRY_LOCK = threading.RLock()


def register_cache(name: str, cache: ByteBoundedTTLCache) -> ByteBoundedTTLCache:
    """Register a fixed diagnostic name; a replacement with that name supersedes it."""
    with _REGISTRY_LOCK:
        _REGISTRY[name] = cache
    return cache


def _cache_snapshot() -> list[tuple[str, ByteBoundedTTLCache]]:
    with _REGISTRY_LOCK:
        return list(_REGISTRY.items())


def get_cache_stats() -> dict[str, dict[str, int]]:
    return {name: cache.stats() for name, cache in _cache_snapshot()}


def prune_caches() -> dict[str, int]:
    return {name: cache.prune_expired() for name, cache in _cache_snapshot()}


def clear_caches() -> dict[str, int]:
    return {name: cache._clear_count() for name, cache in _cache_snapshot()}
