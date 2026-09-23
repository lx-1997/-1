from __future__ import annotations

from deepfocus_api import recall_ingest as ingest


def test_research_normalizer_preserves_stable_dedupe_and_topic():
    result = ingest.normalize_research_item(
        {
            "id": "file-1",
            "file_id": "file-1",
            "title": "某公司目标价上调",
            "org": "海外投行",
            "date": "2026-08-26",
            "hashtag": "#海外投行报告#",
            "instruments": ["600519", "贵州茅台"],
            "preview_url": "/api/research/wire-file?file_id=file-1",
        }
    )
    assert result is not None
    external_id, request = result
    assert external_id == "file-1"
    assert request.topic == "研报"
    assert request.severity == "success"
    assert request.metadata["native_dedupe_id"] == "report:file-1"
    assert request.metadata["symbols"] == ["600519", "贵州茅台"]


def test_institution_normalizer_truncates_body_and_marks_digest():
    external_id, request = ingest.normalize_institution_note(
        {"id": "topic-9", "title": "调研纪要", "text": "风险提示 " * 500, "digested": True}
    )
    assert external_id == "topic-9"
    assert request.topic == "机构纪要"
    assert request.severity == "warning"
    assert len(request.content) <= ingest.MAX_CONTENT_CHARS
    assert request.metadata["native_dedupe_id"] == "zsxq:topic-9"


def test_ingest_is_baselined_then_notifies_only_new_items(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "DB_PATH", tmp_path / "ingest.sqlite3")
    created: list[str] = []

    def fake_create(request):
        created.append(request.metadata["external_id"])
        return type("Message", (), {"id": f"msg-{len(created)}"})()

    baseline = ingest.ingest_items(
        "research_wire",
        [{"id": "old", "title": "旧研报"}],
        ingest.normalize_research_item,
        create_fn=fake_create,
    )
    assert baseline["baseline"] == 1 and baseline["notified"] == 0
    assert created == []

    second = ingest.ingest_items(
        "research_wire",
        [{"id": "new", "title": "新研报"}, {"id": "old", "title": "旧研报"}],
        ingest.normalize_research_item,
        create_fn=fake_create,
    )
    assert second["new"] == 1 and second["notified"] == 1
    assert created == ["new"]

    duplicate = ingest.ingest_items(
        "research_wire", [{"id": "new", "title": "新研报"}], ingest.normalize_research_item, create_fn=fake_create
    )
    assert duplicate["new"] == 0
    assert created == ["new"]

