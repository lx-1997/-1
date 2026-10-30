import asyncio
import copy
from datetime import date, timedelta
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from deepfocus_api import quant_research_engine as e, quant_research_store as st, quant_research as svc
from deepfocus_api import quant_research_api as api


def dataset(count=420, step=.05):
    bars=[]; d=date(2023,1,2)
    while len(bars)<count:
        if d.weekday()<5:
            p=20+len(bars)*step
            bars.append({'date':d.isoformat(),'open':p,'high':p+.3,'low':p-.2,'close':p+.1,'volume':1_000_000})
        d+=timedelta(days=1)
    def payload(rows): return {'bars':rows,'source':'unit_test_fixture','fetched_at':'2026-01-01T00:00:00+00:00','is_synthetic':False,'adjustment':'none','volume_unit':'shares'}
    return {'600519':payload(bars),'000300':payload([{**b,'open':20.,'high':20.1,'low':19.9,'close':20.} for b in bars])}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('DEEPFOCUS_QUANT_RESEARCH_DB_PATH',str(tmp_path/'research.sqlite3'))


def request(): return {'name':'验证','symbols':['600519'],'benchmark':'000300','start_date':'2023-01-01','end_date':'2024-08-09','capital':1000000}


def test_replay_deterministic_and_cash_reconciles():
    data=dataset();a=e.replay(data,['600519'],e.CANDIDATES[0]);b=e.replay(data,['600519'],e.CANDIDATES[0])
    assert a==b and a['fills']
    cash=1_000_000
    for f in a['fills']:
        assert f['signal_date']<f['date']
        assert f['qty']%100==0 and f['fee']>=5
        cash+=(-f['qty']*f['price'] if f['side']=='buy' else f['qty']*f['price'])-f['fee'];cash=round(cash,2)
    assert cash==a['state']['cash']>=0
    assert all(x['exposure_pct']<=21 for x in a['curve'])


def test_future_changes_do_not_change_past_fills():
    data=dataset();cutoff=data['600519']['bars'][200]['date']
    expected=e.replay(data,['600519'],e.CANDIDATES[0],end=cutoff)
    for b in data['600519']['bars'][201:]:
        for k in ('open','high','low','close'):b[k]*=2
    assert e.replay(data,['600519'],e.CANDIDATES[0],end=cutoff)==expected


def test_current_close_does_not_drive_same_day_fill():
    data=dataset(100);d=data['600519']['bars'][11]['date']
    a=e.replay(data,['600519'],e.CANDIDATES[0],end=d)
    data['600519']['bars'][11].update(close=100,high=101)
    b=e.replay(data,['600519'],e.CANDIDATES[0],end=d)
    assert a['fills']==b['fills']


def test_incremental_replay_matches_batch():
    data=dataset();split=data['600519']['bars'][200]['date']
    first=e.replay(data,['600519'],e.CANDIDATES[0],end=split)
    second=e.replay(data,['600519'],e.CANDIDATES[0],state=first['state'])
    whole=e.replay(data,['600519'],e.CANDIDATES[0])
    assert second['state']==whole['state']
    assert first['fills']+second['fills']==whole['fills']
    assert first['curve']+second['curve']==whole['curve']
    assert not e.replay(data,['600519'],e.CANDIDATES[0],state=second['state'])['fills']


@pytest.mark.parametrize('mutation',[lambda p:p.update(is_synthetic=True),lambda p:p.update(adjustment='qfq'),lambda p:p.update(volume_unit='hands'),lambda p:p['bars'][0].update(close=float('nan')),lambda p:p['bars'].append(p['bars'][-1])])
def test_bad_data_fails_closed(mutation):
    data=dataset();mutation(data['600519'])
    with pytest.raises((ValueError,TypeError)):e.validate_dataset(data)


def test_limits_and_suspension_block_fills():
    data=dataset(30);b=data['600519']['bars'][11];b.update(open=40,close=40,high=40,low=40)
    r=e.replay(data,['600519'],e.CANDIDATES[0],end=b['date'])
    assert not r['fills'] and any(x['reason']=='price_limit' for x in r['blocked'])
    b.update(open=21,close=21,high=21,low=21,volume=0)
    r=e.replay(data,['600519'],e.CANDIDATES[0],end=b['date'])
    assert not r['fills'] and any(x['reason']=='suspended_or_missing_volume' for x in r['blocked'])


def test_previous_volume_cap_not_future_volume():
    data=dataset(30);data['600519']['bars'][10]['volume']=10_000
    data['600519']['bars'][11]['volume']=100_000_000
    r=e.replay(data,['600519'],e.CANDIDATES[0],end=data['600519']['bars'][11]['date'])
    assert sum(f['qty'] for f in r['fills'])<=100


def test_walk_forward_holdout_is_never_selection_data():
    data=dataset();a=e.walk_forward(data,['600519'],'000300');start=a['holdout']['curve'][0]['date']
    for b in data['600519']['bars']:
        if b['date']>=start:
            for k in ('open','high','low','close'):b[k]*=.5
    b=e.walk_forward(data,['600519'],'000300')
    assert a['parameters']==b['parameters'] and a['selection_trials']==b['selection_trials']
    assert a['folds']==b['folds'] and a['holdout']!=b['holdout']
    assert all(f['train_end']<f['test_start']<=f['test_end']<start for f in a['folds'])
    assert len({x['date'] for x in a['oos']['curve']})==len(a['oos']['curve'])
    assert a['gate']['commercial_data_licensed'] is False


def test_insufficient_history_is_not_a_success():
    with pytest.raises(ValueError,match='300'):e.walk_forward(dataset(200),['600519'],'000300')


def test_idempotency_ownership_and_terminal_immutability():
    r,fresh=st.create('a','same-key',request(),'kernel');assert fresh
    assert st.create('a','same-key',request(),'kernel')[0]['id']==r['id']
    assert st.get('b',r['id']) is None and st.list_records('b')==[]
    with pytest.raises(ValueError):st.create('a','same-key',{**request(),'capital':2000000},'kernel')
    st.cancel('a',r['id']);assert not st.update('a',r['id'],status='completed',extra={'result':'late'})
    assert st.get('a',r['id'])['status']=='cancelled'


def test_concurrency_limits_persist_and_paper_cas():
    st.create('a','key-one',request(),'kernel');st.create('a','key-two',request(),'kernel')
    with pytest.raises(RuntimeError):st.create('a','key-three',request(),'kernel')
    p=st.create_paper('a','experiment',{'state':{'cash':10000}})
    assert st.create_paper('a','experiment',{})['id']==p['id']
    st.save_paper('a',p['id'],0,{'state':{'cash':10001}})
    with pytest.raises(RuntimeError):st.save_paper('a',p['id'],0,{'state':{'cash':0}})
    assert st.get('a',p['id'],True)['state']['cash']==10001
    assert st.get('b',p['id'],True) is None


@pytest.mark.asyncio
async def test_worker_runs_kernel_persists_and_promotes(monkeypatch):
    async def fetch(*_):return dataset()
    monkeypatch.setattr(svc,'fetch_dataset',fetch)
    job=svc.submit('a','research-job',request());await svc._tasks[job['id']]
    final=st.get('a',job['id']);assert final['status']=='completed'
    assert e.digest(final['result'])==final['result_digest']
    assert final['dataset'] and final['result']['holdout']['fills']
    assert svc.promote('a',job['id'])['state']['positions']=={}


@pytest.mark.asyncio
async def test_cancel_during_fetch_never_finishes(monkeypatch):
    entered=asyncio.Event();release=asyncio.Event()
    async def fetch(*_):entered.set();await release.wait();return dataset()
    monkeypatch.setattr(svc,'fetch_dataset',fetch)
    job=svc.submit('a','cancel-key',request());task=svc._tasks[job['id']];await entered.wait()
    st.cancel('a',job['id']);release.set();await task
    assert st.get('a',job['id'])['status']=='cancelled' and 'result' not in st.get('a',job['id'])


@pytest.mark.asyncio
async def test_api_owner_export_and_validation():
    app=FastAPI();app.include_router(api.router);app.dependency_overrides[api.require_current_user]=lambda:{'sub':'b'}
    job,_=st.create('a','owner-test',request(),'kernel')
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        assert (await client.get('/api/quant-research/experiments/'+job['id'])).status_code==404
        assert (await client.post('/api/quant-research/experiments',json={'symbols':['600519','600519']})).status_code==422
        app.dependency_overrides[api.require_current_user]=lambda:{'sub':'a'}
        export=await client.get('/api/quant-research/experiments/'+job['id']+'/export')
        assert export.status_code==200 and 'dataset' not in export.json()


@pytest.mark.asyncio
async def test_paper_refresh_is_incremental(monkeypatch):
    async def fetch(*_):return dataset()
    monkeypatch.setattr(svc,'fetch_dataset',fetch);monkeypatch.setattr(svc,'complete_day',lambda:'2024-01-01')
    job=svc.submit('a','paper-key',request());await svc._tasks[job['id']]
    p=svc.promote('a',job['id']);assert p['state']['cursor']=='2024-01-01'
    r=await svc.refresh_paper('a',p['id']);again=await svc.refresh_paper('a',p['id'])
    assert r['fills']==again['fills'] and r['state']==again['state']
    assert all(f['date']>'2024-01-01' for f in r['fills'])
    svc.pause('a',p['id'])
    with pytest.raises(ValueError,match='暂停'):await svc.refresh_paper('a',p['id'])


@pytest.mark.asyncio
async def test_revisions_and_kernel_drift_cannot_rewrite_paper(monkeypatch):
    data=dataset()
    async def fetch(*_):return data
    monkeypatch.setattr(svc,'fetch_dataset',fetch)
    job=svc.submit('a','revision-key',request());await svc._tasks[job['id']]
    p=svc.promote('a',job['id']);snapshot=copy.deepcopy(p['state'])
    data['600519']['bars'][2]['volume']+=1
    with pytest.raises(ValueError,match='修订'):await svc.refresh_paper('a',p['id'])
    assert st.get('a',p['id'],True)['state']==snapshot
    monkeypatch.setattr(svc,'kernel_identity',lambda:'different')
    with pytest.raises(ValueError,match='内核'):await svc.refresh_paper('a',p['id'])


def test_sina_day_field_is_preserved():
    from deepfocus_api.backtest_data import _clean_bars
    rows=_clean_bars([{'day':'2026-10-29','open':'10','high':'11','low':'9','close':'10','volume':'12345'}],'2026-01-01','2026-10-30')
    assert rows==[{'date':'2026-10-29','open':10.,'high':11.,'low':9.,'close':10.,'volume':12345.}]

def test_expired_worker_is_visible_not_stuck_forever():
    from datetime import datetime,timezone
    job,_=st.create('a','expired-key',request(),'kernel')
    with st.connect() as c:
        c.execute('UPDATE qr_experiment SET updated=? WHERE id=?',('2020-01-01T00:00:00+00:00',job['id']))
    st.expire_interrupted()
    assert st.get('a',job['id'])['status']=='failed'
    assert not st.update('a',job['id'],status='completed')

def test_oos_uses_one_continuous_account():
    r=e.walk_forward(dataset(),['600519'],'000300')
    assert r['oos']['capital_resets_per_fold'] is False
    assert r['oos']['observations']==sum(f['metrics']['observations'] for f in r['folds'])

@pytest.mark.asyncio
async def test_api_unauthenticated_and_workspace_shell():
    from fastapi import HTTPException
    app=FastAPI();app.include_router(api.router)
    def denied():raise HTTPException(401,'请登录')
    app.dependency_overrides[api.require_current_user]=denied
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        assert (await client.get('/api/quant-research/experiments')).status_code==401
        shell=await client.get('/api/quant-research/workspace')
        assert shell.status_code==200 and '量化研究工作台' in shell.text
        assert 'auth_token' in shell.text and 'localStorage.setItem' not in shell.text


@pytest.mark.asyncio
async def test_reproduce_uses_sealed_snapshot_without_fetch(monkeypatch):
    async def fetch(*_):return dataset()
    monkeypatch.setattr(svc,'fetch_dataset',fetch)
    first=svc.submit('a','reproduce-base',request());await svc._tasks[first['id']]
    original=st.get('a',first['id'])
    async def forbidden(*_):raise AssertionError('must not refetch')
    monkeypatch.setattr(svc,'fetch_dataset',forbidden)
    repeat=svc.reproduce('a',first['id'],'reproduce-repeat');await svc._tasks[repeat['id']]
    final=st.get('a',repeat['id'])
    assert final['status']=='completed' and final['result_digest']==original['result_digest']
    assert final['stage']=='冻结数据复核一致'
    with pytest.raises(LookupError):svc.reproduce('b',first['id'],'reproduce-stolen')


@pytest.mark.asyncio
async def test_parallel_refresh_has_bounded_admission(monkeypatch):
    entered=asyncio.Event();release=asyncio.Event()
    async def work(*_):entered.set();await release.wait();return {'ok':True}
    monkeypatch.setattr(svc,'_refresh_paper',work)
    a=asyncio.create_task(svc.refresh_paper('a','one'));await entered.wait()
    with pytest.raises(RuntimeError,match='正在同步'):await svc.refresh_paper('a','one')
    release.set();assert await a=={'ok':True}
    assert ('a','one') not in svc._refreshing

@pytest.mark.asyncio
async def test_lifecycle_stops_owned_scheduler(monkeypatch):
    from contextlib import asynccontextmanager
    entered=asyncio.Event();ended=asyncio.Event()
    async def background():
        entered.set()
        try:await asyncio.Event().wait()
        finally:ended.set()
    monkeypatch.setattr(svc,'scheduler',background)
    app=FastAPI();svc.attach_lifespan(app);svc.attach_lifespan(app)
    async with app.router.lifespan_context(app):await entered.wait()
    assert ended.is_set()

def test_star_minimum_sell_and_tick_precision():
    data=dataset(100);data['688001']=data.pop('600519')
    r=e.replay(data,['688001'],e.CANDIDATES[0])
    for f in r['fills']:
        assert round(f['price'],2)==f['price']
        assert f['qty']>=200 or f['side']=='sell'


def test_parallel_idempotent_create_is_single_row():
    from concurrent.futures import ThreadPoolExecutor
    # Schema is ready before racing independent DB connections.
    with st.connect():pass
    with ThreadPoolExecutor(max_workers=6) as pool:
        values=list(pool.map(lambda _:st.create('owner','race-key',request(),'kernel'),range(6)))
    assert len({row['id'] for row,fresh in values})==1
    assert sum(fresh for row,fresh in values)==1

def test_list_omits_large_or_private_payloads():
    job,_=st.create('a','summary-key',request(),'kernel')
    st.update('a',job['id'],extra={'dataset':{'private':'bars'},'result':{'private':'result'}})
    item=st.list_records('a')[0]
    assert item['request']==request() and 'dataset' not in item and 'result' not in item

@pytest.mark.asyncio
async def test_short_coverage_cannot_be_promoted(monkeypatch):
    async def fetch(*_):return dataset()
    monkeypatch.setattr(svc,'fetch_dataset',fetch)
    req={**request(),'start_date':'2020-01-01'}
    job=svc.submit('a','short-cover-key',req);await svc._tasks[job['id']]
    assert st.get('a',job['id'])['status']=='completed'
    with pytest.raises(ValueError,match='门槛'):svc.promote('a',job['id'])


def test_oos_covers_partial_final_window_without_gap():
    data=dataset(420);r=e.walk_forward(data,['600519'],'000300')
    dates=e.validate_dataset(data)
    expected=dates[131:len(dates)-63]
    assert [x['date'] for x in r['oos']['curve']]==expected
    assert r['folds'][-1]['test_end']==dates[len(dates)-64]
    assert 'risk_paused' in r['oos']

def test_kernel_pins_protocol_orchestration_and_data_adapter():
    import inspect
    source=inspect.getsource(svc.kernel_identity)
    assert 'quant_research.py' in source and 'backtest_data.py' in source


@pytest.mark.asyncio
async def test_polling_detects_orphan_without_page_reload():
    app=FastAPI();app.include_router(api.router);app.dependency_overrides[api.require_current_user]=lambda:{'sub':'a'}
    job,_=st.create('a','orphan-poll-key',request(),'kernel')
    with st.connect() as c:c.execute('UPDATE qr_experiment SET updated=? WHERE id=?',('2020-01-01T00:00:00+00:00',job['id']))
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        response=await client.get('/api/quant-research/experiments/'+job['id'])
        assert response.status_code==200 and response.json()['status']=='failed'
        assert response.headers['Cache-Control']=='no-store'


def test_kernel_identity_is_frozen_before_later_source_publish(monkeypatch):
    from pathlib import Path
    expected=svc.kernel_identity()
    def changed(*_):raise AssertionError('a live worker must not reread later source bytes')
    monkeypatch.setattr(Path,'read_bytes',changed)
    assert svc.kernel_identity()==expected


def test_manual_midday_check_does_not_suppress_evening_sync():
    from datetime import datetime,timezone
    local=datetime(2026,10,30,17,0,tzinfo=timezone(timedelta(hours=8)))
    assert svc.paper_due({'last_check':'2026-10-30T02:00:00+00:00'},local)
    assert not svc.paper_due({'last_eod_date':'2026-10-30'},local)
    assert not svc.paper_due({},local.replace(hour=10))
    assert not svc.paper_due({},local+timedelta(days=2))


@pytest.mark.asyncio
async def test_api_full_research_to_incremental_paper_and_pause(monkeypatch):
    from datetime import datetime
    all_data=dataset(420)
    train_end=all_data['600519']['bars'][299]['date']
    initial_day=(date.fromisoformat(train_end)+timedelta(days=1)).isoformat()
    monkeypatch.setattr(svc,'complete_day',lambda:initial_day)
    async def fetch(symbols,start,end):
        return {s:{**p,'bars':[b for b in p['bars'] if start<=b['date']<=end]} for s,p in all_data.items()}
    monkeypatch.setattr(svc,'fetch_dataset',fetch)
    app=FastAPI();app.include_router(api.router);app.dependency_overrides[api.require_current_user]=lambda:{'sub':'api-flow-owner'}
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
        created=await client.post('/api/quant-research/experiments',json={**request(),'end_date':train_end,'request_id':'api-full-flow'})
        assert created.status_code==202
        identity=created.json()['id'];await svc._tasks[identity]
        report=(await client.get('/api/quant-research/experiments/'+identity)).json()
        assert report['status']=='completed' and report['result']['gate']['passed']
        promoted=await client.post('/api/quant-research/experiments/'+identity+'/paper')
        assert promoted.status_code==201
        paper=promoted.json();assert paper['state']['positions']=={} and paper['fills']==[]
        assert (await client.post('/api/quant-research/experiments/'+identity+'/paper')).json()['id']==paper['id']
        later=(date.fromisoformat(all_data['600519']['bars'][-1]['date'])+timedelta(days=1)).isoformat()
        monkeypatch.setattr(svc,'complete_day',lambda:later)
        refreshed=(await client.post('/api/quant-research/paper/'+paper['id']+'/refresh')).json()
        assert refreshed['fills'] and all(initial_day<f['date']<later for f in refreshed['fills'])
        again=(await client.post('/api/quant-research/paper/'+paper['id']+'/refresh')).json()
        assert again['state']==refreshed['state'] and again['fills']==refreshed['fills']
        assert (await client.post('/api/quant-research/paper/'+paper['id']+'/pause')).json()['status']=='paused'
        assert (await client.post('/api/quant-research/paper/'+paper['id']+'/refresh')).status_code==422
        app.dependency_overrides[api.require_current_user]=lambda:{'sub':'other-owner'}
        assert (await client.get('/api/quant-research/paper/'+paper['id'])).status_code==404


def test_process_restart_and_sqlite_backup_preserve_registry(tmp_path,monkeypatch):
    import os,subprocess,sys,sqlite3
    path=tmp_path/'primary.sqlite3';copy_path=tmp_path/'restored.sqlite3'
    env={**os.environ,'DEEPFOCUS_QUANT_RESEARCH_DB_PATH':str(path)}
    code="from deepfocus_api import quant_research_store as s; j,_=s.create('isolated-owner','restart-key',{'name':'fixture'},'kernel');s.update('isolated-owner',j['id'],status='completed',progress=100,stage='retained');print(j['id'])"
    identity=subprocess.check_output([sys.executable,'-c',code],env=env,text=True).strip()
    read="from deepfocus_api import quant_research_store as s; j=s.get('isolated-owner','"+identity+"');assert j['status']=='completed' and j['stage']=='retained';assert s.get('other-owner',j['id']) is None;print('retained')"
    assert subprocess.check_output([sys.executable,'-c',read],env=env,text=True).strip()=='retained'
    source=sqlite3.connect(f'file:{path}?mode=ro',uri=True);target=sqlite3.connect(copy_path)
    try:source.backup(target)
    finally:source.close();target.close()
    assert subprocess.check_output([sys.executable,'-c',read],env={**env,'DEEPFOCUS_QUANT_RESEARCH_DB_PATH':str(copy_path)},text=True).strip()=='retained'
