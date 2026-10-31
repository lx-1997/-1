"""Immutable evidence clocks, independent component visibility and cold recovery."""
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from dataclasses import asdict
import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path
import sys
import threading

import pytest
from deepfocus_api import quant_share_store as s,quant_share_entitlements as q,quant_recovery as recovery

PDF=b'%PDF-1.7\nUNIT FIXTURE NOT REAL ISSUER';RPDF=b'%PDF-1.7\nUNIT FIXTURE NOT REAL ALLOTMENT'
SHA=hashlib.sha256(PDF).hexdigest();RSHA=hashlib.sha256(RPDF).hexdigest()

def fixture(qty=100,role='counterfactual_model'):
 e=q.Capitalization('unit-action','300750','capital_reserve','2023-04-25','2023-04-26','2023-04-26','2023-04-26','0.8','2023-04-19T00:00:00+08:00','2023-04-20T00:00:00+08:00','https://example.invalid/unit.pdf',SHA)
 p={'scope':'unit-account.custody1','position_role':role,'start_after':'2023-04-24','mode':'historical_reconstruction','register_asof':'2023-04-25T15:00:00+08:00','recognize_asof':'2023-04-26T09:30:00+08:00','credit_asof':'2023-04-26T10:00:00+08:00','closing_lots':[{'id':'lot','symbol':'300750','qty':qty,'restriction':'unrestricted'}],'action':asdict(e),'allotment':None}
 return p,{SHA:PDF}

def clock(monkeypatch,value):monkeypatch.setattr(s,'_now',lambda:datetime.fromisoformat(value))
def counts(path):
 with sqlite3.connect(path) as c:return tuple(c.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('qr_share_source','qr_share_bundle','qr_share_review'))

@pytest.fixture
def ledger(tmp_path,monkeypatch):
 p=tmp_path/'research.sqlite3';monkeypatch.setenv('DEEPFOCUS_QUANT_RESEARCH_DB_PATH',str(p));clock(monkeypatch,'2023-04-24T08:00:00+00:00');return p

def run(receipt,key='review1',mode='historical_reconstruction',asof='2023-04-26T12:00:00+08:00',owner='a'):
 return s.review(owner,receipt['id'],key,mode=mode,as_of=asof)

def test_system_clock_replaces_no_declared_fact_and_idempotence_preserves_first_receipt(ledger,monkeypatch):
 p,src=fixture();original=copy.deepcopy(p);r=s.put('a','first',p,src)
 assert r['recorded_at']=='2023-04-24T08:00:00+00:00' and r['stored_visible_at']=='2023-04-25T00:00:00+08:00'
 clock(monkeypatch,'2023-04-26T08:00:00+00:00');assert s.put('a','first',p,src)==r
 assert counts(ledger)==(1,1,0) and p==original
 out=run(r,mode='as_recorded');assert out['result']['available_new_shares']==80 and not out['promotion_allowed']
 assert run(r,mode='as_recorded')==out and counts(ledger)==(1,1,1)
 with pytest.raises(ValueError):run(r,mode='historical_reconstruction')

def test_today_import_does_not_become_past_system_knowledge(ledger,monkeypatch):
 p,src=fixture();clock(monkeypatch,'2026-10-31T08:00:00+00:00');r=s.put('a','late',p,src)
 blocked=run(r,key='stored',mode='as_recorded');assert blocked['result']['status']=='not_available_as_recorded' and blocked['result']['final_book'] is None
 reconstructed=run(r,key='reconstructed');assert reconstructed['result']['available_new_shares']==80
 assert s.inspect('a',r['id'])['component_visibility']['action']=='2026-11-01T00:00:00+08:00'

def test_later_allotment_keeps_earlier_action_and_snapshot_clocks(ledger,monkeypatch):
 p,src=fixture(1);first=s.put('a','pending',p,src)
 clock(monkeypatch,'2023-04-26T12:00:00+00:00')
 pending=run(first,mode='as_recorded');assert pending['result']['status']=='awaiting_allotment'
 p['allotment']=asdict(q.Allotment('receipt1',p['scope'],q.Capitalization(**p['action']).fingerprint,'2023-04-26',1,0,'2023-04-26T10:00:00+08:00','2023-04-26T11:00:00+08:00',RSHA));src[RSHA]=RPDF
 p['credit_asof']='2023-04-27T10:00:00+08:00';second=s.put('a','confirmed',p,src)
 assert second['component_visibility']['action']==first['component_visibility']['action']
 assert second['component_visibility']['position_snapshot']==first['component_visibility']['position_snapshot']
 assert second['component_visibility']['allotment']=='2023-04-27T00:00:00+08:00'
 clock(monkeypatch,'2023-04-27T08:00:00+00:00');out=run(second,key='confirmed',mode='as_recorded',asof='2023-04-27T12:00:00+08:00')
 assert out['result']['available_new_shares']==1 and counts(ledger)==(2,2,2)
 assert pending['result']['final_book']['actions']['unit-action']['credited'] is None

def test_recapture_observation_does_not_erase_first_fact_knowledge(ledger,monkeypatch):
 p,src=fixture();first=s.put('a','first',p,src);clock(monkeypatch,'2023-04-26T08:00:00+00:00')
 p['action']['observed_at']='2023-04-26T10:00:00+08:00';again=s.put('a','recapture',p,src)
 assert again['component_visibility']['action']==first['component_visibility']['action']
 out=run(again,mode='as_recorded');assert out['result']['available_new_shares']==80
 assert out['result']['final_book']['actions']['unit-action']['event']['observed_at']==first['component_visibility']['action']

def test_late_new_snapshot_does_not_inherit_old_action_visibility(ledger,monkeypatch):
 p,src=fixture();first=s.put('a','first',p,src);clock(monkeypatch,'2023-04-26T08:00:00+00:00')
 p['closing_lots'][0]['qty']=200;r=s.put('a','new model',p,src)
 assert r['component_visibility']['action']==first['component_visibility']['action']
 assert r['component_visibility']['position_snapshot']=='2023-04-27T00:00:00+08:00'
 assert run(r,mode='as_recorded')['result']['status']=='not_available_as_recorded'

def test_owner_isolation_sources_and_refs_are_never_shared(ledger,monkeypatch):
 p,src=fixture();a=s.put('a','first',p,src);b=s.put('b','first',p,src);assert a['id']!=b['id'] and counts(ledger)==(2,2,0)
 with pytest.raises(LookupError):s.inspect('b',a['id'])
 clock(monkeypatch,'2023-04-26T08:00:00+00:00')
 with pytest.raises(LookupError):run(a,owner='b')

@pytest.mark.parametrize('table,op',[(t,o) for t in ['qr_share_source','qr_share_bundle','qr_share_review'] for o in ['UPDATE','DELETE']])
def test_ordinary_mutations_are_blocked(ledger,monkeypatch,table,op):
 r=s.put('a','first',*fixture());clock(monkeypatch,'2023-04-26T08:00:00+00:00');run(r)
 with sqlite3.connect(ledger) as c:
  with pytest.raises(sqlite3.IntegrityError):c.execute(('DELETE FROM '+table) if op=='DELETE' else ('UPDATE '+table+' SET owner=owner'))
 assert counts(ledger)==(1,1,1)

@pytest.mark.parametrize('damage',['fact','actual_snapshot','allotment'])
def test_conflicting_retained_facts_and_actual_positions_refuse(ledger,monkeypatch,damage):
 p,src=fixture(1,'operator_supplied_account_close' if damage=='actual_snapshot' else 'counterfactual_model')
 if damage=='allotment':
  p['allotment']=asdict(q.Allotment('r1',p['scope'],q.Capitalization(**p['action']).fingerprint,'2023-04-26',1,0,'2023-04-26T10:00:00+08:00','2023-04-26T10:00:00+08:00',RSHA));src[RSHA]=RPDF;clock(monkeypatch,'2023-04-26T08:00:00+00:00')
 s.put('a','first',p,src);before=counts(ledger)
 if damage=='fact':p['action']['new_shares_per_old_share']='0.9'
 elif damage=='actual_snapshot':p['closing_lots'][0]['qty']=2
 else:p['allotment']['unrestricted_shares']=0
 with pytest.raises(ValueError,match='Conflicting'):s.put('a','conflict',p,src)
 assert counts(ledger)==before

@pytest.mark.parametrize('damage',['source','first_clock','foreign_reference','review_result'])
def test_corruption_refuses_read_and_cold_recovery(ledger,monkeypatch,damage):
 r=s.put('a','first',*fixture());clock(monkeypatch,'2023-04-26T08:00:00+00:00');review=run(r)
 with sqlite3.connect(ledger) as c:
  if damage=='source':
   c.execute('DROP TRIGGER qr_share_source_update');c.execute('UPDATE qr_share_source SET body=?',(b'corrupt',))
  else:
   table='qr_share_review' if damage=='review_result' else 'qr_share_bundle';c.execute('DROP TRIGGER '+table+'_update')
   value=json.loads(c.execute('SELECT payload FROM '+table).fetchone()[0])
   if damage=='first_clock':value['components']['action'].update(first_recorded_at='2023-04-01T08:00:00+00:00',visible_at='2023-04-02T00:00:00+08:00')
   elif damage=='foreign_reference':value['components']['action']['first_receipt_id']='other-owner-or-missing'
   else:value['result']['available_new_shares']=999
   c.execute('UPDATE '+table+' SET payload=?,fingerprint=?',(json.dumps(value),s.digest(value)))
 if damage!='review_result':
  with pytest.raises(ValueError):s.inspect('a',r['id'])
 else:
  c=recovery.connect_ro(ledger)
  try:
   with pytest.raises(ValueError):s.verify_review_row(c,c.execute('SELECT * FROM qr_share_review').fetchone())
  finally:c.close()
 with pytest.raises(ValueError):recovery.prepare(ledger,ledger.parent/('bad-'+damage))

@pytest.mark.parametrize('damage',['backwards','midnight','budget','quota','source_quota','global_quota','metadata_quota','future_observation'])
def test_import_failure_rolls_back_all_sources_and_bundle(ledger,monkeypatch,damage):
 p,src=fixture()
 if damage=='backwards':
  times=iter(['2023-04-24T08:00:00+00:00','2023-04-24T07:00:00+00:00']);monkeypatch.setattr(s,'_now',lambda:datetime.fromisoformat(next(times)))
 elif damage=='midnight':
  times=iter(['2023-04-24T15:59:59+00:00','2023-04-24T16:00:00+00:00']);monkeypatch.setattr(s,'_now',lambda:datetime.fromisoformat(next(times)))
 elif damage=='budget':
  times=iter([0,61]);monkeypatch.setattr(s.elapsed_time,'monotonic',lambda:next(times))
 elif damage=='quota':monkeypatch.setattr(s,'MAX_BUNDLES',0)
 elif damage=='source_quota':monkeypatch.setattr(s,'MAX_OWNER_SOURCE',1)
 elif damage=='global_quota':monkeypatch.setattr(s,'MAX_GLOBAL_BYTES',1)
 elif damage=='metadata_quota':monkeypatch.setattr(s,'MAX_OWNER_METADATA',1)
 else:p['action']['observed_at']='2023-04-28T00:00:00+08:00'
 with pytest.raises((ValueError,RuntimeError)):s.put('a','failed',p,src)
 assert counts(ledger)==(0,0,0)

def test_clock_is_taken_after_waiting_for_write_lock(ledger,monkeypatch):
 with s.connect():pass
 held=sqlite3.connect(ledger);held.execute('BEGIN IMMEDIATE');sampled=threading.Event();now=[datetime.fromisoformat('2023-04-24T08:00:00+00:00')]
 def current():sampled.set();return now[0]
 monkeypatch.setattr(s,'_now',current)
 with ThreadPoolExecutor(max_workers=1) as executor:
  future=executor.submit(s.put,'a','locked',*fixture())
  assert not sampled.wait(.15)
  now[0]=datetime.fromisoformat('2023-04-25T08:00:00+00:00');held.rollback();held.close();r=future.result(timeout=5)
 assert r['recorded_at']=='2023-04-25T08:00:00+00:00'

def test_concurrent_duplicate_import_and_review_do_not_duplicate_receipts(ledger,monkeypatch):
 with s.connect():pass
 with ThreadPoolExecutor(max_workers=2) as executor:results=list(executor.map(lambda _:s.put('a','same',*fixture()),range(2)))
 assert results[0]==results[1] and counts(ledger)==(1,1,0)
 clock(monkeypatch,'2023-04-26T08:00:00+00:00')
 with ThreadPoolExecutor(max_workers=2) as executor:reviews=list(executor.map(lambda _:run(results[0]),range(2)))
 assert reviews[0]==reviews[1] and counts(ledger)==(1,1,1)

def test_read_only_inspection_and_recovery_preserve_all_three_tables(ledger,monkeypatch):
 r=s.put('a','first',*fixture());clock(monkeypatch,'2023-04-26T08:00:00+00:00');run(r)
 before=hashlib.sha256(ledger.read_bytes()).hexdigest();s.inspect('a',r['id']);assert hashlib.sha256(ledger.read_bytes()).hexdigest()==before
 c=recovery.connect_ro(ledger)
 try:original=recovery.inventory(c)
 finally:c.close()
 result=recovery.prepare(ledger,ledger.parent/'restored');assert result['verified'] and not result['activation_authorized']
 c=recovery.connect_ro(ledger.parent/'restored/research.sqlite3')
 try:
  restored=recovery.inventory(c)
  for table in ['qr_share_source','qr_share_bundle','qr_share_review']:assert original['tables'][table]==restored['tables'][table]
 finally:c.close()
 assert hashlib.sha256(ledger.read_bytes()).hexdigest()==before

def test_future_view_and_changed_review_key_do_not_create_extra_reviews(ledger,monkeypatch):
 r=s.put('a','first',*fixture())
 with pytest.raises(ValueError):run(r)
 assert counts(ledger)==(1,1,0)
 clock(monkeypatch,'2023-04-26T08:00:00+00:00');run(r)
 with pytest.raises(ValueError):run(r,asof='2023-04-26T13:00:00+08:00')
 assert counts(ledger)==(1,1,1)

@pytest.mark.parametrize('quota',['MAX_OWNER_METADATA','MAX_GLOBAL_BYTES'])
def test_unicode_metadata_is_budgeted_in_bytes_not_SQLite_characters(ledger,monkeypatch,quota):
 p,src=fixture();p['action']['source_url']+='/'+'\u4e00'*1000;s.put('a','one',p,src)
 with sqlite3.connect(ledger) as c:raw=c.execute('SELECT payload FROM qr_share_bundle').fetchone()[0]
 size=len(raw.encode());chars=len(raw);assert size>chars
 limit=(chars+3*size)//2+(len(PDF) if quota=='MAX_GLOBAL_BYTES' else 0)
 monkeypatch.setattr(s,quota,limit)
 with pytest.raises(RuntimeError):s.put('a','two',p,src)
 assert counts(ledger)==(1,1,0)

def test_operator_CLI_real_process_import_review_isolation_and_no_output_overwrite(ledger,tmp_path):
 p,src=fixture();source=tmp_path/'unit.pdf';source.write_bytes(PDF)
 path=tmp_path/'plan.json';path.write_text(json.dumps({**p,'sources':{SHA:str(source)}}))
 cli=Path(__file__).resolve().parents[3]/'scripts/quant-share-receipt.py'
 def command(owner,*args):return subprocess.run([sys.executable,str(cli),'--db',str(ledger),'--owner',owner,*args],capture_output=True,text=True,timeout=10)
 imported=command('a','import','--plan',str(path),'--label','fixture');assert imported.returncode==0,imported.stderr
 rid=json.loads(imported.stdout)['id'];assert command('b','inspect','--id',rid).returncode==2
 output=tmp_path/'historical.json'
 result=command('a','review','--id',rid,'--key','historical','--as-of','2023-04-26T12:00:00+08:00','--mode','historical_reconstruction','--output',str(output))
 assert result.returncode==0,result.stderr;assert json.loads(output.read_text())['result']['available_new_shares']==80
 blocked=tmp_path/'stored.json';result=command('a','review','--id',rid,'--key','stored','--as-of','2023-04-26T12:00:00+08:00','--mode','as_recorded','--output',str(blocked))
 assert result.returncode==2 and json.loads(blocked.read_text())['result']['status']=='not_available_as_recorded'
 old=output.read_bytes();repeat=command('a','review','--id',rid,'--key','historical','--as-of','2023-04-26T12:00:00+08:00','--mode','historical_reconstruction','--output',str(output))
 assert repeat.returncode==2 and output.read_bytes()==old

@pytest.mark.parametrize('quota',['MAX_REVIEWS','MAX_OWNER_REVIEWS','MAX_GLOBAL_BYTES'])
def test_review_quota_failure_does_not_add_partial_receipt(ledger,monkeypatch,quota):
 r=s.put('a','first',*fixture());clock(monkeypatch,'2023-04-26T08:00:00+00:00');monkeypatch.setattr(s,quota,0)
 with pytest.raises(RuntimeError):run(r)
 assert counts(ledger)==(1,1,0)
