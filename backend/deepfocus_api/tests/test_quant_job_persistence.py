import asyncio

import pytest
import pytest_asyncio

from deepfocus_api import quant_jobs
from deepfocus_api.schemas import QuantLabRequest


@pytest_asyncio.fixture(autouse=True)
async def store(monkeypatch, tmp_path):
    monkeypatch.setattr(quant_jobs, '_db_path', lambda: tmp_path / 'jobs.sqlite3')
    await quant_jobs.reset_quant_jobs_for_tests()
    yield
    await quant_jobs.reset_quant_jobs_for_tests()


@pytest.mark.asyncio
async def test_cancel_before_worker_starts_is_terminal(monkeypatch):
    called = False
    async def fake_run(request, progress_callback=None):
        nonlocal called
        called = True
        return {}
    monkeypatch.setattr(quant_jobs, 'run_quant_lab', fake_run)
    created = await quant_jobs.create_quant_job('owner', QuantLabRequest(name='test', symbols=['AAPL']))
    cancelled = await quant_jobs.cancel_quant_job('owner', created['job_id'])
    assert cancelled['status'] == 'cancelled'
    assert cancelled['completed_at']
    assert called is False
    assert quant_jobs.get_quant_job('other', created['job_id']) is None


@pytest.mark.asyncio
async def test_completed_result_survives_process_memory_loss(monkeypatch):
    async def fake_run(request, progress_callback=None):
        return {'answer': 42}
    monkeypatch.setattr(quant_jobs, 'run_quant_lab', fake_run)
    created = await quant_jobs.create_quant_job('owner', QuantLabRequest(name='test', symbols=['AAPL']))
    await quant_jobs._jobs[created['job_id']].task
    quant_jobs._jobs.clear()
    quant_jobs.init_quant_jobs()
    assert quant_jobs.get_quant_job('owner', created['job_id'])['result'] == {'answer': 42}
    assert quant_jobs.get_quant_job('other', created['job_id']) is None


def test_restart_marks_stale_active_job_interrupted():
    job = quant_jobs.QuantJob('interrupted', 'owner', status='running', progress=25)
    quant_jobs._save(job)
    quant_jobs.init_quant_jobs()
    recovered = quant_jobs.get_quant_job('owner', 'interrupted')
    assert recovered['status'] == 'failed'
    assert recovered['error'] == 'worker_interrupted'
    assert recovered['completed_at']
