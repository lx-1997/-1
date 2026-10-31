"""Read-only archive access, safe projections and owned async read capacity."""
import asyncio,copy,hashlib,json,sqlite3,threading
from datetime import datetime

import pytest
import pytest_asyncio
from fastapi import FastAPI,HTTPException
from httpx import AsyncClient,ASGITransport
from deepfocus_api import quant_research_api as api,quant_share_api as routes,quant_share_read as read,quant_share_store as store
from deepfocus_api.tests.test_quant_share_store import fixture

@pytest.fixture
def records(tmp_path,monkeypatch):
 path=tmp_path/'research.sqlite3';monkeypatch.setenv('DEEPFOCUS_QUANT_RESEARCH_DB_PATH',str(path))
 monkeypatch.setattr(store,'_now',lambda:datetime.fromisoformat('2023-04-24T08:00:00+00:00'))
 a=store.put('a','<img src=x onerror=bad> test',*fixture());pending=store.put('a','fractional',*fixture(1))
 monkeypatch.setattr(store,'_now',lambda:datetime.fromisoformat('2023-04-26T08:00:00+00:00'))
 done=store.review('a',a['id'],'done',mode='historical_reconstruction',as_of='2023-04-26T12:00:00+08:00')
 wait=store.review('a',pending['id'],'pending',mode='historical_reconstruction',as_of='2023-04-26T12:00:00+08:00')
 return path,a,pending,done,wait

def application(owner='a'):
 app=FastAPI();app.include_router(api.router);app.dependency_overrides[api.owner]=lambda:owner;return app

@pytest.mark.asyncio
async def test_owned_read_only_pages_and_private_projections(records,monkeypatch):
 path,a,pending,done,wait=records;before=hashlib.sha256(path.read_bytes()).hexdigest()
 def forbidden(*a,**k):raise AssertionError('GET must not initialize or mutate research storage')
 monkeypatch.setattr(store,'connect',forbidden)
 async with AsyncClient(transport=ASGITransport(app=application()),base_url='http://test') as c:
  listing=await c.get('/api/quant-research/share-evidence');assert listing.status_code==200 and len(listing.json()['items'])==2
  for url in [f"/share-evidence/{a['id']}",f"/share-reviews?bundle_id={a['id']}",f"/share-reviews/{done['id']}",f"/share-reviews/{wait['id']}"]:
   response=await c.get('/api/quant-research'+url);assert response.status_code==200,response.text
   assert response.headers['cache-control']=='no-store'
   for private in ['source_url','source_sha256','source_pins','final_book','record_lots','unit-account.custody1','%PDF-']:
    assert private not in response.text
  result=(await c.get('/api/quant-research/share-reviews/'+done['id'])).json()
  assert result['quantity']['available_new_unrestricted_shares']==80 and result['verification_level']=='evidence_and_quantity_checked'
  p=(await c.get('/api/quant-research/share-reviews/'+wait['id'])).json();assert p['status']=='awaiting_allotment' and p['quantity']['credited'] is None
  assert (await c.post('/api/quant-research/share-evidence',json={'source_url':'file:///tmp/no'})).status_code==405
  assert (await c.post('/api/quant-research/share-reviews/'+done['id']+'/paper')).status_code==404
 assert hashlib.sha256(path.read_bytes()).hexdigest()==before

@pytest.mark.asyncio
async def test_other_owner_sees_empty_and_unknown_records_not_data(records):
 _,a,_,done,_=records
 async with AsyncClient(transport=ASGITransport(app=application('b')),base_url='http://test') as c:
  assert (await c.get('/api/quant-research/share-evidence')).json()['items']==[]
  assert (await c.get('/api/quant-research/share-reviews')).json()['items']==[]
  for url in ['/share-evidence/'+a['id'],'/share-reviews/'+done['id'],'/share-reviews?bundle_id='+a['id']]:
   assert (await c.get('/api/quant-research'+url)).status_code==404

@pytest.mark.asyncio
async def test_missing_ledger_has_empty_lists_without_creation(tmp_path,monkeypatch):
 path=tmp_path/'not-created.sqlite3';monkeypatch.setenv('DEEPFOCUS_QUANT_RESEARCH_DB_PATH',str(path))
 async with AsyncClient(transport=ASGITransport(app=application()),base_url='http://test') as c:
  assert (await c.get('/api/quant-research/share-evidence')).json()=={'items':[],'next_cursor':None}
  assert (await c.get('/api/quant-research/share-reviews')).json()=={'items':[],'next_cursor':None}
 assert not path.exists()

@pytest.mark.asyncio
async def test_unknown_or_corrupt_metadata_never_looks_like_empty_archive(records):
 path,a,_,_,_=records
 with sqlite3.connect(path) as c:
  c.execute('DROP TRIGGER qr_share_bundle_update');c.execute('UPDATE qr_share_bundle SET fingerprint=? WHERE id=?',('0'*64,a['id']))
 async with AsyncClient(transport=ASGITransport(app=application()),base_url='http://test') as c:
  assert (await c.get('/api/quant-research/share-evidence')).status_code==409
  assert (await c.get('/api/quant-research/share-evidence/'+a['id'])).status_code==409

@pytest.mark.asyncio
async def test_stale_version_is_metadata_readable_but_cannot_claim_quantity_verification(records,monkeypatch):
 path,a,_,done,_=records
 # Isolated synthetic version mismatch only; not a migrated real old account.
 with sqlite3.connect(path) as c:
  c.execute('DROP TRIGGER qr_share_review_update')
  value=json.loads(c.execute('SELECT payload FROM qr_share_review WHERE id=?',(done['id'],)).fetchone()[0]);value['source_pins']['quant_share_store.py']='0'*64
  c.execute('UPDATE qr_share_review SET payload=?,fingerprint=? WHERE id=?',(json.dumps(value),store.digest(value),done['id']))
 def never(*a,**k):raise AssertionError('Old pinned result must not execute current verifier')
 monkeypatch.setattr(store,'verify_review_row',never)
 async with AsyncClient(transport=ASGITransport(app=application()),base_url='http://test') as c:
  r=await c.get('/api/quant-research/share-reviews/'+done['id']);assert r.status_code==200
  assert not r.json()['source_version_current'] and r.json()['quantity'] is None and r.json()['verification_level']=='metadata_only'

@pytest.mark.asyncio
async def test_cursor_pages_are_bounded_stable_and_validated(records):
 _,a,_,_,_=records
 first=read.listing('a',limit=1);assert len(first['items'])==1 and first['next_cursor']
 second=read.listing('a',cursor=first['next_cursor'],limit=1);assert len(second['items'])==1 and second['items'][0]['id']!=first['items'][0]['id']
 assert second['next_cursor'] is None
 with pytest.raises(ValueError):read.listing('a',cursor='bad')
 with pytest.raises(ValueError):read.listing('a',limit=11)

@pytest.mark.asyncio
async def test_stale_parent_also_marks_review_list_and_details_stale(records):
 path,a,_,done,_=records
 with sqlite3.connect(path) as c:
  c.execute('DROP TRIGGER qr_share_bundle_update');c.execute('DROP TRIGGER qr_share_review_update')
  parent=json.loads(c.execute('SELECT payload FROM qr_share_bundle WHERE id=?',(a['id'],)).fetchone()[0]);parent['source_pins']['quant_share_store.py']='0'*64
  fp=store.digest(parent);c.execute('UPDATE qr_share_bundle SET payload=?,fingerprint=? WHERE id=?',(json.dumps(parent),fp,a['id']))
  review=json.loads(c.execute('SELECT payload FROM qr_share_review WHERE id=?',(done['id'],)).fetchone()[0]);review['request']['bundle_fingerprint']=fp
  c.execute('UPDATE qr_share_review SET payload=?,fingerprint=?,input_hash=? WHERE id=?',(json.dumps(review),store.digest(review),store.digest(review['request']),done['id']))
 async with AsyncClient(transport=ASGITransport(app=application()),base_url='http://test') as c:
  listing=(await c.get('/api/quant-research/share-reviews?bundle_id='+a['id'])).json();assert not listing['items'][0]['source_version_current']
  detail=(await c.get('/api/quant-research/share-reviews/'+done['id'])).json();assert not detail['source_version_current'] and detail['quantity'] is None

@pytest.mark.asyncio
async def test_workspace_variant_has_single_hashed_script_and_no_new_public_data_bypass():
 import base64
 app=FastAPI();app.include_router(api.router)
 async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as c:
  r=await c.get('/api/quant-research/workspace?view=shares');assert r.status_code==200
  script=r.text.split('<script>')[1].split('</script>')[0]
  assert 'sha256-'+base64.b64encode(hashlib.sha256(script.encode()).digest()).decode() in r.headers['content-security-policy']
  assert r.text.count('<script>')==1 and r.headers['cache-control']=='no-store'
  assert (await c.get('/api/quant-research/workspace?view=../../private')).status_code==422
  assert (await c.get('/api/quant-research/share-evidence')).status_code==401

@pytest.mark.asyncio
async def test_cancelled_HTTP_keeps_read_slot_until_actual_thread_exit():
 entered=threading.Event();release=threading.Event()
 def blocking(owner):entered.set();release.wait(5);return {'done':True}
 task=asyncio.create_task(routes.read('a',blocking))
 await asyncio.to_thread(entered.wait,2);task.cancel();await asyncio.sleep(.02)
 assert not task.done() and routes._reading.get(task)=='a'
 task.cancel();await asyncio.sleep(.02);assert not task.done()
 release.set()
 with pytest.raises(asyncio.CancelledError):await task
 assert task not in routes._reading

@pytest.mark.asyncio
async def test_read_capacity_is_bounded_per_owner_and_globally():
 release=threading.Event();count=[0];lock=threading.Lock()
 def blocking(owner):
  with lock:count[0]+=1
  release.wait(5);return {}
 tasks=[asyncio.create_task(routes.read(owner,blocking)) for owner in ['a','a','b','b']]
 try:
  for _ in range(100):
   if len(routes._reading)==4:break
   await asyncio.sleep(.005)
  with pytest.raises(HTTPException) as exc:await routes.read('a',blocking)
  assert exc.value.status_code==429
  with pytest.raises(HTTPException) as exc:await routes.read('c',blocking)
  assert exc.value.status_code==429
 finally:release.set();await asyncio.gather(*tasks)
 assert not routes._reading
