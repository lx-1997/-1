"""Pure isolated share quantities; never submit fixture facts to the application."""
import copy
from dataclasses import replace
from decimal import localcontext, Context
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('share_inventory',ROOT/'backend/deepfocus_api/quant_share_entitlements.py')
v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)
PDF=b'%PDF-1.7\nUNIT FIXTURE NOT REAL ISSUER EVIDENCE'
RECEIPT=b'%PDF-1.7\nUNIT FIXTURE NOT REAL ACCOUNT CONFIRMATION'
SHA=hashlib.sha256(PDF).hexdigest();R_SHA=hashlib.sha256(RECEIPT).hexdigest()
SOURCES={SHA:PDF,R_SHA:RECEIPT};SCOPE='test-account.custody-unit-1'
REG='2023-04-25T15:00:00+08:00';EX='2023-04-26T09:30:00+08:00';CREDIT='2023-04-26T10:00:00+08:00'

def event(**changes):
 e=v.Capitalization('unit-action','300750','capital_reserve','2023-04-25','2023-04-26','2023-04-26','2023-04-27','0.8','2023-04-19T00:00:00+08:00','2023-04-20T00:00:00+08:00','https://example.invalid/unit.pdf',SHA)
 return replace(e,**changes)
def lot(identity,qty,restriction='unrestricted',symbol='300750'):
 return {'id':identity,'symbol':symbol,'qty':qty,'restriction':restriction}
def receipt(e,unrestricted,restricted=0,**changes):
 a=v.Allotment('unit-receipt',SCOPE,e.fingerprint,'2023-04-26',unrestricted,restricted,CREDIT,CREDIT,R_SHA)
 return replace(a,**changes)

class ShareTests(unittest.TestCase):
 def setUp(self):self.e=event();self.b=v.initial(SCOPE,'2023-04-24',mode='as_recorded')
 def register(self,lots=None,e=None,book=None,asof=REG):
  return v.register(book or self.b,e or self.e,lots if lots is not None else [lot('a',100)],SOURCES,asof=asof,scope=SCOPE)
 def recognized(self,lots=None,e=None):
  e=e or self.e
  return v.recognize(self.register(lots,e),e,SOURCES,asof=EX,scope=SCOPE)
 def credited(self,lots=None,e=None,allotment=None):
  e=e or self.e
  return v.credit(self.recognized(lots,e),e,SOURCES,asof=CREDIT,scope=SCOPE,allotment=allotment)
 def test_lifecycle_keeps_pending_credited_listed_and_consumed_separate(self):
  b=self.register();self.assertEqual(b['actions'][self.e.event_id]['status'],'registered')
  b=v.recognize(b,self.e,SOURCES,asof=EX,scope=SCOPE)
  self.assertEqual(v.available(b,self.e.event_id,asof=CREDIT,scope=SCOPE),0)
  b=v.credit(b,self.e,SOURCES,asof=CREDIT,scope=SCOPE)
  self.assertEqual(b['actions'][self.e.event_id]['credited'],{'unrestricted':80,'restricted':0})
  self.assertEqual(v.available(b,self.e.event_id,asof=CREDIT,scope=SCOPE),0)
  asof='2023-04-27T09:30:00+08:00'
  self.assertEqual(v.available(b,self.e.event_id,asof=asof,scope=SCOPE),80)
  used=v.consume(b,self.e.event_id,30,'usage1',asof=asof,scope=SCOPE)
  self.assertEqual(v.available(used,self.e.event_id,asof=asof,scope=SCOPE),50)
  self.assertEqual(v.consume(used,self.e.event_id,30,'usage1',asof=asof,scope=SCOPE),used)
  with self.assertRaises(ValueError):v.consume(used,self.e.event_id,31,'usage1',asof=asof,scope=SCOPE)
  self.assertFalse(used['strategy_execution_ready']);self.assertFalse(used['tax_basis_verified'])
 def test_fractional_lots_are_aggregated_without_lot_rounding(self):
  e=event(new_shares_per_old_share='0.5')
  b=self.credited([lot('first',1),lot('second',1)],e)
  self.assertEqual(b['actions'][e.event_id]['credited']['unrestricted'],1)
  self.assertEqual(len(b['actions'][e.event_id]['record_lots']),2)
 def test_unresolved_fraction_refuses_without_mutating_receivable(self):
  b=self.recognized([lot('a',1)]);before=copy.deepcopy(b)
  with self.assertRaises(ValueError):v.credit(b,self.e,SOURCES,asof=CREDIT,scope=SCOPE)
  self.assertEqual(b,before)
 def test_fraction_can_resolve_to_either_valid_account_allotment(self):
  for q in (0,1):
   with self.subTest(q=q):
    b=self.credited([lot('a',1)],allotment=receipt(self.e,q))
    self.assertEqual(b['actions'][self.e.event_id]['credited']['unrestricted'],q)
    self.assertIn('not_automatically_authenticated',b['actions'][self.e.event_id]['credit_basis'])
 def test_confirmed_account_total_cannot_round_each_restriction_bucket_up(self):
  e=event(new_shares_per_old_share='0.4');b=self.recognized([lot('a',1),lot('b',1,'restricted')],e)
  with self.assertRaises(ValueError):v.credit(b,e,SOURCES,asof=CREDIT,scope=SCOPE,allotment=receipt(e,1,1))
 def test_restricted_and_unknown_trade_dates_are_never_sellable(self):
  b=self.credited([lot('a',100,'restricted')])
  self.assertEqual(v.available(b,self.e.event_id,asof='2023-05-01T10:00:00+08:00',scope=SCOPE),0)
  e=event(unrestricted_trade_date=None);b=self.credited(e=e)
  self.assertEqual(v.available(b,e.event_id,asof='2023-05-01T10:00:00+08:00',scope=SCOPE),0)
 def test_unknown_restriction_is_not_assumed_unrestricted(self):
  with self.assertRaises(ValueError):self.register([lot('a',100,'unknown')])
 def test_early_credit_early_record_missing_record_and_missing_recognition_reject(self):
  with self.assertRaises(ValueError):self.register(asof='2023-04-25T14:59:59+08:00')
  with self.assertRaises(ValueError):v.recognize(self.b,self.e,SOURCES,asof=EX,scope=SCOPE)
  b=self.register()
  with self.assertRaises(ValueError):v.credit(b,self.e,SOURCES,asof=CREDIT,scope=SCOPE)
  with self.assertRaises(ValueError):v.credit(self.recognized(),self.e,SOURCES,asof=REG,scope=SCOPE)
 def test_source_scope_symbol_and_duplicate_lot_guards(self):
  for lots in ([lot('a',100,symbol='600519')],[lot('a',100),lot('a',1)]):
   with self.assertRaises(ValueError):self.register(lots)
  with self.assertRaises(ValueError):v.register(self.b,self.e,[lot('a',100)],{},asof=REG,scope=SCOPE)
  with self.assertRaises(ValueError):v.register(self.b,self.e,[lot('a',100)],{SHA:PDF+b'x'},asof=REG,scope=SCOPE)
  with self.assertRaises(ValueError):v.register(self.b,self.e,[lot('a',100)],SOURCES,asof=REG,scope='other')
 def test_frozen_action_and_operation_input_cannot_silently_change(self):
  b=self.register();self.assertEqual(self.register(book=b),b)
  with self.assertRaises(ValueError):self.register([lot('a',101)],book=b)
  with self.assertRaises(ValueError):self.register(e=event(new_shares_per_old_share='0.9'),book=b)
 def test_knowledge_modes_do_not_backfill_today_sources_into_2023(self):
  e=event(observed_at='2026-10-31T10:00:00+08:00')
  with self.assertRaises(ValueError):self.register(e=e)
  b=v.initial(SCOPE,'2023-04-24',mode='historical_reconstruction')
  self.assertEqual(self.register(e=e,book=b)['actions'][e.event_id]['status'],'registered')
  with self.assertRaises(ValueError):event(available_at='2023-04-26T00:00:00+08:00')
 def test_preexisting_rights_never_silently_become_zero(self):
  b=v.initial(SCOPE,'2023-04-25',mode='as_recorded')
  with self.assertRaises(ValueError):self.register(book=b)
 def test_receipt_must_bind_scope_event_date_and_distinct_original(self):
  b=self.recognized([lot('a',1)])
  for changes in ({'account_scope':'other'},{'event_fingerprint':'0'*64},{'share_credit_date':'2023-04-27','available_at':'2023-04-27T10:00:00+08:00','observed_at':'2023-04-27T10:00:00+08:00'},{'source_sha256':SHA}):
   with self.subTest(changes=changes):
    with self.assertRaises(ValueError):v.credit(b,self.e,SOURCES,asof=CREDIT,scope=SCOPE,allotment=receipt(self.e,1,**changes))
  with self.assertRaises(ValueError):v.credit(b,self.e,SOURCES,asof=CREDIT,scope=SCOPE,allotment=receipt(self.e,3))
 def test_late_receipt_not_usable_before_observed_or_backdated(self):
  a=receipt(self.e,1,observed_at='2023-04-28T10:00:00+08:00');b=self.recognized([lot('a',1)])
  with self.assertRaises(ValueError):v.credit(b,self.e,SOURCES,asof=CREDIT,scope=SCOPE,allotment=a)
  after='2023-04-28T10:00:00+08:00';b=v.credit(b,self.e,SOURCES,asof=after,scope=SCOPE,allotment=a)
  self.assertEqual(v.available(b,self.e.event_id,asof='2023-04-27T10:00:00+08:00',scope=SCOPE),0)
  with self.assertRaises(ValueError):v.consume(b,self.e.event_id,1,'backdate',asof='2023-04-27T10:00:00+08:00',scope=SCOPE)
 def test_state_roundtrip_resume_and_no_double_credit(self):
  b=self.recognized();restored=v.restore(json.dumps(b),scope=SCOPE)
  left=v.credit(b,self.e,SOURCES,asof=CREDIT,scope=SCOPE);right=v.credit(restored,self.e,SOURCES,asof=CREDIT,scope=SCOPE)
  self.assertEqual(left,right)
  self.assertEqual(v.credit(left,self.e,SOURCES,asof=CREDIT,scope=SCOPE),left)
  corrupted=copy.deepcopy(b);corrupted['actions'][self.e.event_id]['exact_new_shares']['unrestricted']='999'
  with self.assertRaises(ValueError):v.restore(json.dumps(corrupted),scope=SCOPE)
  with self.assertRaises(ValueError):v.restore(json.dumps(b),scope='other')
  with self.assertRaises(ValueError):v.restore('{"a":1,"a":2}',scope=SCOPE)
 def test_over_consumption_unlisted_and_missing_event_refuse_atomically(self):
  b=self.credited();before=copy.deepcopy(b)
  with self.assertRaises(ValueError):v.consume(b,self.e.event_id,1,'early',asof=CREDIT,scope=SCOPE)
  with self.assertRaises(ValueError):v.consume(b,self.e.event_id,81,'too-many',asof='2023-04-27T10:00:00+08:00',scope=SCOPE)
  with self.assertRaises(ValueError):v.consume(b,'missing',1,'absent',asof=CREDIT,scope=SCOPE)
  self.assertEqual(b,before)
 def test_invalid_ratio_quantity_time_and_unreviewed_action_types_refuse(self):
  for ratio in ('0','-1','NaN','Infinity','0.12345678901',True,0.8):
   with self.subTest(ratio=ratio):
    with self.assertRaises(ValueError):event(new_shares_per_old_share=ratio)
  for qty in (0,-1,True,1.1,10**13):
   with self.subTest(qty=qty):
    with self.assertRaises(ValueError):self.register([lot('a',qty)])
  with self.assertRaises(ValueError):event(kind='rights_issue')
  with self.assertRaises(ValueError):event(unrestricted_trade_date='2023-04-25')
  with self.assertRaises(ValueError):event(available_at='2023-04-19T00:00:00')
  with self.assertRaises(ValueError):event(fractional_policy='round_each_lot')
 def test_explicit_empty_registration_is_distinct_from_missing_snapshot(self):
  b=self.credited([])
  self.assertEqual(b['actions'][self.e.event_id]['credited'],{'unrestricted':0,'restricted':0})
  self.assertEqual(v.available(b,self.e.event_id,asof='2023-04-27T10:00:00+08:00',scope=SCOPE),0)
 def test_bonus_type_remains_distinct_and_never_certifies_tax_treatment(self):
  e=event(kind='stock_bonus');b=self.credited(e=e)
  self.assertEqual(b['actions'][e.event_id]['event']['kind'],'stock_bonus')
  self.assertFalse(b['tax_basis_verified'])
 def test_restore_cannot_upgrade_scope_or_erase_frozen_knowledge_mode(self):
  for key,value in [('mode','unknown'),('promotion_allowed',True),('tax_basis_verified',True),('strategy_execution_ready',True)]:
   b=copy.deepcopy(self.b);b[key]=value;b.pop('state_digest');b['state_digest']=v.digest(b)
   with self.assertRaises(ValueError):v.restore(json.dumps(b),scope=SCOPE)
  with self.assertRaises(ValueError):v.restore('[]',scope=SCOPE)
 def test_lot_order_does_not_change_account_snapshot_or_share_count(self):
  lots=[lot('b',1),lot('a',1)];e=event(new_shares_per_old_share='0.5')
  self.assertEqual(self.register(lots,e),self.register(list(reversed(lots)),e))
  self.assertEqual(self.credited(lots,e),self.credited(list(reversed(lots)),e))
 def test_callers_decimal_precision_rounding_and_exponent_limits_cannot_change_quantity(self):
  baseline=self.credited([lot('a',12345)])
  with localcontext(Context(prec=1,Emax=1,Emin=-1)) as hostile:
   actual=self.credited([lot('a',12345)])
   self.assertEqual(hostile.prec,1);self.assertEqual(hostile.Emax,1)
  self.assertEqual(actual,baseline)
 def test_declared_listing_on_credit_day_is_not_replaced_by_purchase_T_plus_one(self):
  e=event(unrestricted_trade_date='2023-04-26');b=self.credited(e=e)
  self.assertEqual(v.available(b,e.event_id,asof=CREDIT,scope=SCOPE),80)
  b=v.consume(b,e.event_id,80,'same-listing-day',asof=CREDIT,scope=SCOPE)
  self.assertEqual(v.available(b,e.event_id,asof=CREDIT,scope=SCOPE),0)
 def test_malformed_source_values_fail_with_domain_error(self):
  for url in (5,True,None,[],{}):
   with self.subTest(url=url):
    with self.assertRaises(ValueError):event(source_url=url)
  with self.assertRaises(ValueError):v.pdf_source(SHA,[SHA])
  with self.assertRaises(ValueError):v.available(self.b,[],asof=REG,scope=SCOPE)
 def test_deep_or_multibyte_oversized_state_refuses_before_use(self):
  with self.assertRaises(ValueError):v.restore('['*1100+'0'+']'*1100,scope=SCOPE)
  from unittest.mock import patch
  with patch.object(v,'MAX_STATE',10):
   with self.assertRaises(ValueError):v.restore('"'+'\u4e00'*4+'"',scope=SCOPE)

if __name__=='__main__':unittest.main()
