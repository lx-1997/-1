#!/usr/bin/env python3
"""Capture the frozen research-date universe, one immutable gzip response/day."""
import argparse,copy,gzip,hashlib,importlib.metadata,json,os,signal,socket,subprocess,sys,time
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from deepfocus_api import quant_historical_universe as universe
from deepfocus_api.quant_research_engine import digest

LOADED_SHA=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class QueryDeadline(BaseException):
    """Escape the inspected SDK's broad Exception handler on a stuck receive."""


def bounded_call(fn):
    def expired(*_):raise QueryDeadline('Owned query exceeded 45 seconds')
    previous=signal.signal(signal.SIGALRM,expired);signal.setitimer(signal.ITIMER_REAL,45)
    try:return fn()
    finally:signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,previous)


def seed_completed(oldroot,work,scope):
    """Reuse verified data bytes, not old code or the failed query itself."""
    raw=(oldroot/'manifest.json').read_bytes();old=json.loads(raw)
    if old.get('schema')!=universe.VERSION or old.get('scope')!=scope:raise ValueError('Seed scope differs')
    entries=old['results']
    if not entries or entries[0]['query']!={'method':'query_stock_basic','code':''}:raise ValueError('Seed catalog missing')
    before=universe.verify_entry(oldroot,entries[0]);days={};origins=[]
    for i,entry in enumerate(entries[1:]):
        q=entry['query']
        if q['method']!='query_all_stock':continue
        if q['day'] not in scope['days'] or q['day'] in days:raise ValueError('Conflicting seed day')
        universe.verify_entry(oldroot,entry);copied=copy.deepcopy(entry)
        for original,frame in zip(entry['frames'],copied['frames']):
            name='seed-'+str(i)+'-'+original['file'];packed=(oldroot/original['file']).read_bytes()
            with (work/name).open('xb') as f:f.write(packed)
            frame['file']=name
        copied['origin']='verified_prior_capture';days[q['day']]=copied
        origins.append({'day':q['day'],'original_entry_digest':digest(entry),'copied_entry_digest':digest(copied)})
    with (work/'seed-origin-manifest.json').open('xb') as f:f.write(raw)
    return {'catalogue_digest':digest(before),'daily_entries':days,'origin_manifest_sha256':hashlib.sha256(raw).hexdigest(),
            'origin_collector_sha256':old['collector_sha256'],'origin_entries':origins,'prior_errors_retained':old.get('errors',[])}


def worker(plan,work):
    if (plan['collector_sha256']!=LOADED_SHA or plan['source_pins']!=universe.SOURCE_PINS
            or hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=LOADED_SHA):
        raise ValueError('Parent/worker source mismatch')
    universe.require_current_source()
    if importlib.metadata.version('baostock')!='0.9.1':raise ValueError('Only inspected SDK 0.9.1 supported')
    import baostock as bs
    from baostock.util import socketutil
    package=Path(bs.__file__).resolve().parent
    sdk={str(p.relative_to(package)):hashlib.sha256(p.read_bytes()).hexdigest() for p in package.rglob('*.py')}
    socket.setdefaulttimeout(15);results=[];errors=[];failed_attempts=[];current=None;frames=[];actual=socketutil.send_msg
    def retain(message):
        response=actual(message)
        if response is not None and current is not None:
            raw=response.encode('utf-8')
            if len(raw)>4*1024*1024:raise ValueError('Universe response exceeds 4MiB')
            name=current['name']+'-'+str(len(frames))+'.response.gz';packed=gzip.compress(raw,mtime=0)
            with (work/name).open('xb') as f:f.write(packed)
            frames.append({'file':name,'sha256':hashlib.sha256(raw).hexdigest(),'gzip_sha256':hashlib.sha256(packed).hexdigest(),
                'bytes':len(raw),'observed_at':datetime.now(timezone.utc).isoformat(),'query':current['query'],
                'capture_layer':'SDK_UTF8_metadata_response_not_packet_capture'})
        return response
    socketutil.send_msg=retain
    try:
        if bounded_call(bs.login).error_code!='0':raise ValueError('Anonymous login failed')
        queries=[('basic-before',{'method':'query_stock_basic','code':''})]+[(d,{'method':'query_all_stock','day':d}) for d in plan['scope']['days']]+[('basic-after',{'method':'query_stock_basic','code':''})]
        for i,(name,q) in enumerate(queries):
            universe.require_current_source()
            if hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=LOADED_SHA:raise ValueError('Collector changed during capture')
            seed=plan.get('seed',{});retained=seed.get('daily_entries',{}).get(q.get('day'))
            if retained:
                universe.verify_entry(work,retained);entry=retained
            else:
                for attempt in (1,2):
                    current={'name':name+'-attempt-'+str(attempt),'query':q};frames=[]
                    def fetch():
                        rs=bs.query_all_stock(day=q['day']) if q['method']=='query_all_stock' else bs.query_stock_basic(code='')
                        rows=[]
                        while rs.error_code=='0' and rs.next():
                            rows.append(rs.get_row_data())
                            if len(rows)>=10000:raise ValueError('Unsupported metadata pagination')
                        return rs,rows
                    deadline=False
                    try:rs,rows=bounded_call(fetch)
                    except QueryDeadline:deadline=True;rs=None;rows=[]
                    network_failure=not frames and (deadline or rs.error_code!='0')
                    if network_failure:
                        failed_attempts.append({'query':q,'attempt':attempt,'stage':'network','deadline':deadline,
                            'sdk_error_code':None if rs is None else rs.error_code,'observed_at':datetime.now(timezone.utc).isoformat()})
                        with (work/'failed-attempts.jsonl').open('a') as f:f.write(json.dumps(failed_attempts[-1])+'\n')
                        if attempt==2:raise ValueError('Same query failed after one bounded reconnect retry')
                        from baostock.common import context
                        oldsocket=getattr(context,'default_socket',None)
                        if oldsocket is not None:oldsocket.close()
                        current=None;time.sleep(1)
                        if bounded_call(bs.login).error_code!='0':raise ValueError('Reconnect failed')
                        continue
                    if deadline or rs.error_code!='0' or len(frames)!=1:raise ValueError('Failed or unsupported universe query; not a retryable network failure')
                    decoded=universe.decode_response(universe.read_frame(work,frames[0]),q)
                    if rs.fields!=decoded['fields'] or rows!=decoded['sdk_rows']:raise ValueError('Unexpected SDK metadata parsing')
                    entry={'query':q,'fields':rs.fields,'rows':len(rows),'raw_rows_digest':digest(decoded['rows']),
                           'sdk_rows_digest':digest(rows),'sdk_whitespace_changed_rows':decoded['sdk_whitespace_changed_rows'],
                           'error_code':rs.error_code,'frames':list(frames),'successful_attempt':attempt}
                    break
                if name=='basic-before' and seed and entry['raw_rows_digest']!=seed['catalogue_digest']:
                    raise ValueError('Current lifecycle catalog differs from retained seed')
            results.append(entry)
            with (work/'completed-queries.jsonl').open('a') as f:f.write(json.dumps(entry,ensure_ascii=False)+'\n')
            if i%5==0 or i==len(queries)-1:print(json.dumps({'completed_queries':i+1,'total_queries':len(queries),'last':name,'rows':entry['rows'],'reused':bool(retained)}),flush=True)
            if not retained:time.sleep(.5)
        current=None;bounded_call(bs.logout)
    except QueryDeadline:errors.append({'stage':'network','type':'QueryDeadline'})
    except Exception as exc:errors.append({'stage':'capture','type':type(exc).__name__,'message':str(exc)[:160]})
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=LOADED_SHA or universe._pins()!=plan['source_pins']:
        errors.append({'stage':'source_changed','type':'CollectorOrReaderChanged'})
    if sdk!={str(p.relative_to(package)):hashlib.sha256(p.read_bytes()).hexdigest() for p in package.rglob('*.py')}:
        errors.append({'stage':'source_changed','type':'SDKChanged'})
    out={'schema':universe.VERSION,'scope':plan['scope'],'plan_digest':digest(plan),'collector_sha256':LOADED_SHA,
         'source_pins':plan['source_pins'],'sdk_version':'0.9.1','sdk_source_pins':sdk,
         'created_at':datetime.now(timezone.utc).isoformat(),'results':results,'errors':errors,'failed_network_attempts':failed_attempts,
         'historical_pit_universe_certified':False,'preopen_knowledge_verified':False,'data_rights_verified':False}
    with (work/'manifest.json').open('x') as f:json.dump(out,f,ensure_ascii=False,indent=2)
    return 1 if errors else 0


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--workspace',type=Path,required=True);p.add_argument('--worker-plan',type=Path,help=argparse.SUPPRESS)
    p.add_argument('--seed-capture',type=Path,help='Read-only verified successful days from an earlier failed capture')
    for n in ('experiments','calendar','calendar-dir'):p.add_argument('--'+n,type=Path)
    a=p.parse_args()
    if a.worker_plan:return worker(json.loads(a.worker_plan.read_text()),a.workspace)
    if not a.workspace.is_absolute() or a.workspace.exists():p.error('Use a new absolute capture workspace')
    if not all((a.experiments,a.calendar,a.calendar_dir)):p.error('Explicit sealed experiments and reviewed calendar required')
    exps=json.loads(a.experiments.read_text());calendar=json.loads(a.calendar.read_text());calroot=a.calendar_dir.resolve();sources={}
    for sha,name in calendar['source_files'].items():
        file=(calroot/name).resolve()
        if file.parent!=calroot or not file.is_file() or file.stat().st_size>2*1024*1024:raise ValueError('Missing calendar source')
        sources[sha]=file.read_bytes()
    scope=universe.collection_scope(exps,calendar,sources);universe.require_current_source()
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=LOADED_SHA:raise ValueError('Collector source changed')
    parent=a.workspace.parent;free=os.statvfs(parent).f_bavail*os.statvfs(parent).f_frsize
    if free<512*1024*1024:raise ValueError('Need 512MiB free capture headroom')
    plan={'registered_at':datetime.now(timezone.utc).isoformat(),'scope':scope,'source_pins':dict(universe.SOURCE_PINS),
          'collector_sha256':LOADED_SHA,'minimum_query_spacing_seconds':.5,'worker_timeout_seconds':14400,
          'per_response_max_bytes':4*1024*1024,'per_query_max_rows_exclusive':10000,
          'maximum_attempts_per_network_query':2,'retry_on_bad_data':False,'query_deadline_seconds':45}
    a.workspace.mkdir(mode=0o700)
    if a.seed_capture:plan['seed']=seed_completed(a.seed_capture.resolve(),a.workspace,scope)
    with (a.workspace/'capture-plan.json').open('x') as f:json.dump(plan,f,ensure_ascii=False,indent=2)
    child=None
    def stopped(*_):raise KeyboardInterrupt('Owned capture cancelled')
    previous=signal.signal(signal.SIGTERM,stopped)
    try:
        child=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--workspace',str(a.workspace),
                                '--worker-plan',str(a.workspace/'capture-plan.json')],start_new_session=True)
        with (a.workspace/'owned-process.json').open('x') as f:json.dump({'parent_pid':os.getpid(),'worker_pid':child.pid,'collector_sha256':LOADED_SHA,'workspace':str(a.workspace)},f)
        return child.wait(timeout=14400)
    except subprocess.TimeoutExpired:
        with (a.workspace/'FAILED.json').open('x') as f:json.dump({'error':'OwnedUniverseCaptureTimeout','retry_automatically':False},f)
        return 1
    except KeyboardInterrupt:
        with (a.workspace/'CANCELLED.json').open('x') as f:json.dump({'error':'OwnedCaptureCancelled','retry_automatically':False},f)
        return 130
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            try:child.wait(timeout=5)
            except subprocess.TimeoutExpired:child.kill();child.wait()
        signal.signal(signal.SIGTERM,previous)


if __name__=='__main__':raise SystemExit(main())
