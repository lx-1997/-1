"""Deterministic end-of-day research kernel, shared by replay and paper tracking.

No network, databases, clocks or random state here. Signals see completed prior
bars only; fills use the next session's open. Public OHLCV is not a tick oracle.
"""
from __future__ import annotations
import copy
import hashlib
import json
import math
from datetime import date
from .ai_fund_ledger import fee
from .ai_fund_policy import order_quantity

VERSION = 'research-eod-v1'
CANDIDATES = tuple({'lookback': n, 'threshold': t} for n in (10, 20, 40) for t in (0.0, 0.03))
ASSUMPTIONS = {
    'version': VERSION, 'signal': 'previous_complete_close', 'fill': 'next_session_open',
    'slippage_bps': 10, 'volume_participation': 0.01, 'volume_basis': 'previous_session_shares',
    'single_weight': 0.20, 'gross_weight': 0.80, 'drawdown_pause': 0.08,
    'stop_loss': 0.08, 'commission_rate': 0.00025, 'minimum_commission': 5,
    'sell_stamp_rate': 0.0005, 'transfer_rate': 0.00001,
    'unfilled_policy': 'discard_and_recompute_next_session', 'shorting': False,
    'corporate_actions': 'unverified_raw_prices', 'live_trading': False,
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def validate_dataset(dataset):
    if not isinstance(dataset, dict) or not 1 <= len(dataset) <= 6:
        raise ValueError('研究集须有 1–6 个标的（含基准）')
    for symbol, payload in dataset.items():
        if not (len(symbol) == 6 and symbol.isdigit() and symbol[0] in '036'):
            raise ValueError('目前仅支持沪深股票的六位代码')
        if payload.get('is_synthetic') is not False or payload.get('adjustment') != 'none' or payload.get('volume_unit') != 'shares':
            raise ValueError('须提供明确的真实不复权行情和股数单位')
        if not payload.get('source') or not payload.get('fetched_at'):
            raise ValueError('缺少来源和抓取时间')
        previous = ''
        for bar in payload.get('bars', []):
            d = bar.get('date', '')
            date.fromisoformat(d)
            if d <= previous:
                raise ValueError('行情日期须唯一且递增')
            previous = d
            values = [bar.get(k) for k in ('open', 'high', 'low', 'close', 'volume')]
            if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in values):
                raise ValueError('行情数字无效')
            o, h, l, c, v = values
            if min(o, h, l, c) <= 0 or v < 0 or h < max(o, c) or l > min(o, c) or h < l:
                raise ValueError('OHLCV 不一致')
        if not previous:
            raise ValueError('没有真实日线')
    dates = sorted(set.intersection(*[set(b['date'] for b in p['bars']) for p in dataset.values()]))
    return dates


def initial_state(capital=1000000):
    if not math.isfinite(capital) or not 10000 <= capital <= 100000000:
        raise ValueError('模拟本金须在 1 万至 1 亿元之间')
    return {'cash': float(capital), 'initial': float(capital), 'peak': float(capital), 'positions': {}, 'cursor': '', 'paused': False}


def metrics(curve, capital):
    navs = [capital] + [x['nav'] for x in curve]
    peak, worst = capital, 0.0
    returns = []
    for a, b in zip(navs, navs[1:]):
        peak = max(peak, b)
        worst = min(worst, b / peak - 1)
        returns.append(b / a - 1)
    n = len(returns)
    mean = sum(returns) / n if n else 0
    variance = sum((r - mean)**2 for r in returns) / (n - 1) if n > 1 else 0
    return {'return_pct': (navs[-1]/capital-1)*100, 'max_drawdown_pct': worst*100,
            'observations': n, 'sharpe': mean / math.sqrt(variance) * math.sqrt(252) if n >= 60 and variance > 0 else None}


def replay(dataset, symbols, params, *, start=None, end=None, state=None, capital=1000000,
           profile='legacy', benchmark=None):
    dates = validate_dataset(dataset)
    if set(symbols) - set(dataset) or not symbols or len(symbols) > 5 or len(set(symbols)) != len(symbols):
        raise ValueError('标的集合无效')
    if params not in CANDIDATES:
        raise ValueError('策略参数未登记')
    if profile != 'legacy':
        from .quant_research_candidates import PROFILES, decision
        if profile not in PROFILES or benchmark not in dataset or benchmark in symbols:
            raise ValueError('研究候选或价格基准未登记')
    state = copy.deepcopy(state if state is not None else initial_state(capital))
    maps = {s: {b['date']: b for b in dataset[s]['bars']} for s in dataset}
    histories = {s: [b for b in dataset[s]['bars']] for s in symbols}
    fills, curve, blocks = [], [], []
    lookback = params['lookback']
    for session_index, d in enumerate(dates):
        if (start and d < start) or (end and d > end) or d <= state['cursor']:
            continue
        today = {s: maps[s][d] for s in symbols}
        prior = {s: [b for b in histories[s] if b['date'] < d] for s in symbols}
        if any(len(v) < lookback + 1 for v in prior.values()):
            continue
        def nav(field):
            return state['cash'] + sum(p['qty'] * today[s][field] for s, p in state['positions'].items())
        open_nav = nav('open')
        state['peak'] = max(state['peak'], open_nav)
        if open_nav / state['peak'] - 1 <= -ASSUMPTIONS['drawdown_pause']:
            state['paused'] = True
        scores = {s: prior[s][-1]['close']/prior[s][-lookback-1]['close']-1 for s in symbols}
        chosen = set(s for s in symbols if scores[s] > params['threshold']) if not state['paused'] else set()
        target = min(ASSUMPTIONS['single_weight'], ASSUMPTIONS['gross_weight']/len(chosen)) if chosen else 0
        policy = None
        if profile != 'legacy':
            policy = decision(profile, prior, [b for b in dataset[benchmark]['bars'] if b['date']<d],
                              scores, params['threshold'], session_index, ASSUMPTIONS)
        # Sell first; no intraday decision uses the current close/high/low signal.
        touched = set()
        for side in ('sell', 'buy'):
            for s in sorted(symbols):
                bar, prev = today[s], prior[s][-1]
                if s in touched: continue
                if bar['volume'] <= 0:
                    blocks.append({'date': d, 'symbol': s, 'reason': 'suspended_or_missing_volume', 'side': side}); continue
                pos = state['positions'].get(s, {'qty': 0, 'cost': 0, 'opened': ''})
                stop = pos['qty'] > 0 and prev['close'] / pos['cost'] - 1 <= -ASSUMPTIONS['stop_loss']
                desired = open_nav*target if s in chosen and not stop else 0
                current = pos['qty']*bar['open']
                if policy is not None:
                    if stop or state['paused'] or policy['market_risk_off']:
                        desired = 0
                    elif not policy['rebalance']:
                        desired = current
                    else:
                        desired = open_nav*policy['weights'].get(s,0.)
                        if desired>0 and abs(desired-current)<open_nav*policy['band']:
                            desired = current
                if (side == 'buy' and desired <= current) or (side == 'sell' and desired >= current):
                    continue
                reason = 'stop_loss' if stop else ('risk_pause' if state['paused'] else 'momentum_rebalance')
                if policy and policy['market_risk_off'] and not stop and not state['paused']:
                    reason = 'market_trend_exit'
                # Conservative open-limit screen for all ordinary main-board names;
                # unknown ST/IPO limit regimes remain a declared data limitation.
                limit = .20 if s.startswith(('300', '301', '688', '689')) else .10
                change = bar['open']/prev['close']-1
                if (side == 'buy' and change >= limit-.003) or (side == 'sell' and change <= -limit+.003):
                    blocks.append({'date': d, 'symbol': s, 'reason': 'price_limit', 'side': side}); continue
                cap = max(0, int(prev['volume']*ASSUMPTIONS['volume_participation']))
                unrounded = bar['open'] * (1 + (.001 if side == 'buy' else -.001))
                price = (math.ceil(unrounded*100-1e-8) if side=='buy' else math.floor(unrounded*100+1e-8))/100
                if side == 'buy':
                    budget = min(max(0, desired-current), state['cash'])
                    qty = order_quantity(s, budget, price)
                    qty = min(qty, order_quantity(s, cap*price, price))
                    while qty > 0 and qty*price + fee(qty*price, side) > state['cash']:
                        qty = qty-1 if s.startswith(('688','689')) else qty-100
                        if s.startswith(('688','689')) and qty < 200: qty = 0
                else:
                    if pos['opened'] >= d: continue  # T+1
                    want = pos['qty'] if not desired else min(pos['qty'], int((current-desired)/price))
                    qty = min(want, cap)
                    if qty < pos['qty']:
                        qty = int(qty) if s.startswith(('688','689')) else int(qty//100)*100
                if side == 'sell' and s.startswith(('688','689')) and qty < 200 and qty != pos['qty']:
                    qty = 0
                if qty <= 0:
                    blocks.append({'date': d, 'symbol': s, 'reason': 'volume_or_cash_or_lot', 'side': side}); continue
                amount, charge = qty*price, fee(qty*price, side)
                if side == 'sell' and amount <= charge:
                    blocks.append({'date': d, 'symbol': s, 'reason': 'fee_exceeds_proceeds', 'side': side}); continue
                touched.add(s)
                pnl = None
                if side == 'buy':
                    new_qty = pos['qty']+qty
                    state['cash'] = round(state['cash']-amount-charge, 2)
                    state['positions'][s] = {'qty': new_qty, 'cost': (pos['cost']*pos['qty']+amount+charge)/new_qty, 'opened': d}
                else:
                    pnl = round(amount-charge-pos['cost']*qty, 2)
                    state['cash'] = round(state['cash']+amount-charge, 2)
                    if qty == pos['qty']: state['positions'].pop(s)
                    else: state['positions'][s] = {**pos, 'qty': pos['qty']-qty}
                assert state['cash'] >= 0, 'cash invariant'
                fill_key = [VERSION, d, s, side, qty, price]
                if profile != 'legacy': fill_key += [profile, params]
                fills.append({'id': digest(fill_key), 'date': d, 'signal_date': prev['date'],
                              'symbol': s, 'side': side, 'qty': qty, 'price': price, 'fee': charge, 'realized_pnl': pnl, 'reason': reason})
        close_nav = round(nav('close'), 2)
        state['peak'] = max(state['peak'], close_nav)
        if close_nav/state['peak']-1 <= -.08: state['paused'] = True
        state['cursor'] = d
        curve.append({'date': d, 'nav': close_nav, 'return_pct': (close_nav/state['initial']-1)*100,
                      'cash': state['cash'], 'exposure_pct': (close_nav-state['cash'])/close_nav*100 if close_nav else 0})
    return {'state': state, 'fills': fills, 'curve': curve, 'blocked': blocks, 'metrics': metrics(curve, state['initial'])}


def benchmark_return(dataset, benchmark, dates):
    rows = {b['date']: b for b in dataset[benchmark]['bars']}
    if not dates: return 0.0
    return (rows[dates[-1]]['close']/rows[dates[0]]['open']-1)*100


def walk_forward(dataset, symbols, benchmark, capital=1000000, progress=None, *, profile='legacy'):
    dates = validate_dataset(dataset)
    if len(dates) < 300:
        raise ValueError(f'共同交易日仅 {len(dates)} 天，完整验证至少需要 300 天')
    if benchmark in symbols: raise ValueError('基准不能同时作为策略标的')
    protocol = {'version': VERSION, 'candidates': list(CANDIDATES), 'train_min': 126, 'test_days': 42,
                'gap_days': 5, 'holdout_days': 63, 'last_test_window': 'partial_if_needed', 'selection': 'train_return_minus_absolute_drawdown',
                'universe': 'user_selected_current_universe_not_survivorship_free', 'capital': capital}
    replay_kwargs = {}
    if profile != 'legacy':
        from .quant_research_candidates import PROFILES, RULES
        if profile not in PROFILES: raise ValueError('研究候选未登记')
        protocol.update(profile=profile, candidate_rules=RULES, validation_status='development_seen_history')
        replay_kwargs = {'profile':profile, 'benchmark':benchmark}
    holdout_start = len(dates)-63
    def select(stop):
        trials = []
        for p in CANDIDATES:
            train_start = dates[max(0,stop-252)] if profile!='legacy' else None
            r = replay(dataset, symbols, p, start=train_start, end=dates[stop-1], capital=capital, **replay_kwargs)
            m = r['metrics']
            trials.append({'parameters': p, 'train_end': dates[stop-1], 'metrics': m,
                           'score': m['return_pct']+m['max_drawdown_pct']})
        winner = max(trials, key=lambda t: (t['score'], -t['parameters']['lookback'], -t['parameters']['threshold']))
        return winner['parameters'], trials
    folds, combined = [], []
    oos_benchmark = 1.0
    oos_state = initial_state(capital)
    cursor = 131
    while cursor < holdout_start:
        chosen, trials = select(cursor-5)
        stop = min(cursor+42, holdout_start)
        window_capital = oos_state['cash'] + sum(p['qty'] * next(b['close'] for b in dataset[s]['bars'] if b['date']==oos_state['cursor']) for s,p in oos_state['positions'].items())
        result = replay(dataset, symbols, chosen, start=dates[cursor], end=dates[stop-1], state=oos_state, **replay_kwargs)
        oos_state = result['state']
        m = metrics(result['curve'], window_capital)
        base_dates = [r['date'] for r in result['curve']]
        base = benchmark_return(dataset, benchmark, base_dates)
        if folds and base_dates:
            bm = {b['date']:b for b in dataset[benchmark]['bars']}
            base = (bm[base_dates[-1]]['close']/bm[folds[-1]['test_end']]['close']-1)*100
        oos_benchmark *= 1+base/100
        folds.append({'train_end': dates[cursor-6], 'test_start': dates[cursor], 'test_end': dates[stop-1],
                      'parameters': chosen, 'trials': trials, 'metrics': {**m, 'benchmark_pct': base, 'excess_pct': m['return_pct']-base}})
        last = window_capital
        for row in result['curve']:
            combined.append({'date': row['date'], 'daily_return': row['nav']/last-1})
            last = row['nav']
        cursor = stop
        if progress: progress(35+int(35*cursor/holdout_start), f'已完成 {len(folds)} 个样本外窗口')
    chosen, trials = select(holdout_start-5)
    final = replay(dataset, symbols, chosen, start=dates[holdout_start], capital=capital, **replay_kwargs)
    base = benchmark_return(dataset, benchmark, [r['date'] for r in final['curve']])
    final['metrics'].update(benchmark_pct=base, excess_pct=final['metrics']['return_pct']-base)
    cumulative, oos_curve = capital, []
    for r in combined:
        cumulative *= 1+r['daily_return']
        oos_curve.append({'date': r['date'], 'nav': cumulative})
    oos = metrics(oos_curve, capital)
    oos['benchmark_pct'] = (oos_benchmark-1)*100
    oos['excess_pct'] = oos['return_pct']-oos['benchmark_pct']
    reasons = []
    if len(folds) < 2: reasons.append('滚动样本外窗口少于两个')
    if oos['return_pct'] <= 0: reasons.append('滚动样本外成本后收益未为正')
    if final['metrics']['excess_pct'] <= 0: reasons.append('最终留出集未跑赢价格基准')
    if min(oos['max_drawdown_pct'], final['metrics']['max_drawdown_pct']) < -8: reasons.append('回撤超过研究门槛')
    if profile!='legacy': reasons.append('候选使用已见历史开发数据，须完成冻结后的全新验证，禁止晋级')
    return {'protocol': protocol, 'parameters': chosen, 'selection_trials': trials, 'folds': folds,
            'oos': {**oos, 'curve': oos_curve, 'capital_resets_per_fold': False, 'risk_paused': oos_state['paused']}, 'holdout': final,
            'gate': {'passed': not reasons, 'reasons': reasons, 'purpose': 'paper_research_only',
                     'commercial_data_licensed': False, 'live_trading_enabled': False,
                     'unresolved': ['公司行为未核验', 'ST/IPO/退市历史池未覆盖', '公开行情商用授权未确认', '日线撮合无法证明盘口可成交']},
            'assumptions': ASSUMPTIONS, 'dataset_digest': digest(dataset)}
