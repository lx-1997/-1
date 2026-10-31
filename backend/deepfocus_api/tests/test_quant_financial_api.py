import asyncio,base64,hashlib,json,sqlite3,threading
import pytest
from fastapi import FastAPI,HTTPException
from httpx import AsyncClient,ASGITransport
from deepfocus_api import quant_research_api as api,quant_financial_read as read,quant_financial_api as routes,quant_share_api as shared
from deepfocus_api.tests.test_quant_financial_fact_derived import clock,setup,endpoints
from deepfocus_api.tests.test_quant_financial_store import ledger


def app(owner='a'):
    result=FastAPI();result.include_router(api.router)
    if owner is not None:result.dependency_overrides[api.owner]=lambda:owner
    return result


def params(**kw):
    args=dict(issuer_id='cninfo:9900002009',symbol='002109',period_end='2025-03-31',metric='consolidated_net_profit',as_of='2026-05-01T00:00:00+08:00',mode='historical_reconstruction',begin_report_period_end='2024-03-31',begin_column='current_period',end_report_period_end='2025-03-31',end_column='current_period')
    args.update(kw);return args


@pytest.mark.asyncio
async def test_gets_owner_metadata_and_exact_derived_values_without_writes_or_source_payload(clock,monkeypatch):
    _,p,_=setup();before=clock.read_bytes()
    def forbidden(*a,**kw):raise AssertionError('GET must not initialize or modify financial storage')
    monkeypatch.setattr(read.facts.base,'connect',forbidden);monkeypatch.setattr(read.facts,'put',forbidden)
    async with AsyncClient(transport=ASGITransport(app=app()),base_url='http://test') as c:
        listing=await c.get('/api/quant-research/financial-extractions');assert listing.status_code==200
        detail=await c.get('/api/quant-research/financial-extractions/'+p['id']);assert detail.status_code==200
        assert 'consolidated_net_profit' in detail.text and '-175.00' not in detail.text
        r=await c.get('/api/quant-research/financial-extractions/'+p['id']+'/derive',params=params());assert r.status_code==200,r.text
        value=r.json();assert value['ttm_value']=='-395.00' and value['endpoint_average_assets']['value_exact_yuan']=='105000.015'
        assert value['endpoint_average_assets']['fraction_yuan']=={'numerator':'21000003','denominator':'200'}
        for result in [listing,detail,r]:
            assert result.headers['cache-control']=='no-store'
            for word in ['source_pins','effective_manifest','source_files','source_url','payload','owner','%PDF','dataset']:
                assert '"'+word+'"' not in result.text
        assert (await c.post('/api/quant-research/financial-extractions',json={})).status_code==405
        assert (await c.post('/api/quant-research/financial-extractions/'+p['id']+'/paper')).status_code==404
    assert clock.read_bytes()==before


@pytest.mark.asyncio
async def test_foreign_and_anonymous_empty_or_denied(clock):
    _,p,_=setup()
    async with AsyncClient(transport=ASGITransport(app=app('b')),base_url='http://test') as c:
        assert (await c.get('/api/quant-research/financial-extractions')).json()=={'items':[],'next_cursor':None}
        for path in [p['id'],p['id']+'/derive']:
            r=await c.get('/api/quant-research/financial-extractions/'+path,params=params() if path.endswith('derive') else {})
            assert r.status_code==404
    async with AsyncClient(transport=ASGITransport(app=app(None)),base_url='http://test') as c:
        assert (await c.get('/api/quant-research/financial-extractions')).status_code==401


@pytest.mark.asyncio
async def test_missing_database_and_initial_parent_only_schema_never_create_tables(tmp_path,monkeypatch):
    path=tmp_path/'missing.sqlite3';monkeypatch.setenv('DEEPFOCUS_QUANT_RESEARCH_DB_PATH',str(path))
    async with AsyncClient(transport=ASGITransport(app=app()),base_url='http://test') as c:
        assert (await c.get('/api/quant-research/financial-extractions')).json()['items']==[]
        assert (await c.get('/api/quant-research/financial-extractions/missing/derive',params=params())).status_code==404
    assert not path.exists()


@pytest.mark.asyncio
async def test_as_recorded_gate_hides_nested_values(clock):
    _,p,_=setup()
    async with AsyncClient(transport=ASGITransport(app=app()),base_url='http://test') as c:
        r=await c.get('/api/quant-research/financial-extractions/'+p['id']+'/derive',params=params(mode='as_recorded',as_of='2026-10-02T23:59:59+08:00'))
        assert r.status_code==200 and r.json()['result'] is None and r.json()['ttm_value'] is None


@pytest.mark.asyncio
@pytest.mark.parametrize('change',[{'metric':'total_assets'},{'metric':'unknown'},{'as_of':'2099-01-01T00:00:00+08:00'},{'as_of':'2025-01-01T00:00:00'}, {'period_end':'2025-02-28'},{'end_column':'previous_year_end'},{'mode':'latest'}])
async def test_invalid_user_query_422_not_data_corruption(clock,change):
    _,p,_=setup()
    async with AsyncClient(transport=ASGITransport(app=app()),base_url='http://test') as c:
        r=await c.get('/api/quant-research/financial-extractions/'+p['id']+'/derive',params=params(**change));assert r.status_code==422,r.text


@pytest.mark.asyncio
@pytest.mark.parametrize('damage',['packet','source','schema'])
async def test_corruption_refused_not_disguised_as_empty(clock,damage):
    _,p,_=setup();c=sqlite3.connect(clock)
    try:
        if damage=='packet':c.execute('drop trigger qr_financial_fact_packet_update');c.execute('update qr_financial_fact_packet set fingerprint=?',('0'*64,))
        elif damage=='source':c.execute('drop trigger qr_financial_source_update');c.execute('update qr_financial_source set body=?',(b'bad',))
        else:c.execute('drop table qr_financial_source')
        c.commit()
    finally:c.close()
    async with AsyncClient(transport=ASGITransport(app=app()),base_url='http://test') as c:
        assert (await c.get('/api/quant-research/financial-extractions')).status_code==409
        r=await c.get('/api/quant-research/financial-extractions/'+p['id']+'/derive',params=params());assert r.status_code in [409,503]
        assert '-395.00' not in r.text


def test_listing_pagination_and_no_financial_amount_metadata(clock):
    parent,p,fs=setup();new=list(fs);new[0]={**fs[0],'source_page':4}
    note={**{k:fs[0][k] for k in ('document_id','metric','column')},'reason':'Synthetic explicit page extraction correction'}
    read.facts.put('a',parent['id'],'second extraction',new,supersedes_packet_id=p['id'],correction_notes=[note])
    one=read.listing('a',limit=1);two=read.listing('a',limit=1,cursor=one['next_cursor'])
    assert one['items'][0]['id']!=two['items'][0]['id'] and two['next_cursor'] is None
    with pytest.raises(ValueError):read.listing('a',cursor='bad')
    with pytest.raises(ValueError):read.listing('a',limit=6)


@pytest.mark.asyncio
async def test_financial_workspace_has_csp_and_static_no_private_facts():
    async with AsyncClient(transport=ASGITransport(app=app(None)),base_url='http://test') as c:
        r=await c.get('/api/quant-research/workspace?view=financials');assert r.status_code==200
        script=r.text.split('<script>')[1].split('</script>')[0]
        assert 'sha256-'+base64.b64encode(hashlib.sha256(script.encode()).digest()).decode() in r.headers['content-security-policy']
        assert r.text.count('<script>')==1 and r.headers['cache-control']=='no-store'
        assert 'auth_token' in r.text and 'data-order' not in r.text and 'value_exact_yuan' in r.text


@pytest.mark.asyncio
async def test_financial_and_existing_archive_routes_share_limits_until_worker_exit():
    entered=threading.Event();release=threading.Event()
    def block(owner):entered.set();release.wait(5);return {}
    one=asyncio.create_task(routes.read('a',block));await asyncio.to_thread(entered.wait,2)
    two=asyncio.create_task(shared.read('a',block))
    try:
        for _ in range(100):
            if len(shared._reading)==2:break
            await asyncio.sleep(.005)
        one.cancel();await asyncio.sleep(.02);assert not one.done() and one in shared._reading
        with pytest.raises(HTTPException) as e:await routes.read('a',block)
        assert e.value.status_code==429
    finally:release.set();await asyncio.gather(one,two,return_exceptions=True)
    assert not shared._reading


@pytest.mark.asyncio
async def test_parent_only_schema_remains_without_extraction_table(clock):
    from deepfocus_api.tests.test_quant_financial_flows import fixture
    read.facts.base.put('a','parent only',*fixture());before=clock.read_bytes()
    async with AsyncClient(transport=ASGITransport(app=app()),base_url='http://test') as c:
        assert (await c.get('/api/quant-research/financial-extractions')).json()['items']==[]
        assert (await c.get('/api/quant-research/financial-extractions/missing/derive',params=params())).status_code==404
    assert clock.read_bytes()==before
    c=sqlite3.connect(clock)
    try:assert not c.execute("select 1 from sqlite_master where name='qr_financial_fact_packet'").fetchall()
    finally:c.close()
