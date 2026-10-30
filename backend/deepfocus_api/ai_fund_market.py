"""Versioned daily-data contract for the six paper portfolios, never a broker API."""
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal,InvalidOperation
import copy
import hashlib
import json
import re
from urllib.parse import urlsplit,parse_qs

VERSION='paper-daily-market-v1'
BJ=timezone(timedelta(hours=8))
MAX_BODY=1024*1024
TTL=6*3600


def _hash(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def _number(value):
    try:
        n=Decimal(str(value))
        if n.is_finite() and n>=0:return n
    except (ValueError,InvalidOperation):pass
    raise ValueError('Invalid daily price/volume')


def parse(body,symbol,provider,url,*,fetched_at=None,adjustment='none'):
    if not isinstance(body,bytes) or not 0<len(body)<=MAX_BODY:raise ValueError('Invalid daily response size')
    if not re.fullmatch('[036][0-9]{5}',symbol) or symbol in {'000300','000905','000852'} or provider not in ('sina','eastmoney'):raise ValueError('Unsupported daily source identity')
    if adjustment!='none':raise ValueError('Adjusted history cannot enter the raw-price execution contract')
    endpoint=urlsplit(url);query=parse_qs(endpoint.query)
    if provider=='eastmoney':
        secid=('1.' if symbol.startswith('6') else '0.')+symbol
        if endpoint.hostname!='push2his.eastmoney.com' or query.get('fqt')!=['0'] or query.get('klt')!=['101'] or query.get('secid')!=[secid]:
            raise ValueError('Daily source request does not bind raw prices to this stock')
    elif endpoint.hostname not in {'quotes.sina.cn','money.finance.sina.com.cn'} or query.get('scale')!=['240'] or query.get('symbol')!=[('sh' if symbol.startswith('6') else 'sz')+symbol]:
        raise ValueError('Sina request stock identity mismatch')
    stamp=fetched_at or datetime.now(timezone.utc)
    if stamp.tzinfo is None:raise ValueError('Daily observation clock must be timezone-aware')
    payload=json.loads(body);rows=[]
    if provider=='sina':
        if not isinstance(payload,list):raise ValueError('Invalid Sina history')
        for p in payload:
            rows.append((p['day'],p['open'],p['high'],p['low'],p['close'],p['volume'],None))
    else:
        data=payload.get('data') or {}
        if data.get('code')!=symbol:raise ValueError('Daily response stock identity mismatch')
        for row in data.get('klines') or []:
            p=row.split(',')
            if len(p)<7:raise ValueError('Eastmoney raw history requires reported volume and amount')
            rows.append((p[0],p[1],p[3],p[4],p[2],p[5],p[6]))
    if not rows or len(rows)>1024:raise ValueError('Daily source history size invalid')
    bars={};precision=100 if provider=='eastmoney' else 1
    for d,o,h,l,c,v,amount in rows:
        d=str(d)[:10];day=date.fromisoformat(d)
        if day>stamp.astimezone(BJ).date() or d in bars:raise ValueError('Future or duplicate daily candle')
        o,h,l,c,v=map(_number,(o,h,l,c,v))
        if min(o,h,l,c)<=0 or not l<=min(o,c)<=max(o,c)<=h:raise ValueError('Invalid daily OHLC range')
        raw_v=v;v=v*100 if provider=='eastmoney' else v
        if provider=='sina' and v!=v.to_integral_value():raise ValueError('Fractional reported share volume')
        amount=_number(amount) if amount is not None else None
        if amount is not None and ((v>0 and amount==0) or (v==0 and amount>0)):
            raise ValueError('Contradictory daily amount and volume')
        # Rounded lot volume has up to one lot of uncertainty. Do not invent exact odd shares.
        lower=max(Decimal(0),v-(100 if provider=='eastmoney' else 0))
        if amount and v>0:
            average=amount/v
            if average<l*Decimal('.98') or average>h*Decimal('1.02'):
                raise ValueError('Daily amount/volume is inconsistent with raw prices')
        bars[d]={'d':d,'o':float(o),'h':float(h),'l':float(l),'c':float(c),'v':float(v),
                 'source_v':str(raw_v),'v_lower_bound':float(lower),'a':float(amount) if amount is not None else None,
                 'volume_unit':'shares','volume_precision_shares':precision,'price_basis':'unadjusted',
                 'amount_basis':'provider_reported' if amount is not None else 'close_times_volume_proxy'}
    out=[bars[d] for d in sorted(bars)]
    meta={'contract':VERSION,'symbol':symbol,'provider':provider,'price_basis':'unadjusted','volume_unit':'shares',
          'provider_volume_unit':'rounded_lot_100_shares' if provider=='eastmoney' else 'shares',
          'volume_precision_shares':precision,'fetched_at':stamp.isoformat(),'request_url':url,
          'response_sha256':hashlib.sha256(body).hexdigest(),'bars_sha256':_hash(out),
          'identity_check':'response_code' if provider=='eastmoney' else 'request_bound_no_response_symbol',
          'commercial_license_verified':False,'corporate_actions_complete':False}
    return {'bars':out,'meta':meta,'raw_response':body.decode('utf-8')}


def fresh(snapshot,now=None):
    now=now or datetime.now(timezone.utc)
    try:
        stamp=datetime.fromisoformat(snapshot['meta']['fetched_at'])
        return stamp.tzinfo is not None and -5<=(now-stamp).total_seconds()<=TTL and stamp.astimezone(BJ).date()==now.astimezone(BJ).date()
    except (KeyError,TypeError,ValueError):return False


def fields(snapshot):
    if not snapshot:return {'closes':[],'ohlc':[],'market_meta':{'status':'unavailable'}}
    return {'closes':[b['c'] for b in snapshot['bars']],'ohlc':copy.deepcopy(snapshot['bars']),
            'market_meta':copy.deepcopy(snapshot['meta']),'market_raw':snapshot['raw_response']}


def completed_bar(md,symbol,*,now=None,require_fresh=True):
    now=now or datetime.now(timezone.utc)
    try:
        meta=md['market_meta'];bars=md['ohlc'];raw=md['market_raw']
        if meta['contract']!=VERSION or meta['symbol']!=symbol or meta['price_basis']!='unadjusted' or meta['volume_unit']!='shares':return None
        if _hash(bars)!=meta['bars_sha256'] or hashlib.sha256(raw.encode()).hexdigest()!=meta['response_sha256']:return None
        if require_fresh and not fresh({'meta':meta},now):return None
        valid=[b for b in bars if b['d']<now.astimezone(BJ).date().isoformat()]
        if not valid:return None
        bar=valid[-1]
        if (now.astimezone(BJ).date()-date.fromisoformat(bar['d'])).days>14 or bar['v_lower_bound']<=0:return None
        return bar
    except (KeyError,TypeError,ValueError):return None


def turnover(bar):
    if not bar or bar.get('volume_unit')!='shares' or bar.get('price_basis')!='unadjusted':return None
    try:
        amount=_number(bar['a']) if bar.get('a') is not None else _number(bar['c'])*_number(bar['v_lower_bound'])
        return float(amount) if amount>0 else None
    except (KeyError,ValueError):return None
