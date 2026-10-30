#!/usr/bin/env python3
"""Retain CNINFO implementation-filing candidates, not verified cash events.

Explicit scoped stocks, current identity directory, bounded pagination and raw
documents. An index miss/error is never proof that no economic action occurred.
"""
import argparse,hashlib,json,math,re,sys
from datetime import datetime,timezone,timedelta,date
from pathlib import Path
import httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from deepfocus_api.quant_research_engine import digest

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--experiments',type=Path,required=True)
p.add_argument('--workspace',type=Path,required=True)
a=p.parse_args()
if not a.workspace.is_absolute() or a.workspace.exists():p.error('Use a new absolute workspace')
exps=json.loads(a.experiments.read_text());symbols=set()
for e in exps:
    if digest(e['result'])!=e['result_digest'] or digest(e['dataset'])!=e['result']['dataset_digest']:p.error('Modified sealed experiment')
    symbols.update(e['request']['symbols'])
if not 1<=len(symbols)<=20 or any(not re.fullmatch(r'[036]\d{5}',s) for s in symbols):p.error('Invalid scoped securities')
start=min(e['request']['start_date'] for e in exps);end=max(e['request']['end_date'] for e in exps)
if date.fromisoformat(start)>date.fromisoformat(end) or date.fromisoformat(end)>datetime.now(timezone(timedelta(hours=8))).date():p.error('Invalid/future disclosure interval')
a.workspace.mkdir(parents=True,mode=0o700)
captures=[];errors=[];filings={};index_counts=[]
identities={}
collector_sha=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
def now():return datetime.now(timezone.utc).isoformat()
def finish():
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=collector_sha:
        errors.append({'stage':'source_changed','error':'CollectorChangedDuringCapture'})
    manifest={'schema':'implementation-filing-candidates-v1','created_at':now(),'scope':{'symbols':sorted(symbols),'start':start,'end':end},
              'identity_directory_role':'current_issuer_lookup_not_historical_universe','identities':identities,
              'captures':captures,'index_counts':index_counts,'filings':filings,'errors':errors,
              'implementation_facts_reviewed':False,'economic_coverage_verified':False,'execution_events_generated':False,
              'collector_sha256':collector_sha}
    with (a.workspace/'filing-captures.json').open('x') as f:json.dump(manifest,f,ensure_ascii=False,indent=2)
    print(json.dumps({'candidates':len(filings),'pdfs':sum('capture' in v for v in filings.values()),'errors':len(errors)}))
def retain(client,url,name,*,form=None,pdf=False):
    raw=bytearray()
    with client.stream('POST' if form else 'GET',url,data=form) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            raw.extend(chunk)
            if len(raw)>(10 if pdf else 4)*1024*1024:raise ValueError('Response size limit')
    body=bytes(raw)
    if pdf and not body.startswith(b'%PDF-'):raise ValueError('Not a PDF')
    with (a.workspace/name).open('xb') as f:f.write(body)
    entry={'url':url,'request_form':form,'file':name,'sha256':hashlib.sha256(body).hexdigest(),'observed_at':now(),'bytes':len(body)}
    captures.append(entry)
    return body,entry

with httpx.Client(trust_env=False,timeout=20,headers={'User-Agent':'Mozilla/5.0','Referer':'https://www.cninfo.com.cn/'}) as c:
    try:
        body,_=retain(c,'https://www.cninfo.com.cn/new/data/szse_stock.json','security-directory.json')
        directory=json.loads(body)['stockList']
        for code in symbols:
            found=[r for r in directory if r.get('code')==code and r.get('category')=='A股']
            if len(found)!=1 or not re.fullmatch(r'[A-Za-z0-9]+',found[0].get('orgId','')):raise ValueError('Ambiguous/missing issuer identity')
            identities[code]=found[0]
    except Exception as exc:
        errors.append({'stage':'identity_directory','error':type(exc).__name__});finish();sys.exit(1)
    for code in sorted(symbols):
        org=identities[code]['orgId']
        for keyword in ('权益分派','利润分配','分红派息'):
            try:
                page=1;pages=1;rows=[];total=None
                while page<=pages:
                    form={'pageNum':str(page),'pageSize':'30','column':'szse','tabName':'fulltext','plate':'',
                          'stock':code+','+org,'searchkey':keyword,'secid':'','category':'','trade':'',
                          'seDate':start+'~'+end,'sortName':'time','sortType':'desc','isHLtitle':'false'}
                    raw,source=retain(c,'https://www.cninfo.com.cn/new/hisAnnouncement/query',f'{code}-{keyword}-{page}.json',form=form)
                    response=json.loads(raw);n=response['totalAnnouncement'];items=response.get('announcements') or []
                    if type(n) is not int or not 0<=n<=600 or not isinstance(items,list):raise ValueError('Index pagination invalid')
                    if total is not None and n!=total:raise ValueError('Index changed during pagination')
                    total=n;pages=max(1,math.ceil(n/30));rows.extend(items)
                    for item in items:
                        if item.get('secCode')!=code or item.get('orgId')!=org:raise ValueError('Index issuer mismatch')
                        title=item['announcementTitle'];path=item['adjunctUrl'];identity=item['announcementId']
                        if not re.fullmatch(r'finalpage/\d{4}-\d{2}-\d{2}/\d+\.PDF',path):raise ValueError('Invalid source path')
                        if not re.fullmatch(r'\d+',str(identity)) or path.rsplit('/',1)[-1]!=str(identity)+'.PDF':raise ValueError('Filing document identity mismatch')
                        epoch=item['announcementTime']
                        if type(epoch) is not int:raise ValueError('Invalid disclosure timestamp')
                        published=datetime.fromtimestamp(epoch/1000,timezone(timedelta(hours=8))).date().isoformat()
                        if not start<=published<=end:raise ValueError('Out-of-scope disclosure')
                        if not re.search(r'(权益分派|利润分配|分红).*实施',title) or any(w in title for w in ('提示','调整','停止转股','优先股')):continue
                        key=code+':'+str(identity)
                        value={'symbol':code,'org_id':org,'title':title,'published_epoch_ms':item['announcementTime'],
                               'url':'https://static.cninfo.com.cn/'+path,'index_source_sha256':source['sha256']}
                        if key in filings and {k:v for k,v in filings[key].items() if k!='index_source_sha256'}!= {k:v for k,v in value.items() if k!='index_source_sha256'}:
                            raise ValueError('Conflicting indexed filing')
                        filings.setdefault(key,value)
                    page+=1
                if len(rows)!=total or len({r['announcementId'] for r in rows})!=total:raise ValueError('Index row coverage mismatch')
                index_counts.append({'symbol':code,'keyword':keyword,'rows':len(rows),'pages':pages})
            except Exception as exc:errors.append({'symbol':code,'keyword':keyword,'stage':'index','error':type(exc).__name__})
        print(json.dumps({'symbol':code,'indexed_filing_candidates':sum(v['symbol']==code for v in filings.values())}),flush=True)
    for key,item in filings.items():
        try:
            _,entry=retain(c,item['url'],key.replace(':','-')+'.pdf',pdf=True)
            item['capture']=entry
        except Exception as exc:errors.append({'filing':key,'stage':'pdf','error':type(exc).__name__})
        print(json.dumps({'filing':key,'retained':'capture' in item}),flush=True)
finish()
sys.exit(1 if errors else 0)
