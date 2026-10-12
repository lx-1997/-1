"""Process-wide, bounded resources for OpenAI-compatible model calls.

Clients never cross event loops or credential boundaries. Circuit keys contain
credential digests, not secrets; a changed key gets a fresh circuit immediately.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import re
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable


CLIENT_CACHE_MAX = 24
CLIENT_IDLE_SECONDS = 600.0
CIRCUIT_CACHE_MAX = 512
QUOTA_COOLDOWN_SECONDS = 6 * 60 * 60.0
VISION_CAPABILITY_SECONDS = 24 * 60 * 60.0
_TEXT_ONLY_MODELS = frozenset({"glm-4-flash-250414"})
_lock = threading.RLock()
_REQUEST_DEADLINE: ContextVar[float | None] = ContextVar("llm_request_deadline", default=None)


class VisionUnavailableError(RuntimeError):
    """The configured pool has no currently usable image-capable model."""


class ModelPoolUnavailableError(RuntimeError):
    """All configured slots are cooling down; do not reset their circuits."""


@dataclass
class CompletionDeadline:
    expires_at: float

    @classmethod
    def after(cls, seconds: float) -> "CompletionDeadline":
        expires_at = time.monotonic() + max(0.0, float(seconds))
        inherited = _REQUEST_DEADLINE.get()
        return cls(min(expires_at, inherited) if inherited is not None else expires_at)

    def remaining(self) -> float:
        remaining = self.expires_at - time.monotonic()
        if remaining <= 0:
            raise asyncio.TimeoutError("模型调用已耗尽整体等待预算")
        return remaining


@contextmanager
def llm_deadline(seconds: float):
    """Share a deadline across a caller's completion/JSON-repair calls.

    Tool/CPU work still requires the caller's overall timeout. ContextVar keeps
    simultaneous requests isolated and automatically propagates to child tasks.
    """
    deadline = CompletionDeadline.after(seconds)
    token = _REQUEST_DEADLINE.set(deadline.expires_at)
    try:
        yield deadline
    finally:
        _REQUEST_DEADLINE.reset(token)


def _config_identity(config: dict[str, Any], *, include_model: bool) -> str:
    # Label is presentation only. Two labels using one credential must share a
    # circuit; a changed credential must never inherit a previous key's failure.
    values = {key: str(config.get(key) or "") for key in (
        "provider", "base_url", "api_key", "organization", "project",
    )}
    if include_model:
        values["model"] = str(config.get("model") or "")
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def pool_slot_key(config: dict[str, Any]) -> str:
    return _config_identity(config, include_model=True)


@dataclass
class _ClientEntry:
    loop: asyncio.AbstractEventLoop
    client: Any
    last_used: float
    active: int = 0
    retired: bool = False


_CLIENTS: OrderedDict[tuple[Any, ...], _ClientEntry] = OrderedDict()
_CLIENT_ENTRIES: dict[int, _ClientEntry] = {}
_CLOSE_TASKS: set[asyncio.Task] = set()
_COOLDOWNS: OrderedDict[str, float] = OrderedDict()
_VISION_UNSUPPORTED: OrderedDict[str, float] = OrderedDict()


async def _close_client(client: Any) -> None:
    try:
        close = getattr(client, "close", None)
        if close:
            result = close()
            if inspect.isawaitable(result):
                await result
    except Exception:
        # Shutdown and stale-loop cleanup must not interrupt an active request.
        pass


def _schedule_close(entry: _ClientEntry) -> None:
    if entry.active:
        return
    _CLIENT_ENTRIES.pop(id(entry.client), None)
    if entry.loop.is_running() and not entry.loop.is_closed():
        def schedule() -> None:
            task = entry.loop.create_task(_close_client(entry.client))
            _CLOSE_TASKS.add(task)
            task.add_done_callback(_CLOSE_TASKS.discard)
        entry.loop.call_soon_threadsafe(schedule)
    # A stopped/closed loop cannot safely close transports from another loop.
    # close_llm_clients() runs during application shutdown, before loop closure.


def get_cached_client(config: dict[str, Any], factory: Callable[..., Any]) -> Any:
    kwargs: dict[str, Any] = {
        "api_key": config["api_key"], "base_url": config.get("base_url") or None,
        "max_retries": 0,  # Retry/failover belongs to the shared deadline below.
    }
    for key in ("organization", "project"):
        if config.get(key):
            kwargs[key] = config[key]
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Construction outside a running loop is supported for existing callers;
        # such a client is not shared with later asynchronous requests.
        return factory(**kwargs)
    proxy_values = tuple(os.environ.get(name, "") for name in (
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
        "http_proxy", "https_proxy", "all_proxy", "no_proxy",
    ))
    proxy_identity = hashlib.sha256(json.dumps(proxy_values).encode()).hexdigest()
    key = (loop, _config_identity(config, include_model=False), factory, proxy_identity)
    now = time.monotonic()
    with _lock:
        for old_key, entry in list(_CLIENTS.items()):
            if not entry.active and (entry.loop.is_closed() or now - entry.last_used >= CLIENT_IDLE_SECONDS):
                _CLIENTS.pop(old_key)
                entry.retired = True
                _schedule_close(entry)
        entry = _CLIENTS.get(key)
        if entry is not None:
            entry.last_used = now
            _CLIENTS.move_to_end(key)
            return entry.client
        while len(_CLIENTS) >= CLIENT_CACHE_MAX:
            old_key, old_entry = next(iter(_CLIENTS.items()))
            _CLIENTS.pop(old_key)
            old_entry.retired = True
            _schedule_close(old_entry)
        entry = _ClientEntry(loop=loop, client=factory(**kwargs), last_used=now)
        _CLIENTS[key] = entry
        _CLIENT_ENTRIES[id(entry.client)] = entry
        return entry.client


def borrow_client(client: Any) -> None:
    with _lock:
        entry = _CLIENT_ENTRIES.get(id(client))
        if entry:
            entry.active += 1
            entry.last_used = time.monotonic()


def release_client(client: Any) -> None:
    with _lock:
        entry = _CLIENT_ENTRIES.get(id(client))
        if entry:
            entry.active = max(0, entry.active - 1)
            entry.last_used = time.monotonic()
            if entry.retired:
                _schedule_close(entry)


async def close_llm_clients() -> None:
    """Close clients owned by this loop; call after draining requests at shutdown."""
    loop = asyncio.get_running_loop()
    with _lock:
        entries = [entry for entry in _CLIENT_ENTRIES.values() if entry.loop is loop and not entry.active]
        for key, entry in list(_CLIENTS.items()):
            if entry.loop is loop:
                _CLIENTS.pop(key)
                entry.retired = True
        for entry in entries:
            _CLIENT_ENTRIES.pop(id(entry.client), None)
    await asyncio.gather(*(_close_client(entry.client) for entry in entries))
    pending = [task for task in _CLOSE_TASKS if task.get_loop() is loop]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


class DeadlineStream:
    """Keep both the deadline and client lease until the response is consumed."""
    def __init__(self, stream: Any, client: Any, deadline: CompletionDeadline,
                 on_failure: Callable[[Exception], None] | None = None):
        self._stream = stream
        self._iterator = stream.__aiter__()
        self._client = client
        self._deadline = deadline
        self._closed = False
        self._on_failure = on_failure

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return await asyncio.wait_for(self._iterator.__anext__(), self._deadline.remaining())
        except BaseException as exc:
            if isinstance(exc, Exception) and not isinstance(exc, StopAsyncIteration):
                self.mark_failed(exc)
            await self.close()
            raise

    def mark_failed(self, exc: Exception) -> None:
        if self._on_failure:
            self._on_failure(exc)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            close = getattr(self._stream, "close", None) or getattr(self._stream, "aclose", None)
            if close:
                result = close()
                if inspect.isawaitable(result):
                    await result
        except Exception:
            pass
        finally:
            release_client(self._client)


def exception_status(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _exception_text(exc: Exception) -> str:
    body = getattr(exc, "body", None)
    return (str(exc) + " " + (json.dumps(body, ensure_ascii=False, default=str) if body else "")).lower()


def _is_quota_exhausted(exc: Exception) -> bool:
    return exception_status(exc) == 402 or any(marker in _exception_text(exc) for marker in (
        "insufficient_quota", "insufficient balance", "quota_exhausted", "quota exceeded",
        "quota has been exhausted", "token plan", "套餐额度", "用量上限", "额度已耗尽", "余额不足",
    ))


def _reset_delay(value: Any, *, absolute: bool = False) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    try:
        number = float(text)
        if absolute or number > 1_000_000_000:
            if number > 1_000_000_000_000:
                number /= 1000
            return max(0.0, number - time.time())
        return max(0.0, number)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, parsed.timestamp() - time.time())
    except ValueError:
        pass
    try:
        return max(0.0, parsedate_to_datetime(text).timestamp() - time.time())
    except (ValueError, TypeError):
        pass
    # OpenAI-style reset headers can be relative durations, e.g. "1m30s".
    parts = re.findall(r"(\d+(?:\.\d+)?)(ms|s|m|h|d)", text)
    if parts and "".join(number + unit for number, unit in parts) == text:
        return sum(float(number) * {"ms": .001, "s": 1, "m": 60, "h": 3600, "d": 86400}[unit] for number, unit in parts)
    return None


def cooldown_seconds(config: dict[str, Any], exc: Exception) -> float:
    status = exception_status(exc)
    quota = _is_quota_exhausted(exc)
    default = QUOTA_COOLDOWN_SECONDS if quota else (300.0 if status in {401, 403} else (30.0 if status == 429 else 15.0))
    delays: list[float] = []
    quota_resets: list[float] = []
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {}) or {}
    for header in ("retry-after", "x-ratelimit-reset", "x-ratelimit-reset-requests", "x-ratelimit-reset-tokens"):
        value = headers.get(header)
        if value is not None:
            delay = _reset_delay(value)
            if delay is not None:
                delays.append(delay)
    body = getattr(exc, "body", None)
    body = body.get("error", body) if isinstance(body, dict) else {}
    for field in ("reset_at", "reset_time", "quota_reset_at"):
        value = body.get(field) if isinstance(body, dict) else None
        if value is None:
            value = config.get(field)
        delay = _reset_delay(value, absolute=True)
        if delay is not None:
            delays.append(delay)
            if delay > 0:
                quota_resets.append(delay)
    # Quota failures without an authoritative future reset stay quiet for six
    # hours. A documented future reset (even shorter) can reopen the circuit.
    if quota:
        # A generic 429 Retry-After of a few seconds is a request backoff, not
        # evidence that an exhausted subscription has been replenished.
        return max(delays) if quota_resets else max([default, *delays])
    return max([default, *delays])


def _put_bounded(cache: OrderedDict[str, float], key: str, expires_at: float) -> None:
    now = time.monotonic()
    for old_key, expiry in list(cache.items()):
        if expiry <= now:
            cache.pop(old_key, None)
    cache[key] = max(cache.get(key, 0.0), expires_at)
    cache.move_to_end(key)
    while len(cache) > CIRCUIT_CACHE_MAX:
        cache.popitem(last=False)


def cooldown_slot(config: dict[str, Any], exc: Exception) -> None:
    with _lock:
        _put_bounded(_COOLDOWNS, pool_slot_key(config), time.monotonic() + cooldown_seconds(config, exc))


def recover_slot(config: dict[str, Any]) -> None:
    with _lock:
        key = pool_slot_key(config)
        # A request that started before a concurrent quota failure must not
        # clear the newer circuit just because its response arrives later.
        if _COOLDOWNS.get(key, 0.0) <= time.monotonic():
            _COOLDOWNS.pop(key, None)


def slot_available(config: dict[str, Any]) -> bool:
    with _lock:
        return _COOLDOWNS.get(pool_slot_key(config), 0.0) <= time.monotonic()


def vision_supported(config: dict[str, Any]) -> bool:
    if str(config.get("model") or "").lower() in _TEXT_ONLY_MODELS:
        return False
    with _lock:
        return _VISION_UNSUPPORTED.get(pool_slot_key(config), 0.0) <= time.monotonic()


def payload_has_images(payload: dict[str, Any]) -> bool:
    for message in payload.get("messages") or []:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list) and any(isinstance(part, dict) and part.get("type") in {"image_url", "input_image"} for part in content):
            return True
    return False


def is_vision_capability_error(exc: Exception) -> bool:
    if exception_status(exc) != 400:
        return False
    text = _exception_text(exc)
    return any(marker in text for marker in (
        "only support text", "only supports text", "text-only", "text only", "仅支持文本",
        "不支持图片", "不支持图像", "不支持视觉",
    )) or (any(marker in text for marker in ("image", "vision", "multimodal", "图片", "图像")) and any(marker in text for marker in (
        "not support", "unsupported", "doesn't support", "not capable", "不支持",
    )))


def mark_vision_unsupported(config: dict[str, Any]) -> None:
    with _lock:
        _put_bounded(_VISION_UNSUPPORTED, pool_slot_key(config), time.monotonic() + VISION_CAPABILITY_SECONDS)
