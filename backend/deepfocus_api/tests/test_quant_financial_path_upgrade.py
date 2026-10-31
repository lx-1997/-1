"""Exact URL case is source identity; legacy source domains stay frozen."""
import copy
import hashlib,json,sqlite3
from contextlib import closing
import pytest
from deepfocus_api import quant_financial_store as base,quant_financial_fact_store as facts,quant_financial_read as archive
from deepfocus_api.tests.test_quant_financial_fact_derived import clock,ledger,setup,read
from deepfocus_api.tests.test_quant_financial_vintages import fixture
from deepfocus_api.tests.test_quant_financial_identity_upgrade import rename_fixture,mutate_owned,snapshot


def paths(manifest,sources,extension='pdf'):
    for row in manifest['vintages']:
        old=row['index_sha256'];index=json.loads(sources.pop(old));name=manifest['source_files'].pop(old)
        path=index['announcements'][0]['adjunctUrl'].rsplit('.',1)[0]+'.'+extension
        index['announcements'][0]['adjunctUrl']=path;raw=json.dumps(index).encode();sha=hashlib.sha256(raw).hexdigest();sources[sha]=raw;manifest['source_files'][sha]=name
        row.update(source_url='https://static.cninfo.com.cn/'+path,index_sha256=sha)
    return manifest,sources


@pytest.mark.parametrize('extension',['pdf','PDF'])
def test_new_exact_indexed_path_case_is_preserved_and_selectable(clock,extension):
    m,s=paths(*fixture(),extension);receipt=base.put('a','exact URL synthetic',m,s)
    result=base.review('a',receipt['id'],issuer_id='cninfo:9900002009',symbol='002109',period_end='2025-03-31',as_of='2026-10-03T00:00:00+08:00',mode='as_recorded',required_metrics=['parent_net_profit'])
    assert result['metrics']['parent_net_profit']=='-118.00'
    assert result['panel']['source_url']==m['vintages'][1]['source_url']
    assert result['source_url_version']=='cninfo-exact-indexed-PDF-or-pdf-v2'
    assert not result['promotion_allowed']


@pytest.mark.parametrize('changed',['mixed','query','fragment','wrong-id','wrong-date','wrong-host','http'])
def test_URL_domain_stays_exact_even_if_index_is_also_forged(clock,changed):
    m,s=paths(*fixture());r=m['vintages'][0]
    if changed=='mixed':r['source_url']=r['source_url'][:-3]+'PdF'
    if changed=='query':r['source_url']+='?download=1'
    if changed=='fragment':r['source_url']+='#x'
    if changed=='wrong-id':r['source_url']=r['source_url'].replace('/111.pdf','/999.pdf')
    if changed=='wrong-date':r['source_url']=r['source_url'].replace('2025-04-24','2025-04-23')
    if changed=='wrong-host':r['source_url']=r['source_url'].replace('static.cninfo.com.cn','example.invalid')
    if changed=='http':r['source_url']=r['source_url'].replace('https:','http:')
    if changed in {'mixed','query','fragment','wrong-id','wrong-date'}:
        old=r['index_sha256'];index=json.loads(s.pop(old));name=m['source_files'].pop(old)
        index['announcements'][0]['adjunctUrl']=r['source_url'].removeprefix('https://static.cninfo.com.cn/')
        raw=json.dumps(index).encode();sha=hashlib.sha256(raw).hexdigest();s[sha]=raw;m['source_files'][sha]=name;r['index_sha256']=sha
    with pytest.raises(ValueError):base.put('a','invalid exact path',m,s)


def test_cannot_uppercase_indexed_lower_path_as_a_canonical_alias(clock):
    m,s=paths(*fixture());m['vintages'][0]['source_url']=m['vintages'][0]['source_url'][:-3]+'PDF'
    with pytest.raises(ValueError,match='Indexed issuer'):base.put('a','incorrect alias',m,s)


@pytest.mark.parametrize('layer',['parent','typed'])
def test_pre_path_source_families_cannot_gain_lowercase_domain(clock,layer):
    m,s=paths(*fixture());parent=base.put('a','new lower source',m,s)
    if layer=='parent':
        mutate_owned(clock,'qr_financial_bundle',parent['id'],lambda v:v.update(source_pins=copy.deepcopy(base.KNOWN_V4_SOURCE_PINS)))
        with closing(sqlite3.connect(clock)) as c:
            c.row_factory=sqlite3.Row;row=c.execute('SELECT * FROM qr_financial_bundle WHERE id=?',(parent['id'],)).fetchone()
            with pytest.raises(ValueError,match='Legacy financial source requires uppercase'):base.verify_row(c,row)
    else:
        d=m['vintages'][0];packet=facts.put('a',parent['id'],'new lower fact',[dict(document_id=d['document_id'],source_sha256=d['source_sha256'],metric='consolidated_net_profit',column='current_period',basis='year_to_date',period_start=d['period_start'],period_end=d['period_end'],value='-130.00',source_page=2)])
        mutate_owned(clock,'qr_financial_fact_packet',packet['id'],lambda v:v.update(source_pins=copy.deepcopy(facts.KNOWN_V2_SOURCE_PINS)))
        with pytest.raises(ValueError,match='Legacy typed source requires uppercase'):archive.detail('a',packet['id'])


def test_pre_path_alpha_family_is_not_regressed_to_numeric_only(clock):
    m,s=rename_fixture('gfbj0835710');parent=base.put('a','alpha uppercase legacy fixture',m,s)
    old=mutate_owned(clock,'qr_financial_bundle',parent['id'],lambda v:v.update(source_pins=copy.deepcopy(base.KNOWN_V4_SOURCE_PINS)))
    d=m['vintages'][0];packet=facts.put('a',parent['id'],'alpha uppercase fact',[dict(document_id=d['document_id'],source_sha256=d['source_sha256'],metric='consolidated_net_profit',column='current_period',basis='year_to_date',period_start=d['period_start'],period_end=d['period_end'],value='-130.00',source_page=2)])
    mutate_owned(clock,'qr_financial_fact_packet',packet['id'],lambda v:v.update(source_pins=copy.deepcopy(facts.KNOWN_V2_SOURCE_PINS),parent_fingerprint=base.digest(old)))
    before=snapshot(clock);result=archive.detail('a',packet['id'])
    assert result['archive_source_status']=='known_v2_revalidated' and not result['archive_source_current']
    assert result['securities'][0]['issuer_id']=='cninfo:gfbj0835710' and snapshot(clock)==before


def test_active_lower_path_typed_TTM_through_full_core(clock,monkeypatch):
    original=base.put
    def insert(owner,label,m,s):return original(owner,label,*paths(m,s))
    monkeypatch.setattr(base,'put',insert)
    _,packet,_=setup();before=snapshot(clock);r=read(packet)
    assert r['ttm_value']=='-395.00' and r['endpoint_average_assets']['value_exact_yuan']=='105000.015'
    assert r['status']=='arithmetic_available' and r['result']['flow_components'][0]['selected_document']['document_id']=='222'
    assert snapshot(clock)==before


@pytest.mark.parametrize('kind',['parent','typed'])
def test_newly_known_family_hash_not_partial_or_forged_profile(clock,kind):
    parent,packet,_=setup();table='qr_financial_bundle' if kind=='parent' else 'qr_financial_fact_packet';identity=parent['id'] if kind=='parent' else packet['id']
    pins=base.KNOWN_V4_SOURCE_PINS if kind=='parent' else facts.KNOWN_V2_SOURCE_PINS
    def change(v):v.update(source_pins={**pins,'unknown.py':'0'*64},archive_source_status='known_v4_revalidated' if kind=='parent' else 'known_v2_revalidated')
    mutate_owned(clock,table,identity,change)
    with pytest.raises(ValueError):archive.detail('a',packet['id'])
