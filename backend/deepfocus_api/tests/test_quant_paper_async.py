"""Bounded workers, cancellation and event-loop responsiveness under real locks."""
import asyncio
import copy
import os
import sqlite3
import threading
import time

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport,AsyncClient

from deepfocus_api import quant_research as service,quant_research_store as jobs,quant_research_api as api
from deepfocus_api.tests.test_quant_exchange_schedule import reviewed
from deepfocus_api.tests.test_quant_paper_calendar import source
from deepfocus_api.tests.paper_calendar_fixture import aligned,approve


@pytest_asyncio.fixture(autouse=True)
async def isolated(tmp_path,monkeypatch):
    monkeypatch.setenv('DEEPFOCUS_QUANT_RESEARCH_DB_PATH',str(tmp_path/'isolated.sqlite3'))
    monkeypatch.setattr(service,'_tasks',{});monkeypatch.setattr(service,'_refresh_tasks',{});monkeypatch.setattr(service,'_refreshing',set())
    yield
    pending=list(service._tasks.values())+list(service._refresh_tasks.values())
    for task in pending:task.cancel()
    await asyncio.gather(*pending,return_exceptions=True)


async def ready(reviewed,monkeypatch):
    parent=source(reviewed);await approve('a',parent['id'],reviewed)
    monkeypatch.setattr(service,'complete_day',lambda:parent['request']['end_date'])
    paper=service.promote('a',parent['id']);data=aligned(reviewed)
    async def fetch(*a):return data
    monkeypatch.setattr(service,'fetch_dataset',fetch)
    monkeypatch.setattr(service,'complete_day',lambda:data['600519']['bars'][-1]['date'])
    return paper


async def wait_thread(event):
    assert await asyncio.wait_for(asyncio.to_thread(event.wait,2),3)


@pytest.mark.asyncio
async def test_real_sqlite_wait_does_not_block_loop_and_cancel_before_commit_keeps_account(reviewed,monkeypatch):
    paper=await ready(reviewed,monkeypatch);before=copy.deepcopy(paper)
    waiting,release,worker_entered=threading.Event(),threading.Event(),threading.Event();threads=[];actual=jobs.save_paper
    def save(*args,**kwargs):
        def lock():
            c=sqlite3.connect(os.environ['DEEPFOCUS_QUANT_RESEARCH_DB_PATH']);c.execute('BEGIN IMMEDIATE')
            waiting.set();release.wait(3);c.rollback();c.close()
        thread=threading.Thread(target=lock);thread.start();threads.append(thread)
        assert waiting.wait(1);worker_entered.set()
        return actual(*args,**kwargs)
    monkeypatch.setattr(jobs,'save_paper',save)
    task=asyncio.create_task(service.refresh_paper('a',paper['id']))
    try:
        await wait_thread(worker_entered)
        # This coroutine and another API read must run while the write waits.
        started=time.perf_counter();await asyncio.sleep(.02)
        assert time.perf_counter()-started<.2
        app=FastAPI();app.include_router(api.router);app.dependency_overrides[api.owner]=lambda:'b'
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://fixture') as client:
            assert (await asyncio.wait_for(client.get('/api/quant-research/paper'),.5)).json()=={'items':[]}
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert ('a',paper['id']) in service._refreshing
        with pytest.raises(RuntimeError,match='正在同步'):await service.refresh_paper('a',paper['id'])
    finally:
        release.set()
        if not task.done():task.cancel()
    await asyncio.gather(*list(service._refresh_tasks.values()),return_exceptions=True)
    for thread in threads:thread.join(1)
    assert jobs.get('a',paper['id'],True)==before
    assert not service._refreshing and not service._refresh_tasks


@pytest.mark.asyncio
@pytest.mark.parametrize('stage',['read','replay'])
async def test_cancelled_thread_is_drained_before_capacity_released(reviewed,monkeypatch,stage):
    paper=await ready(reviewed,monkeypatch);before=copy.deepcopy(paper)
    entered,release=threading.Event(),threading.Event()
    target=jobs if stage=='read' else service.engine;name='get' if stage=='read' else 'replay';actual=getattr(target,name)
    def work(*args,**kwargs):
        entered.set();release.wait(3);return actual(*args,**kwargs)
    monkeypatch.setattr(target,name,work)
    task=asyncio.create_task(service.refresh_paper('a',paper['id']))
    try:
        await wait_thread(entered);task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert ('a',paper['id']) in service._refreshing
        # Repeated cancellation of the owned task must not orphan the thread.
        service._refresh_tasks[('a',paper['id'])].cancel()
        await asyncio.sleep(.01);assert ('a',paper['id']) in service._refreshing
    finally:release.set()
    await asyncio.gather(*list(service._refresh_tasks.values()),return_exceptions=True)
    assert jobs.get('a',paper['id'],True)==before and not service._refreshing


@pytest.mark.asyncio
async def test_cancel_after_commit_admission_reports_no_false_rollback_and_no_duplicate_fills(reviewed,monkeypatch):
    paper=await ready(reviewed,monkeypatch);committed,release=threading.Event(),threading.Event();actual=jobs.save_paper
    def save(*args,**kwargs):
        result=actual(*args,**kwargs);committed.set();release.wait(3);return result
    monkeypatch.setattr(jobs,'save_paper',save)
    task=asyncio.create_task(service.refresh_paper('a',paper['id']))
    try:
        await wait_thread(committed);task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert ('a',paper['id']) in service._refreshing
        saved=jobs.get('a',paper['id'],True);assert saved['fills'] and saved['revision']==paper['revision']+1
    finally:release.set()
    await asyncio.gather(*list(service._refresh_tasks.values()),return_exceptions=True)
    again=await service.refresh_paper('a',paper['id'])
    assert again['fills']==saved['fills'] and again['state']==saved['state']


@pytest.mark.asyncio
async def test_cancelled_or_failed_worker_does_not_leak_exception_or_capacity(monkeypatch):
    entered,release=threading.Event(),threading.Event()
    def fails():entered.set();release.wait(3);raise ValueError('isolated late failure')
    async def work(*_):return await service._paper_io(fails)
    monkeypatch.setattr(service,'_refresh_paper',work)
    task=asyncio.create_task(service.refresh_paper('a','fixture'))
    try:
        await wait_thread(entered);task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
    finally:release.set()
    await asyncio.gather(*list(service._refresh_tasks.values()),return_exceptions=True)
    assert not service._refreshing and not service._refresh_tasks


@pytest.mark.asyncio
async def test_lifespan_waits_for_owned_refresh_thread_on_shutdown(monkeypatch):
    entered,release=threading.Event(),threading.Event()
    def worker():entered.set();release.wait(3)
    async def work(*_):return await service._paper_io(worker)
    monkeypatch.setattr(service,'_refresh_paper',work)
    app=FastAPI();service.attach_lifespan(app)
    async def context():
        async with app.router.lifespan_context(app):
            task=asyncio.create_task(service.refresh_paper('a','shutdown-fixture'))
            await wait_thread(entered)
        return task
    closing=asyncio.create_task(context())
    try:
        await wait_thread(entered);await asyncio.sleep(.02)
        assert not closing.done() and service._refresh_tasks
    finally:release.set()
    request=await closing
    await asyncio.gather(request,return_exceptions=True)
    assert not service._refreshing


@pytest.mark.asyncio
async def test_api_uncertain_failure_does_not_claim_account_unchanged(monkeypatch):
    async def failed(*_):raise OSError('isolated postcommit lookup failure')
    monkeypatch.setattr(service,'refresh_paper',failed)
    app=FastAPI();app.include_router(api.router);app.dependency_overrides[api.owner]=lambda:'a'
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://fixture') as client:
        response=await client.post('/api/quant-research/paper/fixture/refresh')
    assert response.status_code==503 and '请刷新查看账户状态' in response.json()['detail']
    assert '账户未改动' not in response.text


@pytest.mark.asyncio
async def test_direct_shutdown_cancellation_of_owned_task_revokes_queued_commit(reviewed,monkeypatch):
    paper=await ready(reviewed,monkeypatch);before=copy.deepcopy(paper)
    entered,release=threading.Event(),threading.Event();actual=jobs.save_paper
    def queued(*args,**kwargs):
        entered.set();release.wait(3);return actual(*args,**kwargs)
    monkeypatch.setattr(jobs,'save_paper',queued)
    request=asyncio.create_task(service.refresh_paper('a',paper['id']))
    try:
        await wait_thread(entered)
        service._refresh_tasks[('a',paper['id'])].cancel()
        await asyncio.sleep(.01)
        assert not request.done() and ('a',paper['id']) in service._refreshing
    finally:release.set()
    with pytest.raises(asyncio.CancelledError):await request
    assert jobs.get('a',paper['id'],True)==before
    assert not service._refreshing


@pytest.mark.asyncio
async def test_price_experiment_status_write_does_not_block_event_loop(monkeypatch):
    entered,release=threading.Event(),threading.Event();actual=jobs.update
    from deepfocus_api.tests.test_quant_research_loop import request
    def queued(*args,**kwargs):
        if kwargs.get('progress')==8:entered.set();release.wait(3)
        return actual(*args,**kwargs)
    monkeypatch.setattr(jobs,'update',queued)
    async def fetch(*_):return {}
    monkeypatch.setattr(service,'fetch_dataset',fetch)
    created=service.submit('a','status-write-isolated',request())
    task=service._tasks[created['id']]
    try:
        await wait_thread(entered)
        started=time.perf_counter();await asyncio.sleep(.02)
        assert time.perf_counter()-started<.2 and not task.done()
    finally:release.set()
    await task
    assert jobs.get('a',created['id'])['status']=='failed'
