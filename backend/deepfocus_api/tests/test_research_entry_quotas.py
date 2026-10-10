import asyncio
import json
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from deepfocus_api import cost_quotas as quotas, main
from deepfocus_api.schemas import DulusRoundtableRequest


@pytest.fixture(autouse=True)
def isolated_quota(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPFOCUS_QUOTA_DB_PATH", str(tmp_path / "quotas.sqlite3"))
    monkeypatch.setattr(main, "metrics_get_daily", lambda *args: 0)
    monkeypatch.setattr(main, "metrics_incr", lambda *args: None)
    monkeypatch.setattr(main, "ifind_enhance_enabled", lambda *args: False)
    if hasattr(main, "_roundtable_trace"):
        monkeypatch.setattr(main, "_roundtable_trace", lambda result: [])


def request():
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("127.0.0.1", 1)}, receive)


@pytest.mark.asyncio
@pytest.mark.parametrize("degraded", [False, True])
async def test_research_loop_charges_only_success_and_closes_execution(monkeypatch, degraded):
    closed = asyncio.Event()
    async def run(*args):
        try:
            yield main._sse_frame("research_synthesize", {"executive_summary": "test conclusion", "degraded": degraded})
            yield main._sse_frame("research_done", {})
        finally:
            closed.set()
    monkeypatch.setattr(main, "run_agent_research_loop", run)
    response = await main.research_loop_stream(request(), "TEST", "analysis", None)
    assert len([item async for item in response.body_iterator]) == 2
    assert closed.is_set()
    assert quotas.used("q:dulusdeep:anon:127.0.0.1") == (0 if degraded else 1)


@pytest.mark.asyncio
async def test_research_loop_reserves_before_model_and_releases_on_disconnect(monkeypatch):
    calls = []
    closed = asyncio.Event()
    async def run(*args):
        calls.append(True)
        try:
            yield main._sse_frame("status", {})
            await asyncio.Event().wait()
        finally:
            closed.set()
    monkeypatch.setattr(main, "run_agent_research_loop", run)
    response = await main.research_loop_stream(request(), "TEST", "analysis", None)
    with pytest.raises(main.HTTPException) as error:
        await main.research_loop_stream(request(), "TEST", "another", None)
    assert error.value.status_code == 403
    assert not calls
    await response.body_iterator.__anext__()
    await response.body_iterator.aclose()
    assert closed.is_set() and quotas.used("q:dulusdeep:anon:127.0.0.1") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("clarification", [False, True])
async def test_debate_roundtable_uses_quick_quota_and_releases_late_clarification(monkeypatch, clarification):
    result = SimpleNamespace(decision="research_more" if clarification else "hold", turns=[] if clarification else [1], tool_traces=[], synthesis="test answer", quota_left=None)
    async def run(*args, **kwargs):
        return SimpleNamespace(ok=True, raw=result)
    monkeypatch.setattr(main.core_agent, "run_adapter", run)
    monkeypatch.setattr(main, "_attach_core_agent_metadata", lambda value, core: value)
    await main.dulus_roundtable(DulusRoundtableRequest(objective="解释某家公司的财报", mode="debate"), request(), None)
    assert quotas.used("q:agentqa:anon:127.0.0.1") == (0 if clarification else 1)
