import copy
from decimal import localcontext, Inexact, Rounded
from fractions import Fraction
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
from deepfocus_api import quant_profitability_feature as feature
from deepfocus_api.tests.test_quant_financial_fact_derived import clock, ledger, setup, endpoints


def read(packet, **kwargs):
    args = dict(issuer_id='cninfo:9900002009', symbol='002109', period_end='2025-03-31',
                as_of='2026-10-03T00:00:00+08:00', asset_endpoints=endpoints())
    args.update(kwargs)
    return feature.review('a', packet['id'], **args)


@pytest.mark.parametrize('q', ['03-31', '06-30', '09-30'])
def test_exact_negative_profit_ratio_and_half_cent_denominator(clock, q):
    _, packet, _ = setup(q)
    with localcontext() as context:
        context.prec = 3
        context.traps[Inexact] = context.traps[Rounded] = True
        result = read(packet, period_end='2025-'+q, asset_endpoints=endpoints(q),
                      as_of='2025-12-01T00:00:00+08:00', mode='historical_reconstruction')
    assert result['status'] == 'arithmetic_available'
    assert result['feature']['exact_fraction'] == {'numerator': '-70000', 'denominator': '22000001'}
    assert result['feature']['display_percent'] == '-0.318182'
    assert result['inputs']['endpoint_average_assets']['value_exact_yuan'] == '110000.005'
    assert not result['usable_for_strategy'] and not result['scores_or_orders_generated']


def test_annual_single_flow_and_explicit_adjusted_beginning_assets(clock):
    _, packet, _ = setup()
    ep = {'beginning': {'report_period_end': '2024-03-31', 'column': 'previous_year_end_adjusted'},
          'ending': {'report_period_end': '2024-12-31', 'column': 'current_period'}}
    result = read(packet, period_end='2024-12-31', asset_endpoints=ep)
    ratio = result['feature']['exact_fraction']
    assert Fraction(int(ratio['numerator']), int(ratio['denominator'])) == Fraction('-370') / Fraction('120000.015')
    assert len(result['inputs']['result']['flow_components']) == 1


def test_default_recording_boundary_blocks_ratio_before_actual_visibility(clock, monkeypatch):
    _, packet, _ = setup()
    def forbidden(*args): raise AssertionError('No display calculation before visibility')
    monkeypatch.setattr(feature, '_percent', forbidden)
    result = read(packet, as_of='2026-10-02T23:59:59+08:00')
    assert result['status'] == 'unavailable' and result['inputs']['result'] is None
    assert result['feature']['exact_fraction'] is None and result['feature']['display_percent'] is None
    assert not result['ratios_generated']


@pytest.mark.parametrize('at,expected', [
    ('2026-04-30T23:59:59+08:00', Fraction('-350')/Fraction('110000.005')),
    ('2026-05-01T00:00:00+08:00', Fraction('-395')/Fraction('105000.015')),
])
def test_disclosure_correction_does_not_rewrite_earlier_feature(clock, at, expected):
    _, packet, _ = setup()
    result = read(packet, as_of=at, mode='historical_reconstruction')
    value = result['feature']['exact_fraction']
    assert Fraction(int(value['numerator']), int(value['denominator'])) == expected


@pytest.mark.parametrize('missing', ['profit', 'assets'])
def test_missing_latest_component_never_uses_older_document(clock, missing):
    def change(rows):
        rows[:] = [r for r in rows if not (r['document_id']=='222' and r['metric']==
                    ('consolidated_net_profit' if missing=='profit' else 'total_assets'))]
    _, packet, _ = setup(change=change)
    result = read(packet)
    assert result['status'] == 'unavailable' and result['feature']['exact_fraction'] is None
    assert not result['ratios_generated']


@pytest.mark.parametrize('amount', ['0.00', '-1.00'])
def test_nonpositive_endpoint_is_not_hidden_by_positive_average(clock, amount):
    def change(rows):
        for row in rows:
            if row['document_id']=='444' and row['metric']=='total_assets' and row['column']=='current_period':
                row['value'] = amount
    if amount.startswith('-'):
        # The existing extraction boundary is stricter and rejects this before
        # any feature can be queried; preserve that guard rather than bypass it.
        with pytest.raises(ValueError, match='Negative total assets unsupported'):
            setup(change=change)
        return
    _, packet, _ = setup(change=change)
    result = read(packet)
    assert result['status'] == 'unavailable' and result['availability_reason'] == 'nonpositive_asset_endpoint'
    assert result['feature']['exact_fraction'] is None


def test_reported_blank_is_not_zero(clock):
    def change(rows):
        for row in rows:
            if row['document_id']=='222' and row['metric']=='consolidated_net_profit': row['value']=None
    _, packet, _ = setup(change=change)
    result = read(packet)
    assert result['status']=='unavailable' and result['feature']['display_percent'] is None


def test_owner_endpoint_and_source_guards(clock, monkeypatch):
    _, packet, _ = setup()
    with pytest.raises(LookupError):
        feature.review('b', packet['id'], issuer_id='cninfo:9900002009', symbol='002109',
            period_end='2025-03-31', as_of='2026-10-03T00:00:00+08:00', asset_endpoints=endpoints())
    with pytest.raises(ValueError): read(packet, asset_endpoints=None)
    wrong = endpoints(); wrong['beginning']['report_period_end']='2025-03-31'
    with pytest.raises(ValueError): read(packet, asset_endpoints=wrong)
    monkeypatch.setattr(feature, '_pins', lambda: {})
    with pytest.raises(ValueError, match='source changed'): read(packet)


@pytest.mark.parametrize('value,expected', [
    (Fraction(1,200000000),'0.000000'), (Fraction(-1,200000000),'0.000000'),
    (Fraction(3,200000000),'0.000002'), (Fraction(-3,200000000),'-0.000002'),
])
def test_display_rounding_is_exact_symmetric_and_only_presentation(value, expected):
    assert feature._percent(value)==expected


def test_one_readonly_connection_is_closed_and_request_is_unchanged(clock, monkeypatch):
    _, packet, _ = setup(); ep=endpoints(); before=copy.deepcopy(ep); opened=[]; statements=[]
    real=sqlite3.connect
    def connect(*args, **kwargs):
        c=real(*args, **kwargs);c.set_trace_callback(statements.append);opened.append(c);return c
    monkeypatch.setattr(sqlite3,'connect',connect)
    result=read(packet,asset_endpoints=ep)
    assert ep==before and len(opened)==1 and result['inputs']['single_readonly_snapshot']
    assert not any(s.lstrip().split()[0].upper() in {'INSERT','UPDATE','DELETE','CREATE','REPLACE'} for s in statements)
    with pytest.raises(sqlite3.ProgrammingError):opened[0].execute('SELECT 1')
    assert all(isinstance(v,str) for v in result['feature']['exact_fraction'].values())


def test_cli_available_unavailable_wrong_owner_and_refuses_overwrite(clock, tmp_path):
    _, packet, _ = setup()
    import os
    db=Path(os.environ['DEEPFOCUS_QUANT_RESEARCH_DB_PATH']).resolve()
    script=Path(__file__).resolve().parents[3]/'scripts/quant-profitability-feature-review.py'
    args=[sys.executable,str(script),'--db',str(db),'--id',packet['id'],
          '--issuer-id','cninfo:9900002009','--symbol','002109','--period-end','2025-03-31',
          '--begin-report-period-end','2024-03-31','--begin-column','current_period',
          '--end-report-period-end','2025-03-31','--end-column','current_period']
    for name,owner,at,code in [('available','a','2026-10-03T00:00:00+08:00',0),
                              ('unavailable','a','2026-10-02T23:59:59+08:00',2),
                              ('other-owner','b','2026-10-03T00:00:00+08:00',1)]:
        output=tmp_path/(name+'.json')
        cmd=args+['--owner',owner,'--as-of',at,'--output',str(output)]
        proc=subprocess.run(cmd,capture_output=True,text=True,timeout=15)
        assert proc.returncode==code,proc.stderr
        if code==1:assert not output.exists()
        else:
            assert json.loads(output.read_text())['inputs']['request']['mode']=='as_recorded'
            original=output.read_bytes()
            assert subprocess.run(cmd,capture_output=True,timeout=15).returncode==2
            assert output.read_bytes()==original
