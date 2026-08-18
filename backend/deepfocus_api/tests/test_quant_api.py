from fastapi.testclient import TestClient

from deepfocus_api import main


def test_quant_job_routes_require_login_and_preserve_owner(monkeypatch):
    client = TestClient(main.app)
    payload = {"name": "api", "symbols": ["AAPL"]}

    anonymous = client.post("/api/quant/lab/jobs", json=payload)
    assert anonymous.status_code == 401

    monkeypatch.setattr(main, "require_current_user", lambda request: {"sub": "user-42", "username": "quant-user"})

    async def fake_create(owner, request):
        assert owner == "user-42"
        assert request.symbols == ["AAPL"]
        return {"job_id": "job-1", "status": "pending", "progress": 0, "stage": "任务已创建"}

    def fake_get(owner, job_id):
        assert owner == "user-42"
        assert job_id == "job-1"
        return {"job_id": job_id, "status": "completed", "progress": 100, "stage": "完成", "result": {}}

    async def fake_cancel(owner, job_id):
        assert owner == "user-42"
        return {"job_id": job_id, "status": "cancelled", "progress": 50, "stage": "任务已取消"}

    monkeypatch.setattr(main, "create_quant_job", fake_create)
    monkeypatch.setattr(main, "get_quant_job", fake_get)
    monkeypatch.setattr(main, "cancel_quant_job", fake_cancel)

    created = client.post("/api/quant/lab/jobs", json=payload)
    polled = client.get("/api/quant/lab/jobs/job-1")
    cancelled = client.delete("/api/quant/lab/jobs/job-1")

    assert created.status_code == 200 and created.json()["job_id"] == "job-1"
    assert polled.status_code == 200 and polled.json()["progress"] == 100
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
