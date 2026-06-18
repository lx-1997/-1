from __future__ import annotations

from deepfocus_api.agent_events import _emitted_ids_through, _sse_event, task_agent_events
from deepfocus_api.schemas import InvestmentTaskRecord


def _task() -> InvestmentTaskRecord:
    return InvestmentTaskRecord(
        id="task-1",
        title="NVDA research",
        symbol="NVDA",
        asset_name="NVIDIA",
        task_type="investment_research",
        engine="deepfocus",
        status="running",
        priority=3,
        assigned_agent="ResearchAgent",
        progress=55,
        created_at="2026-05-16T00:00:00+00:00",
        updated_at="2026-05-16T00:02:00+00:00",
        input={"symbol": "NVDA"},
        logs=[
            {
                "timestamp": "2026-05-16T00:01:00+00:00",
                "agent": "EvidenceAgent",
                "message": "已命中 3 条证据。",
                "progress": 18,
            },
            {
                "timestamp": "2026-05-16T00:02:00+00:00",
                "agent": "ResearchAgent",
                "message": "形成基准情景。",
                "progress": 55,
            },
        ],
    )


def test_task_agent_events_preserve_log_progress() -> None:
    events = task_agent_events(_task())
    evidence_event = next(event for event in events if event.payload.get("raw_agent") == "EvidenceAgent")
    research_event = next(event for event in events if event.payload.get("raw_agent") == "ResearchAgent")

    assert events[0].agent == "Analyst"
    assert evidence_event.agent == "Evidence"
    assert research_event.agent == "Analyst"
    assert evidence_event.progress == 18
    assert evidence_event.payload["log_progress"] == 18
    assert research_event.progress == 55


def test_sse_event_includes_resumable_id_and_retry() -> None:
    chunk = _sse_event("reasoning_delta", {"message": "ok"}, event_id="event-1", retry_ms=2000)

    assert chunk.startswith("id: event-1\nretry: 2000\nevent: reasoning_delta\n")
    assert 'data: {"message": "ok"}' in chunk
    assert chunk.endswith("\n\n")


def test_emitted_ids_through_marks_events_before_resume_point() -> None:
    events = task_agent_events(_task())
    target_id = events[1].id

    emitted = _emitted_ids_through(events, target_id)

    assert emitted == {events[0].id, events[1].id}
