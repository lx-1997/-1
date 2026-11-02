import asyncio
import json
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
import httpx

from deepfocus_api import agent_runtime as runtime
from deepfocus_api import research_workspace as ws
from deepfocus_api import research_workspace_runtime as runner
from deepfocus_api import research_workspace_api as api


@pytest.fixture
def database(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "DB_PATH", tmp_path / "agents.sqlite3")
    runtime.init_task_db()
    return runtime.DB_PATH


def project(owner="alice", **kw):
    return ws.create_project(owner, title="谷歌经营跟踪", symbol="GOOGL", market="US",
                             objective="研究谷歌的业绩和估值", **kw)


def claimed(owner, project_id, key):
    task_id = ws.enqueue_refresh(owner, project_id, key)
    task = runtime._claim_next_task()
    assert task and task.id == task_id
    lease = runtime._worker_leases.pop(task.id)
    return task, lease


def state(value=5):
    return {"get_financials": {"status": "limited", "fields": {"eps": {"status": "limited", "value": value}},
                               "issues": ["publication_date_missing_not_point_in_time"]}}


def test_owner_required_and_projects_are_private(database):
    p = project()
    assert ws.list_projects("bob") == []
    with pytest.raises(LookupError):
        ws.get_project("bob", p["id"])
    with pytest.raises(LookupError):
        ws.enqueue_refresh("bob", p["id"], "guess")
    with pytest.raises(ValueError):
        ws.list_projects("")


def test_queue_dedup_and_mapping_are_in_same_db_transaction(database):
    p = project()
    first = ws.enqueue_refresh("alice", p["id"], "one")
    assert ws.enqueue_refresh("alice", p["id"], "one") == first
    assert ws.enqueue_refresh("alice", p["id"], "another-double-click") == first
    with ws.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM rw_run").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM agent_tasks").fetchone()[0] == 1
    ws.init_store()  # Restart/schema initialization does not create a second run.
    assert ws.enqueue_refresh("alice", p["id"], "one") == first


def test_failure_to_insert_mapping_rolls_back_task(database):
    p = project()
    with ws.connect() as conn:
        conn.execute("CREATE TRIGGER fail_run BEFORE INSERT ON rw_run BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(sqlite3.IntegrityError):
        ws.enqueue_refresh("alice", p["id"], "one")
    with ws.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM agent_tasks").fetchone()[0] == 0


def test_unchanged_checks_do_not_duplicate_revisions(database):
    p = project(notes="私人观点，不是证据", hypotheses=["广告利润率改善"])
    task, lease = claimed("alice", p["id"], "first")
    assert ws.finalize_refresh("alice", p["id"], task.id, lease, 1, state(), {"analysis_status": "generated", "summary": "盈利仍待持续核验"})
    old = ws.get_project("alice", p["id"])["revisions"][0]
    task, lease = claimed("alice", p["id"], "second")
    assert ws.finalize_refresh("alice", p["id"], task.id, lease, 1, state(), {"summary": "unchanged"})
    p = ws.get_project("alice", p["id"])
    assert len(p["revisions"]) == 1
    assert p["revisions"][0] == old
    assert p["last_observation"]["changed"] == 0
    assert old["payload"]["project_context"]["notes"] == "私人观点，不是证据"
    task, lease = claimed("alice", p["id"], "third")
    assert ws.finalize_refresh("alice", p["id"], task.id, lease, 1, state(6), {"summary": "changed"})
    p = ws.get_project("alice", p["id"])
    assert len(p["revisions"]) == 2
    assert p["revisions"][0]["payload"]["changed_modules"] == ["get_financials"]
    assert p["revisions"][1] == old


@pytest.mark.parametrize("cause", ["cancelled", "wrong_lease", "updated_project", "wrong_owner"])
def test_stale_or_cancelled_worker_cannot_publish(database, cause):
    p = project()
    task, lease = claimed("alice", p["id"], "one")
    owner = "alice"
    if cause == "cancelled":
        runtime.cancel_investment_task(task.id, owner_user_id="alice")
    elif cause == "wrong_lease":
        lease = "stolen"
    elif cause == "wrong_owner":
        owner = "bob"
    else:
        ws.update_project("alice", p["id"], expected_version=1, notes="new", hypotheses=[], interval_seconds=0)
    assert not ws.finalize_refresh(owner, p["id"], task.id, lease, 1, state(), {"summary": "must not publish"})
    assert ws.get_project("alice", p["id"])["revisions"] == []


def test_scheduled_refresh_survives_restart_and_pause(database, monkeypatch):
    p = project(interval_seconds=86400)
    monkeypatch.setattr(ws.time, "time", lambda: p["next_due"] + 1)
    assert ws.enqueue_due() == 1
    assert ws.enqueue_due() == 0
    ws.init_store()
    assert len(ws.get_project("alice", p["id"])["runs"]) == 1
    ws.update_project("alice", p["id"], expected_version=1, notes="", hypotheses=[], interval_seconds=0)
    monkeypatch.setattr(ws.time, "time", lambda: p["next_due"] + 3 * 86400)
    assert ws.enqueue_due() == 0


def test_edit_requires_current_version(database):
    p = project()
    ws.update_project("alice", p["id"], expected_version=1, notes="new", hypotheses=[], interval_seconds=0)
    with pytest.raises(ValueError):
        ws.update_project("alice", p["id"], expected_version=1, notes="stale", hypotheses=[], interval_seconds=0)


def test_expired_lease_cannot_publish_even_before_another_worker_claims(database):
    p = project()
    task, lease = claimed("alice", p["id"], "one")
    with ws.transaction() as conn:
        conn.execute("UPDATE agent_tasks SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (task.id,))
    assert not ws.finalize_refresh("alice", p["id"], task.id, lease, 1, state(), {"summary": "must not publish"})


def test_exhausted_owner_budget_does_not_starve_other_scheduled_projects(database):
    first = project(interval_seconds=86400)
    second = project("bob", interval_seconds=86400)
    with ws.transaction() as conn:
        conn.executemany("INSERT INTO rw_run VALUES(?,?,?,?,?,?)", [(f"budget-{i}", first["id"], "alice", str(i), 1, ws.time.time()) for i in range(100)])
        conn.execute("UPDATE rw_project SET next_due=1 WHERE id=?", (first["id"],))
        conn.execute("UPDATE rw_project SET next_due=2 WHERE id=?", (second["id"],))
    assert ws.enqueue_due() == 0
    assert ws.enqueue_due() == 1
    assert len(ws.get_project("bob", second["id"])["runs"]) == 1


def test_refresh_key_is_bound_to_the_original_followup(database):
    p = project()
    task = ws.enqueue_refresh("alice", p["id"], "same", followup_question="核对盈利质量")
    assert ws.enqueue_refresh("alice", p["id"], "same", followup_question="核对盈利质量") == task
    with pytest.raises(ValueError, match="另一个问题"):
        ws.enqueue_refresh("alice", p["id"], "same", followup_question="核对估值")


def test_cold_backup_retains_revisions_and_owner_checks(database, tmp_path, monkeypatch):
    p = project()
    task, lease = claimed("alice", p["id"], "one")
    assert ws.finalize_refresh("alice", p["id"], task.id, lease, 1, state(), {"summary": "归档"})
    original = ws.get_project("alice", p["id"])
    target = tmp_path / "cold.sqlite3"
    source = ws.connect(); backup = sqlite3.connect(target)
    try:
        source.backup(backup)
    finally:
        source.close(); backup.close()
    monkeypatch.setattr(runtime, "DB_PATH", target)
    assert ws.get_project("alice", p["id"]) == original
    with pytest.raises(LookupError):
        ws.get_project("bob", p["id"])


@pytest.mark.asyncio
async def test_project_adapter_skips_model_when_facts_unchanged(database, monkeypatch):
    p = project()
    packet = {"evidence_audit": {"checks": [{"tool": "get_financials", **state()["get_financials"]}]}}
    async def build(*a, **kw):
        return packet
    monkeypatch.setattr(runner, "build_research_packet", build)
    calls = []
    class Model:
        provider = "test"
        async def complete_json(self, *a, **kw):
            calls.append(1)
            return {"summary": "盈利仍待核验", "observations": [{"tool": "get_financials", "field": "eps", "interpretation": "该期间盈利为正，期间口径仍需核验"}]}
    for key in ("first", "second"):
        task, lease = claimed("alice", p["id"], key)
        await runner.process_project_task(task, lease, lambda *a: None, Model)
    assert calls == [1]
    assert len(ws.get_project("alice", p["id"])["revisions"]) == 1


@pytest.mark.asyncio
async def test_generic_task_cannot_impersonate_project_job(database):
    p = project()
    task = SimpleNamespace(id="fake", owner_user_id="alice", input={"engine_config": {"research_project_id": p["id"], "config_version": 1}})
    with pytest.raises(ValueError, match="不匹配"):
        await runner.process_project_task(task, "fake", lambda *a: None, lambda: pytest.fail("no model call"))


def test_model_observations_cannot_reference_missing_fields_or_invent_numeric_narrative():
    for row in [
        {"tool": "get_valuation", "field": "pe_ratio", "interpretation": "估值偏低"},
        {"tool": "get_financials", "field": "eps", "interpretation": "盈利增长50%"},
    ]:
        with pytest.raises(ValueError):
            runner.validate_report({"summary": "待核验", "observations": [row]}, state())


@pytest.mark.asyncio
async def test_http_authentication_and_cross_owner_denial(database):
    app = FastAPI()
    app.include_router(api.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/research/projects")).status_code == 401
        app.dependency_overrides[api.require_current_user] = lambda: {"sub": "alice"}
        response = await client.post("/api/research/projects", json={"title": "test", "symbol": "GOOGL", "market": "US", "objective": "研究估值"})
        assert response.status_code == 200
        key = response.json()["id"]
        app.dependency_overrides[api.require_current_user] = lambda: {"sub": "bob", "role": "admin"}
        assert (await client.get(f"/api/research/projects/{key}")).status_code == 404
        assert (await client.post(f"/api/research/projects/{key}/refresh", json={"request_key": "guess"})).status_code == 404
        assert (await client.get("/api/research/projects")).json() == {"projects": []}
