from types import SimpleNamespace

import asyncio
from starlette.requests import Request

from deepfocus_api.research_cache import (
    deep_draft_cache_identity,
    deep_draft_cache_key,
    legacy_deep_draft_cache_key,
)


def test_file_id_key_ignores_display_metadata():
    first = SimpleNamespace(file_id="fid-123", title="原始标题", symbol="NVDA", max_pages=32)
    second = SimpleNamespace(file_id=" fid-123 ", title="改写标题", symbol="AAPL", max_pages=60)
    assert deep_draft_cache_identity(first) == "file:fid-123"
    assert deep_draft_cache_key(first) == deep_draft_cache_key(second)
    assert deep_draft_cache_key(first).startswith("deep-draft:v3:")
    assert legacy_deep_draft_cache_key(first) != legacy_deep_draft_cache_key(second)


def test_source_ids_are_sorted_and_deduplicated():
    first = SimpleNamespace(source_ids=["b", "a", "b"], title="A")
    second = SimpleNamespace(source_ids=["a", "b"], title="B", max_pages=60)
    assert deep_draft_cache_identity(first) == "sources:a\x1fb"
    assert deep_draft_cache_key(first) == deep_draft_cache_key(second)


def test_primary_file_and_extra_sources_form_a_multi_source_identity():
    request = SimpleNamespace(file_id="fid-123", source_ids=["source-b", "source-a"])
    assert deep_draft_cache_identity(request) == "sources:file:fid-123\x1fsource-a\x1fsource-b"


def test_repeated_primary_source_id_does_not_fork_single_file_key():
    first = SimpleNamespace(file_id="fid-123")
    second = SimpleNamespace(file_id="fid-123", source_ids=["fid-123", "file:fid-123"])
    assert deep_draft_cache_identity(second) == deep_draft_cache_identity(first)


def test_urls_drop_signed_query_and_fragments():
    first = SimpleNamespace(pdf_url="HTTPS://PDF.Example.com/reports/abc.pdf?token=one&revision=2#page=1")
    second = SimpleNamespace(pdf_url="https://pdf.example.com/reports/abc.pdf?revision=2&token=two")
    assert deep_draft_cache_identity(first) == "url:https://pdf.example.com/reports/abc.pdf?revision=2"
    assert deep_draft_cache_key(first) == deep_draft_cache_key(second)


def test_workbench_identity_normalises_separators():
    first = SimpleNamespace(workbench_out="downloads\\海外投行报告", workbench_filename="a.pdf")
    second = SimpleNamespace(workbench_out="./downloads/海外投行报告/", workbench_filename="a.pdf", title="不同")
    assert deep_draft_cache_identity(first) == deep_draft_cache_identity(second)


def test_empty_workbench_dir_uses_the_resolver_default():
    explicit_default = SimpleNamespace(workbench_out="downloads/海外投行报告", workbench_filename="a.pdf")
    implicit_default = SimpleNamespace(workbench_out="", workbench_filename="a.pdf")
    assert deep_draft_cache_identity(explicit_default) == deep_draft_cache_identity(implicit_default)


def test_hidden_filename_keeps_leading_dot():
    hidden = SimpleNamespace(workbench_out="downloads", workbench_filename=".report.pdf")
    plain = SimpleNamespace(workbench_out="downloads", workbench_filename="report.pdf")
    assert deep_draft_cache_identity(hidden) != deep_draft_cache_identity(plain)


def test_unsafe_workbench_paths_do_not_get_a_cache_identity():
    traversal = SimpleNamespace(workbench_out="downloads", workbench_filename="../report.pdf")
    absolute = SimpleNamespace(workbench_out="downloads", workbench_filename="/tmp/report.pdf")
    rooted_dir = SimpleNamespace(workbench_out="/downloads", workbench_filename="report.pdf")
    assert deep_draft_cache_identity(traversal) == ""
    assert deep_draft_cache_identity(absolute) == ""
    assert deep_draft_cache_identity(rooted_dir) == ""


def test_empty_source_does_not_create_shared_key():
    assert deep_draft_cache_key(SimpleNamespace(title="无来源", symbol="NVDA")) == ""


def test_handler_reuses_v3_cache_across_display_metadata(monkeypatch):
    """A second caller with the same source must never enter the generator."""
    from deepfocus_api import main
    from deepfocus_api.schemas import ResearchDeepDraftRequest

    cached = {
        "title": "缓存稿",
        "mode": "deep_draft",
        "generated_at": "2026-08-30T04:00:00+00:00",
        "confidence": 0.5,
        "sections": [{"id": "s1", "title": "概览", "paragraphs": ["原文"]}],
        "watchlist": [],
        "risks": [],
        "sources": [],
        "source_coverage": {"pages_read": 1, "chars_read": 2, "source_count": 0},
    }
    calls: list[str] = []

    def get_cache(key: str):
        calls.append(key)
        return cached if key.startswith("deep-draft:v3:") else None

    monkeypatch.setattr(main, "metrics_get_ai_cache", get_cache)
    monkeypatch.setattr(main, "metrics_set_ai_cache", lambda *args: None)
    monkeypatch.setattr(main, "_check_ai_quota", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "metrics_incr", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "metrics_incr_ai_ref", lambda *args, **kwargs: None)

    async def should_not_generate(*args, **kwargs):
        raise AssertionError("cache hit must not resolve sources or invoke the generator")

    monkeypatch.setattr(main, "resolve_source_documents", should_not_generate)

    def req(payload: ResearchDeepDraftRequest):
        http_req = Request({
            "type": "http", "method": "POST", "path": "/api/research/deep-draft",
            "headers": [], "client": ("127.0.0.1", 1234), "query_string": b"",
        })
        return asyncio.run(main.api_research_deep_draft(payload, http_req, None))

    first = req(ResearchDeepDraftRequest(file_id="fid-123", title="标题 A", symbol="NVDA", max_pages=32))
    second = req(ResearchDeepDraftRequest(file_id=" fid-123 ", title="标题 B", symbol="AAPL", max_pages=60))
    assert first.mode == second.mode == "deep_draft"
    v3_keys = [key for key in calls if key.startswith("deep-draft:v3:")]
    assert len(v3_keys) == 2
    assert v3_keys[0] == v3_keys[1]


def test_handler_migrates_a_valid_v2_row_to_the_stable_key(monkeypatch):
    """Existing deep drafts become globally reusable without another model call."""
    from deepfocus_api import main
    from deepfocus_api.schemas import ResearchDeepDraftRequest

    cached = {
        "title": "旧版缓存稿",
        "mode": "deep_draft",
        "generated_at": "2026-08-30T04:00:00+00:00",
        "confidence": 0.5,
        "sections": [{"id": "s1", "title": "概览", "paragraphs": ["原文"]}],
        "watchlist": [],
        "risks": [],
        "sources": [],
        "source_coverage": {"pages_read": 1, "chars_read": 2, "source_count": 0},
    }
    request = ResearchDeepDraftRequest(
        file_id="legacy-fid",
        filename="legacy-report.pdf",
        title="旧版标题",
        symbol="OLD",
        max_pages=32,
    )
    from deepfocus_api.research_cache import deep_draft_cache_key, legacy_deep_draft_cache_key

    primary = deep_draft_cache_key(request)
    legacy = legacy_deep_draft_cache_key(request)
    store: dict[str, object] = {legacy: cached}
    writes: list[tuple[str, object]] = []
    monkeypatch.setattr(main, "metrics_get_ai_cache", lambda key: store.get(key))
    monkeypatch.setattr(main, "metrics_set_ai_cache", lambda key, value: writes.append((key, value)))
    monkeypatch.setattr(main, "_check_ai_quota", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "metrics_incr", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "metrics_incr_ai_ref", lambda *args, **kwargs: None)

    async def should_not_generate(*args, **kwargs):
        raise AssertionError("v2 migration must not invoke source/model generation")

    monkeypatch.setattr(main, "resolve_source_documents", should_not_generate)
    http_req = Request({
        "type": "http", "method": "POST", "path": "/api/research/deep-draft",
        "headers": [], "client": ("127.0.0.1", 1234), "query_string": b"",
    })
    response = asyncio.run(main.api_research_deep_draft(request, http_req, None))
    assert response.mode == "deep_draft"
    assert (primary, cached) in writes
