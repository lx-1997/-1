"""Real worker and independent-process quota tests; no production database."""
import asyncio
import copy
import json
import os
import subprocess
import sys
import threading

import pytest
import pytest_asyncio
from deepfocus_api import quant_research as service,quant_research_store as jobs,quant_worker_slots as slots
from deepfocus_api import quant_event_service as event,quant_evidence_store as event_evidence
from deepfocus_api import quant_calendar_service as calendar,quant_calendar_store as calendar_evidence
from deepfocus_api.tests.test_quant_research_loop import dataset,request
from deepfocus_api.tests.test_quant_exchange_schedule import reviewed
from deepfocus_api.tests.test_quant_event_api import parent as event_parent,sample,PDF,SHA
from deepfocus_api.tests.test_quant_paper_calendar import source


@pytest_asyncio.fixture(autouse=True)
async def isolated(tmp_path,monkeypatch):
    monkeypatch.setenv('DEEPFOCUS_QUANT_RESEARCH_DB_PATH',str(tmp_path/'isolated.sqlite3'))
    monkeypatch.setattr(service,'_tasks',{})
    # These tests deliberately inject threading.Event barriers into Python
    # callables. Keep that thread-lifecycle unit adapter explicit; real CPU
    # subprocess ownership and cancellation are tested separately.
    async def thread_compute(owner,identity,req,data,attempt):
        def progress(value,stage):
            if attempt.cancelled or not jobs.update(owner,identity,progress=value,stage=stage):
                raise InterruptedError('isolated cancelled compute')
        return await service._wait_price_worker(service._paper_io(service.engine.walk_forward,data,req['symbols'],req['benchmark'],req['capital'],progress))
    monkeypatch.setattr(service,'_compute_price',thread_compute)
    yield
    pending=list(service._tasks.values())
    for task in pending:task.cancel()
    await asyncio.gather(*pending,return_exceptions=True)


def occupancy(owner='a'):
    with jobs.connect() as c:return slots.counts(c,owner)


async def wait(event):assert await asyncio.wait_for(asyncio.to_thread(event.wait,2),3)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['price','event','calendar'])
async def test_each_actual_service_retains_cancelled_worker_slot_until_thread_exits(monkeypatch,reviewed,kind):
    entered,release=threading.Event(),threading.Event()
    if kind=='price':
        async def fetch(*a):return dataset()
        monkeypatch.setattr(service,'fetch_dataset',fetch)
        module,name=service.engine,'walk_forward'
    elif kind=='event':module,name=event.engine,'replay'
    else:module,name=calendar,'check_dataset'
    actual=getattr(module,name)
    def blocked(*a,**k):entered.set();release.wait(3);return actual(*a,**k)
    monkeypatch.setattr(module,name,blocked)
    if kind=='price':created=service.submit('a','blocked-price',request())
    elif kind=='event':
        p=event_parent();bundle=event_evidence.put('a','event fixture',[sample()],{SHA:PDF})
        created=await event.submit('a',p['id'],bundle['id'],'blocked-event','historical_reconstruction')
    else:
        p=source(reviewed);bundle=calendar_evidence.put('a','annual fixture',*reviewed)
        created=await calendar.submit('a',p['id'],bundle['id'],'blocked-calendar')
    task=service._tasks[created['id']]
    try:
        await wait(entered);jobs.cancel('a',created['id'])
        assert jobs.get('a',created['id'])['status']=='cancelled' and occupancy()==(1,1)
        jobs.create('a','second-reserved',request(),'test')
        with pytest.raises(RuntimeError,match='名额'):jobs.create('a','third-denied',request(),'test')
    finally:release.set()
    await task
    assert jobs.get('a',created['id'])['status']=='cancelled' and occupancy()==(1,1)
    assert jobs.create('a','after-exit',request(),'test')[1]


def test_claim_is_once_and_wrong_token_or_owner_cannot_release_active_worker():
    job,_=jobs.create('a','once',request(),'test')
    assert jobs.claim_worker('a',job['id'],'first')
    assert not jobs.claim_worker('a',job['id'],'second')
    jobs.cancel('a',job['id']);assert occupancy()==(1,1)
    assert not jobs.finish_worker('a',job['id'],'second')
    assert not jobs.finish_worker('b',job['id'],'first')
    assert occupancy()==(1,1)
    assert jobs.finish_worker('a',job['id'],'first')
    assert occupancy()==(0,0) and jobs.get('a',job['id'])['status']=='cancelled'


@pytest.mark.asyncio
async def test_cancel_before_claim_never_computes_or_retains_unclaimed_slot(monkeypatch):
    job,_=jobs.create('a','never-started',request(),'test');jobs.cancel('a',job['id'])
    async def forbidden(*a):raise AssertionError('Cancelled unclaimed job cannot execute')
    guarded=service.research_worker(forbidden)
    await guarded('a',job['id'],request())
    assert occupancy()==(0,0)


def test_expired_active_worker_is_not_reclaimed_just_because_status_is_failed():
    job,_=jobs.create('a','alive-expiry',request(),'test');assert jobs.claim_worker('a',job['id'],'worker')
    with jobs.connect() as c:c.execute('UPDATE qr_experiment SET updated=? WHERE id=?',('2020-01-01T00:00:00+00:00',job['id']))
    jobs.expire_interrupted()
    assert jobs.get('a',job['id'])['status']=='failed' and occupancy()==(1,1)
    jobs.create('a','second',request(),'test')
    with pytest.raises(RuntimeError):jobs.create('a','third',request(),'test')
    assert jobs.finish_worker('a',job['id'],'worker')


@pytest.mark.parametrize('terminal',['running','cancelled'])
def test_proven_dead_same_scope_process_releases_slot_without_reopening_terminal_job(terminal):
    if slots.process_scope() is None:pytest.skip('OS process scope unavailable; intentionally no automatic reaping')
    code="""import os,json,sys
from deepfocus_api import quant_research_store as j
r,_=j.create('a','dead-child',{'name':'isolated'},'test');assert j.claim_worker('a',r['id'],'child')
if sys.argv[1]=='cancelled':j.cancel('a',r['id'])
print(r['id'],flush=True);os._exit(0)
"""
    child=subprocess.run([sys.executable,'-c',code,terminal],capture_output=True,text=True,check=True)
    identity=child.stdout.strip();assert occupancy()==(1,1)
    jobs.create('a','after-death',request(),'test')
    assert occupancy()==(1,1)
    assert jobs.get('a',identity)['status']==('failed' if terminal=='running' else 'cancelled')


@pytest.mark.parametrize('unknown',['foreign_scope','permission'])
def test_unknown_process_liveness_is_not_treated_as_dead(monkeypatch,unknown):
    job,_=jobs.create('a','unknown',request(),'test');assert jobs.claim_worker('a',job['id'],'unknown')
    jobs.cancel('a',job['id'])
    if unknown=='foreign_scope':
        with jobs.connect() as c:c.execute('UPDATE qr_worker_slot SET scope=? WHERE experiment=?',('foreign-instance',job['id']))
    else:
        def denied(*a):raise PermissionError('test only')
        monkeypatch.setattr(slots.os,'kill',denied)
    jobs.create('a','second-unknown',request(),'test')
    with pytest.raises(RuntimeError):jobs.create('a','denied-third',request(),'test')
    assert occupancy()==(2,2)


def test_independent_live_process_cancelled_workers_still_enforce_owner_and_global_quotas():
    code="""import json,sys
from deepfocus_api import quant_research_store as j
rows=[]
for i in range(2):
 r,_=j.create('a','live-'+str(i),{'name':'isolated'},'test');assert j.claim_worker('a',r['id'],'child');j.cancel('a',r['id']);rows.append(r['id'])
print(json.dumps(rows),flush=True);sys.stdin.readline()
for identity in rows:assert j.finish_worker('a',identity,'child')
"""
    child=subprocess.Popen([sys.executable,'-c',code],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        ids=json.loads(child.stdout.readline());assert child.poll() is None and len(ids)==2
        with pytest.raises(RuntimeError):jobs.create('a','owner-blocked',request(),'test')
        for i in range(2):jobs.create('b','other-'+str(i),request(),'test')
        with pytest.raises(RuntimeError):jobs.create('c','global-blocked',request(),'test')
        assert occupancy()==(2,4)
    finally:out,err=child.communicate('exit\n',timeout=5)
    assert child.returncode==0,err
    assert occupancy()==(0,2)


@pytest.mark.asyncio
async def test_timeout_does_not_release_slot_while_actual_compute_is_still_draining(monkeypatch):
    entered,release=threading.Event(),threading.Event();actual=service.engine.walk_forward;real_wait=asyncio.wait_for
    async def shorter(awaitable,timeout):return await real_wait(awaitable,.02 if timeout==180 else timeout)
    def compute(*a,**k):entered.set();release.wait(3);return actual(*a,**k)
    async def fetch(*a):return dataset()
    monkeypatch.setattr(service,'fetch_dataset',fetch);monkeypatch.setattr(service.engine,'walk_forward',compute)
    monkeypatch.setattr(service.asyncio,'wait_for',shorter)
    created=service.submit('a','timeout-drain',request());task=service._tasks[created['id']]
    try:
        await wait(entered);await asyncio.sleep(.05)
        assert not task.done() and occupancy()==(1,1)
        jobs.create('a','reserved-while-drain',request(),'test')
        with pytest.raises(RuntimeError):jobs.create('a','third-while-drain',request(),'test')
    finally:release.set()
    await task
    assert jobs.get('a',created['id'])['status']=='failed' and occupancy()==(1,1)


@pytest.mark.asyncio
async def test_cancel_during_claim_releases_only_its_own_claim(monkeypatch):
    entered,release=threading.Event(),threading.Event();actual=jobs.claim_worker;calls=[]
    def claim(*a):entered.set();release.wait(3);return actual(*a)
    async def compute(*a):calls.append('unexpected')
    guarded=service.research_worker(compute)
    monkeypatch.setattr(jobs,'claim_worker',claim)
    job,_=jobs.create('a','claim-race',request(),'test');task=asyncio.create_task(guarded('a',job['id'],request()))
    try:
        await wait(entered);task.cancel();await asyncio.sleep(.01);assert not task.done()
    finally:release.set()
    with pytest.raises(asyncio.CancelledError):await task
    assert not calls and occupancy()==(0,0) and jobs.get('a',job['id'])['status']=='failed'


@pytest.mark.asyncio
async def test_duplicate_executor_cannot_claim_or_remove_the_original_owned_task(monkeypatch):
    entered,release=threading.Event(),threading.Event();actual=service.engine.walk_forward
    def compute(*a,**k):entered.set();release.wait(3);return actual(*a,**k)
    async def fetch(*a):return dataset()
    monkeypatch.setattr(service,'fetch_dataset',fetch);monkeypatch.setattr(service.engine,'walk_forward',compute)
    job=service.submit('a','single-executor',request());task=service._tasks[job['id']]
    try:
        await wait(entered)
        await service.execute('a',job['id'],request())
        assert service._tasks.get(job['id']) is task and occupancy()==(1,1)
    finally:release.set()
    await task
    assert occupancy()==(0,0) and jobs.get('a',job['id'])['status']=='completed'


@pytest.mark.asyncio
async def test_cancel_after_claim_before_first_stage_does_not_fetch_data(monkeypatch):
    actual=jobs.claim_worker;calls=[]
    def claimed_then_cancelled(owner,identity,token):
        accepted=actual(owner,identity,token)
        if accepted:jobs.cancel(owner,identity)
        return accepted
    async def forbidden(*a):calls.append('fetch');raise AssertionError('Cancelled job must not fetch')
    monkeypatch.setattr(jobs,'claim_worker',claimed_then_cancelled)
    monkeypatch.setattr(service,'fetch_dataset',forbidden)
    job=service.submit('a','cancel-after-claim',request());await service._tasks[job['id']]
    assert not calls and jobs.get('a',job['id'])['status']=='cancelled' and occupancy()==(0,0)


def test_readonly_inspector_reports_cancelled_occupancy_without_raw_owner_or_mutation():
    from pathlib import Path
    owner='private-owner-canary'
    job,_=jobs.create(owner,'inspect',request(),'test');assert jobs.claim_worker(owner,job['id'],'worker');jobs.cancel(owner,job['id'])
    with jobs.connect() as c:before=[tuple(r) for r in c.execute('SELECT * FROM qr_worker_slot')]
    script=Path(__file__).resolve().parents[3]/'scripts/inspect-quant-worker-slots.py'
    result=subprocess.run([sys.executable,str(script),'--db',os.environ['DEEPFOCUS_QUANT_RESEARCH_DB_PATH']],capture_output=True,text=True,check=True)
    report=json.loads(result.stdout)
    assert report['occupied_slots']==1 and report['items'][0]['job_status']=='cancelled'
    assert report['read_only'] and not report['automatic_recovery_performed']
    assert owner not in result.stdout
    with jobs.connect() as c:assert [tuple(r) for r in c.execute('SELECT * FROM qr_worker_slot')]==before


def test_readonly_inspector_does_not_migrate_legacy_database(tmp_path):
    from pathlib import Path
    import sqlite3
    path=tmp_path/'legacy.sqlite3';c=sqlite3.connect(path)
    c.execute('CREATE TABLE qr_experiment(status TEXT)');c.execute("INSERT INTO qr_experiment VALUES('running')");c.commit();c.close()
    script=Path(__file__).resolve().parents[3]/'scripts/inspect-quant-worker-slots.py'
    result=subprocess.run([sys.executable,str(script),'--db',str(path)],capture_output=True,text=True,check=True)
    report=json.loads(result.stdout);assert report['legacy_active_without_slot']==1 and report['occupied_slots']==0
    c=sqlite3.connect(path)
    try:assert c.execute("SELECT count(*) FROM sqlite_master WHERE name='qr_worker_slot'").fetchone()[0]==0
    finally:c.close()
