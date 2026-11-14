"""Frozen multi-index rule model. All features use completed bars at or before t.
No parameter search; historical experiments are hypothetical price-index proxies.
"""
from __future__ import annotations
import math
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any

VERSION = 'multi-index-v4.1'
UNIVERSE = {'csi300': '沪深300', 'csi500': '中证500', 'chinext': '创业板指', 'sse': '上证指数'}
WEIGHTS = {'trend': 30, 'momentum': 20, 'risk': 20, 'breadth': 20, 'volume': 10}
NAMES = {'trend': '趋势结构', 'momentum': '20日动量', 'risk': '波动与回撤', 'breadth': '跨指数共振', 'volume': '量价确认'}
BJ = timezone(timedelta(hours=8))

def clamp(v: float) -> float:
    return min(100., max(0., v))

def normalize(rows: list[dict], now: datetime) -> list[dict]:
    """Reject invalid, duplicate or future rows; exclude an unfinished daily bar.
    Do not impute missing sessions or accept a last-wins duplicate revision.
    """
    cutoff = now.astimezone(BJ).date()
    if (now.astimezone(BJ).hour, now.astimezone(BJ).minute) < (15, 5):
        cutoff -= timedelta(days=1)
    out, seen = [], set()
    for r in rows:
        try:
            day = datetime.strptime(r['date'], '%Y-%m-%d').date()
            o, c, v = float(r['open']), float(r['close']), float(r['volume'])
            if day > cutoff or day.weekday() >= 5:
                continue
            if day in seen:
                return []
            if not all(math.isfinite(x) for x in (o, c, v)) or min(o, c) <= 0 or v <= 0:
                return []
            seen.add(day)
            out.append({'date': day.isoformat(), 'open': o, 'close': c, 'volume': v})
        except (KeyError, ValueError, TypeError):
            return []
    return sorted(out, key=lambda r: r['date'])

def features(rows: list[dict]) -> dict[str, dict]:
    out = {}
    for i in range(59, len(rows)):
        w = rows[i-59:i+1]; cs = [r['close'] for r in w]; c = cs[-1]
        m20, m60 = statistics.mean(cs[-20:]), statistics.mean(cs)
        r20 = c / cs[-21] - 1
        rs = [math.log(cs[j]/cs[j-1]) for j in range(len(cs)-20, len(cs))]
        vol = statistics.stdev(rs)*math.sqrt(252)
        dd = c / max(cs) - 1
        ratio = w[-1]['volume'] / statistics.mean(r['volume'] for r in w[-20:])
        sign = 1 if c > cs[-6] else -1 if c < cs[-6] else 0
        state = ('上涨右侧' if c > m20 > m60 else '下跌右侧' if c < m20 < m60 else
                 '上涨左侧' if m20 > m60 else '下跌左侧' if m20 < m60 else '震荡')
        out[rows[i]['date']] = {
            'trend': clamp(50+500*(c/m60-1)+250*(m20/m60-1)),
            'momentum': clamp(50+500*r20),
            'risk': (clamp(100-vol*200)+clamp(100+dd*500))/2,
            'breadth': (int(c > m20)+int(c > m60))*50,
            'volume': 50+max(-25, min(25, (ratio-1)*50))*sign,
            'tech_state': state, 'heat_ratio': ratio, 'ma20': m20, 'ma60': m60,
        }
    return out

def daily_scores(series: dict[str, list[dict]]) -> list[dict]:
    """Only exact common dates in the fixed four-index universe; never backfill a feature."""
    if any(not series.get(k) for k in UNIVERSE):
        return []
    fs = {k: features(series[k]) for k in UNIVERSE}
    dates = sorted(set.intersection(*(set(f) for f in fs.values())))
    core = {r['date']: r for r in series['csi300']}
    out = []
    for day in dates:
        dims = {k: round(statistics.mean(f[day][k] for f in fs.values()), 4) for k in WEIGHTS}
        score = round(sum(dims[k]*WEIGHTS[k] for k in WEIGHTS)/100)
        f = fs['csi300'][day]
        out.append({**core[day], 'session_index': next(i for i, r in enumerate(series['csi300']) if r['date'] == day), 'temperature': score, 'dimensions': dims,
                    'tech_state': f['tech_state'], 'heat_ratio': f['heat_ratio'],
                    'tech_score': round(dims['trend']), 'heat_score': round(dims['volume'])})
    return out

def exposure(score: int) -> float:
    # A predeclared research rule, not a personalized recommendation.
    return .25 if score < 35 else .5 if score < 55 else .75 if score < 70 else 1.

def simulate(daily: list[dict], start: int, end: int, cost_bps: float = 5., constant: float | None = None) -> dict:
    """Signal at close t -> rebalance open t+1, with cash/share accounting.
    Both sides pay commission+slippage assumption, including initial/final trades.
    End liquidation included. No dividends, interest, ETF tracking or impact model.
    """
    cash, units, prev, peak = 1., 0., 1., 1.
    returns, curve, turnover, costs, exposure_sum, max_dd = [], [], 0., 0., 0., 0.
    fee = cost_bps/10000
    for i in range(max(1, start), min(len(daily), end)):
        row = daily[i]; op, close = row['open'], row['close']
        nav = cash+units*op; held = units*op
        target = constant if constant is not None else exposure(daily[i-1]['temperature'])
        sign = 1 if target*nav >= held else -1
        value = target*(nav+sign*fee*held)/(1+sign*fee*target)
        traded = abs(value-held); charge = traded*fee
        cash = nav-value-charge; units = value/op
        turnover += traded/nav; costs += charge; exposure_sum += target
        equity = cash+units*close
        if i == min(len(daily), end)-1:
            charge = units*close*fee; equity -= charge
            turnover += units*close/(equity+charge); costs += charge
        returns.append(equity/prev-1); prev = equity
        peak = max(peak, equity); max_dd = min(max_dd, equity/peak-1)
        curve.append({'date': row['date'], 'nav': round(equity, 8)})
    n = len(returns)
    sd = statistics.stdev(returns) if n > 1 else 0.
    return {'days': n, 'return_pct': round((prev-1)*100, 3),
            'annualized_pct': round((prev**(252/n)-1)*100, 3) if n else None,
            'max_drawdown_pct': round(max_dd*100, 3),
            'sharpe': round(statistics.mean(returns)/sd*math.sqrt(252), 3) if sd else None,
            'turnover': round(turnover, 3), 'cost_pct_initial': round(costs*100, 4),
            'avg_exposure_pct': round(exposure_sum/n*100, 1) if n else None, 'curve': curve}

def backtest(daily: list[dict]) -> dict:
    # Freeze rule first. Chronological final third is a retrospective holdout,
    # not genuinely unseen live data. Never use its result to auto-change weights.
    if any(b.get('session_index', 0)-a.get('session_index', -1) != 1 for a,b in zip(daily,daily[1:]) if 'session_index' in a and 'session_index' in b):
        return {'available': False, 'reason': '共同日线存在交易日缺口，暂停连续成交回测', 'model': VERSION}
    split = max(1, len(daily)*2//3)
    if len(daily)-split < 60:
        return {'available': False, 'reason': '共同历史不足：末段至少需要60个交易日', 'model': VERSION}
    def run(start, end, bps):
        strategy = simulate(daily, start, end, bps)
        bench = simulate(daily, start, end, bps, 1.)
        balanced = simulate(daily, start, end, bps, .5)
        return {'strategy': strategy, 'benchmark': bench, 'fixed_half': balanced,
                'excess_pct': round(strategy['return_pct']-bench['return_pct'], 3)}
    holdout = run(split, len(daily), 5.)
    folds = []
    for start in range(split, len(daily), 60):
        end = min(start+60, len(daily))
        if end-start < 20: continue
        r = run(start, end, 5.)
        folds.append({'start': daily[start]['date'], 'end': daily[end-1]['date'],
                      'days': end-start, 'return_pct': r['strategy']['return_pct'],
                      'benchmark_pct': r['benchmark']['return_pct'], 'excess_pct': r['excess_pct']})
    stress = []
    for bps in (2., 5., 10.):
        r = run(split, len(daily), bps)
        stress.append({'cost_bps_per_side': bps, 'return_pct': r['strategy']['return_pct'], 'excess_pct': r['excess_pct']})
    validation = '未通过收益对照' if holdout['excess_pct'] <= 0 else '末段收益领先，仍待前瞻验证'
    return {'available': True, 'model': VERSION, 'days': len(daily)-1,
            'start': daily[1]['date'], 'end': daily[-1]['date'], 'split_date': daily[split]['date'],
            'evaluation': '固定规则·时间末段留出，非上线后样本外', 'cost_bps_per_side': 5,
            'execution': '收盘生成信号，下一交易日开盘调仓；首尾交易均计成本',
            'full': run(1, len(daily), 5.), 'holdout': holdout, 'folds': folds, 'cost_sensitivity': stress,
            'validation': validation, 'adopted_for_advice': False,
            'limitations': ['指数价格代理，非可直接成交标的；未含分红、现金利息、ETF跟踪误差及市场冲击',
                            '规则未按回测自动调参；末段是回顾性检验，不能证明未来有效']}

def build_gauge(series: dict[str, list[dict]], sources: list[dict], now: datetime) -> dict[str, Any]:
    daily = daily_scores(series)
    if not daily:
        return {'available': False, 'reason': '四指数共同历史不足，暂停综合评分', 'sources': sources, 'model_version': VERSION}
    last = daily[-1]; day = datetime.strptime(last['date'], '%Y-%m-%d').date()
    local = now.astimezone(BJ)
    expected = local.date() if (local.hour, local.minute) >= (15, 5) else local.date()-timedelta(days=1)
    while expected.weekday() >= 5: expected -= timedelta(days=1)
    lag = max(0, (expected-day).days)
    # A weekday reference, not an exchange holiday calendar: disclose it explicitly.
    warnings = ['仅使用已完成日线；最近工作日检查未扣除交易所节假日']
    if lag: warnings.append('日线早于最近已结束工作日，当前环境可能滞后')
    conflicts = any(s.get('conflict') for s in sources)
    if any(not s.get('cross_checked') for s in sources): warnings.append('部分指数仅一个数据源可用，尚未完成双源价格核验')
    if conflicts: warnings.append('同日跨源收盘价偏差超过0.5%，暂停环境判断')
    regime = '偏弱' if last['temperature'] < 35 else '中性偏弱' if last['temperature'] < 50 else '中性偏强' if last['temperature'] < 70 else '偏强'
    core = series['csi300']; bydate = {r['date']: i for i,r in enumerate(core)}; idx = bydate[last['date']]
    core_features = features(core)[last['date']]
    ratio = last['heat_ratio']
    usable = not lag and not conflicts
    bt = backtest(daily)
    return {'available': True, 'model_version': VERSION, 'as_of': last['date'], 'index': '沪深300',
            'generated_at': local.isoformat(), 'freshness': 'lagging' if lag else 'completed_daily',
            'lag_calendar_days': lag, 'signal_usable': usable,
            'regime_label': '数据待更新' if lag else '来源待核对' if conflicts else regime,
            'temperature': last['temperature'], 'close': last['close'],
            'chg_1d_pct': round((last['close']/core[idx-1]['close']-1)*100, 2) if idx else None,
            'tech_state': last['tech_state'], 'tech_levels': {k:core_features[k] for k in ('ma20','ma60')},
            'heat_ratio': round(ratio,3), 'heat_state': '沸点' if ratio>=1.5 else '偏热' if ratio>=1.15 else '中性' if ratio>=.85 else '偏冷' if ratio>=.5 else '冰点',
            'temperature_trend': daily[-10:], 'temp_delta_1d': last['temperature']-daily[-2]['temperature'] if len(daily)>1 else None,
            'dimensions': [{'key': k, 'name': NAMES[k], 'score': round(last['dimensions'][k]), 'weight': WEIGHTS[k], 'used': True, 'as_of': last['date']} for k in WEIGHTS],
            'universe': list(UNIVERSE.values()), 'sources': sources, 'warnings': warnings,
            'backtest': bt,
            # Expose only the frozen rule's simulated bucket for transparency.
            # It is deliberately separate from the account-position fields below.
            'research_exposure_pct': round(exposure(last['temperature']) * 100) if usable and bt.get('available') else None,
            'position_pct': None, 'position_band': None,
            'disclaimer': '市场环境描述，非买卖或仓位建议；固定规则未经过前瞻验证'}
