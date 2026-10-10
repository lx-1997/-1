from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_institution_notes_pages_until_cutoff(monkeypatch) -> None:
    from datetime import datetime, timezone
    from deepfocus_api import agent_tools
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 31, 7, tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(agent_tools, "_dt", FixedDateTime)
    from deepfocus_api import agent_tools

    calls: list[str] = []

    async def fake_stream(**kwargs):
        calls.append(str(kwargs.get("end_time") or ""))
        if len(calls) == 1:
            return {
                "items": [
                    {"title": "最新纪要A", "text": "正文A", "create_time": "2026-08-30T08:00:00+00:00", "tags": []},
                    {"title": "最新纪要B", "text": "正文B", "create_time": "2026-08-29T08:00:00+00:00", "tags": []},
                ],
                "next_before": "2026-08-28T08:00:00+00:00",
                "has_more": True,
            }
        return {
            "items": [
                {"title": "窗口外纪要", "text": "旧正文", "create_time": "2026-08-27T08:00:00+00:00", "tags": []},
            ],
            "next_before": "",
            "has_more": False,
        }

    monkeypatch.setattr("deepfocus_api.zsxq_stream.fetch_stream", fake_stream)
    result = await agent_tools._tool_get_institution_notes(days=2, limit=20)

    assert [item["title"] for item in result["items"]] == ["最新纪要A", "最新纪要B"]
    assert result["coverage"]["scanned_count"] == 3
    assert result["coverage"]["matched_count"] == 2
    assert result["coverage"]["pages"] == 2
    assert result["coverage"]["complete"] is True
    assert calls == ["", "2026-08-28T08:00:00+00:00"]


@pytest.mark.asyncio
async def test_recent_content_digest_has_one_window_and_index(monkeypatch) -> None:
    from deepfocus_api import agent_tools

    async def fake_site(*, topic, **kwargs):
        return {
            "items": [{"title": f"{topic}条目", "snippet": "摘要", "published_at": "2026-08-30T08:00:00+00:00"}],
            "coverage": {"window_days": kwargs["days"], "scanned_count": 1, "matched_count": 1, "complete": True},
        }

    async def fake_wire(**kwargs):
        return {
            "items": [{"title": "投行研报条目", "date": "2026-08-30", "published_at": "2026-08-30T08:00:00+00:00"}],
            "coverage": {"window_days": kwargs["days"], "scanned_count": 1, "matched_count": 1, "complete": True},
        }

    async def fake_notes(**kwargs):
        return {
            "items": [{"title": "机构纪要条目", "summary": "摘要", "date": "2026-08-30T08:00:00+00:00"}],
            "coverage": {"window_days": kwargs["days"], "scanned_count": 1, "matched_count": 1, "complete": True},
        }

    def fake_index(modules, **kwargs):
        assert set(modules) == {
            "get_site_fast_news", "get_site_articles", "get_stock_research", "get_recent_research", "get_institution_notes",
        }
        return {
            "stats": {"raw_items": 5, "deduped_items": 5, "selected_items": 5, "annotation_version": "rules-v2"},
            "coverage": {},
            "digest": [{"category": "机构纪要", "title": "机构纪要条目"}],
        }

    monkeypatch.setattr(agent_tools, "_recent_site_content", fake_site)
    monkeypatch.setattr(agent_tools, "_recent_wire_content", fake_wire)
    monkeypatch.setattr(agent_tools, "_tool_get_institution_notes", fake_notes)
    monkeypatch.setattr("deepfocus_api.research_index.build_research_index", fake_index)

    result = await agent_tools._tool_get_recent_content_digest(days=2)

    assert result["window_days"] == 2
    assert set(result["sources"]) == {"快讯", "文章", "研报", "投行研报", "机构纪要"}
    assert result["coverage_complete"] is True
    assert result["index"]["raw_items"] == 5
    assert result["evidence"][0]["category"] == "机构纪要"


def test_recent_bundle_detection_parses_two_days() -> None:
    from deepfocus_api.llm import _recent_content_bundle_args

    assert _recent_content_bundle_args("总结下最近2天的机构纪要") == {
        "days": 2, "limit": 500, "query": "",
    }
    assert _recent_content_bundle_args("最近一周全部快讯和研报") == {
        "days": 7, "limit": 500, "query": "",
    }
    assert _recent_content_bundle_args("帮我看看贵州茅台估值") is None


@pytest.mark.asyncio
async def test_recent_site_content_reports_access_filtered_source(monkeypatch) -> None:
    from deepfocus_api import agent_tools

    calls: list[bool] = []

    def fake_list(**kwargs):
        calls.append(bool(kwargs.get("exclude_futoucaixin")))
        if kwargs.get("exclude_futoucaixin"):
            return []
        return [SimpleNamespace(created_at="2026-08-30T08:00:00+00:00")]

    monkeypatch.setattr(agent_tools, "list_realtime_messages", fake_list)
    result = await agent_tools._recent_site_content(topic="快讯", days=2, limit=20)

    assert result["items"] == []
    assert result["coverage"]["visibility"] == "access_filtered"
    assert result["coverage"]["raw_available"] is True
    assert result["coverage"]["ingestion_status"] == "access_filtered"
    assert calls == [True, False]


def test_harness_recent_window_detection() -> None:
    from deepfocus_api.research_harness import recent_content_window_days

    assert recent_content_window_days("总结下最近2天的机构纪要") == 2
    assert recent_content_window_days("最近一周全部快讯和研报") == 7
    assert recent_content_window_days("研究贵州茅台估值") is None
