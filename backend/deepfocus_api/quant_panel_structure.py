"""Count declared research units; repeated clocks are not independent evidence."""
from collections import defaultdict
from datetime import timezone
import hashlib
from pathlib import Path

from . import quant_feature_panel as panel
from .quant_corporate_actions import timestamp
from .quant_research_engine import digest

VERSION = 'qr-profitability-panel-structure-v1'
POLICY = {
    'issuer_identity': 'issuer_id_and_symbol_pair; conflicting_identity_mapping_rejected',
    'base_observation': 'issuer_symbol_report_period; clocks_or_packets_do_not_add_units',
    'cross_section': 'exact_canonical_UTC_knowledge_clock; periods_not_silently_selected',
    'missing': 'all_declared_cells_retained_in_counts_no_zero_or_forward_fill',
    'interpretation': 'counts_not_effective_independent_sample_size_or_factor_validation',
}


def _pins():
    return {**panel.LOADED_PINS,
            'quant_panel_structure.py': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


LOADED_PINS = _pins()


def require_current_source():
    panel.require_current_source()
    if _pins() != LOADED_PINS:
        raise ValueError('Loaded panel structure source changed')


def _summarize(report):
    """Internal projection of the panel just built, without choosing observations."""
    bases = defaultdict(list)
    clocks = defaultdict(list)
    issuers = set()
    issuer_symbols, symbol_issuers = {}, {}
    for row in report['rows']:
        cell = row['request']
        issuer, symbol = cell['issuer_id'], cell['symbol']
        if (issuer_symbols.setdefault(issuer, symbol) != symbol
                or symbol_issuers.setdefault(symbol, issuer) != issuer):
            raise ValueError('Conflicting issuer and symbol identities require explicit resolution')
        clock = timestamp(cell['as_of']).astimezone(timezone.utc).isoformat()
        identity = (issuer, symbol)
        issuers.add(identity)
        bases[(*identity, cell['period_end'])].append((clock, row))
        clocks[clock].append(row)
    units = []
    for (issuer, symbol, period), observations in sorted(bases.items()):
        rows = [row for _, row in observations]
        available = sum(row['observation']['status'] == 'arithmetic_available' for row in rows)
        units.append({'issuer_id': issuer, 'symbol': symbol, 'period_end': period,
                      'declared_cells': len(rows), 'available_cells': available,
                      'unavailable_cells': len(rows)-available,
                      'distinct_knowledge_clocks': len({c for c, _ in observations}),
                      'distinct_packet_ids': len({r['request']['packet_id'] for r in rows})})
    sections = []
    for clock, rows in sorted(clocks.items()):
        requested = defaultdict(list)
        for row in rows:
            c = row['request']
            requested[(c['issuer_id'], c['symbol'])].append(row)
        ambiguous = sum(len(group) > 1 for group in requested.values())
        available = sum(any(r['observation']['status'] == 'arithmetic_available' for r in group)
                        for group in requested.values())
        sections.append({'as_of_utc': clock, 'declared_cells': len(rows),
                         'declared_issuers': len(requested),
                         'issuers_with_any_available_cell': available,
                         'issuers_with_no_available_cell': len(requested)-available,
                         'issuers_with_multiple_report_periods': ambiguous,
                         'common_period_end': next(iter({r['request']['period_end'] for r in rows}))
                             if len({r['request']['period_end'] for r in rows}) == 1 else None,
                         'one_cell_per_issuer': ambiguous == 0})
    n = len(report['rows'])
    return {'schema': VERSION, 'policy': dict(POLICY),
            'parent_report_digest': report['report_digest'], 'plan_digest': report['plan_digest'],
            'mode': report['mode'], 'declared_cells': n,
            'available_cells': report['available_cells'], 'unavailable_cells': report['unavailable_cells'],
            'missing_mask': list(report['missing_mask']),
            'distinct_issuers': len(issuers), 'distinct_issuer_period_units': len(bases),
            'additional_clock_or_packet_cells': n-len(bases),
            'distinct_knowledge_clocks': len(clocks),
            'maximum_declared_issuers_at_one_clock': max(s['declared_issuers'] for s in sections),
            'maximum_issuers_with_any_available_cell_at_one_clock':
                max(s['issuers_with_any_available_cell'] for s in sections),
            'issuer_period_units': units, 'cross_sections': sections,
            'status': 'structure_only_not_factor_validation',
            'independent_sample_size': None, 'factor_effectiveness_evaluated': False,
            'accounting_comparability_verified': False, 'cross_sectional_coverage_verified': False,
            'labels_or_returns_generated': False, 'new_unseen_validation': False,
            'usable_for_strategy': False, 'promotion_allowed': False, 'commercial_ready': False}


def build(owner, plan):
    require_current_source()
    # Authenticate and recompute the entire declared panel, rather than trust a
    # caller-supplied report hash as proof of provenance or owner authority.
    parent = panel.build(owner, plan)
    result = {**_summarize(parent), 'source_pins': dict(LOADED_PINS)}
    result['report_digest'] = digest(result)
    require_current_source()
    return result
