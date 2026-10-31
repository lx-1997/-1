import asyncio
from dataclasses import replace
from datetime import date,timedelta
import hashlib
import json
import sqlite3
from pathlib import Path
import pytest
from fastapi import FastAPI
from httpx import AsyncClient,ASGITransport
from deepfocus_api import quant_research_api as api,quant_research_store as jobs
from deepfocus_api import quant_evidence_store as evidence,quant_event_service as service
from deepfocus_api import quant_research_engine as legacy
from deepfocus_api import quant_calendar_store as calendars
from deepfocus_api.quant_exchange_schedule import planned_sessions
from deepfocus_api.tests.test_quant_exchange_schedule import reviewed

PDF=b'%PDF-unit-test-only';SHA=hashlib.sha256(PDF).hexdigest()


def sample():
    from deepfocus_api.quant_corporate_actions import CashDividend
    return CashDividend(event_id='fixture',revision=1,symbol='000333',record_date='2024-08-06',ex_date='2024-08-07',
        pay_date='2024-08-08',cash_per_share_gross='1',price_adjustment_per_share='1',
        available_at='2024-08-01T18:00:00+08:00',observed_at='2024-08-01T19:00:00+08:00',
        source_url='https://example.invalid/fixture.pdf',source_sha256=SHA)


def dataset():
    manifest=json.loads((Path(__file__).resolve().parents[3]/'docs/quant-reviewed-exchange-schedule-2023-2026-20261030.json').read_text())
    bars=[]
    for day in planned_sessions(manifest,'2024-01-02','2025-12-31')[:330]:
        p=100+len(bars)*.1;bars.append(dict(date=day,open=p,close=p+.05,high=p+1,low=p-1,volume=10_000_000))
    def payload(rows):return dict(source='unit_test_fixture',fetched_at='2026-01-01T00:00:00+00:00',adjustment='none',volume_unit='shares',is_synthetic=False,bars=rows)
    return {'000333':payload(bars),'000300':payload([{**b,'open':100.,'close':100.,'high':101.,'low':99.} for b in bars])}


@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch,reviewed):
    monkeypatch.setenv('DEEPFOCUS_QUANT_RESEARCH_DB_PATH',str(tmp_path/'qr.sqlite3'))
    calendars.put('a','isolated official-calendar fixture',*reviewed)


def parent(owner='a',*,missing_index=None,extra_day=None):
    ds=dataset();req=dict(name='fixture',symbols=['000333'],benchmark='000300',capital=1000000,start_date='2024-01-02',end_date=ds['000333']['bars'][-1]['date'])
    for payload in ds.values():
        if missing_index is not None:del payload['bars'][missing_index]
        if extra_day:
            payload['bars'].append({**payload['bars'][0],'date':extra_day})
            payload['bars'].sort(key=lambda b:b['date'])
    result=legacy.walk_forward(ds,req['symbols'],req['benchmark'])
    item,_=jobs.create(owner,'parent-key',req,'sealed-legacy-fixture')
    jobs.update(owner,item['id'],status='completed',extra=dict(dataset=ds,result=result,result_digest=legacy.digest(result)))
    return jobs.get(owner,item['id'])


def test_evidence_owner_idempotence_bytes_integrity_and_immutability():
    a=evidence.put('a','fixture',[sample()],{SHA:PDF})
    assert evidence.put('a','fixture',[sample()],{SHA:PDF})==a
    assert evidence.summary('b',a['id']) is None and evidence.listing('b')==[]
    assert not a['coverage_verified'] and a['coverage_kind']=='sample'
    info,ev,source=evidence.load('a',a['id']);assert source=={SHA:PDF}
    assert 'owner' not in info and 'body' not in json.dumps(info)
    with evidence.connect() as c:
        with pytest.raises(sqlite3.IntegrityError):c.execute('UPDATE qr_evidence_bundle SET manifest=? WHERE id=?',('{}',a['id']))
        with pytest.raises(sqlite3.IntegrityError):c.execute('DELETE FROM qr_evidence_source WHERE owner=?',('a',))
    with pytest.raises(ValueError,match='完整覆盖'):evidence.put('a','bad',[sample()],{SHA:PDF},completeness_attested=True)
    with pytest.raises(ValueError,match='缺失或已被修改'):evidence.put('a','bad',[sample()],{SHA:PDF+b'changed'})


@pytest.mark.asyncio
async def test_real_api_event_job_is_owned_bounded_progress_and_cannot_promote():
    p=parent();bundle=evidence.put('a','fixture',[sample()],{SHA:PDF})
    app=FastAPI();app.include_router(api.router);app.dependency_overrides[api.owner]=lambda:'a'
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://fixture') as c:
        listing=await c.get('/api/quant-research/evidence');assert listing.status_code==200 and listing.headers['cache-control']=='no-store'
        route=f"/api/quant-research/experiments/{p['id']}/event-replay"
        payload={'bundle_id':bundle['id'],'calendar_bundle_id':calendars.listing('a')[0]['id'],'request_id':'event-key','knowledge_mode':'historical_reconstruction'}
        r=await c.post(route,json=payload);assert r.status_code==202;r=r.json();identity=r['id']
        retry=await c.post(route,json=payload);assert retry.json()['id']==identity
        task=service.lifecycle._tasks.get(identity)
        if task:await task
        row=(await c.get('/api/quant-research/experiments/'+identity)).json()
        assert row['status']=='completed' and row['kernel_current'] and row['progress']==100
        assert row['request']['kind']=='event_replay' and row['request']['parent_id']==p['id']
        assert row['result']['evidence']['coverage_verified'] is False and not row['result']['gate']['passed']
        assert row['result']['calendar_quality']['status']=='matches_plan'
        assert row['result']['calendar_evidence']['id']==payload['calendar_bundle_id']
        assert any('已重放' in e['stage'] for e in row['events'])
        assert 'dataset' not in row and 'body' not in row['result']['evidence']
        before=jobs.get('a',p['id'])
        assert (await c.post('/api/quant-research/experiments/'+identity+'/paper')).status_code==422
        assert jobs.get('a',p['id'])==before and jobs.list_records('a',True)==[]
        replay=await c.post('/api/quant-research/experiments/'+identity+'/replay',json={'request_id':'verify-key'})
        assert replay.status_code==202
        verified_id=replay.json()['id']
        task=service.lifecycle._tasks.get(verified_id)
        if task:await task
        verified=jobs.get('a',verified_id)
        assert verified['status']=='completed' and verified['result_digest']==row['result_digest']
        assert verified['stage']=='冻结事件重放核对一致'
        exported=await c.get('/api/quant-research/experiments/'+identity+'/export')
        assert exported.status_code==200 and exported.headers['cache-control']=='no-store'
        assert 'dataset' not in exported.json() and 'body' not in exported.json()['result']['evidence']
        assert (await c.post(route,json={**payload,'complete':True})).status_code==422
        app.dependency_overrides[api.owner]=lambda:'b'
        assert (await c.get('/api/quant-research/evidence/'+bundle['id'])).status_code==404
        assert (await c.get('/api/quant-research/experiments/'+identity)).status_code==404
        assert (await c.post(route,json=payload)).status_code==404


@pytest.mark.asyncio
async def test_anonymous_cannot_access_evidence_or_replay():
    app=FastAPI();app.include_router(api.router)
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://fixture') as c:
        for url in ('/evidence','/evidence/'+'a'*32,'/experiments'):
            assert (await c.get('/api/quant-research'+url)).status_code==401


@pytest.mark.asyncio
async def test_cancel_never_publishes_partial_event_result(monkeypatch):
    p=parent();bundle=evidence.put('a','fixture',[sample()],{SHA:PDF})
    original=service.engine.replay
    def cancel_in_worker(*args,**kwargs):
        running=[r for r in jobs.list_records('a') if r['request'].get('kind')=='event_replay'][0]
        jobs.cancel('a',running['id'])
        return original(*args,**kwargs)
    monkeypatch.setattr(service.engine,'replay',cancel_in_worker)
    r=await service.submit('a',p['id'],bundle['id'],'cancel-key','historical_reconstruction',calendar_bundle_id=calendars.listing('a')[0]['id'])
    task=service.lifecycle._tasks.get(r['id'])
    if task:await task
    row=jobs.get('a',r['id']);assert row['status']=='cancelled' and 'result' not in row


@pytest.mark.asyncio
@pytest.mark.parametrize('missing_index,extra_day',[(0,None),(180,None),(-1,None),(None,'2024-01-06')])
async def test_common_date_gap_or_unplanned_date_is_saved_before_any_orders(monkeypatch,missing_index,extra_day):
    p=parent(missing_index=missing_index,extra_day=extra_day);before=jobs.get('a',p['id'])
    bundle=evidence.put('a','fixture',[sample()],{SHA:PDF});called=[]
    def forbidden(*args,**kwargs):called.append(True);raise AssertionError('Calendar must reject before event orders')
    monkeypatch.setattr(service.engine,'replay',forbidden)
    app=FastAPI();app.include_router(api.router);app.dependency_overrides[api.owner]=lambda:'a'
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://fixture') as c:
        reply=await c.post(f"/api/quant-research/experiments/{p['id']}/event-replay",json={
            'bundle_id':bundle['id'],'calendar_bundle_id':calendars.listing('a')[0]['id'],'request_id':'calendar-reject'})
        assert reply.status_code==202;identity=reply.json()['id']
        task=service.lifecycle._tasks.get(identity)
        if task:await task
        row=jobs.get('a',identity)
        assert row['status']=='failed' and row['error_code']=='calendar_alignment_rejected' and 'result' not in row
        assert not called and row['quality']['annual_plan']['status']=='rejected'
        comparisons=list(row['quality']['annual_plan']['symbols'].values())
        assert len(comparisons)==2 and all(v['status']=='rejected' for v in comparisons)
        assert all(v['unexpected_sessions']==['2024-01-06'] for v in comparisons) if extra_day else all(len(v['missing_planned_sessions'])==1 for v in comparisons)
        export=await c.get('/api/quant-research/experiments/'+identity+'/export')
        assert export.status_code==200 and export.json()['quality']==row['quality']
        assert 'dataset' not in export.json() and 'body' not in export.text
        assert jobs.get('a',p['id'])==before and jobs.list_records('a',True)==[]


@pytest.mark.asyncio
async def test_calendar_is_mandatory_and_other_owner_calendar_is_not_visible(reviewed):
    p=parent();bundle=evidence.put('a','fixture',[sample()],{SHA:PDF});foreign=calendars.put('b','private calendar',*reviewed)
    app=FastAPI();app.include_router(api.router);app.dependency_overrides[api.owner]=lambda:'a'
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://fixture') as c:
        path=f"/api/quant-research/experiments/{p['id']}/event-replay"
        payload={'bundle_id':bundle['id'],'request_id':'mandatory-calendar'}
        assert (await c.post(path,json=payload)).status_code==422
        response=await c.post(path,json={**payload,'calendar_bundle_id':foreign['id']})
        assert response.status_code==404 and 'private calendar' not in response.text
        assert len(jobs.list_records('a'))==1


@pytest.mark.asyncio
async def test_same_request_cannot_silently_switch_bound_calendar(reviewed):
    p=parent();bundle=evidence.put('a','fixture',[sample()],{SHA:PDF})
    first=calendars.listing('a')[0];second=calendars.put('a','another immutable calendar label',*reviewed)
    created=await service.submit('a',p['id'],bundle['id'],'same-calendar-key','historical_reconstruction',calendar_bundle_id=first['id'])
    task=service.lifecycle._tasks.get(created['id'])
    if task:await task
    before=jobs.get('a',created['id'])
    with pytest.raises(ValueError,match='同一提交标识'):
        await service.submit('a',p['id'],bundle['id'],'same-calendar-key','historical_reconstruction',calendar_bundle_id=second['id'])
    assert jobs.get('a',created['id'])==before and len(jobs.list_records('a'))==2


@pytest.mark.asyncio
@pytest.mark.parametrize('tamper',['calendar','parent_request'])
async def test_worker_rechecks_frozen_calendar_and_parent_request_before_orders(monkeypatch,tamper):
    p=parent();bundle=evidence.put('a','fixture',[sample()],{SHA:PDF});original=service.reference;calls=[];orders=[]
    def changed(*args):
        values=list(original(*args));calls.append(True)
        if len(calls)>1:
            if tamper=='calendar':values[2]={**values[2],'fingerprint':'changed'}
            else:values[0]={**values[0],'request':{**values[0]['request'],'name':'changed after admission'}}
        return tuple(values)
    def forbidden(*args,**kwargs):orders.append(True);raise AssertionError('No orders with changed frozen input')
    monkeypatch.setattr(service,'reference',changed);monkeypatch.setattr(service.engine,'replay',forbidden)
    created=await service.submit('a',p['id'],bundle['id'],'bound-calendar-'+tamper,'historical_reconstruction',calendar_bundle_id=calendars.listing('a')[0]['id'])
    task=service.lifecycle._tasks.get(created['id'])
    if task:await task
    row=jobs.get('a',created['id'])
    assert row['status']=='failed' and '冻结引用' in row['stage'] and not orders and 'result' not in row


def test_registry_backup_restore_retains_sources_and_scope(tmp_path):
    b=evidence.put('a','fixture',[sample()],{SHA:PDF});backup=tmp_path/'backup.sqlite3'
    with evidence.connect() as c:
        with sqlite3.connect(backup) as target:c.backup(target)
    with sqlite3.connect(backup) as c:
        assert c.execute('SELECT fingerprint FROM qr_evidence_bundle WHERE id=? AND owner=?',(b['id'],'a')).fetchone()[0]==b['fingerprint']
        assert hashlib.sha256(c.execute('SELECT body FROM qr_evidence_source WHERE owner=? AND sha=?',('a',SHA)).fetchone()[0]).hexdigest()==SHA
        assert c.execute('SELECT count(*) FROM qr_evidence_bundle WHERE owner=?',('b',)).fetchone()[0]==0
