"""Describe coverage of a declared blind cohort without dropping missing members."""
import copy
from datetime import timezone
import hashlib
import json
from pathlib import Path

from . import quant_feature_panel as panel, quant_research_sample as sample
from .quant_corporate_actions import timestamp
from .quant_research_engine import digest

VERSION = 'qr-cohort-financial-coverage-v1'
POLICY = {
    'denominator': 'every_member_in_validated_declared_sample_order',
    'scope': 'one_explicit_report_period_and_canonical_knowledge_clock',
    'missing': 'distinguish_not_declared_from_declared_unavailable_no_imputation',
    'authority': 'recompute_owned_panel_not_caller_supplied_results',
    'universe': 'sampling_algorithm_checked_not_historical_membership_certification',
}


def _pins():
    return {**panel.LOADED_PINS, **{name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in ('quant_cohort_coverage.py', 'quant_research_sample.py')}}


LOADED_PINS = _pins()


def require_current_source():
    panel.require_current_source()
    if _pins() != LOADED_PINS:
        raise ValueError('Loaded cohort coverage source changed')


def build(owner, sample_plan, panel_plan):
    require_current_source()
    if not isinstance(sample_plan, dict):
        raise ValueError('Explicit blind sample plan required')
    if len(json.dumps(sample_plan, allow_nan=False).encode()) > 1024**2:
        raise ValueError('Sample plan exceeds 1MiB')
    frozen_sample = copy.deepcopy(sample_plan)
    members = sample.validate_plan(frozen_sample)
    frozen_panel = panel.validate_plan(panel_plan)
    codes = {q['code'].split('.')[1] for q in members}
    periods, clocks, symbols = set(), set(), set()
    issuer_symbols = {}
    for cell in frozen_panel['cells']:
        symbol, issuer = cell['symbol'], cell['issuer_id']
        if symbol not in codes:
            raise ValueError('Panel member is outside the declared blind cohort')
        if symbol in symbols or issuer_symbols.setdefault(issuer, symbol) != symbol:
            raise ValueError('One unambiguous issuer observation per cohort member required')
        symbols.add(symbol)
        periods.add(cell['period_end'])
        clocks.add(timestamp(cell['as_of']).astimezone(timezone.utc).isoformat())
    if len(periods) != 1 or len(clocks) != 1:
        raise ValueError('One report period and knowledge clock required; do not select silently')
    parent = panel.build(owner, frozen_panel)
    observed = {row['request']['symbol']: (i, row) for i, row in enumerate(parent['rows'])}
    rows = []
    for rank, member in enumerate(members):
        symbol = member['code'].split('.')[1]
        entry = observed.get(symbol)
        status = ('not_declared_in_panel' if entry is None else
                  'arithmetic_available' if entry[1]['observation']['status'] == 'arithmetic_available'
                  else 'declared_unavailable')
        rows.append({'sample_rank': rank, 'code': member['code'], 'status': status,
                     'panel_row': entry[0] if entry is not None else None,
                     'missing': status != 'arithmetic_available'})
    available = sum(not row['missing'] for row in rows)
    result = {'schema': VERSION, 'policy': dict(POLICY), 'source_pins': dict(LOADED_PINS),
              'sample_plan_digest': digest(frozen_sample), 'panel_plan_digest': parent['plan_digest'],
              'parent_panel_report_digest': parent['report_digest'], 'mode': parent['mode'],
              'period_end': next(iter(periods)), 'as_of_utc': next(iter(clocks)),
              'cohort_members': len(rows), 'declared_panel_members': len(observed),
              'available_members': available, 'unavailable_members': len(rows)-available,
              'not_declared_members': sum(row['status'] == 'not_declared_in_panel' for row in rows),
              'declared_unavailable_members': sum(row['status'] == 'declared_unavailable' for row in rows),
              'coverage_fraction': {'numerator': available, 'denominator': len(rows)},
              'rows': rows, 'missing_mask': [row['missing'] for row in rows],
              'status': 'coverage_only_not_factor_validation',
              'historical_membership_source_verified': False, 'full_revision_history_certified': False,
              'accounting_comparability_verified': False, 'independent_sample_size': None,
              'labels_or_returns_generated': False, 'usable_for_strategy': False,
              'promotion_allowed': False, 'commercial_ready': False}
    result['report_digest'] = digest(result)
    require_current_source()
    return result
