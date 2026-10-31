import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest
from deepfocus_api import quant_panel_structure as structure
from deepfocus_api.tests.test_quant_feature_panel import plan
from deepfocus_api.tests.test_quant_financial_fact_derived import clock, ledger, setup


def test_real_panel_recomputed_once_owner_missingness_and_no_independence_claim(clock,monkeypatch):
    _,packet,_=setup(); declaration=plan(packet); before=copy.deepcopy(declaration)
    original=structure.panel.build; calls=[]
    def compute(*args):
        value=original(*args);calls.append(value);return value
    monkeypatch.setattr(structure.panel,'build',compute)
    result=structure.build('a',declaration)
    assert len(calls)==1 and declaration==before
    assert result['parent_report_digest']==calls[0]['report_digest']
    assert result['missing_mask']==[False,True]
    assert (result['distinct_issuers'],result['distinct_issuer_period_units'],result['additional_clock_or_packet_cells'])==(1,1,1)
    assert result['maximum_declared_issuers_at_one_clock']==1
    assert result['issuer_period_units'][0]['available_cells']==1
    assert result['independent_sample_size'] is None
    assert all(result[k] is False for k in ['factor_effectiveness_evaluated','cross_sectional_coverage_verified','usable_for_strategy','promotion_allowed'])
    raw=copy.deepcopy(result);fingerprint=raw.pop('report_digest');assert structure.digest(raw)==fingerprint
    with pytest.raises(LookupError):structure.build('b',declaration)


def synthetic(rows):
    """Projection-only fixture, not a forged financial packet or production input."""
    r=[]
    for issuer,symbol,period,clock,available,packet in rows:
        r.append({'request':{'issuer_id':issuer,'symbol':symbol,'period_end':period,'as_of':clock,'packet_id':packet},
                  'observation':{'status':'arithmetic_available' if available else 'unavailable'}})
    mask=[row['observation']['status']=='unavailable' for row in r]
    return {'rows':r,'report_digest':'synthetic-parent','plan_digest':'synthetic-plan','mode':'as_recorded',
            'available_cells':len(r)-sum(mask),'unavailable_cells':sum(mask),'missing_mask':mask}


def test_clock_aliases_period_ambiguity_revisions_and_missing_cells_are_not_independent_units():
    rows=[('a','000001','2025-03-31','2026-10-03T00:00:00+08:00',True,'p1'),
          ('b','000002','2025-03-31','2026-10-02T16:00:00Z',False,'p2'),
          ('a','000001','2025-06-30','2026-10-02T16:00:00Z',True,'p3'),
          ('a','000001','2025-03-31','2026-10-04T00:00:00+08:00',False,'p4')]
    r=structure._summarize(synthetic(rows))
    assert (r['distinct_issuers'],r['distinct_issuer_period_units'],r['distinct_knowledge_clocks'])==(2,3,2)
    assert r['additional_clock_or_packet_cells']==1 and r['missing_mask']==[False,True,False,True]
    s=r['cross_sections'][0]
    assert (s['declared_cells'],s['declared_issuers'],s['issuers_with_any_available_cell'],s['issuers_with_no_available_cell'])==(3,2,1,1)
    assert s['issuers_with_multiple_report_periods']==1 and s['common_period_end'] is None and not s['one_cell_per_issuer']
    assert r['issuer_period_units'][0]['distinct_packet_ids']==2


@pytest.mark.parametrize('available',[True,False])
def test_128_clocks_of_one_company_remain_one_base_unit(available):
    from datetime import datetime,timedelta,timezone
    rows=[('a','000001','2025-03-31',(datetime(2026,10,3,tzinfo=timezone.utc)+timedelta(seconds=i)).isoformat(),available,'p') for i in range(128)]
    r=structure._summarize(synthetic(rows))
    assert r['declared_cells']==128 and r['distinct_issuer_period_units']==1
    assert r['additional_clock_or_packet_cells']==127 and r['maximum_declared_issuers_at_one_clock']==1
    assert r['maximum_issuers_with_any_available_cell_at_one_clock']==int(available)
    assert r['independent_sample_size'] is None


@pytest.mark.parametrize('identities',[[('a','000001'),('a','000002')],[('a','000001'),('b','000001')]])
def test_unresolved_identity_mapping_is_rejected_not_merged(identities):
    rows=[(*identity,'2025-03-31','2026-10-03T00:00:00Z',True,'p') for identity in identities]
    with pytest.raises(ValueError,match='Conflicting issuer'):structure._summarize(synthetic(rows))


def test_loaded_structure_source_guard(clock,monkeypatch):
    monkeypatch.setattr(structure,'_pins',lambda:{})
    with pytest.raises(ValueError,match='source changed'):structure.build('a',{})


def cli_module():
    script=Path(__file__).resolve().parents[3]/'scripts/quant-panel-structure-review.py'
    spec=importlib.util.spec_from_file_location('structure_cli',script);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return script,module


def test_actual_cli_missingness_owner_and_no_overwrite(clock,tmp_path):
    _,packet,_=setup();script,_=cli_module();request=tmp_path/'plan.json';request.write_text(json.dumps(plan(packet)))
    output=tmp_path/'result.json';cmd=[sys.executable,str(script),'--db',str(clock.resolve()),'--owner','a','--plan',str(request),'--output',str(output)]
    run=subprocess.run(cmd,capture_output=True,text=True,timeout=20);assert run.returncode==0,run.stderr
    saved=output.read_bytes();r=json.loads(saved);assert r['missing_mask']==[False,True] and not r['promotion_allowed']
    assert subprocess.run(cmd,capture_output=True,timeout=20).returncode==2 and output.read_bytes()==saved
    cmd[cmd.index('--owner')+1]='b';cmd[-1]=str(tmp_path/'foreign.json')
    assert subprocess.run(cmd,capture_output=True,timeout=20).returncode==1 and not Path(cmd[-1]).exists()


@pytest.mark.parametrize('failure',['declaration','fsync','race'])
def test_cli_failure_never_publishes_partial_or_overwrites(clock,tmp_path,monkeypatch,failure):
    _,packet,_=setup();script,cli=cli_module();request=tmp_path/'plan.json';request.write_text(json.dumps(plan(packet)));out=tmp_path/'out.json'
    value=structure.build('a',plan(packet))
    def build(*args):
        if failure=='declaration':request.write_bytes(b'changed')
        return value
    monkeypatch.setattr(cli.structure,'build',build)
    if failure=='fsync':
        def fail(*args):raise OSError('synthetic flush failure')
        monkeypatch.setattr(cli.os,'fsync',fail)
    if failure=='race':
        def fail(*args):out.write_bytes(b'racing-writer');raise FileExistsError()
        monkeypatch.setattr(cli.os,'link',fail)
    monkeypatch.setattr(sys,'argv',[str(script),'--db',str(clock.resolve()),'--owner','a','--plan',str(request),'--output',str(out)])
    with pytest.raises((ValueError,OSError)):cli.main()
    assert out.read_bytes()==b'racing-writer' if failure=='race' else not out.exists()
    assert not list(tmp_path.glob('.quant-structure-*'))
