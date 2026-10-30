"""Cash-event-aware development replay, using raw executable prices and FIFO cash.

No network or production mutation. Partial public action coverage can exercise the
engine, but can NEVER authorize promotion or commercial performance claims.
"""
import copy
from datetime import date,datetime,time,timedelta
from dataclasses import asdict
from decimal import Decimal,ROUND_CEILING,ROUND_FLOOR
import hashlib
import math
from pathlib import Path
from . import quant_dividend_book as ledger
from .quant_corporate_actions import (BJ,money,visible_events,validate_evidence,ex_reference,gross_return_index)
from .quant_research_engine import digest,validate_dataset,metrics,CANDIDATES,ASSUMPTIONS
from .ai_fund_ledger import fee
from .ai_fund_policy import order_quantity

VERSION='event-replay-development-v2'


def _prefix(dataset,cursor):
    return {s:digest({**{k:p[k] for k in ('source','adjustment','volume_unit','is_synthetic')},
                     'bars':[b for b in p['bars'] if b['date']<=cursor]}) for s,p in dataset.items()}


def _known(events,d,mode):
    return digest([asdict(e) for e in visible_events(events,d+'T23:59:59+08:00',mode=mode)])


def _qty(book,s):return sum(l['qty'] for l in book['lots'] if l['symbol']==s)


def _stop(previous,s,prior_close,prior_date):
    lots=[l for l in previous['lots'] if l['symbol']==s]
    cost=sum((Decimal(l['cost_total']) for l in lots),Decimal(0))
    if not cost:return False
    rights=Decimal(0);ids={l['id'] for l in lots}
    for claim in previous['claims'].values():
        for part in claim['parts']:
            if part['lot'] in ids:
                rights+=part['remaining']*Decimal(claim['cash_ps'])*(1-ledger.tax_rate(part['acquired'],prior_date,claim['record_date']))
    value=Decimal(str(prior_close))*sum(l['qty'] for l in lots)+rights
    return value/cost-1<=-Decimal(str(ASSUMPTIONS['stop_loss']))


def replay(dataset,symbols,params,events,source_bytes,*,investor,mode='as_recorded',
           start=None,end=None,state=None,capital=1000000,schedule=None,progress=None):
    sessions=validate_dataset(dataset);validate_evidence(events,source_bytes)
    if not symbols or len(symbols)>5 or len(set(symbols))!=len(symbols) or set(symbols)-set(dataset):raise ValueError('研究标的无效')
    if set(symbols)&{'000300','000905','000852'}:raise ValueError('价格指数不能作为可交易股票')
    if any(e.symbol not in symbols for e in events):raise ValueError('公司行为超出策略股票范围')
    if params not in CANDIDATES:raise ValueError('参数未登记')
    if schedule:
        previous_end=''
        for f in schedule:
            if f['parameters'] not in CANDIDATES or not f['train_end']<f['test_start']<=f['test_end'] or f['test_start']<=previous_end:
                raise ValueError('参数窗口冲突、前视或未登记')
            if sum(f['train_end']<d<f['test_start'] for d in sessions)<5:raise ValueError('训练和测试须保留原五日间隔')
            previous_end=f['test_end']
    plan=digest([params,schedule,investor,mode])
    stop=end or sessions[-1]
    date.fromisoformat(stop)
    if stop>ledger.POLICY_REVIEWED_THROUGH:raise ValueError('超出已核验税务日期范围')
    if stop>sessions[-1]:raise ValueError('研究尾部缺少明确交易日历覆盖')
    maximum=max(p['lookback'] for p in ([f['parameters'] for f in schedule] if schedule else [params]))
    eligible=[d for d in sessions if (not start or d>=start) and d<=stop and
              all(sum(b['date']<d for b in dataset[s]['bars'])>=maximum+1 for s in symbols)]
    if not eligible:raise ValueError('缺少信号预热或研究区间行情')
    if state is None:
        first=eligible[0];cursor=(date.fromisoformat(first)-timedelta(days=1)).isoformat()
        account=ledger.initial_book(capital,cursor,investor=investor,mode=mode)
        state={'kernel':KERNEL,'plan':plan,'book':account,'peak':str(money(str(capital))),'paused':False,
               'prefix':_prefix(dataset,cursor),'known_events':_known(events,cursor,mode)}
    else:
        state=copy.deepcopy(state);cursor=state['book']['cursor']
        if state['kernel']!=KERNEL or state['plan']!=plan:raise ValueError('冻结内核或研究计划改变')
        if state['prefix']!=_prefix(dataset,cursor) or state['known_events']!=_known(events,cursor,mode):raise ValueError('历史行情或已知公司行为发生修订')
    account=state['book'];capital=float(account['initial'])
    period_start=float(account.get('valuation',{}).get('conservative_risk_nav',capital))
    first=account['cursor'];live=[d for d in eligible if d>first]
    maps={s:{b['date']:b for b in dataset[s]['bars']} for s in dataset}
    # Never build an index through future bars beyond the requested replay end.
    indexes={s:gross_return_index([b for b in dataset[s]['bars'] if b['date']<=stop],
                                 [e for e in events if e.symbol==s],mode=mode) for s in symbols} if live else {}
    event_dates=[]
    for e in events:
        for d in (e.record_date,e.ex_date,e.pay_date):
            if first<d<=stop and any(v.event_id==e.event_id for v in visible_events(events,d+'T23:59:59+08:00',mode=mode)):
                event_dates.append(d)
    timeline=sorted(set(live+event_dates))
    fills=[];curve=[];blocks=[]
    for completed,d in enumerate(timeline,1):
        if progress and (completed==1 or completed%10==0):progress(completed,len(timeline),d)
        trading=d in live
        opens={s:maps[s][d]['open'] for s in symbols} if trading else {}
        prior_account=account
        opened=ledger.open_session(account,d,opens,events,source_bytes,sessions,mode=mode)
        opening_nav=Decimal(opened['valuation']['conservative_risk_nav'])
        if opening_nav<=0:raise ValueError('税后风险净值非正，停止重放')
        if trading:
            state['peak']=str(max(Decimal(state['peak']),opening_nav))
            if opening_nav/Decimal(state['peak'])-1<=-Decimal(str(ASSUMPTIONS['drawdown_pause'])):state['paused']=True
        if trading:
            p=params
            if schedule:
                windows=[f for f in schedule if f['test_start']<=d<=f['test_end']]
                if len(windows)!=1:raise ValueError('交易日缺少唯一冻结参数窗口')
                p=windows[0]['parameters']
            prior={s:[b for b in dataset[s]['bars'] if b['date']<d] for s in symbols}
            signal_rows={s:[b for b in indexes[s] if b['date']<d] for s in symbols}
            scores={s:signal_rows[s][-1]['gross_return_index']/signal_rows[s][-p['lookback']-1]['gross_return_index']-1 for s in symbols}
            if not all(math.isfinite(v) for v in scores.values()):raise ValueError('信号收益出现非有限值')
            chosen={s for s in symbols if scores[s]>p['threshold']} if not state['paused'] else set()
            target=min(ASSUMPTIONS['single_weight'],ASSUMPTIONS['gross_weight']/len(chosen)) if chosen else 0
            touched=set()
            for side in ('sell','buy'):
                for s in sorted(symbols):
                    if s in touched:continue
                    bar=maps[s][d];prev=prior[s][-1];q=_qty(opened,s)
                    stopped=_stop(prior_account,s,prev['close'],prev['date'])
                    desired=opening_nav*Decimal(str(target)) if s in chosen and not stopped else Decimal(0)
                    current=Decimal(str(bar['open']))*q
                    if (side=='buy' and desired<=current) or (side=='sell' and desired>=current):continue
                    if bar['volume']<=0:
                        blocks.append({'date':d,'symbol':s,'side':side,'reason':'suspended_or_missing_volume'});continue
                    reference=ex_reference(prev['close'],events,d,symbol=s,asof=d+'T09:30:00+08:00',mode=mode)
                    limit=Decimal('.20') if s.startswith(('300','301','688','689')) else Decimal('.10')
                    change=Decimal(str(bar['open']))/reference-1
                    if (side=='buy' and change>=limit-Decimal('.003')) or (side=='sell' and change<=-limit+Decimal('.003')):
                        blocks.append({'date':d,'symbol':s,'side':side,'reason':'price_limit'});continue
                    cap=max(0,int(prev['volume']*ASSUMPTIONS['volume_participation']))
                    price=(Decimal(str(bar['open']))*(Decimal('1.001') if side=='buy' else Decimal('.999'))).quantize(Decimal('.01'),rounding=ROUND_CEILING if side=='buy' else ROUND_FLOOR)
                    if (side=='buy' and price>money(reference*(1+limit))) or (side=='sell' and price<money(reference*(1-limit))):
                        blocks.append({'date':d,'symbol':s,'side':side,'reason':'slippage_price_outside_limit'});continue
                    if side=='buy':
                        exposure=sum((Decimal(str(opens[a]))*_qty(opened,a) for a in symbols),Decimal(0))
                        budget=min(desired-current,ledger.available_cash(opened,d),max(Decimal(0),opening_nav*Decimal(str(ASSUMPTIONS['gross_weight']))-exposure))
                        n=min(order_quantity(s,float(budget),float(price)),order_quantity(s,float(price*cap),float(price)))
                        while n>0 and Decimal(n)*price+Decimal(str(fee(n*price,side)))>ledger.available_cash(opened,d):
                            n=n-1 if s.startswith(('688','689')) else n-100
                            if s.startswith(('688','689')) and n<200:n=0
                    else:
                        want=q if desired==0 else min(q,int((current-desired)/price))
                        settled=sum(l['qty'] for l in opened['lots'] if l['symbol']==s and l['acquired']<d)
                        n=min(want,cap,settled)
                        if n<q:n=int(n) if s.startswith(('688','689')) else n//100*100
                        if s.startswith(('688','689')) and n<200 and n!=q:n=0
                    if n<=0:
                        blocks.append({'date':d,'symbol':s,'side':side,'reason':'volume_cash_lot_or_Tplus1'});continue
                    if side=='sell' and n*price<Decimal(str(fee(n*price,side))):continue
                    reason='risk_pause' if state['paused'] else 'stop_loss' if stopped else 'momentum_rebalance'
                    f={'id':digest([VERSION,plan,d,s,side,n,str(price)]),'date':d,'signal_date':prev['date'],
                       'symbol':s,'side':side,'qty':int(n),'price':float(price),'fee':fee(n*price,side),
                       'reason':reason,'signal':scores[s],'opening_risk_nav':str(opening_nav),
                       'available_cash_before':str(ledger.available_cash(opened,d))}
                    opened=ledger.execute_fills(opened,[f]);fills.append(f);touched.add(s)
        closes={s:maps[s][d]['close'] for s in symbols} if trading else {}
        account=ledger.close_session(opened,closes,events,source_bytes,sessions,mode=mode)
        nav=Decimal(account['valuation']['conservative_risk_nav'])
        if nav<=0:raise ValueError('税后风险净值非正，停止重放')
        if trading:
            state['peak']=str(max(Decimal(state['peak']),nav))
            if nav/Decimal(state['peak'])-1<=-Decimal(str(ASSUMPTIONS['drawdown_pause'])):state['paused']=True
            curve.append({'date':d,'nav':float(nav),**account['valuation'],'risk_paused':state['paused']})
    state.update(book=account,prefix=_prefix(dataset,account['cursor']),known_events=_known(events,account['cursor'],mode))
    if progress:progress(len(timeline),len(timeline),account['cursor'])
    return {'state':state,'fills':fills,'curve':curve,'blocked':blocks,'metrics':metrics(curve,period_start),
            'cumulative_return_pct':(float(account.get('valuation',{}).get('conservative_risk_nav',capital))/capital-1)*100,
            'version':VERSION,'signal_basis':'prior_completed_gross_return_index',
            'execution_basis':'raw_open_and_declared_ex_reference','risk_basis':'dividend_tax_reserved_nav',
            'gate':{'passed':False,'reasons':['已见开发数据','公司行为覆盖不完整','价格基准和税务现金时点仍待完整核验'],
                    'commercial_ready':False,'live_trading_enabled':False}}


KERNEL=digest({n:hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest() for n in (
    'quant_event_replay.py','quant_dividend_book.py','quant_corporate_actions.py','quant_research_engine.py',
    'ai_fund_ledger.py','ai_fund_policy.py')})
