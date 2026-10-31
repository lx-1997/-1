#!/usr/bin/env python3
"""Bounded public filings or index inventory from the existing blind sample.

Current directory resolves issuer identity only. Index completeness never proves
financial/revision/PIT completeness. No facts, returns, signals or orders.
"""
import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from deepfocus_api import quant_research_sample as sample

INDEX='https://www.cninfo.com.cn/new/hisAnnouncement/query'
QUERIES=[('annual-2024','2024年年度报告'),('q1-2024-short','2024年一季度报告'),
         ('q1-2024-long','2024年第一季度报告'),('q1-2025-short','2025年一季度报告'),
         ('q1-2025-long','2025年第一季度报告')]
LIMITS={'stocks':8,'requests':112,'wall_seconds':240,'request_seconds':12,
        'json_bytes':4*1024**2,'pdf_bytes':10*1024**2,'total_bytes':96*1024**2,
        'maximum_index_rows_per_query':120,'page_size':30,'retries':0}

INDEX_LIMITS={**LIMITS,'stocks':64,'requests':400,'total_bytes':32*1024**2,'pdf_bytes':0}


def now():return datetime.now(timezone.utc).isoformat()
def sha(raw):return hashlib.sha256(raw).hexdigest()


def register(sample_raw,directory_raw,*,offset=0,count=8,index_only=False):
    parent=json.loads(sample_raw);queries=sample.validate_plan(parent)
    if (type(offset) is not int or type(count) is not int or type(index_only) is not bool
            or offset<0 or count<1 or offset+count>len(queries) or (not index_only and count>8)):
        raise ValueError('Explicit bounded original sample slice required; PDF batch at most eight')
    directory=json.loads(directory_raw)['stockList'];issuers=[]
    for q in queries[offset:offset+count]:
        symbol=q['code'].split('.')[1]
        found=[r for r in directory if r.get('code')==symbol and r.get('category')=='A股']
        if len(found)!=1 or not re.fullmatch(r'[A-Za-z0-9]{1,40}',str(found[0].get('orgId',''))):
            raise ValueError('Ambiguous or missing issuer; do not replace blind sample member')
        issuers.append({'code':q['code'],'symbol':symbol,'org_id':found[0]['orgId'],
                        'current_directory_name':found[0].get('zwjc')})
    return {'schema':'blind-cohort-financial-index-v2' if index_only else 'blind-cohort-financial-filings-v1','registered_at':now(),
            'parent_sample_sha256':sha(sample_raw),'directory_sha256':sha(directory_raw),
            'collector_sha256':sha(Path(__file__).read_bytes()),'issuers':issuers,
            'selection':'original_64_hash_order_slice_no_replacement','sample_slice':{'offset':offset,'count':count},'index_only':index_only,
            'queries':[list(q) for q in QUERIES],'index_disclosure_start':'2024-01-01','index_disclosure_end':'2026-11-01',
            'target_report_period':'2025-03-31','historical_comparison_clock':'2025-05-01T00:00:00+08:00',
            'historical_clock_role':'future_fact_review_target_not_as_recorded_or_unseen_certificate',
            'directory_role':'current_identity_lookup_not_historical_universe_or_eligibility',
            'limits':dict(INDEX_LIMITS if index_only else LIMITS),'facts_reviewed':False,'historical_pit_certified':False,
            'full_revision_history_certified':False,'strategy_evaluations':0,'production_writes':0}


def index_page(data,page,issuer,total_seen=None):
    n=data.get('totalAnnouncement');rows=data.get('announcements') or []
    if type(n) is not int or not 0<=n<=120 or not isinstance(rows,list):raise ValueError('Invalid/bounded index required')
    if total_seen is not None and n!=total_seen:raise ValueError('Index total changed')
    if type(page) is not int or not 1<=page<=4 or len(rows)!=min(30,max(0,n-(page-1)*30)):
        raise ValueError('Incomplete or invalid index page')
    for row in rows:
        doc=str(row.get('announcementId'));path=row.get('adjunctUrl','');epoch=row.get('announcementTime')
        if (row.get('secCode')!=issuer['symbol'] or str(row.get('orgId'))!=issuer['org_id']
                or not re.fullmatch(r'\d{1,20}',doc)
                or not re.fullmatch(r'finalpage/\d{4}-\d{2}-\d{2}/'+re.escape(doc)+r'\.(?:PDF|pdf)',path)
                or type(epoch) is not int or not 0<=epoch<=int(datetime.now(timezone.utc).timestamp()*1000)):
            raise ValueError('Issuer/document/time mismatch')
        day=datetime.fromtimestamp(epoch/1000,timezone(timedelta(hours=8))).date().isoformat()
        if path.split('/')[1]!=day or not '2024-01-01'<=day<='2026-11-01':raise ValueError('Disclosure day mismatch/outside scope')
        if not isinstance(row.get('announcementTitle'),str):raise ValueError('Missing title')
    return n,rows


def selected(row):
    text=re.sub(r'\s+','',re.sub(r'<[^>]+>','',row['announcementTitle']))
    if any(s in text for s in ('摘要','说明会','提示性','取消','英文','审议','关于')):return False
    return bool(re.search(r'(?:2024年年度报告(?:全文)?|202[45]年(?:第?一|1)季度报告)(?:[（(](?:更正后|修订版|更新后)[）)])?$',text))


class Retainer:
    def __init__(self,workspace,limits=None):
        self.limits=LIMITS if limits is None else limits
        self.workspace=workspace;self.captures=[];self.used=0;self.started=time.monotonic()
    def remaining(self):return self.limits['wall_seconds']-(time.monotonic()-self.started)
    def get(self,client,url,name,form=None,pdf=False):
        if self.remaining()<=0 or len(self.captures)>=self.limits['requests']:raise TimeoutError('Fixed capture budget exhausted')
        record={'url':url,'request_form':form,'requested_at':now(),'status':None,'valid_response':False}
        self.captures.append(record) # Failed attempts also consume the request budget.
        body=bytearray();failure=None
        try:
            with client.stream('POST' if form is not None else 'GET',url,data=form,timeout=min(12,self.remaining())) as response:
                record['status']=response.status_code
                for chunk in response.iter_bytes():
                    remaining=min((self.limits['pdf_bytes'] if pdf else self.limits['json_bytes'])-len(body),self.limits['total_bytes']-self.used-len(body))
                    body.extend(chunk[:max(0,remaining)])
                    if len(chunk)>remaining or self.remaining()<=0:raise ValueError('Fixed byte/time budget exhausted')
            if record['status']!=200 or (pdf and not body.startswith(b'%PDF-')):raise ValueError('HTTP status/PDF signature rejected')
            record['valid_response']=True
        except Exception as exc:
            failure=exc;record['error_type']=type(exc).__name__
        raw=bytes(body);self.used+=len(raw);file=name if failure is None else name+'.response.bin'
        with (self.workspace/file).open('xb') as f:f.write(raw)
        record.update(file=file,observed_at=now(),bytes=len(raw),sha256=sha(raw))
        if failure is not None:raise failure
        return raw,record


def capture(sample_path,directory_path,workspace,*,offset=0,count=8,index_only=False):
    import httpx
    if not workspace.is_absolute() or workspace.exists():raise ValueError('NEW absolute workspace required')
    original={}
    for path in (sample_path,directory_path):
        with path.open('rb') as stream:raw=stream.read(8*1024**2+1)
        if len(raw)>8*1024**2:raise ValueError('Input exceeds 8MiB')
        original[path]=raw
    plan=register(original[sample_path],original[directory_path],offset=offset,count=count,index_only=index_only);workspace.mkdir(mode=0o700)
    with (workspace/'plan.json').open('x') as f:json.dump(plan,f,ensure_ascii=False,indent=2)
    limits=plan['limits'];retained=Retainer(workspace,limits);queries=[];filings={};errors=[]
    with httpx.Client(trust_env=False,headers={'User-Agent':'Mozilla/5.0','Referer':'https://www.cninfo.com.cn/'}) as client:
        for issuer in plan['issuers']:
            for key,keyword in QUERIES:
                if retained.remaining()<=0 or len(retained.captures)>=limits['requests'] or retained.used>=limits['total_bytes']:break
                result={'symbol':issuer['symbol'],'key':key,'complete':False,'document_ids':[]};queries.append(result)
                try:
                    page=1;total=None
                    while page<=max(1,math.ceil((total or 0)/30)):
                        form={'pageNum':str(page),'pageSize':'30','column':'szse','tabName':'fulltext','plate':'',
                              'stock':issuer['symbol']+','+issuer['org_id'],'searchkey':keyword,'secid':'','category':'','trade':'',
                              'seDate':'2024-01-01~2026-11-01','sortName':'time','sortType':'asc','isHLtitle':'false'}
                        raw,record=retained.get(client,INDEX,issuer['symbol']+'-'+key+'-'+str(page)+'.json',form)
                        total,rows=index_page(json.loads(raw),page,issuer,total)
                        for row in rows:
                            doc=str(row['announcementId'])
                            if doc in result['document_ids']:raise ValueError('Duplicate query document')
                            result['document_ids'].append(doc)
                            if selected(row):
                                identity=issuer['symbol']+':'+doc
                                if identity in filings and filings[identity]['row']!=row:raise ValueError('Conflicting indexed document')
                                filings.setdefault(identity,{'issuer':issuer,'row':row,'first_index_sha256':record['sha256']})
                        page+=1
                    if len(result['document_ids'])!=total:raise ValueError('Untraversed query total')
                    result.update(complete=True,total=total,pages=page-1)
                except Exception as exc:errors.append({'stage':'index','symbol':issuer['symbol'],'key':key,'error_type':type(exc).__name__})
            print(json.dumps({'symbol':issuer['symbol'],'queries_recorded':sum(q['symbol']==issuer['symbol'] for q in queries)}),flush=True)
        for identity,item in ([] if index_only else filings.items()):
            if retained.remaining()<=0 or len(retained.captures)>=limits['requests'] or retained.used>=limits['total_bytes']:break
            try:
                _,record=retained.get(client,'https://static.cninfo.com.cn/'+item['row']['adjunctUrl'],identity.replace(':','-')+'.pdf',pdf=True)
                item['capture']=record
            except Exception as exc:errors.append({'stage':'pdf','identity':identity,'error_type':type(exc).__name__})
    if any(path.read_bytes()!=raw for path,raw in original.items()) or sha(Path(__file__).read_bytes())!=plan['collector_sha256']:
        errors.append({'stage':'inputs','error_type':'InputOrSourceChanged'})
    report={'schema':plan['schema'],'plan':plan,'finished_at':now(),'captures':retained.captures,'queries':queries,'filings':filings,'errors':errors,
            'not_started_queries':len(plan['issuers'])*len(QUERIES)-len(queries),'index_only':index_only,'PDFs_not_requested':list(filings) if index_only else [],
            'PDFs_not_retained':[] if index_only else [k for k,v in filings.items() if 'capture' not in v],
            'query_inventory_complete':len(queries)==len(plan['issuers'])*len(QUERIES) and all(q['complete'] for q in queries),
            'facts_reviewed':False,'full_revision_history_certified':False,'historical_pit_certified':False,'strategy_evaluations':0,'production_writes':0}
    with (workspace/'manifest.json').open('x') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['sample','directory','workspace']:p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--offset',type=int,default=0);p.add_argument('--count',type=int,default=8);p.add_argument('--index-only',action='store_true')
    a=p.parse_args();r=capture(a.sample,a.directory,a.workspace,offset=a.offset,count=a.count,index_only=a.index_only)
    print(json.dumps({'queries':len(r['queries']),'candidates':len(r['filings']),'PDFs':sum('capture' in f for f in r['filings'].values()),'errors':len(r['errors']),'not_started_queries':r['not_started_queries']}))
    return 0 if not r['errors'] and not r['not_started_queries'] and not r['PDFs_not_retained'] else 2


if __name__=='__main__':raise SystemExit(main())
