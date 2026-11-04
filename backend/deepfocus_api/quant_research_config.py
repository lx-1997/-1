"""Bounded, immutable experiment parameters. Validation gates are not inputs."""
import copy
import math
from decimal import Decimal, ROUND_HALF_UP
from statistics import stdev

DEFAULT = {
    'version': 'research-config-v1', 'strategy': 'momentum',
    'lookbacks': [10, 20, 40], 'thresholds': [0.0, 0.03],
    'rebalance_days': 1, 'rebalance_band': 0.0, 'max_holdings': 5,
    'single_weight': 0.20, 'gross_weight': 0.80, 'stop_loss': 0.08, 'drawdown_pause': 0.08,
    'slippage_bps': 10, 'volume_participation': 0.01,
    'commission_rate': 0.00025, 'minimum_commission': 5.0,
    'sell_stamp_rate': 0.0005, 'transfer_rate': 0.00001,
    'min_price': 0.0, 'min_turnover': 0.0,
    'market_sma_days': 60, 'volatility_days': 20, 'vol_target': 0.08,
}
BOUNDS = {
    'rebalance_days': (1, 20, True), 'rebalance_band': (0, .10, False), 'max_holdings': (1, 5, True),
    'single_weight': (.01, .20, False), 'gross_weight': (.01, .80, False),
    'stop_loss': (.01, .08, False), 'drawdown_pause': (.01, .08, False),
    'slippage_bps': (10, 100, False), 'volume_participation': (.0001, .01, False),
    'commission_rate': (.00025, .01, False), 'minimum_commission': (5, 100, False),
    'sell_stamp_rate': (.0005, .01, False), 'transfer_rate': (.00001, .001, False),
    'min_price': (0, 10000, False), 'min_turnover': (0, 1e10, False),
    'market_sma_days': (20, 120, True), 'volatility_days': (10, 60, True), 'vol_target': (.01, .08, False),
}

def validate(value):
    if value is None: return copy.deepcopy(DEFAULT)
    if not isinstance(value, dict) or set(value) - set(DEFAULT):
        raise ValueError('实验配置包含未知参数；验证窗口和通过门槛不可修改')
    c = {**copy.deepcopy(DEFAULT), **copy.deepcopy(value)}
    if c['version'] != DEFAULT['version'] or c['strategy'] not in ('momentum','low_turnover','trend_guard','risk_scaled'):
        raise ValueError('策略模板或配置版本无效')
    for key, (low, high, integer) in BOUNDS.items():
        v = c[key]
        if isinstance(v, bool) or not isinstance(v, (int,float)) or not math.isfinite(v) or not low <= v <= high or (integer and type(v) is not int):
            raise ValueError(f'参数 {key} 超出允许范围 [{low}, {high}]')
    for key, low, high, integer, maximum in [('lookbacks',5,120,True,4),('thresholds',0,.20,False,3)]:
        values = c[key]
        if not isinstance(values,list) or not 1 <= len(values) <= maximum:
            raise ValueError(f'{key} 候选数量无效')
        for v in values:
            if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not low <= v <= high or (integer and type(v) is not int):
                raise ValueError(f'{key} 候选值无效')
        if len(set(values)) != len(values): raise ValueError('候选参数不能重复')
        c[key] = sorted(values)
    if len(c['lookbacks'])*len(c['thresholds']) > 12: raise ValueError('单次最多十二组候选')
    if c['single_weight'] > c['gross_weight']: raise ValueError('单股上限不能超过总仓位上限')
    return c

def effective(value):
    c = validate(value)
    return None if c == DEFAULT else c

def candidates(c):
    return tuple({'lookback': n, 'threshold': t} for n in c['lookbacks'] for t in c['thresholds'])

def charge(amount, side, c):
    amount = Decimal(str(amount))
    commission = max(Decimal(str(c['minimum_commission'])), amount*Decimal(str(c['commission_rate'])))
    total = commission + amount*Decimal(str(c['transfer_rate']))
    if side == 'sell': total += amount*Decimal(str(c['sell_stamp_rate']))
    return float(total.quantize(Decimal('.01'), rounding=ROUND_HALF_UP))

def decision(c, prior, benchmark_prior, scores, threshold, session_index):
    eligible = [s for s in scores if scores[s] > threshold and prior[s][-1]['close'] >= c['min_price']
                and prior[s][-1]['close']*prior[s][-1]['volume'] >= c['min_turnover']]
    chosen = sorted(eligible, key=lambda s: (-scores[s], s))[:c['max_holdings']]
    target = min(c['single_weight'],c['gross_weight']/len(chosen)) if chosen else 0
    weights = {s:target for s in chosen}
    risk_off = False
    if c['strategy'] in ('trend_guard','risk_scaled'):
        closes = [b['close'] for b in benchmark_prior]; n = c['market_sma_days']
        risk_off = len(closes)<n or closes[-1] <= sum(closes[-n:])/n
        if risk_off: weights = {}
    if c['strategy'] == 'risk_scaled' and weights:
        n = c['volatility_days']; dates = sorted(set.intersection(*[{b['date'] for b in prior[s]} for s in weights]))[-n-1:]
        maps = {s:{b['date']:b['close'] for b in prior[s]} for s in weights}
        if len(dates)<n+1: weights = {}
        else:
            returns = [sum(w*(maps[s][b]/maps[s][a]-1) for s,w in weights.items()) for a,b in zip(dates,dates[1:])]
            vol = stdev(returns)*math.sqrt(252)
            scale = min(1.,c['vol_target']/vol) if vol > 0 else 1.
            weights = {s:w*scale for s,w in weights.items()}
    return {'weights':weights,'rebalance':session_index%c['rebalance_days']==0,
            'band':c['rebalance_band'],'market_risk_off':risk_off}
