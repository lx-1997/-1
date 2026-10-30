"""Strict mutual date alignment for new research inputs, not a calendar oracle.

Identical price dates do not prove that an exchange session was not omitted by
every source. Never fill absent prices or infer a suspension from an absent bar.
The frozen legacy mathematical kernel stays unchanged for historical replay.
"""
import copy

from .quant_research_engine import digest, validate_dataset

SCHEMA = 'research-calendar-alignment-v1'


class CalendarAlignmentError(ValueError):
    def __init__(self, report, dataset=None):
        super().__init__('研究行情日期或标的范围不完整，已拒绝截取日期交集；请核对缺失数据和证券状态后重新提交')
        self.report = copy.deepcopy(report)
        self.dataset = copy.deepcopy(dataset) if dataset is not None else None


def require_aligned(dataset, *, benchmark=None, expected_symbols=None):
    report = {'schema': SCHEMA, 'status': 'rejected',
              'exchange_calendar_verified': False,
              'all_series_missing_same_session_detectable': False,
              'missing_bar_is_suspension': False, 'forward_fill': False,
              'intersection_trimming': False}
    try:
        validate_dataset(dataset)
    except (ValueError, TypeError, KeyError, AttributeError, IndexError):
        # Malformed input can contain non-JSON numbers or provider text. Keep
        # only a safe diagnostic; do not persist that raw payload as JSON.
        report['reason'] = 'invalid_market_input'
        raise CalendarAlignmentError(report) from None
    expected = list(expected_symbols) if expected_symbols is not None else list(dataset)
    if (len(expected) != len(set(expected)) or set(dataset) != set(expected)
            or (benchmark is not None and benchmark not in dataset)):
        report['reason'] = 'unexpected_symbol_scope'
        raise CalendarAlignmentError(report, dataset)
    calendars = {s: [b['date'] for b in p['bars']] for s, p in dataset.items()}
    union = sorted(set().union(*(set(v) for v in calendars.values())))
    common = sorted(set.intersection(*(set(v) for v in calendars.values())))
    anchor = benchmark if benchmark is not None else sorted(dataset)[0]
    reference = set(calendars[anchor])
    report.update(anchor_symbol=anchor, union_sessions=len(union),
                  common_sessions=len(common), union_dates_digest=digest(union),
                  symbols={s: {'observed_sessions': len(days), 'dates_digest': digest(days),
                               'missing_vs_union': sorted(set(union)-set(days)),
                               'missing_vs_anchor': sorted(reference-set(days)),
                               'extra_vs_anchor': sorted(set(days)-reference)}
                           for s, days in calendars.items()})
    if any(days != calendars[anchor] for days in calendars.values()):
        report['reason'] = 'series_dates_differ'
        raise CalendarAlignmentError(report, dataset)
    report.update(status='aligned', reason='all_observed_series_dates_equal',
                  first=common[0], last=common[-1], dates_digest=digest(common))
    return report
