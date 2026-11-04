import asyncio
import copy
import pytest
from fastapi import FastAPI
from httpx import ASGITransport,AsyncClient
from deepfocus_api import quant_research_api as api,quant_research as svc,quant_research_store as store
from deepfocus_api.tests.test_quant_config import data


@pytest.mark.asyncio
async def test_actual_api_worker_freezes_and_uses_configuration_with_owner_isolation(tmp_path,monkeypatch):
    monkeypatch.setenv('DEEPFOCUS_QUANT_RESEARCH_DB_PATH',str(tmp_path/'qr.sqlite3'))
    ds=data()
    async def fetch(*args):return copy.deepcopy(ds)
    monkeypatch.setattr(svc,'fetch_dataset',fetch)
    app=FastAPI();app.include_router(api.router);app.dependency_overrides[api.owner]=lambda:'a'
    request=dict(request_id='config-http-integration',name='configuration QA',symbols=['600519'],benchmark='000300',capital=1000000,start_date=ds['600519']['bars'][0]['date'],end_date=ds['600519']['bars'][-1]['date'],configuration={'lookbacks':[5,15],'thresholds':[0.01],'rebalance_days':5,'minimum_commission':50})
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://isolated') as client:
        r=await client.post('/api/quant-research/experiments',json=request);assert r.status_code==202,r.text
        identity=r.json()['id'];task=svc._tasks.get(identity)
        if task:await asyncio.wait_for(asyncio.shield(task),20)
        job=(await client.get('/api/quant-research/experiments/'+identity)).json()
        assert job['status']=='completed',job.get('stage')
        assert job['request']['configuration']['minimum_commission']==50
        assert job['result']['protocol']['configuration']['lookbacks']==[5,15]
        assert job['result']['holdout']['fills'] and all(f['fee']>=50 for f in job['result']['holdout']['fills'])
        assert job['result']['protocol']['holdout_days']==63 and not job['result']['gate']['passed']
        repeated=await client.post('/api/quant-research/experiments',json=request);assert repeated.json()['id']==identity
        changed=await client.post('/api/quant-research/experiments',json={**request,'configuration':{'rebalance_days':10}});assert changed.status_code==422
        export=await client.get('/api/quant-research/experiments/'+identity+'/export');assert export.json()['request']['configuration']==job['request']['configuration'] and 'dataset' not in export.json()
        paper=await client.post('/api/quant-research/experiments/'+identity+'/paper');assert paper.status_code==422
        app.dependency_overrides[api.owner]=lambda:'b'
        assert (await client.get('/api/quant-research/experiments/'+identity)).status_code==404
        assert (await client.get('/api/quant-research/experiments')).json()['items']==[]
        app.dependency_overrides.clear()
        assert (await client.get('/api/quant-research/experiments/'+identity)).status_code==401


@pytest.mark.asyncio
@pytest.mark.parametrize('bad',[{'holdout_days':1},{'slippage_bps':0},{'gross_weight':1.0},{'max_holdings':True}])
async def test_api_rejects_relaxed_or_invalid_configuration_before_creation(tmp_path,monkeypatch,bad):
    monkeypatch.setenv('DEEPFOCUS_QUANT_RESEARCH_DB_PATH',str(tmp_path/'qr.sqlite3'))
    app=FastAPI();app.include_router(api.router);app.dependency_overrides[api.owner]=lambda:'a'
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://isolated') as client:
        response=await client.post('/api/quant-research/experiments',json={'symbols':['600519'],'configuration':bad})
        assert response.status_code==422 and store.list_records('a')==[]
