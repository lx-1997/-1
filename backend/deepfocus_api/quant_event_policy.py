"""Four fixed development execution policies; no parameter search or promotion."""
from datetime import date
import math
from statistics import mean

from .quant_research_engine import digest

POLICIES = {
    'daily': {'cadence': 1, 'market_lookback': None},
    'daily_market60': {'cadence': 1, 'market_lookback': 60},
    'cadence20': {'cadence': 20, 'market_lookback': None},
    'cadence20_market60': {'cadence': 20, 'market_lookback': 60},
}


def validate(policy, market_curve, sessions, stop):
    if policy not in POLICIES:
        raise ValueError('Unregistered execution policy')
    filtered = POLICIES[policy]['market_lookback'] is not None
    if not filtered:
        if market_curve is not None:
            raise ValueError('Unfiltered policy must not consume market data')
        return
    if not isinstance(market_curve, list) or not market_curve:
        raise ValueError('Market filter requires completed closes')
    previous = ''
    for row in market_curve:
        day, close = row['date'], row['close']
        date.fromisoformat(day)
        if (day <= previous or type(close) not in (int, float)
                or not math.isfinite(close) or close <= 0):
            raise ValueError('Invalid market closes')
        previous = day
    required = [d for d in sessions if d <= stop]
    observed = [r['date'] for r in market_curve if required[0] <= r['date'] <= required[-1]]
    if observed != required:
        raise ValueError('Market and stock calendars differ; no intersection or interpolation')


def market_prefix(curve, cursor):
    return digest([r for r in curve if r['date'] <= cursor]) if curve is not None else None


def decision(policy, market_curve, sessions, anchor, day):
    rules = POLICIES[policy]
    position = sessions.index(day)-sessions.index(anchor)
    if position < 0:
        raise ValueError('Execution precedes rebalance anchor')
    result = {'date': day, 'rebalance_due': position % rules['cadence'] == 0,
              'market_on': True, 'market_signal_date': None,
              'market_close': None, 'market_mean': None}
    if rules['market_lookback']:
        prior = [r for r in market_curve if r['date'] < day][-rules['market_lookback']:]
        if len(prior) != rules['market_lookback']:
            raise ValueError('Insufficient prior market warmup')
        average = mean(r['close'] for r in prior)
        result.update(market_on=prior[-1]['close'] > average,
                      market_signal_date=prior[-1]['date'],
                      market_close=prior[-1]['close'], market_mean=average)
    return result
