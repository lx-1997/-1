#!/usr/bin/env python3
"""Register/run a declared tick-rounded standard-band diagnostic of immutable original experiments."""
import argparse, hashlib, json, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend'))
from deepfocus_api import quant_price_band as audit, quant_research_engine as engine
from deepfocus_api.quant_research_analysis import replay_oos

FILES = ['backend/deepfocus_api/'+name for name in ['quant_price_band.py', 'quant_research_engine.py',
         'quant_research_analysis.py', 'ai_fund_ledger.py', 'ai_fund_policy.py']]+['scripts/quant-price-band-review.py']
RULE_EVIDENCE={'url':'https://www.sse.com.cn/lawandrules/sselawsrules2025/repeal/rules/c/c_20250612_10824490.shtml',
               'retained_docx_sha256':'7aa2319f6dcf597be1e86b3b69d7c2ad0e6acb2a5d0cc6be48a01af602fded40',
               'clauses':['3.3.11','3.3.17'],'scope':'arithmetic_review_only_not_full_historical_applicability'}


def pins():
    return {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in FILES}


def read(path, limit):
    with path.open('rb') as stream: body = stream.read(limit+1)
    if len(body)>limit: raise ValueError('Input exceeds byte limit')
    return body


def audit_path(data,path,scenario):
    from collections import Counter
    from fractions import Fraction as F
    rows=[];controls=[];counts=Counter()
    for symbol,pct in scenario['percentages'].items():
        if symbol not in data:continue
        bars=data[symbol]['bars']
        for previous,current in zip(bars,bars[1:]):
            band=audit.calculate(str(previous['close']),pct,tick=scenario['tick'],regime='declared_standard')
            naive={'lower':str(round(previous['close']*(1-float(pct)/100),2)), 'upper':str(round(previous['close']*(1+float(pct)/100),2))}
            mismatched=[key for key in ['lower','upper'] if F(naive[key])!=F(band[key])]
            if mismatched:controls.append({'symbol':symbol,'date':current['date'],'reference':str(previous['close']),'fields':mismatched,'naive':naive,'exact':{'lower':band['lower'],'upper':band['upper']}})
            flags=[key for key in ['open','high','low','close'] if not audit.check_price(str(current[key]),band)['within_declared_arithmetic_band']]
            rows.append({'symbol':symbol,'date':current['date'],'reference':str(previous['close']),'percentage':pct,'lower':band['lower'],'upper':band['upper'],'outside_fields':flags})
            counts.update(flags)
    maps={(row['symbol'],row['date']):row for row in rows};fills=[]
    for fill in path['fills']:
        row=maps[(fill['symbol'],fill['date'])];band=audit.calculate(row['reference'],row['percentage'],tick=scenario['tick'],regime='declared_standard')
        fills.append({'date':fill['date'],'symbol':fill['symbol'],'side':fill['side'],'model_cashflow_price':str(fill['price']),**audit.check_price(str(fill['price']),band)})
    return {'transitions':len(rows),'quote_fields_checked':len(rows)*4,'quote_outside_fields':dict(counts),'fill_count':len(fills),'model_prices_outside_declared_band':sum(not f['within_declared_arithmetic_band'] for f in fills),'naive_float_control_differences':len(controls),'naive_control_is_existing_execution_code':False,'rows':rows,'fills':fills,'naive_control_rows':controls,'security_status_and_exchange_reference_verified':False,'execution_verified':False,'promotion_allowed':False}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=['register', 'run'])
    for name in ['input', 'protocol']: p.add_argument('--'+name, type=Path, required=True)
    p.add_argument('--output', type=Path)
    a=p.parse_args()
    destination=a.protocol if a.mode=='register' else a.output
    if destination is None or destination.exists() or not destination.parent.is_dir(): p.error('New output under an existing parent required')
    body=read(a.input, 16*1024**2); experiments=json.loads(body)
    if not isinstance(experiments,list) or not 1<=len(experiments)<=10: raise ValueError('Scoped experiment list required')
    bindings={'rule_evidence':RULE_EVIDENCE,'scenario':{'tick':'0.01','percentages':{'600519':'10','000001':'10','300750':'20','600036':'10','600900':'10','000333':'10'},'reference':'prior_raw_close_assumption_not_exchange_certified'},'input_sha256':hashlib.sha256(body).hexdigest(), 'source_pins':pins(), 'rules':audit.RULES,
              'experiments':[{'id':e['id'],'result_digest':e['result_digest'], 'dataset_digest':engine.digest(e['dataset'])} for e in experiments]}
    if len({e['id'] for e in experiments})!=len(experiments): raise ValueError('Duplicate experiment')
    if any(set(e['request']['symbols'])-set(bindings['scenario']['percentages']) for e in experiments):raise ValueError('Unregistered symbol scenario')
    if a.mode=='register':
        result={'schema':'declared-price-band-review-v1', 'registered_at':datetime.now(timezone.utc).isoformat(),
                'bindings':bindings, 'promotion_allowed':False, 'strategy_searches':0}
    else:
        protocol=json.loads(read(a.protocol, 1024**2))
        if (protocol.get('schema')!='declared-price-band-review-v1' or protocol.get('bindings')!=bindings
                or protocol.get('promotion_allowed') is not False or protocol.get('strategy_searches')!=0
                or datetime.fromisoformat(protocol['registered_at'])>datetime.now(timezone.utc)):
            raise ValueError('Protocol/input/source changed')
        rows=[]
        for e in experiments:
            r,req,data=e['result'],e['request'],e['dataset']
            if engine.digest(r)!=e['result_digest'] or engine.digest(data)!=r['dataset_digest']:
                raise ValueError('Sealed experiment changed')
            reproduced=engine.walk_forward(data,req['symbols'],req['benchmark'],req['capital'])
            if engine.digest(reproduced)!=e['result_digest']: raise ValueError('Original result did not reproduce')
            path=replay_oos(data,req['symbols'],r,req['capital'])
            rows.append({'experiment':e['id'], 'result_digest':e['result_digest'], 'original_reproduced_exactly':True,
                         'original_gate':r['gate'], 'original_oos_metrics':{k:v for k,v in r['oos'].items() if k!='curve'}, 'band_audit':audit_path(data,path,bindings['scenario'])})
        if pins()!=bindings['source_pins'] or hashlib.sha256(read(a.input,16*1024**2)).hexdigest()!=bindings['input_sha256']:
            raise ValueError('Source/input changed during audit')
        result={'schema':'declared-price-band-review-v1', 'protocol':protocol, 'protocol_digest':engine.digest(protocol),
                'results':rows, 'strategy_searches':0, 'promotion_allowed':False, 'commercial_ready':False,
                'production_writes':0, 'execution_verified':False, 'oos_validation':False,
                'limitations':['Seen development data; original raw corporate-action limitations remain',
                               'Declared ordinary scenario and prior raw close only; ex-date exchange reference not certified',
                               'No intraday prints/order book/auction/queue or explicit halt/limit identity verification',
                               'Naive Python round control is not asserted to be the existing execution implementation; no return correction']}
    with destination.open('x') as stream: json.dump(result,stream,ensure_ascii=False,indent=2,allow_nan=False)
    print(json.dumps({'mode':a.mode,'output':str(destination),'digest':engine.digest(result)}))


if __name__=='__main__':
    try: main()
    except (ValueError, TypeError, KeyError, OSError, AssertionError, OverflowError) as exc:
        print(json.dumps({'status':'rejected','error_type':type(exc).__name__,'error':str(exc)}),file=sys.stderr)
        raise SystemExit(2)
