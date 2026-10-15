"""Prewarm the same source-stable deep draft used by interactive readers.

The caller owns scheduling, download budgets and retries.  This module owns
one bounded background attempt and never reserves or completes a user quota.
"""
from __future__ import annotations

import asyncio
import inspect
from typing import Any, Callable, Optional

import async_timeout

from .llm_runtime import llm_deadline
from .research_cache import (
    deep_draft_cache_key,
    is_valid_deep_draft_cache,
    legacy_deep_draft_cache_key,
)


async def _cache_call(function: Callable[..., Any], *args: Any) -> Any:
    """Keep synchronous SQLite work off the event loop; allow async test stores."""
    if inspect.iscoroutinefunction(function):
        return await function(*args)
    result = await asyncio.to_thread(function, *args)
    return await result if inspect.isawaitable(result) else result


async def background_deep_prewarm(
    request: Any,
    *,
    get_cache: Callable[..., Any],
    set_cache: Callable[..., Any],
    limiter: Any,
    resolve: Optional[Callable[..., Any]] = None,
    generate: Optional[Callable[..., Any]] = None,
    timeout_seconds: float = 330.0,
    write_legacy: bool = True,
) -> Optional[dict[str, Any]]:
    """Read or generate one complete draft, returning ``None`` for a fallback.

    Generation runs in the calling task under a non-queueing background lease.
    Timeout and external cancellation unwind and await the resolver/generator
    before releasing that lease; no shielded or detached model task survives.
    Cached results need no lease, PDF download, user quota or model invocation.
    """
    cache_key = deep_draft_cache_key(request)
    if not cache_key:
        raise ValueError("研报预解读需要可识别的原文来源")
    legacy_key = legacy_deep_draft_cache_key(request) if write_legacy else ""

    async def lookup() -> Optional[dict[str, Any]]:
        cached = await _cache_call(get_cache, cache_key)
        if is_valid_deep_draft_cache(cached):
            return cached
        if legacy_key:
            cached = await _cache_call(get_cache, legacy_key)
            if is_valid_deep_draft_cache(cached):
                await _cache_call(set_cache, cache_key, cached)
                return cached
        return None

    async def attempt() -> Optional[dict[str, Any]]:
        cached = await lookup()
        if cached is not None:
            return cached
        async with limiter.background():
            # A foreground reader may have completed during admission.
            cached = await lookup()
            if cached is not None:
                return cached
            source_resolver = resolve
            draft_generator = generate
            if source_resolver is None or draft_generator is None:
                from .research_digest import generate_deep_draft, resolve_source_documents

                source_resolver = source_resolver or resolve_source_documents
                draft_generator = draft_generator or generate_deep_draft
            docs = source_resolver(request)
            if inspect.isawaitable(docs):
                docs = await docs
            result = draft_generator(request, documents=docs, background=True)
            if inspect.isawaitable(result):
                result = await result
            payload = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
            if not is_valid_deep_draft_cache(payload):
                return None
            await _cache_call(set_cache, cache_key, payload)
            if legacy_key and legacy_key != cache_key:
                await _cache_call(set_cache, legacy_key, payload)
            return payload

    # This existing runtime dependency supports Python 3.9 and cancels this
    # task once, allowing async generator/provider cleanup to finish before
    # the timeout escapes.  A cancel scope that repeatedly cancels awaits
    # would interrupt that cleanup and leak model or limiter work.
    with llm_deadline(timeout_seconds):
        async with async_timeout.timeout(timeout_seconds):
            return await attempt()


def deep_to_compact(payload: dict[str, Any]) -> dict[str, Any]:
    """Project real deep-draft content into the existing list/blog card shape.

    Sections produce at most one logic line each.  Empty sections are omitted;
    no minimum line count, invented causal chain or second model call is used.
    """
    if not is_valid_deep_draft_cache(payload):
        raise ValueError("仅完整的深度解读可投影为研报卡片")

    def text(value: Any) -> str:
        return value.strip() if isinstance(value, str) else ""

    summary = text(payload.get("executive_summary")) or text(payload.get("core_conclusion")) or text(payload.get("thesis"))
    lines: list[dict[str, str]] = []
    points: list[str] = []
    for section in payload.get("sections", []):
        if not isinstance(section, dict):
            continue
        section_summary = text(section.get("summary"))
        excerpts = [text(item.get("excerpt")) for item in section.get("evidence", []) if isinstance(item, dict)]
        evidence = "；".join(item for item in excerpts if item)
        if section_summary or evidence:
            lines.append({
                "title": text(section.get("title")),
                "evidence": evidence,
                "chain": section_summary,
                "impact": "",
                "watch": "",
            })
        points.extend(text(item) for item in section.get("bullets", []) if text(item))
    risks: list[str] = []
    for risk in payload.get("risks", []):
        if isinstance(risk, str):
            value = text(risk)
        elif isinstance(risk, dict):
            value = "：".join(part for part in (text(risk.get("title")), text(risk.get("detail"))) if part)
        else:
            value = ""
        if value:
            risks.append(value)
    coverage = payload.get("source_coverage", {})
    pages = int(payload.get("pages_analyzed") or coverage.get("pages_read") or 0)
    return {
        "title": text(payload.get("title")),
        "subject": text(payload.get("subject")) or text(payload.get("symbol")),
        "one_liner": text(payload.get("one_liner")),
        "summary": summary,
        "core_logic": text(payload.get("core_conclusion")) or text(payload.get("thesis")),
        "logic_lines": lines,
        "bullish": points,
        "key_points": points,
        "bearish": risks,
        "risks": risks,
        "instruments": list(payload.get("instruments") or []),
        "confidence": payload.get("confidence", 0.0),
        "pages_analyzed": pages,
        "provider": payload.get("provider", ""),
        "disclaimer": text(payload.get("disclaimer")),
        "source_coverage": dict(coverage),
        "analysis_mode": "deep-draft-projection",
        "rating": None,
        "target_price": None,
    }


__all__ = ["background_deep_prewarm", "deep_to_compact"]
