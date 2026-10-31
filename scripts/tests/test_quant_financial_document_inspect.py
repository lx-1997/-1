"""Offline synthetic documents only; never downloads or declares revisions."""
from pathlib import Path
import importlib.util
import copy,json,hashlib,time
import fitz
import pytest

spec=importlib.util.spec_from_file_location('financial_document_inspect',Path(__file__).resolve().parents[1]/'quant-financial-document-inspect.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def pdf(text):
    with fitz.open() as d:
        page=d.new_page();page.insert_text((40,50),text,fontname='china-s',fontsize=12)
        return d.tobytes()


def capture(tmp_path,contents):
    caps=[];documents=[]
    for i,body in enumerate(contents,1):
        doc=str(100+i);item={'symbol':'600001','document_id':doc,'period':'2024-12-31','published_date':'2025-04-20','exact_adjunct_path':'finalpage/2025-04-20/'+doc+'.PDF','URL':'https://static.cninfo.com.cn/finalpage/2025-04-20/'+doc+'.PDF'};documents.append(item);file=doc+'.pdf';(tmp_path/file).write_bytes(body)
        caps.append({**item,'status':200,'valid':True,'file':file,'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest(),'requested_at':'2026-10-31T00:00:01+00:00','observed_at':'2026-10-31T00:00:02+00:00'})
    value={'plan':{'documents':documents,'registered_at':'2026-10-31T00:00:00+00:00','requests':len(caps),'automatic_retries':0},'finished_at':'2026-10-31T00:00:03+00:00','captures':caps}
    (tmp_path/'capture-manifest.json').write_text(json.dumps(value));return value


def test_identical_bytes_preserve_both_document_ids_no_alias_or_revision(tmp_path):
    body=pdf('2024 年年度报告\n100.01');capture(tmp_path,[body,body]);r=m.review(tmp_path);c=r['comparisons'][0]
    assert c['PDF_bytes_equal'] and c['all_RGB72_pages_equal'] and c['document_ids']==['101','102']
    assert not c['automatic_canonical_selection'] and not c['revision_order_inferred'] and c['manual_content_review_required']
    saved=copy.deepcopy(r);fingerprint=saved.pop('report_digest');assert m.sha(json.dumps(saved,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode())==fingerprint


def test_cover_summary_marker_is_only_a_diagnostic_not_full_report_certificate(tmp_path):
    capture(tmp_path,[pdf('2024 年年度报告'),pdf('2024 年年度报告摘要')]);r=m.review(tmp_path)
    assert [d['first_page_summary_marker'] for d in r['documents']]==[False,True]
    assert r['comparisons'][0]['any_summary_marker_on_cover'] and not r['comparisons'][0]['exact_extracted_text_equal']
    assert not r['financial_values_generated'] and not r['promotion_allowed']


def test_whitespace_hash_never_authorizes_collapsing_numeric_or_layout_differences(tmp_path):
    capture(tmp_path,[pdf('2024 年年度报告\n1 00.01'),pdf('2024 年年度报告\n100.01')]);r=m.review(tmp_path);c=r['comparisons'][0]
    assert c['whitespace_removed_text_equal'] and not c['all_RGB72_pages_equal']
    assert r['whitespace_comparison_is_not_value_or_document_equivalence'] and not c['automatic_canonical_selection']


@pytest.mark.parametrize('damage',['file-bytes','sha','escape','symbol','document','URL','clock','future-publication','incomplete','duplicate','singleton'])
def test_corrupt_scope_or_source_refused_before_PDF_analysis(tmp_path,monkeypatch,damage):
    value=capture(tmp_path,[pdf('first'),pdf('second')]);cap=value['captures'][0]
    if damage=='file-bytes':(tmp_path/cap['file']).write_bytes(b'%PDF-wrong')
    elif damage=='sha':cap['sha256']='0'*64
    elif damage=='escape':cap['file']='../outside.pdf'
    elif damage=='symbol':cap['symbol']='600002'
    elif damage=='document':cap['document_id']='999'
    elif damage=='URL':cap['URL']=cap['URL'].replace('static.cninfo.com.cn','example.invalid')
    elif damage=='clock':cap['observed_at']='2026-10-30T23:59:59+00:00'
    elif damage=='future-publication':
        for field in ['captures']:
            value[field][0]['published_date']='2027-04-20';value[field][0]['exact_adjunct_path']='finalpage/2027-04-20/101.PDF';value[field][0]['URL']='https://static.cninfo.com.cn/finalpage/2027-04-20/101.PDF'
        value['plan']['documents'][0]={k:value['captures'][0][k] for k in value['plan']['documents'][0]}
    elif damage=='incomplete':value['captures'].pop()
    elif damage=='duplicate':value['captures'][1]=copy.deepcopy(value['captures'][0]);value['plan']['documents'][1]=copy.deepcopy(value['plan']['documents'][0])
    elif damage=='singleton':value['captures'][1]['symbol']='600002';value['plan']['documents'][1]['symbol']='600002'
    (tmp_path/'capture-manifest.json').write_text(json.dumps(value))
    def forbidden(*args):raise AssertionError('Must reject before native parsing')
    monkeypatch.setattr(m,'inspect_pdf',forbidden)
    with pytest.raises((ValueError,FileNotFoundError)):m.review(tmp_path)


def test_empty_text_and_cooperative_deadline_are_not_equivalence_certificates(tmp_path):
    with fitz.open() as doc:doc.new_page();blank=doc.tobytes()
    capture(tmp_path,[blank,blank]);r=m.review(tmp_path)
    assert r['documents'][0]['empty_text_pages']==[1] and r['comparisons'][0]['manual_content_review_required']
    with pytest.raises(TimeoutError):m.inspect_pdf(blank,time.monotonic()-1,{'pages':0,'text':0})


@pytest.mark.parametrize('failure',['fsync','race'])
def test_publication_failure_preserves_other_writer_and_no_partial(tmp_path,monkeypatch,failure):
    out=tmp_path/'result.json'
    if failure=='fsync':
        def fail(*a):raise OSError('synthetic flush failure')
        monkeypatch.setattr(m.os,'fsync',fail)
    else:
        def fail(*a):out.write_bytes(b'other-writer');raise FileExistsError()
        monkeypatch.setattr(m.os,'link',fail)
    with pytest.raises(OSError):m.publish({'diagnostic':True},out)
    assert out.read_bytes()==b'other-writer' if failure=='race' else not out.exists()
    assert not list(tmp_path.glob('.financial-doc-*'))
