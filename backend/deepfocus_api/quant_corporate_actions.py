"""Evidence-bound cash-dividend reconciliation, separate from trading execution.

The overlay never changes sealed fills, risk pauses or promotion gates. It is
gross-of-tax and incomplete until a whole-universe action feed is verified.
"""
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import re
from urllib.parse import urlsplit
from .quant_research_engine import digest, validate_dataset

BJ = timezone(timedelta(hours=8))
CENT = Decimal('.01')


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.tzinfo is None: raise ValueError('公告和采集时间须带时区')
    return parsed


def money(value): return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class CashDividend:
    event_id: str
    revision: int
    symbol: str
    record_date: str
    ex_date: str
    pay_date: str
    cash_per_share_gross: str
    price_adjustment_per_share: str
    available_at: str
    observed_at: str
    source_url: str
    source_sha256: str
    kind: str = 'cash_dividend'

    def __post_init__(self):
        if not isinstance(self.event_id,str) or not 1<=len(self.event_id)<=128: raise ValueError('事件标识无效')
        if type(self.revision) is not int or self.revision<1: raise ValueError('修订序号无效')
        if not re.fullmatch(r'[036][0-9]{5}',self.symbol): raise ValueError('沪深代码无效')
        if self.kind!='cash_dividend':raise ValueError('仅支持现金分红；送转配股不能假装成现金事件')
        days=[date.fromisoformat(x) for x in (self.record_date,self.ex_date,self.pay_date)]
        if not days[0]<days[1]<=days[2]:raise ValueError('登记日须早于除息日，到账日不得更早')
        for value in (self.cash_per_share_gross,self.price_adjustment_per_share):
            if not isinstance(value,str) or not re.fullmatch(r'[0-9]+(?:\.[0-9]{1,10})?',value) or Decimal(value)<=0:
                raise ValueError('每股金额须为正的精确十进制字符串')
        published,observed=timestamp(self.available_at),timestamp(self.observed_at)
        if observed<published:raise ValueError('采集时间不得早于公告可用时间')
        # Unknown intraday publication uses the end of the dated announcement,
        # not a guessed 00:00 that would expose it to that morning's decisions.
        if self.revision==1 and published.astimezone(BJ).date()>days[0]:raise ValueError('登记后才披露的初始事件须人工核验，不能直接回填')
        url=urlsplit(self.source_url)
        if url.scheme!='https' or not url.hostname or url.username or url.password:
            raise ValueError('原始证据须有不含凭据的 HTTPS 来源')
        if not re.fullmatch(r'[0-9a-f]{64}',self.source_sha256):raise ValueError('缺少原文 SHA256')

    @property
    def fingerprint(self): return digest(asdict(self))


def _validate_versions(events):
    identities=set();groups={}
    for event in events:
        if not isinstance(event,CashDividend):raise ValueError('公司行为须先通过结构验证')
        key=(event.event_id,event.revision)
        if key in identities:raise ValueError('重复事件修订，不能重复计入权益')
        identities.add(key)
        groups.setdefault(event.event_id,[]).append(event)
    for versions in groups.values():
        ordered=sorted(versions,key=lambda e:e.revision)
        if len({v.symbol for v in ordered})!=1:raise ValueError('修订不能改变证券身份')
        if [v.revision for v in ordered]!=list(range(1,len(ordered)+1)):raise ValueError('修订链不完整')
        for a,b in zip(ordered,ordered[1:]):
            if timestamp(a.available_at)>=timestamp(b.available_at) or timestamp(a.observed_at)>timestamp(b.observed_at):
                raise ValueError('修订时间顺序无效')


def validate_evidence(events, source_bytes):
    """Content pins prove retained bytes, not completeness or market-data rights."""
    _validate_versions(events)
    for event in events:
        body=source_bytes.get(event.source_sha256)
        if not isinstance(body,bytes) or hashlib.sha256(body).hexdigest()!=event.source_sha256:
            raise ValueError('原始公告缺失或已被修改')


def visible_events(events, asof, *, mode='as_recorded'):
    _validate_versions(events)
    if mode not in ('as_recorded','historical_reconstruction'):raise ValueError('须明确历史重建或当时实存模式')
    asof=timestamp(asof);selected={}
    for event in events:
        if timestamp(event.available_at)>asof:continue
        if mode=='as_recorded' and timestamp(event.observed_at)>asof:continue
        if event.event_id not in selected or event.revision>selected[event.event_id].revision:
            selected[event.event_id]=event
    return sorted(selected.values(),key=lambda e:e.event_id)


def ex_reference(previous_close, events, trade_date, *, symbol, asof, mode='as_recorded'):
    """Exchange reference uses the declared adjustment, not shareholder cash.

    Treasury-share exclusions can make these amounts different (Midea 2024).
    This helper requires validated evidence; it never supplies a market quote.
    """
    if timestamp(asof)>datetime.combine(date.fromisoformat(trade_date),time(9,30),BJ):raise ValueError('开盘参考价不能使用开盘后的公告')
    amount=sum((Decimal(e.price_adjustment_per_share) for e in visible_events(events,asof,mode=mode)
                if e.ex_date==trade_date and e.symbol==symbol),Decimal(0))
    value=Decimal(str(previous_close))-amount
    if value<=0:raise ValueError('除息参考价无效')
    return money(value)


def gross_return_index(bars, events, *, mode='as_recorded'):
    if not bars:return []
    symbols={e.symbol for e in events}
    if len(symbols)>1:raise ValueError('总收益序列须逐证券计算')
    index=Decimal(1);rows=[];previous=None;applied={}
    for bar in bars:
        asof=datetime.combine(date.fromisoformat(bar['date']),time(15,0),BJ).isoformat()
        visible=visible_events(events,asof,mode=mode)
        for event in visible:
            if event.event_id in applied and event.fingerprint!=applied[event.event_id]:raise ValueError('已使用的分红发生修订，须另行重建版本')
            if event.ex_date==bar['date']:applied[event.event_id]=event.fingerprint
        cash=sum((Decimal(e.cash_per_share_gross) for e in visible if e.ex_date==bar['date']),Decimal(0))
        close=Decimal(str(bar['close']))
        if close<=0:raise ValueError('收盘价无效')
        if previous is not None:index*= (close+cash)/previous
        rows.append({'date':bar['date'],'gross_return_index':float(index),'cash_per_share_gross':str(cash)})
        previous=close
    return rows


def fixed_path_overlay(dataset, symbols, run, events, source_bytes, *, starts_flat, mode='as_recorded'):
    """Reconcile entitlements against actual sealed fills, without reinvestment.

    Register-day close determines ownership; ex-day creates a receivable; pay-day
    transfers that receivable to gross cash. Tax is unresolved and neither cash
    nor adjusted NAV is permitted to drive trades, selection or a risk reset.
    """
    if starts_flat is not True:raise ValueError('本核验器仅支持明确空仓起始的封存实验')
    calendar=validate_dataset(dataset);validate_evidence(events,source_bytes)
    if set(symbols)-set(dataset) or any(e.symbol not in symbols for e in events):raise ValueError('证券范围不一致')
    if len(set(symbols))!=len(symbols):raise ValueError('证券不能重复')
    curve=run['curve']
    if not curve:raise ValueError('缺少已封存净值')
    curve_dates=[r['date'] for r in curve]
    if curve_dates!=sorted(set(curve_dates)) or set(curve_dates)-set(calendar):raise ValueError('净值日期重复、乱序或无原始行情')
    first,last=curve[0]['date'],curve[-1]['date']
    if any(not first<=f['date']<=last for f in run['fills']):raise ValueError('成交超出净值区间')
    dates=sorted(set([r['date'] for r in curve]+[d for e in events for d in (e.record_date,e.ex_date,e.pay_date) if first<=d<=last]))
    original={r['date']:r for r in curve};trades={}
    ids=set()
    for f in run['fills']:
        if f['id'] in ids:raise ValueError('成交标识重复')
        if f['symbol'] not in symbols or f['side'] not in ('buy','sell') or type(f['qty']) is not int or f['qty']<=0:
            raise ValueError('成交记录无效')
        ids.add(f['id']);trades.setdefault(f['date'],[]).append(f)
    qty={s:0 for s in symbols};closes={};entitlements={};paid=Decimal(0);receivable=Decimal(0)
    entries=[];output=[]
    for d in dates:
        asof=datetime.combine(date.fromisoformat(d),time(23,59,59),BJ).isoformat()
        visible=visible_events(events,asof,mode=mode)
        for event in visible:
            key=event.event_id
            if first<=event.ex_date<d and key not in entitlements:
                raise ValueError('迟到的历史公司行为须单独重建，不能悄悄漏记')
            if key in entitlements and entitlements[key]['fingerprint']!=event.fingerprint:
                raise ValueError('已入账事件出现修订，须另立更正账，不得改写历史')
            if event.ex_date==d and key not in entitlements:
                if event.record_date<first:
                    # This run starts flat, so prior registration creates no right.
                    held=0
                else:
                    if event.record_date not in closes:raise ValueError('缺少登记日持仓快照')
                    held=closes[event.record_date][event.symbol]
                amount=money(Decimal(held)*Decimal(event.cash_per_share_gross))
                entitlements[key]={'fingerprint':event.fingerprint,'qty':held,'amount':str(amount),'paid':False}
                receivable+=amount
                entries.append({'event':key,'date':d,'kind':'gross_receivable','amount':str(amount),'record_close_qty':held})
        for f in trades.get(d,[]):
            qty[f['symbol']]+=f['qty']*(1 if f['side']=='buy' else -1)
            if qty[f['symbol']]<0:raise ValueError('缺少期初持仓或存在卖空，禁止猜测登记权益')
        for event in visible:
            key=event.event_id
            if event.pay_date==d and key in entitlements and not entitlements[key]['paid']:
                amount=Decimal(entitlements[key]['amount']);receivable-=amount;paid+=amount
                entitlements[key]['paid']=True
                entries.append({'event':key,'date':d,'kind':'gross_payment','amount':str(amount)})
        closes[d]=dict(qty)
        if d in original:
            nav=Decimal(str(original[d]['nav']))
            output.append({'date':d,'sealed_nav':str(nav),'unpaid_gross_receivable':str(receivable),
                'paid_cash_gross':str(paid),'fixed_path_nav_before_dividend_tax':str(money(nav+paid+receivable))})
    return {'mode':mode,'entries':entries,'curve':output,'entitlements':entitlements,
            'gross_cash_paid':str(paid),'gross_receivable':str(receivable),
            'source_hashes':sorted(source_bytes),'event_snapshot_digest':digest([asdict(e) for e in events]),
            'tax_model':'unresolved_not_net_return','action_coverage_complete':False,
            'is_strategy_replay':False,'usable_for_gate':False,'promotion_allowed':False}
