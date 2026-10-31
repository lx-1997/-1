#!/usr/bin/env python3
"""Owned event/calendar JWT/TCP soak; isolated DBs and retained evidence only.

Smoke is an engineering check and can NEVER certify the fixed one-hour run.
No broker, production service, supplier request or strategy qualification.
"""
import argparse
import asyncio
import copy
from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import ipaddress
import json
import math
import os
from pathlib import Path
import resource
import re
import secrets
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import threading

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = {
    'schema':'internal-event-soak-v2','duration_seconds':3600,'round_period_seconds':30,
    'parallel_lanes':4,'research_owners':2,'observer_owners':1,
    'cancel_every_nth_job':5,'cancel_delay_seconds':.05,'status_poll_seconds':.5,
    'observer_period_seconds':1,'heartbeat_period_seconds':.01,
    'max_job_seconds':180,'read_p95_ms':500,'read_p99_ms':1500,
    'create_p95_ms':2000,'loop_p99_ms':250,'loop_max_ms':2000,
    'rss_max_bytes':1024**3,'qr_disk_max_bytes':512*1024**2,
    'rss_scope':'API_lifetime_peak_including_all_event_threads_no_descendants',
    'rss_descendant_limit':0,'rss_requires_all_children_waited':True,
    'whole_owned_tree_rss_scope':'control_parent_peak_plus_waited_API_lifetime_peak_after_exit',
    'whole_owned_tree_descendant_limit':1,
    'CPU_descendants_and_socket_connections':'all_descendants_and_nonloopback_connects_denied_by_fixed_private_entrypoint',
    'minimum_completed_jobs':32,'minimum_cancelled_jobs':10,
    'minimum_rounds':30,'minimum_observer_reads':600,'minimum_heartbeat_samples':600,
    'global_worker_limit':4,'owner_worker_limit':2,
    'source_adapter':'two_original_frozen_729_day_parents_31_cash_facts_8_annual_notices_per_owner_no_network',
    'barrier':'four_real_calendar_check_threads_wait_until_burst_capacity_checks_finish',
    'scope':'single_local_API_worker_real_JWT_and_loopback_TCP_not_production_SLO',
    'promotion_allowed':False,'knowledge_modes':['historical_reconstruction','as_recorded'],
    'lane_cases':[0,1,0,1],
    'lane_modes':['historical_reconstruction','as_recorded','as_recorded','historical_reconstruction'],
    'golden_result':'direct_current_event_engine_plus_service_metadata_before_timed_workload',
}

SOURCES = ('auth.py','storage.py','db.py','quant_research.py','quant_research_api.py',
           'quant_research_store.py','quant_research_engine.py','quant_research_candidates.py',
           'quant_calendar.py','quant_paper_calendar.py','quant_calendar_service.py',
           'quant_calendar_store.py','quant_exchange_schedule.py','quant_worker_slots.py',
           'backtest_data.py','ai_fund_ledger.py','ai_fund_policy.py',
           'quant_cpu_process.py','quant_cpu_worker.py','quant_event_service.py','quant_event_replay.py',
           'quant_event_policy.py','quant_dividend_book.py','quant_corporate_actions.py','quant_evidence_store.py')


def dump(path, value):
    with Path(path).open('x') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False)


def source_pins():
    return {**{name:hashlib.sha256((ROOT/'backend/deepfocus_api'/name).read_bytes()).hexdigest()
               for name in SOURCES},'quant-event-service-soak.py':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def percentile(values, fraction):
    if not values:return None
    return sorted(values)[max(0, math.ceil(len(values)*fraction)-1)]


def evaluate(metrics, mode):
    """Fixed checks; missing observations never turn into zero latency/pass."""
    read=metrics['observer_ms'];create=metrics['create_ms'];loop=metrics['loop_ms']
    def under(value, maximum):return value is not None and math.isfinite(value) and value<=maximum
    checks={
        'read_p95':under(percentile(read,.95),PROTOCOL['read_p95_ms']),
        'read_p99':under(percentile(read,.99),PROTOCOL['read_p99_ms']),
        'create_p95':under(percentile(create,.95),PROTOCOL['create_p95_ms']),
        'loop_p99':under(percentile(loop,.99),PROTOCOL['loop_p99_ms']),
        'loop_max':under(max(loop) if loop else None,PROTOCOL['loop_max_ms']),
        'rss':under(metrics['peak_rss_bytes'],PROTOCOL['rss_max_bytes']),
        'rss_tree':memory_ok(metrics),
        'disk':under(metrics['peak_qr_disk_bytes'],PROTOCOL['qr_disk_max_bytes']),
        'worker_caps':metrics['peak_global_slots']<=4 and metrics['peak_owner_slots']<=2,
        'four_active_slots_observed':metrics['peak_active_slots']==4,
        'no_unexpected_errors':not metrics['errors'],
        'no_external_connections':metrics['external_connection_attempts']==0,
        'all_results_exact':metrics['completed']>0 and metrics['exact_results']==metrics['completed'],
        'all_drained':metrics['remaining_slots']==0 and metrics['remaining_admissions']==0,
        'no_paper_accounts':metrics['paper_accounts']==0,
    }
    full={
        'mode':mode=='full','duration':metrics['elapsed_seconds']>=PROTOCOL['duration_seconds'],
        'completed':metrics['completed']>=PROTOCOL['minimum_completed_jobs'],
        'cancelled':metrics['cancelled']>=PROTOCOL['minimum_cancelled_jobs'],
        'rounds':metrics['rounds']>=PROTOCOL['minimum_rounds'],
        'reads':len(read)>=PROTOCOL['minimum_observer_reads'],
        'heartbeats':len(loop)>=PROTOCOL['minimum_heartbeat_samples'],
    }
    return {'safety_checks':checks,'full_run_checks':full,
            'engineering_checks_passed':all(checks.values()),
            'full_soak_passed':all(checks.values()) and all(full.values()),
            'latency_ms':{'read_p95':percentile(read,.95),'read_p99':percentile(read,.99),
                          'create_p95':percentile(create,.95),'loop_p99':percentile(loop,.99),
                          'loop_max':max(loop) if loop else None},
            'promotion_allowed':False,'production_SLO_certified':False}


def qr_disk(workspace):
    return sum(p.stat().st_size for p in workspace.glob('research.sqlite3*') if p.is_file())


def rss():
    value=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform=='darwin' else value*1024


def memory_snapshot():
    if sys.platform!='darwin' and not sys.platform.startswith('linux'):
        raise ValueError('Uncalibrated RSS platform')
    value=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    child=value if sys.platform=='darwin' else value*1024
    own=rss()
    return {'api_lifetime_peak_bytes':own,'largest_waited_child_peak_bytes':child,
            'descendant_limit':0,'tree_peak_upper_bound_bytes':own}


def no_children_left():
    # Only in this owned isolated worker after service shutdown/drain. A live
    # child or an unexpectedly unreaped zombie refuses acceptance.
    try:os.waitpid(-1,os.WNOHANG)
    except ChildProcessError:return True
    return False


def memory_ok(metrics):
    m=metrics.get('memory',{});own=m.get('api_lifetime_peak_bytes')
    return (type(own) is int and own>0 and m.get('largest_waited_child_peak_bytes')==0
            and m.get('descendant_limit')==0 and m.get('all_children_waited') is True
            and m.get('SIGCHLD_not_ignored') is True and m.get('forbidden_spawn_attempts')==0
            and m.get('CPU_spawn_attempts')==0 and m.get('tree_peak_upper_bound_bytes')==own
            and own<=PROTOCOL['rss_max_bytes'])


def parent_memory_receipt():
    """After the owned API exits, its final peak and reaped CPU peaks roll up.

    Exactly one permitted descendant is the API worker; event computation uses threads.
    This deliberately overcounts shared pages and non-simultaneous peaks; it
    is an upper bound, never a sampled instantaneous sum or a measured peak.
    """
    m=memory_snapshot();own=m['api_lifetime_peak_bytes'];largest=m['largest_waited_child_peak_bytes']
    absent=no_children_left();sig=signal.getsignal(signal.SIGCHLD)!=signal.SIG_IGN
    bound=own+largest
    return {'control_parent_peak_bytes':own,'largest_waited_descendant_peak_bytes':largest,
            'descendant_limit':1,'whole_tree_peak_upper_bound_bytes':bound,
            'all_children_waited':absent,'SIGCHLD_not_ignored':sig,
            'accepted':absent and sig and largest>0 and bound<=PROTOCOL['rss_max_bytes']}


async def drain_monitors(tasks):
    # Finish owned reads while their HTTP client is still alive; retain failures.
    for task in tasks:task.cancel()
    return await asyncio.gather(*tasks,return_exceptions=True)


async def run_worker(workspace, plan):
    # Environment is bound before importing auth/storage or any application module.
    os.environ.update(DEEPFOCUS_DATABASE_URL='sqlite:///'+str(workspace/'auth.sqlite3'),
        DEEPFOCUS_DATA_DIR=str(workspace),DEEPFOCUS_QUANT_RESEARCH_DB_PATH=str(workspace/'research.sqlite3'),
        DEEPFOCUS_JWT_SECRET=secrets.token_hex(32),DEEPFOCUS_JWT_TTL_MINUTES='120',
        DEEPFOCUS_AUTH_REQUIRED='true')
    for key in ('DEEPFOCUS_ADMIN_EMAIL','DEEPFOCUS_ADMIN_PASSWORD'):os.environ.pop(key,None)
    sys.path.insert(0,str(ROOT/'backend'))
    import httpx
    import uvicorn
    from fastapi import FastAPI
    from deepfocus_api import auth,quant_research as service,quant_research_api as api
    from deepfocus_api import quant_research_store as store
    from deepfocus_api.quant_research_engine import digest
    from deepfocus_api import quant_event_service as events,quant_evidence_store as evidence,quant_calendar_store as calendars
    from deepfocus_api.quant_corporate_actions import CashDividend
    from deepfocus_api.quant_dividend_book import INVESTOR
    assert source_pins()==plan['source_pins']
    raw=Path(plan['experiments']).read_bytes();assert hashlib.sha256(raw).hexdigest()==plan['experiments_sha256']
    experiments=json.loads(raw)
    assert len(experiments)==2 and all(digest(x['result'])==x['result_digest'] for x in experiments)
    assert all(len(next(iter(x['dataset'].values()))['bars'])==729 for x in experiments)
    for name,item in plan['evidence_inputs'].items():
        assert hashlib.sha256(Path(item['path']).read_bytes()).hexdigest()==item['sha256'],name
    auth.init_auth()
    users={k:auth.create_user(None,'isolated-soak-'+k,secrets.token_urlsafe(24),role=auth.ROLE_ANALYST)
           for k in ('a','b','observer')}
    headers={k:{'Authorization':'Bearer '+auth.create_access_token(u,auth.rotate_session(u.id))} for k,u in users.items()}
    facts=json.loads(Path(plan['events']).read_text());calendar_manifest=json.loads(Path(plan['calendar']).read_text())
    cash_events=[CashDividend(**x) for x in facts['events']]
    cash_sources={sha:Path(plan['evidence_inputs']['cash:'+sha]['path']).read_bytes() for sha in facts['source_files']}
    calendar_sources={sha:Path(plan['evidence_inputs']['calendar:'+sha]['path']).read_bytes() for sha in calendar_manifest['source_files']}
    assert len(cash_events)==31 and len(calendar_sources)==8
    parents={};bundles={};calendar_bundles={};expected={};parent_before={}
    for who in ('a','b'):
        owner=users[who].id
        bundles[who]=evidence.put(owner,'Isolated event soak retained cash facts',cash_events,cash_sources)
        calendar_bundles[who]=calendars.put(owner,'Isolated event soak annual plan',calendar_manifest,calendar_sources)
        parents[who]=[]
        for i,x in enumerate(experiments):
            job,_=store.create(owner,'seed-'+str(i),x['request'],x['kernel'])
            store.update(owner,job['id'],status='completed',extra={k:x[k] for k in ('dataset','quality','result','result_digest')})
            parent_before[(who,job['id'])]=store.get(owner,job['id']);parents[who].append(job['id'])
    # Independent direct numerical baseline; no production service worker called here.
    for i,x in enumerate(experiments):
        schedule=[{k:f[k] for k in ('train_end','test_start','test_end','parameters')} for f in x['result']['folds']]
        q=events.check_dataset(calendar_manifest,x['dataset'],x['request']['start_date'],x['request']['end_date'],as_of=store.now())
        assert q['status']=='matches_plan'
        for who in ('a','b'):
            mode=PROTOCOL['lane_modes'][i+(2 if who=='b' else 0)]
            core=events.engine.replay(x['dataset'],x['request']['symbols'],schedule[0]['parameters'],
                [e for e in cash_events if e.symbol in x['request']['symbols']],cash_sources,investor=INVESTOR,
                mode=mode,capital=x['request']['capital'],start=schedule[0]['test_start'],end=schedule[-1]['test_end'],schedule=schedule)
            assert len(core['curve'])==535 and len(core['fills'])>0,'Golden must exercise active positions'
            result={**core,'parent_oos':{k:v for k,v in x['result']['oos'].items() if k!='curve'},
                'evidence':bundles[who],'source_dataset_digest':x['result']['dataset_digest'],
                'calendar_evidence':calendar_bundles[who],'calendar_quality':q,
                'validation_status':'seen_history_development_only','holdout_replayed':False}
            expected[(who,i)]=digest(result)
    dump(workspace/'golden-results.json',{'kernel':events.KERNEL,'digests':{who:[expected[(who,i)] for i in range(2)] for who in ('a','b')},'modes':PROTOCOL['knowledge_modes'],'lane_modes':PROTOCOL['lane_modes'],'original_parent_count':4})
    metrics={'observer_ms':[],'create_ms':[],'loop_ms':[],'errors':[],'rounds':0,'submitted':0,
             'completed':0,'exact_results':0,'cancelled':0,'cancel_requested':0,'cancel_missed':0,
             'peak_rss_bytes':rss(),'peak_qr_disk_bytes':0,'peak_global_slots':0,'peak_owner_slots':0,'peak_active_slots':0,
             'remaining_slots':None,'remaining_admissions':None,'external_connection_attempts':0}
    spawn={'CPU_spawn_attempts':0,'forbidden_spawn_attempts':0}
    def audit(event,args):
        if event in {'subprocess.Popen','os.fork','os.forkpty','os.posix_spawn','os.exec','os.system'}:
            spawn['forbidden_spawn_attempts']+=1
            raise PermissionError('No descendants allowed in owned event API worker')
        if event=='socket.connect':
            address=args[1]
            try:allowed=isinstance(address,tuple) and ipaddress.ip_address(address[0]).is_loopback
            except ValueError:allowed=False
            if not allowed:
                metrics['external_connection_attempts']+=1
                raise OSError('External network is forbidden in isolated research soak')
    sys.addaudithook(audit)
    barrier=threading.Event();arrivals=[];check_calendar=events.check_dataset
    def checked_calendar(*args,**kwargs):
        arrivals.append(True)
        if not barrier.wait(10):raise TimeoutError('Registered calendar barrier expired')
        return check_calendar(*args,**kwargs)
    events.check_dataset=checked_calendar
    async def forbidden_fetch(*args,**kwargs):raise AssertionError('No provider fetch permitted')
    service.fetch_dataset=forbidden_fetch
    app=FastAPI();app.include_router(api.router);app.add_middleware(auth.AuthMiddleware)
    service.attach_lifespan(app)
    @app.get('/qa/health')
    async def health():return {'isolated':True,'ready':True}
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(128)
    port=listener.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,access_log=False,log_level='warning'))
    # Keep the owned runner's signal handlers; do not let Uvicorn replace them.
    server.capture_signals=lambda:nullcontext()
    stopped=asyncio.Event();stop_reason=[]
    loop=asyncio.get_running_loop()
    for sig in (signal.SIGTERM,signal.SIGINT):
        loop.add_signal_handler(sig,lambda s=sig:(stop_reason.append('signal_'+s.name),stopped.set()))
    log=(workspace/'observations.jsonl').open('x')
    def observe(value):log.write(json.dumps(value,allow_nan=False)+'\n');log.flush()
    serving=asyncio.create_task(server.serve(sockets=[listener]));started=None;monitors=[]
    async def request(client,method,path,who='a',**kwargs):
        before=time.monotonic()
        response=await client.request(method,path,headers=headers[who] if who else {},**kwargs)
        ms=(time.monotonic()-before)*1000
        observe({'kind':'http','method':method,'route':re.sub(r'(?<=/experiments/)[a-f0-9]{32}', '{id}', path),
                 'status':response.status_code,'elapsed_ms':ms,'response_bytes':len(response.content)})
        return response,ms
    async def heartbeat():
        while not stopped.is_set():
            before=time.monotonic();await asyncio.sleep(.01)
            metrics['loop_ms'].append(max(0,time.monotonic()-before-.01)*1000)
    async def observer(client):
        while not stopped.is_set():
            for path in ('/experiments','/evidence','/calendar-evidence'):
                response,ms=await request(client,'GET','/api/quant-research'+path,'observer')
                if response.status_code!=200 or response.json().get('items')!=[]:
                    raise AssertionError('Observer isolation or availability failed')
                metrics['observer_ms'].append(ms)
                observe({'kind':'observer','route':path,'elapsed_ms':ms,'items':len(response.json()['items'])})
            await asyncio.sleep(1)
    def occupancy():
        c=sqlite3.connect((workspace/'research.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=2)
        try:
            total=c.execute('SELECT count(*) FROM qr_worker_slot').fetchone()[0]
            own=max([r[0] for r in c.execute('SELECT count(*) FROM qr_worker_slot GROUP BY owner')],default=0)
            active=c.execute("SELECT count(*) FROM qr_worker_slot WHERE phase='active'").fetchone()[0]
            return total,own,active
        finally:c.close()
    async def sample():
        total,own,active=await asyncio.to_thread(occupancy)
        metrics['peak_global_slots']=max(metrics['peak_global_slots'],total)
        metrics['peak_owner_slots']=max(metrics['peak_owner_slots'],own)
        metrics['peak_active_slots']=max(metrics['peak_active_slots'],active)
        metrics['peak_qr_disk_bytes']=max(metrics['peak_qr_disk_bytes'],qr_disk(workspace))
        metrics['peak_rss_bytes']=max(metrics['peak_rss_bytes'],rss())
        observe({'kind':'occupancy','global_slots':total,'owner_slots':own,'active_slots':active,
                 'rss_bytes':rss(),'qr_disk_bytes':qr_disk(workspace)})
        if total>4 or own>2:raise AssertionError('Worker capacity violated')
        if metrics['peak_qr_disk_bytes']>PROTOCOL['qr_disk_max_bytes'] or rss()>PROTOCOL['rss_max_bytes']:
            raise AssertionError('Resource safety budget exceeded')
        if memory_snapshot()['tree_peak_upper_bound_bytes']>PROTOCOL['rss_max_bytes']:
            raise AssertionError('Waited-child memory upper bound exceeds fixed budget')
    try:
        for _ in range(100):
            if server.started:break
            if serving.done():await serving
            await asyncio.sleep(.05)
        assert server.started,'Loopback server did not start'
        dump(workspace/'owned-worker.json',{'pid':os.getpid(),'parent_pid':os.getppid(),'port':port,
             'workspace':str(workspace),'script_sha256':plan['source_pins']['quant-event-service-soak.py']})
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}',trust_env=False,timeout=5) as client:
            try:
                anonymous,_=await request(client,'GET','/api/quant-research/experiments',None)
                assert anonymous.status_code==401
                started=time.monotonic();duration=3600 if plan['mode']=='full' else 10
                deadline=started+duration;period=30 if plan['mode']=='full' else 1
                monitors=[asyncio.create_task(heartbeat()),asyncio.create_task(observer(client))]
                while time.monotonic()<deadline and not stopped.is_set():
                    if source_pins()!=plan['source_pins']:raise ValueError('Soak source changed during run')
                    assert hashlib.sha256(Path(plan['experiments']).read_bytes()).hexdigest()==plan['experiments_sha256']
                    for item in plan['evidence_inputs'].values():
                        assert hashlib.sha256(Path(item['path']).read_bytes()).hexdigest()==item['sha256']
                    for monitor in monitors:
                        if monitor.done():await monitor
                    round_start=time.monotonic();barrier.clear();arrivals.clear();round_index=metrics['rounds'];lanes=[]
                    async def submit_lane(lane):
                        x=experiments[lane%2];who='a' if lane<2 else 'b';key=f'soak-{round_index:04}-{lane}'
                        payload={'bundle_id':bundles[who]['id'],'calendar_bundle_id':calendar_bundles[who]['id'],
                            'knowledge_mode':PROTOCOL['lane_modes'][lane],'request_id':key}
                        route='/api/quant-research/experiments/'+parents[who][lane%2]+'/event-replay'
                        response,ms=await request(client,'POST',route,who,json=payload)
                        metrics['create_ms'].append(ms)
                        observe({'kind':'submission','round':round_index,'lane':lane,'elapsed_ms':ms,'status':response.status_code})
                        assert response.status_code==202,(response.status_code,'burst submission failed')
                        return {'id':response.json()['id'],'who':who,'payload':payload,'route':route,'expected':expected[(who,lane%2)],
                                'cancel':(round_index*4+lane+1)%5==0}
                    lanes=await asyncio.gather(*(submit_lane(lane) for lane in range(4)))
                    metrics['submitted']+=4
                    while len(arrivals)<4:
                        if time.monotonic()-round_start>5:raise TimeoutError('Four declared workers did not reach retained adapter')
                        await asyncio.sleep(.01)
                    await sample()
                    assert metrics['peak_global_slots']==4
                    overflow={**lanes[0]['payload'],'request_id':f'overflow-{round_index:04}'}
                    rejected,_=await request(client,'POST',lanes[0]['route'],'a',json=overflow)
                    assert rejected.status_code==429
                    retry,_=await request(client,'POST',lanes[0]['route'],lanes[0]['who'],json=lanes[0]['payload'])
                    assert retry.status_code==202 and retry.json()['id']==lanes[0]['id']
                    foreign,_=await request(client,'GET','/api/quant-research/experiments/'+lanes[0]['id'],'observer')
                    assert foreign.status_code==404
                    barrier.set();await asyncio.sleep(.05)
                    for lane in lanes:
                        if lane['cancel']:
                            cancelled,_=await request(client,'DELETE','/api/quant-research/experiments/'+lane['id'],lane['who'])
                            assert cancelled.status_code==200
                            metrics['cancel_requested']+=1
                    remaining=list(lanes)
                    while remaining:
                        if time.monotonic()-round_start>180:raise TimeoutError('Research round exceeded fixed job budget')
                        for lane in list(remaining):
                            response,_=await request(client,'GET','/api/quant-research/experiments/'+lane['id'],lane['who'])
                            assert response.status_code==200
                            result=response.json();status=result['status']
                            if status=='completed':
                                assert result['result_digest']==lane['expected'] and digest(result['result'])==lane['expected']
                                metrics['completed']+=1;metrics['exact_results']+=1
                                if lane['cancel']:metrics['cancel_missed']+=1
                                remaining.remove(lane)
                            elif status=='cancelled':
                                assert lane['cancel'];metrics['cancelled']+=1;remaining.remove(lane)
                            elif status!='running':raise AssertionError('Unexpected research terminal state')
                        await sample()
                        if remaining:await asyncio.sleep(.5)
                    # Terminal rows do not prove real worker exit; retain occupancy checks.
                    while service._tasks or service._admission_tasks:
                        if time.monotonic()-round_start>180:raise TimeoutError('Workers did not drain')
                        await sample();await asyncio.sleep(.05)
                    await sample();metrics['rounds']+=1
                    observe({'kind':'round','index':round_index,'elapsed_seconds':time.monotonic()-started,
                             'completed':metrics['completed'],'cancelled':metrics['cancelled'],
                             'rss_bytes':rss(),'qr_disk_bytes':qr_disk(workspace)})
                    print(json.dumps({'round':metrics['rounds'],'completed':metrics['completed'],'cancelled':metrics['cancelled']}),flush=True)
                    wait=min(max(0,period-(time.monotonic()-round_start)),max(0,deadline-time.monotonic()))
                    if wait:
                        try:await asyncio.wait_for(stopped.wait(),wait)
                        except asyncio.TimeoutError:pass
                if stop_reason:raise InterruptedError(stop_reason[-1])
                metrics['elapsed_seconds']=time.monotonic()-started
            finally:
                stopped.set()
                for result in await drain_monitors(monitors):
                    if isinstance(result,Exception):metrics['errors'].append({'type':type(result).__name__,'stage':'monitor'})
                monitors=[]
    except BaseException as exc:
        metrics['errors'].append({'type':type(exc).__name__,'stage':'isolated_soak','detail':str(exc)[:300],'signal':stop_reason[-1] if stop_reason else None})
        metrics['elapsed_seconds']=time.monotonic()-started if started else 0
    finally:
        stopped.set();barrier.set()
        monitor_results=await drain_monitors(monitors)
        for result in monitor_results:
            if isinstance(result,Exception):metrics['errors'].append({'type':type(result).__name__,'stage':'monitor'})
        server.should_exit=True
        await serving
        listener.close();log.close()
    total,_,_=await asyncio.to_thread(occupancy)
    metrics['remaining_slots']=total;metrics['remaining_admissions']=len(service._admission_tasks)
    with sqlite3.connect((workspace/'research.sqlite3').as_uri()+'?mode=ro',uri=True) as c:
        metrics['paper_accounts']=c.execute('SELECT count(*) FROM qr_paper').fetchone()[0]
    metrics['peak_qr_disk_bytes']=max(metrics['peak_qr_disk_bytes'],qr_disk(workspace))
    metrics['peak_rss_bytes']=max(metrics['peak_rss_bytes'],rss())
    metrics['memory']={**memory_snapshot(),**spawn,'all_children_waited':no_children_left(),
                       'SIGCHLD_not_ignored':signal.getsignal(signal.SIGCHLD)!=signal.SIG_IGN}
    if source_pins()!=plan['source_pins']:metrics['errors'].append({'type':'SourceChangedAtEnd'})
    if hashlib.sha256(Path(plan['experiments']).read_bytes()).hexdigest()!=plan['experiments_sha256']:
        metrics['errors'].append({'type':'InputChangedAtEnd'})
    for (who,identity),before in parent_before.items():
        if store.get(users[who].id,identity)!=before:metrics['errors'].append({'type':'OriginalParentChanged'})
    for item in plan['evidence_inputs'].values():
        if hashlib.sha256(Path(item['path']).read_bytes()).hexdigest()!=item['sha256']:
            metrics['errors'].append({'type':'EvidenceChangedAtEnd'})
    result={'protocol':PROTOCOL,'mode':plan['mode'],'metrics':metrics,'evaluation':evaluate(metrics,plan['mode']),
            'source_pins':plan['source_pins'],'provider_requests':0,'new_paper_accounts':0,
            'production_writes':0,'promotion_allowed':False}
    dump(workspace/'result.json',result)
    print(json.dumps({'engineering_checks_passed':result['evaluation']['engineering_checks_passed'],
                      'full_soak_passed':result['evaluation']['full_soak_passed'],'errors':metrics['errors']}),flush=True)
    return 0 if (result['evaluation']['full_soak_passed'] if plan['mode']=='full' else result['evaluation']['engineering_checks_passed']) else 2


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--experiments',type=Path)
    parser.add_argument('--events',type=Path)
    parser.add_argument('--evidence-dir',type=Path)
    parser.add_argument('--calendar',type=Path)
    parser.add_argument('--calendar-dir',type=Path)
    parser.add_argument('--mode',choices=['smoke','full'],default='full')
    parser.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args();workspace=args.workspace
    if args.worker:
        plan=json.loads((workspace/'plan.json').read_text())
        assert os.getppid()==plan['parent_pid'] and hashlib.sha256(os.environ.pop('QUANT_EVENT_SOAK_NONCE').encode()).hexdigest()==plan['nonce_sha256']
        return asyncio.run(run_worker(workspace,plan))
    if not workspace.is_absolute() or workspace.exists() or not args.experiments or not args.experiments.is_file():
        parser.error('Existing experiments and NEW absolute isolated workspace required')
    if not all(x and x.exists() for x in (args.events,args.evidence_dir,args.calendar,args.calendar_dir)):
        parser.error('Retained cash and annual-calendar evidence required')
    if __import__('shutil').disk_usage(workspace.parent).free<768*1024**2:
        parser.error('At least 768 MiB free required for the fixed 512 MiB research DB safety budget')
    evidence_inputs={}
    for kind,manifest_path,folder in [('cash',args.events,args.evidence_dir),('calendar',args.calendar,args.calendar_dir)]:
        raw=manifest_path.read_bytes();manifest=json.loads(raw);folder=folder.resolve()
        evidence_inputs[kind+':manifest']={'path':str(manifest_path.resolve()),'sha256':hashlib.sha256(raw).hexdigest()}
        for sha,name in manifest['source_files'].items():
            path=(folder/name).resolve()
            if path.parent!=folder or not path.is_file() or path.stat().st_size>10*1024**2:
                parser.error('Evidence source missing, oversized or outside declared directory')
            body=path.read_bytes()
            if hashlib.sha256(body).hexdigest()!=sha:parser.error('Evidence digest differs')
            evidence_inputs[kind+':'+sha]={'path':str(path),'sha256':sha}
    workspace.mkdir(mode=0o700,parents=False);nonce=secrets.token_hex(24)
    plan={'protocol':PROTOCOL,'mode':args.mode,'registered_at':datetime.now(timezone.utc).isoformat(),
          'parent_pid':os.getpid(),'nonce_sha256':hashlib.sha256(nonce.encode()).hexdigest(),
          'source_pins':source_pins(),'experiments':str(args.experiments.resolve()),
          'experiments_sha256':hashlib.sha256(args.experiments.read_bytes()).hexdigest(),
          'runtime_versions':{n:importlib.metadata.version(n) for n in ('fastapi','uvicorn','httpx','sqlalchemy')},
          'events':str(args.events.resolve()),'calendar':str(args.calendar.resolve()),'evidence_inputs':evidence_inputs,
          'wall_budget_seconds':3840 if args.mode=='full' else 240}
    archive=workspace/'source-archive';archive.mkdir()
    for name in plan['source_pins']:
        path=Path(__file__) if name=='quant-event-service-soak.py' else ROOT/'backend/deepfocus_api'/name
        body=path.read_bytes()
        if hashlib.sha256(body).hexdigest()!=plan['source_pins'][name]:raise ValueError('Source changed while archiving')
        (archive/name).write_bytes(body)
    dump(workspace/'plan.json',plan)
    command=[sys.executable,str(Path(__file__).resolve()),'--workspace',str(workspace),'--worker']
    child=None
    def interrupt(signum,frame):raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM,interrupt)
    try:
        child=subprocess.Popen(command,env={**os.environ,'QUANT_EVENT_SOAK_NONCE':nonce},start_new_session=True)
        dump(workspace/'owned-parent.json',{'pid':os.getpid(),'worker_pid':child.pid,'workspace':str(workspace),
             'command':command,'script_sha256':plan['source_pins']['quant-event-service-soak.py']})
        code=child.wait(timeout=plan['wall_budget_seconds'])
        memory=parent_memory_receipt()
        full_passed=args.mode=='full' and code==0 and memory['accepted']
        dump(workspace/'parent-exit.json',{'worker_exit_code':code,'owned_worker_exited':True,
             'owned_tree_memory':memory,
             'full_soak_passed':full_passed,
             'finished_at':datetime.now(timezone.utc).isoformat(),
             'result_sha256':hashlib.sha256((workspace/'result.json').read_bytes()).hexdigest() if (workspace/'result.json').exists() else None})
        print(json.dumps({'owned_tree_memory_accepted':memory['accepted'],'full_soak_passed':full_passed}),flush=True)
        return code if memory['accepted'] else 2
    except BaseException as exc:
        if child is not None and child.poll() is None:
            child.terminate()
            try:child.wait(timeout=20)
            except subprocess.TimeoutExpired:child.kill();child.wait()
        dump(workspace/'PARENT_FAILED.json',{'type':type(exc).__name__,'owned_worker_exited':child is None or child.poll() is not None,
             'full_soak_passed':False,'promotion_allowed':False})
        return 2


if __name__=='__main__':raise SystemExit(main())
