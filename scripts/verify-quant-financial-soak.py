#!/usr/bin/env python3
"""Independent, read-only financial workload review. Never imports archive code."""
import argparse,hashlib,json,math,os,socket,sqlite3
from collections import Counter
from datetime import date,datetime,time,timedelta,timezone
from fractions import Fraction
from pathlib import Path
from urllib.parse import urlencode
PROTOCOL_SHA='ce7c7c5fbda6446a1a3243b530d93ae4d62e39ecc82a635382d1781591e7e2d5'
MANIFEST_SHA='82a17fb882260f2d55cab3b4e8699f67250e90f5d526173e2a624c1b813e650e'
TYPED_SHA='6f115b6fe4a06048e90adc0c7c0156e3a896471471fe28be908ce088b58eca70'
BJ=timezone(timedelta(hours=8))


def check(v,msg):
 if not v:raise ValueError(msg)


def raw(p,limit=32*1024**2):
 check(p.is_file() and not p.is_symlink() and p.stat().st_size<=limit,'Missing/oversized evidence');return p.read_bytes()


def parse(body):
 def pairs(xs):
  v={}
  for k,x in xs:check(k not in v,'Duplicate JSON key');v[k]=x
  return v
 def invalid(v):raise ValueError('Nonfinite JSON')
 return json.loads(body,object_pairs_hook=pairs,parse_constant=invalid)


def load(p):return parse(raw(p))


def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def sha(p,limit=512*1024**2):return hashlib.sha256(raw(p,limit)).hexdigest()


def boundary(s):return datetime.combine(datetime.fromisoformat(s).astimezone(BJ).date()+timedelta(days=1),time(),BJ)


def pct(v,q):
 check(isinstance(v,list) and 0<len(v)<=500000 and all(type(x) in (int,float) and math.isfinite(x) and x>=0 for x in v),'Invalid latency observations');return sorted(v)[math.ceil(len(v)*q)-1]


def url(g):
 args=dict(g['request']);ep=args.pop('asset_endpoints')
 if ep:
  for side,k in [('begin','beginning'),('end','ending')]:args[side+'_report_period_end']=ep[k]['report_period_end'];args[side+'_column']=ep[k]['column']
 return '/api/quant-research/financial-extractions/'+g['id']+'/derive?'+urlencode(args)


def validate_http_matrix(golden,observations,metrics,rounds):
 """Bind every completed HTTP request to its registered owner, route and role."""
 expected=Counter()
 def add(category,who,route,status,count):expected[(category,who,route,status)]+=count
 listing='/api/quant-research/financial-extractions'
 for lane,g in enumerate(golden[:4]):
  cancelled=sum((i*4+lane+1)%5==0 for i in range(rounds))
  add('lane',g['who'],url(g),200,rounds-cancelled)
 for g in golden[4:]:add('extra',g['who'],url(g),200,rounds)
 for who,status in [('a',429),('observer',200),('observer',429),('b',429)]:add('probe',who,listing,status,rounds)
 cycles=metrics['observer_cycles']
 check(type(cycles) is int and cycles>=0,'Invalid observer cycles')
 for route,status in [(listing,200),(listing+'/'+golden[0]['id'],404),(url(golden[0]),404)]:add('observer','observer',route,status,cycles)
 actual=Counter((e['category'],e['who'],e['route'],e['status']) for e in observations if e['kind']=='http')
 check(actual==expected,'Registered HTTP request matrix differs')


def validate_golden(g,packets,parents):
 r=g['result'];request=g['request'];row=packets[g['id']];p=row['parsed'];parent=parents[row['parent_id']]['parsed'];clock=datetime.fromisoformat(request['as_of']);end=date.fromisoformat(request['period_end'])
 check(r['request']==request and r['receipt']['id']==g['id'] and digest(r)==g['digest'],'Golden request or output identity')
 check(all(r.get(k) is False for k in ['ratios_or_scores_generated','accounting_comparability_verified','historical_pit_feed_certified','usable_for_strategy','promotion_allowed','commercial_ready']),'Unsupported qualification')
 check('source_pins' not in r,'Source code mapping exposed')
 blocked=request['mode']=='as_recorded' and clock<max(boundary(row['created']),boundary(parent['recorded_at']))
 if blocked:
  check(r['result'] is None and r['ttm_value'] is None and r['endpoint_average_assets'] is None and r['status']=='unavailable','Stored clock leak');return
 start=date(end.year,1,1) if end.month==12 else date(end.year-1,end.month,end.day)+timedelta(days=1)
 check(r['period_start']==start.isoformat(),'Wrong flow window')
 specs=[('current_annual',str(end),1)] if end.month==12 else [('current_ytd',str(end),1),('previous_annual',f'{end.year-1}-12-31',1),('previous_comparable_ytd',f'{end.year-1}'+str(end)[4:],-1)]
 def pick(report,metric,col):
  docs=[d for d in parent['effective_manifest']['vintages'] if d['issuer_id']==request['issuer_id'] and d['symbol']==request['symbol'] and d['period_end']==report
   and datetime.combine(date.fromisoformat(d['published_date'])+timedelta(days=1),time(),BJ)<=clock
   and (request['mode']=='historical_reconstruction' or max(datetime.fromisoformat(d['observed_at']),datetime.fromisoformat(d['recorded_at']))<=clock)]
  doc=max(docs,key=lambda d:d['revision']) if docs else None
  fact=next((f for f in p['input']['facts'] if doc and (f['document_id'],f['metric'],f['column'])==(doc['document_id'],metric,col)),None)
  return doc,fact
 nodes=r['result']['flow_components'];check(len(nodes)==len(specs),'Flow component count');missing=[];values=[];points=[]
 for node,(role,report,coef) in zip(nodes,specs):
  doc,fact=pick(report,request['metric'],'current_period');check(node['fact']==fact and node['role']==role and node['coefficient']==coef,'Wrong selected flow fact')
  check(node['as_of']==request['as_of'] and node['mode']==request['mode'],'Mixed knowledge clocks')
  check(node['selected_document']==({k:doc[k] for k in ('document_id','source_sha256','revision','published_date')} if doc else None) and node['report_period_end']==report and node['metric']==request['metric'] and node['column']=='current_period','Wrong disclosure revision')
  if fact is None or fact['value'] is None:missing.append(role)
  else:values.append(Fraction(fact['value'])*coef)
 assets=r['result']['asset_components'];ep=request['asset_endpoints'];check(len(assets)==(2 if ep else 0),'Asset component count')
 if ep:
  for node,role,target in zip(assets,['beginning','ending'],[start-timedelta(days=1),end]):
   spec=ep[role];doc,fact=pick(spec['report_period_end'],'total_assets',spec['column']);check(node['fact']==fact and node['selected_document']==({k:doc[k] for k in ('document_id','source_sha256','revision','published_date')} if doc else None),'Wrong selected stock fact')
   check(node['as_of']==request['as_of'] and node['mode']==request['mode'],'Mixed asset clocks')
   if fact is None or fact['value'] is None:missing.append(role+'_assets')
   else:
    check(fact['basis']=='point_in_time_stock' and fact['period_start'] is None and fact['period_end']==str(target),'Wrong stock date');points.append(Fraction(fact['value']))
 if missing:check(r['status']=='unavailable' and r['ttm_value'] is None and r['endpoint_average_assets'] is None and set(r['result']['missing_components'])==set(missing),'Missing fact filled')
 else:
  check(r['status']=='arithmetic_available' and Fraction(r['ttm_value'])==sum(values),'Wrong TTM arithmetic')
  if points:
   avg=sum(points)/2;m=r['endpoint_average_assets'];a=m['fraction_yuan'];check(isinstance(a['numerator'],str) and isinstance(a['denominator'],str),'Unsafe JSON rational')
   check(Fraction(int(a['numerator']),int(a['denominator']))==avg==Fraction(m['value_exact_yuan']),'Wrong exact stock mean')
  else:check(r['endpoint_average_assets'] is None,'Unexpected stock mean')


def review(w,anchor):
 check(sha(w/'plan.json')==anchor,'External plan anchor differs');plan=load(w/'plan.json');check(digest(plan['protocol'])==PROTOCOL_SHA and plan['mode'] in ['smoke','full'],'Unregistered protocol')
 for rel,h in plan['source_pins'].items():
  p=w/'source-archive'/rel;check(not Path(rel).is_absolute() and '..' not in Path(rel).parts and sha(p)==h,'Frozen source changed')
 for v in plan['inputs'].values():check(sha(Path(v['path']))==v['sha256'],'Input changed')
 check(plan['inputs']['manifest']['sha256']==MANIFEST_SHA and plan['inputs']['typed_input']['sha256']==TYPED_SHA,'Unregistered sample')
 manifest=load(Path(plan['manifest']));typed=load(Path(plan['typed_input']))['facts'];check(len(typed)==43,'Wrong typed sample')
 check({k[7:] for k in plan['inputs'] if k.startswith('source:')}==set(manifest['source_files']),'Source inventory changed')
 db=w/'research.sqlite3';check(sha(db)==load(w/'seed.json')['research_main_sha256'],'Research main changed');c=sqlite3.connect(db.as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
 try:
  parents={r['id']:dict(r) for r in c.execute('select * from qr_financial_bundle')};packets={r['id']:dict(r) for r in c.execute('select * from qr_financial_fact_packet')};check(len(parents)==2 and len(packets)==4,'Owned archive count')
  for row in parents.values():
   p=parse(row['payload']);row['parsed']=p;expected_pins={name:plan['source_pins']['backend/deepfocus_api/'+name] for name in ['quant_financial_vintages.py','quant_corporate_actions.py','quant_research_engine.py','quant_financial_store.py']};check(p['source_pins']==expected_pins,'Parent code identity differs');check(digest(p)==row['fingerprint'] and digest(p['input'])==row['input_hash'] and p['input']['manifest']==manifest,'Parent data differs')
   expected=json.loads(json.dumps(manifest))
   for d in expected['vintages']:d['recorded_at']=row['created']
   check(p['effective_manifest']==expected and p['recorded_at']==row['created'] and datetime.fromisoformat(p['stored_visible_at'])==boundary(row['created']),'Parent clock changed')
   for h in manifest['source_files']:
    body=c.execute('select body from qr_financial_source where owner=? and sha=?',(row['owner'],h)).fetchone();check(body is not None and hashlib.sha256(body[0]).hexdigest()==h,'Parent source bytes differ')
  for row in packets.values():
   p=parse(row['payload']);row['parsed']=p;expected_pins={name:plan['source_pins']['backend/deepfocus_api/'+name] for name in ['quant_financial_vintages.py','quant_corporate_actions.py','quant_research_engine.py','quant_financial_store.py','quant_financial_fact_store.py']};check(p['source_pins']==expected_pins,'Extraction code identity differs');parent=parents[row['parent_id']];check(row['owner']==parent['owner'] and p['parent_fingerprint']==parent['fingerprint'] and digest(p)==row['fingerprint'] and digest(p['input'])==row['input_hash'],'Extraction provenance differs')
   check(p['recorded_at']==row['created'] and datetime.fromisoformat(p['stored_visible_at'])==boundary(row['created']),'Extraction clock differs')
   check(p['input']['facts']==(typed[:5] if row['revision']==1 else typed) and p['input']['correction_notes']==[] and row['revision'] in [1,2],'Extraction facts differ')
   prior=p['input']['supersedes_packet_id']
   if row['revision']==1:check(prior is None,'Root predecessor')
   else:check(prior in packets and packets[prior]['owner']==row['owner'] and packets[prior]['parent_id']==row['parent_id'] and packets[prior]['revision']==1,'Wrong extraction predecessor')
  check(c.execute('select count(*) from qr_paper').fetchone()[0]==0 and c.execute('select count(*) from qr_experiment').fetchone()[0]==0,'Unexpected strategy/account writes')
 finally:c.close()
 golden=load(w/'golden.json');check(len(golden)==6,'Golden matrix count')
 for g in golden:validate_golden(g,packets,parents)
 check([g['result']['status'] for g in golden]==['arithmetic_available','unavailable','arithmetic_available','unavailable','unavailable','unavailable'],'Wrong registered statuses')
 check(golden[1]['request']['as_of']==plan['knowledge_as_of'],'Fixed stored clock differs')
 result=load(w/'result.json');m=result['metrics'];check(result['protocol']==plan['protocol'] and result['mode']==plan['mode'],'Result protocol differs');check(not m['errors'],'Observed workload errors')
 observations=[parse(s) for s in raw(w/'observations.jsonl').splitlines()];rounds=[e for e in observations if e['kind']=='round'];n=len(rounds)
 check(n==m['rounds'] and n>=2 and [r['index'] for r in rounds]==list(range(1,n+1)),'Missing rounds')
 server=[e for e in observations if e['kind']=='server-read'];check(Counter((e['round'],e['lane']) for e in server)==Counter((r,l) for r in range(n) for l in range(4)),'Missing/duplicate server reads')
 check(len(list((w/'results').glob('*.json')))==4*n,'Unexpected result files')
 for e in server:
  v=load(w/'results'/f"{e['round']:04}-{e['lane']}.json");check(v==golden[e['lane']]['result'] and digest(v)==e['result_digest'],'Actual result mismatch')
 cancels=[e for e in observations if e['kind']=='client-cancel'];check(len(cancels)==n,'Missing client cancellation records')
 for i,e in enumerate(cancels):check(e['round']==i and e['reads_held_after']==4 and e['lanes']==[j for j in range(4) if (i*4+j+1)%5==0],'Cancellation freed slots or differs')
 quota=[e for e in observations if e['kind']=='quota-refusal'];check(Counter((e['round'],e['phase']) for e in quota)==Counter((i,p) for i in range(n) for p in ['owner','global','after_client_cancel']),'Quota probes incomplete')
 for e in quota:check((e['global']==2 and e['same_owner']==2) if e['phase']=='owner' else e['global']==4,'Wrong quota occupancy')
 validate_http_matrix(golden,observations,m,n)
 http=[e for e in observations if e['kind']=='http'];empty=digest({'items':[],'next_cursor':None});notfound=digest({'detail':'档案不存在'});busy=digest({'detail':'档案读取繁忙，请稍后重试'})
 expected={(g['who'],url(g)):g['digest'] for g in golden}
 for e in http:
  if e['status']==429:check(e['body_digest']==busy,'Wrong quota response')
  elif e['who']=='observer':check((e['status']==200 and e['body_digest']==empty) or (e['status']==404 and e['body_digest']==notfound),'Observer saw owned content')
  else:check(e['status']==200 and expected.get((e['who'],e['route']))==e['body_digest'],'Client response mismatched registered request')
 check(m['server_completed']==m['exact_server_results']==4*n and m['client_cancelled']==4*n//5 and m['client_completed']+m['client_cancelled']==4*n and m['quota_checks']==3*n,'Counter mismatch')
 check(sorted(e['elapsed_ms'] for e in server)==sorted(m['server_query_ms']),'Server latency not raw')
 check(sorted(e['elapsed_ms'] for e in http if e['category'] in ['lane','extra'])==sorted(m['query_ms']),'Client latency not raw')
 check(sorted(e['elapsed_ms'] for e in http if e['category']=='observer')==sorted(m['observer_ms']),'Observer latency not raw')
 period=30 if plan['mode']=='full' else 5
 check(all(period-2<=b['elapsed_seconds']-a['elapsed_seconds']<=period+5 for a,b in zip(rounds,rounds[1:])),'Workload rounds not spread over registered period')
 lat={k:{'p95':pct(m[k],.95),'p99':pct(m[k],.99)} for k in ['query_ms','server_query_ms','observer_ms']};check(all(v['p95']<=500 and v['p99']<=1500 for v in lat.values()),'Read latency gate')
 check(pct(m['loop_ms'],.99)<=250 and max(m['loop_ms'])<=2000 and len(m['loop_ms'])>=500,'Heartbeat gate')
 check(m['remaining_reads']==m['paper_accounts']==m['external_connection_attempts']==m['forbidden_spawn_attempts']==0 and m['research_main_unchanged'],'Reads or side effects left')
 check(m['peak_qr_disk_bytes']>=db.stat().st_size and m['peak_qr_disk_bytes']<=512*1024**2,'Research disk gate')
 memory=m['memory'];parent=load(w/'parent-memory.json');check(memory['largest_waited_child_peak_bytes']==0 and memory['all_children_waited'] and memory['SIGCHLD_not_ignored'],'API children not drained')
 bound=parent['control_parent_peak_bytes']+parent['largest_waited_descendant_peak_bytes'];check(parent['all_children_waited'] and parent['SIGCHLD_not_ignored'] and parent['accepted'] and parent['largest_waited_descendant_peak_bytes']>=memory['api_lifetime_peak_bytes']>0 and parent['whole_tree_peak_upper_bound_bytes']==bound<=1024**3,'Whole-tree memory gate')
 exit=load(w/'exit.json');check(exit['worker_exit_code']==0 and exit['all_children_waited'] and exit['environment_unchanged'],'Worker did not exit successfully')
 for pid in [exit['worker_pid'],exit['parent_pid']]:
  try:os.kill(pid,0)
  except ProcessLookupError:pass
  else:raise ValueError('Owned PID still exists')
 runtime=load(w/'runtime.json');check(runtime['environment']==plan['environment'] and set(plan['environment']['packages'])=={'fastapi','uvicorn','httpx','pydantic','SQLAlchemy','python-jose','passlib','uvloop'},'Runtime package identity changed')
 with socket.socket() as s:check(s.connect_ex(('127.0.0.1',runtime['port']))!=0,'Owned port still open')
 full=plan['mode']=='full';check(result['evaluation']['engineering_checks_passed'],'Engineering gate refused')
 if full:check(m['elapsed_seconds']>=3600 and n>=120 and m['observer_cycles']>=3000 and len(m['loop_ms'])>=200000 and result['evaluation']['full_soak_passed'],'Full one-hour coverage missing')
 else:check(not result['evaluation']['full_soak_passed'],'Smoke mislabeled as full')
 return {'schema':'independent-financial-read-soak-review-v1','plan_sha256':anchor,'mode':plan['mode'],'engineering_checks_passed':True,'full_soak_passed':full,'rounds':n,'server_reads_exact':4*n,'client_cancelled':m['client_cancelled'],'observer_cycles':m['observer_cycles'],'latency_ms':lat,'whole_tree_rss_upper_bytes':bound,'research_disk_peak_bytes':m['peak_qr_disk_bytes'],'project_modules_imported':False,'production_writes':0,'promotion_allowed':False}


def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--workspace',type=Path,required=True);p.add_argument('--expected-plan-sha256',required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();v=review(a.workspace.resolve(),a.expected_plan_sha256)
 with a.output.open('x') as f:json.dump(v,f,ensure_ascii=False,indent=2)
 print(json.dumps(v,ensure_ascii=False))


if __name__=='__main__':main()
