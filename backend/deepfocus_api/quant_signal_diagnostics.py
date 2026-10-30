"""Predeclared signal/forward-mark diagnostics on seen, bounded history.

No orders, selection, risk reset, holdout evaluation or alpha certification.
Overlapping labels and a tiny surviving universe are not independent samples.
"""
from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path
from statistics import mean

from .quant_calendar import require_aligned
from .quant_corporate_actions import CashDividend, gross_return_index, timestamp, validate_evidence
from .quant_research_analysis import diagnose
from .quant_research_engine import digest
from . import quant_event_training as training

RULES = {
    'lookbacks': [10, 20, 40], 'horizons': [5, 20], 'thresholds': [0.0, 0.03],
    'windows': ['full_seen_development', 'before_reference_permanent_pause'],
    'signal': 'prior_completed_gross_index_momentum',
    'cash_evidence_mode': 'historical_reconstruction_late_collected_not_as_recorded',
    'label': 'raw_entry_open_to_horizon_close_gross_index_mark',
    'label_end': 'must_be_within_each_declared_window_no_holdout',
    'correlation': 'daily_cross_section_spearman_average_tied_ranks',
    'rank_input_decimal_places': 12,
    'conditioning': 'strict_score_above_each_original_threshold',
    'relative_label': 'subtract_same_day_equal_weight_pool_mark_return',
    'overlap_check': 'all_horizon_nonoverlap_offsets_no_best_offset_selection',
    'entry_filter': 'none_informational_marks_only_zero_volume_counted',
    'p_values': 'not_computed_nonstationarity_small_pool_and_overlap',
    'nomination': 'none', 'benchmark_alpha': 'not_estimated',
}


def source_pins():
    return {**training.source_pins(), **{name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in ('quant_signal_diagnostics.py', 'quant_calendar.py')}}


def windows(exp, reference_row):
    dates = [r['date'] for r in reference_row['cases']['refit_expanded']['curve']]
    folds = exp['result']['folds']
    if not dates or not folds or dates[0] != folds[0]['test_start'] or dates[-1] != folds[-1]['test_end']:
        raise ValueError('Reference period differs from sealed development folds')
    pause = next((i for i, row in enumerate(reference_row['cases']['refit_expanded']['curve']) if row['risk_paused']), None)
    return {'full_seen_development': [dates[0], dates[-1]],
            'before_reference_permanent_pause': [dates[0], dates[pause-1]] if pause is not None and pause>0 else None}


def bindings(experiments, manifest, reference):
    rows = {r['experiment']: r for r in reference['results']}
    if len(rows) != len(reference['results']) or set(rows) != {e['id'] for e in experiments}:
        raise ValueError('Reference experiment scope differs')
    return {'inputs': training.bindings(experiments, manifest), 'reference': digest(reference),
            'windows': {e['id']: windows(e, rows[e['id']]) for e in experiments},
            'rules': digest(RULES), 'source_pins': source_pins()}


def validate_protocol(protocol, experiments, manifest, reference):
    if (protocol.get('schema') != 'signal-information-diagnostic-v1' or protocol.get('rules') != RULES
            or protocol.get('purpose') != 'seen_development_diagnostic_no_selection'
            or protocol.get('promotion_allowed') is not False):
        raise ValueError('Unregistered diagnostic rule or promotion request')
    if timestamp(protocol['registered_at']) > datetime.now(timezone.utc):
        raise ValueError('Future registration')
    training.validate_protocol(reference['protocol'], experiments, manifest)
    if reference['protocol_digest'] != digest(reference['protocol']):
        raise ValueError('Reference protocol digest changed')
    if protocol.get('bindings') != bindings(experiments, manifest, reference):
        raise ValueError('Registered inputs, windows or source changed')
    if any(len(e['request']['symbols']) < 3 for e in experiments):
        raise ValueError('Cross-sectional study requires at least three declared stocks')


def ranks(values):
    if not values or any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
        raise ValueError('Finite nonempty rank input required')
    values = [round(v, RULES['rank_input_decimal_places']) for v in values]
    result = [0.0]*len(values)
    ordered = sorted(range(len(values)), key=values.__getitem__)
    i = 0
    while i < len(ordered):
        j = i+1
        while j < len(ordered) and values[ordered[j]] == values[ordered[i]]:
            j += 1
        for index in ordered[i:j]:
            result[index] = (i+j-1)/2+1
        i = j
    return result


def rank_ic(scores, labels):
    if len(scores) != len(labels):
        raise ValueError('Rank inputs must have equal lengths')
    a, b = ranks(scores), ranks(labels)
    ma, mb = mean(a), mean(b)
    denominator = math.sqrt(sum((x-ma)**2 for x in a)*sum((y-mb)**2 for y in b))
    return sum((x-ma)*(y-mb) for x, y in zip(a, b))/denominator if denominator else None


def samples(dataset, symbols, events, start, end, lookback, horizon):
    if lookback not in RULES['lookbacks'] or horizon not in RULES['horizons']:
        raise ValueError('Undeclared lookback or label horizon')
    require_aligned(dataset, expected_symbols=list(dataset))
    # Never build either features or labels through the old holdout.
    bars = {s: [b for b in dataset[s]['bars'] if b['date']<=end] for s in symbols}
    if not symbols or not bars[symbols[0]]:
        raise ValueError('No declared stock history')
    dates = [b['date'] for b in bars[symbols[0]]]
    indexes = {s: gross_return_index(bars[s], [e for e in events if e.symbol==s], mode='historical_reconstruction') for s in symbols}
    rows = []
    for i, day in enumerate(dates):
        if day < start or i < lookback+1 or i+horizon > len(dates):
            continue
        stocks = []
        for s in symbols:
            index, series = indexes[s], bars[s]
            score = index[i-1]['gross_return_index']/index[i-lookback-1]['gross_return_index']-1
            # Buying on an ex-date does not earn that day's dividend. Start
            # with its raw open-to-close mark, then grow the index after close.
            label = (series[i]['close']/series[i]['open']
                     *index[i+horizon-1]['gross_return_index']/index[i]['gross_return_index']-1)
            stocks.append({'symbol': s, 'score': score, 'label_return_pct': label*100,
                           'entry_zero_volume': series[i]['volume']==0})
        baseline = mean(s['label_return_pct'] for s in stocks)
        for stock in stocks:
            stock['relative_to_pool_pct'] = stock['label_return_pct']-baseline
        rows.append({'date': day, 'signal_date': dates[i-1], 'label_end': dates[i+horizon-1],
                     'rank_ic': rank_ic([s['score'] for s in stocks], [s['label_return_pct'] for s in stocks]),
                     'pool_mean_label_pct': baseline, 'stocks': stocks})
    return rows


def summarize(rows, horizon):
    def stats(subset):
        ic = [r['rank_ic'] for r in subset if r['rank_ic'] is not None]
        conditioned = []
        for threshold in RULES['thresholds']:
            above = [s for r in subset for s in r['stocks'] if s['score']>threshold]
            below = [s for r in subset for s in r['stocks'] if s['score']<=threshold]
            def group(stocks):
                return {'stock_labels': len(stocks), 'mean_mark_return_pct': mean(s['label_return_pct'] for s in stocks) if stocks else None,
                        'mean_relative_to_pool_pct': mean(s['relative_to_pool_pct'] for s in stocks) if stocks else None,
                        'positive_mark_fraction': sum(s['label_return_pct']>0 for s in stocks)/len(stocks) if stocks else None}
            conditioned.append({'threshold': threshold, 'above': group(above), 'at_or_below': group(below)})
        return {'entry_dates': len(subset), 'defined_ic_dates': len(ic),
                'undefined_ic_dates': len(subset)-len(ic), 'mean_rank_ic': mean(ic) if ic else None,
                'positive_ic_fraction': sum(v>0 for v in ic)/len(ic) if ic else None,
                'mean_pool_mark_return_pct': mean(r['pool_mean_label_pct'] for r in subset) if subset else None,
                'zero_volume_stock_labels': sum(s['entry_zero_volume'] for r in subset for s in r['stocks']),
                'conditions': conditioned}
    return {'all_overlapping_dates': stats(rows),
            'all_nonoverlap_offsets': [{'offset': i, **stats(rows[i::horizon])} for i in range(horizon)],
            'p_value': None, 'alpha_certified': False}


def run(protocol, experiments, manifest, reference, sources, *, progress=None):
    validate_protocol(protocol, experiments, manifest, reference)
    frozen = bindings(experiments, manifest, reference)
    all_events = [CashDividend(**e) for e in manifest['events']]
    validate_evidence(all_events, sources)
    results = []
    for exp in experiments:
        diagnose(exp)
        req = exp['request']; events = [e for e in all_events if e.symbol in req['symbols']]
        profiles = []
        for scope, bounds in frozen['windows'][exp['id']].items():
            if bounds is None:
                profiles.append({'window': scope, 'status': 'not_defined_no_pre_pause_period'})
                continue
            for lookback in RULES['lookbacks']:
                for horizon in RULES['horizons']:
                    if progress:
                        progress(exp['id'], scope, lookback, horizon)
                    rows = samples(exp['dataset'], req['symbols'], events, *bounds, lookback, horizon)
                    profiles.append({'window': scope, 'start': bounds[0], 'end': bounds[1],
                                     'lookback': lookback, 'horizon': horizon, 'summary': summarize(rows, horizon),
                                     'daily_rows': rows, 'daily_rows_digest': digest(rows)})
        results.append({'experiment': exp['id'], 'original_reproduced_exactly': True, 'profiles': profiles})
    if frozen != bindings(experiments, manifest, reference):
        raise ValueError('Inputs/source changed during diagnostic')
    return {'schema': protocol['schema'], 'protocol': protocol, 'protocol_digest': digest(protocol),
            'results': results, 'promotion_allowed': False, 'oos_validation': False, 'nomination': None,
            'limitations': ['seen surviving three-stock pools', 'partial cash-event coverage and non-PIT prices',
                            'hypothetical gross marks exclude tax/fees/slippage/limits/sizing/risk exits',
                            'overlapping labels and correlated stocks are not independent samples',
                            'no significance/alpha claim; all offsets retained; no holdout reassessment']}
