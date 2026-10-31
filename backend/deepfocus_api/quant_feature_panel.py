"""Explicit owner-bound descriptive panel; missing observations are never dropped."""
import copy
from datetime import timezone
import hashlib
import json
from pathlib import Path
import time

from . import quant_profitability_feature as feature
from .quant_research_engine import digest
from .quant_corporate_actions import timestamp

VERSION = 'qr-profitability-panel-v1'
POLICY = {
    'version': VERSION,
    'scope': 'explicit_observations_no_labels_ranking_training_or_orders',
    'selection': 'preserve_every_declared_cell_in_input_order_no_missing_filter',
    'mode': 'one_explicit_mode_for_whole_panel',
    'identity': 'one_cell_per_issuer_symbol_period_end_knowledge_clock_no_packet_duplicates',
    'snapshot': 'explicit_immutable_packet_per_cell_not_global_DB_snapshot',
    'limits': {'cells': 128, 'plan_bytes': 262144, 'result_bytes': 4194304, 'cooperative_seconds': 12},
    'qualification': 'always_unqualified_descriptive_research_only',
}


def _pins():
    return {**feature.LOADED_PINS,
            'quant_feature_panel.py': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


LOADED_PINS = _pins()


def require_current_source():
    feature.require_current_source()
    if _pins() != LOADED_PINS:
        raise ValueError('Loaded feature panel source changed')


def validate_plan(plan):
    if not isinstance(plan, dict) or set(plan) != {'schema', 'mode', 'cells'}:
        raise ValueError('Explicit panel schema, mode and cells required')
    if plan['schema'] != VERSION or plan['mode'] not in {'as_recorded', 'historical_reconstruction'}:
        raise ValueError('Unsupported panel version or knowledge mode')
    cells = plan['cells']
    if not isinstance(cells, list) or not 1 <= len(cells) <= 128:
        raise ValueError('A nonempty panel of at most 128 observations is required')
    if len(json.dumps(plan, ensure_ascii=False, allow_nan=False).encode()) > 262144:
        raise ValueError('Panel plan exceeds 256KiB')
    keys = {'packet_id', 'issuer_id', 'symbol', 'period_end', 'as_of', 'asset_endpoints'}
    seen = set()
    for cell in cells:
        if not isinstance(cell, dict) or set(cell) != keys:
            raise ValueError('Only explicit feature request fields are permitted')
        if any(not isinstance(cell[k], str) or not 1 <= len(cell[k]) <= 160
               for k in keys - {'asset_endpoints'}):
            raise ValueError('Bounded nonempty observation identity required')
        if not isinstance(cell['asset_endpoints'], dict):
            raise ValueError('Explicit asset endpoints required')
        key = tuple(cell[k] for k in ('issuer_id', 'symbol', 'period_end')) + (
            timestamp(cell['as_of']).astimezone(timezone.utc).isoformat(),)
        if key in seen:
            raise ValueError('Duplicate observation cannot select another packet or denominator')
        seen.add(key)
    return copy.deepcopy(plan)


def build(owner, plan):
    require_current_source()
    frozen = validate_plan(plan)
    deadline = time.monotonic() + 12
    rows = []
    size = 0
    for cell in frozen['cells']:
        if time.monotonic() >= deadline:
            raise TimeoutError('Cooperative feature panel budget exhausted')
        request = {k: v for k, v in cell.items() if k != 'packet_id'}
        observation = feature.review(owner, cell['packet_id'], mode=frozen['mode'], **request)
        if (observation['schema'] != feature.VERSION or observation['source_pins'] != feature.LOADED_PINS
                or observation['usable_for_strategy'] is not False or observation['promotion_allowed'] is not False):
            raise ValueError('Feature version, source or qualification contract changed')
        if observation['status'] not in {'arithmetic_available', 'unavailable'}:
            raise ValueError('Unknown feature availability')
        expected_request = {**request, 'mode': frozen['mode'], 'metric': 'consolidated_net_profit'}
        if (observation['inputs']['request'] != expected_request
                or observation['inputs']['receipt']['id'] != cell['packet_id']):
            raise ValueError('Feature response differs from declared observation')
        available = observation['status'] == 'arithmetic_available'
        if (observation['ratios_generated'] is not available
                or (observation['feature']['exact_fraction'] is None) == available):
            raise ValueError('Feature availability and missing value disagree')
        row = {'cell_id': digest({'mode': frozen['mode'], 'request': cell}),
               'request': cell, 'observation': observation}
        size += len(json.dumps(row, ensure_ascii=False, allow_nan=False).encode())
        if size > 4*1024**2:
            raise ValueError('Feature panel exceeds 4MiB result budget')
        rows.append(row)
    if time.monotonic() >= deadline:
        raise TimeoutError('Cooperative feature panel budget exhausted')
    missing = [r['observation']['status'] == 'unavailable' for r in rows]
    report = {
        'schema': VERSION, 'policy': copy.deepcopy(POLICY), 'plan': frozen, 'plan_digest': digest(frozen),
        'rows': rows, 'missing_mask': missing,
        'declared_cells': len(rows), 'available_cells': len(rows)-sum(missing), 'unavailable_cells': sum(missing),
        'status': 'incomplete' if any(missing) else 'arithmetic_available',
        'global_database_snapshot': False, 'explicit_immutable_packet_per_cell': True,
        'mode': frozen['mode'], 'source_pins': dict(LOADED_PINS),
        'labels_or_training_generated': False, 'cross_sectional_coverage_verified': False,
        'new_unseen_validation': False, 'usable_for_strategy': False,
        'promotion_allowed': False, 'commercial_ready': False,
    }
    report['report_digest'] = digest(report)
    if len(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2).encode()) > 4*1024**2:
        raise ValueError('Feature panel exceeds 4MiB result budget')
    require_current_source()
    return report
