"""Reconcile a retained discovery catalog with evidence, never certify coverage.

The vendor report-period table is not an authoritative event ledger: special
dividends may be absent or only ordinary cash may be reported for the same date.
Catalog rows must never become execution events without the actual filing.
"""
from collections import Counter,defaultdict
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal,InvalidOperation
import hashlib
import json
import re
from urllib.parse import parse_qs,urlsplit
from .quant_corporate_actions import timestamp,visible_events,validate_evidence
from .quant_research_engine import digest

SCHEMA='action-catalog-review-v1'


def _day(value):
    if value is None:return None
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}(?: 00:00:00)?',value):
        raise ValueError('Invalid catalog date')
    return date.fromisoformat(value[:10]).isoformat()


def _amount(value):
    if value is None:return None
    if isinstance(value,bool):raise ValueError('Invalid catalog amount')
    try:number=Decimal(str(value))
    except InvalidOperation as exc:raise ValueError('Invalid catalog amount') from exc
    if not number.is_finite() or number<0 or number>Decimal('1000000000') or number.as_tuple().exponent < -12:
        raise ValueError('Invalid catalog amount')
    return str(number)


def parse_catalog(captures,bodies,*,as_of):
    """Validate request identity, raw bytes, pagination and per-row identity."""
    clock=timestamp(as_of);groups=defaultdict(list)
    if not captures or len(captures)>100:raise ValueError('Catalog capture count out of bounds')
    for item in captures:
        symbol=item['symbol'];page=item['page'];url=urlsplit(item['url'])
        if not re.fullmatch(r'[036]\d{5}',symbol) or type(page) is not int or not 1<=page<=20:
            raise ValueError('Invalid catalog identity')
        expected={'reportName':['RPT_SHAREBONUS_DET'],'columns':['ALL'],'pageSize':['100'],
                  'pageNumber':[str(page)],'sortColumns':['EX_DIVIDEND_DATE'],'sortTypes':['-1'],
                  'filter':[f'(SECURITY_CODE="{symbol}")']}
        if (url.scheme!='https' or url.netloc!='datacenter-web.eastmoney.com' or
            url.path!='/api/data/v1/get' or url.fragment or parse_qs(url.query)!=expected):
            raise ValueError('Catalog request identity mismatch')
        observed=timestamp(item['observed_at'])
        if observed>clock+timedelta(seconds=5):raise ValueError('Future catalog observation')
        raw=bodies.get(item['sha256'])
        if not isinstance(raw,bytes) or len(raw)>2*1024*1024 or hashlib.sha256(raw).hexdigest()!=item['sha256']:
            raise ValueError('Catalog raw bytes missing or modified')
        payload=json.loads(raw,parse_float=str)
        result=payload.get('result')
        if payload.get('success') is not True or payload.get('code')!=0 or not isinstance(result,dict):
            raise ValueError('Catalog provider failure')
        count=result.get('count');pages=result.get('pages');rows=result.get('data')
        if (type(count) is not int or not 0<=count<=2000 or type(pages) is not int or
            pages not in {max(1,(count+99)//100),(count+99)//100} or not isinstance(rows,list)):
            raise ValueError('Invalid catalog pagination')
        normalized=[]
        for row in rows:
            suffix='SH' if symbol.startswith('6') else 'SZ'
            if row.get('SECURITY_CODE')!=symbol or row.get('SECUCODE')!=symbol+'.'+suffix:
                raise ValueError('Catalog row security mismatch')
            dates={k:_day(row.get(v)) for k,v in [('record_date','EQUITY_RECORD_DATE'),
                  ('ex_date','EX_DIVIDEND_DATE'),('notice_date','NOTICE_DATE'),('report_period','REPORT_DATE')]}
            if not dates['report_period'] or not isinstance(row.get('ASSIGN_PROGRESS'),str):raise ValueError('Catalog row lacks identity/status')
            cash=_amount(row.get('PRETAX_BONUS_RMB'))
            normalized.append({'symbol':symbol,**dates,'cash_per_share':str(Decimal(cash)/10) if cash is not None else None,
                'bonus_per_10':_amount(row.get('BONUS_RATIO')),'transfer_per_10':_amount(row.get('IT_RATIO')),
                'progress':row['ASSIGN_PROGRESS'],'plan':row.get('IMPL_PLAN_PROFILE'),
                'raw_row_digest':digest(row),'source_sha256':item['sha256'],'source_url':item['url'],
                'observed_at':item['observed_at']})
        groups[symbol].append((page,pages,count,normalized))
    output=[]
    for symbol,parts in sorted(groups.items()):
        pages,count=parts[0][1:3];expected_pages=max(1,pages)
        if (any(p[1:3]!=(pages,count) for p in parts) or
            sorted(p[0] for p in parts)!=list(range(1,expected_pages+1))):raise ValueError('Incomplete or drifting catalog pages')
        rows=[r for p in sorted(parts) for r in p[3]]
        if len(rows)!=count:raise ValueError('Catalog row count mismatch')
        identities=[(r['report_period'],r['record_date'],r['ex_date']) for r in rows]
        if len(identities)!=len(set(identities)):raise ValueError('Duplicate/conflicting catalog event identity')
        output.extend(rows)
    return output


def reconcile(captures,bodies,events,sources,ranges,*,as_of):
    validate_evidence(events,sources)
    rows=parse_catalog(captures,bodies,as_of=as_of)
    if not ranges or set(ranges)!={c['symbol'] for c in captures}:raise ValueError('Catalog must cover exactly the declared research securities')
    for symbol,(start,end) in ranges.items():
        if not re.fullmatch(r'[036]\d{5}',symbol) or date.fromisoformat(start)>date.fromisoformat(end):raise ValueError('Invalid review interval')
        if date.fromisoformat(end)>timestamp(as_of).astimezone(timezone(timedelta(hours=8))).date():raise ValueError('Future review interval')
    latest=visible_events(events,as_of,mode='as_recorded')
    included=[e for e in latest if e.symbol in ranges and ranges[e.symbol][0]<=e.ex_date<=ranges[e.symbol][1]]
    outcomes=[];outside=0;unresolved=[]
    for row in rows:
        start,end=ranges[row['symbol']]
        if not row['ex_date']:
            unresolved.append(row);continue
        if not start<=row['ex_date']<=end:outside+=1;continue
        found=[e for e in included if (e.symbol,e.ex_date)==(row['symbol'],row['ex_date'])]
        noncash=any(Decimal(row[k] or '0')>0 for k in ('bonus_per_10','transfer_per_10'))
        status='filing_missing'
        if row['progress']!='实施分配':status='not_implemented_or_unknown_status'
        elif noncash:status='noncash_action_unsupported'
        elif len(found)>1:status='ambiguous_same_day_events_requires_manual_review'
        elif found:
            e=found[0]
            if row['record_date']!=e.record_date:status='record_date_conflict'
            elif row['cash_per_share'] is None:status='cash_amount_unknown'
            elif Decimal(row['cash_per_share'])!=Decimal(e.cash_per_share_gross):status='cash_amount_conflict'
            else:status='matches_operator_filing_evidence'
        outcomes.append({**row,'status':status,'evidence_event_ids':[e.event_id for e in found]})
    # An implementation filing missing from the catalog is a gap in the catalog,
    # not evidence that the dividend did not happen.
    absent=[{'event_id':e.event_id,'symbol':e.symbol,'ex_date':e.ex_date,'source_sha256':e.source_sha256,
             'status':'filing_event_absent_from_catalog'} for e in included
            if not any(r['symbol']==e.symbol and r['ex_date']==e.ex_date for r in rows)]
    return {'schema':SCHEMA,'reviewed_at':as_of,'ranges':ranges,'catalog_row_count':len(rows),
            'in_range_catalog_rows':len(outcomes),'outside_interval_rows':outside,
            'outcomes':outcomes,'unresolved_date_rows':unresolved,'filing_events_absent_from_catalog':absent,
            'status_counts':dict(Counter(r['status'] for r in outcomes)),
            'catalog_pagination_verified':True,'economic_event_coverage_verified':False,
            'promotion_allowed':False,'execution_events_generated':False,'oos_validation':False,
            'fact_review':'operator_filing_evidence_not_automated_pdf_fact_extraction',
            'limitations':['vendor_report_period_table_is_discovery_only','not_a_rights_issues_or_delisting_inventory',
                           'pay_date_and_ex_reference_need_implementation_filing','unknown_null_noncash_fields_are_not_proof_of_no_action']}
