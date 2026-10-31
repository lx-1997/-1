"""Independent retained-protocol checks for one declared action price sample.

These are retrospective provider observations, never exchange-certified prices,
pre-open state, a complete trading calendar or unseen strategy validation data.
"""
from datetime import datetime,timezone
from decimal import Decimal,Context,localcontext
import hashlib
import re
from .quant_security_status import decode_query
from .quant_share_entitlements import stamp

FIELDS='date,code,open,high,low,close,preclose,volume,amount,adjustflag,tradestatus,pctChg,isST'
QUERY={'code':'sz.300750','fields':FIELDS,'start_date':'2023-04-24','end_date':'2023-04-28','frequency':'d','adjustflag':'3'}
DAYS=['2023-04-24','2023-04-25','2023-04-26','2023-04-27','2023-04-28']


def _decimal(value,*,positive=True,tick=False):
    if not isinstance(value,str) or not re.fullmatch(r'[0-9]{1,12}(?:\.[0-9]{1,10})?',value):raise ValueError('Invalid plain bounded market decimal')
    n=Decimal(value)
    if n<0 or positive and n==0 or n>Decimal('1000000000000'):raise ValueError('Invalid market value')
    if tick and n.quantize(Decimal('.01'))!=n:raise ValueError('Raw price is not a cent tick')
    return n


def normalize(manifest,sources):
    with localcontext(Context(prec=50)):
        return _normalize(manifest,sources)


def _normalize(m,sources):
    if not isinstance(m,dict) or m.get('schema')!='capitalization-raw-price-capture-v1' or m.get('query')!=QUERY or m.get('errors')!=[] or m.get('sdk_version')!='0.9.1' or m.get('capture_layer')!='SDK_decoded_protocol_not_wire':
        raise ValueError('Unsuccessful or differently scoped raw-price capture')
    captures=m.get('captures')
    if not isinstance(captures,list) or len(captures)!=3 or any(not isinstance(c,dict) for c in captures) or [c.get('phase') for c in captures]!=['login','query','logout'] or type(m.get('sdk_requests')) is not int or m['sdk_requests']!=3:
        raise ValueError('Unexpected request/capture inventory')
    names=[c.get('file') for c in captures]
    if names!=['sdk-response-0.bin','sdk-response-1.bin','sdk-response-2.bin'] or not isinstance(sources,dict) or set(sources)!=set(names):raise ValueError('Ambiguous raw response inventory')
    for c in captures:
        raw=sources[c['file']]
        if not isinstance(raw,bytes) or len(raw)>4*1024**2 or len(raw)!=c['bytes'] or hashlib.sha256(raw).hexdigest()!=c['sha256']:
            raise ValueError('Retained response bytes changed')
        if stamp(c['observed_at'])>datetime.now(timezone.utc):raise ValueError('Future provider observation')
    if any(stamp(captures[i]['observed_at'])>stamp(captures[i+1]['observed_at']) for i in range(2)):raise ValueError('Provider capture clock order changed')
    capture=captures[1]
    if stamp(capture['observed_at'])<stamp('2023-04-28T16:30:00+08:00'):raise ValueError('Prices observed before sample completion')
    rows=decode_query(sources[capture['file']],QUERY)
    if rows!=m.get('rows') or m.get('fields')!=FIELDS.split(',') or len(rows)!=5:raise ValueError('SDK rows differ from bound decoded protocol')
    result=[]
    for expected,row in zip(DAYS,rows):
        if not isinstance(row,list) or len(row)!=13 or row[:2]!=[expected,QUERY['code']] or any(not isinstance(v,str) for v in row):raise ValueError('Missing, reordered or wrong-security raw-price row')
        if row[9:11]!=['3','1'] or row[12]!='0':raise ValueError('Sample requires unadjusted provider-normal non-ST observations')
        prices={k:_decimal(row[i],tick=True) for i,k in [(2,'open'),(3,'high'),(4,'low'),(5,'close'),(6,'provider_reference')]}
        if not prices['low']<=min(prices['open'],prices['close'])<=max(prices['open'],prices['close'])<=prices['high']:raise ValueError('Raw OHLC range inconsistent')
        if not re.fullmatch(r'[0-9]{1,13}',row[7]) or int(row[7])<=0:raise ValueError('Missing positive share volume')
        amount=_decimal(row[8]);pct=row[11]
        if not re.fullmatch(r'-?[0-9]{1,6}(?:\.[0-9]{1,10})?',pct):raise ValueError('Invalid provider percent')
        # Check the provider's own numeric convention, not an exchange rule.
        implied=(prices['close']/prices['provider_reference']-1)*100
        if abs(implied-Decimal(pct))>Decimal('.0002'):raise ValueError('Provider percent and reference disagree')
        if result and expected!='2023-04-26' and prices['provider_reference']!=Decimal(result[-1]['close']):
            raise ValueError('Non-action-day reference differs from previous retained close')
        result.append({'date':expected,**{k:str(v) for k,v in prices.items()},'volume_shares':int(row[7]),'amount_CNY':str(amount),
                       'provider_pct_change':pct,'observed_at':capture['observed_at'],'response_sha256':capture['sha256']})
    return {'schema':'reviewed-capitalization-raw-price-sample-v1','rows':result,'price_adjustment':'none',
            'reference_semantics':'provider_percent_reference_not_assumed_previous_raw_close',
            'retrospective_only':True,'exchange_certified':False,'preopen_state_verified':False,
            'calendar_certified':False,'data_rights_verified':False,'promotion_allowed':False}
