"""Historical membership must retain past delistings and listed suspensions."""
import copy,gzip,hashlib,importlib.util,json,zlib
from datetime import date,timedelta
from pathlib import Path
import subprocess,sys
import pytest

from deepfocus_api import quant_historical_universe as u
from deepfocus_api.quant_research_engine import digest
from deepfocus_api.tests.test_quant_exchange_schedule import reviewed
from deepfocus_api.tests.test_quant_event_replay import fixture as price_fixture


BASIC=[['sz.000333','美的集团','2013-09-18','','1','1'],
       ['sz.002699','*ST美盛','2012-09-11','2024-06-05','1','0'],
       ['sz.001389','广合科技','2024-04-02','','1','1'],
       ['sh.000846','ECPI ESG指数','2012-10-16','','2','1'],
       ['sh.512280','基金','2020-01-01','','5','1']]


def response(query,rows):
    method=query['method'];payload=json.dumps({'record':rows},ensure_ascii=False,separators=(',',':'))
    tail=[query['day'],','.join(u.FIELDS[method])] if method=='query_all_stock' else [query.get('code',''),'',' , '.join(u.FIELDS[method])]
    body='\x01'.join(['0','success',method,'anonymous','1','10000',payload,*tail]);kind='36' if method=='query_all_stock' else '46'
    content='00.9.10\x01'+kind+'\x01'+str(len(body)).zfill(10)+body
    return (content+'\x01'+str(zlib.crc32(content.encode()))+'<![CDATA[]]>\n').encode()


def entry(root,query,rows,name):
    raw=response(query,rows);packed=gzip.compress(raw,mtime=0);(root/name).write_bytes(packed);decoded=u.decode_response(raw,query)
    return {'query':query,'fields':decoded['fields'],'rows':len(rows),'raw_rows_digest':digest(rows),
            'sdk_rows_digest':digest(decoded['sdk_rows']),'sdk_whitespace_changed_rows':decoded['sdk_whitespace_changed_rows'],
            'error_code':'0','frames':[{'file':name,'sha256':hashlib.sha256(raw).hexdigest(),'gzip_sha256':hashlib.sha256(packed).hexdigest(),
                'bytes':len(raw),'observed_at':'2026-10-01T12:00:00+08:00','query':query}]}


def setup(root,reviewed):
    calendar,sources=reviewed;data,_,_=price_fixture(count=60,dividend=1);data={s:{**p,'bars':p['bars'][:5]} for s,p in data.items()}
    result={'dataset_digest':digest(data)};exps=[{'id':'synthetic-universe','request':{'symbols':['000333']},'dataset':data,'result':result,'result_digest':digest(result)}]
    scope=u.collection_scope(exps,calendar,sources);basicq={'method':'query_stock_basic','code':''}
    entries=[entry(root,basicq,BASIC,'before.gz')]
    for i,day in enumerate(scope['days']):
        rows=[['sz.000333','1','美的集团'],['sz.002699','0','*ST美盛'],['sh.000846','1','ECPI ESG指数'],['sh.512280','1','基金']]
        entries.append(entry(root,{'method':'query_all_stock','day':day},rows,'day-'+str(i)+'.gz'))
    entries.append(entry(root,basicq,BASIC,'after.gz'))
    return {'schema':u.VERSION,'scope':scope,'results':entries,'errors':[],'collector_sha256':'unit-fixture'},scope,exps,calendar,sources


def test_protocol_unicode_character_length_crc_and_lossy_sdk_name_projection():
    q={'method':'query_stock_basic','code':''};raw=response(q,BASIC);decoded=u.decode_response(raw,q)
    assert decoded['rows'][3][1]=='ECPI ESG指数' and decoded['sdk_rows'][3][1]=='ECPIESG指数'
    assert decoded['sdk_whitespace_changed_rows']==1 and len(raw.decode())<len(raw)
    corrupt=raw.replace(b'2013',b'2014',1)
    with pytest.raises(ValueError,match='checksum'):u.decode_response(corrupt,q)
    with pytest.raises(ValueError):u.decode_response(raw[:-1],q)
    with pytest.raises(ValueError):u.decode_response(raw,{'method':'query_stock_basic','code':'sz.002699'})


def test_past_delisted_and_suspended_members_are_not_removed_by_current_status():
    basic=u.parse_basic(BASIC);rows=[['sz.000333','1','美的集团'],['sz.002699','0','*ST美盛'],['sz.001389','1','广合科技']]
    before=u.membership('2024-06-04',rows,basic);members={r['code']:r for r in before['members']}
    assert before['coverage_consistent'] and members['sz.002699']['provider_trading_status']=='suspended'
    assert members['sz.002699']['catalogue_status_observed_now']=='0'
    on=u.membership('2024-06-05',rows,basic)
    assert on['coverage_consistent'] and 'sz.002699' not in {r['code'] for r in on['members']}
    assert on['expired_snapshot_codes_excluded']==['sz.002699']
    rows[1][1]='1';conflict=u.membership('2024-06-05',rows,basic)
    assert not conflict['coverage_consistent'] and conflict['expired_but_trading_snapshot_conflicts']==['sz.002699']


def test_ipo_inclusive_while_preipo_appearance_is_a_conflict_not_a_future_member():
    basic=u.parse_basic(BASIC);rows=[['sz.000333','1','美的集团'],['sz.002699','1','*ST美盛']]
    assert u.membership('2024-04-01',rows,basic)['coverage_consistent']
    rows.append(['sz.001389','1','广合科技'])
    pre=u.membership('2024-04-01',rows,basic);assert not pre['coverage_consistent'] and pre['pre_ipo_snapshot_conflicts']==['sz.001389']
    actual=u.membership('2024-04-02',rows,basic);assert actual['coverage_consistent']
    assert 'sz.001389' in {r['code'] for r in actual['members']} and not actual['order_admission_allowed']


@pytest.mark.parametrize('damage',['missing_ipo','missing_out','unknown_type','duplicate','bad_dates'])
def test_bad_lifecycle_cannot_silently_remove_a_historical_stock(damage):
    rows=copy.deepcopy(BASIC)
    if damage=='missing_ipo':rows[0][2]=''
    elif damage=='missing_out':rows[1][3]=''
    elif damage=='unknown_type':rows[0][4]='3'
    elif damage=='duplicate':rows.append(copy.deepcopy(rows[0]))
    else:rows[1][3]='2000-01-01'
    with pytest.raises(ValueError):u.parse_basic(rows)


def test_unknown_and_missing_in_scope_identities_remain_explicit():
    basic=u.parse_basic(BASIC);rows=[['sz.000333','1','美的集团'],['sz.000999','1','未知']]
    panel=u.membership('2024-06-04',rows,basic)
    assert not panel['coverage_consistent'] and panel['unknown_in_scope_codes']==['sz.000999']
    assert set(panel['missing_lifecycle_expected_codes'])=={'sz.002699','sz.001389'}


def test_complete_frozen_review_keeps_raw_names_and_suspended_member_rows(tmp_path,reviewed):
    m,scope,*_=setup(tmp_path,reviewed);before=copy.deepcopy(m);r=u.review(tmp_path,m,scope)
    assert m==before and r['days']==5 and r['member_day_rows']==10 and r['suspended_member_day_rows']==5
    assert r['all_daily_joins_consistent'] and r['snapshot_name_whitespace_differences_preserved']==5
    assert not r['historical_pit_universe_certified'] and not r['commercial_ready'] and not r['promotion_allowed']


@pytest.mark.parametrize('damage',['missing_day','reordered','reused_file','bad_source','raw_digest','sdk_digest','catalogue_changed','scope','future_clock','errors','empty_scope','source_version'])
def test_incomplete_or_rebound_capture_cannot_claim_full_daily_coverage(tmp_path,reviewed,monkeypatch,damage):
    m,scope,*_=setup(tmp_path,reviewed)
    if damage=='missing_day':m['results'].pop(2)
    elif damage=='reordered':m['results'][1:3]=reversed(m['results'][1:3])
    elif damage=='reused_file':m['results'][-1]['frames']=copy.deepcopy(m['results'][0]['frames'])
    elif damage=='bad_source':(tmp_path/m['results'][1]['frames'][0]['file']).write_bytes(b'changed')
    elif damage=='raw_digest':m['results'][1]['raw_rows_digest']='0'*64
    elif damage=='sdk_digest':m['results'][1]['sdk_rows_digest']='0'*64
    elif damage=='catalogue_changed':
        basic=copy.deepcopy(BASIC);basic[0][1]='new name';m['results'][-1]=entry(tmp_path,{'method':'query_stock_basic','code':''},basic,'changed-after.gz')
    elif damage=='scope':m['scope']={**scope,'end':'2024-01-09'}
    elif damage=='future_clock':m['results'][1]['frames'][0]['observed_at']='2099-01-01T00:00:00+08:00'
    elif damage=='errors':m['errors']=[{'error':'provider_failure'}]
    elif damage=='empty_scope':scope={**scope,'days':[]};m['scope']=scope
    else:monkeypatch.setattr(u,'_pins',lambda:{'changed':'code'})
    with pytest.raises(ValueError):u.review(tmp_path,m,scope)


def load_collector():
    path=Path(__file__).resolve().parents[3]/'scripts/capture-quant-historical-universe.py'
    spec=importlib.util.spec_from_file_location('unit_universe_collector',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def test_seed_preserves_successful_bytes_clocks_and_failed_origin_without_executing_old_code(tmp_path,reviewed):
    old=tmp_path/'old';old.mkdir();m,scope,*_=setup(old,reviewed);m['results']=m['results'][:3];m['errors']=[{'type':'NetworkFailure'}]
    original=json.dumps(m,ensure_ascii=False).encode();(old/'manifest.json').write_bytes(original)
    target=tmp_path/'new';target.mkdir();module=load_collector();seed=module.seed_completed(old,target,scope)
    assert len(seed['daily_entries'])==2 and seed['prior_errors_retained']==m['errors']
    assert (target/'seed-origin-manifest.json').read_bytes()==original and (old/'manifest.json').read_bytes()==original
    for day,e in seed['daily_entries'].items():
        assert e['origin']=='verified_prior_capture' and e['frames'][0]['observed_at']=='2026-10-01T12:00:00+08:00'
        assert u.verify_entry(target,e)==u.verify_entry(old,next(r for r in m['results'] if r['query'].get('day')==day))


@pytest.mark.parametrize('damage',['duplicate','outside','corrupt'])
def test_seed_refuses_bad_or_conflicting_successful_entries(tmp_path,reviewed,damage):
    old=tmp_path/'old';old.mkdir();m,scope,*_=setup(old,reviewed)
    if damage=='duplicate':m['results'].insert(2,copy.deepcopy(m['results'][1]))
    elif damage=='outside':scope={**scope,'days':scope['days'][1:]};m['scope']=scope
    else:(old/'day-0.gz').write_bytes(b'corrupt')
    (old/'manifest.json').write_text(json.dumps(m));target=tmp_path/'new';target.mkdir()
    with pytest.raises(ValueError):load_collector().seed_completed(old,target,scope)


def test_parent_worker_source_mismatch_is_rejected_before_optional_sdk(tmp_path):
    module=load_collector()
    with pytest.raises(ValueError,match='Parent/worker'):module.worker({'collector_sha256':'wrong','source_pins':{}},tmp_path)


@pytest.mark.parametrize('damage',['gzip_sha','raw_sha','escape','bomb'])
def test_bounded_archive_does_not_accept_forged_hashes_path_escape_or_decompression_bomb(tmp_path,damage):
    q={'method':'query_stock_basic','code':''};e=entry(tmp_path,q,BASIC,'source.gz');f=e['frames'][0]
    if damage=='gzip_sha':f['gzip_sha256']='0'*64
    elif damage=='raw_sha':f['sha256']='0'*64
    elif damage=='escape':f['file']='../source.gz'
    else:
        raw=b'x'*(4*1024*1024+1);packed=gzip.compress(raw,mtime=0);(tmp_path/'source.gz').write_bytes(packed)
        f.update(gzip_sha256=hashlib.sha256(packed).hexdigest(),sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw))
    with pytest.raises(ValueError):u.read_frame(tmp_path,f)


def test_query_deadline_escapes_sdk_exception_handler_and_restores_owned_signal(monkeypatch):
    module=load_collector();handler={'current':'previous'};timers=[]
    def install(sig,new):old=handler['current'];handler['current']=new;return old
    monkeypatch.setattr(module.signal,'signal',install);monkeypatch.setattr(module.signal,'setitimer',lambda kind,seconds:timers.append(seconds))
    def sdk_receive():
        try:handler['current'](None,None)
        except Exception:return 'SDK incorrectly swallowed owned deadline'
    with pytest.raises(module.QueryDeadline):module.bounded_call(sdk_receive)
    assert handler['current']=='previous' and timers==[45,0]


@pytest.mark.parametrize('case',['stable','seed','network_once','network_twice','bad_crc','sdk_changed'])
def test_owned_worker_retries_only_one_network_failure_and_retains_raw_attempts(tmp_path,reviewed,monkeypatch,case):
    import types
    old=tmp_path/'old';old.mkdir();m,scope,*_=setup(old,reviewed);module=load_collector();work=tmp_path/'new';work.mkdir()
    package=tmp_path/'fake_sdk';package.mkdir();init=package/'__init__.py';init.write_text('# isolated fixture SDK, no network\n')
    sdk=types.ModuleType('baostock');sdk.__file__=str(init);util=types.ModuleType('baostock.util');sock=types.ModuleType('baostock.util.socketutil')
    common=types.ModuleType('baostock.common');context=types.ModuleType('baostock.common.context');closed=[]
    context.default_socket=types.SimpleNamespace(close=lambda:closed.append(True));common.context=context
    query_rows={e['query'].get('day'):u.verify_entry(old,e) for e in m['results'][1:-1]};calls={};logins=[]
    def wire(message):
        q=json.loads(message);rows=BASIC if q['method']=='query_stock_basic' else query_rows[q['day']]
        raw=response(q,rows)
        if case=='bad_crc' and q['method']=='query_all_stock':raw=raw.replace(b'2013',b'2014',1) if b'2013' in raw else raw[:-15]+b'0<![CDATA[]]>\n'
        return raw.decode()
    sock.send_msg=wire;util.socketutil=sock
    sdk.login=lambda:(logins.append(True) or types.SimpleNamespace(error_code='0'))
    sdk.logout=lambda:init.write_text('# changed fixture SDK\n') if case=='sdk_changed' else None
    class Result:
        error_code='0';error_msg='success'
        def __init__(self,q,rows):self.fields=u.FIELDS[q['method']];self.rows=u.decode_response(response(q,rows),q)['sdk_rows'];self.i=-1
        def next(self):self.i+=1;return self.i<len(self.rows)
        def get_row_data(self):return self.rows[self.i]
    def query(q):
        key=q.get('day','basic');calls[key]=calls.get(key,0)+1
        if q.get('day')==scope['days'][0] and case in ('network_once','network_twice') and (case=='network_twice' or calls[key]==1):
            raise module.QueryDeadline('isolated timed-out query')
        sock.send_msg(json.dumps(q));return Result(q,BASIC if q['method']=='query_stock_basic' else query_rows[q['day']])
    sdk.query_stock_basic=lambda **kw:query({'method':'query_stock_basic','code':''})
    sdk.query_all_stock=lambda **kw:query({'method':'query_all_stock','day':kw['day']})
    for n,v in [('baostock',sdk),('baostock.util',util),('baostock.util.socketutil',sock),('baostock.common',common),('baostock.common.context',context)]:monkeypatch.setitem(sys.modules,n,v)
    monkeypatch.setattr(module.importlib.metadata,'version',lambda _: '0.9.1');monkeypatch.setattr(module.socket,'setdefaulttimeout',lambda _:None)
    monkeypatch.setattr(module.time,'sleep',lambda _:None);monkeypatch.setattr(module,'bounded_call',lambda f:f())
    plan={'scope':scope,'source_pins':dict(u.SOURCE_PINS),'collector_sha256':module.LOADED_SHA}
    if case=='seed':
        m['results']=m['results'][:3];m['errors']=[{'type':'old network failure'}];(old/'manifest.json').write_text(json.dumps(m))
        plan['seed']=module.seed_completed(old,work,scope)
    code=module.worker(plan,work);captured=json.loads((work/'manifest.json').read_text())
    if case in ('stable','seed','network_once'):
        assert code==0 and not captured['errors'] and u.review(work,captured,scope)['all_daily_joins_consistent']
        if case=='network_once':assert len(captured['failed_network_attempts'])==1 and len(closed)==1 and len(logins)==2
        if case=='seed':assert not any(d in calls for d in scope['days'][:2]) and sum(e.get('origin')=='verified_prior_capture' for e in captured['results'])==2
    else:
        assert code==1 and captured['errors']
        with pytest.raises(ValueError):u.review(work,captured,scope)
        if case=='network_twice':assert len(captured['failed_network_attempts'])==2 and len(logins)==2
        if case=='bad_crc':assert not captured['failed_network_attempts'] and len(logins)==1


@pytest.mark.parametrize('case',['valid','overwrite','incomplete'])
def test_offline_review_cli_preserves_existing_reports_and_rejects_partial_range(tmp_path,reviewed,case):
    calendar,sources=copy.deepcopy(reviewed)
    calendar['source_files']={sha:'notice-'+str(i)+'.html' for i,sha in enumerate(sources)}
    m,scope,exps,calendar,sources=setup(tmp_path,(calendar,sources));plan={'scope':scope};m['plan_digest']=digest(plan)
    if case=='incomplete':m['results'].pop(2)
    (tmp_path/'capture-plan.json').write_text(json.dumps(plan));(tmp_path/'manifest.json').write_text(json.dumps(m));(tmp_path/'experiments.json').write_text(json.dumps(exps))
    for sha,name in calendar['source_files'].items():(tmp_path/name).write_bytes(sources[sha])
    (tmp_path/'calendar.json').write_text(json.dumps(calendar));output=tmp_path/'review.json'
    if case=='overwrite':output.write_text('preserve')
    script=Path(__file__).resolve().parents[3]/'scripts/quant-historical-universe-review.py'
    result=subprocess.run([sys.executable,str(script),'--capture-dir',str(tmp_path),'--experiments',str(tmp_path/'experiments.json'),
        '--calendar',str(tmp_path/'calendar.json'),'--calendar-dir',str(tmp_path),'--output',str(output)],capture_output=True,text=True)
    if case=='valid':assert result.returncode==0 and json.loads(output.read_text())['days']==5
    elif case=='overwrite':assert result.returncode!=0 and output.read_text()=='preserve'
    else:assert result.returncode!=0 and not output.exists()


def load_sealer():
    path=Path(__file__).resolve().parents[3]/'scripts/seal-quant-universe-prefix.py';spec=importlib.util.spec_from_file_location('unit_universe_sealer',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def test_append_only_live_prefix_preserves_torn_tail_and_can_seed_without_claiming_full_capture(tmp_path,reviewed):
    source=tmp_path/'source';source.mkdir();m,scope,*_=setup(source,reviewed);plan={'scope':scope,'collector_sha256':'unit-source','source_pins':dict(u.SOURCE_PINS)}
    (source/'capture-plan.json').write_text(json.dumps(plan));entries=m['results'][:3]
    following=(json.dumps(m['results'][3])+'\n').encode();tail=following[:13]
    raw=b''.join((json.dumps(e,ensure_ascii=False)+'\n').encode() for e in entries)+tail
    journal=source/'completed-queries.jsonl';journal.write_bytes(raw);work=tmp_path/'prefix'
    result=load_sealer().seal(source,work)
    assert result['daily_entries']==2 and result['trailing_partial_bytes_preserved']==len(tail) and not result['full_capture_complete']
    assert journal.read_bytes()==raw and (work/'source-journal-snapshot.jsonl').read_bytes()==raw
    # Producer continues appending after the frozen prefix was taken.
    with journal.open('ab') as f:f.write(following[len(tail):])
    target=tmp_path/'resumed';target.mkdir();seed=load_collector().seed_completed(work,target,scope)
    assert len(seed['daily_entries'])==2 and seed['prior_errors_retained'][0]['type']=='PrefixCheckpointNotFullCapture'
    with pytest.raises(ValueError):u.review(work,json.loads((work/'manifest.json').read_text()),scope)


@pytest.mark.parametrize('damage',['no_complete_line','reorder','bad_frame','overwrite'])
def test_prefix_sealer_refuses_invalid_snapshots_without_changing_producer(tmp_path,reviewed,damage):
    source=tmp_path/'source';source.mkdir();m,scope,*_=setup(source,reviewed)
    (source/'capture-plan.json').write_text(json.dumps({'scope':scope,'collector_sha256':'unit','source_pins':dict(u.SOURCE_PINS)}))
    entries=m['results'][:3]
    if damage=='reorder':entries[1:]=reversed(entries[1:])
    raw=b'{"partial"' if damage=='no_complete_line' else b''.join((json.dumps(e)+'\n').encode() for e in entries)
    journal=source/'completed-queries.jsonl';journal.write_bytes(raw);work=tmp_path/'prefix'
    if damage=='bad_frame':(source/'day-0.gz').write_bytes(b'broken')
    if damage=='overwrite':work.mkdir();(work/'keep').write_text('preserve')
    with pytest.raises(ValueError):load_sealer().seal(source,work)
    assert journal.read_bytes()==raw
    if damage=='overwrite':assert (work/'keep').read_text()=='preserve'
    else:assert not (work/'manifest.json').exists()


def test_prefix_tool_version_change_cannot_relabel_loaded_code(tmp_path,monkeypatch):
    module=load_sealer();monkeypatch.setattr(module,'LOADED_TOOL_SHA','wrong-loaded-source')
    with pytest.raises(ValueError,match='tool source changed'):module.seal(tmp_path,tmp_path/'new')
