"""Synthetic cross-provider inputs only; no live quotes or external calls."""
import json
from decimal import Context,localcontext
import pytest
from deepfocus_api import quant_capitalization_cross_source as cross
from deepfocus_api.tests.test_quant_capitalization_prices import fixture

URL='https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol=sz300750&scale=240&ma=no&datalen=1023'
CLOCK='2026-10-30T08:00:00+00:00'


def fixture_pair():
    m,s=fixture();rows=[{'day':r[0]+' 00:00:00','open':r[2],'high':r[3],'low':r[4],'close':r[5],'volume':r[7]} for r in m['rows']]
    return m,s,rows


def run(m,s,rows,url=URL,clock=CLOCK):return cross.compare(m,s,json.dumps(rows).encode(),url,clock)


def test_all_price_and_share_cells_equal_without_qualification_or_unit_fitting():
    m,s,rows=fixture_pair();r=run(m,s,rows)
    assert r['all_matched'] and r['compared_cells']==25 and r['mismatch_count']==0
    assert not r['promotion_allowed'] and not r['upstream_feed_independence_verified'] and not r['unit_conversion_applied']
    assert 'provider_ex_reference' in r['uncompared']
    with localcontext(Context(prec=3)):assert run(m,s,rows)==r


@pytest.mark.parametrize('field,value',[('open','10.01'),('volume','100'),('volume','10001')])
def test_real_differences_are_retained_not_rounded_rescaled_or_selected_away(field,value):
    m,s,rows=fixture_pair();rows[2][field]=value;r=run(m,s,rows)
    assert not r['all_matched'] and r['mismatch_count']==1
    assert sum(not c['matched'] for c in r['checks'])==1
    assert r['price_tolerance']==r['share_volume_tolerance']=='0'


@pytest.mark.parametrize('failure',['missing','duplicate-date','hidden-fraction','fractional-volume','wrong-symbol','future-clock','adjusted-primary'])
def test_invalid_or_incomparable_inputs_are_rejected(failure):
    m,s,rows=fixture_pair();url=URL;clock=CLOCK
    if failure=='missing':rows.pop()
    if failure=='duplicate-date':rows[0]['day']=rows[1]['day']
    if failure=='hidden-fraction':rows[0]['open']='10.0000000000000001'
    if failure=='fractional-volume':rows[0]['volume']='10000.1'
    if failure=='wrong-symbol':url=URL.replace('sz300750','sh600519')
    if failure=='future-clock':clock='2099-01-01T00:00:00+00:00'
    if failure=='adjusted-primary':m['query']['adjustflag']='2'
    with pytest.raises(ValueError):run(m,s,rows,url,clock)


def test_duplicate_raw_key_cannot_be_hidden_by_the_existing_float_projection():
    m,s,rows=fixture_pair();body=json.dumps(rows).replace('"open": "10.0000"','"open": "10.0000", "open": "10.01"',1).encode()
    with pytest.raises(ValueError,match='Duplicate raw'):cross.compare(m,s,body,URL,CLOCK)
