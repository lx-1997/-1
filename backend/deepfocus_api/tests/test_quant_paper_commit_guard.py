"""Actual async/SQLite interleavings on synthetic owner-isolated paper accounts."""
import asyncio
import copy
import json
import os
import sqlite3
import subprocess
import sys

import pytest
import pytest_asyncio

from deepfocus_api import quant_research as service, quant_research_store as jobs
from deepfocus_api import quant_calendar_store as evidence, quant_calendar_service as calendar
from deepfocus_api.quant_exchange_schedule import facts_digest
from deepfocus_api.tests.test_quant_exchange_schedule import reviewed
from deepfocus_api.tests.test_quant_paper_calendar import source, mutate
from deepfocus_api.tests.paper_calendar_fixture import aligned, approve


@pytest_asyncio.fixture(autouse=True)
async def isolated(tmp_path,monkeypatch):
    monkeypatch.setenv('DEEPFOCUS_QUANT_RESEARCH_DB_PATH',str(tmp_path/'isolated.sqlite3'))
    monkeypatch.setattr(service,'_tasks',{})
    yield
    pending=list(service._tasks.values())
    for task in pending:task.cancel()
    await asyncio.gather(*pending,return_exceptions=True)


async def changed_review(parent,reviewed):
    # Different reviewed facts are explicit synthetic fault injection, not a
    # claimed real-world extra holiday. Execute the real calendar worker.
    manifest,sources=copy.deepcopy(reviewed)
    for row in manifest['records']:
        if row['year']==2024:
            row['closed_ranges'][0][1]='2024-01-02';row['resumes_on'][0]='2024-01-03'
            row['reviewed_facts_digest']=facts_digest(row)
    bundle=evidence.put('a','synthetic changed annual fixture',manifest,sources)
    created=await calendar.submit('a',parent['id'],bundle['id'],'late-negative')
    handle=service._tasks.get(created['id'])
    if handle:await handle
    result=jobs.get('a',created['id'])
    assert result['status']=='completed' and result['result']['quality']['status']=='rejected'
    return result


async def active_paper(reviewed,monkeypatch):
    parent=source(reviewed);review=await approve('a',parent['id'],reviewed)
    monkeypatch.setattr(service,'complete_day',lambda:parent['request']['end_date'])
    paper=service.promote('a',parent['id']);data=aligned(reviewed)
    prefix={s:{**p,'bars':p['bars'][:340]} for s,p in data.items()}
    async def fetch(*_):return prefix
    monkeypatch.setattr(service,'fetch_dataset',fetch)
    monkeypatch.setattr(service,'complete_day',lambda:prefix['600519']['bars'][-1]['date'])
    paper=await service.refresh_paper('a',paper['id'])
    assert paper['fills'] and paper['state']['positions']
    monkeypatch.setattr(service,'complete_day',lambda:data['600519']['bars'][-1]['date'])
    return parent,review,paper,data


@pytest.mark.asyncio
@pytest.mark.parametrize('change',['negative','positive','parent','review_result','bundle','source','pause'])
async def test_late_change_cannot_commit_cached_proof_or_resume_operator_pause(reviewed,monkeypatch,change):
    parent,review,paper,data=await active_paper(reviewed,monkeypatch)
    before=copy.deepcopy(paper);entered,release=asyncio.Event(),asyncio.Event();calculated=[]
    async def fetch(*_):entered.set();await release.wait();return data
    actual=service.engine.replay
    def tracked(*a,**k):
        r=actual(*a,**k);calculated.append(len(r['fills']));return r
    monkeypatch.setattr(service,'fetch_dataset',fetch);monkeypatch.setattr(service.engine,'replay',tracked)
    task=asyncio.create_task(service.refresh_paper('a',paper['id']))
    await asyncio.wait_for(entered.wait(),5)
    if change=='negative':await changed_review(parent,reviewed)
    elif change=='positive':await approve('a',parent['id'],reviewed,key='late-positive')
    elif change=='parent':mutate(parent['id'],lambda p:p['request'].update(capital=2000000))
    elif change=='review_result':mutate(review['id'],lambda p:p.update(result_digest='0'*64))
    elif change=='bundle':
        with evidence.connect() as c:
            c.execute('DROP TRIGGER qr_calendar_bundle_update')
            row=c.execute('SELECT payload FROM qr_calendar_bundle WHERE owner=?',('a',)).fetchone()
            value=json.loads(row['payload']);value['label']='changed behind running task'
            c.execute('UPDATE qr_calendar_bundle SET payload=? WHERE owner=?',(json.dumps(value),'a'))
    elif change=='source':
        with evidence.connect() as c:
            c.execute('DROP TRIGGER qr_calendar_source_update')
            c.execute('UPDATE qr_calendar_source SET body=? WHERE owner=?',(b'changed behind running task','a'))
    else:service.pause('a',paper['id'])
    release.set()
    with pytest.raises((ValueError,RuntimeError)):await asyncio.wait_for(task,10)
    assert calculated and calculated[0]>0, 'Exercise an actual financial replay, not an empty path'
    after=jobs.get('a',paper['id'],True)
    if change=='pause':
        assert after['status']=='paused' and after['revision']==before['revision']+1
        assert all(after[k]==before[k] for k in ('state','dataset','fills','curve'))
    else:assert after==before
    if change=='positive':
        # A benign completed review still requires a fresh proof; retry safely
        # once, without duplicating the discarded in-memory fills.
        again=await service.refresh_paper('a',paper['id'])
        assert len(again['fills'])==len(before['fills'])+calculated[-1]
        assert again['revision']==before['revision']+1


@pytest.mark.asyncio
@pytest.mark.parametrize('other',['owner','parent'])
async def test_other_owner_or_parent_review_does_not_block_valid_account(reviewed,monkeypatch,other):
    parent,review,paper,data=await active_paper(reviewed,monkeypatch)
    entered,release=asyncio.Event(),asyncio.Event()
    async def fetch(*_):entered.set();await release.wait();return data
    monkeypatch.setattr(service,'fetch_dataset',fetch)
    task=asyncio.create_task(service.refresh_paper('a',paper['id']))
    await asyncio.wait_for(entered.wait(),5)
    owner='b' if other=='owner' else 'a'
    unrelated=source(reviewed,owner=owner,key='other-parent')
    await approve(owner,unrelated['id'],reviewed,key='unrelated-calendar')
    release.set();after=await asyncio.wait_for(task,10)
    assert len(after['fills'])>len(paper['fills']) and after['revision']==paper['revision']+1


@pytest.mark.asyncio
@pytest.mark.parametrize('existing',[False,True])
async def test_promotion_guard_runs_before_insert_or_idempotent_existing_return(reviewed,monkeypatch,existing):
    parent=source(reviewed);await approve('a',parent['id'],reviewed)
    before=service.promote('a',parent['id']) if existing else None
    actual=jobs.create_paper
    def late_change(*args,**kwargs):
        mutate(parent['id'],lambda p:p['request'].update(capital=2000000))
        return actual(*args,**kwargs)
    monkeypatch.setattr(jobs,'create_paper',late_change)
    with pytest.raises(ValueError,match='处理期间变化'):service.promote('a',parent['id'])
    if existing:assert jobs.get('a',before['id'],True)==before
    else:assert jobs.list_records('a',True)==[]


def test_guard_and_update_hold_one_write_lock_against_independent_process(tmp_path):
    paper=jobs.create_paper('a','explicit-test-source',{'state':{'cash':10000}})
    observed=[]
    def guard(c):
        assert c.in_transaction
        code="""import sqlite3,sys,json
c=sqlite3.connect(sys.argv[1],timeout=0)
try:
 try:c.execute('BEGIN IMMEDIATE')
 except sqlite3.OperationalError as e:print(json.dumps({'locked':'locked' in str(e)}))
 else:raise AssertionError('A second writer entered between guard and account write')
finally:c.close()
"""
        child=subprocess.run([sys.executable,'-c',code,os.environ['DEEPFOCUS_QUANT_RESEARCH_DB_PATH']],capture_output=True,text=True,check=True)
        observed.append(json.loads(child.stdout)['locked'])
    updated=jobs.save_paper('a',paper['id'],paper['revision'],{'state':{'cash':10001}},guard=guard)
    assert observed==[True] and updated['state']['cash']==10001


def test_guard_failure_rolls_back_entire_transaction_including_guard_side_effects():
    paper=jobs.create_paper('a','rollback-source',{'state':{'cash':10000}})
    with jobs.connect() as c:c.execute('CREATE TABLE guard_fixture_probe(value TEXT)')
    def fail(c):c.execute('INSERT INTO guard_fixture_probe VALUES(?)',('not committed',));raise ValueError('injected guard failure')
    with pytest.raises(ValueError):jobs.save_paper('a',paper['id'],paper['revision'],{'state':{'cash':0}},guard=fail)
    assert jobs.get('a',paper['id'],True)==paper
    with jobs.connect() as c:assert c.execute('SELECT count(*) FROM guard_fixture_probe').fetchone()[0]==0
