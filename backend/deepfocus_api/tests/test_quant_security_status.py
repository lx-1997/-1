"""Decode provenance, flags, missing days and explicitly retrospective clocks."""
import copy,hashlib,json
from datetime import datetime,timezone
from pathlib import Path
import subprocess,sys

import pytest
from deepfocus_api import quant_security_status as status
from deepfocus_api.quant_research_engine import digest
from deepfocus_api.tests.test_quant_exchange_schedule import reviewed
from deepfocus_api.tests.test_quant_event_replay import fixture as prices


def frame(query,rows):
    body='\x01'.join(['0','success','query_history_k_data_plus','anonymous','1','10000',
        json.dumps({'record':rows},separators=(',',':')),query['code'],query['fields'],
        query['start_date'],query['end_date'],query['frequency'],query['adjustflag']])
    return ('00.9.10\x0196\x010000000001'+body).encode()


def fixture():
    data,_,_=prices(count=60,dividend=1)
    # This five-session January prefix matches the independent annual plan.
    data={s:{**p,'bars':[{**b,**{k:round(b[k],4) for k in ('open','high','low','close')}} for b in p['bars'][:5]]} for s,p in data.items()}
    bars=data['000333']['bars']
    result={'dataset_digest':digest(data)};exp={'id':'synthetic-status-source','request':{'symbols':['000333']},
        'dataset':data,'result':result,'result_digest':digest(result)}
    scope={'experiments':[{'experiment':exp['id'],'dataset_digest':result['dataset_digest'],'result_digest':exp['result_digest']}],
           'requests':[],'out_of_pool_status_sample':'sz.002109'}
    captures=[];results=[];sources={}
    for code in ('sz.000333','sz.002109'):
        q=dict(code=code,fields=status.FIELDS,start_date=bars[0]['date'],end_date=bars[-1]['date'],frequency='d',adjustflag='3')
        scope['requests'].append(q);rows=[[b['date'],code,format(b['close'],'.4f'),'100000','1','0'] for b in bars]
        if code.endswith('002109'):rows[1][3:]=['','0','0'];rows[2][-1]='1'
        raw=frame(q,rows);name='response-'+str(len(captures))+'.bin';sources[name]=raw
        captures.append({'file':name,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),
            'observed_at':'2026-10-01T12:00:00+08:00','request':q,'capture_layer':'SDK_decoded_protocol_not_wire'})
        results.append({'request':q,'fields':status.FIELDS.split(','),'rows':rows,'error_code':'0',
                        'error_msg':'success','capture_indices':[len(captures)-1]})
    return {'schema':'baostock-historical-security-status-v1','capture_layer':'SDK_decoded_protocol_not_wire',
            'scope':scope,'captures':captures,'results':results,'errors':[]},sources,[exp]


def rebind(m,s,index=0):
    r=m['results'][index];c=m['captures'][r['capture_indices'][0]];body=frame(r['request'],r['rows'])
    s[c['file']]=body;c.update(sha256=hashlib.sha256(body).hexdigest(),bytes=len(body))


def test_independent_frame_decode_preserves_halt_empty_volume_and_st_transition():
    m,s,_=fixture();normalized=status.normalize(m,s);sample=normalized['sz.002109']
    assert sample[1]['provider_trading_status']=='suspended' and sample[1]['volume_shares'] is None
    assert not sample[1]['is_st_by_provider'] and sample[2]['is_st_by_provider']
    assert all(r['special_treatment_subtype']=='unknown' for r in sample)
    assert status.decode_query(s['response-0.bin'],m['scope']['requests'][0])==m['results'][0]['rows']


def test_absent_or_preopen_day_never_defaults_to_normal_or_order_permission():
    m,s,_=fixture();normalized=status.normalize(m,s);day=normalized['sz.000333'][0]['date']
    pre=status.lookup(normalized,'sz.000333',day,as_of=day+'T09:30:00+08:00',mode='historical_reconstruction')
    assert pre['status']=='unknown' and pre['record'] is None and not pre['order_admission_allowed']
    after=status.lookup(normalized,'sz.000333',day,as_of=day+'T16:30:00+08:00',mode='historical_reconstruction')
    assert after['status']=='retrospective_label' and not after['preopen_knowledge_verified']
    actual=status.lookup(normalized,'sz.000333',day,as_of=day+'T16:30:00+08:00',mode='as_recorded')
    assert actual['status']=='unknown'
    missing=status.lookup(normalized,'sz.000333','2024-02-01',as_of='2026-10-02T00:00:00+08:00',mode='as_recorded')
    assert missing['status']=='unknown' and not missing['promotion_allowed']
    actual=status.lookup(normalized,'sz.000333',day,as_of='2026-10-02T00:00:00+08:00',mode='as_recorded')
    assert actual['record'] and not actual['historical_security_status_certified']


@pytest.mark.parametrize('damage',['parsed_rows','blob','request','protocol','field','duplicate','order','unknown_st','unknown_trade','normal_volume','negative_volume','giant_volume','nan_close','exponent_close','wrong_code','future_observed','early_observed','failed','empty','pagination','source_version'])
def test_corrupt_unknown_or_differently_bound_states_reject(monkeypatch,damage):
    m,s,_=fixture();r=m['results'][0];c=m['captures'][0]
    if damage=='parsed_rows':r['rows'][0][-1]='1'
    elif damage=='blob':s[c['file']]+=b'changed'
    elif damage=='request':c['request']={**r['request'],'code':'sh.600519'}
    elif damage=='protocol':
        s[c['file']]=s[c['file']].replace(b'00.9.10',b'00.9.40');c.update(sha256=hashlib.sha256(s[c['file']]).hexdigest())
    elif damage=='field':r['fields'].pop()
    elif damage=='duplicate':r['rows'][1]=copy.deepcopy(r['rows'][0]);rebind(m,s)
    elif damage=='order':r['rows'].reverse();rebind(m,s)
    elif damage=='unknown_st':r['rows'][0][-1]='';rebind(m,s)
    elif damage=='unknown_trade':r['rows'][0][-2]='2';rebind(m,s)
    elif damage=='normal_volume':r['rows'][0][3]='';rebind(m,s)
    elif damage=='negative_volume':r['rows'][0][3]='-10';rebind(m,s)
    elif damage=='giant_volume':r['rows'][0][3]='1'*100;rebind(m,s)
    elif damage=='nan_close':r['rows'][0][2]='NaN';rebind(m,s)
    elif damage=='exponent_close':r['rows'][0][2]='1e9999999';rebind(m,s)
    elif damage=='wrong_code':r['rows'][0][1]='sz.002109';rebind(m,s)
    elif damage=='future_observed':c['observed_at']='2099-01-01T00:00:00+08:00'
    elif damage=='early_observed':c['observed_at']=r['rows'][0][0]+'T15:00:00+08:00'
    elif damage=='failed':m['errors']=[{'error':'ProviderFailure'}]
    elif damage=='empty':r['rows']=[];rebind(m,s)
    elif damage=='pagination':r['capture_indices']=[0,1]
    else:monkeypatch.setattr(status,'_pins',lambda:{'changed':'source'})
    with pytest.raises((ValueError,KeyError)):status.normalize(m,s)


def test_coverage_and_close_check_do_not_hide_missing_days_or_price_disagreements(reviewed):
    m,s,exps=fixture();calendar,html=reviewed
    report=status.review(m,s,exps,calendar,html);assert report['coverage_matches_original_and_plan'] and report['prices_match_original']
    m['results'][0]['rows'].pop(2);rebind(m,s)
    broken=status.review(m,s,exps,calendar,html);assert not broken['coverage_matches_original_and_plan']
    assert broken['stocks'][0]['missing_original_days']==[exps[0]['dataset']['000333']['bars'][2]['date']]
    m,s,exps=fixture();m['results'][0]['rows'][2][2]='111.11';rebind(m,s)
    changed=status.review(m,s,exps,calendar,html);assert changed['coverage_matches_original_and_plan'] and not changed['prices_match_original']
    assert not changed['historical_security_status_certified']


@pytest.mark.parametrize('damage',['window','other_parent','extra_sample','calendar_source'])
def test_original_scope_and_independent_calendar_evidence_stay_bound(reviewed,damage):
    m,s,exps=fixture();calendar,html=copy.deepcopy(reviewed)
    if damage=='window':m['results'][0]['request']['start_date']='2024-01-01';rebind(m,s)
    elif damage=='other_parent':m['scope']['experiments'][0]['experiment']='other'
    elif damage=='extra_sample':m['scope']['out_of_pool_status_sample']='sz.000001'
    else:html[next(iter(html))]+=b'changed'
    with pytest.raises(ValueError):status.review(m,s,exps,calendar,html)


@pytest.mark.parametrize('damage',['empty','duplicate','empty_symbols'])
def test_empty_or_duplicate_experiment_scope_cannot_report_vacuous_coverage(reviewed,damage):
    m,s,exps=fixture();calendar,html=reviewed
    if damage=='empty':exps=[]
    elif damage=='duplicate':exps+=copy.deepcopy(exps)
    else:exps[0]['request']['symbols']=[]
    with pytest.raises(ValueError,match='stock scope' if damage=='empty_symbols' else 'Nonempty unique'):
        status.review(m,s,exps,calendar,html)


@pytest.mark.parametrize('damage',['escape','overwrite','valid','missing_day'])
def test_review_cli_binds_raw_sources_and_preserves_reports(tmp_path,reviewed,damage):
    m,s,exps=fixture();cal,html=reviewed
    if damage=='missing_day':m['results'][0]['rows'].pop(2);rebind(m,s)
    for name,body in s.items():(tmp_path/name).write_bytes(body)
    if damage=='escape':m['captures'][0]['file']='../outside.bin'
    (tmp_path/'manifest.json').write_text(json.dumps(m));(tmp_path/'experiments.json').write_text(json.dumps(exps))
    # The synthetic fixture replaces source SHAs, so bind its own raw files.
    cal['source_files']={sha:'calendar-'+str(i)+'.html' for i,sha in enumerate(html)}
    for sha,name in cal['source_files'].items():(tmp_path/name).write_bytes(html[sha])
    (tmp_path/'calendar.json').write_text(json.dumps(cal));output=tmp_path/'review.json'
    if damage=='overwrite':output.write_text('preserve prior failure')
    script=Path(__file__).resolve().parents[3]/'scripts/quant-security-status-review.py'
    p=subprocess.run([sys.executable,str(script),'--capture-dir',str(tmp_path),'--experiments',str(tmp_path/'experiments.json'),
        '--calendar',str(tmp_path/'calendar.json'),'--calendar-dir',str(tmp_path),'--output',str(output)],capture_output=True,text=True)
    if damage=='valid':assert p.returncode==0 and json.loads(output.read_text())['coverage_matches_original_and_plan']
    elif damage=='missing_day':assert p.returncode==2 and not json.loads(output.read_text())['coverage_matches_original_and_plan']
    elif damage=='overwrite':assert p.returncode!=0 and output.read_text()=='preserve prior failure'
    else:assert p.returncode!=0 and not output.exists()


@pytest.mark.parametrize('damage',['existing','relative','modified','empty'])
def test_capture_refuses_unsafe_scope_before_network_or_overwriting(tmp_path,damage):
    _,_,exps=fixture();workspace=tmp_path/'capture';path=tmp_path/'experiments.json'
    if damage=='existing':workspace.mkdir();(workspace/'keep').write_text('preserve')
    elif damage=='relative':workspace=Path('relative-target')
    elif damage=='modified':exps[0]['dataset']['000333']['bars'][0]['close']+=1
    else:exps=[]
    path.write_text(json.dumps(exps));script=Path(__file__).resolve().parents[3]/'scripts/capture-quant-security-status.py'
    r=subprocess.run([sys.executable,str(script),'--experiments',str(path),'--workspace',str(workspace)],capture_output=True,text=True)
    assert r.returncode!=0
    if damage=='existing':assert (workspace/'keep').read_text()=='preserve'
    elif workspace.is_absolute():assert not workspace.exists()


def test_capture_worker_rejects_parent_source_mismatch_before_sdk_or_network(tmp_path):
    import importlib.util
    path=Path(__file__).resolve().parents[3]/'scripts/capture-quant-security-status.py'
    spec=importlib.util.spec_from_file_location('isolated_status_capture',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with pytest.raises(ValueError,match='source changed before worker start'):
        module.worker({'collector_sha256':'wrong-parent-version','requests':[]},tmp_path)
    assert not (tmp_path/'manifest.json').exists()


@pytest.mark.parametrize('changed_sdk',[False,True])
def test_capture_worker_complete_offline_response_and_sdk_change_fail_closed(tmp_path,monkeypatch,changed_sdk):
    import importlib.util,types
    path=Path(__file__).resolve().parents[3]/'scripts/capture-quant-security-status.py'
    spec=importlib.util.spec_from_file_location('isolated_status_worker',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    m,_,_=fixture();package=tmp_path/'fake_sdk';package.mkdir();init=package/'__init__.py';init.write_text('# synthetic offline SDK fixture only\n')
    sdk=types.ModuleType('baostock');sdk.__file__=str(init);util=types.ModuleType('baostock.util');sock=types.ModuleType('baostock.util.socketutil')
    results={r['request']['code']:r for r in m['results']}
    sock.send_msg=lambda code:frame(results[code]['request'],results[code]['rows']).decode()
    util.socketutil=sock;sdk.login=lambda:types.SimpleNamespace(error_code='0')
    class Result:
        error_code='0';error_msg='success';fields=status.FIELDS.split(',')
        def __init__(self,rows):self.rows=rows;self.index=-1
        def next(self):self.index+=1;return self.index<len(self.rows)
        def get_row_data(self):return self.rows[self.index]
    def query(**q):sock.send_msg(q['code']);return Result(results[q['code']]['rows'])
    sdk.query_history_k_data_plus=query
    sdk.logout=lambda:init.write_text('# changed fixture SDK source\n') if changed_sdk else None
    for name,value in [('baostock',sdk),('baostock.util',util),('baostock.util.socketutil',sock)]:monkeypatch.setitem(sys.modules,name,value)
    monkeypatch.setattr(module.importlib.metadata,'version',lambda _: '0.9.1-offline-unit-fixture')
    monkeypatch.setattr(module.socket,'setdefaulttimeout',lambda _:None)
    work=tmp_path/'capture';work.mkdir();scope={**m['scope'],'collector_sha256':module.LOADED_COLLECTOR_SHA}
    code=module.worker(scope,work);captured=json.loads((work/'manifest.json').read_text());sources={c['file']:(work/c['file']).read_bytes() for c in captured['captures']}
    assert captured['collector_sha256']==module.LOADED_COLLECTOR_SHA and len(captured['captures'])==2
    if changed_sdk:
        assert code==1 and any(e.get('type')=='SDKChangedDuringCapture' for e in captured['errors'])
        with pytest.raises(ValueError):status.normalize(captured,sources)
    else:
        assert code==0 and not captured['errors']
        assert len(status.normalize(captured,sources)['sz.000333'])==5
