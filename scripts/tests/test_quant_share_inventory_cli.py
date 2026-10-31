"""Offline CLI boundaries only; fixture files never enter production."""
import hashlib,json,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
CLI=ROOT/'scripts/quant-share-inventory-review.py'
class CliTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.p=Path(self.temp.name);self.pdf=self.p/'unit.pdf';self.pdf.write_bytes(b'%PDF-1.7\nUNIT FIXTURE')
  sha=hashlib.sha256(self.pdf.read_bytes()).hexdigest()
  self.plan={'position_role':'counterfactual_model','scope':'unit-account','start_after':'2023-04-24','mode':'historical_reconstruction','register_asof':'2023-04-25T15:00:00+08:00','recognize_asof':'2023-04-26T09:30:00+08:00','credit_asof':'2023-04-26T10:00:00+08:00','closing_lots':[{'id':'lot','symbol':'300750','qty':100,'restriction':'unrestricted'}],'sources':{sha:str(self.pdf)},'action':{'event_id':'unit','symbol':'300750','kind':'capital_reserve','record_date':'2023-04-25','ex_date':'2023-04-26','share_credit_date':'2023-04-26','unrestricted_trade_date':'2023-04-26','new_shares_per_old_share':'0.8','available_at':'2023-04-19T00:00:00+08:00','observed_at':'2026-10-31T00:00:00+08:00','source_url':'https://example.invalid/unit.pdf','source_sha256':sha}}
 def tearDown(self):self.temp.cleanup()
 def run_plan(self,name='run'):
  return self.run_raw(json.dumps(self.plan),name)
 def run_raw(self,raw,name):
  plan=self.p/'plan.json';plan.write_text(raw);w=self.p/name
  run=subprocess.run([sys.executable,str(CLI),'--plan',str(plan),'--workspace',str(w)],capture_output=True,text=True,timeout=10)
  return run,w
 def test_model_pipeline_retains_receipts_sources_and_false_scope_flags(self):
  run,w=self.run_plan();self.assertEqual(run.returncode,0,run.stderr)
  result=json.loads((w/'result.json').read_text());self.assertEqual(result['available_new_unrestricted_shares'],80)
  self.assertFalse(result['strategy_execution_ready']);self.assertFalse(result['tax_basis_verified']);self.assertEqual(result['production_writes'],0)
  for name in ['registered.json','receivable.json','credited.json','module-source-archive.py']:self.assertTrue((w/name).is_file())
  self.assertEqual(len(list((w/'evidence').glob('*.pdf'))),1)
 def test_fractional_credit_refuses_and_keeps_pending_checkpoint(self):
  self.plan['closing_lots'][0]['qty']=1;run,w=self.run_plan()
  self.assertEqual(run.returncode,2);self.assertTrue((w/'receivable.json').exists());self.assertFalse((w/'credited.json').exists())
  self.assertEqual(json.loads((w/'result.json').read_text())['failed_stage'],'credit')
 def test_existing_workspace_is_never_overwritten(self):
  run,w=self.run_plan();before=(w/'result.json').read_bytes();run2,w=self.run_plan()
  self.assertEqual(run2.returncode,2);self.assertEqual((w/'result.json').read_bytes(),before)
 def test_damaged_original_cannot_generate_registration(self):
  self.pdf.write_bytes(self.pdf.read_bytes()+b'corrupt');run,w=self.run_plan()
  self.assertEqual(run.returncode,2);self.assertFalse((w/'registered.json').exists())
  self.assertEqual(json.loads((w/'result.json').read_text())['failed_stage'],'evidence')
 def test_as_recorded_rejects_today_observation_for_past_registration(self):
  self.plan['mode']='as_recorded';run,w=self.run_plan();self.assertEqual(run.returncode,2)
  self.assertFalse((w/'registered.json').exists())
 def test_position_role_must_be_declared(self):
  self.plan['position_role']='real_proven_account';run,w=self.run_plan();self.assertEqual(run.returncode,2)
  self.assertFalse((w/'registered.json').exists())
 def test_bad_URL_type_retains_failure_instead_of_traceback(self):
  self.plan['action']['source_url']=5;run,w=self.run_plan()
  self.assertEqual(run.returncode,2);self.assertNotIn('Traceback',run.stderr)
  result=json.loads((w/'result.json').read_text());self.assertEqual(result['error_type'],'ValueError');self.assertEqual(result['failed_stage'],'plan')
 def test_array_source_map_cannot_escape_failure_report(self):
  self.plan['sources']=list(self.plan['sources']);run,w=self.run_plan()
  self.assertEqual(run.returncode,2);self.assertNotIn('Traceback',run.stderr);self.assertTrue((w/'result.json').exists())
 def test_nonfinite_exponent_giant_integer_and_deep_JSON_reject_without_registration(self):
  raw=json.dumps({**self.plan,'unused':'EXPONENT'}).replace('"EXPONENT"','1e999')
  for index,body in enumerate([raw,json.dumps({**self.plan,'unused':10**40}),'['*1100+'0'+']'*1100]):
   run,w=self.run_raw(body,'bad-'+str(index));self.assertEqual(run.returncode,2);self.assertNotIn('Traceback',run.stderr)
   self.assertTrue((w/'result.json').exists());self.assertFalse((w/'registered.json').exists())
 def test_missing_output_parent_is_an_admission_error(self):
  run,w=self.run_plan('missing-parent/new');self.assertEqual(run.returncode,2);self.assertNotIn('Traceback',run.stderr);self.assertFalse(w.exists())
if __name__=='__main__':unittest.main()
