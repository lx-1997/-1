"""Frozen composition of cash-dividend accounting and capitalization inventory.

Research valuation only: scheduled credit at session CLOSE, never opening cash.
Unknown fractional allocation is a value interval, not fictitious credited stock.
Tax/cost inheritance is unresolved: affected-symbol fills and later cash-dividend
registrations on new shares are rejected, rather than silently losing tax rights.
The sealed cash, share, replay and qualification kernels are not modified.
"""
import copy
from dataclasses import asdict
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
import hashlib
import json
from pathlib import Path
from . import quant_dividend_book as cash, quant_share_entitlements as shares
from .quant_corporate_actions import visible_events, money

VERSION='capitalization-composite-research-book-v1'
FILES=('quant_capitalization_book.py','quant_share_entitlements.py','quant_dividend_book.py',
       'quant_corporate_actions.py','quant_research_engine.py','ai_fund_ledger.py','ai_fund_policy.py')


def pins():
    return {n:hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest() for n in FILES}


LOADED_PINS=pins()


def _seal(book):
    if pins()!=LOADED_PINS:raise ValueError('Composite source changed')
    book.pop('state_digest',None);book['state_digest']=shares.digest(book)
    if len(json.dumps(book,ensure_ascii=False,allow_nan=False).encode())>shares.MAX_STATE:raise ValueError('Composite state byte budget exceeded')
    return book


def _check(book,scope):
    shares.identifier(scope)
    if not isinstance(book,dict) or book.get('schema')!=VERSION or book.get('scope')!=scope or book.get('source_pins')!=LOADED_PINS or pins()!=LOADED_PINS:
        raise ValueError('Composite account or frozen source mismatch')
    if shares.digest({k:v for k,v in book.items() if k!='state_digest'})!=book.get('state_digest'):raise ValueError('Composite checkpoint changed')
    if len(json.dumps(book,ensure_ascii=False,allow_nan=False).encode())>shares.MAX_STATE:raise ValueError('Composite state byte budget exceeded')
    if any(book.get(k) is not False for k in ('tax_basis_verified','strategy_execution_ready','promotion_allowed')):raise ValueError('Unverified execution cannot be enabled')
    cash._check_book(book['cash'],book['shares']['mode']);shares._check(book['shares'],scope)
    if book['cash']['phase'] not in ('open','closed') or book['cash']['start_after']!=book['shares']['start_after']:
        raise ValueError('Component boundary mismatch')
    if shares.stamp(book['shares']['clock']).astimezone(shares.BJ).date().isoformat()>book['cash']['cursor']:raise ValueError('Share inventory ahead of cash session')


def initial(capital,start_after,*,scope,investor,mode):
    return _seal({'schema':VERSION,'scope':scope,'source_pins':LOADED_PINS,
                  'cash':cash.initial_book(capital,start_after,investor=investor,mode=mode),
                  'shares':shares.initial(scope,start_after,mode=mode),'marks':{},
                  'tax_basis_verified':False,'strategy_execution_ready':False,'promotion_allowed':False})


def restore(raw,*,scope):
    if not isinstance(raw,(str,bytes)) or len(raw)>shares.MAX_STATE or isinstance(raw,str) and len(raw.encode())>shares.MAX_STATE:raise ValueError('Bounded JSON checkpoint required')
    def pairs(items):
        result={}
        for k,v in items:
            if k in result:raise ValueError('Duplicate checkpoint key')
            result[k]=v
        return result
    def invalid(value):raise ValueError('Non-finite checkpoint')
    try:book=json.loads(raw,object_pairs_hook=pairs,parse_constant=invalid)
    except RecursionError as exc:raise ValueError('Checkpoint nesting budget exceeded') from exc
    _check(book,scope);return book


def _inputs(actions,sources):
    if not isinstance(actions,list) or len(actions)>1000:raise ValueError('Bounded action list required')
    ids=set();facts=set()
    for e in actions:
        if not isinstance(e,shares.Capitalization):raise ValueError('Validated capitalization required')
        fact=(e.symbol,e.record_date,e.ex_date)
        if e.event_id in ids or fact in facts:raise ValueError('Duplicate or overlapping capitalization')
        ids.add(e.event_id);facts.add(fact);shares.pdf_source(e.source_sha256,sources)


def _visible(actions,asof,mode):
    return [e for e in actions if shares.stamp(e.available_at)<=shares.stamp(asof)
            and (mode=='historical_reconstruction' or shares.stamp(e.observed_at)<=shares.stamp(asof))]


def _context(actions,events,sessions,mode):
    return shares.digest([[asdict(e) for e in actions],[asdict(e) for e in events],sessions,mode])


def _existing(book,actions):
    declared={e.event_id:e for e in actions}
    for key,item in book['shares']['actions'].items():
        if key not in declared or declared[key].fingerprint!=item['event_fingerprint']:raise ValueError('Registered capitalization removed or revised')


def _outstanding(item):
    if item['status']=='registered':return Decimal(0)
    if item['status']=='receivable':return sum(map(Decimal,item['exact_new_shares'].values()),Decimal(0))
    if item['status']=='credited':return Decimal(sum(item['credited'].values())-item['consumed'])
    raise ValueError('Unknown capitalization status')


def _new_lots(book,symbol):
    result=[]
    for key,item in sorted(book['shares']['actions'].items()):
        if item['event']['symbol']!=symbol:continue
        if item['status']=='receivable' and _outstanding(item):raise ValueError('Unallocated shares cannot determine a later registration')
        if item['status']=='credited':
            for bucket,n in item['credited'].items():
                n-=item['consumed'] if bucket=='unrestricted' else 0
                if n:result.append({'id':'cap:'+shares.digest([key,bucket]),'symbol':symbol,'qty':n,'restriction':bucket})
    return result


def _mark(book,prices,d):
    symbols={l['symbol'] for l in book['cash']['lots']}
    symbols|={i['event']['symbol'] for i in book['shares']['actions'].values() if _outstanding(i)}
    if book['cash'].get('session_tradeable'):
        if symbols-set(prices):raise ValueError('Missing raw mark for cash or new-share position')
    for symbol,value in prices.items():
        price=cash._decimal(value)
        if price<Decimal('.01') or price>Decimal('100000000') or money(price)!=price:raise ValueError('Invalid bounded cent-tick raw mark')
        book['marks'][symbol]={'price':str(price),'date':d}
    for symbol in symbols:
        if symbol not in book['marks'] or book['marks'][symbol]['date']>d:raise ValueError('Missing or future inventory mark')


@shares.exact_context
def _valuation(book):
    d=book['cash']['cursor'];base=cash.valuation(book['cash'],d)
    exact=Decimal(0);lower=Decimal(0);upper=Decimal(0);details=[]
    for key,item in sorted(book['shares']['actions'].items()):
        n=_outstanding(item)
        if not n:continue
        symbol=item['event']['symbol'];p=Decimal(book['marks'][symbol]['price'])
        lo=n.to_integral_value(rounding=ROUND_FLOOR);hi=n.to_integral_value(rounding=ROUND_CEILING)
        exact+=n*p;lower+=lo*p;upper+=hi*p
        details.append({'event_id':key,'symbol':symbol,'status':item['status'],
                        'quantity_exact_or_credited':str(n),'quantity_lower':str(lo),'quantity_upper':str(hi),
                        'available_new_shares':shares.available(book['shares'],key,asof=d+'T23:59:59+08:00',scope=book['scope']),
                        'mark':book['marks'][symbol]})
    gross=Decimal(base['gross_nav']);risk=Decimal(base['conservative_risk_nav'])
    return {'cash_book':base,'new_share_value_exact':str(exact),'new_share_value_lower':str(lower),'new_share_value_upper':str(upper),
            'composite_gross_nav_exact':str(money(gross+exact)),
            'composite_gross_nav_lower':str(money(gross+lower)),'composite_gross_nav_upper':str(money(gross+upper)),
            'cash_dividend_tax_reserved_nav_lower':str(money(risk+lower)),
            'cash_dividend_tax_reserved_nav_upper':str(money(risk+upper)),
            'available_cash':base['available_cash'],'new_shares':details,
            'basis':'raw_marks_plus_separate_receivable_or_credited_inventory',
            'complete_after_tax_nav':None,'strategy_risk_nav':None,
            'unresolved':['capitalization_tax_and_cost_inheritance','restricted_liquidity_value','complete_action_coverage','broker_clearing']}


@shares.exact_context
def open_session(previous,d,prices,events,actions,sources,sessions,*,scope):
    _check(previous,scope);_inputs(actions,sources);_existing(previous,actions)
    mode=previous['shares']['mode'];asof=d+'T09:30:00+08:00';known=_visible(actions,asof,mode)
    for e in known:
        if previous['shares']['start_after']<e.record_date<d and e.event_id not in previous['shares']['actions']:
            raise ValueError('Late or skipped capitalization registration; explicit rebuild required')
        if previous['cash']['cursor']<e.ex_date<d:raise ValueError('Cannot skip capitalization ex-date')
        if previous['shares']['start_after']<e.record_date and (e.record_date not in sessions or e.ex_date not in sessions
                or sessions.index(e.ex_date)!=sessions.index(e.record_date)+1):raise ValueError('Registration and ex-date require consecutive supplied sessions')
    book=copy.deepcopy(previous)
    book['cash']=cash.open_session(book['cash'],d,prices,events,sources,sessions,mode=mode)
    for e in known:
        if e.ex_date==d and e.event_id in book['shares']['actions']:
            book['shares']=shares.recognize(book['shares'],e,sources,asof=asof,scope=scope)
    book['session_context']=_context(actions,events,sessions,mode)
    _mark(book,prices,d);book['valuation']=_valuation(book)
    return _seal(book)


@shares.exact_context
def execute_fills(previous,fills,*,scope):
    _check(previous,scope)
    if not isinstance(fills,list) or len(fills)>10000:raise ValueError('Bounded fills required')
    blocked={i['event']['symbol'] for i in previous['shares']['actions'].values()
             if i['status']!='registered' and sum(i['registered_old_shares'].values())}
    if any(f.get('symbol') in blocked for f in fills):raise ValueError('Capitalization tax/cost inheritance unresolved; affected-symbol fills forbidden')
    if any(f.get('symbol') not in previous['marks'] or previous['marks'][f['symbol']]['date']!=previous['cash']['cursor'] for f in fills):
        raise ValueError('Execution needs an explicit current-session raw mark')
    book=copy.deepcopy(previous);book['cash']=cash.execute_fills(book['cash'],fills)
    book['valuation']=_valuation(book);return _seal(book)


@shares.exact_context
def close_session(previous,prices,events,actions,sources,sessions,*,scope,allotments=None):
    _check(previous,scope);_inputs(actions,sources);_existing(previous,actions)
    mode=previous['shares']['mode'];d=previous['cash']['cursor'];asof=d+'T23:59:59+08:00'
    if previous.get('session_context')!=_context(actions,events,sessions,mode):raise ValueError('Composite session inputs changed')
    if allotments is None:allotments={}
    if not isinstance(allotments,dict) or set(allotments)-{e.event_id for e in actions}:raise ValueError('Unknown allotment event')
    if any(not isinstance(value,shares.Allotment) for value in allotments.values()):raise ValueError('Explicit validated allotment required')
    book=copy.deepcopy(previous)
    for e in visible_events(events,asof,mode=mode):
        if e.record_date==d and _new_lots(book,e.symbol):raise ValueError('New-share dividend tax inheritance unresolved; registration forbidden')
    known=sorted(_visible(actions,asof,mode),key=lambda e:(e.record_date,e.event_id))
    if any(book['shares']['start_after']<e.record_date<d and e.event_id not in book['shares']['actions'] for e in known):
        raise ValueError('Late close disclosure cannot backfill capitalization registration')
    eligible={e.event_id for e in known if e.event_id in book['shares']['actions']
              and book['shares']['actions'][e.event_id]['status']=='receivable' and d>=e.share_credit_date}
    if set(allotments)-eligible:raise ValueError('Allotment has no eligible receivable in this session')
    for e in known:
        if e.record_date==d:
            lots=[{'id':l['id'],'symbol':l['symbol'],'qty':l['qty'],'restriction':'unrestricted'} for l in book['cash']['lots'] if l['symbol']==e.symbol]
            lots+=_new_lots(book,e.symbol)
            book['shares']=shares.register(book['shares'],e,lots,sources,asof=asof,scope=scope)
        item=book['shares']['actions'].get(e.event_id)
        if item and item['status']=='receivable' and d>=e.share_credit_date:
            try:book['shares']=shares.credit(book['shares'],e,sources,asof=asof,scope=scope,allotment=allotments.get(e.event_id))
            except shares.PendingAllotment:pass
    # Validate marks while still in an open session; cash.close removes the flag.
    _mark(book,prices,d)
    book['cash']=cash.close_session(book['cash'],prices,events,sources,sessions,mode=mode)
    book.pop('session_context');book['valuation']=_valuation(book)
    return _seal(book)
