import asyncio
import threading
import pytest
from httpx import AsyncClient, ASGITransport
from deepfocus_api import quant_financial_read as archive, quant_profitability_feature as feature, quant_share_api as shared
from deepfocus_api.tests.test_quant_financial_api import app,params
from deepfocus_api.tests.test_quant_financial_fact_derived import clock,ledger,setup


@pytest.mark.asyncio
async def test_one_actual_derivation_and_private_exact_feature_response(clock,monkeypatch):
    _,packet,_=setup();before=clock.read_bytes();calls=[];real=feature.derived.review
    def counted(*args,**kwargs):calls.append(threading.get_ident());return real(*args,**kwargs)
    monkeypatch.setattr(feature.derived,'review',counted)
    async with AsyncClient(transport=ASGITransport(app=app()),base_url='http://test') as client:
        path='/api/quant-research/financial-extractions/'+packet['id']
        response=await client.get(path+'/profitability',params=params())
        assert response.status_code==200,response.text
        assert len(calls)==1 and calls[0]!=threading.get_ident()
        result=response.json();assert result['feature']['exact_fraction']=={'numerator':'-79000','denominator':'21000003'}
        assert result['feature']['display_percent']=='-0.376190' and not result['usable_for_strategy']
        baseline=await client.get(path+'/derive',params=params());assert result['inputs']==baseline.json()
        assert response.headers['cache-control']=='no-store'
        for key in ['source_pins','effective_manifest','source_files','source_url','payload','owner','%PDF','dataset']:
            assert '"'+key+'"' not in response.text
        assert (await client.post(path+'/profitability',json={})).status_code==405
        assert (await client.post(path+'/profitability/paper',json={})).status_code==404
    assert clock.read_bytes()==before


@pytest.mark.asyncio
async def test_default_recording_boundary_and_foreign_or_missing_archive(clock):
    _,packet,_=setup();path='/api/quant-research/financial-extractions/'+packet['id']+'/profitability'
    args=params(as_of='2026-10-02T23:59:59+08:00');args.pop('mode')
    async with AsyncClient(transport=ASGITransport(app=app()),base_url='http://test') as client:
        response=await client.get(path,params=args);assert response.status_code==200
        value=response.json();assert value['inputs']['request']['mode']=='as_recorded'
        assert value['status']=='unavailable' and value['feature']['exact_fraction'] is None
        assert value['inputs']['result'] is None
    for owner,status in [('b',404),(None,401)]:
        async with AsyncClient(transport=ASGITransport(app=app(owner)),base_url='http://test') as client:
            response=await client.get(path,params=params());assert response.status_code==status
            assert 'exact_fraction' not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize('case',['metric','missing-endpoint','wrong-date','future'])
async def test_invalid_feature_request_is_rejected_before_work(clock,monkeypatch,case):
    _,packet,_=setup();args=params()
    if case=='metric':args['metric']='minority_net_profit'
    if case=='missing-endpoint':args.pop('begin_column')
    if case=='wrong-date':args['begin_report_period_end']='2025-03-31'
    if case=='future':args['as_of']='2099-01-01T00:00:00+08:00'
    def forbidden(*a,**k):raise AssertionError('Invalid request must not enter a data worker')
    monkeypatch.setattr(archive,'profitability',forbidden)
    async with AsyncClient(transport=ASGITransport(app=app()),base_url='http://test') as client:
        response=await client.get('/api/quant-research/financial-extractions/'+packet['id']+'/profitability',params=args)
        assert response.status_code==422


@pytest.mark.asyncio
async def test_profitability_route_retains_shared_read_slots_after_cancellation(clock,monkeypatch):
    entered=threading.Event();release=threading.Event();calls=[]
    def blocked(owner,identity,**kwargs):calls.append(owner);entered.set();release.wait(3);return {}
    monkeypatch.setattr(archive,'profitability',blocked)
    async with AsyncClient(transport=ASGITransport(app=app()),base_url='http://test') as client:
        path='/api/quant-research/financial-extractions/owned/profitability'
        one=asyncio.create_task(client.get(path,params=params()))
        two=None
        try:
            assert await asyncio.to_thread(entered.wait,1)
            two=asyncio.create_task(client.get(path,params=params()))
            for _ in range(100):
                if len(shared._reading)==2:break
                await asyncio.sleep(.005)
            assert len(shared._reading)==2
            one.cancel();await asyncio.sleep(.01);assert not one.done()
            response=await client.get(path,params=params());assert response.status_code==429
            assert response.headers['cache-control']=='no-store' and calls==['a','a']
        finally:
            release.set();await asyncio.gather(*[t for t in [one,two] if t is not None],return_exceptions=True)
    assert not shared._reading
