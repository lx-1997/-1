"""Provider historical snapshots joined to dated lifecycle metadata.

Retrospective, scoped Shanghai/Shenzhen A-code/type-1 research membership;
never certified PIT identity, preopen status, tradability, or a strategy signal.
"""
from datetime import date,datetime,time,timezone
import gzip,hashlib,io,json,re,zlib
from pathlib import Path

from .quant_corporate_actions import BJ,timestamp
from .quant_research_engine import digest
from .quant_calendar import require_aligned
from .quant_exchange_schedule import validate_manifest as validate_calendar,planned_sessions

VERSION='historical-provider-universe-v1'
SCOPE_RULES={'purpose':'provider_historical_universe_data_quality_not_strategy',
             'market_scope':'provider_type_1_and_SH6_or_SZ0_3_codes_no_Beijing_or_B_shares',
             'lifecycle_rule':'ipoDate_inclusive_outDate_exclusive_current_status_never_historical_filter',
             'selection_or_return_experiment':False,'promotion_allowed':False}
FIELDS={'query_all_stock':['code','tradeStatus','code_name'],
        'query_stock_basic':['code','code_name','ipoDate','outDate','type','status']}
NAMES=('quant_historical_universe.py','quant_corporate_actions.py','quant_research_engine.py','quant_calendar.py','quant_exchange_schedule.py')


def _pins():return {n:hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest() for n in NAMES}
SOURCE_PINS=_pins()


def require_current_source():
    if _pins()!=SOURCE_PINS:raise ValueError('Loaded universe source changed')


def decode_response(raw,query):
    """Inspected uncompressed metadata envelopes; CRC covers UTF-8 content.

    The header body length counts Unicode characters, not UTF-8 byte length.
    CRC detects accidental corruption; it is not publisher authentication.
    """
    if not isinstance(raw,bytes) or len(raw)>4*1024*1024:raise ValueError('Invalid metadata response budget')
    text=raw.decode('utf-8');ending='<![CDATA[]]>\n'
    if not text.endswith(ending):raise ValueError('Truncated metadata response')
    content,checksum=text[:-len(ending)].rsplit('\x01',1)
    if not checksum.isascii() or not checksum.isdecimal() or zlib.crc32(content.encode())!=int(checksum):
        raise ValueError('Metadata response checksum mismatch')
    header=content[:21].split('\x01');body=content[21:];parts=body.split('\x01');method=query['method']
    expected_type={'query_all_stock':'36','query_stock_basic':'46'}.get(method)
    if (not expected_type or len(header)!=3 or header[:2]!=['00.9.10',expected_type]
            or not re.fullmatch(r'\d{10}',header[2]) or int(header[2])!=len(body)
            or parts[:6]!=['0','success',method,'anonymous','1','10000']):
        raise ValueError('Unsupported, failed or paginated metadata response')
    suffix=[query['day']] if method=='query_all_stock' else [query.get('code',''),'']
    if parts[7:-1]!=suffix or [s.strip() for s in parts[-1].split(',')]!=FIELDS[method]:
        raise ValueError('Metadata response belongs to another query')
    rows=json.loads(parts[6]).get('record')
    if not isinstance(rows,list) or not 1<=len(rows)<10000:raise ValueError('Empty or unsupported metadata page')
    width=len(FIELDS[method])
    if any(not isinstance(r,list) or len(r)!=width or any(not isinstance(v,str) or len(v)>256 for v in r) for r in rows):
        raise ValueError('Invalid metadata rows')
    # The inspected SDK strips JSON whitespace even inside quoted names. Keep
    # the raw rows authoritative; only validate this known lossy SDK projection.
    sdk_rows=json.loads(''.join(parts[6].split()))['record']
    return {'fields':FIELDS[method],'rows':rows,'sdk_rows':sdk_rows,
            'sdk_whitespace_changed_rows':sum(a!=b for a,b in zip(rows,sdk_rows))}


def read_frame(root,record):
    root=Path(root).resolve();path=(root/record['file']).resolve()
    if path.parent!=root or not path.is_file() or path.stat().st_size>4*1024*1024:
        raise ValueError('Missing or out-of-scope universe source')
    packed=path.read_bytes()
    if hashlib.sha256(packed).hexdigest()!=record['gzip_sha256']:raise ValueError('Universe archive changed')
    with gzip.GzipFile(fileobj=io.BytesIO(packed)) as f:raw=f.read(4*1024*1024+1)
    if len(raw)>4*1024*1024 or len(raw)!=record['bytes'] or hashlib.sha256(raw).hexdigest()!=record['sha256']:
        raise ValueError('Universe decoded source changed or exceeds budget')
    return raw


def collection_scope(experiments,calendar,calendar_sources):
    require_current_source()
    if not experiments or len({e['id'] for e in experiments})!=len(experiments):raise ValueError('Nonempty unique experiments required')
    validate_calendar(calendar,calendar_sources,as_of=datetime.now(timezone.utc).isoformat())
    intervals=set();bindings=[]
    for e in experiments:
        if digest(e['result'])!=e['result_digest'] or digest(e['dataset'])!=e['result']['dataset_digest']:raise ValueError('Modified sealed experiment')
        require_aligned(e['dataset'],expected_symbols=list(e['dataset']))
        # require_aligned returns its quality frame; preserve original bars as
        # the explicit range, then compare against independent annual notices.
        observed=[b['date'] for b in next(iter(e['dataset'].values()))['bars']]
        intervals.add((observed[0],observed[-1]))
        if observed!=planned_sessions(calendar,observed[0],observed[-1]):raise ValueError('Original history differs from independent annual plan')
        bindings.append({'id':e['id'],'request_digest':digest(e['request']),'dataset_digest':e['result']['dataset_digest'],'result_digest':e['result_digest']})
    if len(intervals)!=1:raise ValueError('One explicit shared frozen interval required')
    start,end=next(iter(intervals));days=planned_sessions(calendar,start,end)
    if not 1<=len(days)<=800:raise ValueError('Capture supports one to eight hundred planned sessions')
    return {**SCOPE_RULES,'experiments':bindings,
            'start':start,'end':end,'days':days,'days_digest':digest(days),'calendar_digest':digest(calendar),
            }


def parse_basic(rows):
    result={}
    for code,name,ipo,out,kind,current in rows:
        if code in result or not re.fullmatch(r'(sh|sz)\.\d{6}',code):raise ValueError('Duplicate or unsupported lifecycle identity')
        if kind not in {'1','2','4','5'} or current not in {'0','1'}:raise ValueError('Unknown provider instrument type/state')
        if ipo:date.fromisoformat(ipo)
        if out:date.fromisoformat(out)
        if ipo and out and not ipo<out:raise ValueError('Invalid lifecycle interval')
        if kind=='1' and a_code(code) and (not ipo or current=='0' and not out):
            raise ValueError('Incomplete in-scope lifecycle cannot define historical membership')
        result[code]={'code':code,'observed_name':name,'ipo_date':ipo or None,'out_date':out or None,
                      'provider_type':kind,'current_status':current}
    return result


def a_code(code):return bool(re.fullmatch(r'(sh\.6\d{5}|sz\.[03]\d{5})',code))


def membership(day,rows,basic):
    date.fromisoformat(day);observed={};members=[];unknown=[];outside=[];expired=[];expired_active=[];preipo=[]
    for code,trade,name in rows:
        if code in observed or not re.fullmatch(r'(sh|sz)\.\d{6}',code) or trade not in {'0','1'}:
            raise ValueError('Duplicate or unknown snapshot state')
        observed[code]=(trade,name);life=basic.get(code)
        if not a_code(code):outside.append(code);continue
        if life is None:unknown.append(code);continue
        if life['provider_type']!='1':outside.append(code);continue
        if not life['ipo_date'] or life['current_status']=='0' and not life['out_date']:
            unknown.append(code);continue
        if day<life['ipo_date']:preipo.append(code);continue
        if life['out_date'] and day>=life['out_date']:
            expired.append(code)
            if trade=='1':expired_active.append(code)
            continue
        members.append({'code':code,'provider_trading_status':'normal' if trade=='1' else 'suspended',
                        'snapshot_name':name,'ipo_date':life['ipo_date'],'out_date':life['out_date'],
                        'catalogue_status_observed_now':life['current_status']})
    expected={c for c,v in basic.items() if a_code(c) and v['provider_type']=='1' and v['ipo_date']
              and v['ipo_date']<=day and (not v['out_date'] or day<v['out_date'])
              and not (v['current_status']=='0' and not v['out_date'])}
    missing=sorted(expected-set(observed))
    return {'date':day,'members':sorted(members,key=lambda r:r['code']),
            'unknown_in_scope_codes':sorted(unknown),'missing_lifecycle_expected_codes':missing,
            'pre_ipo_snapshot_conflicts':sorted(preipo),'expired_snapshot_codes_excluded':sorted(expired),
            'expired_but_trading_snapshot_conflicts':sorted(expired_active),
            'outside_declared_scope_count':len(outside),'snapshot_securities_count':len(rows),
            'coverage_consistent':not unknown and not missing and not preipo and not expired_active,
            'preopen_knowledge_verified':False,'historical_pit_universe_certified':False,'order_admission_allowed':False}


def verify_entry(root,entry):
    if entry['error_code']!='0' or len(entry['frames'])!=1:raise ValueError('Incomplete or unsupported paginated universe query')
    frame=entry['frames'][0];decoded=decode_response(read_frame(root,frame),entry['query'])
    if (entry['fields']!=decoded['fields'] or entry['rows']!=len(decoded['rows'])
            or entry['raw_rows_digest']!=digest(decoded['rows']) or entry['sdk_rows_digest']!=digest(decoded['sdk_rows'])
            or entry['sdk_whitespace_changed_rows']!=decoded['sdk_whitespace_changed_rows']):
        raise ValueError('Universe SDK/raw row binding changed')
    timestamp(frame['observed_at'])
    if timestamp(frame['observed_at'])>datetime.now(timezone.utc):raise ValueError('Future universe capture clock')
    if frame['query']!=entry['query']:raise ValueError('Universe capture belongs to another query')
    if entry['query']['method']=='query_all_stock':
        completed=datetime.combine(date.fromisoformat(entry['query']['day']),time(16,30),BJ)
        if timestamp(frame['observed_at'])<completed:raise ValueError('Incomplete daily universe capture')
    return decoded['rows']


def review(root,manifest,scope):
    require_current_source()
    days=scope.get('days')
    if (not isinstance(days,list) or not 1<=len(days)<=800 or days!=sorted(set(days))
            or scope.get('days_digest')!=digest(days) or scope.get('start')!=days[0] or scope.get('end')!=days[-1]
            or any(scope.get(k)!=v for k,v in SCOPE_RULES.items())):
        raise ValueError('Explicit complete nonempty universe scope required')
    if manifest.get('schema')!=VERSION or manifest.get('scope')!=scope or manifest.get('errors')!=[]:
        raise ValueError('Incomplete or differently scoped universe capture')
    entries=manifest['results'];expected_queries=[{'method':'query_stock_basic','code':''}]+[
        {'method':'query_all_stock','day':d} for d in scope['days']]+[{'method':'query_stock_basic','code':''}]
    if [e['query'] for e in entries]!=expected_queries:raise ValueError('Missing, reordered or extra universe query')
    files=[f['file'] for e in entries for f in e['frames']]
    if len(set(files))!=len(files):raise ValueError('Reused universe response file')
    before=verify_entry(root,entries[0]);after=verify_entry(root,entries[-1])
    if before!=after:raise ValueError('Provider lifecycle catalog changed during capture')
    basic=parse_basic(before);panels=[];all_members=set();suspended=0;whitespace=0
    for entry in entries[1:-1]:
        rows=verify_entry(root,entry);panel=membership(entry['query']['day'],rows,basic)
        members=panel.pop('members');panel.update(member_count=len(members),member_codes_digest=digest([r['code'] for r in members]),
                                                suspended_member_count=sum(r['provider_trading_status']=='suspended' for r in members))
        all_members.update(r['code'] for r in members);suspended+=panel['suspended_member_count'];whitespace+=entry['sdk_whitespace_changed_rows'];panels.append(panel)
    require_current_source()
    return {'version':VERSION,'scope':scope,'manifest_digest':digest(manifest),'source_pins':dict(SOURCE_PINS),
            'lifecycle_rows':len(before),'days':len(panels),'unique_member_codes':len(all_members),
            'member_day_rows':sum(p['member_count'] for p in panels),'suspended_member_day_rows':suspended,
            'snapshot_name_whitespace_differences_preserved':whitespace,'panels':panels,
            'all_daily_joins_consistent':all(p['coverage_consistent'] for p in panels),
            'historical_pit_universe_certified':False,'preopen_knowledge_verified':False,'data_rights_verified':False,
            'promotion_allowed':False,'commercial_ready':False,
            'limitations':['retrospective vendor lifecycle/catalog, not captured original preopen identity',
                'code reuse/relisting, missing historical instruments and data rights require separate evidence',
                'Shanghai/Shenzhen A-code/provider-type scope; not all securities or Beijing stocks',
                'membership includes suspended shares; membership never implies an executable order',
                'no strategy selection, returns, alpha or old OOS/holdout changes']}
