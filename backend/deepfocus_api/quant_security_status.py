"""Retrospective provider day labels, not certified pre-open trading status.

Decode retained SDK protocol independently; absent flags never mean ordinary
tradable stock. No orders, dataset repairs, name-based inference or promotion.
"""
from datetime import date,datetime,time,timezone
from decimal import Decimal,InvalidOperation
import hashlib,json,re
from pathlib import Path

from .quant_corporate_actions import BJ,timestamp
from .quant_research_engine import digest
from .quant_calendar import require_aligned
from .quant_exchange_schedule import validate_manifest as validate_calendar,compare_dates

FIELDS='date,code,close,volume,tradestatus,isST'
VERSION='retained-provider-security-status-v1'
NAMES=('quant_security_status.py','quant_research_engine.py','quant_calendar.py','quant_exchange_schedule.py','quant_corporate_actions.py')


def _pins():return {n:hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest() for n in NAMES}
SOURCE_PINS=_pins()


def require_current_source():
    if _pins()!=SOURCE_PINS:raise ValueError('Loaded security-status source changed')


def decode_query(raw,query):
    if not isinstance(raw,bytes) or len(raw)>4*1024*1024:raise ValueError('Invalid bounded decoded SDK response')
    text=raw.decode('utf-8');header=text[:21].split('\x01');parts=text[21:].split('\x01')
    # This is the inspected 0.9.1 SDK decoded protocol. Header length describes
    # compressed transport, so it is NOT falsely checked as decoded byte length.
    if (len(header)!=3 or header[:2]!=['00.9.10','96'] or not re.fullmatch(r'\d{10}',header[2])
            or len(parts)!=13 or parts[:6]!=['0','success','query_history_k_data_plus','anonymous','1','10000']
            or parts[7:]!=[query['code'],query['fields'],query['start_date'],query['end_date'],query['frequency'],query['adjustflag']]):
        raise ValueError('Unsupported, failed or differently bound SDK response')
    payload=json.loads(parts[6]);rows=payload.get('record')
    if not isinstance(rows,list) or not 1<=len(rows)<=1500:raise ValueError('Empty or oversized status response')
    return rows


def normalize(manifest,sources):
    require_current_source()
    if (manifest.get('schema')!='baostock-historical-security-status-v1'
            or manifest.get('capture_layer')!='SDK_decoded_protocol_not_wire' or manifest.get('errors')!=[]):
        raise ValueError('Incomplete or unsupported provider capture')
    captures=manifest['captures'];results=manifest['results'];requests=manifest['scope']['requests']
    if (not 1<=len(results)<=11 or [r['request'] for r in results]!=requests or len(captures)>16
            or len({c['file'] for c in captures})!=len(captures) or set(sources)!={c['file'] for c in captures}):
        raise ValueError('Missing, duplicate or changed capture/query scope')
    if any(not isinstance(b,bytes) or len(b)>4*1024*1024 for b in sources.values()) or sum(map(len,sources.values()))>25*1024*1024:
        raise ValueError('Status evidence byte budget exceeded')
    now=datetime.now(timezone.utc)
    for c in captures:
        if (hashlib.sha256(sources[c['file']]).hexdigest()!=c['sha256'] or len(sources[c['file']])!=c['bytes']
                or c['capture_layer']!='SDK_decoded_protocol_not_wire' or timestamp(c['observed_at'])>now):
            raise ValueError('Retained SDK response or clock changed')
    normalized={};used=set()
    for result in results:
        q=result['request'];code=q['code'];start,end=map(date.fromisoformat,(q['start_date'],q['end_date']))
        if (not re.fullmatch(r'(sh\.6\d{5}|sz\.[03]\d{5})',code) or code in normalized or start>end
                or q['fields']!=FIELDS or q['frequency']!='d' or q['adjustflag']!='3'
                or result['error_code']!='0' or result['fields']!=FIELDS.split(',')):
            raise ValueError('Invalid explicitly scoped daily unadjusted status query')
        indices=result['capture_indices']
        if len(indices)!=1 or type(indices[0]) is not int or not 0<=indices[0]<len(captures) or indices[0] in used:
            raise ValueError('Unsupported pagination or reused query capture')
        used.update(indices);capture=captures[indices[0]]
        if capture['request']!=q:raise ValueError('Status response belongs to another query')
        rows=decode_query(sources[capture['file']],q)
        if rows!=result['rows']:raise ValueError('Parsed status rows differ from retained protocol')
        observed=timestamp(capture['observed_at']);items=[];previous=''
        for values in rows:
            if len(values)!=6 or any(not isinstance(v,str) for v in values):raise ValueError('Malformed status row')
            day,symbol,close,volume,trade,st=values;local=datetime.combine(date.fromisoformat(day),time(16,30),BJ)
            if not previous<day or not q['start_date']<=day<=q['end_date'] or symbol!=code or observed<local:
                raise ValueError('Duplicate, future, unordered or wrong-security day')
            if trade not in {'0','1'} or st not in {'0','1'}:raise ValueError('Unknown provider state is not ordinary/tradable')
            if not re.fullmatch(r'\d{1,12}(?:\.\d{1,10})?',close):raise ValueError('Invalid plain close')
            try:price=Decimal(close)
            except InvalidOperation:raise ValueError('Invalid status-associated close') from None
            if not price.is_finite() or price<=0 or len(close)>30:raise ValueError('Invalid close')
            if volume and (len(volume)>20 or not volume.isascii() or not volume.isdecimal()):raise ValueError('Invalid share volume')
            if not volume and trade!='0':raise ValueError('Missing normal-session volume')
            items.append({'date':day,'provider_trading_status':'suspended' if trade=='0' else 'normal',
                          'is_st_by_provider':st=='1','close':close,'volume_shares':int(volume) if volume else None,
                          'observed_at':capture['observed_at'],'response_sha256':capture['sha256'],
                          'retrospective_day_complete_after':local.isoformat(),
                          'special_treatment_subtype':'unknown','IPO_relisting_and_exact_bands':'unknown'})
            previous=day
        normalized[code]=items
    return normalized


def lookup(normalized,code,day,*,as_of,mode):
    if mode not in {'historical_reconstruction','as_recorded'}:raise ValueError('Explicit knowledge mode required')
    clock=timestamp(as_of)
    if clock>datetime.now(timezone.utc):raise ValueError('Future status knowledge clock')
    date.fromisoformat(day);rows=[r for r in normalized.get(code,[]) if r['date']==day]
    if len(rows)>1:raise ValueError('Ambiguous day status')
    visible=rows and timestamp(rows[0]['retrospective_day_complete_after'])<=clock
    if mode=='as_recorded' and rows:visible=visible and timestamp(rows[0]['observed_at'])<=clock
    return {'status':'retrospective_label' if visible else 'unknown','record':dict(rows[0]) if visible else None,
            'mode':mode,'preopen_knowledge_verified':False,'order_admission_allowed':False,
            'historical_security_status_certified':False,'promotion_allowed':False}


def review(manifest,sources,experiments,calendar,calendar_sources):
    if (not isinstance(experiments,list) or not experiments
            or len({e['id'] for e in experiments})!=len(experiments)):
        raise ValueError('Nonempty unique sealed experiment scope required')
    normalized=normalize(manifest,sources);validate_calendar(calendar,calendar_sources,as_of=datetime.now(timezone.utc).isoformat())
    expected_scope=[];rows=[];seen={}
    for e in experiments:
        if digest(e['result'])!=e['result_digest'] or digest(e['dataset'])!=e['result']['dataset_digest']:
            raise ValueError('Modified sealed experiment')
        require_aligned(e['dataset'],expected_symbols=list(e['dataset']))
        symbols=e['request']['symbols']
        if not symbols or len(set(symbols))!=len(symbols) or not set(symbols)<=set(e['dataset']):
            raise ValueError('Explicit original stock scope required')
        expected_scope.append({'experiment':e['id'],'dataset_digest':e['result']['dataset_digest'],'result_digest':e['result_digest']})
        for symbol in e['request']['symbols']:
            code=('sh.' if symbol.startswith('6') else 'sz.')+symbol;bars=e['dataset'][symbol]['bars'];days=[b['date'] for b in bars]
            if code in seen and seen[code]!=digest(bars):raise ValueError('Conflicting original stock scope')
            if code in seen:continue
            seen[code]=digest(bars);labels=normalized.get(code,[]);by_day={r['date']:r for r in labels}
            query=next((q for q in manifest['scope']['requests'] if q['code']==code),None)
            if query and (query['start_date']!=days[0] or query['end_date']!=days[-1]):
                raise ValueError('Status query window differs from original frozen stock history')
            gap=compare_dates(calendar,[r['date'] for r in labels],days[0],days[-1])
            missing=sorted(set(days)-set(by_day));extra=sorted(set(by_day)-set(days))
            different=[b['date'] for b in bars if b['date'] in by_day and Decimal(str(b['close']))!=Decimal(by_day[b['date']]['close'])]
            rows.append({'code':code,'expected_original_days':len(days),'provider_days':len(labels),
                         'calendar':gap,'missing_original_days':missing,'extra_days':extra,'close_mismatch_dates':different,
                         'provider_suspended_days':sum(r['provider_trading_status']=='suspended' for r in labels),
                         'provider_st_days':sum(r['is_st_by_provider'] for r in labels)})
    if expected_scope!=manifest['scope']['experiments']:raise ValueError('Capture is bound to other experiments')
    sample=manifest['scope'].get('out_of_pool_status_sample')
    if sample in seen or set(normalized)-set(seen)!={sample}:
        raise ValueError('Extra queries are not the single declared out-of-pool status sample')
    complete=all(not r['missing_original_days'] and not r['extra_days'] and r['calendar']['status']=='matches_plan' for r in rows)
    require_current_source()
    return {'version':VERSION,'capture_digest':digest(manifest),'calendar_digest':digest(calendar),'source_pins':dict(SOURCE_PINS),
            'stocks':rows,'original_stock_days':sum(r['expected_original_days'] for r in rows),
            'coverage_matches_original_and_plan':complete,'prices_match_original':all(not r['close_mismatch_dates'] for r in rows),
            'historical_security_status_certified':False,'preopen_knowledge_verified':False,'data_rights_verified':False,
            'promotion_allowed':False,'commercial_ready':False,
            'limitations':['post-hoc vendor day labels, not original preopen capture',
                'ST/*ST subtype, identity lifecycle, IPO/relisting and exact price bands not certified',
                'six surviving stocks are not a survivorship-free historical universe',
                'matching annual plan is not actual-exchange-session certification']}
