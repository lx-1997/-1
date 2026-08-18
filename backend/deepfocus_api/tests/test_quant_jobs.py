import asyncio

import pytest

from deepfocus_api import quant_jobs
from deepfocus_api.schemas import QuantLabRequest


@pytest.fixture(autouse=True)
async def clean_jobs():
    await quant_jobs.reset_quant_jobs_for_tests()
    yield
    await quant_jobs.reset_quant_jobs_for_tests()


@pytest.mark.asyncio
async def test_job_progress_completion_and_owner_isolation(monkeypatch):
    async def fake_run(request, progress_callback=None):
        progress_callback(35, "正在计算")
        await asyncio.sleep(0)
        progress_callback(100, "完成")
        return {"name": request.name}

    monkeypatch.setattr(quant_jobs, "run_quant_lab", fake_run)
    created = await quant_jobs.create_quant_job("owner-a", QuantLabRequest(name="job", symbols=["AAPL"]))
    await asyncio.sleep(0.01)
    completed = quant_jobs.get_quant_job("owner-a", created["job_id"])

    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["progress"] == 100
    assert completed["result"] == {"name": "job"}
    assert quant_jobs.get_quant_job("owner-b", created["job_id"]) is None


@pytest.mark.asyncio
async def test_job_can_be_cancelled(monkeypatch):
    async def fake_run(request, progress_callback=None):
        progress_callback(10, "正在等待")
        await asyncio.sleep(60)
        return {}

    monkeypatch.setattr(quant_jobs, "run_quant_lab", fake_run)
    created = await quant_jobs.create_quant_job("owner", QuantLabRequest(name="job", symbols=["AAPL"]))
    await asyncio.sleep(0)
    cancelled = await quant_jobs.cancel_quant_job("owner", created["job_id"])

    assert cancelled is not None
    assert cancelled["status"] == "cancelled"
    assert cancelled["completed_at"]
