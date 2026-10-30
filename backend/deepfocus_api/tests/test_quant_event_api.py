import asyncio
from dataclasses import replace
from datetime import date,timedelta
import hashlib
import json
import sqlite3
import pytest
from fastapi import FastAPI
from httpx import AsyncClient,ASGITransport
from deepfocus_api import quant_research_api as api,quant_research_store as jobs
from deepfocus_api import quant_evidence_store as evidence,quant_event_service as service
from deepfocus_api import quant_research_engine as legacy

PDF=b'%PDF-unit-test-only';SHA=hashlib.sha256(PDF).hexdigest()


def sample():
    from deepfocus_api.quant_corporate_actions import CashDividend
    return CashDividend(event_id='fixture',revision=1,symbol='000333',record_date='2024-08-06',ex_date='2024-08-07',
        pay_date='2024-08-08',cash_per_share_gross='1',price_adjustment_per_share='1',
        available_at='2024-08-01T18:00:00+08:00',observed_at='2024-08-01T19:00:00+08:00',
        source_url='https://example.invalid/fixture.pdf',source_sha256=SHA)


def dataset():
    bars=[];d=date(2024,1,2)
    while len(bars)<330:
        if d.weekday()<5:
            p=100+len(bars)*.1;bars.append(dict(date=d.isoformat(),open=p,close=p+.05,high=p+1,low=p-1,volume=10_000_000))
        d+=timedelta(days=1)
    def payload(rows):return dict(source='unit_test_fixture',fetched_at='2026-01-01',adjustment='none',volume_unit='shares',is_synthetic=False,bars=rows)
    return {'000333':payload(bars),'000300':payload([{**b,'open':100.,'close':100.,'high':101.,'low':99.} for b in bars])}


@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):monkeypatch.setenv('DEEPFOCUS_QUANT_RESEARCH_DB_PATH',str(tmp_path/'qr.sqlite3'))


def parent(owner='a'):
    ds=dataset();req=dict(name='fixture',symbols=['000333'],benchmark='000300',capital=1000000,start_date='2024-01-02',end_date='2025-04-06')
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
        payload={'bundle_id':bundle['id'],'request_id':'event-key','knowledge_mode':'historical_reconstruction'}
        r=await c.post(route,json=payload);assert r.status_code==202;r=r.json();identity=r['id']
        retry=await c.post(route,json=payload);assert retry.json()['id']==identity
        task=service.lifecycle._tasks.get(identity)
        if task:await task
        row=(await c.get('/api/quant-research/experiments/'+identity)).json()
        assert row['status']=='completed' and row['kernel_current'] and row['progress']==100
        assert row['request']['kind']=='event_replay' and row['request']['parent_id']==p['id']
        assert row['result']['evidence']['coverage_verified'] is False and not row['result']['gate']['passed']
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
    r=await service.submit('a',p['id'],bundle['id'],'cancel-key','historical_reconstruction')
    task=service.lifecycle._tasks.get(r['id'])
    if task:await task
    row=jobs.get('a',r['id']);assert row['status']=='cancelled' and 'result' not in row


def test_registry_backup_restore_retains_sources_and_scope(tmp_path):
    b=evidence.put('a','fixture',[sample()],{SHA:PDF});backup=tmp_path/'backup.sqlite3'
    with evidence.connect() as c:
        with sqlite3.connect(backup) as target:c.backup(target)
    with sqlite3.connect(backup) as c:
        assert c.execute('SELECT fingerprint FROM qr_evidence_bundle WHERE id=? AND owner=?',(b['id'],'a')).fetchone()[0]==b['fingerprint']
        assert hashlib.sha256(c.execute('SELECT body FROM qr_evidence_source WHERE owner=? AND sha=?',('a',SHA)).fetchone()[0]).hexdigest()==SHA
        assert c.execute('SELECT count(*) FROM qr_evidence_bundle WHERE owner=?',('b',)).fetchone()[0]==0
