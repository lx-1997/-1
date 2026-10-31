"""Outcome-blind pilot sampling from an explicit historical membership snapshot.

Not an investable universe, strategy selection, or unseen-data certificate.
Names, current status, returns and later lifecycle are not selection inputs.
"""
from datetime import date
import hashlib
import re

SIZE = 64
NAMESPACE = 'daocaijing-internal-research-universe-v1'
FIELDS = 'date,code,open,high,low,close,preclose,volume,amount,adjustflag,turn,tradestatus,isST'
RULES = {'size': SIZE, 'namespace': NAMESPACE, 'ranking': 'sha256(namespace:historical_start:code),then_code',
         'eligible': 'all_members_of_bound_historical_start_snapshot',
         'current_status_filter': False, 'tradestatus_filter': False, 'replace_failures': False,
         'outcome_selection': False, 'strategy_evaluations': 0, 'promotion_allowed': False,
         'new_unseen_validation': False, 'economic_event_completeness_verified': False}


def sample(codes, start, end):
    date.fromisoformat(start); date.fromisoformat(end)
    if not start < end: raise ValueError('Historical interval must increase')
    if (not isinstance(codes, list) or not SIZE <= len(codes) <= 10000
            or any(not isinstance(c, str) or not re.fullmatch(r'(sh\.6[0-9]{5}|sz\.[03][0-9]{5})', c) for c in codes)
            or len(set(codes)) != len(codes)):
        raise ValueError('Unique explicit scoped historical members required; no size fallback')
    ranked = sorted(codes, key=lambda c: (hashlib.sha256((NAMESPACE + ':' + start + ':' + c).encode('ascii')).hexdigest(), c))
    return [{'code': c, 'fields': FIELDS, 'start_date': start, 'end_date': end, 'frequency': 'd', 'adjustflag': '3'}
            for c in ranked[:SIZE]]


def validate_plan(plan):
    if (plan.get('schema') != 'historical-research-sample-v1' or plan.get('rules') != RULES
            or plan.get('requests') != sample(plan['eligible_codes'], plan['start'], plan['end'])):
        raise ValueError('Unregistered or outcome-selected sample plan')
    return plan['requests']
