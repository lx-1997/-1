"""Off-loop durable admission: disconnect, idempotence, quotas and shutdown."""
import asyncio
import sqlite3
import threading

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from deepfocus_api import quant_research as service, quant_research_store as jobs
from deepfocus_api import quant_research_api as api, quant_worker_slots as slots
from deepfocus_api.tests.test_quant_research_loop import request


@pytest_asyncio.fixture(autouse=True)
async def isolated(tmp_path,monkeypatch):
    monkeypatch.setenv('DEEPFOCUS_QUANT_RESEARCH_DB_PATH',str(tmp_path/'qr.sqlite3'))
    monkeypatch.setattr(service,'_tasks',{})
    monkeypatch.setattr(service,'_admission_tasks',{})
    monkeypatch.setattr(service,'_refresh_tasks',{})
    monkeypatch.setattr(service,'_admissions_open',True)
    with jobs.connect():pass
    yield
    admissions=list(service._admission_tasks)
    for t in admissions:t.cancel()
    await asyncio.gather(*admissions,return_exceptions=True)
    workers=list(service._tasks.values())
    for t in workers:t.cancel()
    await asyncio.gather(*workers,return_exceptions=True)


def occupancy():
    with jobs.connect() as c:return slots.counts(c,'a')


async def wait(event):
    assert await asyncio.wait_for(asyncio.to_thread(event.wait,3),4)


def worker(counter):
    @service.research_worker
    async def execute(owner,identity,req):
        counter.append(identity)
        await service._paper_io(jobs.update,owner,identity,status='completed',stage='isolated scheduler test')
    return execute


def held_create(monkeypatch,*,after_commit=True):
    entered,release=threading.Event(),threading.Event();actual=jobs.create
    def create(*args):
        if after_commit:
            result=actual(*args)
        entered.set()
        if not release.wait(3):raise TimeoutError('test release missing')
        return result if after_commit else actual(*args)
    monkeypatch.setattr(jobs,'create',create)
    return entered,release


@pytest.mark.asyncio
async def test_api_database_write_wait_does_not_block_loop(monkeypatch):
    counter=[];monkeypatch.setattr(service,'execute',worker(counter))
    held,release=threading.Event(),threading.Event()
    def lock():
        with jobs.connect() as c:
            c.execute('BEGIN IMMEDIATE');held.set();release.wait(3);c.rollback()
    locker=threading.Thread(target=lock);locker.start();await wait(held)
    app=FastAPI();app.include_router(api.router);app.dependency_overrides[api.owner]=lambda:'a'
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
            post=asyncio.create_task(client.post('/api/quant-research/experiments',json={**request(),'request_id':'off-loop-key'}))
            await asyncio.sleep(.03)
            assert not release.is_set() and locker.is_alive() and not post.done()
            release.set();result=await asyncio.wait_for(post,4)
            assert result.status_code==202,result.text
            await service._tasks[result.json()['id']]
    finally:release.set();await asyncio.to_thread(locker.join,4)
    assert len(counter)==1 and occupancy()==(0,0)


@pytest.mark.asyncio
async def test_disconnected_caller_keeps_admission_and_retry_schedules_once(monkeypatch):
    counter=[];execute=worker(counter);entered,release=held_create(monkeypatch)
    caller=asyncio.create_task(service.admit_job('a','disconnect-key',request(),'test',execute))
    try:
        await wait(entered);caller.cancel()
        with pytest.raises(asyncio.CancelledError):await caller
        assert len(service._admission_tasks)==1 and occupancy()==(1,1)
        release.set()
        await asyncio.gather(*list(service._admission_tasks))
        retry=await service.admit_job('a','disconnect-key',request(),'test',execute)
        await asyncio.gather(*list(service._tasks.values()))
        assert counter==[retry['id']] and occupancy()==(0,0)
        with jobs.connect() as c:assert c.execute('SELECT count(*) FROM qr_experiment').fetchone()[0]==1
    finally:release.set()


@pytest.mark.asyncio
async def test_explicit_cancel_before_executor_is_scheduled_removes_reserved_slot(monkeypatch):
    counter=[];entered,release=held_create(monkeypatch)
    caller=asyncio.create_task(service.admit_job('a','delete-key',request(),'test',worker(counter)))
    try:
        await wait(entered)
        with jobs.connect() as c:identity=c.execute('SELECT id FROM qr_experiment').fetchone()[0]
        jobs.cancel('a',identity);assert occupancy()==(0,0)
        release.set();await caller
        await asyncio.gather(*list(service._tasks.values()))
        assert not counter and jobs.get('a',identity)['status']=='cancelled' and occupancy()==(0,0)
    finally:release.set()


@pytest.mark.asyncio
@pytest.mark.parametrize('existing',[False,True])
async def test_shutdown_waits_for_real_admission_and_cancels_only_its_fresh_reservation(monkeypatch,existing):
    counter=[]
    if existing:
        old,_=jobs.create('a','shutdown-key',request(),'test')
        jobs.update('a',old['id'],status='completed',stage='old completed record')
    entered,release=held_create(monkeypatch)
    app=FastAPI();service.attach_lifespan(app);context=app.router.lifespan_context(app)
    await context.__aenter__()
    caller=asyncio.create_task(service.admit_job('a','shutdown-key',request(),'test',worker(counter)))
    await wait(entered)
    closing=asyncio.create_task(context.__aexit__(None,None,None))
    try:
        await asyncio.sleep(.02)
        assert not closing.done() and not service._admissions_open and service._admission_tasks
        with pytest.raises(RuntimeError):await service.admit_job('b','closed-key',request(),'test',worker(counter))
        release.set();await asyncio.wait_for(closing,4)
        with pytest.raises(asyncio.CancelledError):await caller
        assert not counter and not service._admission_tasks and occupancy()==(0,0)
        with jobs.connect() as c:status=c.execute('SELECT status FROM qr_experiment').fetchone()[0]
        assert status==('completed' if existing else 'cancelled')
    finally:
        release.set()
        if not closing.done():await closing


@pytest.mark.asyncio
@pytest.mark.parametrize('limit',['owner','global'])
async def test_pending_admissions_have_separate_bounded_queue_and_do_not_bypass_worker_limit(monkeypatch,limit):
    entered,release=held_create(monkeypatch,after_commit=False);counter=[];execute=worker(counter)
    monkeypatch.setattr(service,'MAX_OWNER_ADMISSIONS',1 if limit=='owner' else 4)
    monkeypatch.setattr(service,'MAX_PENDING_ADMISSIONS',1 if limit=='global' else 16)
    first=asyncio.create_task(service.admit_job('a','queue-one',request(),'test',execute))
    try:
        await wait(entered)
        with pytest.raises(RuntimeError):await service.admit_job('a' if limit=='owner' else 'b','queue-two',request(),'test',execute)
        with jobs.connect() as c:assert c.execute('SELECT count(*) FROM qr_experiment').fetchone()[0]==0
        first.cancel()
        with pytest.raises(asyncio.CancelledError):await first
        assert len(service._admission_tasks)==1
        release.set();await asyncio.gather(*list(service._admission_tasks))
        await asyncio.gather(*list(service._tasks.values()))
        assert len(counter)==1 and occupancy()==(0,0)
    finally:release.set()


@pytest.mark.asyncio
async def test_database_failure_consumed_after_disconnection_and_no_record_or_reservation(monkeypatch):
    entered,release=threading.Event(),threading.Event()
    def fail(*args):
        entered.set();release.wait(3);raise sqlite3.OperationalError('injected test fault')
    monkeypatch.setattr(jobs,'create',fail)
    task=asyncio.create_task(service.admit_job('a','failure-key',request(),'test',worker([])))
    try:
        await wait(entered);task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        owned=list(service._admission_tasks);release.set()
        result=await asyncio.gather(*owned,return_exceptions=True)
        assert isinstance(result[0],sqlite3.OperationalError)
        await asyncio.sleep(0);assert not service._admission_tasks and occupancy()==(0,0)
    finally:release.set()


@pytest.mark.asyncio
async def test_repeated_shutdown_cancellation_drains_admission_then_actual_live_worker(monkeypatch):
    working,finish=threading.Event(),threading.Event()
    def compute():working.set();assert finish.wait(4)
    @service.research_worker
    async def live(owner,identity,req):
        try:await service._paper_io(compute)
        except asyncio.CancelledError:
            await service._paper_io(jobs.update,owner,identity,status='failed',stage='isolated shutdown test')
            raise
    app=FastAPI();service.attach_lifespan(app);context=app.router.lifespan_context(app)
    await context.__aenter__()
    active=await service.admit_job('a','live-during-close',request(),'test',live);await wait(working)
    entered,release=held_create(monkeypatch)
    pending=asyncio.create_task(service.admit_job('b','reserve-during-close',request(),'test',worker([])))
    await wait(entered);closing=asyncio.create_task(context.__aexit__(None,None,None))
    try:
        await asyncio.sleep(.01);closing.cancel();await asyncio.sleep(.01);closing.cancel()
        assert not closing.done() and occupancy()==(1,2)
        release.set()
        for _ in range(100):
            if not service._admission_tasks:break
            await asyncio.sleep(.005)
        assert not service._admission_tasks and not closing.done() and occupancy()==(1,1)
        finish.set()
        with pytest.raises(asyncio.CancelledError):await asyncio.wait_for(closing,4)
        with pytest.raises(asyncio.CancelledError):await pending
        assert not service._tasks and occupancy()==(0,0)
        assert jobs.get('a',active['id'])['status']=='failed'
    finally:release.set();finish.set()
