#!/usr/bin/env python3
"""Bounded official filing discovery for the existing out-of-pool TTM QA case.

A complete keyword query is not a complete issuer/revision history or PIT feed.
No strategy, current-status selection, licensed feed, application or broker call.
"""
import argparse
from datetime import datetime,timezone,timedelta
import hashlib
import json
import math
from pathlib import Path
import re
import time

INDEX='https://www.cninfo.com.cn/new/hisAnnouncement/query'
QUERIES=[('annual','2024年年度报告','2025-01-01','2026-10-31'),
         ('quarter_short','2024年一季度报告','2024-04-01','2026-10-31'),
         ('quarter_long','2024年第一季度报告','2024-04-01','2026-10-31'),
         ('correction','前期会计差错更正','2024-04-01','2026-10-31'),
         ('restatement','2024年度对以前年度报告','2025-01-01','2026-10-31')]
RULES={'symbol':'002109','issuer_id':'9900002009','page_size':30,'maximum_pages_per_query':4,
       'maximum_requests':20,'wall_seconds':120,'per_request_timeout_seconds':15,
       'maximum_json_bytes':4*1024**2,'maximum_pdf_bytes':10*1024**2,'maximum_total_bytes':64*1024**2,
       'automatic_retries':0,'disclosure_timestamp_zone':'Asia/Shanghai_UTC+08:00','scope':'existing_out_of_pool_financial_data_quality_case_only',
       'full_revision_history_certified':False,'historical_pit_certified':False,'strategy_evaluations':0,
       'promotion_allowed':False,'production_writes':0}


def now():return datetime.now(timezone.utc).isoformat()


def title(value):return re.sub(r'\s+','',re.sub(r'<[^>]+>','',value))


def index_page(data,page,total_seen=None):
    total=data.get('totalAnnouncement');rows=data.get('announcements') or []
    if type(total) is not int or not 0<=total<=120 or not isinstance(rows,list):raise ValueError('Unbounded/invalid index')
    if total_seen is not None and total!=total_seen:raise ValueError('Index total changed during pagination')
    expected=min(30,max(0,total-(page-1)*30))
    if len(rows)!=expected:raise ValueError('Incomplete index page; not an empty history')
    for r in rows:
        doc=str(r.get('announcementId'));path=r.get('adjunctUrl','')
        if (r.get('secCode')!='002109' or str(r.get('orgId'))!='9900002009'
            or not re.fullmatch(r'finalpage/\d{4}-\d{2}-\d{2}/'+re.escape(doc)+r'\.PDF',path)
            or not re.fullmatch(r'\d+',doc) or type(r.get('announcementTime')) is not int
            or not 0<=r['announcementTime']<=int(datetime.now(timezone.utc).timestamp()*1000)):
            raise ValueError('Index issuer/date/document mismatch')
        published=datetime.fromtimestamp(r['announcementTime']/1000,timezone(timedelta(hours=8))).date().isoformat()
        if path.split('/')[1]!=published:raise ValueError('Index date and PDF URL differ')
    return total,rows


def selected(row):
    text=title(row['announcementTitle'])
    return bool(re.fullmatch(r'(?:公司)?2024年(?:年度报告|(?:第?一|1)季度报告)(?:[（(]更正后[）)])?',text)
                or '前期会计差错更正' in text or '2024年度对以前年度报告' in text)


def capture(workspace):
    import httpx
    if not workspace.is_absolute() or workspace.exists():raise ValueError('NEW absolute workspace required')
    workspace.mkdir(mode=0o700)
    source=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    plan={'schema':'financial-flow-filing-discovery-v1','registered_at':now(),'collector_sha256':source,
          'rules':RULES,'queries':QUERIES,'raw_layer':'decoded_HTTP_response_body_not_wire'}
    with (workspace/'plan.json').open('x') as f:json.dump(plan,f,ensure_ascii=False,indent=2)
    captures=[];queries=[];filings={};errors=[];started=time.monotonic();used=0
    def retained(client,url,name,form=None,pdf=False):
        nonlocal used
        remaining=RULES['wall_seconds']-(time.monotonic()-started)
        if remaining<=0 or len(captures)>=RULES['maximum_requests']:raise ValueError('Fixed request/wall budget exhausted')
        requested=now();body=bytearray();limit=RULES['maximum_pdf_bytes'] if pdf else RULES['maximum_json_bytes']
        with client.stream('POST' if form is not None else 'GET',url,data=form,timeout=min(15,remaining)) as response:
            status=response.status_code
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body)>limit or used+len(body)>RULES['maximum_total_bytes'] or time.monotonic()-started>120:
                    raise ValueError('Fixed body/time budget exceeded')
        used+=len(body);raw=bytes(body);good=status==200 and (not pdf or raw.startswith(b'%PDF-'))
        filename=name if good else name+'.response.bin'
        with (workspace/filename).open('xb') as f:f.write(raw)
        record={'file':filename,'url':url,'request_form':form,'requested_at':requested,'observed_at':now(),
                'status':status,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'valid_response':good}
        captures.append(record)
        if not good:raise ValueError('HTTP status or PDF signature rejected')
        return raw,record
    try:
        with httpx.Client(trust_env=False,headers={'User-Agent':'Mozilla/5.0','Referer':'https://www.cninfo.com.cn/'}) as client:
            for key,keyword,start,end in QUERIES:
                seen=[];total=None;page=1
                while page<=max(1,math.ceil((total or 0)/30)):
                    form={'pageNum':str(page),'pageSize':'30','column':'szse','tabName':'fulltext','plate':'',
                          'stock':'002109,9900002009','searchkey':keyword,'secid':'','category':'','trade':'',
                          'seDate':start+'~'+end,'sortName':'time','sortType':'asc','isHLtitle':'false'}
                    raw,record=retained(client,INDEX,f'index-{key}-{page}.json',form)
                    total,rows=index_page(json.loads(raw),page,total)
                    for row in rows:
                        identity=str(row['announcementId'])
                        if identity in seen:raise ValueError('Duplicate indexed document within query')
                        seen.append(identity)
                        if selected(row):
                            if identity in filings and filings[identity]['row']!=row:raise ValueError('Conflicting indexed disclosure')
                            filings.setdefault(identity,{'row':row,'index_sha256':record['sha256'],'index_file':record['file']})
                    page+=1
                if len(seen)!=total:raise ValueError('Index total not fully traversed')
                queries.append({'key':key,'total':total,'collected':len(seen),'pages':page-1,'query_complete':True})
            for identity,item in filings.items():
                _,record=retained(client,'https://static.cninfo.com.cn/'+item['row']['adjunctUrl'],identity+'.pdf',pdf=True)
                item['capture']=record
    except Exception as e:errors.append({'type':type(e).__name__,'reason':str(e),'observed_at':now()})
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=source:errors.append({'type':'CollectorChanged'})
    report={'schema':plan['schema'],'plan':plan,'finished_at':now(),'captures':captures,'queries':queries,
            'filings':filings,'errors':errors,'facts_reviewed':False,'full_revision_history_certified':False,
            'strategy_evaluations':0,'production_writes':0}
    with (workspace/'manifest.json').open('x') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--workspace',type=Path,required=True);a=p.parse_args()
    r=capture(a.workspace);print(json.dumps({'documents':len(r['filings']),'queries':r['queries'],'errors':r['errors']},ensure_ascii=False))
    return 2 if r['errors'] else 0


if __name__=='__main__':raise SystemExit(main())
