import asyncio

import pytest
import pytest_asyncio

from deepfocus_api import quant_jobs
from deepfocus_api.schemas import QuantLabRequest


@pytest_asyncio.fixture(autouse=True)
async def store(monkeypatch, tmp_path):
    monkeypatch.setattr(quant_jobs, '_db_path', lambda: tmp_path / 'jobs.sqlite3')
    monkeypatch.setattr(quant_jobs, '_jobs', {})
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
    quant_jobs._save(job, create=True)
    quant_jobs.init_quant_jobs()
    recovered = quant_jobs.get_quant_job('owner', 'interrupted')
    assert recovered['status'] == 'failed'
    assert recovered['error'] == 'worker_interrupted'
    assert recovered['completed_at']


@pytest.mark.asyncio
@pytest.mark.parametrize('remote_action', ['cancel', 'restart'])
async def test_remote_terminal_state_cannot_be_revived(monkeypatch, remote_action):
    started, release = asyncio.Event(), asyncio.Event()
    async def fake_run(request, progress_callback=None):
        started.set()
        await release.wait()
        progress_callback(90, 'late progress')
        return {'late': True}
    monkeypatch.setattr(quant_jobs, 'run_quant_lab', fake_run)
    created = await quant_jobs.create_quant_job('owner', QuantLabRequest(name='test', symbols=['AAPL']))
    task = quant_jobs._jobs[created['job_id']].task
    await started.wait()
    # A second worker has the same durable store but a separate task registry.
    with monkeypatch.context() as remote:
        remote.setattr(quant_jobs, '_jobs', {})
        if remote_action == 'cancel':
            await quant_jobs.cancel_quant_job('owner', created['job_id'])
        else:
            quant_jobs.init_quant_jobs()
        terminal = quant_jobs.get_quant_job('owner', created['job_id'])
    release.set()
    await task
    assert quant_jobs.get_quant_job('owner', created['job_id']) == terminal


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['nonfinite', 'exception'])
async def test_invalid_result_and_provider_errors_fail_safely(monkeypatch, failure):
    canary = 'secret-provider-token-canary'
    async def fake_run(request, progress_callback=None):
        if failure == 'exception':
            raise ValueError('upstream https://example.invalid/?token=' + canary)
        return {'bad': float('nan')}
    monkeypatch.setattr(quant_jobs, 'run_quant_lab', fake_run)
    created = await quant_jobs.create_quant_job('owner', QuantLabRequest(name='test', symbols=['AAPL']))
    await quant_jobs._jobs[created['job_id']].task
    result = quant_jobs.get_quant_job('owner', created['job_id'])
    assert result['status'] == 'failed'
    assert result['result'] is None
    assert result['error'] == 'research_failed'
    assert canary not in quant_jobs._db_path().read_bytes().decode('latin1')


def test_late_writes_cannot_change_owner_or_recreate_deleted_job():
    job = quant_jobs.QuantJob('guarded', 'owner', status='running')
    assert quant_jobs._save(job, create=True)
    impostor = quant_jobs.QuantJob('guarded', 'other', status='completed')
    assert not quant_jobs._save(impostor)
    assert quant_jobs.get_quant_job('owner', 'guarded')['status'] == 'running'
    with quant_jobs._connect() as conn:
        conn.execute('DELETE FROM quant_jobs WHERE job_id=?', ('guarded',))
    job.status = 'completed'
    assert not quant_jobs._save(job)
    assert quant_jobs.get_quant_job('owner', 'guarded') is None


@pytest.mark.asyncio
async def test_remote_cancel_without_further_progress_blocks_completion(monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    async def fake_run(request, progress_callback=None):
        started.set()
        await release.wait()
        return {'late': True}
    monkeypatch.setattr(quant_jobs, 'run_quant_lab', fake_run)
    created = await quant_jobs.create_quant_job('owner', QuantLabRequest(name='test', symbols=['AAPL']))
    task = quant_jobs._jobs[created['job_id']].task
    await started.wait()
    with monkeypatch.context() as remote:
        remote.setattr(quant_jobs, '_jobs', {})
        terminal = await quant_jobs.cancel_quant_job('owner', created['job_id'])
    release.set()
    await task
    assert quant_jobs.get_quant_job('owner', created['job_id']) == terminal


@pytest.mark.parametrize('terminal', ['completed', 'failed', 'cancelled'])
def test_terminal_payload_is_immutable(terminal):
    job = quant_jobs.QuantJob('terminal', 'owner', status=terminal, result={'original': 1})
    assert quant_jobs._save(job, create=True)
    original = quant_jobs.get_quant_job('owner', 'terminal')
    job.status, job.result = 'running', {'replacement': 2}
    assert not quant_jobs._save(job)
    assert quant_jobs.get_quant_job('owner', 'terminal') == original


@pytest.mark.asyncio
async def test_cancellation_from_real_separate_process(monkeypatch):
    import os
    import subprocess
    import sys
    started, release = asyncio.Event(), asyncio.Event()
    async def fake_run(request, progress_callback=None):
        started.set()
        await release.wait()
        return {'late': True}
    monkeypatch.setattr(quant_jobs, 'run_quant_lab', fake_run)
    created = await quant_jobs.create_quant_job('owner', QuantLabRequest(name='test', symbols=['AAPL']))
    task = quant_jobs._jobs[created['job_id']].task
    await started.wait()
    env = dict(os.environ, DEEPFOCUS_QUANT_JOBS_DB_PATH=str(quant_jobs._db_path()))
    code = ('import asyncio,sys; from deepfocus_api.quant_jobs import cancel_quant_job; '
            "assert asyncio.run(cancel_quant_job('owner',sys.argv[1]))['status']=='cancelled'")
    try:
        completed = await asyncio.to_thread(subprocess.run, [sys.executable, '-c', code, created['job_id']],
                                           env=env, capture_output=True, text=True, timeout=20)
        assert completed.returncode == 0, completed.stderr
    finally:
        release.set()
        await task
    assert quant_jobs.get_quant_job('owner', created['job_id'])['status'] == 'cancelled'


def test_capacity_check_and_insert_share_one_write_transaction(monkeypatch):
    monkeypatch.setattr(quant_jobs, '_MAX_JOBS', 1)
    assert quant_jobs._save(quant_jobs.QuantJob('first', 'owner'), create=True)
    with pytest.raises(RuntimeError, match='队列已满'):
        quant_jobs._save(quant_jobs.QuantJob('second', 'other'), create=True)
    assert quant_jobs.get_quant_job('other', 'second') is None
