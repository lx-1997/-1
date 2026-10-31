import copy
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend'))
spec=importlib.util.spec_from_file_location('cohort_capture',ROOT/'scripts/capture-quant-financial-cohort.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def inputs():
    codes=['sh.'+str(600000+i) for i in range(64)]
    p={'schema':'historical-research-sample-v1','rules':m.sample.RULES,'eligible_codes':codes,'start':'2023-10-26','end':'2026-10-29'}
    p['requests']=m.sample.sample(codes,p['start'],p['end'])
    d={'stockList':[{'code':c.split('.')[1],'category':'A股','orgId':'abc'+c.split('.')[1],'zwjc':'合成测试'} for c in codes]}
    return p,d


def row():
    return {'secCode':'600001','orgId':'abc001','announcementId':'123','announcementTitle':'测试股份2024年年度报告全文',
            'announcementTime':int(datetime.fromisoformat('2025-04-20T00:00:00+08:00').timestamp()*1000),'adjunctUrl':'finalpage/2025-04-20/123.PDF'}


def test_first_eight_original_hash_order_alphanumeric_ids_and_no_replacement():
    p,d=inputs();r=m.register(json.dumps(p).encode(),json.dumps(d).encode())
    assert [i['code'] for i in r['issuers']]==[q['code'] for q in p['requests'][:8]]
    assert all(i['org_id'].startswith('abc') for i in r['issuers'])
    assert not r['historical_pit_certified'] and not r['full_revision_history_certified']
    d['stockList']=[x for x in d['stockList'] if x['code']!=r['issuers'][0]['symbol']]
    with pytest.raises(ValueError,match='do not replace'):m.register(json.dumps(p).encode(),json.dumps(d).encode())


def test_changed_original_sample_and_ambiguous_directory_refused():
    p,d=inputs();p['requests'].reverse()
    with pytest.raises(ValueError):m.register(json.dumps(p).encode(),json.dumps(d).encode())
    p,d=inputs();d['stockList'].extend(copy.deepcopy(d['stockList']))
    with pytest.raises(ValueError):m.register(json.dumps(p).encode(),json.dumps(d).encode())


def test_index_identity_beijing_day_complete_pages_and_empty_scope():
    r=row();issuer={'symbol':'600001','org_id':'abc001'}
    assert m.index_page({'totalAnnouncement':1,'announcements':[r]},1,issuer)==(1,[r])
    assert m.index_page({'totalAnnouncement':0,'announcements':None},1,issuer)==(0,[])
    for field,value in [('orgId','wrong'),('secCode','600002'),('announcementTime',True),('adjunctUrl','finalpage/2025-04-19/123.PDF')]:
        changed={**r,field:value}
        with pytest.raises(ValueError):m.index_page({'totalAnnouncement':1,'announcements':[changed]},1,issuer)
    for data,page,seen in [({'totalAnnouncement':1,'announcements':[]},1,None),({'totalAnnouncement':1,'announcements':[r]},1,2),({'totalAnnouncement':121,'announcements':[]},1,None)]:
        with pytest.raises(ValueError):m.index_page(data,page,issuer,seen)


@pytest.mark.parametrize('title,expected',[
 ('江河集团2024年年度报告全文',True),('2024年年度报告（更正后）',True),('2025年一季度报告',True),
 ('2024年第一季度报告',True),('2025年第一季度报告（修订版）',True),
 ('2024年年度报告摘要',False),('关于2024年年度报告',False),('2025年年度报告',False),('2025年一季度报告（英文版）',False)])
def test_only_declared_full_reports_not_summaries_or_explanations(title,expected):
    assert m.selected({'announcementTitle':title}) is expected


class Response:
    def __init__(self,status,chunks,fail=False):self.status_code=status;self.chunks=chunks;self.fail=fail
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def iter_bytes(self):
        yield from self.chunks
        if self.fail:raise TimeoutError('synthetic midstream timeout')


def test_failed_attempts_and_partial_bytes_are_retained_and_counted(tmp_path,monkeypatch):
    ret=m.Retainer(tmp_path);client=SimpleNamespace(stream=lambda *a,**k:Response(200,[b'%PDF-partial'],True))
    with pytest.raises(TimeoutError):ret.get(client,'https://example.invalid','a.pdf',pdf=True)
    assert ret.captures[0]['valid_response'] is False and ret.captures[0]['error_type']=='TimeoutError'
    assert (tmp_path/'a.pdf.response.bin').read_bytes()==b'%PDF-partial'
    assert ret.used==12 and len(ret.captures)==1
    monkeypatch.setitem(m.LIMITS,'requests',1)
    with pytest.raises(TimeoutError):ret.get(client,'https://example.invalid','b.pdf',pdf=True)
    assert len(ret.captures)==1 and not (tmp_path/'b.pdf.response.bin').exists()


def test_non_success_and_byte_budget_never_mark_valid_pdf(tmp_path,monkeypatch):
    r=m.Retainer(tmp_path);c=SimpleNamespace(stream=lambda *a,**k:Response(403,[b'forbidden']))
    with pytest.raises(ValueError):r.get(c,'https://example.invalid','denied.pdf',pdf=True)
    assert r.captures[0]['status']==403 and (tmp_path/'denied.pdf.response.bin').read_bytes()==b'forbidden'
    monkeypatch.setitem(m.LIMITS,'pdf_bytes',5)
    c=SimpleNamespace(stream=lambda *a,**k:Response(200,[b'%PDF-',b'overflow']))
    with pytest.raises(ValueError):r.get(c,'https://example.invalid','large.pdf',pdf=True)
    assert (tmp_path/'large.pdf.response.bin').read_bytes()==b'%PDF-' and not r.captures[-1]['valid_response']


def test_capture_preserves_all_failed_queries_without_retries_or_pdfs(tmp_path,monkeypatch):
    p,d=inputs();a=tmp_path/'sample.json';a.write_text(json.dumps(p));b=tmp_path/'directory.json';b.write_text(json.dumps(d));calls=[]
    class Client:
        def __init__(self,**kwargs):assert kwargs['trust_env'] is False
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def stream(self,*args,**kwargs):calls.append(1);return Response(403,[b'forbidden'])
    import httpx
    monkeypatch.setattr(httpx,'Client',Client)
    out=tmp_path/'capture';r=m.capture(a,b,out)
    assert len(calls)==40 and len(r['captures'])==40 and len(r['errors'])==40
    assert not r['query_inventory_complete'] and r['not_started_queries']==0 and not r['filings']
    assert len(r['plan']['issuers'])==8 and all(not q['complete'] for q in r['queries'])
    assert all((out/c['file']).read_bytes()==b'forbidden' for c in r['captures'])
    assert json.loads((out/'manifest.json').read_text())==r


def test_index_only_remaining_slice_preserves_all_56_in_original_order():
    p,d=inputs();r=m.register(json.dumps(p).encode(),json.dumps(d).encode(),offset=8,count=56,index_only=True)
    assert r['schema']=='blind-cohort-financial-index-v2' and r['sample_slice']=={'offset':8,'count':56}
    assert [i['code'] for i in r['issuers']]==[q['code'] for q in p['requests'][8:]]
    assert r['index_only'] and r['limits']['pdf_bytes']==0 and r['limits']['requests']==400
    assert r['limits']['total_bytes']==32*1024**2


@pytest.mark.parametrize('offset,count,index_only',[(-1,8,True),(8,57,True),(0,0,True),(True,8,True),(0,64,False)])
def test_invalid_slice_and_large_pdf_batch_rejected(offset,count,index_only):
    p,d=inputs()
    with pytest.raises(ValueError,match='sample slice'):m.register(json.dumps(p).encode(),json.dumps(d).encode(),offset=offset,count=count,index_only=index_only)


def test_index_only_finds_56_documents_without_any_pdf_GET_or_download(tmp_path,monkeypatch):
    p,d=inputs();a=tmp_path/'sample.json';a.write_text(json.dumps(p));b=tmp_path/'directory.json';b.write_text(json.dumps(d));calls=[]
    class Client:
        def __init__(self,**kwargs):assert kwargs['trust_env'] is False
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def stream(self,method,url,**kwargs):
            assert method=='POST' and url==m.INDEX;form=kwargs['data'];calls.append(form)
            code,org=form['stock'].split(',');r={**row(),'secCode':code,'orgId':org,'announcementId':code,'adjunctUrl':'finalpage/2025-04-20/'+code+'.PDF'}
            rows=[r] if form['searchkey']=='2024年年度报告' else []
            return Response(200,[json.dumps({'totalAnnouncement':len(rows),'announcements':rows}).encode()])
    import httpx
    monkeypatch.setattr(httpx,'Client',Client)
    out=tmp_path/'inventory';r=m.capture(a,b,out,offset=8,count=56,index_only=True)
    assert len(calls)==280 and len(r['filings'])==56 and r['query_inventory_complete']
    assert len(r['PDFs_not_requested'])==56 and r['PDFs_not_retained']==[] and r['errors']==[]
    assert all('capture' not in f for f in r['filings'].values()) and not list(out.glob('*.pdf'))
    assert all(c['request_form'] is not None for c in r['captures'])


def test_index_budget_stops_without_turning_unattempted_queries_into_empty_history(tmp_path,monkeypatch):
    p,d=inputs();a=tmp_path/'sample.json';a.write_text(json.dumps(p));b=tmp_path/'directory.json';b.write_text(json.dumps(d));calls=[]
    class Client:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def stream(self,*args,**kwargs):calls.append(1);return Response(200,[b'{"totalAnnouncement":0,"announcements":[]}'])
    import httpx
    monkeypatch.setattr(httpx,'Client',Client);monkeypatch.setitem(m.INDEX_LIMITS,'requests',2)
    r=m.capture(a,b,tmp_path/'limited',offset=8,count=56,index_only=True)
    assert len(calls)==2 and len(r['queries'])==2 and r['not_started_queries']==278
    assert not r['query_inventory_complete'] and len(r['plan']['issuers'])==56


@pytest.mark.parametrize('extension',['PDF','pdf'])
def test_actual_upper_or_lower_pdf_extension_preserves_exact_index_URL(extension):
    r=row();r['adjunctUrl']='finalpage/2025-04-20/123.'+extension
    total,rows=m.index_page({'totalAnnouncement':1,'announcements':[r]},1,{'symbol':'600001','org_id':'abc001'})
    assert total==1 and rows[0]['adjunctUrl']==r['adjunctUrl']


@pytest.mark.parametrize('path',['finalpage/2025-04-20/123.PdF','finalpage/2025-04-20/123.pdf?x=1','finalpage/2025-04-20/124.pdf','finalpage/2025-04-19/123.pdf'])
def test_extension_support_does_not_loosen_path_document_or_date_binding(path):
    r={**row(),'adjunctUrl':path}
    with pytest.raises(ValueError):m.index_page({'totalAnnouncement':1,'announcements':[r]},1,{'symbol':'600001','org_id':'abc001'})
