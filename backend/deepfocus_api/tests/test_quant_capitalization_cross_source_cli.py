"""Actual offline command and mismatch stop; fixtures are explicitly synthetic."""
import hashlib,json,subprocess,sys
from pathlib import Path
import pytest
from deepfocus_api import ai_fund_market as market
from deepfocus_api.tests.test_quant_capitalization_real_prices_cli import inputs
from deepfocus_api.tests.test_quant_capitalization_cross_source import fixture_pair,URL,CLOCK

ROOT=Path(__file__).resolve().parents[3]


@pytest.mark.parametrize('mode',['equal','one-share-difference','prior-input-tamper'])
def test_cross_source_cli_rebuilds_only_after_strict_comparison_and_binding(tmp_path,mode):
    bao=inputs(tmp_path);account=tmp_path/'account'
    result=subprocess.run([sys.executable,str(ROOT/'scripts/quant-capitalization-real-price-review.py'),'--capture',str(bao),'--action-plan',str(tmp_path/'plan.json'),'--facts',str(tmp_path/'facts.json'),'--workspace',str(account)],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
    sina=tmp_path/'sina';sina.mkdir();_,_,rows=fixture_pair()
    if mode=='one-share-difference':rows[2]['volume']='10001'
    body=json.dumps(rows).encode();(sina/'response.bin').write_bytes(body)
    m={'schema':'capitalization-sina-raw-capture-v1','errors':[],'status':200,'request_url':URL,'final_url':URL,'symbol':'300750','provider':'sina','price_adjustment':'none',
       'collector_sha256':hashlib.sha256((ROOT/'scripts/capture-quant-capitalization-cross-source.py').read_bytes()).hexdigest(),
       'parser_sha256':hashlib.sha256(Path(market.__file__).read_bytes()).hexdigest(),'bytes':len(body),'response_sha256':hashlib.sha256(body).hexdigest(),'observed_at':CLOCK}
    (sina/'manifest.json').write_text(json.dumps(m))
    if mode=='prior-input-tamper':(account/'facts.json').write_text('{}')
    w=tmp_path/'review';result=subprocess.run([sys.executable,str(ROOT/'scripts/quant-capitalization-cross-source-review.py'),'--sina',str(sina),'--baostock',str(bao),'--accounting',str(account),'--workspace',str(w)],capture_output=True,text=True,timeout=20)
    assert 'Traceback' not in result.stderr,result.stderr
    r=json.loads((w/'result.json').read_text());assert r['network_requests']==r['strategy_replays']==r['production_writes']==0 and not r['promotion_allowed']
    assert (sina/'response.bin').read_bytes()==body
    if mode=='equal':
        assert result.returncode==0 and r['status']=='cross_source_accounting_equal'
        assert r['compared_cells']==25 and r['valuation_phases_equal']==10 and r['final_checkpoint_equal'] and r['accounting_replays']==2
    else:
        assert result.returncode==2 and r['accounting_replays']==0 and not (w/'primary-rebuild').exists()
        if mode=='one-share-difference':assert r['status']=='source_mismatch' and r['mismatch_count']==1
        else:assert r['failed_stage']=='prior_binding'
