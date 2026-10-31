"""Versioned exact model-cost accounting; never upgrades a legacy receipt.

Fractional weighted basis and rounding residuals remain exact. No floats in
accounting arithmetic, no orders, tax inheritance or execution certification.
"""
from collections import defaultdict
from fractions import Fraction as F
import hashlib
import json
import math
from pathlib import Path

from . import quant_research_engine as engine

VERSION = 'exact-model-cost-v1'
LOADED_SHA = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
RULES = {'version':VERSION, 'scope':'fixed_original_seen_path_no_signal_or_order_generation',
         'basis':'exact_rational_weighted_average_model_basis_not_tax_basis',
         'realized_rounding':'per_sale_cent_half_even_with_exact_residual_retained',
         'fee':'original_notional_tariff_cent_half_up_no_fee_base_change',
         'invariants':'exact_cash_plus_unrealized_plus_realized_conservation_every_day',
         'legacy':'record_all_differences_never_relabel_v1_as_passed_or_migrate_implicitly',
         'restore':'JSON_after_first_original_buy_close_with_held_shares_all_outputs_and_state_equal',
         'promotion_allowed':False, 'gates_changed':False}


def rational(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        raise ValueError('Finite model input required')
    return F(str(value))


def rounded(value, *, even=True):
    """Round rational cents to integer cents; negative ties are symmetric."""
    sign = -1 if value < 0 else 1
    q,r=divmod(abs(value.numerator),value.denominator)
    if 2*r>value.denominator or (2*r==value.denominator and (not even or q%2)):q+=1
    return sign*q


def cents(value):
    result=rational(value)*100
    if result.denominator!=1:raise ValueError('Original cash/fee/price must be cent-valued')
    return result.numerator


def pack(value):
    if max(len(str(abs(value.numerator))),len(str(value.denominator)))>1024:
        raise ValueError('Exact basis exceeds bounded fraction size')
    return [str(value.numerator),str(value.denominator)]


def unpack(value):
    if (not isinstance(value,list) or len(value)!=2 or any(not isinstance(v,str) or len(v)>1025 for v in value)):
        raise ValueError('Invalid bounded rational state')
    numerator,denominator=int(value[0]),int(value[1])
    if denominator<=0:raise ValueError('Positive exact denominator required')
    result=F(numerator,denominator)
    if pack(result)!=value:raise ValueError('Rational state must be canonical')
    return result


def seal(state):
    state['state_digest']=engine.digest({k:v for k,v in state.items() if k!='state_digest'})
    return state


def run(dataset,path,capital,*,scope,through=None,state=None):
    engine.validate_dataset(dataset)
    if not isinstance(scope,str) or not 1<=len(scope)<=160 or not isinstance(path,dict):raise ValueError('Explicit model scope required')
    curve,fills=path['curve'],path['fills']
    if not isinstance(curve,list) or not 1<=len(curve)<=20000 or not isinstance(fills,list) or len(fills)>100000:raise ValueError('Bounded nonempty path required')
    days=[r['date'] for r in curve]
    if days!=sorted(set(days)) or (through is not None and through not in days):raise ValueError('Unique ordered review dates required')
    maps={s:{b['date']:b for b in p['bars']} for s,p in dataset.items()}
    if any(any(d not in m for m in maps.values()) for d in days):raise ValueError('Missing original valuation day')
    plan=engine.digest({'data':dataset,'path':path,'capital':capital,'scope':scope,'rules':RULES})
    initial=cents(capital)
    if initial<=0:raise ValueError('Positive initial model cash required')
    byday=defaultdict(list);seen=set();previous=''
    for f in fills:
        identity=engine.digest(f);d,s=f['date'],f['symbol']
        if d not in days or s not in maps or d<previous or identity in seen:raise ValueError('Invalid original fill coverage/order/duplicate')
        prior=[b['date'] for b in dataset[s]['bars'] if b['date']<d]
        if not prior or f.get('signal_date')!=prior[-1]:raise ValueError('Prior completed signal date required')
        seen.add(identity);previous=d;byday[d].append(f)
    if state is None:
        current={'version':VERSION,'source_sha256':LOADED_SHA,'plan_digest':plan,'scope':scope,'cursor':-1,
                 'cash_cents':initial,'positions':{},'realized_exact':pack(F(0)),
                 'realized_rounded_cents_sum':0,'fills_processed':0,'fill_prefix_digest':engine.digest([])}
    else:
        if len(json.dumps(state))>2*1024**2:raise ValueError('State exceeds 2MiB')
        current=json.loads(json.dumps(state))
        if (current.get('version')!=VERSION or current.get('source_sha256')!=LOADED_SHA or current.get('plan_digest')!=plan
                or current.get('scope')!=scope or current.get('state_digest')!=engine.digest({k:v for k,v in current.items() if k!='state_digest'})):
            raise ValueError('State version/source/plan/scope/digest mismatch; no implicit migration')
        cursor=current['cursor']
        if type(cursor)is not int or not 0<=cursor<len(days):raise ValueError('Invalid restored cursor')
        prefix=[f for f in fills if f['date']<=days[cursor]]
        if type(current['fills_processed'])is not int or current['fills_processed']!=len(prefix) or current['fill_prefix_digest']!=engine.digest(prefix):raise ValueError('Restored fill prefix differs')
        if current['cash_cents']!=cents(curve[cursor]['cash']):raise ValueError('Restored cash differs')
    cash=current['cash_cents'];positions={s:{'qty':p['qty'],'basis':unpack(p['basis'])} for s,p in current['positions'].items()}
    exact=unpack(current['realized_exact']);rounded_sum=current['realized_rounded_cents_sum'];count=current['fills_processed']
    if type(cash)is not int or cash<0 or type(rounded_sum)is not int or any(s not in maps or type(p['qty'])is not int or p['qty']<=0 or p['basis']<=0 for s,p in positions.items()):raise ValueError('Invalid restored model quantities/money')
    daily=[];receipts=[];differences=[]
    stop=days.index(through) if through is not None else len(days)-1
    if stop<current['cursor']:raise ValueError('Cannot rewind a closed model checkpoint')
    for index in range(current['cursor']+1,stop+1):
        d=days[index]
        for f in byday[d]:
            s,n,side=f['symbol'],f['qty'],f['side']
            if type(n)is not int or n<=0 or side not in ('buy','sell'):raise ValueError('Positive integer buy/sell required')
            reference=rational(maps[s][d]['open']);price=F(cents(f['price']),100);charge=cents(f['fee'])
            sign=1 if side=='buy' else -1
            unrounded=reference*(1+F(sign,1000))*100
            expected=-(-unrounded.numerator//unrounded.denominator) if sign>0 else unrounded.numerator//unrounded.denominator
            amount=n*price;tariff=max(F(5),amount/F(4000))+amount/F(100000)+(amount/F(2000) if side=='sell' else 0)
            if cents(f['price'])!=expected or charge!=rounded(tariff*100,even=False):raise ValueError('Original model price/fee differs from exact declared contract')
            slip=n*reference/F(1000);tick=sign*n*(price-reference)-slip
            delta=-sign*amount-F(charge,100)
            if tick<0 or delta!=-sign*n*reference-slip-tick-F(charge,100) or (delta*100).denominator!=1:raise ValueError('Cost cash identity differs')
            p=positions.get(s,{'qty':0,'basis':F(0)});realized=None;allocated=F(0)
            if side=='buy':
                if f.get('realized_pnl')is not None:raise ValueError('Buy cannot realize model profit')
                positions[s]={'qty':p['qty']+n,'basis':p['basis']+amount+F(charge,100)}
            else:
                if n>p['qty']:raise ValueError('Sell exceeds exact model holding')
                allocated=p['basis']*F(n,p['qty']);profit=amount-F(charge,100)-allocated
                realized=rounded(profit*100);exact+=profit;rounded_sum+=realized
                if n==p['qty']:positions.pop(s)
                else:positions[s]={'qty':p['qty']-n,'basis':p['basis']-allocated}
                legacy=cents(f['realized_pnl'])
                if legacy!=realized:differences.append({'date':d,'symbol':s,'fill_digest':engine.digest(f),'legacy_cents':legacy,'exact_model_rounded_cents':realized,'legacy_minus_exact_cents':legacy-realized})
            cash+=(delta*100).numerator;count+=1
            if cash<0:raise ValueError('Negative exact model cash')
            receipts.append({'date':d,'symbol':s,'side':side,'qty':n,'fill_digest':engine.digest(f),
                             'reference_notional':pack(n*reference),'adverse_slippage':pack(slip),'adverse_tick':pack(tick),
                             'original_fee_cents':charge,'cash_delta_cents':(delta*100).numerator,'cash_after_cents':cash,
                             'allocated_model_basis':pack(allocated),'exact_model_realized_cents':realized})
        marked=sum((p['qty']*rational(maps[s][d]['close']) for s,p in positions.items()),F(0))
        unrealized=marked-sum((p['basis'] for p in positions.values()),F(0));economic=F(cash-initial,100)+marked
        residual=exact-F(rounded_sum,100)
        if economic!=exact+unrealized or exact!=F(rounded_sum,100)+residual:raise ValueError('Exact PnL/basis conservation failed')
        nav=rounded(F(cash)+marked*100)
        if cash!=cents(curve[index]['cash']) or nav!=cents(curve[index]['nav']):raise ValueError('Original daily cash/NAV changed')
        daily.append({'date':d,'cash_cents':cash,'nav_cents':nav,'economic_pnl':pack(economic),
                      'realized_exact':pack(exact),'realized_rounded_cents_sum':rounded_sum,'rounding_residual':pack(residual),
                      'unrealized_exact':pack(unrealized),'model_quantities':{s:p['qty'] for s,p in positions.items()}})
    if stop==len(days)-1 and (cash!=cents(path['state']['cash']) or {s:p['qty'] for s,p in positions.items()}!={s:p['qty'] for s,p in path['state']['positions'].items()}):raise ValueError('Original final quantities/cash changed')
    current.update(cursor=stop,cash_cents=cash,positions={s:{'qty':p['qty'],'basis':pack(p['basis'])} for s,p in positions.items()},
                   realized_exact=pack(exact),realized_rounded_cents_sum=rounded_sum,
                   fills_processed=count,fill_prefix_digest=engine.digest([f for f in fills if f['date']<=days[stop]]))
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=LOADED_SHA:raise ValueError('Exact model source changed')
    return {'version':VERSION,'state':seal(current),'daily':daily,'receipts':receipts,'legacy_differences':differences,
            'exact_conservation_verified':True,'legacy_reconciliation_passed':not differences if state is None else None,
            'legacy_receipts_rewritten':False,'promotion_allowed':False,'execution_verified':False,'tax_basis_verified':False}
