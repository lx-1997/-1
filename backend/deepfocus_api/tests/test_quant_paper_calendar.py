import asyncio
import copy
import json

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from deepfocus_api import quant_research as service, quant_research_store as jobs
from deepfocus_api import quant_calendar_store as evidence, quant_calendar_service as calendar
from deepfocus_api.quant_research_engine import digest, walk_forward
from deepfocus_api.tests.test_quant_exchange_schedule import reviewed
from deepfocus_api.tests.paper_calendar_fixture import aligned, approve
from deepfocus_api.tests.test_quant_research_loop import request


@pytest_asyncio.fixture(autouse=True)
async def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('DEEPFOCUS_QUANT_RESEARCH_DB_PATH',str(tmp_path/'isolated.sqlite3'))
    monkeypatch.setattr(service,'_tasks',{})
    yield
    pending=list(service._tasks.values())
    for task in pending:task.cancel()
    await asyncio.gather(*pending,return_exceptions=True)


def source(reviewed, owner='a', key='parent', *, count=300):
    data=aligned(reviewed,count)
    req={**request(),'end_date':data['600519']['bars'][-1]['date']}
    result=walk_forward(data,req['symbols'],req['benchmark'])
    assert result['gate']['passed'] and not result['oos']['risk_paused']
    job,_=jobs.create(owner,key,req,service.kernel_identity())
    jobs.update(owner,job['id'],status='completed',extra={'dataset':data,'result':result,'result_digest':digest(result)})
    return jobs.get(owner,job['id'])


def mutate(identity, fn):
    # Isolated corruption drill; no public mutation or production database.
    with jobs.connect() as c:
        payload=json.loads(c.execute('SELECT payload FROM qr_experiment WHERE id=?',(identity,)).fetchone()['payload'])
        fn(payload)
        c.execute('UPDATE qr_experiment SET payload=? WHERE id=?',(json.dumps(payload),identity))


@pytest.mark.asyncio
async def test_missing_review_rejects_api_even_when_math_gate_passed(reviewed):
    parent=source(reviewed)
    app=FastAPI();app.include_router(__import__('deepfocus_api.quant_research_api',fromlist=['router']).router)
    from deepfocus_api.quant_research_api import owner
    app.dependency_overrides[owner]=lambda:'a'
    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://fixture') as client:
        response=await client.post(f'/api/quant-research/experiments/{parent["id"]}/paper')
        assert response.status_code==422 and '日历核验' in response.json()['detail']
    assert jobs.list_records('a',True)==[]


@pytest.mark.asyncio
async def test_positive_owner_bound_review_and_update_retain_proof_and_live_positions(reviewed,monkeypatch):
    parent=source(reviewed);review=await approve('a',parent['id'],reviewed)
    start=parent['request']['end_date']
    monkeypatch.setattr(service,'complete_day',lambda:start)
    paper=service.promote('a',parent['id'])
    assert not paper['state']['positions']
    assert paper['calendar_admission']['review_id']==review['id']
    assert not paper['calendar_admission']['actual_exchange_sessions_certified']
    data=aligned(reviewed)
    async def fetch(*_):return data
    monkeypatch.setattr(service,'fetch_dataset',fetch)
    monkeypatch.setattr(service,'complete_day',lambda:data['600519']['bars'][-1]['date'])
    updated=await service.refresh_paper('a',paper['id'])
    assert updated['fills'] and updated['state']['positions']
    assert all(f['date']>start for f in updated['fills'])
    assert updated['calendar_update']['fresh']['status']=='matches_plan'
    assert updated['calendar_update']['merged']['status']=='matches_plan'
    assert not updated['calendar_update']['fresh']['actual_exchange_sessions_certified']
    assert not jobs.list_records('b',True)


@pytest.mark.asyncio
@pytest.mark.parametrize('damage',['other_owner','other_parent','stale_review','changed_reference',
                                  'changed_result','changed_bundle','corrupt_html','forged_quality'])
async def test_invalid_review_cannot_admit_or_advance_existing_active_paper(reviewed,monkeypatch,damage):
    parent=source(reviewed);review=await approve('a',parent['id'],reviewed)
    paper=service.promote('a',parent['id']);before=copy.deepcopy(paper)
    if damage=='other_owner':
        with jobs.connect() as c:c.execute('UPDATE qr_experiment SET owner=? WHERE id=?',('b',review['id']))
    elif damage=='other_parent':
        mutate(review['id'],lambda p:p['request'].update(parent_id='different-parent'))
    elif damage=='stale_review':
        mutate(review['id'],lambda p:p.update(kernel='old-calendar-kernel'))
    elif damage=='changed_reference':
        mutate(review['id'],lambda p:p['request'].update(parent_request_digest='0'*64))
    elif damage=='changed_result':
        mutate(review['id'],lambda p:p['result']['quality'].update(completed_through='2024-01-01'))
    elif damage=='changed_bundle':
        mutate(review['id'],lambda p:p['request'].update(bundle_digest='0'*64))
    elif damage=='corrupt_html':
        with evidence.connect() as c:
            c.execute('DROP TRIGGER qr_calendar_source_update')
            c.execute('UPDATE qr_calendar_source SET body=? WHERE owner=?',(b'corrupt','a'))
    elif damage=='forged_quality':
        def change(p):
            p['result']['quality']['symbols']['600519']['expected_sessions']+=1
            p['result_digest']=digest(p['result'])
        mutate(review['id'],change)
    async def forbidden(*_):raise AssertionError('Invalid evidence must fail before fetching')
    monkeypatch.setattr(service,'fetch_dataset',forbidden)
    with pytest.raises(ValueError):service.promote('a',parent['id'])
    with pytest.raises(ValueError):await service.refresh_paper('a',paper['id'])
    assert jobs.get('a',paper['id'],True)==before


@pytest.mark.asyncio
@pytest.mark.parametrize('where',['fresh_common_gap','old_common_gap','new_tail','extra_holiday','stale_clock'])
async def test_calendar_failures_do_not_execute_or_modify_cash_positions_or_risk(reviewed,monkeypatch,where):
    parent=source(reviewed);await approve('a',parent['id'],reviewed)
    all_data=aligned(reviewed)
    monkeypatch.setattr(service,'complete_day',lambda:parent['request']['end_date'])
    paper=service.promote('a',parent['id'])
    async def fetch(*_):return copy.deepcopy(all_data)
    monkeypatch.setattr(service,'fetch_dataset',fetch)
    monkeypatch.setattr(service,'complete_day',lambda:all_data['600519']['bars'][-1]['date'])
    active=await service.refresh_paper('a',paper['id'])
    assert active['fills'] and active['state']['positions']
    before=copy.deepcopy(jobs.get('a',paper['id'],True))
    if where=='fresh_common_gap':
        # The old stored snapshot still contains this day: a merge alone hides it.
        for payload in all_data.values():payload['bars'].pop(200)
    elif where=='old_common_gap':
        payload={k:v for k,v in active.items() if k not in ('id','experiment','revision','status','updated')}
        for p in payload['dataset'].values():p['bars'].pop(200)
        jobs.save_paper('a',paper['id'],active['revision'],payload)
        before=copy.deepcopy(jobs.get('a',paper['id'],True))
        # A successful fresh response would otherwise mask corrupted old history.
    elif where=='new_tail':
        for payload in all_data.values():payload['bars'].pop()
    elif where=='extra_holiday':
        for payload in all_data.values():
            payload['bars'].append({**payload['bars'][0],'date':'2024-02-12'})
            payload['bars'].sort(key=lambda b:b['date'])
    else:
        for payload in all_data.values():
            payload['fetched_at']='2024-08-01T08:30:00+00:00'
            payload['bars']=[b for b in payload['bars'] if b['date']<='2024-08-01']
    def forbidden(*_,**__):raise AssertionError('No replay may start with invalid calendar coverage')
    monkeypatch.setattr(service.engine,'replay',forbidden)
    with pytest.raises(ValueError):await service.refresh_paper('a',paper['id'])
    assert jobs.get('a',paper['id'],True)==before


@pytest.mark.asyncio
@pytest.mark.parametrize('path',['oos','holdout','missing'])
async def test_a_passed_summary_cannot_restart_a_permanently_paused_validation_path(reviewed,path):
    parent=source(reviewed)
    def pause(p):
        if path=='oos':p['result']['oos']['risk_paused']=True
        elif path=='holdout':p['result']['holdout']['state']['paused']=True
        else:p['result']['oos'].pop('risk_paused')
        # Explicit passed-summary contradiction fixture, not a real new result.
        assert p['result']['gate']['passed']
        p['result_digest']=digest(p['result'])
    mutate(parent['id'],pause)
    await approve('a',parent['id'],reviewed)
    with pytest.raises(ValueError,match='风险状态|永久暂停'):service.promote('a',parent['id'])
    assert jobs.list_records('a',True)==[]


@pytest.mark.asyncio
@pytest.mark.parametrize('field',['experiment_digest','request','source_integrity'])
async def test_paper_and_parent_bindings_cannot_be_silently_reused(reviewed,monkeypatch,field):
    parent=source(reviewed);await approve('a',parent['id'],reviewed)
    paper=service.promote('a',parent['id'])
    if field=='source_integrity':
        mutate(parent['id'],lambda p:p['dataset']['600519']['bars'][0].update(volume=1))
    else:
        payload={k:copy.deepcopy(v) for k,v in paper.items() if k not in ('id','experiment','revision','status','updated')}
        if field=='experiment_digest':payload['experiment_digest']='0'*64
        else:payload['request']['capital']=2000000
        jobs.save_paper('a',paper['id'],paper['revision'],payload)
    before=jobs.get('a',paper['id'],True)
    async def forbidden(*_):raise AssertionError('Changed source must not fetch')
    monkeypatch.setattr(service,'fetch_dataset',forbidden)
    with pytest.raises(ValueError):await service.refresh_paper('a',paper['id'])
    assert jobs.get('a',paper['id'],True)==before
