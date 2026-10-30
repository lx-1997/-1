"""Evidence-bound index references, separate from sealed strategy gates.

Never synthesize opening levels, interpolate dates or describe CSI's fixed-tax
NTR as a personal FIFO tax benchmark. These indices are not executable stocks.
"""
from datetime import datetime,timedelta,timezone
from decimal import Decimal,InvalidOperation
import hashlib,json,re
from urllib.parse import urlsplit,parse_qs

SCHEMA='csi-benchmark-reference-v1'
BJ=timezone(timedelta(hours=8))
VARIANTS={
 '000300':{'name':'沪深300指数','basis':'price','dividend_tax_assumption':None},
 'H00300':{'name':'沪深300全收益指数','basis':'gross_total_return','dividend_tax_assumption':'0%'},
 'N00300':{'name':'沪深300净收益指数','basis':'net_total_return','dividend_tax_assumption':'10%'},
}


def stamp(value):
    d=datetime.fromisoformat(value.replace('Z','+00:00'))
    if d.tzinfo is None:raise ValueError('Capture timestamp needs a timezone')
    return d


def positive(value):
    if isinstance(value,bool) or value is None:raise ValueError('Missing/nonpositive index or NAV level')
    try:d=Decimal(str(value))
    except InvalidOperation as exc:raise ValueError('Invalid index or NAV level') from exc
    if not d.is_finite() or not Decimal(0)<d<Decimal('1e15'):raise ValueError('Invalid index or NAV level')
    return float(d)


def parse_capture(capture,raw,*,as_of):
    code=capture['index_code']
    if code not in VARIANTS:raise ValueError('Unknown benchmark variant')
    if not isinstance(raw,bytes) or not 0<len(raw)<=4*1024*1024 or hashlib.sha256(raw).hexdigest()!=capture['sha256']:
        raise ValueError('Benchmark raw content missing or modified')
    observed=stamp(capture['observed_at']);clock=stamp(as_of)
    if observed>clock+timedelta(seconds=5):raise ValueError('Future benchmark observation')
    url=urlsplit(capture['url']);query=parse_qs(url.query)
    if (url.scheme!='https' or url.netloc!='www.csindex.com.cn' or url.path!='/csindex-home/perf/index-perf' or url.fragment or
        set(query)!={'indexCode','startDate','endDate'} or query['indexCode']!=[code] or
        any(len(query[k])!=1 or not re.fullmatch(r'\d{8}',query[k][0]) for k in ('startDate','endDate'))):
        raise ValueError('Benchmark request identity mismatch')
    start=datetime.strptime(query['startDate'][0],'%Y%m%d').date();end=datetime.strptime(query['endDate'][0],'%Y%m%d').date()
    if start>end or end>observed.astimezone(BJ).date():raise ValueError('Invalid benchmark request interval')
    payload=json.loads(raw,parse_float=str)
    if payload.get('code')!='200' or not isinstance(payload.get('data'),list) or not 1<=len(payload['data'])<=10000:
        raise ValueError('Benchmark provider failure or invalid rows')
    rows=[];previous=''
    for row in payload['data']:
        if row.get('indexCode')!=code or row.get('indexNameCnAll')!=VARIANTS[code]['name']:raise ValueError('Benchmark response identity mismatch')
        text=row.get('tradeDate','')
        if not isinstance(text,str) or not re.fullmatch(r'\d{8}',text):raise ValueError('Invalid benchmark date')
        day=datetime.strptime(text,'%Y%m%d').date();d=day.isoformat()
        if not start<=day<=end or d<=previous:raise ValueError('Duplicate, unordered or out-of-range benchmark date')
        if day==observed.astimezone(BJ).date() and observed.astimezone(BJ).time().isoformat()<'16:30:00':
            raise ValueError('Current-session benchmark is not a completed close')
        close=positive(row.get('close'));ohlc=[row.get(k) for k in ('open','high','low')]
        if all(v is None for v in ohlc):op=high=low=None
        elif any(v is None for v in ohlc):raise ValueError('Partial benchmark OHLC cannot be completed by inference')
        else:
            op,high,low=map(positive,ohlc)
            if high<max(op,close) or low>min(op,close) or high<low:raise ValueError('Invalid benchmark OHLC')
        rows.append({'date':d,'close':close,'open':op,'high':high,'low':low});previous=d
    return {'schema':SCHEMA,'code':code,**VARIANTS[code],'observed_at':capture['observed_at'],
            'source_sha256':capture['sha256'],'source_url':capture['url'],'currency':'CNY','rows':rows,
            'all_ohlc_present':all(r['open'] is not None for r in rows),'tradable':False,
            'historical_point_in_time_verified':False,'rights_verified':False}


def align(series,calendar):
    if set(series)!=set(VARIANTS):raise ValueError('Require price, gross and net variants together')
    if not calendar or calendar!=sorted(set(calendar)):raise ValueError('Declared calendar must be unique and ordered')
    required=set(calendar);sets={code:{r['date'] for r in data['rows']} for code,data in series.items()}
    for code,dates in sets.items():
        if required-dates:raise ValueError('Benchmark missing declared sessions: '+code)
    if len({tuple(sorted(v)) for v in sets.values()})!=1:raise ValueError('Benchmark variants have different calendars')
    return {'declared_sessions':len(calendar),'fetched_sessions':len(next(iter(sets.values()))),
            'extra_fetched_sessions':sorted(next(iter(sets.values()))-required),'no_interpolation':True}


def reference_comparison(curve,initial,series,calendar,*,starts_flat):
    coverage=align(series,calendar)
    if starts_flat is not True:raise ValueError('Prior-close cash anchor requires confirmed flat initial account')
    if not curve:raise ValueError('No strategy curve')
    days=[r['date'] for r in curve]
    if days!=sorted(set(days)) or any(d not in calendar for d in days):raise ValueError('Invalid strategy dates')
    first=calendar.index(days[0]);last=calendar.index(days[-1])
    if first==0 or days!=calendar[first:last+1]:raise ValueError('Missing prior anchor or skipped strategy sessions')
    navs=[positive(r['nav']) for r in curve];capital=positive(initial);prior=calendar[first-1]
    strategy=(navs[-1]/capital-1)*100;observed=(navs[-1]/navs[0]-1)*100;results={}
    for code,s in series.items():
        values={r['date']:r['close'] for r in s['rows']}
        total=(values[days[-1]]/values[prior]-1)*100;close=(values[days[-1]]/values[days[0]]-1)*100
        results[code]={'name':s['name'],'basis':s['basis'],'dividend_tax_assumption':s['dividend_tax_assumption'],
             'prior_close_to_final_close_pct':total,'strategy_minus_reference_pp':strategy-total,
             'observed_close_to_close_pct':close,'strategy_observed_minus_reference_pp':observed-close,
             'source_sha256':s['source_sha256'],'investor_tax_matched':False,'tradable':False}
    return {'coverage':coverage,'period':{'prior_close':prior,'first_session':days[0],'last_session':days[-1],
                    'strategy_prior_anchor':'declared_initial_cash_no_holdings','first_observed_close':days[0]},
            'strategy_original_start_capital_return_pct':strategy,'strategy_observed_close_return_pct':observed,
            'observed_close_comparison_excludes_first_session_pnl':True,'references':results,
            'original_gate_modified':False,'promotion_allowed':False,'oos_validation':False,
            'limitations':['reference_only_not_executable_or_net_alpha','index_has_no_execution_fees_or_matching_cash_exposure',
                          'N00300_fixed_10pct_dividend_tax_is_not_personal_FIFO_tax','current_retrieval_not_historical_point_in_time_data',
                          'missing_open_not_synthesized']}
