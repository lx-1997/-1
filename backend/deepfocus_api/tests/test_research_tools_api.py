from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from deepfocus_api.auth import require_current_user
from deepfocus_api import research_jobs
from deepfocus_api import research_tools_api as api


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(research_jobs, "manager", research_jobs.ResearchJobs())
    monkeypatch.setattr(api, "_catalog_cache", None)
    @asynccontextmanager
    async def main_lifespan(_app):
        yield
    application = FastAPI(lifespan=main_lifespan)
    application.include_router(api.router)
    api.attach_research_lifespan(application)
    return application


def test_every_route_protects_anonymous(app):
    with TestClient(app) as client:
        for method, path, body in [
            ("GET", "/catalog", None), ("GET", "/templates/funds", None),
            ("POST", "/jobs", {"tool": "funds", "input": {}}),
            ("GET", "/jobs/unknown", None), ("DELETE", "/jobs/unknown", None),
        ]:
            assert client.request(method, "/api/research-tools" + path, json=body).status_code == 401


def test_auth_job_isolation_and_private_downloads(app, monkeypatch):
    owner = {"value": "alice"}
    app.dependency_overrides[require_current_user] = lambda: {"sub": owner["value"]}
    async def worker(payload, **kwargs):
        if payload.get("operation") == "catalog":
            return {"tools": [{"tool": "funds", "available": True}], "expires_in_minutes": 30}
        return {"tool": "funds", "title": "report", "summary": []}
    monkeypatch.setattr(research_jobs, "run_worker", worker)
    with TestClient(app) as client:
        catalog = client.get("/api/research-tools/catalog")
        assert catalog.status_code == 200 and catalog.headers["cache-control"] == "no-store"
        download = client.get("/api/research-tools/templates/funds")
        assert download.status_code == 200 and download.content.startswith(b"\xef\xbb\xbf")
        assert "attachment" in download.headers["content-disposition"]
        response = client.post("/api/research-tools/jobs", json={"tool": "funds", "input": {}, "request_id": "request-1"})
        assert response.status_code == 202 and response.headers["cache-control"] == "no-store"
        identifier = response.json()["id"]
        owner["value"] = "bob"
        assert client.get("/api/research-tools/jobs/" + identifier).status_code == 404
        assert client.delete("/api/research-tools/jobs/" + identifier).status_code == 404
        owner["value"] = "alice"
        current = client.get("/api/research-tools/jobs/" + identifier)
        assert current.status_code == 200
        assert "owner" not in current.json() and "digest" not in current.json()
        assert client.delete("/api/research-tools/jobs/" + identifier).json()["state"] == "cancelled"


def test_combined_custom_lifespan_reaps_tasks_and_results(app, monkeypatch):
    import asyncio
    app.dependency_overrides[require_current_user] = lambda: {"sub": "alice"}
    async def worker(_payload):
        await asyncio.sleep(60)
    monkeypatch.setattr(research_jobs, "run_worker", worker)
    with TestClient(app) as client:
        response = client.post("/api/research-tools/jobs", json={"tool": "funds", "input": {}})
        assert response.status_code == 202
        assert research_jobs.manager.jobs
    assert research_jobs.manager.closing and not research_jobs.manager.jobs


def test_invalid_shapes_and_oversize_requests(app):
    app.dependency_overrides[require_current_user] = lambda: {"sub": "alice"}
    with TestClient(app) as client:
        assert client.post("/api/research-tools/jobs", json={"tool": "unsupported", "input": {}}).status_code == 422
        assert client.post("/api/research-tools/jobs", json={"tool": "funds", "input": []}).status_code == 422
        assert client.post("/api/research-tools/jobs", json={"tool": "funds", "input": {"data": "x" * (research_jobs.MAX_INPUT_BYTES + 1)}}).status_code == 413
