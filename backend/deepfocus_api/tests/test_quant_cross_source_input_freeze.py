"""Deterministic file-change injection at the real child-process boundary.

All inputs are synthetic; actual accounting subprocesses still perform the work.
"""
import hashlib,importlib.util,json,subprocess,sys
from pathlib import Path
import pytest
from deepfocus_api import ai_fund_market as market
from deepfocus_api.tests.test_quant_capitalization_real_prices_cli import inputs
from deepfocus_api.tests.test_quant_capitalization_cross_source import fixture_pair,URL,CLOCK
from deepfocus_api.tests.test_quant_capitalization_prices import rebind

ROOT=Path(__file__).resolve().parents[3]


def prepare(tmp_path):
    bao=inputs(tmp_path);account=tmp_path/'account'
    result=subprocess.run([sys.executable,str(ROOT/'scripts/quant-capitalization-real-price-review.py'),'--capture',str(bao),'--action-plan',str(tmp_path/'plan.json'),'--facts',str(tmp_path/'facts.json'),'--workspace',str(account)],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
    sina=tmp_path/'sina';sina.mkdir();_,_,rows=fixture_pair();body=json.dumps(rows).encode();(sina/'response.bin').write_bytes(body)
    m={'schema':'capitalization-sina-raw-capture-v1','errors':[],'status':200,'request_url':URL,'final_url':URL,'symbol':'300750','provider':'sina','price_adjustment':'none',
       'collector_sha256':hashlib.sha256((ROOT/'scripts/capture-quant-capitalization-cross-source.py').read_bytes()).hexdigest(),
       'parser_sha256':hashlib.sha256(Path(market.__file__).read_bytes()).hexdigest(),'bytes':len(body),'response_sha256':hashlib.sha256(body).hexdigest(),'observed_at':CLOCK}
    (sina/'manifest.json').write_text(json.dumps(m));return bao,account,sina


def change_bao(folder):
    path=folder/'manifest.json';m=json.loads(path.read_text());sources={n:(folder/n).read_bytes() for n in ['sdk-response-0.bin','sdk-response-1.bin','sdk-response-2.bin']}
    m['rows'][0][3]='10.3000';rebind(m,sources)
    for p in [path,folder/'sdk-response-1.bin']:
        p.chmod(0o600)
    path.write_text(json.dumps(m));(folder/'sdk-response-1.bin').write_bytes(sources['sdk-response-1.bin'])


@pytest.mark.parametrize('mode',['none','live-bao','live-facts','live-pdf','live-reference','owned-snapshot'])
def test_comparison_and_children_use_one_frozen_input_revision(tmp_path,monkeypatch,mode):
    bao,account,sina=prepare(tmp_path);workspace=tmp_path/'review';expected_manifest=hashlib.sha256((bao/'manifest.json').read_bytes()).hexdigest()
    spec=importlib.util.spec_from_file_location('cross_freeze_test',ROOT/'scripts/quant-capitalization-cross-source-review.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    actual_run=subprocess.run;changed=[]
    def boundary(cmd,*args,**kwargs):
        if 'quant-capitalization-real-price-review.py' in str(cmd[1]) and not changed:
            changed.append(True)
            if mode=='live-bao':change_bao(bao)
            if mode=='live-facts':(account/'facts.json').write_text('{}')
            if mode=='live-pdf':(tmp_path/'unit.pdf').write_bytes(b'%PDF-1.7 changed after freeze')
            if mode=='live-reference':(account/'ledger-review/daily-valuations.json').write_text('[]')
            if mode=='owned-snapshot':change_bao(Path(cmd[cmd.index('--capture')+1]))
        return actual_run(cmd,*args,**kwargs)
    monkeypatch.setattr(module.subprocess,'run',boundary)
    monkeypatch.setattr(sys,'argv',['cross-review','--sina',str(sina),'--baostock',str(bao),'--accounting',str(account),'--workspace',str(workspace)])
    code=module.main();r=json.loads((workspace/'result.json').read_text());assert changed
    if mode=='owned-snapshot':
        assert code==2 and r['status']=='rejected' and r['failed_stage']=='frozen_integrity'
        assert not (workspace/'secondary-rebuild').exists()
    else:
        assert code==0 and r['status']=='cross_source_accounting_equal'
        normalized=json.loads((workspace/'primary-rebuild/normalized-prices.json').read_text())
        assert normalized['rows'][0]['high']=='10.2000'
        assert r['primary_capture_snapshot_sha256']==expected_manifest
        assert r['input_snapshot_complete'] and r['frozen_files_verified']
        for name,sha in r['frozen_file_sha256'].items():assert hashlib.sha256((workspace/name).read_bytes()).hexdigest()==sha
