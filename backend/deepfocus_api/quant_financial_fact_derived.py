"""Typed flow TTM and explicit stock endpoints from ONE immutable extraction.

Derived reads are separate from extraction source identity. Arithmetic does not
certify accounting comparability, PIT completeness, ratios or strategy quality.
"""
import copy
from datetime import date,timedelta
import hashlib,math,sqlite3
from pathlib import Path
from . import quant_financial_fact_store as facts,quant_financial_flows as flows
from .quant_corporate_actions import timestamp

VERSION='qr-typed-financial-derived-v1'


def _pins():
    return {**facts.LOADED_PINS,**flows.source_pins(),
            'quant_financial_fact_derived.py':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


LOADED_PINS=_pins()


def require_current_source():
    facts.require_current_source();flows.require_current_source()
    if _pins()!=LOADED_PINS:raise ValueError('Loaded typed financial derivation source changed')


def _stock_source(spec,target):
    if not isinstance(spec,dict) or set(spec)!={'report_period_end','column'}:raise ValueError('Explicit stock report and column required')
    flows.components(spec['report_period_end'])
    if not isinstance(spec['column'],str) or spec['column'] not in facts.COLUMNS:raise ValueError('Unsupported asset column')
    end=date.fromisoformat(spec['report_period_end'])
    point=(end if spec['column']=='current_period' else date(end.year-1,12,31))
    if point!=target:raise ValueError('Asset source column does not identify exact flow endpoint')


def _compute(flow_nodes,asset_nodes):
    missing=[n['role'] for n in flow_nodes+asset_nodes if n['fact'] is None or n['fact']['value'] is None]
    if missing:return {'status':'unavailable','ttm_value':None,'endpoint_average_assets':None,'missing_components':missing}
    value=flows._amount(sum(n['coefficient']*flows._cents(n['fact']['value']) for n in flow_nodes))
    mean=None
    if asset_nodes:
        total=sum(flows._cents(n['fact']['value']) for n in asset_nodes)
        # cents endpoints -> numerator/200 yuan; keep half-cent information.
        divisor=math.gcd(total,200);mills=total*5;whole,part=divmod(mills,1000)
        mean={'value_exact_yuan':f'{whole}.{part:03}','fraction_yuan':{'numerator':str(total//divisor),'denominator':str(200//divisor)},
              'half_cent_present':bool(total%2),'method':'simple_endpoint_mean_not_daily_or_quarterly_average',
              'rounded_to_cents':False,'positive_for_possible_future_denominator':total>0}
    return {'status':'arithmetic_available','ttm_value':value,'endpoint_average_assets':mean,'missing_components':[]}


def review(owner,identity,*,issuer_id,symbol,period_end,metric,as_of,mode,asset_endpoints=None):
    require_current_source()
    request=copy.deepcopy({'issuer_id':issuer_id,'symbol':symbol,'period_end':period_end,'metric':metric,
                           'as_of':as_of,'mode':mode,'asset_endpoints':asset_endpoints})
    issuer_id,symbol,period_end,metric,as_of,mode,asset_endpoints=(request[k] for k in
        ('issuer_id','symbol','period_end','metric','as_of','mode','asset_endpoints'))
    if not isinstance(metric,str) or metric not in facts.FLOW_METRICS:raise ValueError('TTM requires a supported flow metric, never a stock')
    schedule=flows.components(period_end);end=date.fromisoformat(period_end)
    start=(date(end.year,1,1) if period_end.endswith('12-31') else date(end.year-1,end.month,end.day)+timedelta(days=1))
    if asset_endpoints is not None:
        if not isinstance(asset_endpoints,dict) or set(asset_endpoints)!={'beginning','ending'}:raise ValueError('Both explicit asset endpoints required')
        _stock_source(asset_endpoints['beginning'],start-timedelta(days=1));_stock_source(asset_endpoints['ending'],end)
    path=facts.base.jobs.db.data_path('.quant_research.sqlite3','DEEPFOCUS_QUANT_RESEARCH_DB_PATH').resolve()
    c=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2);c.row_factory=sqlite3.Row
    try:
        c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
        row=c.execute('SELECT * FROM qr_financial_fact_packet WHERE owner=? AND id=?',(owner,identity)).fetchone()
        if row is None:raise LookupError('Typed extraction not found')
        cache={};packet=facts.verify_row(c,row,cache);parent=facts._parent(c,owner,row['parent_id'],cache)
        sources={sha:c.execute('SELECT body FROM qr_financial_source WHERE owner=? AND sha=?',(owner,sha)).fetchone()['body']
                 for sha in parent['effective_manifest']['source_files']}
        selected={}
        def panel(report_end):
            if report_end not in selected:
                p=facts.base.vintages.select_panel(parent['effective_manifest'],sources,issuer_id=issuer_id,symbol=symbol,
                    period_end=report_end,as_of=as_of,mode=mode,required_metrics=['parent_net_profit'])
                selected[report_end]=p['panel'] if p['status']!='unavailable' else None
            return selected[report_end]
        # Validate identity/mode/future clock even when stored visibility blocks.
        panel(period_end)
        blocked=mode=='as_recorded' and timestamp(as_of)<max(timestamp(parent['stored_visible_at']),timestamp(packet['stored_visible_at']))
        result=None
        if not blocked:
            index={facts._key(f):f for f in packet['input']['facts']}
            def node(role,report_end,which,column,coefficient=None):
                p=panel(report_end);f=None if p is None else index.get((p['document_id'],which,column))
                return {'role':role,'report_period_end':report_end,'column':column,'metric':which,'coefficient':coefficient,
                        'as_of':as_of,'mode':mode,'selected_document':None if p is None else
                            {k:p[k] for k in ('document_id','source_sha256','revision','published_date')},
                        'fact':copy.deepcopy(f),'availability_reason':'report_not_visible' if p is None else
                            'missing_selected_document_fact' if f is None else 'reported_blank' if f['value'] is None else 'reviewed_fact_available'}
            flow_nodes=[node(role,report_end,metric,'current_period',coef) for role,report_end,coef in schedule]
            asset_nodes=[] if asset_endpoints is None else [node(role,spec['report_period_end'],'total_assets',spec['column'])
                for role,spec in [('beginning_assets',asset_endpoints['beginning']),('ending_assets',asset_endpoints['ending'])]]
            # A second semantic guard prevents any future input-contract change
            # from feeding a wrong-date or flow-typed endpoint into arithmetic.
            for n,target in zip(asset_nodes,[start-timedelta(days=1),end]):
                f=n['fact']
                if f is not None and (f['basis']!='point_in_time_stock' or f['period_start'] is not None or f['period_end']!=target.isoformat()):
                    raise ValueError('Selected asset fact is not the requested stock endpoint')
            result={**_compute(flow_nodes,asset_nodes),'flow_components':flow_nodes,'asset_components':asset_nodes}
        out={'schema':VERSION,'request':request,'period_start':start.isoformat(),'period_end':period_end,
             'receipt':facts._summary(row,packet),'status':'unavailable' if blocked else result['status'],
             'availability_reason':'receipt_not_yet_visible' if blocked else 'derived_from_one_verified_extraction' if result['status']=='arithmetic_available' else 'required_component_missing_or_blank',
             'ttm_value':None if blocked else result['ttm_value'],
             'endpoint_average_assets':None if blocked else result['endpoint_average_assets'],'result':result,
             'single_readonly_snapshot':True,'one_extraction_snapshot_only':True,'same_knowledge_clock_for_all_components':True,
             'uses_both_immutable_recording_boundaries':True,'source_pins':dict(LOADED_PINS),
             'ratios_or_scores_generated':False,'accounting_comparability_verified':False,'historical_pit_feed_certified':False,
             'usable_for_strategy':False,'promotion_allowed':False,'commercial_ready':False}
        require_current_source();return out
    finally:c.close()
