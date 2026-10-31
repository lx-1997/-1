from decimal import localcontext
import copy
import json
from pathlib import Path
import subprocess
import sys
import pytest
from deepfocus_api import quant_price_band as band


@pytest.mark.parametrize('reference,percentage,tick,lower,upper',[
    ('10.05','10','0.01','9.05','11.06'),('10.15','10','0.01','9.14','11.17'),
    ('10','20','0.01','8','12'),('10','5','0.01','9.5','10.5'),
    ('0.01','10','0.01','0.01','0.02'),('0.02','5','0.01','0.01','0.03'),
    ('0.05','10','0.01','0.04','0.06'),('10.005','10','0.001','9.005','11.006')])
def test_exact_half_up_minimum_distance_and_minimum_price(reference,percentage,tick,lower,upper):
    r=band.calculate(reference,percentage,tick=tick,regime='declared_standard')
    assert (r['lower'],r['upper'])==(lower,upper)
    assert not r['exchange_reference_verified'] and not r['security_regime_verified'] and not r['order_or_fill_permission']
    assert band.check_price(lower,r)['within_declared_arithmetic_band']
    assert band.check_price(upper,r)['within_declared_arithmetic_band']


def test_decimal_context_cannot_change_half_tick_and_float_round_is_not_used():
    before=band.calculate('10.15','10',tick='0.01',regime='declared_standard')
    assert round(10.15*.9,2)!=float(before['lower'])
    with localcontext() as ctx:
        ctx.prec=3
        assert band.calculate('10.15','10',tick='0.01',regime='declared_standard')==before


@pytest.mark.parametrize('value',['NaN','Infinity','1e2','-1','0','01',' 10','10.','1.000000001',10.05,True])
def test_invalid_or_ambiguous_reference_is_not_normalized(value):
    with pytest.raises(ValueError):band.calculate(value,'10',tick='0.01',regime='declared_standard')


@pytest.mark.parametrize('damage',['unknown','no_limit','fractional_reference','percentage100','tick_too_large','bounds','permission'])
def test_unknown_regime_unaligned_reference_or_tamper_refuses(damage):
    if damage in ['unknown','no_limit']:
        with pytest.raises(ValueError):band.calculate('10','10',tick='0.01',regime=damage)
    elif damage=='fractional_reference':
        with pytest.raises(ValueError):band.calculate('10.005','10',tick='0.01',regime='declared_standard')
    elif damage=='percentage100':
        with pytest.raises(ValueError):band.calculate('10','100',tick='0.01',regime='declared_standard')
    elif damage=='tick_too_large':
        with pytest.raises(ValueError):band.calculate('0.01','10',tick='0.1',regime='declared_standard')
    else:
        r=band.calculate('10','10',tick='0.01',regime='declared_standard')
        r['upper']='12' if damage=='bounds' else r['upper']
        if damage=='permission':r['order_or_fill_permission']=True
        with pytest.raises(ValueError):band.check_price('10',r)


def test_exact_bounds_are_necessary_only_off_tick_and_outside_refuse_without_clipping():
    r=band.calculate('10','10',tick='0.01',regime='declared_standard');before=copy.deepcopy(r)
    assert not band.check_price('11.01',r)['within_declared_arithmetic_band']
    assert not band.check_price('10.005',r)['on_tick']
    assert not band.check_price('11',r)['order_or_fill_permission'] and r==before


@pytest.mark.parametrize('damage',['input','source','overwrite'])
def test_actual_cli_refuses_changed_registration_and_output_reuse(tmp_path,damage):
    from deepfocus_api.tests.test_quant_fill_feasibility import fixture
    data,_=fixture();source=tmp_path/'input.json';protocol=tmp_path/'protocol.json';output=tmp_path/'output.json'
    source.write_text(json.dumps([{'id':'unit','dataset':data,'result_digest':'unit','request':{'symbols':['000333']}}]))
    script=Path(__file__).resolve().parents[3]/'scripts/quant-price-band-review.py';args=['--input',str(source),'--protocol',str(protocol)]
    r=subprocess.run([sys.executable,str(script),'register']+args,capture_output=True,text=True,timeout=10);assert r.returncode==0,r.stderr
    if damage=='input':source.write_text(source.read_text()+'\n')
    if damage=='source':
        p=json.loads(protocol.read_text());p['bindings']['source_pins']['backend/deepfocus_api/quant_price_band.py']='0'*64;protocol.write_text(json.dumps(p))
    if damage=='overwrite':output.write_text('retain')
    r=subprocess.run([sys.executable,str(script),'run']+args+['--output',str(output)],capture_output=True,text=True,timeout=10)
    assert r.returncode==2
    if damage=='overwrite':assert output.read_text()=='retain'
    else:assert not output.exists()
