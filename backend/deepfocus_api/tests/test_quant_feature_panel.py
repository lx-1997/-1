import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from deepfocus_api import quant_feature_panel as panel
from deepfocus_api.tests.test_quant_financial_fact_derived import clock, ledger, setup, endpoints


def plan(packet):
    return {'schema': panel.VERSION, 'mode': 'as_recorded', 'cells': [
        {'packet_id': packet['id'], 'issuer_id': 'cninfo:9900002009', 'symbol': '002109',
         'period_end': '2025-03-31', 'as_of': '2026-10-03T00:00:00+08:00', 'asset_endpoints': endpoints()},
        {'packet_id': packet['id'], 'issuer_id': 'cninfo:9900002009', 'symbol': '002109',
         'period_end': '2025-03-31', 'as_of': '2026-10-02T23:59:59+08:00', 'asset_endpoints': endpoints()}]}


def test_every_observation_and_missing_mask_preserve_declared_order(clock):
    _, packet, _ = setup(); declaration=plan(packet); before=copy.deepcopy(declaration)
    result=panel.build('a',declaration)
    assert declaration==before and result['plan']==declaration
    assert [r['request'] for r in result['rows']]==declaration['cells']
    assert result['status']=='incomplete' and result['missing_mask']==[False,True]
    assert (result['declared_cells'],result['available_cells'],result['unavailable_cells'])==(2,1,1)
    assert result['rows'][1]['observation']['feature']['exact_fraction'] is None
    assert not result['usable_for_strategy'] and not result['global_database_snapshot']
    report=copy.deepcopy(result);fingerprint=report.pop('report_digest')
    assert fingerprint==panel.digest(report) and result==panel.build('a',declaration)


@pytest.mark.parametrize('variant', ['same-clock','equivalent-clock','different-packet','different-denominator'])
def test_duplicate_observation_is_rejected_before_read(clock,monkeypatch,variant):
    _,packet,_=setup();declaration=plan(packet);declaration['cells'][1]=copy.deepcopy(declaration['cells'][0])
    if variant=='equivalent-clock':declaration['cells'][1]['as_of']='2026-10-02T16:00:00Z'
    if variant=='different-packet':declaration['cells'][1]['packet_id']='another-packet'
    if variant=='different-denominator':declaration['cells'][1]['asset_endpoints']['beginning']['column']='previous_year_end'
    def forbidden(*args,**kwargs):raise AssertionError('must reject before database read')
    monkeypatch.setattr(panel.feature,'review',forbidden)
    with pytest.raises(ValueError,match='Duplicate observation'):panel.build('a',declaration)


@pytest.mark.parametrize('variant', ['empty','too-many','mixed-mode','owner-field','label-field','oversized','nan'])
def test_plan_budget_and_unknown_fields_fail_before_read(clock,monkeypatch,variant):
    _,packet,_=setup();declaration=plan(packet)
    if variant=='empty':declaration['cells']=[]
    if variant=='too-many':declaration['cells']=declaration['cells']*65
    if variant=='mixed-mode':declaration['cells'][1]['mode']='historical_reconstruction'
    if variant=='owner-field':declaration['owner']='a'
    if variant=='label-field':declaration['cells'][0]['target_return']=.1
    if variant=='oversized':declaration['cells'][0]['packet_id']='x'*262144
    if variant=='nan':declaration['cells'][0]['asset_endpoints']['unexpected']=float('nan')
    def forbidden(*args,**kwargs):raise AssertionError('must reject before database read')
    monkeypatch.setattr(panel.feature,'review',forbidden)
    with pytest.raises((ValueError,TypeError)):panel.build('a',declaration)


def test_owner_and_late_missing_packet_cannot_publish_partial_panel(clock):
    _,packet,_=setup();declaration=plan(packet)
    with pytest.raises(LookupError):panel.build('b',declaration)
    declaration['cells'][1]['packet_id']='nonexistent'
    with pytest.raises(LookupError):panel.build('a',declaration)


@pytest.mark.parametrize('variant', ['source','mode','packet','missing-value','eligibility','unknown-status'])
def test_response_cannot_silently_change_provenance_or_missingness(clock,monkeypatch,variant):
    _,packet,_=setup();declaration=plan(packet);declaration['cells']=declaration['cells'][:1]
    cell=declaration['cells'][0];args={k:v for k,v in cell.items() if k!='packet_id'}
    real=panel.feature.review('a',packet['id'],mode='as_recorded',**args)
    if variant=='source':real['source_pins']={}
    if variant=='mode':real['inputs']['request']['mode']='historical_reconstruction'
    if variant=='packet':real['inputs']['receipt']['id']='other-packet'
    if variant=='missing-value':real['feature']['exact_fraction']=None
    if variant=='eligibility':real['promotion_allowed']=True
    if variant=='unknown-status':real['status']='successful'
    monkeypatch.setattr(panel.feature,'review',lambda *a,**k:real)
    with pytest.raises(ValueError):panel.build('a',declaration)


def test_cooperative_deadline_retains_failure_instead_of_partial_result(clock,monkeypatch):
    _,packet,_=setup();declaration=plan(packet);calls=[];real=panel.feature.review;now=[0]
    def read(*args,**kwargs):
        calls.append(1);value=real(*args,**kwargs);now[0]=13;return value
    monkeypatch.setattr(panel,'time',SimpleNamespace(monotonic=lambda:now[0]))
    monkeypatch.setattr(panel.feature,'review',read)
    with pytest.raises(TimeoutError):panel.build('a',declaration)
    assert len(calls)==1


def test_source_guard_and_output_budget(clock,monkeypatch):
    _,packet,_=setup();declaration=plan(packet);declaration['cells']=declaration['cells'][:1]
    cell=declaration['cells'][0];args={k:v for k,v in cell.items() if k!='packet_id'}
    observation=panel.feature.review('a',packet['id'],mode='as_recorded',**args)
    observation['oversized']='x'*(4*1024**2)
    monkeypatch.setattr(panel.feature,'review',lambda *a,**k:observation)
    with pytest.raises(ValueError,match='4MiB'):panel.build('a',declaration)
    monkeypatch.setattr(panel,'_pins',lambda:{})
    with pytest.raises(ValueError,match='source changed'):panel.build('a',declaration)


def test_cli_complete_incomplete_wrong_owner_and_no_overwrite(clock,tmp_path):
    _,packet,_=setup();script=Path(__file__).resolve().parents[3]/'scripts/quant-feature-panel-review.py'
    for name,owner,complete,code in [('complete','a',True,0),('incomplete','a',False,2),('wrong-owner','b',False,1),('late-bad','a',False,1)]:
        declaration=plan(packet)
        if complete:declaration['cells']=declaration['cells'][:1]
        if name=='late-bad':declaration['cells'][1]['packet_id']='nonexistent'
        request=tmp_path/(name+'-plan.json');request.write_text(json.dumps(declaration));output=tmp_path/(name+'.json')
        cmd=[sys.executable,str(script),'--db',str(clock.resolve()),'--owner',owner,'--plan',str(request),'--output',str(output)]
        run=subprocess.run(cmd,capture_output=True,text=True,timeout=20)
        assert run.returncode==code,run.stderr
        if code==1:assert not output.exists()
        else:
            report=json.loads(output.read_text());assert len(report['rows'])==len(declaration['cells'])
            saved=output.read_bytes();assert subprocess.run(cmd,capture_output=True,timeout=20).returncode==2
            assert output.read_bytes()==saved


def test_cli_declaration_changed_mid_read_never_writes_result(clock,tmp_path,monkeypatch):
    _,packet,_=setup();path=Path(__file__).resolve().parents[3]/'scripts/quant-feature-panel-review.py'
    spec=importlib.util.spec_from_file_location('owned_panel_cli',path);cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    declaration=plan(packet);request=tmp_path/'declared.json';request.write_text(json.dumps(declaration));output=tmp_path/'result.json'
    result=panel.build('a',declaration)
    def altered(*args):request.write_bytes(b'x'*262145);return result
    monkeypatch.setattr(cli.panel,'build',altered)
    monkeypatch.setattr(sys,'argv',[str(path),'--db',str(clock.resolve()),'--owner','a','--plan',str(request),'--output',str(output)])
    with pytest.raises(ValueError,match='declaration changed'):cli.main()
    assert not output.exists()


@pytest.mark.parametrize('failure', ['fsync','late-destination'])
def test_atomic_publication_never_leaves_partial_or_overwrites(tmp_path,monkeypatch,failure):
    path=Path(__file__).resolve().parents[3]/'scripts/quant-feature-panel-review.py'
    spec=importlib.util.spec_from_file_location('owned_publish_cli',path);cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    output=tmp_path/'result.json'
    if failure=='fsync':
        def failed(*args):raise OSError('synthetic disk flush failure')
        monkeypatch.setattr(cli.os,'fsync',failed)
    else:
        def raced(*args):output.write_bytes(b'existing-other-writer');raise FileExistsError()
        monkeypatch.setattr(cli.os,'link',raced)
    with pytest.raises(OSError):cli.publish({'report':'complete'},output)
    if failure=='fsync':assert not output.exists()
    else:assert output.read_bytes()==b'existing-other-writer'
    assert not list(tmp_path.glob('.quant-panel-*'))
