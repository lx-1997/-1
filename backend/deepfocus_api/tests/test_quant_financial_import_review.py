import copy,json,hashlib,sqlite3,subprocess,sys,importlib.util
from pathlib import Path
from contextlib import closing
from types import SimpleNamespace
import pytest
import fitz
from deepfocus_api import quant_financial_import_review as body,quant_financial_store as store
from deepfocus_api.tests.test_quant_financial_store import ledger
from deepfocus_api.tests.test_quant_financial_vintages import fixture
from deepfocus_api.tests.financial_pdf_fixture import readable


def test_readable_preflight_preserves_inputs_and_does_not_certify_full_report(ledger):
    m,s=readable(*fixture());before=copy.deepcopy(m);source=dict(s);r=body.review(m,s)
    assert m==before and s==source and not ledger.exists()
    assert r['manifest_digest']==store.digest(m) and len(r['documents'])==2
    assert all(d['page_count']==2 and d['declared_parent_page_references_in_range'] for d in r['documents'])
    assert r['full_report_or_financial_facts_certified'] is False and not r['externally_attested_time']
    raw=dict(r);fingerprint=raw.pop('review_digest');assert store.digest(raw)==fingerprint


@pytest.mark.parametrize('case',['annual-summary','quarter-summary','blank-cover','malformed','encrypted','outside-page'])
def test_invalid_body_or_impossible_page_cannot_pass(ledger,case):
    cover='2025 年第一季度报告'
    if case=='annual-summary':cover='2024 年年度报告摘要'
    if case=='quarter-summary':cover='2025 年季度报告摘要'
    if case=='blank-cover':cover=''
    m,s=readable(*fixture(),cover=cover)
    if case in {'malformed','encrypted'}:
        old=m['vintages'][0]['source_sha256'];name=m['source_files'].pop(old);raw=s.pop(old)
        if case=='malformed':raw=b'%PDF-synthetic-corrupt'
        else:
            with fitz.open(stream=raw,filetype='pdf') as d:raw=d.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256,owner_pw='synthetic-only',user_pw='synthetic-only')
        sha=hashlib.sha256(raw).hexdigest();s[sha]=raw;m['source_files'][sha]=name;m['vintages'][0]['source_sha256']=sha
    if case=='outside-page':m['vintages'][0]['source_page']=3
    with pytest.raises(ValueError):body.review(m,s)
    assert not ledger.exists()


def test_metric_specific_page_bounds_and_runtime_source_guard(ledger,monkeypatch):
    m,s=readable(*fixture());r=m['vintages'][0];r['metric_source_pages']={k:(3 if k=='revenue' else 2) for k in r['metrics']}
    with pytest.raises(body.PreflightRejected,match='invalid_page_reference'):body.review(m,s)
    monkeypatch.setattr(body,'_pins',lambda:{})
    with pytest.raises(ValueError,match='source changed'):body.review(m,s)
    assert not ledger.exists()


def test_cooperative_timeout_and_missing_PDF_dependency_fail_closed(ledger,monkeypatch):
    m,s=readable(*fixture());ticks=iter([0,6]);monkeypatch.setattr(body,'time',SimpleNamespace(monotonic=lambda:next(ticks)))
    with pytest.raises(TimeoutError):body.review(m,s)
    monkeypatch.setitem(sys.modules,'fitz',None)
    with pytest.raises(ImportError):body.review(m,s)
    assert not ledger.exists()


def cli_files(tmp_path,m,s):
    e=tmp_path/'evidence';e.mkdir()
    for sha,name in m['source_files'].items():(e/name).write_bytes(s[sha])
    file=tmp_path/'manifest.json';file.write_text(json.dumps(m));script=Path(__file__).resolve().parents[3]/'scripts/quant-financial-receipt.py'
    return file,e,script


@pytest.mark.parametrize('cover',['2024 年年度报告摘要','','late-summary'])
def test_actual_default_CLI_rejects_summary_or_unreadable_before_creating_DB(ledger,tmp_path,cover):
    m,s=readable(*fixture(),cover=cover)
    if cover=='late-summary':
        item=m['vintages'][1];old=item['source_sha256'];name=m['source_files'].pop(old);s.pop(old)
        with fitz.open() as d:
            page=d.new_page();page.insert_text((40,50),'2025 年季度报告摘要',fontname='china-s',fontsize=12);raw=d.tobytes()
        sha=hashlib.sha256(raw).hexdigest();s[sha]=raw;m['source_files'][sha]=name;item['source_sha256']=sha
    file,e,script=cli_files(tmp_path,m,s)
    run=subprocess.run([sys.executable,str(script),'--db',str(ledger.resolve()),'--owner','operator','import','--manifest',str(file),'--evidence-dir',str(e),'--label','negative synthetic probe'],capture_output=True,text=True,timeout=20)
    assert run.returncode==1,run.stdout+run.stderr
    result=json.loads(run.stdout);assert result['ok'] is False and result['promotion_allowed'] is False
    assert not ledger.exists()
    assert result['body_preflight_rejection']['code']==('unreadable_cover' if not cover else 'summary_cover')
    assert result['body_preflight_rejection']['document_ids']==(['222'] if cover=='late-summary' else ['111'])


def test_actual_CLI_idempotence_and_preflight_receipt_not_archived_as_fresh_input(ledger,tmp_path):
    m,s=readable(*fixture());file,e,script=cli_files(tmp_path,m,s)
    cmd=[sys.executable,str(script),'--db',str(ledger.resolve()),'--owner','operator','import','--manifest',str(file),'--evidence-dir',str(e),'--label','synthetic positive']
    first=subprocess.run(cmd,capture_output=True,text=True,timeout=20);assert first.returncode==0,first.stdout+first.stderr;a=json.loads(first.stdout)
    with closing(sqlite3.connect(ledger)) as c:before=c.execute('SELECT id,created,fingerprint,payload FROM qr_financial_bundle').fetchall()
    second=subprocess.run(cmd,capture_output=True,text=True,timeout=20);assert second.returncode==0,second.stdout+second.stderr;b=json.loads(second.stdout)
    assert a['id']==b['id'] and a['recorded_at']==b['recorded_at']
    with closing(sqlite3.connect(ledger)) as c:assert c.execute('SELECT id,created,fingerprint,payload FROM qr_financial_bundle').fetchall()==before
    value=json.loads(before[0][-1]);assert 'import_body_preflight' not in value and value['input']['manifest']==m
    assert a['import_body_preflight']['manifest_digest']==b['import_body_preflight']['manifest_digest']==store.digest(m)


def test_input_file_change_during_preflight_never_reaches_store_put(ledger,tmp_path,monkeypatch,capsys):
    m,s=readable(*fixture());file,e,script=cli_files(tmp_path,m,s);spec=importlib.util.spec_from_file_location('owned_receipt_cli',script);cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    def changed(*a):file.write_text('{}');return {}
    def forbidden(*a):raise AssertionError('Must reject changed operator files before store put')
    monkeypatch.setattr(body,'review',changed);monkeypatch.setattr(store,'put',forbidden)
    monkeypatch.setattr(sys,'argv',[str(script),'--db',str(ledger.resolve()),'--owner','operator','import','--manifest',str(file),'--evidence-dir',str(e),'--label','changed synthetic'])
    assert cli.main()==1 and not ledger.exists();assert json.loads(capsys.readouterr().out)['error_type']=='ValueError'


def test_total_source_budget_rejects_before_native_reader_or_store(ledger,tmp_path,monkeypatch,capsys):
    m,s=readable(*fixture());file,e,script=cli_files(tmp_path,m,s)
    for n in range(3):
        target=e/('extra-'+str(n)+'.bin')
        with target.open('wb') as f:f.write(bytes([n+1]));f.truncate(10*1024**2)
        raw=target.read_bytes();m['source_files'][hashlib.sha256(raw).hexdigest()]=target.name
    file.write_text(json.dumps(m));spec=importlib.util.spec_from_file_location('budget_receipt_cli',script);cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    def forbidden(*a):raise AssertionError('Budget must fail before PDF parser/store')
    calls=[];real=cli.bounded
    def counted(path,limit):calls.append((path.name,limit));return real(path,limit)
    monkeypatch.setattr(cli,'bounded',counted);monkeypatch.setattr(body,'review',forbidden);monkeypatch.setattr(store,'put',forbidden)
    monkeypatch.setattr(sys,'argv',[str(script),'--db',str(ledger.resolve()),'--owner','operator','import','--manifest',str(file),'--evidence-dir',str(e),'--label','budget synthetic'])
    assert cli.main()==1 and not ledger.exists()
    assert any(name=='extra-2.bin' and limit<10*1024**2 for name,limit in calls)
    assert json.loads(capsys.readouterr().out)['error_type']=='ValueError'


def test_source_grows_after_preflight_recheck_is_bounded_and_no_write(ledger,tmp_path,monkeypatch,capsys):
    m,s=readable(*fixture());file,e,script=cli_files(tmp_path,m,s);spec=importlib.util.spec_from_file_location('growing_receipt_cli',script);cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    target=e/m['source_files'][m['vintages'][0]['source_sha256']]
    def grew(*a):
        with target.open('r+b') as f:f.truncate(10*1024**2+1)
        return {}
    def forbidden(*a):raise AssertionError('Growing source must fail before store')
    monkeypatch.setattr(body,'review',grew);monkeypatch.setattr(store,'put',forbidden)
    monkeypatch.setattr(sys,'argv',[str(script),'--db',str(ledger.resolve()),'--owner','operator','import','--manifest',str(file),'--evidence-dir',str(e),'--label','growing synthetic'])
    assert cli.main()==1 and not ledger.exists();assert json.loads(capsys.readouterr().out)['error_type']=='ValueError'
