"""Native identity and complete waited-child bound; no production data."""
import ctypes
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from deepfocus_api import quant_worker_slots as slots, quant_cpu_worker as worker

spec=importlib.util.spec_from_file_location('soak_memory',Path(__file__).resolve().parents[3]/'scripts/quant-service-soak.py')
soak=importlib.util.module_from_spec(spec);spec.loader.exec_module(soak)
BOOT='ABCDEF01-2345-6789-ABCD-0123456789AB'

class Sysctl:
 def __init__(self,body=(BOOT+'\0').encode(),size=None,error=False):self.body=body;self.size=size;self.error=error
 def __call__(self,key,out,size,new,newsize):
  assert key==b'kern.bootsessionuuid' and new is None and newsize==0
  if self.error:return -1
  n=len(self.body) if self.size is None else self.size
  if out is not None:ctypes.memmove(out,self.body,min(len(out),len(self.body)))
  ctypes.cast(size,ctypes.POINTER(ctypes.c_size_t))[0]=n
  return 0

def test_native_boot_uuid_does_not_spawn(monkeypatch):
 monkeypatch.setattr(slots.ctypes,'CDLL',lambda *a,**k:SimpleNamespace(sysctlbyname=Sysctl()))
 monkeypatch.setattr(subprocess,'Popen',lambda *a,**k:pytest.fail('Identity must not spawn'))
 assert slots._darwin_boot_uuid()==BOOT

@pytest.mark.parametrize('fake',[Sysctl(size=0),Sysctl(size=129),Sysctl(error=True),Sysctl(body=b'garbage\0'),Sysctl(body=BOOT.encode()),Sysctl(body=b'\xff\0')])
def test_invalid_native_identity_fails_closed(monkeypatch,fake):
 monkeypatch.setattr(slots.ctypes,'CDLL',lambda *a,**k:SimpleNamespace(sysctlbyname=fake))
 monkeypatch.setattr(slots.sys,'platform','darwin');slots.process_scope.cache_clear()
 try:assert slots.process_scope() is None
 finally:slots.process_scope.cache_clear()

@pytest.mark.skipif(sys.platform!='darwin',reason='Darwin native consistency')
def test_actual_native_matches_existing_boot_scope():
 assert slots._darwin_boot_uuid()==subprocess.check_output(['/usr/sbin/sysctl','-n','kern.bootsessionuuid'],text=True).strip()

@pytest.mark.parametrize('event',['socket.connect','subprocess.Popen','os.fork','os.forkpty','os.posix_spawn','os.exec','os.system'])
def test_private_child_denies_network_and_descendants(event):
 with pytest.raises(PermissionError):worker.reject_external_io(event,())
 worker.reject_external_io('open',())

def memory():return {'api_lifetime_peak_bytes':100*1024**2,'largest_waited_child_peak_bytes':50*1024**2,'descendant_limit':4,'tree_peak_upper_bound_bytes':300*1024**2,'all_children_waited':True,'SIGCHLD_not_ignored':True,'CPU_spawn_attempts':4,'forbidden_spawn_attempts':0}

@pytest.mark.parametrize('field,value',[('all_children_waited',False),('SIGCHLD_not_ignored',False),('descendant_limit',3),('CPU_spawn_attempts',0),('CPU_spawn_attempts',5),('forbidden_spawn_attempts',1),('tree_peak_upper_bound_bytes',1),('largest_waited_child_peak_bytes',float('nan'))])
def test_tree_bound_requires_complete_observations(field,value):
 m=memory();assert soak.memory_ok({'memory':m,'submitted':4})
 m[field]=value;assert not soak.memory_ok({'memory':m,'submitted':4})

def test_small_API_cannot_hide_children_exceeding_fixed_GiB():
 m=memory();m['largest_waited_child_peak_bytes']=300*1024**2;m['tree_peak_upper_bound_bytes']=m['api_lifetime_peak_bytes']+4*m['largest_waited_child_peak_bytes']
 assert not soak.memory_ok({'memory':m,'submitted':4})

@pytest.mark.parametrize('platform,factor',[('darwin',1),('linux',1024)])
def test_platform_units_and_upper_bound(monkeypatch,platform,factor):
 monkeypatch.setattr(soak.sys,'platform',platform)
 monkeypatch.setattr(soak.resource,'getrusage',lambda who:SimpleNamespace(ru_maxrss=100 if who==soak.resource.RUSAGE_SELF else 200))
 m=soak.memory_snapshot();assert m['tree_peak_upper_bound_bytes']==900*factor


def test_final_parent_bound_includes_API_final_peak_and_all_four_CPU_children(monkeypatch):
 monkeypatch.setattr(soak,'memory_snapshot',lambda:{'api_lifetime_peak_bytes':20*1024**2,'largest_waited_child_peak_bytes':150*1024**2})
 monkeypatch.setattr(soak,'no_children_left',lambda:True)
 monkeypatch.setattr(soak.signal,'getsignal',lambda s:soak.signal.SIG_DFL)
 m=soak.parent_memory_receipt();assert m['accepted'] and m['whole_tree_peak_upper_bound_bytes']==770*1024**2
 monkeypatch.setattr(soak,'memory_snapshot',lambda:{'api_lifetime_peak_bytes':20*1024**2,'largest_waited_child_peak_bytes':210*1024**2})
 assert not soak.parent_memory_receipt()['accepted']
 monkeypatch.setattr(soak,'no_children_left',lambda:False)
 assert not soak.parent_memory_receipt()['accepted']

def test_live_and_unreaped_children_refuse_terminal_acceptance(monkeypatch):
 for result in [(0,0),(123,0)]:
  monkeypatch.setattr(soak.os,'waitpid',lambda *args:result);assert not soak.no_children_left()
 def absent(*args):raise ChildProcessError()
 monkeypatch.setattr(soak.os,'waitpid',absent);assert soak.no_children_left()


def test_actual_short_lived_and_killed_child_peaks_are_retained_after_wait():
 # An isolated fresh interpreter has no earlier child statistics. The large
 # allocation exits before any periodic sampler; wait must still retain it.
 child="""import json,resource,sys,time
b=bytearray(int(sys.argv[1])*1024**2)
for i in range(0,len(b),4096):b[i]=1
print(json.dumps({'peak':resource.getrusage(0).ru_maxrss}),flush=True)
if sys.argv[2]=='hold':time.sleep(30)
"""
 parent="""import json,resource,subprocess,sys
samples=[]
for size,mode in [(128,'exit'),(64,'hold')]:
 p=subprocess.Popen([sys.executable,'-c',sys.argv[1],str(size),mode],stdout=subprocess.PIPE,text=True)
 try:
  peak=json.loads(p.stdout.readline())['peak']
  if mode=='hold':p.kill()
  p.wait(timeout=5)
  samples.append({'own':peak,'waited':resource.getrusage(-1).ru_maxrss,'exit':p.returncode})
 finally:
  if p.poll() is None:p.kill();p.wait()
print(json.dumps(samples))
"""
 out=subprocess.run([sys.executable,'-c',parent,child],capture_output=True,text=True,timeout=15,check=True)
 samples=json.loads(out.stdout);factor=1 if sys.platform=='darwin' else 1024
 assert samples[0]['own']*factor>=128*1024**2
 assert samples[0]['waited']>=samples[0]['own'] and samples[0]['exit']==0
 assert samples[1]['waited']>=max(x['own'] for x in samples) and samples[1]['exit']<0


def test_actual_child_audit_rejects_process_and_socket_io_without_installing_in_test_parent():
 code="""import json,subprocess,sys,socket
from deepfocus_api.quant_cpu_worker import reject_external_io
sys.addaudithook(reject_external_io)
denied=[]
for label,action in [('process',lambda:subprocess.run([sys.executable,'-c','pass'])),('socket',lambda:socket.create_connection(('127.0.0.1',1),timeout=.1))]:
 try:action()
 except PermissionError:denied.append(label)
print(json.dumps(denied))
"""
 out=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=10,check=True)
 assert json.loads(out.stdout)==['process','socket']


@pytest.mark.parametrize('accepted',[True,False])
def test_parent_cannot_report_full_pass_when_worker_passes_but_tree_budget_fails(tmp_path,monkeypatch,accepted):
 workspace=tmp_path/'new';source=tmp_path/'originals.json';source.write_text('[]')
 class Child:
  pid=123456
  def wait(self,timeout=None):
   (workspace/'result.json').write_text('{"unit_fixture":true}');return 0
 monkeypatch.setattr(soak.subprocess,'Popen',lambda *a,**k:Child())
 monkeypatch.setattr(soak,'parent_memory_receipt',lambda:{'accepted':accepted})
 monkeypatch.setattr(soak.signal,'signal',lambda *a:None)
 monkeypatch.setattr(soak.importlib.metadata,'version',lambda *a:'unit-fixture')
 monkeypatch.setattr(sys,'argv',[str(soak.__file__),'--workspace',str(workspace),'--experiments',str(source),'--mode','full'])
 assert soak.main()==(0 if accepted else 2)
 assert json.loads((workspace/'parent-exit.json').read_text())['full_soak_passed'] is accepted
