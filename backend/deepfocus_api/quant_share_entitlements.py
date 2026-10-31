"""Evidence-bound account-level capitalization share inventory.

No cash, tax basis, strategy orders, supplier, database or broker operations.
This independent development component retains unresolved fractional rights;
it never rounds each FIFO lot or infers that restricted shares are sellable.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, date, time, timezone, timedelta
from decimal import Decimal, Context, localcontext, ROUND_FLOOR, ROUND_CEILING
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit
import copy
import hashlib
import json
import re

VERSION='capitalization-share-inventory-v1'
SOURCE_SHA=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
BJ=timezone(timedelta(hours=8))
MAX_STATE=16*1024**2


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def stamp(value):
    if not isinstance(value,str):raise ValueError('Explicit zoned timestamp required')
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.tzinfo is None:raise ValueError('Timestamp has no timezone')
    return result


def day(value):
    if not isinstance(value,str) or date.fromisoformat(value).isoformat()!=value:raise ValueError('Invalid calendar date')
    return value


def identifier(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',value):raise ValueError('Invalid scoped identity')
    return value


def quantity(value,*,zero=True):
    if type(value) is not int or not (0 if zero else 1)<=value<=10**12:raise ValueError('Invalid bounded whole-share quantity')
    return value


def pdf_source(sha,sources):
    if not isinstance(sources,dict):raise ValueError('Explicit source map required')
    if not isinstance(sha,str) or not re.fullmatch(r'[0-9a-f]{64}',sha):raise ValueError('Missing original SHA256')
    raw=sources.get(sha)
    if not isinstance(raw,bytes) or not raw.startswith(b'%PDF-') or len(raw)>10*1024**2 or hashlib.sha256(raw).hexdigest()!=sha:
        raise ValueError('Original evidence missing/changed/oversized')


def exact_context(function):
    @wraps(function)
    def wrapped(*args,**kwargs):
        # Every admissible integer/decimal product fits in 50 digits. A caller's
        # thread-local precision, rounding or exponent limits must not alter it.
        with localcontext(Context(prec=50)):
            return function(*args,**kwargs)
    return wrapped


@dataclass(frozen=True)
class Capitalization:
    event_id: str
    symbol: str
    kind: str
    record_date: str
    ex_date: str
    share_credit_date: str
    unrestricted_trade_date: str  # None means unknown, NOT immediate trading.
    new_shares_per_old_share: str
    available_at: str
    observed_at: str
    source_url: str
    source_sha256: str
    fractional_policy: str='account_remainder_rank_random_ties'

    def __post_init__(self):
        identifier(self.event_id)
        if not isinstance(self.symbol,str) or not re.fullmatch(r'[036][0-9]{5}',self.symbol):raise ValueError('Invalid scoped equity')
        if self.kind not in ('capital_reserve','stock_bonus'):raise ValueError('Split/rights/merger need separate validated semantics')
        if not day(self.record_date)<day(self.ex_date)<=day(self.share_credit_date):raise ValueError('Invalid registration/ex/credit dates')
        if self.unrestricted_trade_date is not None and day(self.unrestricted_trade_date)<self.share_credit_date:
            raise ValueError('Trading cannot precede share credit')
        if (not isinstance(self.new_shares_per_old_share,str)
                or not re.fullmatch(r'[0-9]+(?:\.[0-9]{1,10})?',self.new_shares_per_old_share)
                or not 0<Decimal(self.new_shares_per_old_share)<=10):raise ValueError('Exact positive bounded share ratio required')
        published,observed=stamp(self.available_at),stamp(self.observed_at)
        if observed<published or published.astimezone(BJ).date().isoformat()>self.record_date:raise ValueError('Invalid or late implementation disclosure')
        if not isinstance(self.source_url,str):raise ValueError('HTTPS original must be a string')
        parsed=urlsplit(self.source_url)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password:raise ValueError('Credential-free HTTPS original required')
        if not isinstance(self.source_sha256,str) or not re.fullmatch(r'[0-9a-f]{64}',self.source_sha256):raise ValueError('Original SHA256 required')
        if self.fractional_policy!='account_remainder_rank_random_ties':raise ValueError('Unreviewed fractional-allocation policy')

    @property
    def fingerprint(self):return digest(asdict(self))


@dataclass(frozen=True)
class Allotment:
    """Operator-supplied account confirmation; bytes are NOT parsed/authenticated."""
    receipt_id: str
    account_scope: str
    event_fingerprint: str
    share_credit_date: str
    unrestricted_shares: int
    restricted_shares: int
    available_at: str
    observed_at: str
    source_sha256: str

    def __post_init__(self):
        identifier(self.receipt_id);identifier(self.account_scope);day(self.share_credit_date)
        quantity(self.unrestricted_shares);quantity(self.restricted_shares)
        if not isinstance(self.event_fingerprint,str) or not re.fullmatch(r'[0-9a-f]{64}',self.event_fingerprint):raise ValueError('Allotment must bind the frozen action')
        if stamp(self.observed_at)<stamp(self.available_at):raise ValueError('Invalid allotment knowledge clocks')
        if stamp(self.available_at).astimezone(BJ).date().isoformat()<self.share_credit_date:raise ValueError('Credit confirmation cannot precede credit')


def _visible(available,observed,asof,mode):
    if stamp(available)>stamp(asof) or mode=='as_recorded' and stamp(observed)>stamp(asof):
        raise ValueError('Evidence was not yet known in this mode')


def _seal(book):
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=SOURCE_SHA:raise ValueError('Inventory source changed')
    book.pop('state_digest',None);book['state_digest']=digest(book)
    if len(json.dumps(book,ensure_ascii=False,allow_nan=False).encode())>MAX_STATE:raise ValueError('Inventory state byte budget exceeded')
    return book


def _check(book,scope):
    identifier(scope)
    if not isinstance(book,dict):raise ValueError('Inventory must be a JSON object')
    if book.get('schema')!=VERSION or book.get('kernel')!=SOURCE_SHA or book.get('account_scope')!=scope:
        raise ValueError('Wrong account scope or frozen inventory version')
    if book.get('mode') not in ('as_recorded','historical_reconstruction'):raise ValueError('Invalid frozen knowledge mode')
    if any(book.get(k) is not False for k in ('tax_basis_verified','strategy_execution_ready','promotion_allowed')):
        raise ValueError('Inventory cannot upgrade its tax/execution/qualification scope')
    if not all(isinstance(book.get(k),dict) for k in ('actions','operations')) or not isinstance(book.get('journal'),list):
        raise ValueError('Invalid inventory collections')
    if len(book['actions'])>1000 or len(book['operations'])>10000 or len(book['journal'])!=len(book['operations']):
        raise ValueError('Invalid inventory history size')
    day(book['start_after']);stamp(book['clock'])
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=SOURCE_SHA:raise ValueError('Inventory source changed')
    payload={k:v for k,v in book.items() if k!='state_digest'}
    if digest(payload)!=book.get('state_digest'):raise ValueError('Inventory state checksum changed')
    if len(json.dumps(book,ensure_ascii=False,allow_nan=False).encode())>MAX_STATE:raise ValueError('Inventory state byte budget exceeded')


def initial(scope,start_after,*,mode):
    identifier(scope);day(start_after)
    if mode not in ('as_recorded','historical_reconstruction'):raise ValueError('Explicit knowledge mode required')
    return _seal({'schema':VERSION,'kernel':SOURCE_SHA,'account_scope':scope,'mode':mode,
                  'start_after':start_after,'clock':start_after+'T00:00:00+08:00',
                  'actions':{},'operations':{},'journal':[],
                  'tax_basis_verified':False,'strategy_execution_ready':False,'promotion_allowed':False})


def restore(raw,*,scope):
    if not isinstance(raw,(str,bytes)) or len(raw)>MAX_STATE or isinstance(raw,str) and len(raw.encode())>MAX_STATE:
        raise ValueError('Bounded JSON state required')
    def pairs(items):
        obj={}
        for key,value in items:
            if key in obj:raise ValueError('Duplicate JSON key')
            obj[key]=value
        return obj
    def invalid(value):raise ValueError('Non-finite JSON')
    try:book=json.loads(raw,object_pairs_hook=pairs,parse_constant=invalid)
    except RecursionError as exc:raise ValueError('State nesting exceeds parser budget') from exc
    _check(book,scope);return book


def _begin(previous,scope,asof,key,payload):
    _check(previous,scope);clock=stamp(asof);fp=digest(payload)
    if key in previous['operations']:
        if previous['operations'][key]!=fp:raise ValueError('Operation identity reused with changed input')
        return None,fp
    if clock<stamp(previous['clock']):raise ValueError('Cannot backdate an applied inventory operation')
    if len(previous['operations'])>=10000:raise ValueError('Inventory operation quota exceeded')
    return copy.deepcopy(previous),fp


def _finish(previous,book,asof,key,fp):
    book['clock']=asof;book['operations'][key]=fp
    book['journal'].append({'operation':key,'asof':asof,'input_digest':fp,'previous_state_digest':previous['state_digest']})
    return _seal(book)


def _event(previous,event,sources,asof,scope):
    _check(previous,scope)
    if not isinstance(event,Capitalization):raise ValueError('Validated capitalization required')
    pdf_source(event.source_sha256,sources)
    _visible(event.available_at,event.observed_at,asof,previous['mode'])
    if event.record_date<=previous['start_after']:raise ValueError('Preexisting rights require an explicit opening position, not a backfill')
    old=previous['actions'].get(event.event_id)
    if old is not None and old['event_fingerprint']!=event.fingerprint:raise ValueError('Registered action revised; explicit rebuild required')


@exact_context
def register(previous,event,closing_lots,sources,*,asof,scope):
    """Snapshot AFTER record-day trading; supplied positions are model inputs."""
    _event(previous,event,sources,asof,scope)
    local=stamp(asof).astimezone(BJ)
    if local.date().isoformat()!=event.record_date or local.time()<time(15):raise ValueError('Record-date close snapshot required')
    if not isinstance(closing_lots,list) or len(closing_lots)>10000:raise ValueError('Bounded closing lots required')
    lots=[];ids=set();totals={'unrestricted':0,'restricted':0}
    for lot in closing_lots:
        if not isinstance(lot,dict) or set(lot)!={'id','symbol','qty','restriction'} or lot['symbol']!=event.symbol:raise ValueError('Ambiguous/mismatched record lot schema')
        identity=identifier(lot['id']);qty=quantity(lot['qty'],zero=False);restriction=lot['restriction']
        if identity in ids or restriction not in totals:raise ValueError('Duplicate lot or unknown restriction')
        ids.add(identity);totals[restriction]+=qty;quantity(totals[restriction]);lots.append(dict(lot))
    lots.sort(key=lambda x:x['id']);key='register:'+event.event_id
    book,fp=_begin(previous,scope,asof,key,{'event':asdict(event),'closing_lots':lots,'asof':asof})
    if book is None:return copy.deepcopy(previous)
    if len(book['actions'])>=1000:raise ValueError('Inventory action quota exceeded')
    ratio=Decimal(event.new_shares_per_old_share)
    quantity(sum(totals.values()))
    if sum(totals.values())*ratio>10**12:raise ValueError('Share entitlement quantity budget exceeded')
    book['actions'][event.event_id]={'event':asdict(event),'event_fingerprint':event.fingerprint,
        'record_lots':lots,'registered_old_shares':totals,
        'exact_new_shares':{k:str(v*ratio) for k,v in totals.items()},
        'status':'registered','credited':None,'consumed':0,
        'snapshot_role':'operator_supplied_model_close_not_broker_confirmation'}
    return _finish(previous,book,asof,key,fp)


def recognize(previous,event,sources,*,asof,scope):
    _event(previous,event,sources,asof,scope)
    if stamp(asof).astimezone(BJ).date().isoformat()!=event.ex_date:raise ValueError('Recognition must occur on ex-date')
    key='recognize:'+event.event_id;book,fp=_begin(previous,scope,asof,key,{'event':event.fingerprint,'asof':asof})
    if book is None:return copy.deepcopy(previous)
    item=book['actions'].get(event.event_id)
    if item is None or item['status']!='registered':raise ValueError('Missing registration snapshot')
    item['status']='receivable';item['recognized_asof']=asof
    return _finish(previous,book,asof,key,fp)


@exact_context
def credit(previous,event,sources,*,asof,scope,allotment=None):
    _event(previous,event,sources,asof,scope)
    if stamp(asof).astimezone(BJ).date().isoformat()<event.share_credit_date:raise ValueError('Shares not yet creditable')
    if allotment is not None:
        if not isinstance(allotment,Allotment) or allotment.account_scope!=scope or allotment.event_fingerprint!=event.fingerprint or allotment.share_credit_date!=event.share_credit_date:
            raise ValueError('Allotment belongs to a different account/action/date')
        pdf_source(allotment.source_sha256,sources)
        if allotment.source_sha256==event.source_sha256:raise ValueError('Issuer notice is not a personal allotment confirmation')
        _visible(allotment.available_at,allotment.observed_at,asof,previous['mode'])
    key='credit:'+event.event_id
    book,fp=_begin(previous,scope,asof,key,{'event':event.fingerprint,'allotment':asdict(allotment) if allotment else None,'asof':asof})
    if book is None:return copy.deepcopy(previous)
    item=book['actions'].get(event.event_id)
    if item is None or item['status']!='receivable':raise ValueError('Unrecognized share entitlement')
    exact={k:Decimal(v) for k,v in item['exact_new_shares'].items()}
    if allotment is None:
        if any(v!=v.to_integral_value() for v in exact.values()):raise ValueError('Fractional allotment unresolved; never round FIFO lots')
        credited={k:int(v) for k,v in exact.items()};basis='scheduled_exact_ratio_model_not_broker_confirmation'
    else:
        credited={'unrestricted':allotment.unrestricted_shares,'restricted':allotment.restricted_shares}
        for k,v in exact.items():
            if credited[k] not in {int(v.to_integral_value(rounding=ROUND_FLOOR)),int(v.to_integral_value(rounding=ROUND_CEILING))}:
                raise ValueError('Confirmed bucket exceeds fractional-allotment bounds')
        total=sum(exact.values(),Decimal(0))
        if sum(credited.values()) not in {int(total.to_integral_value(rounding=ROUND_FLOOR)),int(total.to_integral_value(rounding=ROUND_CEILING))}:
            raise ValueError('Allotment account total inconsistent with frozen ratio')
        basis='operator_supplied_allotment_not_automatically_authenticated'
    item.update(status='credited',credited=credited,credited_asof=asof,credit_basis=basis,
                allotment=asdict(allotment) if allotment else None)
    return _finish(previous,book,asof,key,fp)


def available(previous,event_id,*,asof,scope):
    """New unrestricted inventory only; NOT permission to place an order."""
    _check(previous,scope);identifier(event_id);now=stamp(asof);item=previous['actions'].get(event_id)
    if item is None:return 0
    event=item['event'];trade=event['unrestricted_trade_date']
    if item['status']!='credited' or now<stamp(item['credited_asof']) or trade is None or now.astimezone(BJ).date().isoformat()<trade:return 0
    return item['credited']['unrestricted']-item['consumed']


def consume(previous,event_id,qty,usage_id,*,asof,scope):
    """Record an inventory debit after the caller's separate cash/tax checks."""
    quantity(qty,zero=False);identifier(usage_id);identifier(event_id)
    key='consume:'+usage_id;book,fp=_begin(previous,scope,asof,key,{'event':event_id,'qty':qty,'asof':asof})
    if book is None:return copy.deepcopy(previous)
    if qty>available(previous,event_id,asof=asof,scope=scope):raise ValueError('Insufficient listed unrestricted new shares')
    book['actions'][event_id]['consumed']+=qty
    return _finish(previous,book,asof,key,fp)
