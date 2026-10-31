#!/usr/bin/env python3
"""Register/run a versioned exact model-cost verification of immutable original experiments."""
import argparse, hashlib, json, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend'))
from deepfocus_api import quant_exact_model_cost as audit, quant_research_engine as engine
from deepfocus_api.quant_research_analysis import replay_oos

FILES = ['backend/deepfocus_api/'+name for name in ['quant_exact_model_cost.py', 'quant_research_engine.py',
         'quant_research_analysis.py', 'ai_fund_ledger.py', 'ai_fund_policy.py']]+['scripts/quant-exact-model-cost-review.py']


def pins():
    return {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in FILES}


def read(path, limit):
    with path.open('rb') as stream: body = stream.read(limit+1)
    if len(body)>limit: raise ValueError('Input exceeds byte limit')
    return body


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
    bindings={'input_sha256':hashlib.sha256(body).hexdigest(), 'source_pins':pins(), 'rules':audit.RULES,
              'experiments':[{'id':e['id'],'result_digest':e['result_digest'], 'dataset_digest':engine.digest(e['dataset'])} for e in experiments]}
    if len({e['id'] for e in experiments})!=len(experiments): raise ValueError('Duplicate experiment')
    if a.mode=='register':
        result={'schema':'exact-model-cost-review-v1', 'registered_at':datetime.now(timezone.utc).isoformat(),
                'bindings':bindings, 'promotion_allowed':False, 'strategy_searches':0}
    else:
        protocol=json.loads(read(a.protocol, 1024**2))
        if (protocol.get('schema')!='exact-model-cost-review-v1' or protocol.get('bindings')!=bindings
                or protocol.get('promotion_allowed') is not False or protocol.get('strategy_searches')!=0
                or datetime.fromisoformat(protocol['registered_at'])>datetime.now(timezone.utc)):
            raise ValueError('Protocol/input/source changed')
        rows=[];failure=None
        for e in experiments:
            r,req,data=e['result'],e['request'],e['dataset']
            if engine.digest(r)!=e['result_digest'] or engine.digest(data)!=r['dataset_digest']:
                raise ValueError('Sealed experiment changed')
            reproduced=engine.walk_forward(data,req['symbols'],req['benchmark'],req['capital'])
            if engine.digest(reproduced)!=e['result_digest']: raise ValueError('Original result did not reproduce')
            path=replay_oos(data,req['symbols'],r,req['capital'])
            row={'experiment':e['id'], 'result_digest':e['result_digest'], 'original_reproduced_exactly':True,
                 'original_gate':r['gate'], 'original_oos_metrics':{k:v for k,v in r['oos'].items() if k!='curve'}}
            try:
                full=audit.run(data,path,req['capital'],scope=e['id'])
                buys=[f['date'] for f in path['fills'] if f['side']=='buy']
                if not buys:raise ValueError('No held-share restore case in this path')
                cut=buys[0]
                prefix=audit.run(data,path,req['capital'],scope=e['id'],through=cut)
                if not prefix['state']['positions']: raise ValueError('Restore must exercise held shares')
                suffix=audit.run(data,path,req['capital'],scope=e['id'],state=json.loads(json.dumps(prefix['state'])))
                if suffix['state']!=full['state'] or any(prefix[k]+suffix[k]!=full[k] for k in ['daily','receipts','legacy_differences']):
                    raise ValueError('Exact model JSON restore differs')
                row['cost_book']=full
                row['restore']={'date':cut,'held_shares':sum(p['qty'] for p in prefix['state']['positions'].values()),'full_state_and_all_outputs_equal':True}

            except ValueError as exc:
                failure={'experiment':e['id'],'stage':'cost_book_reconcile','error_type':type(exc).__name__,
                         'error':str(exc),'details':getattr(exc,'details',{})}
                row['cost_book']=None;row['failure']=failure
            rows.append(row)
            if failure: break
        if pins()!=bindings['source_pins'] or hashlib.sha256(read(a.input,16*1024**2)).hexdigest()!=bindings['input_sha256']:
            raise ValueError('Source/input changed during audit')
        result={'schema':'exact-model-cost-review-v1', 'protocol':protocol, 'protocol_digest':engine.digest(protocol),
                'status':'rejected' if failure else 'exact_conservation_verified_legacy_checked_separately', 'failure':failure,
                'results':rows, 'strategy_searches':0, 'promotion_allowed':False, 'commercial_ready':False,
                'production_writes':0, 'execution_verified':False, 'oos_validation':False,
                'limitations':['Seen development data; original raw corporate-action limitations remain',
                               'Provider daily open is a reference, never certified auction execution',
                               'No intraday prints/order book/auction/queue or explicit halt/limit identity verification',
                               'New model receipt identity; all legacy differences retained, old strict v1 remains rejected']}
    with destination.open('x') as stream: json.dump(result,stream,ensure_ascii=False,indent=2,allow_nan=False)
    print(json.dumps({'mode':a.mode,'output':str(destination),'digest':engine.digest(result)}))
    return 2 if result.get('status')=='rejected' else 0


if __name__=='__main__':
    try: raise SystemExit(main())
    except (ValueError, TypeError, KeyError, OSError, AssertionError, OverflowError) as exc:
        print(json.dumps({'status':'rejected','error_type':type(exc).__name__,'error':str(exc)}),file=sys.stderr)
        raise SystemExit(2)
