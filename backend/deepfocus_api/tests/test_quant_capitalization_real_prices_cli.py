"""Actual offline join command with synthetic protocol and issuer fixtures."""
from dataclasses import asdict
import hashlib,json,subprocess,sys
from pathlib import Path
import pytest
from deepfocus_api.tests.test_quant_capitalization_prices import fixture
from deepfocus_api.tests.test_quant_capitalization_book import ACTION,PDF,SHA,SCOPE

ROOT=Path(__file__).resolve().parents[3]


def inputs(tmp_path):
    capture=tmp_path/'capture';capture.mkdir();m,s=fixture();m['collector_sha256']=hashlib.sha256((ROOT/'scripts/capture-quant-capitalization-prices.py').read_bytes()).hexdigest()
    (capture/'manifest.json').write_text(json.dumps(m))
    for name,body in s.items():(capture/name).write_bytes(body)
    source=tmp_path/'unit.pdf';source.write_bytes(PDF)
    plan={'action':asdict(ACTION),'scope':SCOPE,'position_role':'counterfactual_model','mode':'historical_reconstruction',
          'closing_lots':[{'id':'unit','symbol':'300750','qty':100,'restriction':'unrestricted'}],'sources':{SHA:str(source)}}
    facts={'fact':{**asdict(ACTION),'event_kind':'capital_reserve_capitalization','available_at_conservative':ACTION.available_at,'gross_cash_per_old_share':'2.52','cash_pay_date':'2023-04-26'}}
    (tmp_path/'plan.json').write_text(json.dumps(plan));(tmp_path/'facts.json').write_text(json.dumps(facts));return capture


@pytest.mark.parametrize('failure',[None,'retained-bytes','issuer-bytes','backdated-fact','metadata'])
def test_join_retains_inputs_and_checks_actual_pretrade_open_phase(tmp_path,failure):
    capture=inputs(tmp_path)
    if failure=='retained-bytes':(capture/'sdk-response-1.bin').write_bytes(b'changed')
    if failure=='issuer-bytes':(tmp_path/'unit.pdf').write_bytes(PDF+b'changed')
    if failure=='backdated-fact':
        p=tmp_path/'facts.json';v=json.loads(p.read_text());v['fact']['observed_at']='2022-01-01T00:00:00+08:00';p.write_text(json.dumps(v))
    if failure=='metadata':
        p=capture/'manifest.json';v=json.loads(p.read_text());v['captures'][0]=None;p.write_text(json.dumps(v))
    w=tmp_path/'review';cmd=[sys.executable,str(ROOT/'scripts/quant-capitalization-real-price-review.py'),'--capture',str(capture),'--action-plan',str(tmp_path/'plan.json'),'--facts',str(tmp_path/'facts.json'),'--workspace',str(w)]
    result=subprocess.run(cmd,capture_output=True,text=True,timeout=20);assert 'Traceback' not in result.stderr,result.stderr
    report=json.loads((w/'result.json').read_text());assert not report['promotion_allowed'] and report['network_requests']==report['production_writes']==report['strategy_replays']==0
    if failure:
        assert result.returncode==2 and report['status']=='rejected'
        assert report['failed_stage']=={'retained-bytes':'prices','issuer-bytes':'ledger','backdated-fact':'facts','metadata':'prices'}[failure]
    else:
        assert result.returncode==0 and len(report['identity_checks'])==10
        values=json.loads((w/'ledger-review/daily-valuations.json').read_text())
        assert values[0]['open']['composite_gross_nav_exact']=='50000.00'
        assert values[0]['open']['available_cash']=='50000.00'
        assert (w/'manifest.json').read_bytes()==(capture/'manifest.json').read_bytes()
