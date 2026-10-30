"""Offline collector tests: issuer identity, raw retention and failure disclosure."""
import json,runpy,sys
from pathlib import Path
import pytest
from deepfocus_api.quant_research_engine import digest


def run_collector(tmp_path,monkeypatch,damage=None):
    import httpx
    dataset={};result={'dataset_digest':digest(dataset)}
    sealed=[{'request':{'symbols':['000001'],'start_date':'2023-10-26','end_date':'2026-10-30'},
             'result':result,'dataset':dataset,'result_digest':digest(result)}]
    exp=tmp_path/'sealed.json';exp.write_text(json.dumps(sealed));workspace=tmp_path/'capture'
    calls=[]
    class Response:
        def __init__(self,body):self.body=body
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def raise_for_status(self):pass
        def iter_bytes(self):yield self.body
    class Client:
        def __init__(self,**kw):assert kw['trust_env'] is False
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def stream(self,method,url,data=None):
            calls.append((method,url,data))
            if url.endswith('szse_stock.json'):
                if damage=='directory_error':raise RuntimeError('synthetic identity lookup failure')
                return Response(json.dumps({'stockList':[{'code':'000001','category':'A股','orgId':'fixtureorg'}]}).encode())
            if method=='POST':
                assert data['stock']=='000001,fixtureorg'
                if damage=='http_error':raise RuntimeError('synthetic provider error')
                rows=[{'secCode':'000001','orgId':'fixtureorg','announcementId':'123','announcementTime':1717632000000,
                      'announcementTitle':'2025年中期分红A股实施公告' if damage=='alternative_title' else '2023年年度权益分派实施公告',
                      'adjunctUrl':'finalpage/2024-06-06/123.PDF'}]
                if damage=='wrong_stock':rows[0]['secCode']='600519'
                elif damage=='wrong_org':rows[0]['orgId']='other'
                elif damage=='source_path':rows[0]['adjunctUrl']='../../other.pdf'
                elif damage=='filing_id':rows[0]['announcementId']='../../escape'
                elif damage=='future_disclosure':rows[0]['announcementTime']=1900000000000
                elif damage=='duplicate':rows.append(dict(rows[0]))
                return Response(json.dumps({'totalAnnouncement':len(rows),'announcements':rows}).encode())
            if damage=='oversize':return Response(b'%PDF-'+b'x'*(10*1024*1024))
            return Response(b'not_pdf' if damage=='not_pdf' else b'%PDF- synthetic fixture')
    monkeypatch.setattr(httpx,'Client',Client)
    script=Path(__file__).resolve().parents[3]/'scripts/capture-quant-implementation-filings.py'
    monkeypatch.setattr(sys,'argv',[str(script),'--experiments',str(exp),'--workspace',str(workspace)])
    with pytest.raises(SystemExit) as exitinfo:runpy.run_path(str(script),run_name='__main__')
    return workspace,json.loads((workspace/'filing-captures.json').read_text()),exitinfo.value.code,calls


def test_capture_deduplicates_discovery_hits_but_never_creates_verified_events(tmp_path,monkeypatch):
    folder,manifest,code,calls=run_collector(tmp_path,monkeypatch)
    assert code==0 and not manifest['errors'] and len(manifest['filings'])==1
    assert len([c for c in calls if c[1].endswith('.PDF')])==1
    assert (folder/'000001-123.pdf').read_bytes().startswith(b'%PDF-')
    assert not manifest['implementation_facts_reviewed'] and not manifest['economic_coverage_verified']
    assert not manifest['execution_events_generated']


@pytest.mark.parametrize('damage',['http_error','wrong_stock','wrong_org','source_path','filing_id','future_disclosure','duplicate','not_pdf','oversize'])
def test_partial_or_wrong_source_cannot_pass_as_complete_empty_history(tmp_path,monkeypatch,damage):
    folder,manifest,code,calls=run_collector(tmp_path,monkeypatch,damage)
    assert code==1 and manifest['errors']
    assert (folder/'security-directory.json').is_file()
    assert not manifest['economic_coverage_verified']
    assert not list(folder.glob('*.pdf')) if damage in ('not_pdf','oversize') else True


def test_alternative_midyear_cash_title_is_not_silently_dropped(tmp_path,monkeypatch):
    _,manifest,code,_=run_collector(tmp_path,monkeypatch,'alternative_title')
    assert code==0 and len(manifest['filings'])==1


def test_directory_failure_is_retained_as_failure_not_an_empty_success(tmp_path,monkeypatch):
    _,manifest,code,_=run_collector(tmp_path,monkeypatch,'directory_error')
    assert code==1 and manifest['errors'][0]['stage']=='identity_directory'
    assert not manifest['filings'] and not manifest['economic_coverage_verified']
