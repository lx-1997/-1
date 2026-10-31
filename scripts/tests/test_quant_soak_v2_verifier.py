"""Stdlib fixtures only; never submit synthetic data to an application."""
import copy,hashlib,importlib.util,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('verifier',ROOT/'scripts/verify-quant-soak-v2.py')
v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)

def write(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False))

class VerifyTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.w=self.root/'run';self.w.mkdir();self.archive=self.root/'archive';self.archive.mkdir()
  self.protocol=json.loads((ROOT/'docs/quant-tree-memory-smoke-20261031.json').read_text())['protocol']
  self.pins={}
  for name in v.SOURCE_FILES:
   body=('UNIT FIXTURE NOT EXECUTABLE '+name).encode();(self.archive/name).write_bytes(body);self.pins[name]=hashlib.sha256(body).hexdigest()
  self.originals=[]
  for i in range(2):
   req={'name':'synthetic verifier fixture','symbols':[str(i)],'capital':1000000};ds={'unit_fixture':True,'case':i}
   result={'dataset_digest':v.digest(ds),'unit_fixture_result':i}
   self.originals.append({'request':req,'dataset':ds,'result':result,'result_digest':v.digest(result)})
  self.experiments=self.root/'originals.json';write(self.experiments,self.originals)
  self.exp_sha=hashlib.sha256(self.experiments.read_bytes()).hexdigest()
  self.plan={'mode':'full','protocol':self.protocol,'source_pins':self.pins,'parent_pid':2000000000,'registered_at':'2020-01-01T00:00:00+00:00','wall_budget_seconds':3840,'experiments_sha256':self.exp_sha}
  write(self.w/'plan.json',self.plan);self.plan_sha=v.sha(self.w/'plan.json')
  script=self.pins['quant-service-soak.py']
  write(self.w/'owned-parent.json',{'pid':2000000000,'worker_pid':1999999999,'workspace':str(self.w),'script_sha256':script})
  write(self.w/'owned-worker.json',{'pid':1999999999,'parent_pid':2000000000,'workspace':str(self.w),'port':1,'script_sha256':script})
  connection=sqlite3.connect(self.w/'research.sqlite3')
  connection.execute('CREATE TABLE qr_experiment(id TEXT PRIMARY KEY,owner TEXT,request_key TEXT,input_hash TEXT,status TEXT,progress INTEGER,stage TEXT,created TEXT,updated TEXT,payload TEXT)')
  connection.execute('CREATE TABLE qr_worker_slot(experiment TEXT PRIMARY KEY)')
  connection.execute('CREATE TABLE qr_paper(id TEXT PRIMARY KEY)')
  completed=cancelled=0;observations=[];create=[]
  def http(method,status,ms):return {'kind':'http','method':method,'status':status,'elapsed_ms':ms}
  observations.append(http('GET',401,.1))
  for index in range(30):
   for lane in range(4):
    original=self.originals[lane%2];cancel=(index*4+lane+1)%5==0
    status='cancelled' if cancel else 'completed';payload={'request':original['request'],'dataset':original['dataset'],'kernel':v.digest({k:self.pins[k] for k in v.CPU_FILES})}
    if cancel:
     cancelled+=1;observations.append(http('DELETE',200,.2))
     if index%2==0:payload.pop('dataset') # cancelled before fetch/persist
    else:completed+=1;payload.update(result=original['result'],result_digest=original['result_digest'])
    connection.execute('INSERT INTO qr_experiment VALUES(?,?,?,?,?,?,?,?,?,?)',(f'{index}-{lane}',f'unit-owner-{lane//2}',f'soak-{index:04}-{lane}',v.digest(original['request']),status,100,'unit fixture','2020','2020',json.dumps(payload)))
    observations.append(http('POST',202,float(lane+1)));create.append(float(lane+1))
   observations.extend([http('POST',429,.1),http('POST',202,.2),http('GET',404,.1)])
   observations.extend(http('GET',200,10.) for _ in range(20))
   observations.append({'kind':'round','index':index,'completed':completed,'cancelled':cancelled,'elapsed_seconds':(index+1)*100,'rss_bytes':80*1024**2,'qr_disk_bytes':1024**2})
  connection.commit();connection.close()
  (self.w/'observations.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in observations))
  self.metrics={'observer_ms':[10.]*600,'create_ms':create,'loop_ms':[2.]*600,'elapsed_seconds':3600,'rounds':30,'submitted':120,'completed':completed,'cancelled':cancelled,'exact_results':completed,'cancel_requested':cancelled,'cancel_missed':0,'peak_rss_bytes':100*1024**2,'peak_qr_disk_bytes':100*1024**2,'peak_global_slots':4,'peak_owner_slots':2,'peak_active_slots':4,'remaining_slots':0,'remaining_admissions':0,'paper_accounts':0,'errors':[],'external_connection_attempts':0}
  safety={k:True for k in ['read_p95','read_p99','create_p95','loop_p99','loop_max','rss','disk','worker_caps','four_active_slots_observed','no_unexpected_errors','no_external_connections','all_results_exact','all_drained','no_paper_accounts']}
  full={k:True for k in ['mode','duration','completed','cancelled','rounds','reads','heartbeats']}
  self.result={'mode':'full','protocol':self.protocol,'source_pins':self.pins,'metrics':self.metrics,'evaluation':{'latency_ms':{'read_p95':10.,'read_p99':10.,'create_p95':4.,'loop_p99':2.,'loop_max':2.},'safety_checks':safety,'full_run_checks':full,'engineering_checks_passed':True,'full_soak_passed':True,'promotion_allowed':False,'production_SLO_certified':False},'production_writes':0,'provider_requests':0,'new_paper_accounts':0,'promotion_allowed':False}
  self.metrics['memory']={'api_lifetime_peak_bytes':100*1024**2,'largest_waited_child_peak_bytes':50*1024**2,'descendant_limit':4,'tree_peak_upper_bound_bytes':300*1024**2,'all_children_waited':True,'SIGCHLD_not_ignored':True,'CPU_spawn_attempts':120,'forbidden_spawn_attempts':0}
  self.parent_memory={'control_parent_peak_bytes':20*1024**2,'largest_waited_descendant_peak_bytes':150*1024**2,'descendant_limit':5,'whole_tree_peak_upper_bound_bytes':770*1024**2,'all_children_waited':True,'SIGCHLD_not_ignored':True,'accepted':True}
  self.result['evaluation']['safety_checks']['rss_tree']=True
  self.save()
  self.patches=[patch.object(v,'REGISTERED_PLAN_SHA',self.plan_sha),patch.object(v,'REGISTERED_SOURCE_SHA',v.digest(self.pins)),patch.object(v,'EXPERIMENTS_SHA',self.exp_sha),patch.object(v,'absent',lambda pid:True),patch.object(v,'port_closed',lambda port:True)]
  for p in self.patches:p.start()
 def tearDown(self):
  for p in reversed(self.patches):p.stop()
  self.temp.cleanup()
 def save(self):
  write(self.w/'result.json',self.result)
  api_ok=self.result['evaluation']['full_soak_passed']
  write(self.w/'parent-exit.json',{'worker_exit_code':0 if api_ok else 2,'owned_worker_exited':True,'result_sha256':v.sha(self.w/'result.json'),'finished_at':'2020-01-01T01:00:05+00:00','owned_tree_memory':self.parent_memory,'full_soak_passed':api_ok and self.parent_memory['accepted']})
 def verify(self):return v.verify(self.w,self.archive,self.experiments,self.plan_sha)
 def test_complete_registered_v2_accepts_only_registered_local_capacity(self):
  before=v.sha(self.w/'research.sqlite3');out=self.verify()
  self.assertTrue(out['registered_protocol_passed']);self.assertTrue(out['memory_scope_complete']);self.assertTrue(out['internal_capacity_accepted']);self.assertFalse(out['commercial_ready']);self.assertEqual(before,v.sha(self.w/'research.sqlite3'))
 def test_smoke_rejected_even_with_full_looking_counts(self):
  self.result['mode']='smoke';self.save()
  with self.assertRaises(ValueError):self.verify()
 def test_short_measurement_cannot_pass_but_legitimate_failed_result_is_retained(self):
  self.metrics['elapsed_seconds']=3599;self.result['evaluation']['full_run_checks']['duration']=False;self.result['evaluation']['full_soak_passed']=False;self.save()
  self.assertFalse(self.verify()['registered_protocol_passed'])
 def test_duplicate_and_nonfinite_json_reject(self):
  for text in ['{"a":1,"a":2}','{"a":NaN}','{"a":Infinity}']:
   with self.assertRaises(ValueError):v.parse(text)
 def test_false_green_summary_rejects(self):
  self.result['evaluation']['full_soak_passed']=False;self.save()
  with self.assertRaises(ValueError):self.verify()
 def test_changed_archived_bytes_reject_without_execution(self):
  (self.archive/'quant_cpu_worker.py').write_text('not executed')
  with self.assertRaises(ValueError):self.verify()
 def test_changed_original_payload_rejects(self):
  self.experiments.write_text('[]')
  with self.assertRaises(ValueError):self.verify()
 def test_heartbeat_quantile_cannot_hide_a_large_sample(self):
  self.metrics['loop_ms'][-1]=2500.;self.save()
  with self.assertRaises(ValueError):self.verify()
 def test_request_timing_must_come_from_actual_admitted_posts(self):
  self.metrics['create_ms'][0]=.001;self.save()
  with self.assertRaises(ValueError):self.verify()
 def test_observer_timing_not_in_log_rejects(self):
  self.metrics['observer_ms'][0]=.001;self.save()
  with self.assertRaises(ValueError):self.verify()
 def test_unfinished_or_mutated_financial_result_rejects(self):
  c=sqlite3.connect(self.w/'research.sqlite3');c.execute("UPDATE qr_experiment SET status='failed' WHERE id='0-0'");c.commit();c.close()
  with self.assertRaises(ValueError):self.verify()
 def test_completed_without_retained_data_rejects(self):
  c=sqlite3.connect(self.w/'research.sqlite3');payload=json.loads(c.execute("SELECT payload FROM qr_experiment WHERE id='0-0'").fetchone()[0]);payload.pop('dataset')
  c.execute("UPDATE qr_experiment SET payload=? WHERE id='0-0'",(json.dumps(payload),));c.commit();c.close()
  with self.assertRaises(ValueError):self.verify()
 def test_overflow_or_duplicate_committed_request_rejects(self):
  c=sqlite3.connect(self.w/'research.sqlite3');c.execute("UPDATE qr_experiment SET request_key='overflow-0000' WHERE id='0-0'");c.commit();c.close()
  with self.assertRaises(ValueError):self.verify()
 def test_live_or_unknown_owned_pid_not_killed_or_accepted(self):
  with patch.object(v,'absent',lambda pid:False):
   with self.assertRaises(ValueError):self.verify()
 def test_nonempty_wal_is_not_ignored_by_immutable_reader(self):
  Path(str(self.w/'research.sqlite3')+'-wal').write_bytes(b'not ignored')
  with self.assertRaises(ValueError):self.verify()
 def test_running_or_failed_parent_not_terminal_success(self):
  (self.w/'PARENT_FAILED.json').write_text('{}')
  with self.assertRaises(ValueError):self.verify()
 def test_parent_budget_can_fail_even_though_API_registered_gate_passed(self):
  m=self.parent_memory;m['largest_waited_descendant_peak_bytes']=220*1024**2
  m['whole_tree_peak_upper_bound_bytes']=m['control_parent_peak_bytes']+5*m['largest_waited_descendant_peak_bytes'];m['accepted']=False;self.save()
  out=self.verify();self.assertTrue(out['API_registered_checks_passed']);self.assertFalse(out['registered_protocol_passed']);self.assertFalse(out['internal_capacity_accepted']);self.assertTrue(out['memory_scope_complete'])
 def test_API_and_parent_consistent_over_budget_failures_are_preserved(self):
  m=self.metrics['memory'];m['largest_waited_child_peak_bytes']=300*1024**2;m['tree_peak_upper_bound_bytes']=m['api_lifetime_peak_bytes']+4*m['largest_waited_child_peak_bytes']
  m=self.parent_memory;m['largest_waited_descendant_peak_bytes']=300*1024**2;m['whole_tree_peak_upper_bound_bytes']=m['control_parent_peak_bytes']+5*m['largest_waited_descendant_peak_bytes'];m['accepted']=False
  e=self.result['evaluation'];e['safety_checks']['rss_tree']=False;e['engineering_checks_passed']=False;e['full_soak_passed']=False;self.save()
  out=self.verify();self.assertFalse(out['internal_capacity_accepted']);self.assertFalse(out['registered_protocol_passed'])
 def test_missing_or_nonnumeric_memory_cannot_default_to_zero(self):
  for invalid in [0,-1,True,None,'1',float('inf')]:
   with self.subTest(invalid=invalid):
    original=self.metrics['memory']['largest_waited_child_peak_bytes'];self.metrics['memory']['largest_waited_child_peak_bytes']=invalid
    # Infinity is rejected by strict JSON; other invalid types by the reviewer.
    try:
     if invalid==float('inf'):
      with self.assertRaises(ValueError):self.save()
     else:
      self.save()
      with self.assertRaises(ValueError):self.verify()
    finally:self.metrics['memory']['largest_waited_child_peak_bytes']=original;self.save()
 def test_api_only_or_understated_final_tree_bound_rejects(self):
  for target,key in [(self.metrics['memory'],'tree_peak_upper_bound_bytes'),(self.parent_memory,'whole_tree_peak_upper_bound_bytes')]:
   old=target[key];target[key]=1;self.save()
   with self.assertRaises(ValueError):self.verify()
   target[key]=old;self.save()
 def test_final_parent_peak_cannot_drop_below_API_or_child_peak(self):
  self.parent_memory['largest_waited_descendant_peak_bytes']=30*1024**2
  self.parent_memory['whole_tree_peak_upper_bound_bytes']=170*1024**2;self.save()
  with self.assertRaises(ValueError):self.verify()
 def test_topology_cannot_be_shrunk_to_pass(self):
  for target,value in [(self.metrics['memory'],3),(self.parent_memory,4)]:
   old=target['descendant_limit'];target['descendant_limit']=value;self.save()
   with self.assertRaises(ValueError):self.verify()
   target['descendant_limit']=old;self.save()
 def test_unwaited_child_ignored_SIGCHLD_or_denied_spawn_never_passes(self):
  for target,key in [(self.metrics['memory'],'all_children_waited'),(self.metrics['memory'],'SIGCHLD_not_ignored'),(self.parent_memory,'all_children_waited'),(self.parent_memory,'SIGCHLD_not_ignored')]:
   target[key]=False;self.save()
   with self.assertRaises(ValueError):self.verify() # producer still falsely green
   target[key]=True;self.save()
  self.metrics['memory']['forbidden_spawn_attempts']=1;self.save()
  with self.assertRaises(ValueError):self.verify()
 def test_completed_results_require_at_least_as_many_CPU_spawns(self):
  self.metrics['memory']['CPU_spawn_attempts']=1;self.save()
  with self.assertRaises(ValueError):self.verify()
 def test_approved_sources_cannot_be_replaced_even_if_archive_matches_new_plan(self):
  with patch.object(v,'REGISTERED_SOURCE_SHA','0'*64):
   with self.assertRaises(ValueError):self.verify()
 def test_plan_checkpoint_cannot_be_replaced_by_caller_chosen_digest(self):
  with patch.object(v,'REGISTERED_PLAN_SHA','0'*64):
   with self.assertRaises(ValueError):self.verify()
 def test_false_green_parent_memory_and_full_summaries_reject(self):
  self.parent_memory['accepted']=False;self.save()
  with self.assertRaises(ValueError):self.verify()
 def test_abnormal_exit_or_reused_PID_is_not_a_success_receipt(self):
  p=v.parse((self.w/'parent-exit.json').read_bytes());p['worker_exit_code']=-9;write(self.w/'parent-exit.json',p)
  with self.assertRaises(ValueError):self.verify()
if __name__=='__main__':unittest.main()
