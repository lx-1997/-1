"""Fixed one-hour certification cannot be manufactured by a short/empty run."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

spec=importlib.util.spec_from_file_location('quant_service_soak',Path(__file__).resolve().parents[3]/'scripts/quant-service-soak.py')
soak=importlib.util.module_from_spec(spec);spec.loader.exec_module(soak)


def metrics():
    return {'observer_ms':[10.]*600,'create_ms':[20.]*120,'loop_ms':[2.]*600,
            'peak_rss_bytes':100*1024**2,'peak_qr_disk_bytes':100*1024**2,
            'peak_global_slots':4,'peak_owner_slots':2,'peak_active_slots':4,
            'errors':[],'external_connection_attempts':0,'completed':32,'exact_results':32,
            'remaining_slots':0,'remaining_admissions':0,'paper_accounts':0,
            'elapsed_seconds':3600,'cancelled':10,'rounds':30,'submitted':120,
            'memory':{'api_lifetime_peak_bytes':100*1024**2,'largest_waited_child_peak_bytes':50*1024**2,
                      'descendant_limit':4,'tree_peak_upper_bound_bytes':300*1024**2,
                      'all_children_waited':True,'SIGCHLD_not_ignored':True,
                      'CPU_spawn_attempts':100,'forbidden_spawn_attempts':0}}


def test_smoke_can_never_certify_full_soak_even_with_inflated_counts():
    m=metrics();out=soak.evaluate(m,'smoke')
    assert out['engineering_checks_passed'] and not out['full_soak_passed']
    assert not out['full_run_checks']['mode'] and not out['production_SLO_certified']


@pytest.mark.parametrize('field,value',[('elapsed_seconds',3599.9),('completed',31),('cancelled',9),('rounds',29),('observer_ms',[10.]*599),('loop_ms',[2.]*599)])
def test_full_run_missing_duration_or_work_cannot_pass(field,value):
    m=metrics();m[field]=value;assert not soak.evaluate(m,'full')['full_soak_passed']


@pytest.mark.parametrize('field,value',[('observer_ms',[]),('create_ms',[]),('loop_ms',[]),('peak_global_slots',5),('peak_owner_slots',3),('peak_active_slots',3),('remaining_slots',1),('remaining_admissions',1),('external_connection_attempts',1),('exact_results',31),('paper_accounts',1),('errors',[{'type':'failed'}]),('peak_rss_bytes',1024**3+1),('peak_qr_disk_bytes',512*1024**2+1),('observer_ms',[1501.]*600),('create_ms',[2001.]*120),('loop_ms',[251.]*600)])
def test_unsafe_incomplete_or_slow_observations_reject_without_threshold_changes(field,value):
    m=metrics();m[field]=value;out=soak.evaluate(m,'full')
    assert not out['engineering_checks_passed'] and not out['full_soak_passed']


def test_nearest_rank_percentiles_and_empty_series_are_explicit():
    assert soak.percentile([], .99) is None
    assert soak.percentile([1,2,100],.99)==100
    m=metrics();before=copy.deepcopy(m);out=soak.evaluate(m,'full')
    assert out['full_soak_passed'] and m==before
    assert not out['promotion_allowed'] and not out['production_SLO_certified']


def test_existing_workspace_is_rejected_without_touching_existing_files(tmp_path):
    workspace=tmp_path/'existing';workspace.mkdir();sentinel=workspace/'keep';sentinel.write_bytes(b'untouched')
    source=tmp_path/'input.json';source.write_text('[]')
    out=subprocess.run([sys.executable,str(soak.__file__),'--workspace',str(workspace),'--experiments',str(source)],capture_output=True,text=True)
    assert out.returncode==2 and sentinel.read_bytes()==b'untouched'
    assert sorted(p.name for p in workspace.iterdir())==['keep']


def test_relative_workspace_is_rejected_before_creating_it(tmp_path):
    source=tmp_path/'input.json';source.write_text('[]')
    out=subprocess.run([sys.executable,str(soak.__file__),'--workspace','relative','--experiments',str(source)],cwd=tmp_path,capture_output=True,text=True)
    assert out.returncode==2 and not (tmp_path/'relative').exists()


@pytest.mark.parametrize('needs_kill',[False,True])
def test_parent_wall_budget_terminates_and_waits_only_its_owned_child(tmp_path,monkeypatch,needs_kill):
    workspace=tmp_path/'new';source=tmp_path/'input.json';source.write_text('[]');calls=[]
    class Child:
        pid=123456;code=None
        def wait(self,timeout=None):
            calls.append(('wait',timeout))
            if len([x for x in calls if x[0]=='wait'])==1 or needs_kill and timeout==20:
                raise subprocess.TimeoutExpired('owned-fake-child',timeout)
            self.code=-9 if needs_kill else -15;return self.code
        def poll(self):return self.code
        def terminate(self):calls.append(('terminate',self.pid))
        def kill(self):calls.append(('kill',self.pid))
    child=Child()
    monkeypatch.setattr(soak.subprocess,'Popen',lambda *args,**kwargs:child)
    monkeypatch.setattr(soak.importlib.metadata,'version',lambda name:'unit-fixture')
    monkeypatch.setattr(soak.signal,'signal',lambda *args:None)
    monkeypatch.setattr(sys,'argv',[str(soak.__file__),'--workspace',str(workspace),'--experiments',str(source),'--mode','smoke'])
    assert soak.main()==2
    failed=json.loads((workspace/'PARENT_FAILED.json').read_text())
    assert failed['owned_worker_exited'] and not failed['full_soak_passed']
    assert calls[0]==('wait',240) and ('terminate',123456) in calls and ('wait',20) in calls
    assert (('kill',123456) in calls)==needs_kill and child.poll() is not None
