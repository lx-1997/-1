"""Exact calendar-year TTM flow arithmetic at one declared knowledge clock.

This derives amounts, never a signal: matching units/scope do not establish
comparability of accounting policies, consolidation boundaries or restatements.
"""
import copy
from datetime import date
from decimal import Context,localcontext
import hashlib
from pathlib import Path

from . import quant_financial_vintages as vintages
from .quant_research_engine import digest

VERSION='reviewed-financial-flow-arithmetic-v1'
RULES={'calendar':'January_to_December_only','interim_formula':'current_ytd + previous_annual - previous_comparable_ytd',
       'annual_formula':'current_annual_only','knowledge':'all_components_selected_at_same_clock_and_mode',
       'missing':'no_zero_fill_no_old_revision_or_period_fallback','arithmetic':'signed_integer_CNY_cents',
       'comparability':'not_certified_by_arithmetic','strategy_evaluations':0,'promotion_allowed':False}


def _pins():
    return {**vintages.source_pins(),'quant_financial_flows.py':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


LOADED_PINS=_pins()


def require_current_source():
    vintages.require_current_source()
    if _pins()!=LOADED_PINS:raise ValueError('Loaded financial-flow arithmetic source changed')


def source_pins():return dict(LOADED_PINS)


def components(period_end):
    day=date.fromisoformat(period_end)
    if period_end!=day.isoformat() or period_end[-5:] not in ('03-31','06-30','09-30','12-31'):
        raise ValueError('Explicit calendar quarter end required')
    if period_end.endswith('12-31'):return [('current_annual',period_end,1)]
    if day.year<=1:raise ValueError('Prior calendar year unavailable')
    prior=f'{day.year-1:04}'
    return [('current_ytd',period_end,1),('previous_annual',prior+'-12-31',1),
            ('previous_comparable_ytd',prior+period_end[4:],-1)]


def _cents(amount):
    # The source reader already enforces finite, bounded plain CNY strings.
    sign=-1 if amount.startswith('-') else 1
    parts=amount.lstrip('-').split('.')
    value=sign*(int(parts[0])*100+int((parts[1] if len(parts)==2 else '').ljust(2,'0')))
    if abs(value)>10**20:raise ValueError('Source amount exceeds declared CNY bound')
    return value


def _amount(cents):
    sign='-' if cents<0 else '';whole,part=divmod(abs(cents),100)
    return f'{sign}{whole}.{part:02}'


def derive_ttm(manifest,sources,*,issuer_id,symbol,period_end,as_of,mode,required_metrics):
    require_current_source()
    # Own immutable bytes and a private fact snapshot across the component reads.
    incoming=digest(manifest);manifest=copy.deepcopy(manifest);sources=dict(sources)
    evidence_digest=digest({sha:hashlib.sha256(body).hexdigest() for sha,body in sources.items()})
    selected=[]
    for role,end,coefficient in components(period_end):
        # Isolate the legacy reader's Decimal validation from caller precision/traps.
        with localcontext(Context(prec=80)):
            panel=vintages.select_panel(manifest,sources,issuer_id=issuer_id,symbol=symbol,
                period_end=end,as_of=as_of,mode=mode,required_metrics=required_metrics)
        selected.append({'role':role,'coefficient':coefficient,'selection':panel})
    check_unavailable=any(x['selection']['status']=='unavailable' for x in selected)
    check_incomplete=any(x['selection']['status']=='incomplete' for x in selected)
    status='unavailable' if check_unavailable else 'incomplete' if check_incomplete else 'arithmetic_available'
    year=date.fromisoformat(period_end).year
    start=(f'{year:04}-01-01' if period_end.endswith('12-31') else
           f'{year-1:04}'+{'03-31':'-04-01','06-30':'-07-01','09-30':'-10-01'}[period_end[-5:]])
    result={'schema':VERSION,'rules':copy.deepcopy(RULES),'issuer_id':issuer_id,'symbol':symbol,
        'period_start':start,
        'period_end':period_end,'as_of':as_of,'mode':mode,'required_metrics':list(required_metrics),
        'manifest_digest':incoming,'evidence_digest':evidence_digest,'source_pins':source_pins(),
        'components':selected,'status':status,'metrics':None,
        'missing_components':[{'role':x['role'],'period_end':x['selection']['period_end'],
            'status':x['selection']['status'],'missing_metrics':x['selection']['missing_metrics']}
            for x in selected if x['selection']['status']!='available'],
        'accounting_comparability_verified':False,'historical_pit_feed_certified':False,
        'coverage_verified':False,'usable_for_strategy':False,'promotion_allowed':False,'commercial_ready':False,
        'notes':['Derived arithmetic, not a reported TTM fact or strategy signal',
                 'No automatic proof of consistent policies, consolidation perimeter or revision comparability',
                 'No single-quarter/TTM ratio, score, ranking or price return is generated',
                 'as_recorded follows supplied metadata, not external time attestation']}
    if status=='arithmetic_available':
        result['metrics']={metric:_amount(sum(x['coefficient']*_cents(x['selection']['metrics'][metric]) for x in selected))
                           for metric in required_metrics}
    require_current_source()
    if digest(manifest)!=incoming or digest({sha:hashlib.sha256(body).hexdigest() for sha,body in sources.items()})!=evidence_digest:
        raise ValueError('Private financial inputs changed during derivation')
    return result
