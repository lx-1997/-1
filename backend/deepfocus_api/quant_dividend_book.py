"""Atomic day-end FIFO book for an explicitly selected CN individual tax model.

No broker, database or strategy decisions. Cash dividends are actual book events,
not retrospective NAV add-backs. Old sealed kernels are deliberately untouched.
"""
import calendar as cal
import copy
import re
import hashlib
from pathlib import Path
from datetime import date, datetime, time
from decimal import Decimal
from dataclasses import asdict
from .ai_fund_ledger import fee
from .quant_corporate_actions import BJ, money, visible_events, validate_evidence
from .quant_research_engine import digest

VERSION='dividend-fifo-book-v2'
INVESTOR='cn_resident_individual_public_a_share'
POLICY_REVIEWED_THROUGH='2026-10-30'
POLICY={
    'version':VERSION,'investor':INVESTOR,'tax_policy':'caishui-2015-101',
    'policy_reviewed_through':POLICY_REVIEWED_THROUGH,
    'holding_period':'calendar_month_year_inclusive_anniversary',
    'trade_netting':'one_direction_per_symbol_per_session',
    'payment_timing':'scheduled_pay_date_end_of_day_model_not_broker_confirmation',
    'tax_settlement':'reserve_at_sale_settle_next_session_model',
    'risk_nav':'net_of_assessed_tax_and_unrealized_tax_reserve',
    'live_trading':False,'commercial_ready':False,
}


def _decimal(value):
    try: result=Decimal(str(value))
    except Exception as exc:raise ValueError('金额无效') from exc
    if isinstance(value,bool) or not result.is_finite() or result<0:raise ValueError('金额无效')
    return result


def _anniversary(d, months):
    n=d.year*12+d.month-1+months;y,m=divmod(n,12);m+=1
    return date(y,m,min(d.day,cal.monthrange(y,m)[1]))


def tax_rate(acquired, disposed, registration):
    a,b,r=map(date.fromisoformat,(acquired,disposed,registration))
    if b<a or r< a:raise ValueError('持仓或登记时间无效')
    if not date(2015,9,8)<r<=date.fromisoformat(POLICY_REVIEWED_THROUGH) or b>date.fromisoformat(POLICY_REVIEWED_THROUGH):
        raise ValueError('税制或核验日期不在本版本覆盖范围')
    return Decimal('.20') if b<=_anniversary(a,1) else Decimal('.10') if b<=_anniversary(a,12) else Decimal(0)


def initial_book(capital, start_after, *, investor, mode='as_recorded'):
    date.fromisoformat(start_after)
    if investor!=INVESTOR:raise ValueError('须明确使用受支持的个人公开市场 A 股税务模型')
    if mode not in ('as_recorded','historical_reconstruction'):raise ValueError('须选择知识可用时间模式')
    capital=money(_decimal(capital))
    if capital<=0:raise ValueError('本金须为正')
    return {'version':VERSION,'kernel':BOOK_KERNEL,'knowledge_mode':mode,'investor':investor,'initial':str(capital),'cash':str(capital),
            'start_after':start_after,'cursor':start_after,'lots':[],'registrations':{},'claims':{},
            'fill_ids':{},'payables':[],'paid_gross':'0.00','tax_assessed':'0.00','tax_settled':'0.00',
            'fees':'0.00','prices':{},'events':[],'last_input_digest':None,'phase':'closed'}


def _pending(book):return sum((Decimal(p['amount']) for p in book['payables'] if not p['paid']),Decimal(0))


def tax_reserve(book, d, *, paid_only=False):
    exact=Decimal(0)
    for claim in book['claims'].values():
        if paid_only and not claim['paid']:continue
        for part in claim['parts']:
            exact+=Decimal(part['remaining'])*Decimal(claim['cash_ps'])*tax_rate(part['acquired'],d,claim['record_date'])
    return money(exact)


def available_cash(book, d):
    return max(Decimal(0),money(Decimal(book['cash'])-_pending(book)-tax_reserve(book,d,paid_only=True)))


def valuation(book,d):
    assets=Decimal(book['cash'])+sum((Decimal(l['qty'])*Decimal(book['prices'][l['symbol']]) for l in book['lots']),Decimal(0))
    receivable=sum((Decimal(c['gross']) for c in book['claims'].values() if not c['paid']),Decimal(0))
    gross=money(assets+receivable);net=money(gross-_pending(book));reserve=tax_reserve(book,d)
    return {'gross_nav':str(gross),'nav_after_assessed_dividend_tax':str(net),'unrealized_dividend_tax_reserve':str(reserve),
            'conservative_risk_nav':str(money(net-reserve)),'dividend_receivable':str(money(receivable)),
            'pending_dividend_tax':str(money(_pending(book))),'available_cash':str(available_cash(book,d))}


def _check_book(previous, mode):
    if previous['version']!=VERSION or previous['investor']!=INVESTOR or previous.get('kernel')!=BOOK_KERNEL:raise ValueError('冻结账务版本不兼容')
    if mode!=previous['knowledge_mode']:raise ValueError('不能切换冻结账簿的知识可用模式')


def _marks(book, prices, d, sessions):
    if prices and d not in sessions:raise ValueError('非交易日不能注入新的收盘估值')
    for symbol in {l['symbol'] for l in book['lots']}:
        if d in sessions and symbol not in prices:raise ValueError('缺少持仓收盘估值，禁止默认为零')
    for symbol,value in prices.items():
        price=_decimal(value)
        if price<=0:raise ValueError('持仓估值须为正')
        book['prices'][symbol]=str(price)


def _context(events,sessions,mode):return digest([[asdict(e) for e in events],sessions,mode])


def open_session(previous, d, mark_prices, events, source_bytes, sessions, *, mode='as_recorded'):
    """Apply pre-open tax settlement and dividend receivables, never pay-day cash."""
    _check_book(previous,mode)
    if previous.get('phase')!='closed':raise ValueError('上一交易日尚未收盘')
    day=date.fromisoformat(d)
    if d>POLICY_REVIEWED_THROUGH:raise ValueError('须更新税制核验版本，不能静默推定未来规则')
    if sessions!=sorted(set(sessions)) or any(date.fromisoformat(x).isoformat()!=x for x in sessions):raise ValueError('交易日历须唯一递增')
    validate_evidence(events,source_bytes)
    visible=visible_events(events,datetime.combine(day,time(9,30),BJ).isoformat(),mode=mode)
    if d<=previous['cursor']:raise ValueError('禁止重复改写已入账交易日')
    if any(previous['cursor']<s<d for s in sessions):raise ValueError('不能跳过交易日或登记快照')
    book=copy.deepcopy(previous);book['cursor']=d
    book.update(phase='open',session_fills=[],session_directions={},session_context=_context(events,sessions,mode),session_tradeable=d in sessions)
    # Assess tax as a liability on disposal; debit cash in the next actual
    # supplied trading session, preserving a bill if available cash is short.
    for p in book['payables']:
        if not p['paid'] and p['assessed']<d and d in sessions:
            amount=Decimal(p['amount'])
            if Decimal(book['cash'])>=amount:
                book['cash']=str(money(Decimal(book['cash'])-amount));p['paid']=True
                book['tax_settled']=str(money(Decimal(book['tax_settled'])+amount))
                book['events'].append({'date':d,'kind':'dividend_tax_settlement','amount':str(amount)})
    for event in visible:
        key=event.event_id
        if previous['cursor']<event.pay_date<d:raise ValueError('不能跳过现金支付日，包含非交易日支付')
        for collection in ('registrations','claims'):
            if key in book[collection] and book[collection][key]['fingerprint']!=event.fingerprint:raise ValueError('已登记事件出现修订，须独立重建版本')
        if previous['start_after']<event.record_date<d and key not in book['registrations']:raise ValueError('迟到或遗漏的登记事件，禁止静默补入')
        if event.ex_date==d and key not in book['claims']:
            if event.record_date<=previous['start_after']:parts=[]
            else:
                registration=book['registrations'].get(key)
                if registration is None:raise ValueError('缺少登记日快照')
                parts=copy.deepcopy(registration['parts'])
                if event.record_date not in sessions or event.ex_date not in sessions or sessions.index(event.ex_date)!=sessions.index(event.record_date)+1:
                    raise ValueError('本日频账务要求登记和除息为连续交易日')
            gross=money(sum((Decimal(p['remaining']) for p in parts),Decimal(0))*Decimal(event.cash_per_share_gross))
            book['claims'][key]={'parts':parts,'gross':str(gross),'cash_ps':event.cash_per_share_gross,
                'record_date':event.record_date,'pay_date':event.pay_date,'paid':False,'tax_exact':'0','fingerprint':event.fingerprint}
            book['events'].append({'date':d,'kind':'dividend_receivable','event':key,'amount':str(gross)})
    _marks(book,mark_prices,d,sessions)
    book['valuation']=valuation(book,d)
    return book


def execute_fills(previous, fills):
    """Apply fills atomically to an open session; no closing information enters."""
    _check_book(previous,previous['knowledge_mode'])
    if previous.get('phase')!='open':raise ValueError('成交须处于已开盘会话')
    if fills and not previous['session_tradeable']:raise ValueError('非交易日不得成交')
    book=copy.deepcopy(previous);d=book['cursor'];directions=book['session_directions']
    for f in fills:
        symbol=f['symbol'];side=f['side'];qty=f['qty'];price=_decimal(f['price'])
        if not isinstance(symbol,str) or not re.fullmatch(r'[036][0-9]{5}',symbol):raise ValueError('仅支持沪深 A 股代码')
        if f['date']!=d or side not in ('buy','sell') or type(qty) is not int or qty<=0 or price<=0 or money(price)!=price:raise ValueError('成交记录无效')
        if symbol in directions and directions[symbol]!=side:raise ValueError('同日反向交易须采用日终净额税务模型，当前版本拒绝')
        directions[symbol]=side
        if f['id'] in book['fill_ids']:raise ValueError('成交标识重复或冲突')
        amount=money(price*qty);charge=money(fee(amount,side))
        if money(_decimal(f['fee']))!=charge:raise ValueError('成交费用与冻结模拟费率不一致')
        book['fill_ids'][f['id']]=digest(f)
        if side=='buy':
            if amount+charge>available_cash(book,d):raise ValueError('可用现金不足或税款已被预留')
            book['cash']=str(money(Decimal(book['cash'])-amount-charge))
            book['lots'].append({'id':f['id'],'symbol':symbol,'qty':qty,'acquired':d,'cost_total':str(amount+charge)})
            book['prices'].setdefault(symbol,str(price))
        else:
            settled=sum(l['qty'] for l in book['lots'] if l['symbol']==symbol and l['acquired']<d)
            if qty>settled:raise ValueError('T+1 可卖持仓不足')
            if amount<charge:raise ValueError('卖出收入不足支付手续费')
            book['cash']=str(money(Decimal(book['cash'])+amount-charge));remaining=qty
            # Acquisition order is preserved within a date, rather than sorted
            # by an arbitrary fill identifier.
            for lot in book['lots']:
                if not remaining:break
                if lot['symbol']!=symbol or lot['acquired']>=d:continue
                taken=min(remaining,lot['qty']);old_qty=lot['qty'];lot['qty']-=taken;remaining-=taken
                lot['cost_total']=str(Decimal(lot['cost_total'])*lot['qty']/old_qty)
                for key,claim in book['claims'].items():
                    for part in claim['parts']:
                        if part['lot']!=lot['id']:continue
                        taxable=min(taken,part['remaining']);part['remaining']-=taxable
                        before=money(Decimal(claim['tax_exact']))
                        exact=Decimal(claim['tax_exact'])+taxable*Decimal(claim['cash_ps'])*tax_rate(part['acquired'],d,claim['record_date'])
                        claim['tax_exact']=str(exact);tax=money(exact)-before
                        if tax:
                            book['payables'].append({'assessed':d,'event':key,'amount':str(tax),'paid':False})
                            book['tax_assessed']=str(money(Decimal(book['tax_assessed'])+tax))
                            book['events'].append({'date':d,'kind':'dividend_tax_assessment','event':key,'amount':str(tax),'lot':lot['id'],'qty':taxable})
            book['lots']=[l for l in book['lots'] if l['qty']]
        book['fees']=str(money(Decimal(book['fees'])+charge))
        book['session_fills'].append(copy.deepcopy(f))
    book['valuation']=valuation(book,d)
    return book


def close_session(previous, close_prices, events, source_bytes, sessions, *, mode='as_recorded'):
    _check_book(previous,mode)
    if previous.get('phase')!='open':raise ValueError('收盘须处于已开盘会话')
    if previous['session_context']!=_context(events,sessions,mode):raise ValueError('开收盘期间输入证据改变')
    validate_evidence(events,source_bytes)
    book=copy.deepcopy(previous);d=book['cursor']
    visible=visible_events(events,datetime.combine(date.fromisoformat(d),time(23,59,59),BJ).isoformat(),mode=mode)
    for event in visible:
        for collection in ('registrations','claims'):
            if event.event_id in book[collection] and book[collection][event.event_id]['fingerprint']!=event.fingerprint:
                raise ValueError('已登记事件出现修订，须独立重建版本')
    # Registration ownership is captured AFTER today's executions.
    for event in visible:
        if event.record_date==d:
            parts=[{'lot':l['id'],'acquired':l['acquired'],'remaining':l['qty']} for l in book['lots'] if l['symbol']==event.symbol]
            book['registrations'][event.event_id]={'fingerprint':event.fingerprint,'parts':parts}
        claim=book['claims'].get(event.event_id)
        if claim and event.pay_date==d and not claim['paid']:
            claim['paid']=True;amount=Decimal(claim['gross']);book['cash']=str(money(Decimal(book['cash'])+amount))
            book['paid_gross']=str(money(Decimal(book['paid_gross'])+amount))
            book['events'].append({'date':d,'kind':'dividend_payment','event':event.event_id,'amount':str(amount)})
    _marks(book,close_prices,d,sessions)
    book['valuation']=valuation(book,d)
    book['last_input_digest']=digest([d,book['session_fills'],close_prices,[asdict(e) for e in visible],mode])
    for key in ('session_fills','session_directions','session_context','session_tradeable'):book.pop(key)
    book['phase']='closed'
    if Decimal(book['cash'])<0:raise ValueError('现金守恒失败')
    return book


def apply_day(previous, d, fills, close_prices, events, source_bytes, sessions, *, mode='as_recorded'):
    """Atomic compatibility wrapper for known fills; never a strategy decision API."""
    _check_book(previous,mode)
    validate_evidence(events,source_bytes)
    visible=visible_events(events,datetime.combine(date.fromisoformat(d),time(23,59,59),BJ).isoformat(),mode=mode)
    identity=digest([d,fills,close_prices,[asdict(e) for e in visible],mode])
    if previous.get('phase')=='closed' and d==previous['cursor'] and identity==previous['last_input_digest']:return copy.deepcopy(previous)
    opened=open_session(previous,d,close_prices,events,source_bytes,sessions,mode=mode)
    filled=execute_fills(opened,fills)
    return close_session(filled,close_prices,events,source_bytes,sessions,mode=mode)


# Capture loaded-process scientific identity, not newer bytes reread per request.
BOOK_KERNEL=digest({name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
    for name in ('quant_dividend_book.py','quant_corporate_actions.py','quant_research_engine.py',
                 'ai_fund_ledger.py','ai_fund_policy.py')})
