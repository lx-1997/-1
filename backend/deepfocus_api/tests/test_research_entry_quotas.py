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
    monkeypatch.setattr(main, "create_dulus_memory", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "_upgrade_hint", lambda: "test membership required")
    if hasattr(main, "_roundtable_trace"):
        monkeypatch.setattr(main, "_roundtable_trace", lambda result: [])


def request():
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("127.0.0.1", 1)}, receive)


@pytest.fixture
def member(monkeypatch):
    user = {"sub": "entry-member", "role": "analyst"}
    monkeypatch.setattr(main, "get_user_out_by_id", lambda uid: SimpleNamespace(
        membership={"tier": "premium"}) if uid == user["sub"] else None)
    return user


@pytest.fixture
def finite_member_quota(monkeypatch, member):
    """Exercise lease integration AFTER real membership admission.

    Finite limits are injected only at this seam; production members currently
    have unlimited access. Never restore the obsolete anonymous-free policy.
    """
    calls = []
    for name, label in [("_check_dulus_deep_quota", "deep"), ("_check_agent_quota", "quick")]:
        original = getattr(main, name)
        def gate(user, req, original=original, label=label):
            original(user, req)
            calls.append(label)
            try:
                return quotas.reserve("fixture:"+label+":"+user["sub"], 1)
            except quotas.QuotaExceeded:
                raise main.HTTPException(429, "test finite quota exhausted")
        monkeypatch.setattr(main, name, gate)
    return member, calls


@pytest.mark.asyncio
@pytest.mark.parametrize("degraded", [False, True])
async def test_research_loop_charges_only_success_and_closes_execution(monkeypatch, degraded, finite_member_quota):
    user, calls = finite_member_quota
    closed = asyncio.Event()
    async def run(*args):
        try:
            yield main._sse_frame("research_synthesize", {"executive_summary": "test conclusion", "degraded": degraded})
            yield main._sse_frame("research_done", {})
        finally:
            closed.set()
    monkeypatch.setattr(main, "run_agent_research_loop", run)
    response = await main.research_loop_stream(request(), "TEST", "analysis", user)
    assert len([item async for item in response.body_iterator]) == 2
    assert closed.is_set()
    assert calls == ["deep"]
    assert quotas.used("fixture:deep:entry-member") == (0 if degraded else 1)


@pytest.mark.asyncio
async def test_research_loop_reserves_before_model_and_releases_on_disconnect(monkeypatch, finite_member_quota):
    user, _ = finite_member_quota
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
    response = await main.research_loop_stream(request(), "TEST", "analysis", user)
    with pytest.raises(main.HTTPException) as error:
        await main.research_loop_stream(request(), "TEST", "another", user)
    assert error.value.status_code == 429
    assert not calls
    await response.body_iterator.__anext__()
    await response.body_iterator.aclose()
    assert closed.is_set() and quotas.used("fixture:deep:entry-member") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("clarification", [False, True])
async def test_debate_roundtable_uses_quick_quota_and_releases_late_clarification(monkeypatch, clarification, finite_member_quota):
    user, calls = finite_member_quota
    result = SimpleNamespace(decision="research_more" if clarification else "hold", turns=[] if clarification else [1], tool_traces=[], synthesis="test answer", quota_left=None)
    async def run(*args, **kwargs):
        return SimpleNamespace(ok=True, raw=result)
    monkeypatch.setattr(main.core_agent, "run_adapter", run)
    monkeypatch.setattr(main, "_attach_core_agent_metadata", lambda value, core: value)
    await main.dulus_roundtable(DulusRoundtableRequest(objective="解释某家公司的财报", mode="debate"), request(), user)
    assert calls == ["quick"]
    assert quotas.used("fixture:quick:entry-member") == (0 if clarification else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["loop", "debate"])
@pytest.mark.parametrize("user,status", [(None, 403), ({"sub":"ordinary", "role":"analyst"}, 402)])
async def test_real_membership_denial_precedes_model_execution(monkeypatch, member, route, user, status):
    calls = []
    async def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError("denied caller must not execute a model")
    monkeypatch.setattr(main, "run_agent_research_loop", forbidden)
    monkeypatch.setattr(main.core_agent, "run_adapter", forbidden)
    with pytest.raises(main.HTTPException) as error:
        if route == "loop": await main.research_loop_stream(request(), "TEST", "analysis", user)
        else: await main.dulus_roundtable(DulusRoundtableRequest(objective="解释某家公司的财报", mode="debate"), request(), user)
    assert error.value.status_code == status
    assert not calls


@pytest.mark.parametrize("gate", ["_check_dulus_deep_quota", "_check_agent_quota"])
@pytest.mark.parametrize("tier", ["premium", "lifetime", "admin"])
def test_real_member_or_admin_has_unlimited_admission(monkeypatch, gate, tier):
    monkeypatch.setattr(main, "get_user_out_by_id", lambda uid: SimpleNamespace(membership={"tier":tier}))
    user = {"sub":"member", "role":"admin" if tier=="admin" else "analyst"}
    assert getattr(main, gate)(user, request()) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["loop", "debate"])
async def test_native_premium_admission_can_complete_twice_without_finite_seam(monkeypatch, member, route):
    calls = []; closed = []
    async def run_loop(*args):
        calls.append(route)
        try:
            yield main._sse_frame("research_synthesize", {"executive_summary":"answer"})
            yield main._sse_frame("research_done", {})
        finally: closed.append(True)
    async def run_debate(*args, **kwargs):
        calls.append(route)
        return SimpleNamespace(raw=SimpleNamespace(decision="hold",turns=[1],tool_traces=[],synthesis="answer",quota_left=None))
    monkeypatch.setattr(main, "run_agent_research_loop", run_loop)
    monkeypatch.setattr(main.core_agent, "run_adapter", run_debate)
    monkeypatch.setattr(main, "_attach_core_agent_metadata", lambda value, core:value)
    for _ in range(2):
        if route == "loop":
            response = await main.research_loop_stream(request(), "TEST", "analysis", member)
            assert len([item async for item in response.body_iterator]) == 2
        else:
            response = await main.dulus_roundtable(DulusRoundtableRequest(objective="解释某家公司的财报",mode="debate"),request(),member)
            assert response.synthesis == "answer" and response.quota_left is None
    assert calls == [route, route]
    assert len(closed) == (2 if route == "loop" else 0)
    assert quotas.used("fixture:deep:entry-member") == quotas.used("fixture:quick:entry-member") == 0
