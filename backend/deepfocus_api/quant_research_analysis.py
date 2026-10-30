"""Read-only attribution for sealed research experiments; never changes a gate."""
from collections import Counter, defaultdict
from . import quant_research_engine as engine


def replay_oos(dataset, symbols, result, capital, *, profile='legacy', benchmark=None):
    state = engine.initial_state(capital)
    fills, curve, blocked = [], [], []
    for fold in result['folds']:
        kwargs = {} if profile == 'legacy' else {'profile': profile, 'benchmark': benchmark}
        r = engine.replay(dataset, symbols, fold['parameters'], start=fold['test_start'],
                          end=fold['test_end'], state=state, **kwargs)
        state = r['state']
        fills.extend(r['fills']); curve.extend(r['curve']); blocked.extend(r['blocked'])
    expected = result['oos']['curve']
    assert len(curve) == len(expected)
    assert all(a['date'] == b['date'] and abs(a['nav']-b['nav']) < .001 for a,b in zip(curve, expected))
    return {'state': state, 'fills': fills, 'curve': curve, 'blocked': blocked}


def attribution(dataset, replay, capital):
    """Reconcile close-to-close P&L, trading costs, and open/close risk triggers.

    Cost add-back holds the actual fill path fixed. It is NOT a zero-cost replay:
    a new path would change selection, position size and the risk pause date.
    """
    maps = {s:{b['date']:b for b in p['bars']} for s,p in dataset.items()}
    by_day = defaultdict(list)
    for f in replay['fills']: by_day[f['date']].append(f)
    qty = defaultdict(int); previous_close = {}; peak = capital; previous_nav = capital
    fees = friction = turnover = 0.; pause = None; daily = []; by_symbol = defaultdict(float)
    monthly = defaultdict(float); reasons = Counter(); tiny = 0; exits = []
    for row in replay['curve']:
        d = row['date']; trades = by_day[d]
        open_nav = previous_nav + sum(q*(maps[s][d]['open']-previous_close[s]) for s,q in qty.items() if q)
        peak = max(peak, open_nav)
        if pause is None and open_nav/peak-1 <= -.08:
            pause = {'date':d,'phase':'before_open_fills','drawdown_pct':(open_nav/peak-1)*100,'nav':open_nav,'peak':peak}
        pnl = defaultdict(float)
        for s,q in qty.items():
            if q: pnl[s] += q*(maps[s][d]['close']-previous_close[s])
        day_fee = 0.; day_friction = 0.
        for f in trades:
            sign = 1 if f['side']=='buy' else -1
            amount = f['qty']*f['price']; charge = f['fee']
            pnl[f['symbol']] += sign*f['qty']*(maps[f['symbol']][d]['close']-f['price'])-charge
            qty[f['symbol']] += sign*f['qty']
            day_fee += charge
            day_friction += sign*f['qty']*(f['price']-maps[f['symbol']][d]['open'])
            turnover += amount; reasons[f['reason']] += 1
            if amount < previous_nav*.02: tiny += 1
            if f['side']=='sell': exits.append(f)
        delta = row['nav']-previous_nav
        assert abs(sum(pnl.values())-delta) < .021, 'attribution must reconcile to cent-rounded NAV'
        for s,p in pnl.items(): by_symbol[s] += p
        monthly[d[:7]] += delta
        fees += day_fee; friction += day_friction
        peak = max(peak,row['nav'])
        if pause is None and row['nav']/peak-1 <= -.08:
            pause = {'date':d,'phase':'after_close','drawdown_pct':(row['nav']/peak-1)*100,'nav':row['nav'],'peak':peak}
        daily.append({'date':d,'pnl':delta,'fees':day_fee,'slippage_and_tick_cost':day_friction,'exposure_pct':row['exposure_pct']})
        previous_close = {s:maps[s][d]['close'] for s in qty}
        previous_nav = row['nav']
    active = sum(x['exposure_pct']>.01 for x in daily)
    return {'first_risk_pause':pause,'fills':len(replay['fills']),'sell_fills':len(exits),
            'losing_sell_fills':sum(f['realized_pnl']<0 for f in exits),
            'fees_pct_initial':fees/capital*100,'slippage_tick_pct_initial':friction/capital*100,
            'fixed_path_cost_addback_pct':(fees+friction)/capital*100,
            'cost_addback_is_causal_replay':False,'traded_notional_over_initial':turnover/capital,
            'small_fills_under_2pct_nav':tiny,'fill_reasons':dict(reasons),
            'blocked_reasons':dict(Counter(x['reason'] for x in replay['blocked'])),
            'active_sessions':active,'flat_sessions':len(daily)-active,
            'post_pause_sessions':sum(x['date']>pause['date'] for x in daily) if pause else 0,
            'pnl_pct_by_symbol':{s:v/capital*100 for s,v in by_symbol.items()},
            'pnl_pct_by_month':{m:v/capital*100 for m,v in monthly.items()},
            'worst_days':sorted(daily,key=lambda r:r['pnl'])[:5],
            'daily':daily}


def diagnose(experiment):
    r = experiment['result']; req = experiment['request']; dataset = experiment['dataset']
    assert engine.digest(r) == experiment['result_digest'], 'sealed result modified'
    assert engine.digest(dataset) == r['dataset_digest'], 'sealed prices modified'
    reproduced = engine.walk_forward(dataset, req['symbols'], req['benchmark'], req['capital'])
    assert engine.digest(reproduced) == experiment['result_digest'], 'baseline must reproduce before optimization'
    run = replay_oos(dataset,req['symbols'],r,req['capital'])
    return {'experiment':experiment['id'],'symbols':req['symbols'],'result_digest':experiment['result_digest'],
            'reproduced_exactly':True,'gate':r['gate'],
            'oos':{k:v for k,v in r['oos'].items() if k!='curve'},
            'holdout':r['holdout']['metrics'],'attribution':attribution(dataset,run,req['capital']),
            'fold_generalization':[{'start':f['test_start'],'end':f['test_end'],'parameters':f['parameters'],
                 'selected_train':next(t['metrics'] for t in f['trials'] if t['parameters']==f['parameters']),
                 'oos':f['metrics']} for f in r['folds']]}
