import asyncio,base64,hashlib,json,sqlite3,threading
import pytest
from fastapi import FastAPI,HTTPException
from httpx import AsyncClient,ASGITransport
from deepfocus_api import quant_research_api as api,quant_model_cost_read as read,quant_model_cost_api as routes,quant_model_cost_store as store,quant_share_api as shared
from deepfocus_api.tests.test_quant_model_cost_store import original


@pytest.fixture
def records(tmp_path,monkeypatch,original):
    path=tmp_path/'research.sqlite3';monkeypatch.setenv('DEEPFOCUS_QUANT_RESEARCH_DB_PATH',str(path))
    receipt=store.put('a','<img src=x onerror=bad> archive',original)
    return path,receipt,original


def application(owner='a'):
    app=FastAPI();app.include_router(api.router)
    if owner is not None:app.dependency_overrides[api.owner]=lambda:owner
    return app


@pytest.mark.asyncio
async def test_readonly_projections_never_replay_write_or_return_raw_input(records,monkeypatch):
    path,receipt,_=records;before=path.read_bytes()
    def forbidden(*a,**kw):raise AssertionError('HTTP GET must not compute or initialize storage')
    monkeypatch.setattr(store,'compute',forbidden);monkeypatch.setattr(store,'connect',forbidden)
    async with AsyncClient(transport=ASGITransport(app=application()),base_url='http://test') as c:
        listing=await c.get('/api/quant-research/model-cost-archives');assert listing.status_code==200
        detail=await c.get('/api/quant-research/model-cost-archives/'+receipt['id']);assert detail.status_code==200,detail.text
        assert detail.headers['cache-control']=='no-store'
        value=detail.json();assert value['experiments'][0]['fills']>0 and value['experiments'][0]['exact_conservation_verified']
        assert value['verification_level']=='stored_inputs_and_report_integrity' and not value['promotion_allowed']
        for secret in ['dataset','source_pins','reference_open','model_cashflow_price','receipts','cash_cents','input_sha256','source_url','positions']:
            assert secret not in detail.text
        assert (await c.post('/api/quant-research/model-cost-archives',json={})).status_code==405
        assert (await c.post('/api/quant-research/model-cost-archives/'+receipt['id']+'/replay')).status_code==404
        assert (await c.post('/api/quant-research/model-cost-archives/'+receipt['id']+'/paper')).status_code==404
    assert path.read_bytes()==before


@pytest.mark.asyncio
async def test_foreign_and_anonymous_users_cannot_read_archives(records):
    _,receipt,_=records
    async with AsyncClient(transport=ASGITransport(app=application('b')),base_url='http://test') as c:
        assert (await c.get('/api/quant-research/model-cost-archives')).json()=={'items':[],'next_cursor':None}
        denied=await c.get('/api/quant-research/model-cost-archives/'+receipt['id']);assert denied.status_code==404
    async with AsyncClient(transport=ASGITransport(app=application(None)),base_url='http://test') as c:
        assert (await c.get('/api/quant-research/model-cost-archives')).status_code==401


@pytest.mark.asyncio
async def test_missing_database_is_empty_without_creation(tmp_path,monkeypatch):
    path=tmp_path/'missing.sqlite3';monkeypatch.setenv('DEEPFOCUS_QUANT_RESEARCH_DB_PATH',str(path))
    async with AsyncClient(transport=ASGITransport(app=application()),base_url='http://test') as c:
        assert (await c.get('/api/quant-research/model-cost-archives')).json()['items']==[]
        assert (await c.get('/api/quant-research/model-cost-archives/unknown')).status_code==404
    assert not path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('damage',['fingerprint','source','incomplete_schema'])
async def test_corruption_is_not_disguised_as_empty(records,damage):
    path,receipt,_=records;c=sqlite3.connect(path)
    try:
        if damage=='fingerprint':
            c.execute('DROP TRIGGER qr_model_cost_archive_update');c.execute('UPDATE qr_model_cost_archive SET fingerprint=?',('0'*64,))
        elif damage=='source':
            c.execute('DROP TRIGGER qr_model_cost_input_update');c.execute('UPDATE qr_model_cost_input SET body=?',(b'[]',))
        else:c.execute('DROP TABLE qr_model_cost_input')
        c.commit()
    finally:c.close()
    async with AsyncClient(transport=ASGITransport(app=application()),base_url='http://test') as c:
        assert (await c.get('/api/quant-research/model-cost-archives')).status_code==409
        assert (await c.get('/api/quant-research/model-cost-archives/'+receipt['id'])).status_code==409


@pytest.mark.asyncio
async def test_stale_archive_remains_metadata_only_without_current_verifier(records,monkeypatch):
    path,receipt,_=records;c=sqlite3.connect(path)
    try:
        c.execute('DROP TRIGGER qr_model_cost_archive_update');v=json.loads(c.execute('SELECT payload FROM qr_model_cost_archive').fetchone()[0])
        v['source_pins']['quant_model_cost_store.py']='0'*64;v['report']['source_pins']=v['source_pins']
        v['request_hash']=store.engine.digest({'label':v['label'],'input_sha':v['input_sha256'],'source_pins':v['source_pins']})
        c.execute('UPDATE qr_model_cost_archive SET payload=?,fingerprint=?,input_hash=?',(json.dumps(v),store.engine.digest(v),v['request_hash']));c.commit()
    finally:c.close()
    def forbidden(*a,**kw):raise AssertionError('Old source must not be verified as current')
    monkeypatch.setattr(store,'verify_row',forbidden)
    async with AsyncClient(transport=ASGITransport(app=application()),base_url='http://test') as c:
        r=await c.get('/api/quant-research/model-cost-archives/'+receipt['id']);assert r.status_code==200,r.text
        assert r.json()['experiments'] is None and r.json()['verification_level']=='metadata_only' and not r.json()['source_version_current']


def test_bounded_pagination_and_invalid_cursors(records):
    _,_,original=records;store.put('a','second',original)
    first=read.listing('a',limit=1);second=read.listing('a',cursor=first['next_cursor'],limit=1)
    assert first['items'][0]['id']!=second['items'][0]['id'] and second['next_cursor'] is None
    with pytest.raises(ValueError):read.listing('a',cursor='broken')
    with pytest.raises(ValueError):read.listing('a',limit=11)


@pytest.mark.asyncio
async def test_workspace_is_hashed_and_does_not_embed_private_data():
    async with AsyncClient(transport=ASGITransport(app=application(None)),base_url='http://test') as c:
        r=await c.get('/api/quant-research/workspace?view=costs');assert r.status_code==200
        script=r.text.split('<script>')[1].split('</script>')[0]
        assert 'sha256-'+base64.b64encode(hashlib.sha256(script.encode()).digest()).decode() in r.headers['content-security-policy']
        assert r.text.count('<script>')==1 and r.headers['cache-control']=='no-store'
        assert '旧账务严格对账' in r.text and 'data-promote' not in r.text and 'data-order' not in r.text


@pytest.mark.asyncio
async def test_cost_and_share_requests_share_slots_until_cancelled_worker_exits():
    entered=threading.Event();release=threading.Event()
    def blocking(owner):entered.set();release.wait(5);return {}
    first=asyncio.create_task(routes.read('a',blocking));await asyncio.to_thread(entered.wait,2)
    second=asyncio.create_task(shared.read('a',blocking))
    try:
        for _ in range(100):
            if len(shared._reading)==2:break
            await asyncio.sleep(.005)
        first.cancel();await asyncio.sleep(.02)
        assert not first.done() and first in shared._reading
        with pytest.raises(HTTPException) as e:await routes.read('a',blocking)
        assert e.value.status_code==429
    finally:
        release.set();await asyncio.gather(first,second,return_exceptions=True)
    assert not shared._reading
