"""Strict five-date cross-interface reconciliation, never source selection.

Do not infer upstream independence, data rights, identity certification or unseen
strategy evidence from matching public responses. Keep mismatches, with no fit
of adjustment factors, units, tolerances or preferred source to obtain a pass.
"""
from datetime import datetime,timezone
from decimal import Decimal,Context,localcontext
import json
from . import quant_capitalization_prices as primary, ai_fund_market as secondary
from .quant_share_entitlements import stamp


def compare(bao_manifest,bao_sources,sina_body,sina_url,observed_at):
    with localcontext(Context(prec=50)):
        return _compare(bao_manifest,bao_sources,sina_body,sina_url,observed_at)


def _compare(bao_manifest,bao_sources,body,url,observed_at):
    clock=stamp(observed_at)
    if clock>datetime.now(timezone.utc) or clock<stamp('2023-04-28T16:30:00+08:00'):raise ValueError('Invalid secondary-source observation clock')
    base=primary.normalize(bao_manifest,bao_sources)
    parsed=secondary.parse(body,'300750','sina',url,fetched_at=clock)
    def pairs(items):
        out={}
        for k,v in items:
            if k in out:raise ValueError('Duplicate raw secondary-source key')
            out[k]=v
        return out
    def invalid(value):raise ValueError('Non-finite raw secondary-source number')
    raw=json.loads(body,object_pairs_hook=pairs,parse_float=Decimal,parse_constant=invalid)
    # Read exact raw scalars as well as the existing contract: float projection
    # must not hide a fractional tick or a one-share discrepancy.
    indexed={str(r['day'])[:10]:r for r in raw}
    if set(primary.DAYS)-set(indexed):raise ValueError('Secondary source missing declared sample date')
    rows=[];checks=[]
    for b in base['rows']:
        d=b['date'];r=indexed[d]
        values={k:primary._decimal(str(r[k]),tick=True) for k in ('open','high','low','close')}
        volume=primary._decimal(str(r['volume']))
        if volume!=volume.to_integral_value():raise ValueError('Secondary raw share volume is fractional')
        for key in ('open','high','low','close'):
            delta=values[key]-Decimal(b[key]);checks.append({'date':d,'field':key,'secondary_minus_primary':str(delta),'matched':delta==0})
        delta=volume-Decimal(b['volume_shares']);checks.append({'date':d,'field':'volume_shares','secondary_minus_primary':str(delta),'matched':delta==0})
        rows.append({'date':d,**{k:format(v,'.4f') for k,v in values.items()},'volume_shares':int(volume)})
    return {'schema':'capitalization-cross-interface-comparison-v1','all_matched':all(c['matched'] for c in checks),'compared_cells':len(checks),
            'mismatch_count':sum(not c['matched'] for c in checks),'checks':checks,'secondary_sample':rows,
            'secondary_response_sha256':parsed['meta']['response_sha256'],'secondary_identity_check':parsed['meta']['identity_check'],
            'price_tolerance':'0','share_volume_tolerance':'0','unit_conversion_applied':False,
            'uncompared':['provider_ex_reference','reported_amount','preopen_status','complete_calendar','personal_allotment'],
            'upstream_feed_independence_verified':False,'exchange_certified':False,'data_rights_verified':False,
            'retrospective_only':True,'promotion_allowed':False}
