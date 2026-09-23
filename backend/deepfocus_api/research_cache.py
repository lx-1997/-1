"""Stable cache identities for report-level AI interpretations.

The result of a research interpretation belongs to the source document, not to
the user who requested it or to presentation metadata such as title, symbol,
or page limit.  This module deliberately uses duck typing so it can be used by
the FastAPI handler and by small unit tests without importing the application.
"""
from __future__ import annotations

import hashlib
import posixpath
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


DEEP_DRAFT_CACHE_VERSION = "v3"
_DEFAULT_WORKBENCH_OUT = "downloads/海外投行报告"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _canonical_url(value: Any) -> str:
    """Canonicalise a source URL while dropping volatile query/fragment data.

    Unknown query parameters are retained (and sorted) because a provider may
    use one to select a report revision or page variant.  Known signed-link
    fields are removed so expiring download URLs still share one cache entry.
    """
    raw = _clean(value)
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    # Keep a stable path but avoid duplicate slashes and a trailing slash for
    # non-root paths.  Query strings often carry signed/temporary parameters.
    path = posixpath.normpath(parts.path or "/")
    if path != "/":
        path = path.rstrip("/")
    volatile = {
        "token", "access_token", "auth", "signature", "sig", "sign", "expires",
        "expiry", "expires_at", "x-amz-signature", "x-amz-credential",
        "x-amz-date", "x-amz-expires", "x-goog-signature", "x-goog-credential",
        "x-goog-date", "x-goog-expires",
    }
    query = ""
    try:
        pairs = [
            (key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True)
            if key.lower() not in volatile
        ]
        query = urlencode(sorted(pairs))
    except ValueError:
        query = ""
    return urlunsplit((scheme, netloc, path, query, ""))


def _canonical_path(out: Any, filename: Any) -> str:
    """Return a portable workbench identity (Windows separators normalised)."""
    # ``ResearchDeepDraftRequest`` defaults this directory when omitted, and
    # the resolver applies the same default when a caller sends an empty value.
    # Normalize both forms so clients cannot fork the cache accidentally.
    left_raw = (_clean(out) or _DEFAULT_WORKBENCH_OUT).replace("\\", "/")
    right_raw = _clean(filename).replace("\\", "/")
    if not right_raw:
        return ""
    # Keep unsafe paths out of the cache key.  The resolver performs the real
    # path-traversal check after a miss; returning an empty identity here makes
    # sure a crafted ``../x.pdf`` or absolute path cannot hit a cached
    # ``x.pdf`` before that guard runs.
    def _relative(value: str) -> str:
        if value.startswith("/"):
            return ""
        if len(value) >= 2 and value[1] == ":":
            return ""
        normalized = posixpath.normpath(value)
        if normalized == ".." or normalized.startswith("../"):
            return ""
        return "" if normalized == "." else normalized

    left = _relative(left_raw)
    right = _relative(right_raw)
    if not left or not right:
        return ""
    # Do not resolve against the local filesystem: this is an identity, not a
    # path to read, and callers may run on different hosts.  ``.report.pdf``
    # remains distinct from ``report.pdf`` because only a synthetic ``./``
    # prefix is normalized.
    return posixpath.join(left, right)


def _source_ids(request: Any) -> list[str]:
    raw = getattr(request, "source_ids", None) or []
    if isinstance(raw, (str, bytes)):
        raw = [raw]
    values: set[str] = set()
    for item in raw:
        value = _clean(item)
        if not value:
            continue
        canonical = _canonical_url(value) if value.lower().startswith(("http://", "https://")) else value
        values.add(canonical)
    return sorted(values)


def deep_draft_cache_identity(request: Any) -> str:
    """Build a source-only identity for a deep-draft request.

    Priority is intentional: a stable remote ``file_id`` must not be split by
    a caller changing its display filename, symbol or page limit.  A
    multi-source request uses its sorted, deduplicated source ids.  Local
    workbench files and direct PDF URLs are the remaining source identities.
    Empty-source requests return an empty identity so deterministic fallback
    drafts are not accidentally shared between unrelated titles.
    """
    source_ids = _source_ids(request)
    file_id = _clean(getattr(request, "file_id", ""))
    # A request may contain one primary file plus additional source_ids for a
    # topic synthesis.  Include all explicit sources in that case; otherwise a
    # multi-document draft could incorrectly reuse the single-file result.
    if file_id and source_ids:
        # The client may redundantly echo the primary id in ``source_ids``.
        # Treat that as the single-file case so adding the duplicate field does
        # not fork an otherwise identical cache entry.
        file_aliases = {file_id, "file:" + file_id}
        extras = [sid for sid in source_ids if sid not in file_aliases]
        if not extras:
            return "file:" + file_id
        return "sources:" + "\x1f".join(sorted({"file:" + file_id, *extras}))
    if file_id:
        return "file:" + file_id

    if source_ids:
        return "sources:" + "\x1f".join(source_ids)

    workbench = _canonical_path(
        getattr(request, "workbench_out", ""),
        getattr(request, "workbench_filename", "") or getattr(request, "filename", ""),
    )
    if workbench:
        return "workbench:" + workbench

    pdf_url = _canonical_url(getattr(request, "pdf_url", ""))
    if pdf_url:
        return "url:" + pdf_url
    return ""


def deep_draft_cache_key(request: Any) -> str:
    """Return the versioned persistent cache key, or ``""`` without a source."""
    identity = deep_draft_cache_identity(request)
    if not identity:
        return ""
    digest = hashlib.sha256((DEEP_DRAFT_CACHE_VERSION + "\x00" + identity).encode("utf-8")).hexdigest()
    return f"deep-draft:{DEEP_DRAFT_CACHE_VERSION}:{digest}"


def legacy_deep_draft_cache_key(request: Any) -> str:
    """Reproduce the v2 key so old rows can be migrated to the stable key."""
    title = _clean(getattr(request, "title", "研报深度解读")) or "研报深度解读"
    refs = [
        _clean(getattr(request, "file_id", "")),
        _clean(getattr(request, "workbench_filename", "") or getattr(request, "filename", "")),
        _clean(getattr(request, "workbench_out", "")),
        _clean(getattr(request, "pdf_url", "")),
        *[_clean(item) for item in (getattr(request, "source_ids", None) or [])],
    ]
    material = "\x00".join([
        title,
        _clean(getattr(request, "symbol", "")),
        _clean(getattr(request, "max_pages", "")),
        *refs,
    ])
    return "deep-draft:v2:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


__all__ = [
    "DEEP_DRAFT_CACHE_VERSION",
    "deep_draft_cache_identity",
    "deep_draft_cache_key",
    "legacy_deep_draft_cache_key",
]
