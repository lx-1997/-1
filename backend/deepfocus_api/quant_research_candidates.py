"""Pre-registered development hypotheses. No candidate can authorize promotion.

These hypotheses were designed after examining the original sealed experiments.
Their historical scores are development evidence, never fresh OOS evidence.
"""
import math
from statistics import stdev

PROFILES = ('legacy', 'recent_train', 'low_turnover', 'trend_guard', 'risk_scaled')
RULES = {
    'version':'candidate-eod-v2', 'train_sessions':252, 'rebalance_sessions':5,
    'no_trade_band_nav':.02, 'market_sma_sessions':60,
    'volatility_sessions':20, 'portfolio_vol_target_annual':.08,
    'volatility_can_increase_exposure':False,
    'risk_limits':'unchanged: single20/gross80/stop8/permanent_drawdown8',
    'status':'development_only_seen_history',
}


def decision(profile, prior, benchmark_prior, scores, threshold, session_index, limits):
    """Only completed historical bars enter this function; never today's OHLCV."""
    if profile not in PROFILES: raise ValueError('未登记的研究候选')
    chosen = [s for s in sorted(scores) if scores[s]>threshold]
    target = min(limits['single_weight'],limits['gross_weight']/len(chosen)) if chosen else 0.
    weights = {s:target for s in chosen}
    if profile in ('legacy','recent_train'):
        return {'weights':weights,'rebalance':True,'band':0.,'market_risk_off':False}
    risk_off = False
    if profile in ('trend_guard','risk_scaled'):
        closes = [b['close'] for b in benchmark_prior]
        n = RULES['market_sma_sessions']
        risk_off = len(closes)<n or closes[-1]<=sum(closes[-n:])/n
        if risk_off: weights = {}
    if profile == 'risk_scaled' and weights:
        n = RULES['volatility_sessions']
        # Align prior sessions; do not pair a halted/missing stock with a
        # different day's benchmark/peer return.
        dates = sorted(set.intersection(*[{b['date'] for b in prior[s]} for s in weights]))[-n-1:]
        maps = {s:{b['date']:b['close'] for b in prior[s]} for s in weights}
        if len(dates)<n+1:
            weights = {}
        else:
            returns = [sum(w*(maps[s][b]/maps[s][a]-1) for s,w in weights.items()) for a,b in zip(dates,dates[1:])]
            vol = stdev(returns)*math.sqrt(252)
            scale = min(1.,RULES['portfolio_vol_target_annual']/vol) if vol>0 else 1.
            weights = {s:w*scale for s,w in weights.items()}
    return {'weights':weights,'rebalance':session_index%RULES['rebalance_sessions']==0,
            'band':RULES['no_trade_band_nav'],'market_risk_off':risk_off}
