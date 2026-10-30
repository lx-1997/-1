"""Offline, audit-first development study and locked prospective evaluation.

No network, production database, broker, or promotion side effects.
"""
import copy
import hashlib
import random
from datetime import date
from pathlib import Path
from . import quant_research_engine as e
from .quant_research_analysis import diagnose, attribution, replay_oos
from .quant_research_candidates import PROFILES


def source_pins():
    files=('quant_research_engine.py','quant_research_candidates.py','quant_research_analysis.py',
           'quant_research_study.py','ai_fund_ledger.py','ai_fund_policy.py')
    return {f:hashlib.sha256(Path(__file__).with_name(f).read_bytes()).hexdigest() for f in files}


def freeze_parameters(data, symbols, benchmark, capital, profile):
    dates=e.validate_dataset(data);trials=[]
    start=dates[max(0,len(dates)-252)] if profile!='legacy' else None
    kwargs={} if profile=='legacy' else {'profile':profile,'benchmark':benchmark}
    for p in e.CANDIDATES:
        m=e.replay(data,symbols,p,start=start,capital=capital,**kwargs)['metrics']
        trials.append({'parameters':p,'metrics':m,'score':m['return_pct']+m['max_drawdown_pct']})
    winner=max(trials,key=lambda t:(t['score'],-t['parameters']['lookback'],-t['parameters']['threshold']))
    return {'parameters':winner['parameters'],'training_start':start,'training_end':dates[-1],'trials':trials}


def develop(experiments, protocol):
    assert protocol['profiles_in_declared_order']==list(PROFILES)
    assert [x['request']['symbols'] for x in experiments]==protocol['historical_baskets_fixed']
    rows=[];baselines=[]
    for experiment in experiments:
        baseline=diagnose(experiment);baselines.append(baseline)
        req=experiment['request'];data=experiment['dataset']
        for profile in PROFILES:
            r=e.walk_forward(data,req['symbols'],req['benchmark'],req['capital'],profile=profile)
            run=replay_oos(data,req['symbols'],r,req['capital'],profile=profile,benchmark=req['benchmark'])
            a=attribution(data,run,req['capital']);a.pop('daily')
            rows.append({'experiment':experiment['id'],'profile':profile,
                         'oos':{k:v for k,v in r['oos'].items() if k!='curve'},
                         'holdout':r['holdout']['metrics'],'gate':r['gate'],'attribution':a,
                         'result_digest':e.digest(r),'result':r})
    ids=[x['id'] for x in experiments];eligible=[]
    for profile in PROFILES[1:]:
        pairs=[(next(r for r in rows if r['profile']==profile and r['experiment']==i),
                next(r for r in rows if r['profile']=='legacy' and r['experiment']==i)) for i in ids]
        def later(a,b):
            pa=a['attribution']['first_risk_pause'];pb=b['attribution']['first_risk_pause']
            return pa is None or (pb is not None and pa['date']>=pb['date'])
        if all(a['attribution']['fixed_path_cost_addback_pct']<b['attribution']['fixed_path_cost_addback_pct'] and later(a,b) for a,b in pairs):
            eligible.append((min(a['oos']['excess_pct'] for a,_ in pairs),profile))
    eligible.sort(key=lambda p:(-p[0],PROFILES.index(p[1])))
    chosen=eligible[0][1] if eligible else None
    manifest={'protocol':copy.deepcopy(protocol),'protocol_digest':e.digest(protocol),'sources':source_pins(),
              'status':'awaiting_unseen_forward_data' if chosen else 'no_eligible_challenger',
              'profile':chosen,'promotion_allowed':False,'baskets':[]}
    for x in experiments:
        req=x['request'];data=x['dataset']
        manifest['baskets'].append({'symbols':req['symbols'],'benchmark':req['benchmark'],'capital':req['capital'],
             'sealed_data_digest':e.digest(data),'sealed_cutoff':e.validate_dataset(data)[-1],
             'baseline':freeze_parameters(data,req['symbols'],req['benchmark'],req['capital'],'legacy'),
             'challenger':freeze_parameters(data,req['symbols'],req['benchmark'],req['capital'],chosen) if chosen else None})
    manifest['manifest_digest']=e.digest(manifest)
    return {'baseline_diagnoses':baselines,'variants':rows,'eligible':eligible,'forward_manifest':manifest}


def paired_block_interval(challenger, baseline, *, draws=2000):
    """Fixed-seed moving block bootstrap of paired daily return differences.

    This is a strength screen, not a claim to remove selection bias or prove alpha.
    """
    assert [r['date'] for r in challenger]==[r['date'] for r in baseline]
    def returns(curve):
        return [r['nav']/p['nav']-1 for p,r in zip(curve,curve[1:])]
    # Initial daily return is supplied by the caller using an initial NAV row.
    differences=[a-b for a,b in zip(returns(challenger),returns(baseline))]
    if len(differences)<20: return None
    rng=random.Random(20261030);n=len(differences);block=5;means=[]
    for _ in range(draws):
        sample=[]
        while len(sample)<n:
            start=rng.randrange(n-block+1);sample.extend(differences[start:start+block])
        means.append(sum(sample[:n])/n)
    means.sort()
    return {'lower':means[int(draws*.025)],'upper':means[min(draws-1,int(draws*.975))],
            'units':'mean_daily_return_difference','block_sessions':5,'draws':draws,'seed':20261030}


def validate_forward(manifest, sealed_datasets, extended_datasets, *, completed_through):
    """Fail closed before 152 genuinely new common sessions. Never refit parameters."""
    supplied={k:v for k,v in manifest.items() if k!='manifest_digest'}
    if e.digest(supplied)!=manifest['manifest_digest']:raise ValueError('冻结清单已修改')
    if manifest['sources']!=source_pins():raise ValueError('研究源码已改变，不能冒充冻结版本验证')
    if not manifest['profile']:raise ValueError('没有预先登记的合格研究候选')
    date.fromisoformat(completed_through)
    if not (len(sealed_datasets)==len(extended_datasets)==len(manifest['baskets'])):
        raise ValueError('研究组数量不一致')
    results=[];required=manifest['protocol']['new_validation']['minimum_common_completed_sessions']
    for item,old,data in zip(manifest['baskets'],sealed_datasets,extended_datasets):
        if e.digest(old)!=item['sealed_data_digest']:raise ValueError('历史封存快照不匹配')
        if set(old)!=set(data):raise ValueError('标的或基准改变')
        for s in old:
            oldbars={b['date']:b for b in old[s]['bars']};newbars={b['date']:b for b in data[s]['bars']}
            if any(newbars.get(d)!=b for d,b in oldbars.items()):raise ValueError('历史行情修订或丢失，须独立核验')
        after=max(item['sealed_cutoff'],manifest['protocol']['seen_through'])
        dates=[d for d in e.validate_dataset(data) if after<d<=completed_through]
        if len(dates)<required:
            results.append({'symbols':item['symbols'],'status':'insufficient_unseen_data','sessions':len(dates),'required':required,'passed':False})
            continue
        dates=dates[:required];end=dates[-1]
        # Bound all inputs to the registered interval; late extra data does not
        # change a once-completed evaluation or its dataset fingerprint.
        data={s:{**p,'bars':[b for b in p['bars'] if b['date']<=end]} for s,p in data.items()}
        base_params=item['baseline']['parameters'];new_params=item['challenger']['parameters']
        runs={}
        for name,profile,params in [('baseline','legacy',base_params),('challenger',manifest['profile'],new_params)]:
            kwargs={} if profile=='legacy' else {'profile':profile,'benchmark':item['benchmark']}
            # Schedule is anchored to sealed history length, not availability
            # of future provider rows. Full-prefix replay and append agree.
            oos=e.replay(data,item['symbols'],params,start=dates[0],end=dates[83],capital=item['capital'],**kwargs)
            final=e.replay(data,item['symbols'],params,start=dates[89],end=end,capital=item['capital'],**kwargs)
            base=e.benchmark_return(data,item['benchmark'],[r['date'] for r in final['curve']])
            capital=item['capital'];relative=[]
            for run in (oos,final):
                previous=capital
                for row in run['curve']:
                    relative.append({'date':row['date'],'return':row['nav']/previous-1});previous=row['nav']
            runs[name]={'oos':oos['metrics'],'holdout':{**final['metrics'],'excess_pct':final['metrics']['return_pct']-base},
                        'sell_fills':sum(f['side']=='sell' for run in (oos,final) for f in run['fills']),
                        'paired_returns':relative,'end_state_oos':oos['state'],'end_state_holdout':final['state']}
        a,b=runs['challenger'],runs['baseline']
        # Two independent phases must use paired daily returns rather than a
        # fake NAV jump caused by resetting the independent holdout capital.
        curves=[]
        for run in (a,b):
            nav=1.;curve=[{'date':'initial','nav':nav}]
            for row in run['paired_returns']:nav*=1+row['return'];curve.append({'date':row['date'],'nav':nav})
            curves.append(curve)
        interval=paired_block_interval(*curves)
        reasons=[]
        if a['oos']['observations']!=84 or a['holdout']['observations']!=63:reasons.append('新样本区间覆盖不足')
        if a['oos']['return_pct']<=0:reasons.append('新样本外成本后收益未为正')
        if a['holdout']['excess_pct']<=0:reasons.append('新留出未跑赢价格基准')
        if min(a['oos']['max_drawdown_pct'],a['holdout']['max_drawdown_pct']) < -8:reasons.append('回撤超过原研究门槛')
        if curves[0][-1]['nav']<=curves[1][-1]['nav']:reasons.append('未改善配对基线净收益')
        if a['sell_fills']<10:reasons.append('成交样本不足')
        if interval is None or interval['lower']<=0:reasons.append('配对改善置信区间未通过')
        for run in runs.values():run.pop('paired_returns')
        results.append({'symbols':item['symbols'],'status':'evaluated','sessions':required,'first':dates[0],'last':end,
                        'runs':runs,'paired_interval':interval,'passed':not reasons,'reasons':reasons,'data_digest':e.digest(data)})
    return {'manifest_digest':manifest['manifest_digest'],'results':results,
            'validated':len(results)==len(manifest['baskets']) and all(r['passed'] for r in results),
            'promotion_allowed':False,'live_trading_enabled':False}
