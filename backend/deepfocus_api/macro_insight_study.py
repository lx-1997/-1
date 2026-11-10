"""Predeclared cross-index research. No fitting, trading API, or promotion path."""
from __future__ import annotations
import math
from statistics import mean
from .market_regime import simulate

KEYS = ('csi300', 'csi500', 'chinext', 'sse')
LABELS = {'trend': '沪深300单指数趋势', 'resonance': '四指数趋势共振',
          'volume': '沪深300量价确认', 'divergence': '权重与其他指数同步'}


def signals(series: dict) -> dict[str, list[dict]]:
    if any(not series.get(k) for k in KEYS):
        raise ValueError('四指数日线缺失')
    features = {}
    for key in KEYS:
        rows = series[key]
        dates = [r['date'] for r in rows]
        if dates != sorted(set(dates)):
            raise ValueError('日期重复或非递增')
        if any(not all(math.isfinite(float(r[f])) and float(r[f]) > 0
                       for f in ('open', 'close', 'volume')) for r in rows):
            raise ValueError('非法价格或成交量')
        features[key] = {r['date']: (float(r['close']) > mean(float(x['close']) for x in rows[i-19:i+1]),
                                     float(r['volume']) > mean(float(x['volume']) for x in rows[i-19:i+1]))
                         for i, r in enumerate(rows) if i >= 19}
    common = set.intersection(*(set(f) for f in features.values()))
    core = series['csi300']
    selected = [(i, r) for i, r in enumerate(core) if r['date'] in common]
    if any(b[0] - a[0] != 1 for a, b in zip(selected, selected[1:])):
        raise ValueError('共同历史存在缺口，拒绝跨缺口成交')
    output = {k: [] for k in LABELS}
    for _, row in selected:
        day = row['date']
        up, high_volume = features['csi300'][day]
        count = sum(features[k][day][0] for k in KEYS)
        conditions = {'trend': up, 'resonance': count >= 3,
                      'volume': up and high_volume, 'divergence': up and count >= 3}
        for key, condition in conditions.items():
            output[key].append({**row, 'temperature': 100 if condition else 0})
    return output


def _metrics(rows, start, end, bps, constant=None):
    return {k: v for k, v in simulate(rows, start, end, bps, constant).items() if k != 'curve'}


def evaluate(series: dict) -> dict:
    rules = signals(series)
    rows = rules['trend']; n = len(rows); split = n * 2 // 3
    if n - split < 180:
        raise ValueError('至少需要180个末段交易日')
    baseline = _metrics(rows, split, n, 5)
    results = []
    for key, daily in rules.items():
        result = _metrics(daily, split, n, 5)
        avg_exposure = mean(1. if daily[i-1]['temperature'] == 100 else .25 for i in range(split, n))
        matched = _metrics(daily, split, n, 5, avg_exposure)
        folds = []
        for start in range(split, n, 60):
            end = min(n, start + 60)
            if end - start < 20:
                continue
            candidate, control = _metrics(daily, start, end, 5), _metrics(rows, start, end, 5)
            folds.append({'start': daily[start]['date'], 'end': daily[end-1]['date'],
                          'excess_vs_trend_pct': round(candidate['return_pct'] - control['return_pct'], 3)})
        stress = _metrics(daily, split, n, 10)
        stress_base = _metrics(rows, split, n, 10)
        checks = {
            'return_vs_trend': result['return_pct'] > baseline['return_pct'],
            'return_vs_exposure_matched': result['return_pct'] > matched['return_pct'],
            'sharpe_vs_trend': result['sharpe'] is not None and baseline['sharpe'] is not None and result['sharpe'] > baseline['sharpe'],
            'drawdown_vs_trend': result['max_drawdown_pct'] >= baseline['max_drawdown_pct'],
            'fold_consistency': len(folds) >= 3 and sum(f['excess_vs_trend_pct'] > 0 for f in folds) > len(folds)/2,
            'cost_stress': stress['return_pct'] > stress_base['return_pct'],
        }
        results.append({'key': key, 'name': LABELS[key], 'holdout': result, 'matched_exposure': matched,
                        'folds': folds, 'stress_10bps': stress, 'checks': checks,
                        'screen_passed': key != 'trend' and all(checks.values()), 'promotion_allowed': False})
    return {'available': True, 'version': 'macro-insights-v1', 'evaluation': '回顾性时间留出，历史数据曾用于平台研究',
            'as_of': rows[-1]['date'], 'start': rows[0]['date'], 'split_date': rows[split]['date'],
            'holdout_days': n-split, 'cost_bps_per_side': 5, 'promotion_allowed': False,
            'benchmarks': {'buy_hold': _metrics(rows, split, n, 5, 1.), 'fixed_half': _metrics(rows, split, n, 5, .5)},
            'candidates': results,
            'limitations': ['四指数共振不等于全市场成分股广度', '指数价格代理，未含分红、现金利息、ETF跟踪误差、税费及冲击',
                            '匹配敞口对照按评估期平均敞口事后计算，仅用于风险暴露归因',
                            '财报、新闻与持仓缺少已核实的历史可见性快照，本轮未纳入',
                            '所有规则固定，保留失败结果；历史通过也需新的前瞻数据验证']}
