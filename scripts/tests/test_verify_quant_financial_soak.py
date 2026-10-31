"""Independent verifier detects economic corruption even after hash recomputation."""
import copy,importlib.util,json
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('financial_verifier',ROOT/'scripts/verify-quant-financial-soak.py');v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)


def fixture():
    req=dict(issuer_id='cninfo:9900002009',symbol='002109',period_end='2025-03-31',metric='consolidated_net_profit',as_of='2025-12-01T00:00:00+08:00',mode='historical_reconstruction',asset_endpoints={'beginning':{'report_period_end':'2024-03-31','column':'current_period'},'ending':{'report_period_end':'2025-03-31','column':'current_period'}})
    docs=[];facts=[]
    for doc,end,published,amount in [('111','2025-03-31','2025-04-24','-130.00'),('333','2024-12-31','2025-03-10','-370.00'),('444','2024-03-31','2024-04-24','-150.00')]:
        docs.append(dict(document_id=doc,period_end=end,issuer_id=req['issuer_id'],symbol=req['symbol'],published_date=published,revision=1,source_sha256='a'*64,observed_at='2026-09-01T00:00:00+08:00',recorded_at='2026-10-01T00:00:00+08:00'))
        facts.append(dict(document_id=doc,metric='consolidated_net_profit',column='current_period',basis='year_to_date',period_start=end[:4]+'-01-01',period_end=end,value=amount))
    for doc,end,amount in [('111','2025-03-31','100000.00'),('444','2024-03-31','120000.01')]:facts.append(dict(document_id=doc,metric='total_assets',column='current_period',basis='point_in_time_stock',period_start=None,period_end=end,value=amount))
    flow=[];stocks=[]
    for role,doc,coef in [('current_ytd','111',1),('previous_annual','333',1),('previous_comparable_ytd','444',-1),('beginning_assets','444',None),('ending_assets','111',None)]:
        stock=coef is None;d=next(d for d in docs if d['document_id']==doc);metric='total_assets' if stock else 'consolidated_net_profit';f=next(f for f in facts if f['document_id']==doc and f['metric']==metric)
        node=dict(role=role,report_period_end=d['period_end'],metric=metric,column='current_period',coefficient=coef,as_of=req['as_of'],mode=req['mode'],fact=f,selected_document={k:d[k] for k in ['document_id','source_sha256','revision','published_date']})
        (stocks if stock else flow).append(node)
    result=dict(request=req,receipt={'id':'packet'},period_start='2024-04-01',status='arithmetic_available',ttm_value='-350.00',endpoint_average_assets={'fraction_yuan':{'numerator':'22000001','denominator':'200'},'value_exact_yuan':'110000.005'},result={'flow_components':flow,'asset_components':stocks,'missing_components':[]})
    for k in ['ratios_or_scores_generated','accounting_comparability_verified','historical_pit_feed_certified','usable_for_strategy','promotion_allowed','commercial_ready']:result[k]=False
    g=dict(request=req,id='packet',result=result,digest=v.digest(result))
    packets={'packet':{'parsed':{'input':{'facts':facts}},'parent_id':'parent','created':'2026-10-02T00:00:00+08:00'}}
    parents={'parent':{'parsed':{'recorded_at':'2026-10-01T00:00:00+08:00','effective_manifest':{'vintages':docs}}}}
    return g,packets,parents


def test_independent_fraction_and_stock_semantics_accept_exact_fixture():
    v.validate_golden(*fixture())


@pytest.mark.parametrize('damage',['TTM','mean','unsafe_integer','clock','revision_metadata','stock_date','qualification','borrowed_fact'])
def test_recomputed_hash_does_not_hide_economic_or_metadata_corruption(damage):
    g,packets,parents=fixture();r=g['result']
    if damage=='TTM':r['ttm_value']='0.00'
    if damage=='mean':r['endpoint_average_assets']['value_exact_yuan']='110000.01'
    if damage=='unsafe_integer':r['endpoint_average_assets']['fraction_yuan']['numerator']=22000001
    if damage=='clock':r['result']['flow_components'][0]['as_of']='2026-05-01T00:00:00+08:00'
    if damage=='revision_metadata':r['result']['flow_components'][0]['selected_document']['source_sha256']='0'*64
    if damage=='stock_date':r['result']['asset_components'][0]['fact']=dict(r['result']['asset_components'][0]['fact'],period_end='2024-12-31')
    if damage=='qualification':r['promotion_allowed']=True
    if damage=='borrowed_fact':r['result']['flow_components'][0]['fact']=dict(r['result']['flow_components'][0]['fact'],document_id='foreign')
    g['digest']=v.digest(r)
    with pytest.raises(ValueError):v.validate_golden(g,packets,parents)


@pytest.mark.parametrize('raw',[b'{"a":1,"a":2}',b'{"v":NaN}',b'{"v":Infinity}'])
def test_nonfinite_or_duplicate_json_refused(raw):
    with pytest.raises(ValueError):v.parse(raw)


def test_external_anchor_required_before_reviewing_archive(tmp_path):
    (tmp_path/'plan.json').write_text('{}')
    with pytest.raises(ValueError,match='anchor'):v.review(tmp_path,'0'*64)


def http_fixture():
    base=fixture()[0];golden=[]
    for i in range(6):
        g=copy.deepcopy(base);g.update(id='packet-'+str(i),who='a' if i in (0,1,4) else 'b');golden.append(g)
    events=[];listing='/api/quant-research/financial-extractions'
    def add(category,who,route,status,count):
        events.extend(dict(kind='http',category=category,who=who,route=route,status=status) for _ in range(count))
    # Two rounds: only the fifth client wait (lane zero, round two) cancels.
    for i,g in enumerate(golden):add('lane' if i<4 else 'extra',g['who'],v.url(g),200,1 if i==0 else 2)
    for who,status in [('a',429),('observer',200),('observer',429),('b',429)]:add('probe',who,listing,status,2)
    for route,status in [(listing,200),(listing+'/packet-0',404),(v.url(golden[0]),404)]:add('observer','observer',route,status,3)
    return golden,events,{'observer_cycles':3},2


def test_registered_request_matrix_accepts_all_roles_and_cancellation_counts():
    v.validate_http_matrix(*http_fixture())


@pytest.mark.parametrize('damage',['route','owner','category','status','missing','duplicate','cycles','cancelled_response'])
def test_request_matrix_refuses_missing_or_substituted_coverage(damage):
    golden,events,m,n=http_fixture()
    target=next(e for e in events if e['category']=='observer' and e['status']==404)
    if damage=='route':target.update(route='/api/quant-research/financial-extractions',status=200)
    elif damage=='owner':target['who']='a'
    elif damage=='category':target['category']='probe'
    elif damage=='status':target['status']=200
    elif damage=='missing':events.remove(target)
    elif damage=='duplicate':events.append(dict(target))
    elif damage=='cycles':m['observer_cycles']=4
    else:events.append(dict(events[0]))
    with pytest.raises(ValueError,match='matrix'):v.validate_http_matrix(golden,events,m,n)
