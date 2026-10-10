"""海关 AI 创建的后台任务与同步等待必须使用同一真实账号归属。"""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from deepfocus_api import agent_runtime, auth, main, storage
from deepfocus_api.ownership import bind_owner
from deepfocus_api.schemas import CustomsTradeAnalysisRequest, InvestmentTaskCreateRequest


@pytest.fixture(autouse=True)
def stores(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_runtime, "DB_PATH", tmp_path / "tasks.sqlite3")
    monkeypatch.setenv("DEEPFOCUS_DATABASE_URL", f"sqlite:///{tmp_path / 'auth.sqlite3'}")
    monkeypatch.setenv("DEEPFOCUS_JWT_SECRET", "fake-customs-test-key")
    monkeypatch.setenv("DEEPFOCUS_AUTH_REQUIRED", "true")
    storage.reset_engine_for_tests()
    storage.Base.metadata.create_all(storage.get_engine())
    monkeypatch.setattr(main, "is_worker_running", lambda: True)
    yield
    storage.reset_engine_for_tests()


def _completed(task):
    agent_runtime._update_task(task.id, status="completed", result_json=json.dumps({"investor_summary": "隔离测试已完成"}))


def test_real_http_customs_task_and_poll_are_owned_by_current_jwt(monkeypatch):
    alice = auth.create_user("alice@customs.example", "alice", "password1", role="analyst")
    bob = auth.create_user("bob@customs.example", "bob", "password1", role="analyst")
    headers = lambda user: {"Authorization": f"Bearer {auth.create_access_token(user, auth.rotate_session(user.id))}"}
    a_headers, b_headers = headers(alice), headers(bob)
    original_wait = main._wait_for_customs_agent_task
    seen = []

    async def wait(task_id, *, timeout_seconds, owner_user_id=None):
        task = agent_runtime.get_investment_task(task_id)
        assert task.owner_user_id == owner_user_id == alice.id
        seen.append(task_id)
        _completed(task)
        return await original_wait(task_id, timeout_seconds=timeout_seconds, owner_user_id=owner_user_id)

    monkeypatch.setattr(main, "_wait_for_customs_agent_task", wait)
    client = TestClient(main.app)  # No lifespan, worker or external AI calls.
    response = client.post("/api/customs-trade/ai-analysis", headers=a_headers, json={"focus": "出口"})
    assert response.status_code == 200, response.text
    assert response.json()["summary"] == "隔离测试已完成"
    assert len(seen) == 1
    assert [item["id"] for item in client.get("/api/agents/tasks", headers=a_headers).json()["tasks"]] == seen
    assert client.get("/api/agents/tasks", headers=b_headers).json()["tasks"] == []
    assert client.get(f"/api/agents/tasks/{seen[0]}", headers=b_headers).status_code == 404


def test_anonymous_internal_customs_task_stays_unowned(monkeypatch):
    original_wait = main._wait_for_customs_agent_task
    seen = []

    async def wait(task_id, *, timeout_seconds, owner_user_id=None):
        task = agent_runtime.get_investment_task(task_id)
        assert task.owner_user_id is None and owner_user_id is None
        seen.append(task_id)
        _completed(task)
        return await original_wait(task_id, timeout_seconds=timeout_seconds, owner_user_id=owner_user_id)

    monkeypatch.setattr(main, "_wait_for_customs_agent_task", wait)
    with bind_owner(None):
        response = asyncio.run(main.customs_trade_ai_analysis(CustomsTradeAnalysisRequest(focus="出口")))
    assert response.summary == "隔离测试已完成"
    assert len(seen) == 1
    assert agent_runtime.list_investment_tasks(owner_user_id="alice") == []


def test_customs_waiter_rejects_foreign_and_legacy_owned_mismatch():
    task = agent_runtime.create_investment_task(InvestmentTaskCreateRequest(title="private customs"), owner_user_id="alice")
    legacy = agent_runtime.create_investment_task(InvestmentTaskCreateRequest(title="anonymous customs"))
    _completed(task)
    _completed(legacy)
    for task_id, owner in ((task.id, "bob"), (task.id, None), (legacy.id, "alice")):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(main._wait_for_customs_agent_task(task_id, timeout_seconds=1, owner_user_id=owner))
        assert exc.value.status_code == 404
    assert asyncio.run(main._wait_for_customs_agent_task(task.id, timeout_seconds=1, owner_user_id="alice")).id == task.id
    assert asyncio.run(main._wait_for_customs_agent_task(legacy.id, timeout_seconds=1)).id == legacy.id
