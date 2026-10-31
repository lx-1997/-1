#!/usr/bin/env python3
"""Read-only independent event workload review. Never imports application/archive code.

Requires an externally retained plan hash. Smoke can never certify the hour.
Result equality is against retained direct-engine baselines, not proof of alpha.
"""
import argparse
from collections import Counter
from datetime import datetime,timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import socket
import sqlite3

EXPERIMENTS_SHA='25cb54bea8b6795443cbd4d1b0ae56cfab5bfe0921fc780c6a5fcf2da210566f'
PROTOCOL_SHA='97e8b8b579ed5d90a92dc5a47ac57fe1f184f2f3a5844a8174b240ac1157ef2e'


def check(value,message):
    if not value:raise ValueError(message)


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def read(path,limit=32*1024**2):
    check(path.is_file() and not path.is_symlink() and path.stat().st_size<=limit,'Missing or oversized input')
    return path.read_bytes()


def sha(path):return hashlib.sha256(read(path,512*1024**2)).hexdigest()


def parse(raw):
    def pairs(items):
        value={}
        for key,item in items:
            check(key not in value,'Duplicate JSON key');value[key]=item
        return value
    def invalid(value):raise ValueError('Nonfinite JSON')
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=invalid)


def percentile(values,fraction):
    check(isinstance(values,list) and 0<len(values)<=500000,'Missing latency observations')
    check(all(type(x) in (int,float) and math.isfinite(x) and x>=0 for x in values),'Invalid latency')
    return sorted(values)[math.ceil(len(values)*fraction)-1]


def memory_check(m,parent):
    own=m['api_lifetime_peak_bytes'];p=parent['control_parent_peak_bytes'];child=parent['largest_waited_descendant_peak_bytes']
    check(all(type(v) is int and v>0 for v in (own,p,child)),'Invalid memory peaks')
    check(m['largest_waited_child_peak_bytes']==0 and m['descendant_limit']==0,'Unexpected API descendants')
    check(m['CPU_spawn_attempts']==0 and m['forbidden_spawn_attempts']==0,'Unexpected spawn')
    check(m['all_children_waited'] is True and m['SIGCHLD_not_ignored'] is True,'Unwaited API children')
    check(m['tree_peak_upper_bound_bytes']==own and own<=1024**3,'API memory budget')
    check(parent['descendant_limit']==1 and parent['all_children_waited'] is True and parent['SIGCHLD_not_ignored'] is True,'Control-parent drain')
    check(parent['whole_tree_peak_upper_bound_bytes']==p+child and child>=own and p+child<=1024**3,'Whole-tree memory budget')
    check(parent['accepted'] is True,'Parent memory receipt refused')
    return p+child


def absent(pid):
    check(type(pid) is int and pid>1,'Invalid owned PID')
    try:os.kill(pid,0)
    except ProcessLookupError:return True
    except PermissionError:return False
    return False


def review(workspace,expected_plan_sha):
    check(sha(workspace/'plan.json')==expected_plan_sha,'Plan anchor mismatch')
    plan=parse(read(workspace/'plan.json'));protocol=plan['protocol']
    check(digest(protocol)==PROTOCOL_SHA,'Unregistered workload or thresholds')
    check(plan['mode'] in ('smoke','full'),'Unknown mode')
    check(plan['experiments_sha256']==EXPERIMENTS_SHA and sha(Path(plan['experiments']))==EXPERIMENTS_SHA,'Original experiments changed')
    originals=parse(read(Path(plan['experiments'])))
    check(len(originals)==2 and all(digest(x['result'])==x['result_digest'] for x in originals),'Invalid original snapshots')
    for name,value in plan['source_pins'].items():
        check('/' not in name and sha(workspace/'source-archive'/name)==value,'Frozen source changed')
    for item in plan['evidence_inputs'].values():check(sha(Path(item['path']))==item['sha256'],'Evidence changed')
    result=parse(read(workspace/'result.json'));exit=parse(read(workspace/'parent-exit.json'))
    parent=parse(read(workspace/'owned-parent.json'));worker=parse(read(workspace/'owned-worker.json'))
    check(result['protocol']==protocol and result['source_pins']==plan['source_pins'] and result['mode']==plan['mode'],'Result binding mismatch')
    check(exit['owned_worker_exited'] is True and exit['worker_exit_code']==0 and exit['result_sha256']==sha(workspace/'result.json'),'Owned process did not exit successfully')
    check(parent['pid']==plan['parent_pid'] and parent['worker_pid']==worker['pid'] and worker['parent_pid']==parent['pid'],'Owned PID chain mismatch')
    check(parent['script_sha256']==worker['script_sha256']==plan['source_pins']['quant-event-service-soak.py'],'Process source mismatch')
    check(absent(parent['pid']) and absent(worker['pid']),'Owned process still alive')
    sock=socket.socket();sock.settimeout(1)
    try:check(sock.connect_ex(('127.0.0.1',worker['port']))!=0,'Owned listener still open')
    finally:sock.close()
    m=result['metrics'];check(not m['errors'] and m['external_connection_attempts']==0,'Runtime errors/network attempts')
    check(all(result[k]==0 for k in ('provider_requests','new_paper_accounts','production_writes')) and result['promotion_allowed'] is False,'Scope exceeded')
    bound=memory_check(m['memory'],exit['owned_tree_memory'])
    lines=read(workspace/'observations.jsonl',64*1024**2).splitlines();observations=[parse(line) for line in lines]
    submits=[v for v in observations if v['kind']=='submission'];reads=[v for v in observations if v['kind']=='observer']
    occupancy=[v for v in observations if v['kind']=='occupancy'];rounds=[v for v in observations if v['kind']=='round']
    check(len(submits)==m['submitted']==4*m['rounds'] and len(rounds)==m['rounds'],'Submission count mismatch')
    check([v['index'] for v in rounds]==list(range(m['rounds'])),'Round sequence mismatch')
    check(Counter((v['round'],v['lane']) for v in submits)==Counter((i,j) for i in range(m['rounds']) for j in range(4)),'Missing or duplicate lane')
    check(all(v['status']==202 for v in submits),'Submission failed')
    check([v['elapsed_ms'] for v in submits]==m['create_ms'] and [v['elapsed_ms'] for v in reads]==m['observer_ms'],'Latency trace mismatch')
    check(all(v['items']==0 for v in reads),'Observer data leaked')
    check(occupancy and all(0<=v['global_slots']<=4 and 0<=v['owner_slots']<=2 and 0<=v['active_slots']<=v['global_slots'] for v in occupancy),'Worker caps')
    for raw,metric in [('global_slots','peak_global_slots'),('owner_slots','peak_owner_slots'),('active_slots','peak_active_slots')]:
        check(max(v[raw] for v in occupancy)==m[metric],'Peak occupancy mismatch')
    check(m['peak_active_slots']==4,'Four actual worker slots not observed')
    check(m['peak_qr_disk_bytes']<=512*1024**2 and m['peak_qr_disk_bytes']>=max(v['qr_disk_bytes'] for v in occupancy),'Disk budget')
    latency={'read_p95':percentile(m['observer_ms'],.95),'read_p99':percentile(m['observer_ms'],.99),
        'create_p95':percentile(m['create_ms'],.95),'loop_p99':percentile(m['loop_ms'],.99),'loop_max':max(m['loop_ms'])}
    for name,limit in [('read_p95',500),('read_p99',1500),('create_p95',2000),('loop_p99',250),('loop_max',2000)]:
        check(latency[name]<=limit,'Latency budget '+name)
    http=[v for v in observations if v['kind']=='http']
    check(sum(v['method']=='POST' and v['status']==429 for v in http)==m['rounds'],'Overflow not checked each round')
    check(sum(v['method']=='GET' and v['status']==404 for v in http)==m['rounds'],'Foreign-owner read missing')
    check(sum(v['method']=='POST' and v['status']==202 and v['route'].endswith('/event-replay') for v in http)==5*m['rounds'],'Idempotent retry missing')
    check(any(v['status']==401 for v in http),'Anonymous check missing')
    golden=parse(read(workspace/'golden-results.json'));core_names=('quant_event_replay.py','quant_event_policy.py','quant_dividend_book.py','quant_corporate_actions.py','quant_research_engine.py','ai_fund_ledger.py','ai_fund_policy.py','quant_calendar.py')
    core=digest({n:plan['source_pins'][n] for n in core_names})
    kernel=digest({'execution':core,**{n:plan['source_pins'][n] for n in ('quant_event_service.py','quant_calendar.py','quant_calendar_store.py','quant_exchange_schedule.py','quant_evidence_store.py','quant_research_store.py','quant_worker_slots.py')}})
    check(golden['kernel']==kernel,'Golden kernel binding mismatch')
    path=workspace/'research.sqlite3';before=sha(path);conn=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
    try:
        check(conn.execute('select count(*) from qr_worker_slot').fetchone()[0]==0 and conn.execute('select count(*) from qr_paper').fetchone()[0]==0,'Slots or paper accounts remain')
        rows=conn.execute('select id,owner,request_key,status,payload from qr_experiment').fetchall()
        seeds={identity:(owner,key,status,parse(payload)) for identity,owner,key,status,payload in rows if key.startswith('seed-')}
        check(len(seeds)==4,'Original parent count')
        for owner,key,status,payload in seeds.values():
            x=originals[int(key[-1])]
            check(status=='completed' and payload['request']==x['request'] and payload['dataset']==x['dataset'] and payload['result']==x['result'] and payload['result_digest']==x['result_digest'] and payload['quality']==x['quality'] and payload['kernel']==x['kernel'],'Original parent mutated')
        counts=Counter();owner_alias={};keys=set()
        for identity,owner,key,status,raw in rows:
            if identity in seeds:continue
            match=re.fullmatch(r'soak-([0-9]{4})-([0-3])',key);check(match,'Unexpected experiment')
            index,lane=map(int,match.groups());check(index<m['rounds'] and key not in keys,'Duplicate/outside round');keys.add(key)
            alias='a' if lane<2 else 'b';case=lane%2;owner_alias.setdefault(alias,owner);check(owner_alias[alias]==owner,'Lane owner changed')
            p=parse(raw);req=p['request'];seed=seeds[req['parent_id']]
            check(seed[0]==owner and seed[1]=='seed-'+str(case) and p['kernel']==kernel,'Wrong parent/owner/kernel')
            check(req['knowledge_mode']==protocol['lane_modes'][lane] and req['kind']=='event_replay','Wrong workload')
            if status=='completed':
                check(digest(p['result'])==p['result_digest']==golden['digests'][alias][case],'Result differs from direct baseline')
                check(p['result']['gate']['passed'] is False and p['result']['holdout_replayed'] is False,'Unexpected promotion')
            else:
                check(status=='cancelled' and (index*4+lane+1)%5==0 and 'result' not in p,'Unplanned cancellation or partial result')
            counts[status]+=1
        check(len(owner_alias)==2 and len(set(owner_alias.values()))==2,'Owners not distinct')
        check(len(keys)==m['submitted'] and counts['completed']==m['completed']==m['exact_results'] and counts['cancelled']==m['cancelled'],'DB counts differ')
        check(m['remaining_slots']==0 and m['remaining_admissions']==0 and m['paper_accounts']==0,'Undrained report')
    finally:conn.close()
    check(sha(path)==before,'Read-only review changed main database')
    check(result['evaluation']['engineering_checks_passed'] is True and result['evaluation']['latency_ms']==latency,'Runtime evaluation differs')
    full=plan['mode']=='full'
    if full:
        check(m['elapsed_seconds']>=3600 and m['rounds']>=30 and m['completed']>=32 and m['cancelled']>=10 and len(reads)>=600 and len(m['loop_ms'])>=600,'Incomplete fixed-hour workload')
    check(result['evaluation']['full_soak_passed'] is full and exit['full_soak_passed'] is full,'Smoke/full status mismatch')
    registered=datetime.fromisoformat(plan['registered_at']);finished=datetime.fromisoformat(exit['finished_at'])
    check(registered<=finished<=datetime.now(timezone.utc),'Invalid recorded clocks')
    return dict(schema='independent-event-soak-review-v1',plan_sha256=expected_plan_sha,mode=plan['mode'],
        engineering_checks_passed=True,full_soak_passed=full,rounds=m['rounds'],completed=m['completed'],cancelled=m['cancelled'],
        exact_results=m['exact_results'],observer_reads=len(reads),latency_ms=latency,whole_tree_memory_upper_bound_bytes=bound,
        qr_peak_disk_bytes=m['peak_qr_disk_bytes'],original_parents_unchanged=True,owned_processes_absent=True,
        source_archive_verified=True,database_main_unchanged=True,promotion_allowed=False,production_SLO_certified=False)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--workspace',type=Path,required=True)
    p.add_argument('--expected-plan-sha',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    check(not a.output.exists(),'New output required');result=review(a.workspace,a.expected_plan_sha)
    result['verifier_sha256']=sha(Path(__file__))
    with a.output.open('x') as f:json.dump(result,f,ensure_ascii=False,indent=2,allow_nan=False)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
