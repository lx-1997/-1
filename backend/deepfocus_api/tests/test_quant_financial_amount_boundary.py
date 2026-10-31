"""Context-independent factual amount validation; no application/provider use."""
from decimal import localcontext
import pytest
from deepfocus_api import quant_financial_vintages as v
from deepfocus_api.tests.test_quant_financial_vintages import fixture,read


@pytest.mark.parametrize('precision',[1,3,6,28])
@pytest.mark.parametrize('amount',['1000000000000000000.01','-1000000000000000000.01'])
def test_bound_is_exact_for_both_signs_at_every_caller_precision(precision,amount):
    m,s=fixture();m['vintages'][0]['metrics']['parent_net_profit']=amount
    with localcontext() as ctx:
        ctx.prec=precision
        with pytest.raises(ValueError):read(m,s)


@pytest.mark.parametrize('amount',['1000000000000000000.00','-1000000000000000000.00','839505582.23','-0.00'])
def test_valid_amount_preserved_with_low_precision_and_all_traps(amount):
    m,s=fixture();m['vintages'][0]['metrics']['parent_net_profit']=amount
    with localcontext() as ctx:
        ctx.prec=1
        for trap in ctx.traps:ctx.traps[trap]=True
        r=read(m,s)
        assert ctx.prec==1 and all(ctx.traps.values())
    assert r['metrics']['parent_net_profit']==amount


def test_validator_identity_is_explicit_but_input_manifest_schema_remains_v1():
    m,s=fixture();r=read(m,s)
    assert m['schema']==v.VERSION=='reviewed-financial-vintages-v1'
    assert r['validation_version']=='exact-financial-magnitude-v2'
