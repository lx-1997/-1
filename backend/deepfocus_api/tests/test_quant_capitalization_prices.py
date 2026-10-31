"""Only synthetic protocol/price fixtures; never inject these into application data."""
import copy,hashlib
from decimal import Context,localcontext
import pytest
from deepfocus_api import quant_capitalization_prices as p
from deepfocus_api.tests.test_quant_security_status import frame


def fixture():
    rows=[[d,'sz.300750','10.0000','10.2000','9.8000','10.0000','10.0000','10000','100000.0000','3','1','0.000000','0'] for d in p.DAYS]
    sources={'sdk-response-0.bin':b'unit login','sdk-response-1.bin':frame(p.QUERY,rows),'sdk-response-2.bin':b'unit logout'}
    captures=[{'file':n,'phase':phase,'bytes':len(sources[n]),'sha256':hashlib.sha256(sources[n]).hexdigest(),'observed_at':'2026-10-30T08:00:00+00:00'} for n,phase in zip(sources,['login','query','logout'])]
    return {'schema':'capitalization-raw-price-capture-v1','query':dict(p.QUERY),'errors':[],'sdk_version':'0.9.1','capture_layer':'SDK_decoded_protocol_not_wire','sdk_requests':3,'captures':captures,'fields':p.FIELDS.split(','),'rows':rows},sources


def rebind(m,s):
    raw=frame(m['query'],m['rows']);s['sdk-response-1.bin']=raw;m['captures'][1].update(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())


def test_retained_prices_are_exact_decimals_with_no_qualification_upgrade():
    m,s=fixture();before=copy.deepcopy(m);r=p.normalize(m,s)
    assert len(r['rows'])==5 and r['rows'][0]['open']=='10.0000' and r['rows'][0]['volume_shares']==10000
    assert r['retrospective_only'] and not r['promotion_allowed'] and not r['exchange_certified'] and m==before
    with localcontext(Context(prec=3)):assert p.normalize(m,s)==r


@pytest.mark.parametrize('change',['bytes','rows','query','clock','date','adjusted','tick','ohlc','percent','volume','missing'])
def test_corrupt_or_differently_scoped_prices_are_rejected(change):
    m,s=fixture()
    if change=='bytes':s['sdk-response-1.bin']+=b'changed'
    elif change=='rows':m['rows'][0][2]='10.01'
    elif change=='query':m['query']['adjustflag']='2'
    elif change=='clock':m['captures'][1]['observed_at']='2099-01-01T00:00:00+00:00'
    else:
        if change=='date':m['rows'][0][0]=p.DAYS[1]
        if change=='adjusted':m['rows'][0][9]='2'
        if change=='tick':m['rows'][0][2]='10.001'
        if change=='ohlc':m['rows'][0][3]='9.90'
        if change=='percent':m['rows'][0][11]='50'
        if change=='volume':m['rows'][0][7]=''
        if change=='missing':m['rows'].pop()
        rebind(m,s)
    with pytest.raises(ValueError):p.normalize(m,s)


def test_action_day_provider_reference_can_differ_from_previous_raw_close():
    m,s=fixture();m['rows'][2][6]='5.0000';m['rows'][2][11]='100.000000';rebind(m,s)
    result=p.normalize(m,s);assert result['rows'][2]['provider_reference']=='5.0000'
    assert not result['preopen_state_verified'] and not result['exchange_certified']


def test_non_action_reference_cannot_be_substituted_even_when_percent_is_consistent():
    m,s=fixture();m['rows'][1][6]='5.0000';m['rows'][1][11]='100.000000';rebind(m,s)
    with pytest.raises(ValueError,match='Non-action'):p.normalize(m,s)


def test_malformed_capture_metadata_is_a_controlled_rejection():
    m,s=fixture();m['captures'][0]=None
    with pytest.raises(ValueError,match='inventory'):p.normalize(m,s)
