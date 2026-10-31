#!/usr/bin/env python3
"""Offline cross-interface quote comparison plus identical declared-fill replay.

Reject any compared quote difference before replaying. No source selection,
fitted tolerance/unit conversion, network, strategies, orders or promotion.
"""
import argparse,hashlib,importlib.util,json,re,subprocess,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from deepfocus_api import quant_capitalization_cross_source as cross,quant_capitalization_book as book,ai_fund_market as market
spec=importlib.util.spec_from_file_location('input_reader',ROOT/'scripts/quant-share-inventory-review.py');inputs=importlib.util.module_from_spec(spec);spec.loader.exec_module(inputs)
URL='https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol=sz300750&scale=240&ma=no&datalen=1023'
PINS={'cross_reader':'backend/deepfocus_api/quant_capitalization_cross_source.py','raw_price_reader':'backend/deepfocus_api/quant_capitalization_prices.py',
      'protocol_reader':'backend/deepfocus_api/quant_security_status.py','sina_parser':'backend/deepfocus_api/ai_fund_market.py',
      'primary_reviewer':'scripts/quant-capitalization-real-price-review.py','ledger_reviewer':'scripts/quant-capitalization-review.py','input_reader':'scripts/quant-share-inventory-review.py'}


def pins():return {name:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for name,path in PINS.items()}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('sina','baostock','accounting','workspace'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    if any(not value.is_absolute() for value in (a.sina,a.baostock,a.accounting,a.workspace)) or a.workspace.exists() or not a.workspace.parent.is_dir():p.error('Absolute retained inputs and NEW workspace required')
    w=a.workspace;w.mkdir(mode=0o700);stage='input'
    frozen={};frozen_bytes=0
    def freeze(name,body):
        nonlocal frozen_bytes
        frozen_bytes+=len(body)
        if frozen_bytes>96*1024**2:raise ValueError('Frozen input bundle exceeds 96MiB')
        path=w/name;path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('xb') as stream:stream.write(body)
        path.chmod(0o400);frozen[name]={'sha256':hashlib.sha256(body).hexdigest(),'bytes':len(body)}
        return path
    def verify_frozen():
        for name,entry in frozen.items():
            body=inputs.bounded_read(w/name,entry['bytes'])
            if len(body)!=entry['bytes'] or hashlib.sha256(body).hexdigest()!=entry['sha256']:raise ValueError('Owned frozen input changed')
    r={'schema':'capitalization-cross-source-review-v2','status':'rejected','reader_source_pins':pins(),'source_pins':book.LOADED_PINS,
       'reviewer_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'network_requests':0,'strategy_replays':0,'accounting_replays':0,
       'production_writes':0,'promotion_allowed':False,'commercial_ready':False,'model_positions_and_fills':True,
       'broker_clearing_verified':False,'data_rights_verified':False,'upstream_feed_independence_verified':False,
       'input_snapshot_complete':False,'frozen_files_verified':False}
    try:
        raw={name:inputs.bounded_read(path,2*1024**2) for name,path in [('sina-manifest',a.sina/'manifest.json'),('bao-manifest',a.baostock/'manifest.json'),('prior-review',a.accounting/'result.json')]}
        r['input_sha256']={n:hashlib.sha256(v).hexdigest() for n,v in raw.items()}
        for n,v in raw.items():freeze(n+'.json',v)
        m=inputs.load(raw['sina-manifest']);b=inputs.load(raw['bao-manifest']);prior=inputs.load(raw['prior-review'])
        if (not isinstance(m,dict) or m.get('schema')!='capitalization-sina-raw-capture-v1' or m.get('errors')!=[] or m.get('status')!=200
                or m.get('request_url')!=URL or m.get('final_url')!=URL or m.get('symbol')!='300750' or m.get('provider')!='sina' or m.get('price_adjustment')!='none'):
            raise ValueError('Secondary capture was unsuccessful or differently scoped')
        if m.get('collector_sha256')!=hashlib.sha256((ROOT/'scripts/capture-quant-capitalization-cross-source.py').read_bytes()).hexdigest() or m.get('parser_sha256')!=r['reader_source_pins']['sina_parser']:
            raise ValueError('Secondary capture source version changed')
        body=inputs.bounded_read(a.sina/'response.bin',market.MAX_BODY)
        if len(body)!=m['bytes'] or hashlib.sha256(body).hexdigest()!=m['response_sha256']:raise ValueError('Secondary response changed')
        sources={name:inputs.bounded_read(a.baostock/name,4*1024**2) for name in ['sdk-response-0.bin','sdk-response-1.bin','sdk-response-2.bin']}
        frozen_bao=w/'frozen/baostock'
        freeze('frozen/baostock/manifest.json',raw['bao-manifest'])
        for name,body_bytes in sources.items():freeze('frozen/baostock/'+name,body_bytes)
        stage='comparison';comparison=cross.compare(b,sources,body,URL,m['observed_at']);inputs.dump(w/'comparison.json',comparison)
        freeze('secondary-response.bin',body)
        r.update(compared_cells=comparison['compared_cells'],mismatch_count=comparison['mismatch_count'],secondary_response_sha256=m['response_sha256'])
        if not comparison['all_matched']:
            r['status']='source_mismatch';raise ValueError('Sources disagree; no accounting replay or source selection performed')
        stage='prior_binding'
        if not isinstance(prior,dict) or prior.get('status')!='raw_price_accounting_reconciled' or prior.get('source_pins')!=book.LOADED_PINS or prior.get('reviewer_sha256')!=r['reader_source_pins']['primary_reviewer']:
            raise ValueError('Prior accounting review has incompatible identity')
        prior_inputs={}
        for key,file in [('manifest','manifest.json'),('action-plan','action-plan.json'),('facts','facts.json')]:
            original=inputs.bounded_read(a.accounting/file,2*1024**2)
            if hashlib.sha256(original).hexdigest()!=prior['input_sha256'][key]:raise ValueError('Prior input snapshot changed')
            prior_inputs[key]=original;freeze('frozen/prior/'+file,original)
        if prior['input_sha256']['manifest']!=r['input_sha256']['bao-manifest']:raise ValueError('Prior review belongs to another price capture')
        reference={}
        for name,limit in [('daily-valuations.json',2*1024**2),('last-closed-checkpoint.json',16*1024**2),('result.json',2*1024**2)]:
            body_bytes=inputs.bounded_read(a.accounting/'ledger-review'/name,limit);reference[name]=inputs.load(body_bytes)
            freeze('frozen/prior-ledger/'+name,body_bytes)
        prior_state=book.restore(json.dumps(reference['last-closed-checkpoint.json']),scope=inputs.load(prior_inputs['action-plan'])['scope'])
        prior_plan=inputs.bounded_read(a.accounting/'generated-plan.json',2*1024**2);freeze('frozen/prior/generated-plan.json',prior_plan)
        ledger_report=reference['result.json']
        if (ledger_report.get('status')!='composite_quantity_valuation_completed' or ledger_report.get('source_pins')!=book.LOADED_PINS
                or ledger_report.get('last_closed_digest')!=prior_state['state_digest'] or ledger_report.get('plan_sha256')!=hashlib.sha256(prior_plan).hexdigest()):
            raise ValueError('Prior closed reference graph differs')
        stage='source_freeze';materialized=inputs.load(prior_inputs['action-plan']);original_sources=materialized['sources']
        if not isinstance(original_sources,dict) or not 1<=len(original_sources)<=2:raise ValueError('Bounded issuer source inventory required')
        materialized['sources']={}
        for sha,name in original_sources.items():
            if not isinstance(sha,str) or not re.fullmatch('[0-9a-f]{64}',sha):raise ValueError('Invalid original source identity')
            path=Path(name)
            if not path.is_absolute():raise ValueError('Absolute retained issuer source required')
            source_bytes=inputs.bounded_read(path,10*1024**2);book.shares.pdf_source(sha,{sha:source_bytes})
            materialized['sources'][sha]=str(freeze('frozen/evidence/'+sha+'.pdf',source_bytes))
        action_bytes=json.dumps(materialized,ensure_ascii=False,indent=2,allow_nan=False).encode()
        action_path=freeze('frozen/materialized-action-plan.json',action_bytes)
        r.update(input_snapshot_complete=True,primary_capture_snapshot_sha256=hashlib.sha256(raw['bao-manifest']).hexdigest(),
                 original_action_plan_sha256=hashlib.sha256(prior_inputs['action-plan']).hexdigest(),materialized_action_plan_sha256=hashlib.sha256(action_bytes).hexdigest(),
                 source_materialization='Only PDF file paths replaced by owned byte-identical snapshots; original plan retained')
        stage='frozen_integrity';verify_frozen()
        stage='primary_rebuild';cmd=[sys.executable,str(ROOT/'scripts/quant-capitalization-real-price-review.py'),'--capture',str(frozen_bao),'--action-plan',str(action_path),'--facts',str(w/'frozen/prior/facts.json'),'--workspace',str(w/'primary-rebuild')]
        child=subprocess.run(cmd,capture_output=True,text=True,timeout=30);(w/'primary-command.log').write_text(child.stdout+child.stderr)
        if child.returncode!=0:raise ValueError('Primary accounting rebuild did not complete')
        r['accounting_replays']=1
        stage='frozen_integrity';verify_frozen()
        rebuilt=inputs.load(inputs.bounded_read(w/'primary-rebuild/result.json',2*1024**2))
        if rebuilt['input_sha256']!={'manifest':r['primary_capture_snapshot_sha256'],'action-plan':r['materialized_action_plan_sha256'],'facts':hashlib.sha256(prior_inputs['facts']).hexdigest()}:
            raise ValueError('Primary child consumed a different frozen input revision')
        normalized=inputs.load(inputs.bounded_read(w/'primary-rebuild/normalized-prices.json',2*1024**2))
        if normalized!=cross.primary.normalize(b,sources):raise ValueError('Primary child prices differ from compared snapshot')
        plan_bytes=inputs.bounded_read(w/'primary-rebuild/generated-plan.json',2*1024**2)
        rebuilt_ledger=inputs.load(inputs.bounded_read(w/'primary-rebuild/ledger-review/result.json',2*1024**2))
        if rebuilt_ledger['plan_sha256']!=hashlib.sha256(plan_bytes).hexdigest():raise ValueError('Primary generated plan changed')
        plan=inputs.load(plan_bytes);bydate={v['date']:v for v in comparison['secondary_sample']}
        for day in plan['days']:
            row=bydate[day['date']];day['open_prices']['300750']=row['open'];day['close_prices']['300750']=row['close']
            for fill in day['fills']:fill['price']=row['open']
        freeze('secondary-plan.json',json.dumps(plan,ensure_ascii=False,indent=2,allow_nan=False).encode())
        stage='frozen_integrity';verify_frozen();stage='secondary_rebuild'
        child=subprocess.run([sys.executable,str(ROOT/'scripts/quant-capitalization-review.py'),'--plan',str(w/'secondary-plan.json'),'--workspace',str(w/'secondary-rebuild')],capture_output=True,text=True,timeout=30)
        (w/'secondary-command.log').write_text(child.stdout+child.stderr)
        if child.returncode!=0:raise ValueError('Secondary accounting rebuild did not complete')
        r['accounting_replays']=2;stage='frozen_integrity';verify_frozen();stage='state_equality'
        values=[reference['daily-valuations.json']]+[inputs.load(inputs.bounded_read(path,2*1024**2)) for path in [w/'primary-rebuild/ledger-review/daily-valuations.json',w/'secondary-rebuild/daily-valuations.json']]
        states=[reference['last-closed-checkpoint.json']]+[inputs.load(inputs.bounded_read(path,16*1024**2)) for path in [w/'primary-rebuild/ledger-review/last-closed-checkpoint.json',w/'secondary-rebuild/last-closed-checkpoint.json']]
        for state in states:book.restore(json.dumps(state),scope=plan['scope'])
        if not values[0]==values[1]==values[2] or not states[0]==states[1]==states[2]:raise ValueError('Cross-source accounting differs; original outputs retained')
        if pins()!=r['reader_source_pins'] or r['reviewer_sha256']!=hashlib.sha256(Path(__file__).read_bytes()).hexdigest():raise ValueError('Reviewer source changed during run')
        r.update(status='cross_source_accounting_equal',five_day_prices_and_volume_exact=True,valuation_phases_equal=10,
                 final_checkpoint_equal=True,final_checkpoint_digest=states[-1]['state_digest'],tolerances={'price':'0','share_volume':'0'},
                 source_selection_performed=False,unit_conversion_fitted=False,uncompared=comparison['uncompared'],frozen_files_verified=True)
    except (ValueError,TypeError,KeyError,OSError,subprocess.TimeoutExpired) as exc:r.update(failed_stage=stage,error_type=type(exc).__name__,error=str(exc))
    r['frozen_file_sha256']={name:entry['sha256'] for name,entry in frozen.items()}
    r['frozen_input_bundle_bytes']=frozen_bytes;r['frozen_input_digest']=book.shares.digest(frozen)
    inputs.dump(w/'result.json',r);print(json.dumps({'status':r['status'],'mismatch_count':r.get('mismatch_count'),'failed_stage':r.get('failed_stage')}))
    return 0 if r['status']=='cross_source_accounting_equal' else 2


if __name__=='__main__':raise SystemExit(main())
