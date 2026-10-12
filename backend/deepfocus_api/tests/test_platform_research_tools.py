"""Seven research tools through the assistant, original API, and real job manager.

Only market-data resolution and the isolated computation-worker boundary are
scripted. The job queue, ownership, deduplication, cancellation, capabilities,
JWT validation, and assistant confirmation store use their actual code.
"""
from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace

import httpx
import pytest

from deepfocus_api import research_data, research_jobs, research_tools_api
from test_platform_assistant_business import business_app, _answer, _calls, _tool


TOOLS = ("performance", "portfolio", "financials", "institutions", "funds", "bonds", "factors")


@pytest.fixture
def research_case(business_app, monkeypatch):
    case = business_app
    manager = research_jobs.ResearchJobs()
    monkeypatch.setattr(research_jobs, "manager", manager)
    monkeypatch.setattr(research_tools_api, "_catalog_cache", None)
    monkeypatch.setattr(research_tools_api, "_catalog_time", 0.0)
    monkeypatch.setattr(research_tools_api, "_catalog_lock", None)
    case.app.include_router(research_tools_api.router)
    from deepfocus_api.trade_statement_api import router as statement_router
    case.app.include_router(statement_router)
    case.app.openapi_schema = None
    worker_calls, data_calls = [], []

    async def data_boundary(tool, data):
        data_calls.append((tool, copy.deepcopy(data)))
        return data, [{"name": "isolated-data-boundary"}], ["isolated-test-data"]

    async def worker_boundary(payload, **kwargs):
        worker_calls.append(copy.deepcopy(payload))
        await asyncio.sleep(0)
        if payload.get("operation") == "catalog":
            return {"tools": [{"tool": tool, "available": True} for tool in TOOLS]}
        return {"tool": payload["tool"], "title": "隔离计算边界回执", "summary": []}

    monkeypatch.setattr(research_data, "resolve_platform_history", data_boundary)
    monkeypatch.setattr(research_jobs, "run_worker", worker_boundary)
    return SimpleNamespace(case=case, manager=manager, worker_calls=worker_calls, data_calls=data_calls)


async def _chat(case, http, message, *, owner="alice", request_id="assistant-research"):
    response = await http.post("/api/agents/chat", json={"message": message, "request_id": request_id}, headers=case.headers[owner])
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("tool", TOOLS)
def test_each_research_tool_creates_one_owned_job_and_worker_despite_model_and_http_replay(research_case, tool):
    scenario = research_case
    case = scenario.case
    body = {"tool": tool, "input": {"test_marker": tool}, "request_id": f"compute-{tool}"}
    llm = case.script(_calls(_tool("post:/api/research-tools/jobs", body=body)),
                      _calls(_tool("post:/api/research-tools/jobs", body=body)), _answer())

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=case.app), base_url="http://isolated.test") as http:
            message = f"创建一个 {tool} 投研工具研究任务"
            created = await _chat(case, http, message)
            assert created["ok"] is True
            assert len(created["platform_actions"]) == 1
            receipt = created["platform_actions"][0]
            assert receipt["status"] == "completed"
            identifier = receipt["result"]["id"]
            job = scenario.manager.jobs[identifier]
            assert job.owner == case.users["alice"].id
            assert job.tool == tool
            await asyncio.wait_for(job.task, timeout=3)
            assert job.state == "completed"
            assert len(scenario.manager.jobs) == 1
            assert len(scenario.worker_calls) == len(scenario.data_calls) == 1
            assert scenario.worker_calls[0] == {"tool": tool, "input": body["input"]}
            calls_before_replay = len(llm.requests)
            assert await _chat(case, http, message) == created
            assert len(llm.requests) == calls_before_replay
            # The business API independently deduplicates the computation ID.
            replay = await http.post("/api/research-tools/jobs", json=body, headers=case.headers["alice"])
            assert replay.status_code == 202
            assert replay.json()["id"] == identifier
            assert len(scenario.worker_calls) == 1

            private_read = await http.get(f"/api/research-tools/jobs/{identifier}", headers=case.headers["alice"])
            assert private_read.status_code == 200
            assert private_read.json()["result"]["tool"] == tool
            assert "owner" not in private_read.json() and "digest" not in private_read.json()
            assert (await http.get(f"/api/research-tools/jobs/{identifier}", headers=case.headers["bob"])).status_code == 404

            read_llm = case.script(_calls(_tool("get:/api/research-tools/jobs/{identifier}", path_params={"identifier": identifier})), _answer())
            read_result = await _chat(case, http, "查看我的投研工具任务结果", request_id="read-job")
            assert read_result["ok"] is True
            actual_receipt = json.loads(read_llm.requests[1]["messages"][-1]["content"])
            assert actual_receipt["data"]["result"]["tool"] == tool
            assert actual_receipt["data"]["id"] == identifier
            case.script(_calls(_tool("get:/api/research-tools/jobs/{identifier}", path_params={"identifier": identifier})), _answer())
            forbidden = await _chat(case, http, "查看我的投研工具任务结果", owner="bob", request_id="read-alice-job")
            assert forbidden["ok"] is False
            assert forbidden["tool_trace"][0]["ok"] is False
            assert len(scenario.worker_calls) == 1
        await scenario.manager.shutdown()

    asyncio.run(exercise())


def test_running_research_cancel_requires_owner_confirmation_and_cached_replay(research_case, monkeypatch):
    scenario = research_case
    case = scenario.case

    async def exercise():
        entered = asyncio.Event()
        released = asyncio.Event()
        cleaned = []

        async def held_worker(payload, **kwargs):
            scenario.worker_calls.append(copy.deepcopy(payload))
            entered.set()
            try:
                await released.wait()
                return {"tool": payload["tool"]}
            finally:
                cleaned.append(payload["tool"])

        monkeypatch.setattr(research_jobs, "run_worker", held_worker)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=case.app), base_url="http://isolated.test") as http:
            case.script(_calls(_tool("post:/api/research-tools/jobs", body={"tool": "funds", "input": {}, "request_id": "compute-cancel"})), _answer())
            created = await _chat(case, http, "创建一个基金穿透投研工具任务", request_id="create-running")
            identifier = created["platform_actions"][0]["result"]["id"]
            await asyncio.wait_for(entered.wait(), timeout=3)
            job = scenario.manager.jobs[identifier]
            assert job.state == "running"
            case.script(_calls(_tool("delete:/api/research-tools/jobs/{identifier}", path_params={"identifier": identifier})), _answer())
            proposed = await _chat(case, http, "取消我的基金穿透投研工具任务", request_id="propose-cancel")
            action = proposed["platform_actions"][0]
            assert action["status"] == "needs_confirmation"
            assert job.state == "running" and cleaned == []
            confirmation = {"confirmation_token": action["confirmation_token"]}
            wrong_owner = await http.post("/api/agents/platform-actions/confirm", json=confirmation, headers=case.headers["bob"])
            assert wrong_owner.status_code == 409
            assert job.state == "running" and cleaned == []
            confirmed = await http.post("/api/agents/platform-actions/confirm", json=confirmation, headers=case.headers["alice"])
            assert confirmed.status_code == 200
            assert confirmed.json()["ok"] is True
            assert confirmed.json()["platform_actions"][0]["result"]["state"] == "cancelled"
            assert job.state == "cancelled" and job.task.done()
            assert cleaned == ["funds"]
            stopped_at = job.updated_at
            replay = await http.post("/api/agents/platform-actions/confirm", json=confirmation, headers=case.headers["alice"])
            assert replay.json() == confirmed.json()
            assert job.updated_at == stopped_at
            assert len(scenario.worker_calls) == 1 and cleaned == ["funds"]
        await scenario.manager.shutdown()

    asyncio.run(exercise())


def test_changing_only_model_generated_compute_request_id_cannot_duplicate_one_requested_job(research_case):
    scenario = research_case
    case = scenario.case
    first_body = {"tool": "funds", "input": {"test_marker": "same-computation"}, "request_id": "model-generated-first"}
    second_body = {**first_body, "request_id": "model-generated-second"}
    llm = case.script(_calls(_tool("post:/api/research-tools/jobs", body=first_body)),
                      _calls(_tool("post:/api/research-tools/jobs", body=second_body)), _answer())
    scripted_completion = llm._completion

    async def completion_after_worker(payload, timeout_seconds):
        # A real model round can outlast a quick computation; expose that
        # schedule without waiting for an external model or analysis library.
        if len(llm.requests) == 1 and scenario.manager.jobs:
            await asyncio.wait_for(next(iter(scenario.manager.jobs.values())).task, timeout=3)
        return await scripted_completion(payload, timeout_seconds)

    llm._completion = completion_after_worker

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=case.app), base_url="http://isolated.test") as http:
            result = await _chat(case, http, "创建一个基金穿透投研工具任务")
            assert result["ok"] is True
            for job in scenario.manager.jobs.values():
                await asyncio.wait_for(job.task, timeout=3)
            assert len(scenario.manager.jobs) == 1
            assert len(result["platform_actions"]) == 1
            assert len(scenario.worker_calls) == 1
        await scenario.manager.shutdown()

    asyncio.run(exercise())


@pytest.mark.parametrize("body", [
    {"tool": "unsupported", "input": {}, "request_id": "invalid-tool"},
    {"tool": "funds", "input": [], "request_id": "invalid-input"},
    {"tool": "funds", "request_id": "missing-input"},
    {"tool": "funds", "input": {}, "request_id": "short"},
])
def test_invalid_research_tool_contract_returns_422_and_creates_no_job(research_case, body):
    scenario = research_case
    case = scenario.case

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=case.app), base_url="http://isolated.test") as http:
            direct = await http.post("/api/research-tools/jobs", json=body, headers=case.headers["alice"])
            assert direct.status_code == 422
            case.script(_calls(_tool("post:/api/research-tools/jobs", body=body)), _answer())
            result = await _chat(case, http, "创建一个投研工具研究任务")
            assert result["ok"] is False
            assert result["platform_actions"][0]["status"] == "failed"
            assert not scenario.manager.jobs and not scenario.worker_calls and not scenario.data_calls
        await scenario.manager.shutdown()

    asyncio.run(exercise())


def test_seven_tool_catalog_is_assistant_readable_and_file_routes_are_explicit_boundaries(research_case):
    scenario = research_case
    case = scenario.case

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=case.app), base_url="http://isolated.test") as http:
            capabilities = (await http.get("/api/agents/platform/capabilities", headers=case.headers["alice"])).json()
            research_capabilities = {item["id"] for item in capabilities["capabilities"] if item["group"] == "research_tools"}
            assert research_capabilities == {"get:/api/research-tools/catalog", "post:/api/research-tools/jobs",
                                             "get:/api/research-tools/jobs/{identifier}", "delete:/api/research-tools/jobs/{identifier}"}
            boundaries = {item["id"]: item for item in capabilities["boundaries"]["frontend_interactions"]}
            assert boundaries["get:/api/research-tools/templates/{tool}"]["enabled"] is False
            assert boundaries["post:/api/portfolio/statement/analyze"]["frontend_interaction"] is True
            llm = case.script(_calls(_tool("get:/api/research-tools/catalog")), _answer())
            result = await _chat(case, http, "查看投研工具输入要求和功能")
            assert result["ok"] is True
            catalog = json.loads(llm.requests[1]["messages"][-1]["content"])["data"]
            assert {item["tool"] for item in catalog["tools"]} == set(TOOLS)
            assert scenario.worker_calls == [{"operation": "catalog"}]
            assert not scenario.data_calls and not scenario.manager.jobs
            # A binary template cannot be made callable by a model-provided ID.
            case.script(_calls(_tool("get:/api/research-tools/templates/{tool}", path_params={"tool": "funds"})), _answer())
            rejected = await _chat(case, http, "下载投研工具模板", request_id="file-boundary")
            assert rejected["ok"] is False
            assert rejected["tool_trace"][0]["ok"] is False
            assert scenario.worker_calls == [{"operation": "catalog"}]
        await scenario.manager.shutdown()

    asyncio.run(exercise())


def test_anonymous_research_operations_are_rejected_before_the_compute_boundary(research_case):
    scenario = research_case
    case = scenario.case
    for method, path, body in [("GET", "/catalog", None), ("POST", "/jobs", {"tool": "funds", "input": {}}),
                               ("GET", "/jobs/unknown", None), ("DELETE", "/jobs/unknown", None)]:
        response = case.client.request(method, "/api/research-tools" + path, json=body)
        assert response.status_code == 401
    assert not scenario.worker_calls and not scenario.manager.jobs
