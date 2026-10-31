"""Reviewed financial-report vintages; no latest-value historical backfill.

Bounded CNINFO consolidated CNY year-to-date flow panels only. This reader is
not a complete PIT database, licensed feed, automatic fact extractor or signal.
"""
from dataclasses import asdict,dataclass
from datetime import date,datetime,time,timedelta,timezone
from decimal import Decimal,InvalidOperation
import hashlib,json,re
from pathlib import Path
from typing import Optional

from .quant_corporate_actions import BJ,timestamp
from .quant_research_engine import digest

VERSION='reviewed-financial-vintages-v1'
VALIDATION_VERSION='exact-financial-magnitude-v2'
ISSUER_IDENTITY_VERSION='cninfo-ascii-org-v2'
SOURCE_URL_VERSION='cninfo-exact-indexed-PDF-or-pdf-v2'
ISSUER_ID_PATTERN=r'cninfo:[A-Za-z0-9]{5,40}'
METRICS={'revenue','parent_net_profit','net_operating_cashflow'}
BOOK_TIME_POLICY='date_only_disclosure_visible_from_next_calendar_day_00_00_beijing'


@dataclass(frozen=True)
class Vintage:
    issuer_id:str
    symbol:str
    period_start:str
    period_end:str
    scope:str
    basis:str
    currency:str
    revision:int
    document_id:str
    published_date:str
    observed_at:str
    recorded_at:str
    source_url:str
    source_sha256:str
    index_sha256:str
    source_page:int
    metrics:dict
    supersedes_document_id:Optional[str]=None
    metric_source_pages:Optional[dict]=None


def _read_source_pins():
    return {n:hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest()
            for n in ('quant_financial_vintages.py','quant_corporate_actions.py','quant_research_engine.py')}


_LOADED_SOURCE_PINS=_read_source_pins()


def source_pins():
    return dict(_LOADED_SOURCE_PINS)


def require_current_source():
    if _read_source_pins()!=_LOADED_SOURCE_PINS:raise ValueError('Loaded financial source identity changed on disk')


def available_at(record):
    return datetime.combine(date.fromisoformat(record.published_date)+timedelta(days=1),time(),BJ)


def _group(record):
    return record.issuer_id,record.symbol,record.period_start,record.period_end,record.scope,record.basis,record.currency


def _validate_record(record,sources,now):
    if not re.fullmatch(ISSUER_ID_PATTERN,record.issuer_id) or not re.fullmatch(r'\d{6}',record.symbol):
        raise ValueError('Unsupported issuer or symbol')
    start,end,published=map(date.fromisoformat,(record.period_start,record.period_end,record.published_date))
    if (record.period_start!=str(end.year)+'-01-01' or start>end or end>=published
            or record.period_end[-5:] not in {'03-31','06-30','09-30','12-31'}):
        raise ValueError('Expected YTD period ending before publication')
    if record.scope!='consolidated' or record.basis!='year_to_date' or record.currency!='CNY':
        raise ValueError('Unsupported accounting scope, basis or units')
    observed,recorded=timestamp(record.observed_at),timestamp(record.recorded_at)
    if not datetime.combine(published,time(),BJ)<=observed<=recorded<=now:
        raise ValueError('Invalid publication/observation/recording clocks')
    if (type(record.revision) is not int or record.revision<1 or not re.fullmatch(r'\d+',record.document_id)
            or type(record.source_page) is not int or not 1<=record.source_page<=1000):
        raise ValueError('Invalid revision/document/page')
    prefix='https://static.cninfo.com.cn/finalpage/'+record.published_date+'/'+record.document_id+'.'
    if record.source_url not in {prefix+'PDF',prefix+'pdf'}:raise ValueError('Document identity/date URL mismatch')
    if not isinstance(record.metrics,dict) or not record.metrics or set(record.metrics)-METRICS:
        raise ValueError('Unsupported or empty financial metric panel')
    for value in record.metrics.values():
        if value is None:continue
        if not isinstance(value,str) or not re.fullmatch(r'-?(?:0|[1-9]\d{0,18})(?:\.\d{1,2})?',value):
            raise ValueError('Financial values require plain CNY decimal strings or explicit null')
        try:amount=Decimal(value)
        except InvalidOperation:raise ValueError('Invalid decimal amount') from None
        if not amount.is_finite() or amount.copy_abs()>Decimal('1e18') or amount.as_tuple().exponent < -2:
            raise ValueError('Non-finite, oversized or non-CNY-cent amount')
    if record.metric_source_pages is not None:
        pages=record.metric_source_pages
        if (not isinstance(pages,dict) or set(pages)!=set(record.metrics)
                or any(type(v) is not int or not 1<=v<=1000 for v in pages.values())
                or record.source_page!=min(pages.values())):
            raise ValueError('Complete per-metric page references and earliest anchor required')
    if record.source_sha256 not in sources or not sources[record.source_sha256].startswith(b'%PDF-'):
        raise ValueError('Retained report PDF missing')
    if record.index_sha256 not in sources:raise ValueError('Retained disclosure index missing')
    index=json.loads(sources[record.index_sha256]);rows=index.get('announcements')
    if not isinstance(rows,list):raise ValueError('Invalid disclosure index')
    matches=[r for r in rows if str(r.get('announcementId'))==record.document_id]
    if len(matches)!=1:raise ValueError('Missing or ambiguous indexed disclosure')
    item=matches[0];epoch=item.get('announcementTime')
    if (item.get('secCode')!=record.symbol or 'cninfo:'+str(item.get('orgId'))!=record.issuer_id
            or 'https://static.cninfo.com.cn/'+str(item.get('adjunctUrl'))!=record.source_url
            or type(epoch) is not int or not 0<=epoch<=int(now.timestamp()*1000)
            or datetime.fromtimestamp(epoch/1000,BJ).date()!=published):
        raise ValueError('Indexed issuer/date/document does not match financial panel')


def validate_manifest(manifest,sources,*,now=None):
    require_current_source()
    now=timestamp(now) if now else datetime.now(timezone.utc)
    if (manifest.get('schema')!=VERSION or manifest.get('coverage_kind')!='reviewed_sample'
            or manifest.get('coverage_verified') is not False or manifest.get('usage')!='internal_research'):
        raise ValueError('Only explicitly incomplete internal reviewed samples supported')
    rows=manifest.get('vintages');files=manifest.get('source_files')
    if not isinstance(rows,list) or not 1<=len(rows)<=1000 or not isinstance(files,dict) or set(sources)!=set(files):
        raise ValueError('Invalid bounded vintage/source scope')
    for sha,body in sources.items():
        if not isinstance(body,bytes) or len(body)>10*1024*1024 or hashlib.sha256(body).hexdigest()!=sha:
            raise ValueError('Retained financial evidence bytes changed')
    if sum(len(b) for b in sources.values())>25*1024*1024:raise ValueError('Financial evidence exceeds 25MiB')
    records=[Vintage(**r) for r in rows];groups={}
    for record in records:
        _validate_record(record,sources,now);groups.setdefault(_group(record),[]).append(record)
    for group in groups.values():
        ordered=sorted(group,key=lambda r:r.revision)
        if [r.revision for r in ordered]!=list(range(1,len(ordered)+1)) or len({r.document_id for r in ordered})!=len(ordered):
            raise ValueError('Incomplete, duplicate or conflicting revision chain')
        for i,record in enumerate(ordered):
            prior=ordered[i-1] if i else None
            if record.supersedes_document_id!=(prior.document_id if prior else None):
                raise ValueError('Missing or cross-panel supersession link')
            if prior and record.published_date<=prior.published_date:
                raise ValueError('Same-day ordering unknown or revision publication reversed')
    return records


def select_panel(manifest,sources,*,issuer_id,symbol,period_end,as_of,mode,required_metrics):
    records=validate_manifest(manifest,sources);clock=timestamp(as_of)
    if clock>datetime.now(timezone.utc):raise ValueError('Future knowledge clock is not an observed snapshot')
    if not re.fullmatch(ISSUER_ID_PATTERN,issuer_id) or not re.fullmatch(r'\d{6}',symbol):
        raise ValueError('Explicit supported issuer identity and symbol required')
    if mode not in {'historical_reconstruction','as_recorded'}:raise ValueError('Explicit knowledge mode required')
    if (not isinstance(required_metrics,list) or not required_metrics or len(set(required_metrics))!=len(required_metrics)
            or set(required_metrics)-METRICS):raise ValueError('Explicit unique supported metrics required')
    date.fromisoformat(period_end)
    candidates=[r for r in records if r.issuer_id==issuer_id and r.symbol==symbol and r.period_end==period_end]
    if len({_group(r) for r in candidates})>1:raise ValueError('Ambiguous report period/scope')
    visible=[r for r in candidates if available_at(r)<=clock
             and (mode=='historical_reconstruction' or max(timestamp(r.observed_at),timestamp(r.recorded_at))<=clock)]
    result={'version':VERSION,'validation_version':VALIDATION_VERSION,'issuer_identity_version':ISSUER_IDENTITY_VERSION,'source_url_version':SOURCE_URL_VERSION,'issuer_id':issuer_id,'symbol':symbol,'period_end':period_end,'as_of':as_of,
            'mode':mode,'availability_policy':BOOK_TIME_POLICY,'manifest_digest':digest(manifest),
            'source_pins':source_pins(),'coverage_verified':False,'historical_pit_feed_certified':False,
            'promotion_allowed':False,'commercial_ready':False,
            'page_reference_version':'per_metric_pages_v1_legacy_anchor_retained',
            'fact_review':'operator_reviewed_sample_not_automatic_PDF_fact_verification'}
    if not visible:return {**result,'status':'unavailable','panel':None,'metrics':None,'missing_metrics':list(required_metrics)}
    chosen=max(visible,key=lambda r:r.revision)
    missing=[m for m in required_metrics if chosen.metrics.get(m) is None]
    # No older-version or other-period fill for a missing revised metric.
    return {**result,'status':'incomplete' if missing else 'available',
            'panel':{**asdict(chosen),'available_at':available_at(chosen).isoformat(),
                     'record_digest':digest(asdict(chosen))},
            'metrics':{m:chosen.metrics.get(m) for m in required_metrics},'missing_metrics':missing,
            'late_collected_historical_reconstruction':mode=='historical_reconstruction' and timestamp(chosen.observed_at)>clock}
