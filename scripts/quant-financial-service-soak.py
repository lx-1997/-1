#!/usr/bin/env python3
"""Owned JWT/TCP financial-read workload. Smoke is not a one-hour certificate."""
import argparse,asyncio,hashlib,importlib.util,importlib.metadata,ipaddress,json,math,os,resource,secrets,signal,socket,sqlite3,subprocess,sys,threading,time
from contextlib import nullcontext
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PROTOCOL={'schema':'internal-financial-read-soak-v1','duration_seconds':3600,'round_period_seconds':30,
 'smoke_duration_seconds':12,'smoke_round_period_seconds':5,'lanes':4,'owners':2,'observer_owners':1,
 'global_read_limit':4,'owner_read_limit':2,'client_cancel_every_nth_read':5,'barrier_timeout_seconds':5,
 'observer_period_seconds':1,'heartbeat_period_seconds':.01,'read_p95_ms':500,'read_p99_ms':1500,
 'loop_p99_ms':250,'loop_max_ms':2000,'rss_max_bytes':1024**3,'qr_disk_max_bytes':512*1024**2,
 'http_timeout_seconds':5,'minimum_full_rounds':120,'minimum_full_observer_cycles':3000,'minimum_full_heartbeat_samples':200000,'minimum_smoke_heartbeat_samples':500,
 'memory_scope':'control_parent_peak_plus_one_waited_API_peak_no_API_descendants',
 'cancel_semantics':'client_wait_cancelled_server_reads_finish_and_retain_slots_until_exit',
 'observer_barrier_pause':'periodic_monitor_paused_during_registered_quota_probes; controlled_probe_requests_retained',
 'baseline':'direct_current_read_projection_of_same_immutable_packets; independently_checked_not_alpha',
 'scope':'single_local_worker_real_JWT_loopback_TCP_not_production_SLO','promotion_allowed':False}


def dump(p,v):
 with Path(p).open('x') as f:json.dump(v,f,ensure_ascii=False,indent=2,allow_nan=False)


def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def environment():
 return {'python':sys.version,'executable':sys.executable,'packages':{name:importlib.metadata.version(name) for name in ['fastapi','uvicorn','httpx','pydantic','SQLAlchemy','python-jose','passlib','uvloop']}}


def source_pins():
 paths=[p for p in (ROOT/'backend/deepfocus_api').rglob('*.py') if 'tests' not in p.relative_to(ROOT).parts]
 paths+=[ROOT/'scripts/quant-financial-service-soak.py',ROOT/'scripts/quant-event-service-soak.py']
 return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def percentile(v,q):return sorted(v)[math.ceil(len(v)*q)-1] if v else float('inf')


def evaluate(m,mode):
 valid=all(isinstance(m[k],list) and 0<len(m[k])<=500000 and all(type(v) in (int,float) and math.isfinite(v) and v>=0 for v in m[k]) for k in ('query_ms','server_query_ms','observer_ms','loop_ms'))
 thresholds=valid and all(percentile(m[k],.95)<=500 and percentile(m[k],.99)<=1500 for k in ('query_ms','server_query_ms','observer_ms'))
 basic=(not m['errors'] and thresholds and percentile(m['loop_ms'],.99)<=250 and max(m['loop_ms'],default=float('inf'))<=2000
  and len(m['loop_ms'])>=500 and len(m['observer_ms'])>=3*m['observer_cycles']
  and all(type(m[k]) is int and m[k]>=0 for k in ('rounds','server_completed','exact_server_results','client_cancelled','client_completed','quota_checks','remaining_reads','paper_accounts','observer_cycles'))
  and m['server_completed']==m['rounds']*4 and m['exact_server_results']==m['server_completed']
  and m['client_cancelled']==m['rounds']*4//5 and m['client_completed']+m['client_cancelled']==m['server_completed']
  and m['quota_checks']==m['rounds']*3 and m['remaining_reads']==0 and m['paper_accounts']==0
  and m['research_main_unchanged'] and m['peak_qr_disk_bytes']<=PROTOCOL['qr_disk_max_bytes']
  and type(m['memory']['api_lifetime_peak_bytes']) is int and m['memory']['api_lifetime_peak_bytes']>0 and m['memory']['SIGCHLD_not_ignored']
  and m['memory']['api_lifetime_peak_bytes']<=PROTOCOL['rss_max_bytes'] and m['memory']['largest_waited_child_peak_bytes']==0
  and m['memory']['all_children_waited'] and not m['external_connection_attempts'] and not m['forbidden_spawn_attempts'])
 return {'engineering_checks_passed':bool(basic and m['rounds']>=2 and m['observer_cycles']>=6),
  'full_soak_passed':bool(basic and mode=='full' and m['elapsed_seconds']>=3600 and m['rounds']>=120 and m['observer_cycles']>=3000 and len(m['loop_ms'])>=200000)}


def helper():
 spec=importlib.util.spec_from_file_location('owned_financial_soak_common',ROOT/'scripts/quant-event-service-soak.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod


def qr_disk(w):return sum(p.stat().st_size for p in w.glob('research.sqlite3*') if p.is_file())


async def worker(w,plan):
 common=helper();os.environ.update(DEEPFOCUS_DATABASE_URL='sqlite:///'+str(w/'auth.sqlite3'),DEEPFOCUS_DATA_DIR=str(w),DEEPFOCUS_QUANT_RESEARCH_DB_PATH=str(w/'research.sqlite3'),DEEPFOCUS_JWT_SECRET=secrets.token_hex(32),DEEPFOCUS_JWT_TTL_MINUTES='120',DEEPFOCUS_AUTH_REQUIRED='true')
 for k in ('DEEPFOCUS_ADMIN_EMAIL','DEEPFOCUS_ADMIN_PASSWORD'):os.environ.pop(k,None)
 sys.path.insert(0,str(ROOT/'backend'))
 import httpx,uvicorn
 from fastapi import FastAPI,HTTPException
 from deepfocus_api import auth,quant_research_api as api,quant_share_api as slots,quant_financial_api as routes,quant_financial_read as reads,quant_financial_store as base,quant_financial_fact_store as facts
 assert source_pins()==plan['source_pins'] and environment()==plan['environment']
 for item in plan['inputs'].values():assert hashlib.sha256(Path(item['path']).read_bytes()).hexdigest()==item['sha256']
 auth.init_auth();users={k:auth.create_user(None,'isolated-financial-soak-'+k,secrets.token_urlsafe(24),role=auth.ROLE_ANALYST) for k in ('a','b','observer')}
 headers={k:{'Authorization':'Bearer '+auth.create_access_token(u,auth.rotate_session(u.id))} for k,u in users.items()}
 manifest=json.loads(Path(plan['manifest']).read_text());sources={sha:Path(plan['inputs']['source:'+sha]['path']).read_bytes() for sha in manifest['source_files']}
 typed=json.loads(Path(plan['typed_input']).read_text());packets={}
 for who in ('a','b'):
  owner=users[who].id;p=base.put(owner,'Isolated retained five-vintage sample',manifest,sources)
  old=facts.put(owner,p['id'],'Five group profit extractions',typed['facts'][:5]);full=facts.put(owner,p['id'],'43 typed extractions',typed['facts'],supersedes_packet_id=old['id']);packets[who]={'old':old,'full':full}
 # Only this owned DB is converted before the first workload RO query.
 c=sqlite3.connect(w/'research.sqlite3')
 try:assert c.execute('PRAGMA journal_mode=DELETE').fetchone()[0]=='delete'
 finally:c.close()
 db=w/'research.sqlite3';main_before=hashlib.sha256(db.read_bytes()).hexdigest()
 dump(w/'seed.json',{'research_main_sha256':main_before,'parent_archives':2,'extraction_snapshots':4,'facts_per_full_snapshot':43,'paper_accounts':0})
 baseargs=dict(issuer_id='cninfo:9900002009',symbol='002109',period_end='2025-03-31',metric='consolidated_net_profit',as_of='2026-05-01T00:00:00+08:00',mode='historical_reconstruction')
 ttm={'beginning':{'report_period_end':'2024-03-31','column':'current_period'},'ending':{'report_period_end':'2025-03-31','column':'current_period'}}
 annual={'beginning':{'report_period_end':'2024-03-31','column':'previous_year_end_adjusted'},'ending':{'report_period_end':'2025-03-31','column':'previous_year_end'}}
 cases=[('a','full',{**baseargs,'asset_endpoints':ttm}),('a','full',{**baseargs,'as_of':plan['knowledge_as_of'],'mode':'as_recorded','asset_endpoints':ttm}),
  ('b','full',{**baseargs,'period_end':'2024-12-31','as_of':'2025-04-25T00:00:00+08:00','asset_endpoints':annual}),('b','old',{**baseargs,'asset_endpoints':ttm})]
 extras=[('a','full',{**baseargs,'metric':'cashflow_inventory_decrease','asset_endpoints':None}),('b','full',{**baseargs,'period_end':'2024-12-31','metric':'cashflow_long_asset_disposal_loss','asset_endpoints':None})]
 golden=[]
 for who,kind,args in cases+extras:
  value=reads.query(users[who].id,packets[who][kind]['id'],**args);golden.append({'who':who,'id':packets[who][kind]['id'],'request':args,'result':value,'digest':digest(value)})
 assert [g['result']['status'] for g in golden]==['arithmetic_available','unavailable','arithmetic_available','unavailable','unavailable','unavailable']
 dump(w/'golden.json',golden);m={'rounds':0,'client_completed':0,'client_cancelled':0,'server_completed':0,'exact_server_results':0,'quota_checks':0,'observer_cycles':0,'query_ms':[],'server_query_ms':[],'observer_ms':[],'loop_ms':[],'errors':[],'peak_qr_disk_bytes':qr_disk(w),'external_connection_attempts':0,'forbidden_spawn_attempts':0}
 state={'armed':False,'round':0,'arrivals':[],'release':threading.Event()};lookup={(users[g['who']].id,g['id'],digest(g['request'])):i for i,g in enumerate(golden[:4])};query=reads.query;lock=threading.Lock();events=(w/'observations.jsonl').open('x');(w/'results').mkdir()
 def observe(v):
  with lock:events.write(json.dumps(v,ensure_ascii=False,allow_nan=False)+'\n');events.flush()
 def wrapped(owner,identity,**args):
  lane=lookup.get((owner,identity,digest(args)));armed=state if state['armed'] and lane is not None else None;start=time.monotonic()
  if armed is not None:
   armed['arrivals'].append(lane)
   if not armed['release'].wait(5):raise TimeoutError('Fixed read barrier expired')
  value=query(owner,identity,**args)
  if armed is not None:
   elapsed=(time.monotonic()-start)*1000;round_id=armed['round'];assert digest(value)==golden[lane]['digest']
   dump(w/'results'/f'{round_id:04}-{lane}.json',value)
   with lock:m['server_completed']+=1;m['exact_server_results']+=1;m['server_query_ms'].append(elapsed)
   observe({'kind':'server-read','round':round_id,'lane':lane,'result_digest':digest(value),'elapsed_ms':elapsed})
  return value
 reads.query=wrapped;real_read=routes.read
 async def traced(owner,function,*args,**kwargs):
  try:return await real_read(owner,function,*args,**kwargs)
  except HTTPException as e:
   if e.status_code==429:
    counts=[len(slots._reading),sum(x==owner for x in slots._reading.values())];assert counts[0]>=4 or counts[1]>=2
    observe({'kind':'quota-refusal','round':state['round'],'phase':state.get('phase'),'global':counts[0],'same_owner':counts[1]})
   raise
 routes.read=traced
 def audit(event,args):
  if event in {'subprocess.Popen','os.fork','os.forkpty','os.posix_spawn','os.exec','os.system'}:
   m['forbidden_spawn_attempts']+=1;raise PermissionError('No API descendants permitted')
  if event=='socket.connect':
   address=args[1]
   try:allowed=isinstance(address,tuple) and ipaddress.ip_address(address[0]).is_loopback
   except ValueError:allowed=False
   if not allowed:m['external_connection_attempts']+=1;raise OSError('Loopback only')
 sys.addaudithook(audit)
 app=FastAPI();app.include_router(api.router);app.add_middleware(auth.AuthMiddleware)
 listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(128);port=listener.getsockname()[1]
 server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,access_log=False,log_level='warning'));server.capture_signals=lambda:nullcontext();serving=asyncio.create_task(server.serve(sockets=[listener]));stopped=asyncio.Event();reason=[]
 for sig in (signal.SIGINT,signal.SIGTERM):asyncio.get_running_loop().add_signal_handler(sig,lambda s=sig:(reason.append(s.name),stopped.set()))
 while not server.started:
  if serving.done():raise RuntimeError('API failed to start')
  await asyncio.sleep(.01)
 dump(w/'runtime.json',{'pid':os.getpid(),'port':port,'parent_pid':os.getppid(),'isolated':True,'environment':environment()})
 def url(g):
  from urllib.parse import urlencode
  args=dict(g['request']);ep=args.pop('asset_endpoints')
  if ep:
   for side,key in [('begin','beginning'),('end','ending')]:args[side+'_report_period_end']=ep[key]['report_period_end'];args[side+'_column']=ep[key]['column']
  return '/api/quant-research/financial-extractions/'+g['id']+'/derive?'+urlencode(args)
 pause=False;observer_active=False;monitors=[];started=None
 async def sample(client,path,who,status=200,category='probe'):
  t=time.monotonic();r=await client.get(path,headers=headers[who]);ms=(time.monotonic()-t)*1000;assert r.status_code==status,(r.status_code,status)
  observe({'kind':'http','status':r.status_code,'elapsed_ms':ms,'route':path,'who':who,'body_digest':digest(r.json()),'category':category});return r,ms
 async def observer(client):
  nonlocal observer_active
  while not stopped.is_set():
   start=time.monotonic()
   if not pause:
    observer_active=True
    try:
     r,ms=await sample(client,'/api/quant-research/financial-extractions','observer',category='observer');assert r.json()=={'items':[],'next_cursor':None};m['observer_ms'].append(ms)
     for path in ['/api/quant-research/financial-extractions/'+golden[0]['id'],url(golden[0])]:
      _,ms=await sample(client,path,'observer',404,category='observer');m['observer_ms'].append(ms)
     m['observer_cycles']+=1
    finally:observer_active=False
   await asyncio.sleep(max(0,1-(time.monotonic()-start)))
 async def heartbeat():
  previous=time.monotonic()
  while not stopped.is_set():
   await asyncio.sleep(.01);now=time.monotonic();m['loop_ms'].append(max(0,(now-previous-.01)*1000));previous=now
 duration=3600 if plan['mode']=='full' else 12;period=30 if plan['mode']=='full' else 5
 try:
  async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}',timeout=5,trust_env=False) as client:
   started=time.monotonic();deadline=started+duration;monitors=[asyncio.create_task(observer(client)),asyncio.create_task(heartbeat())]
   try:
    while time.monotonic()<deadline and not stopped.is_set():
     start=time.monotonic();pause=True
     while observer_active:await asyncio.sleep(.001)
     state={'armed':True,'round':m['rounds'],'arrivals':[],'release':threading.Event(),'phase':'owner'};pending=[]
     async def lane(i):
      g=golden[i];r,ms=await sample(client,url(g),g['who'],category='lane');assert r.json()==g['result'];return ms
     async def arrivals(count):
      until=time.monotonic()+5
      while len(state['arrivals'])<count:
       if time.monotonic()>until:raise TimeoutError('Read capacity arrivals missing')
       await asyncio.sleep(.001)
     try:
      pending=[asyncio.create_task(lane(i)) for i in range(2)];await arrivals(2)
      assert len(slots._reading)==2;observe({'kind':'owner-capacity','round':m['rounds'],'global_reads':2,'same_owner_reads':2})
      await sample(client,'/api/quant-research/financial-extractions','a',429);m['quota_checks']+=1
      r,_=await sample(client,'/api/quant-research/financial-extractions','observer');assert not r.json()['items']
      pending.extend(asyncio.create_task(lane(i)) for i in range(2,4));await arrivals(4);assert len(slots._reading)==4;observe({'kind':'global-capacity','round':m['rounds'],'global_reads':4})
      state['phase']='global';await sample(client,'/api/quant-research/financial-extractions','observer',429);m['quota_checks']+=1
      cancelled=[i for i in range(4) if (m['rounds']*4+i+1)%5==0]
      for i in cancelled:pending[i].cancel()
      if cancelled:await asyncio.gather(*(pending[i] for i in cancelled),return_exceptions=True)
      assert len(slots._reading)==4;observe({'kind':'client-cancel','round':m['rounds'],'lanes':cancelled,'reads_held_after':len(slots._reading)})
      state['phase']='after_client_cancel';await sample(client,'/api/quant-research/financial-extractions','b',429);m['quota_checks']+=1
      state['release'].set();completed=await asyncio.gather(*pending,return_exceptions=True)
      for i,value in enumerate(completed):
       if i in cancelled:assert isinstance(value,asyncio.CancelledError);m['client_cancelled']+=1
       else:assert isinstance(value,(int,float));m['client_completed']+=1;m['query_ms'].append(value)
      until=time.monotonic()+5
      while slots._reading:
       if time.monotonic()>until:raise TimeoutError('Real read threads did not drain')
       await asyncio.sleep(.001)
      state['armed']=False
      for g in golden[4:]:
       r,ms=await sample(client,url(g),g['who'],category='extra');assert r.json()==g['result'];m['query_ms'].append(ms)
      m['rounds']+=1;m['peak_qr_disk_bytes']=max(m['peak_qr_disk_bytes'],qr_disk(w));observe({'kind':'round','index':m['rounds'],'elapsed_seconds':time.monotonic()-started,'client_cancelled':m['client_cancelled'],'server_completed':m['server_completed']})
      print(json.dumps({'round':m['rounds'],'server_completed':m['server_completed'],'client_cancelled':m['client_cancelled']}),flush=True)
     finally:
      state['release'].set();state['armed']=False
      await asyncio.gather(*pending,return_exceptions=True);pause=False
     wait=min(max(0,period-(time.monotonic()-start)),max(0,deadline-time.monotonic()))
     if wait:
      try:await asyncio.wait_for(stopped.wait(),wait)
      except asyncio.TimeoutError:pass
    if reason:raise InterruptedError(reason[-1])
    m['elapsed_seconds']=time.monotonic()-started
   finally:
    stopped.set()
    for e in await common.drain_monitors(monitors):
     if isinstance(e,Exception):m['errors'].append({'stage':'monitor','type':type(e).__name__})
    monitors=[]
 except BaseException as exc:m['errors'].append({'stage':'workload','type':type(exc).__name__,'detail':str(exc)[:160]})
 finally:
  stopped.set();state['release'].set();await common.drain_monitors(monitors);server.should_exit=True;await serving;listener.close();events.close()
 m['elapsed_seconds']=m.get('elapsed_seconds',time.monotonic()-started if started else 0);m['remaining_reads']=len(slots._reading);m['research_main_unchanged']=hashlib.sha256(db.read_bytes()).hexdigest()==main_before
 c=sqlite3.connect(db.as_uri()+'?mode=ro',uri=True)
 try:m['paper_accounts']=c.execute('select count(*) from qr_paper').fetchone()[0]
 finally:c.close()
 m['memory']={**common.memory_snapshot(),'all_children_waited':common.no_children_left(),'SIGCHLD_not_ignored':signal.getsignal(signal.SIGCHLD)!=signal.SIG_IGN}
 if environment()!=plan['environment']:m['errors'].append({'type':'EnvironmentChanged'})
 if source_pins()!=plan['source_pins']:m['errors'].append({'type':'SourceChanged'})
 if any(hashlib.sha256(Path(v['path']).read_bytes()).hexdigest()!=v['sha256'] for v in plan['inputs'].values()):m['errors'].append({'type':'InputChanged'})
 result={'protocol':PROTOCOL,'mode':plan['mode'],'metrics':m,'evaluation':evaluate(m,plan['mode']),'production_writes':0,'strategy_evaluations':0,'promotion_allowed':False};dump(w/'result.json',result)
 return 0 if result['evaluation']['full_soak_passed' if plan['mode']=='full' else 'engineering_checks_passed'] else 2


def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--workspace',type=Path,required=True);parser.add_argument('--manifest',type=Path);parser.add_argument('--evidence-dir',type=Path);parser.add_argument('--typed-input',type=Path);parser.add_argument('--mode',choices=['smoke','full'],default='full');parser.add_argument('--worker',action='store_true',help=argparse.SUPPRESS);a=parser.parse_args();w=a.workspace
 if a.worker:
  plan=json.loads((w/'plan.json').read_text());assert os.getppid()==plan['parent_pid'] and hashlib.sha256(os.environ.pop('QUANT_FINANCIAL_SOAK_NONCE').encode()).hexdigest()==plan['nonce_sha256'];return asyncio.run(worker(w,plan))
 if not w.is_absolute() or w.exists() or not all(x and x.exists() for x in (a.manifest,a.evidence_dir,a.typed_input)):parser.error('New absolute workspace and retained manifest/evidence/typed input required')
 if __import__('shutil').disk_usage(w.parent).free<768*1024**2:parser.error('At least 768MiB free required')
 inputs={};manifest=json.loads(a.manifest.read_text());folder=a.evidence_dir.resolve()
 for name,path in [('manifest',a.manifest),('typed_input',a.typed_input)]+[('source:'+sha,folder/file) for sha,file in manifest['source_files'].items()]:
  path=path.resolve()
  if name.startswith('source:') and (path.parent!=folder or path.stat().st_size>10*1024**2):parser.error('Invalid retained source path')
  body=path.read_bytes();inputs[name]={'path':str(path),'sha256':hashlib.sha256(body).hexdigest()}
  if name.startswith('source:') and inputs[name]['sha256']!=name[7:]:parser.error('Retained source bytes differ')
 w.mkdir();nonce=secrets.token_hex(32);plan={'protocol':PROTOCOL,'mode':a.mode,'parent_pid':os.getpid(),'nonce_sha256':hashlib.sha256(nonce.encode()).hexdigest(),'knowledge_as_of':datetime.now(timezone.utc).isoformat(),'source_pins':source_pins(),'environment':environment(),'manifest':str(a.manifest.resolve()),'typed_input':str(a.typed_input.resolve()),'inputs':inputs};dump(w/'plan.json',plan)
 for relative,sha in plan['source_pins'].items():
  target=w/'source-archive'/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((ROOT/relative).read_bytes());assert hashlib.sha256(target.read_bytes()).hexdigest()==sha
 print(json.dumps({'plan_sha256':hashlib.sha256((w/'plan.json').read_bytes()).hexdigest(),'mode':a.mode}),flush=True)
 env={**os.environ,'QUANT_FINANCIAL_SOAK_NONCE':nonce};p=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--workspace',str(w),'--worker'],env=env)
 try:code=p.wait()
 except KeyboardInterrupt:p.send_signal(signal.SIGTERM);code=p.wait()
 memory=helper().parent_memory_receipt();dump(w/'parent-memory.json',memory);dump(w/'exit.json',{'worker_exit_code':code,'worker_pid':p.pid,'parent_pid':os.getpid(),'all_children_waited':True,'environment_unchanged':environment()==plan['environment']})
 return code if memory['accepted'] and environment()==plan['environment'] else 2


if __name__=='__main__':raise SystemExit(main())
