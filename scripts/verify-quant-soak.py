#!/usr/bin/env python3
"""Independent read-only review of the frozen v1 one-hour QR soak.

Never imports archived/application code, replays prices, refreshes a token or
changes the source DB. Protocol success is distinct from whole-process memory
acceptance: v1 retained only API-process RSS, so that latter claim stays false.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import socket
import sqlite3

VERSION='independent-qr-soak-review-v1'
LOADED_VERIFIER_SHA=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
PROTOCOL_SHA='17287f44f353849f3f940e3c21031c0fccb73c0c6c822a9d70d482c5861b12c2'
EXPERIMENTS_SHA='25cb54bea8b6795443cbd4d1b0ae56cfab5bfe0921fc780c6a5fcf2da210566f'
CPU_FILES=('quant_research_engine.py','quant_research_candidates.py','quant_research.py',
 'quant_calendar.py','quant_paper_calendar.py','quant_calendar_service.py','quant_calendar_store.py',
 'quant_exchange_schedule.py','quant_research_store.py','quant_worker_slots.py','backtest_data.py',
 'ai_fund_ledger.py','ai_fund_policy.py','quant_cpu_process.py','quant_cpu_worker.py')
SOURCE_FILES=set(CPU_FILES)|{'auth.py','storage.py','db.py','quant_research_api.py','quant-service-soak.py'}


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def check(condition,message):
    if not condition:raise ValueError(message)


def parse(raw):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('Duplicate JSON key')
            result[key]=value
        return result
    def constant(value):raise ValueError('Non-finite JSON')
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=constant)


def read(path,limit=32*1024**2):
    check(path.is_file() and path.stat().st_size<=limit,'Missing/oversized retained input')
    return path.read_bytes()


def sha(path):
    value=hashlib.sha256()
    with path.open('rb') as source:
        for part in iter(lambda:source.read(1024**2),b''):value.update(part)
    return value.hexdigest()


def numbers(values):
    check(isinstance(values,list) and 0<len(values)<=500000,'Missing/bounded latency observations required')
    check(all(type(x) in (int,float) and math.isfinite(x) and x>=0 for x in values),'Invalid latency sample')
    return sorted(values)


def quantile(ordered,fraction):
    return ordered[math.ceil(len(ordered)*fraction)-1]


def subsequence(needle,haystack):
    cursor=iter(haystack)
    return all(any(value==item for item in cursor) for value in needle)


def absent(pid):
    check(type(pid) is int and pid>1,'Invalid owned-process marker')
    try:os.kill(pid,0)
    except ProcessLookupError:return True
    except (PermissionError,OSError):return False
    return False


def port_closed(port):
    check(type(port) is int and 0<port<65536,'Invalid loopback port marker')
    probe=socket.socket();probe.settimeout(.3)
    try:return probe.connect_ex(('127.0.0.1',port))!=0
    finally:probe.close()


def verify(workspace,source_archive,experiments,expected_plan_sha):
    check(sha(Path(__file__))==LOADED_VERIFIER_SHA,'Loaded verifier source changed')
    workspace=Path(workspace);source_archive=Path(source_archive);experiments=Path(experiments)
    check(all(p.is_absolute() and p.exists() for p in [workspace,source_archive,experiments]),'Explicit retained absolute paths required')
    check(not (workspace/'PARENT_FAILED.json').exists(),'Run interrupted or parent failed')
    names=['plan.json','result.json','parent-exit.json','owned-parent.json','owned-worker.json','observations.jsonl']
    raw={name:read(workspace/name) for name in names}
    before={name:hashlib.sha256(value).hexdigest() for name,value in raw.items()}
    check(before['plan.json']==expected_plan_sha,'Plan differs from registered checkpoint')
    plan,result,parent,owned,worker=[parse(raw[n]) for n in names[:5]]
    check(plan['mode']==result['mode']=='full','Smoke cannot certify a full run')
    check(digest(plan['protocol'])==PROTOCOL_SHA and result['protocol']==plan['protocol'],'Protocol changed')
    pins=plan['source_pins'];check(set(pins)==SOURCE_FILES and result['source_pins']==pins,'Source inventory changed')
    for name,value in pins.items():
        path=(source_archive/name).resolve()
        check(path.parent==source_archive.resolve() and sha(path)==value,'Archived source bytes changed')
    check(parent['worker_exit_code']==0 and parent['owned_worker_exited'] is True,'Worker did not exit successfully')
    check(parent['result_sha256']==before['result.json'],'Parent/result receipt mismatch')
    check(owned['pid']==plan['parent_pid']==worker['parent_pid'] and owned['worker_pid']==worker['pid'],'Process markers disagree')
    check(owned['workspace']==worker['workspace']==str(workspace),'Different process workspace')
    check(owned['script_sha256']==worker['script_sha256']==pins['quant-service-soak.py'],'Runner identity changed')
    check(absent(owned['pid']) and absent(worker['pid']),'Owned PID alive/reused/unknown; do not kill it')
    check(port_closed(worker['port']),'Owned listener still open')
    original_raw=read(experiments)
    check(hashlib.sha256(original_raw).hexdigest()==plan['experiments_sha256']==EXPERIMENTS_SHA,'Frozen originals changed')
    originals=parse(original_raw);check(len(originals)==2,'Two fixed originals required')
    for item in originals:
        check(digest(item['result'])==item['result_digest'] and digest(item['dataset'])==item['result']['dataset_digest'],'Original financial payload changed')
    metrics=result['metrics'];elapsed=metrics['elapsed_seconds']
    check(type(elapsed) in (int,float) and math.isfinite(elapsed) and elapsed>=0,'Invalid duration')
    wall=(datetime.fromisoformat(parent['finished_at'])-datetime.fromisoformat(plan['registered_at'])).total_seconds()
    check(wall>=elapsed and wall<=plan['wall_budget_seconds']+20,'Duration/parent clock chain differs')
    read_values=numbers(metrics['observer_ms']);create_values=numbers(metrics['create_ms']);loop_values=numbers(metrics['loop_ms'])
    latency={'read_p95':quantile(read_values,.95),'read_p99':quantile(read_values,.99),
             'create_p95':quantile(create_values,.95),'loop_p99':quantile(loop_values,.99),'loop_max':loop_values[-1]}
    check(latency==result['evaluation']['latency_ms'],'Retained sample quantiles differ')
    observations=[parse(line) for line in raw['observations.jsonl'].splitlines()]
    rounds=[x for x in observations if x['kind']=='round']
    check([x['index'] for x in rounds]==list(range(metrics['rounds'])),'Round log missing/reordered')
    new_create=[];part=[];round_totals=[];status_counts=Counter();get200=[]
    for observation in observations:
        if observation['kind']=='http':
            ms=observation['elapsed_ms'];check(type(ms) in (int,float) and math.isfinite(ms) and ms>=0,'Invalid HTTP observation')
            status_counts[(observation['method'],observation['status'])]+=1
            if observation['method']=='GET' and observation['status']==200:get200.append(ms)
            if observation['method']=='POST':part.append(observation)
        elif observation['kind']=='round':
            check([x['status'] for x in part]==[202,202,202,202,429,202],'Burst quota/idempotence request log changed')
            new_create.extend(x['elapsed_ms'] for x in part[:4]);part=[]
            round_totals.append((observation['completed'],observation['cancelled']))
            check(0<=observation['elapsed_seconds']<=elapsed,'Round outside measurement interval')
            check(observation['rss_bytes']<=metrics['peak_rss_bytes'] and observation['qr_disk_bytes']<=metrics['peak_qr_disk_bytes'],'Reported resource peak is below retained sample')
        else:raise ValueError('Unknown observation record')
    check(not part and new_create==metrics['create_ms'],'Create timings are not the actual four admitted requests')
    check(subsequence(metrics['observer_ms'],get200),'Observer timings not present in retained HTTP observations')
    check(status_counts[('GET',401)]==1 and status_counts[('GET',404)]==metrics['rounds'],'Authentication/foreign-owner checks incomplete')
    check(not any(status not in ({200,401,404} if method=='GET' else {202,429} if method=='POST' else {200}) for method,status in status_counts),'Unexpected HTTP status')
    db=workspace/'research.sqlite3'
    check(db.is_file() and db.stat().st_size<=512*1024**2,'Research DB byte budget exceeded')
    wal=Path(str(db)+'-wal');check(not wal.exists() or wal.stat().st_size==0,'Committed WAL requires a separately frozen cold snapshot')
    database_sha=sha(db);connection=sqlite3.connect(db.resolve().as_uri()+'?mode=ro&immutable=1',uri=True,timeout=2)
    connection.row_factory=sqlite3.Row;connection.execute('PRAGMA query_only=ON')
    completed=cancelled=missed=0;owners={};seen=set();by_round={};kernel=digest({name:pins[name] for name in CPU_FILES})
    try:
        check([x[0] for x in connection.execute('PRAGMA integrity_check')]==['ok'],'SQLite integrity rejected')
        check(connection.execute('SELECT count(*) FROM qr_worker_slot').fetchone()[0]==0,'Actual worker slots remain')
        check(connection.execute('SELECT count(*) FROM qr_paper').fetchone()[0]==0,'Unexpected paper account')
        for row in connection.execute('SELECT * FROM qr_experiment ORDER BY request_key'):
            match=re.fullmatch(r'soak-(\d{4})-([0-3])',row['request_key']);check(match is not None,'Unexpected/overflow experiment was committed')
            index,lane=map(int,match.groups());check(index<metrics['rounds'] and (index,lane) not in seen,'Invalid duplicated lane')
            seen.add((index,lane));bucket=lane//2
            if bucket in owners:check(owners[bucket]==row['owner'],'Lane owner changed')
            else:owners[bucket]=row['owner']
            item=parse(row['payload']);original=originals[lane%2]
            check(item['request']==original['request'] and row['input_hash']==digest(item['request']),'Stored request differs from frozen plan')
            check(item['kernel']==kernel,'Kernel changed')
            # Cancellation may win before fetch/persist; completed computations
            # must always retain data. Any data that did persist must match.
            if row['status']=='completed' or 'dataset' in item:
                check(item.get('dataset')==original['dataset'],'Retained dataset changed/missing')
            should_cancel=(index*4+lane+1)%5==0
            current=by_round.setdefault(index,[0,0])
            if row['status']=='completed':
                check(item['result_digest']==original['result_digest']==digest(item['result']),'Completed financial result changed')
                completed+=1;current[0]+=1;missed+=int(should_cancel)
            elif row['status']=='cancelled':
                check(should_cancel and 'result' not in item,'Unrequested cancel or cancelled result published')
                cancelled+=1;current[1]+=1
            else:raise ValueError('Unfinished/failed experiment in successful run')
        check(len(owners)==2 and len(set(owners.values()))==2,'Two research owners not independently scoped')
    finally:connection.close()
    check(len(seen)==metrics['submitted']==metrics['rounds']*4,'Submitted lane/record counts differ')
    check(len(metrics['create_ms'])==metrics['submitted'],'Missing submission durations')
    check((completed,cancelled,completed,missed)==(metrics['completed'],metrics['cancelled'],metrics['exact_results'],metrics['cancel_missed']),'Database result counts differ')
    requested=sum((index*4+lane+1)%5==0 for index,lane in seen)
    check(metrics['cancel_requested']==requested==cancelled+missed==status_counts[('DELETE',200)],'Cancellation count differs')
    cumulative=[0,0]
    for index,expected in enumerate(round_totals):
        cumulative=[a+b for a,b in zip(cumulative,by_round[index])]
        check(tuple(cumulative)==expected,'Cumulative round outcome differs from DB')
    checks={
        'read_p95':latency['read_p95']<=500,'read_p99':latency['read_p99']<=1500,
        'create_p95':latency['create_p95']<=2000,'loop_p99':latency['loop_p99']<=250,'loop_max':latency['loop_max']<=2000,
        'rss':metrics['peak_rss_bytes']<=1024**3,'disk':metrics['peak_qr_disk_bytes']<=512*1024**2,
        'worker_caps':metrics['peak_global_slots']<=4 and metrics['peak_owner_slots']<=2,
        'four_active_slots_observed':metrics['peak_active_slots']==4,'no_unexpected_errors':not metrics['errors'],
        'no_external_connections':metrics['external_connection_attempts']==0,
        'all_results_exact':completed>0 and completed==metrics['exact_results'],
        'all_drained':metrics['remaining_slots']==0 and metrics['remaining_admissions']==0,
        'no_paper_accounts':metrics['paper_accounts']==0,
    }
    full={'mode':True,'duration':elapsed>=3600,'completed':completed>=32,'cancelled':cancelled>=10,
          'rounds':metrics['rounds']>=30,'reads':len(read_values)>=600,'heartbeats':len(loop_values)>=600}
    evaluation=result['evaluation'];registered=all(checks.values()) and all(full.values())
    check(checks==evaluation['safety_checks'] and full==evaluation['full_run_checks'],'Producer gate detail differs from independent calculation')
    check(evaluation['engineering_checks_passed']==all(checks.values()) and evaluation['full_soak_passed']==registered,'Producer summary differs')
    check(result['production_writes']==result['provider_requests']==result['new_paper_accounts']==0,'Unexpected external/financial effects')
    check(result['promotion_allowed'] is False and evaluation['promotion_allowed'] is False and evaluation['production_SLO_certified'] is False,'Scope was upgraded')
    check(sha(db)==database_sha and all(sha(workspace/name)==value for name,value in before.items()),'Retained evidence changed during read-only review')
    check(sha(Path(__file__))==LOADED_VERIFIER_SHA,'Verifier changed during review')
    return {'version':VERSION,'registered_protocol_passed':registered,'retained_gate_calculation_equal':True,
        'internal_capacity_accepted':False,'memory_scope_complete':False,
        'acceptance_gap':'v1 retained API RUSAGE_SELF only; CPU-child RSS not covered by the aggregate budget',
        'elapsed_seconds':elapsed,'rounds':metrics['rounds'],'completed':completed,'cancelled':cancelled,
        'all_completed_results_match_both_originals':True,'quantiles_recomputed':latency,
        'observed_HTTP_status_counts':{str(k):v for k,v in status_counts.items()},
        'database_sha256':database_sha,'input_sha256':before,'plan_sha256':expected_plan_sha,
        'source_archive_sha_verified':True,'source_SQL_mutations':False,'source_bytes_unchanged':True,
        'owned_parent_and_worker_PIDs_absent':True,'owned_port_closed':True,'actual_slots_and_papers_zero':True,
        'externally_attested_time':False,'all_historical_child_PIDs_independently_audited':False,
        'observer_actor_not_separately_logged':True,'resource_peak_secondary_log_is_sampled_not_complete':True,
        'promotion_allowed':False,'production_SLO_certified':False,'commercial_ready':False,
        'verifier_sha256':LOADED_VERIFIER_SHA,'reviewed_at':datetime.now(timezone.utc).isoformat()}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['workspace','source-archive','experiments','output']:parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--plan-sha256',required=True);args=parser.parse_args()
    if not args.output.is_absolute() or args.output.exists():parser.error('New absolute output file required')
    try:report=verify(args.workspace,args.source_archive,args.experiments,args.plan_sha256)
    except (ValueError,TypeError,KeyError,OSError,sqlite3.DatabaseError) as exc:
        report={'verified':False,'error_type':type(exc).__name__,'error':str(exc),'internal_capacity_accepted':False,'promotion_allowed':False}
    with args.output.open('x') as out:json.dump(report,out,ensure_ascii=False,indent=2,allow_nan=False)
    print(json.dumps({'registered_protocol_passed':report.get('registered_protocol_passed',False),
                      'internal_capacity_accepted':False,'acceptance_gap':report.get('acceptance_gap',report.get('error_type'))}))
    return 2


if __name__=='__main__':raise SystemExit(main())
