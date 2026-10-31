"""Source-bound compatibility; all mutation helpers only touch owned fixtures."""
import copy
from contextlib import closing
from datetime import datetime,timezone
import hashlib,json,sqlite3

import pytest
from httpx import AsyncClient,ASGITransport
from deepfocus_api import quant_financial_vintages as vintages,quant_financial_store as base
from deepfocus_api import quant_financial_fact_store as facts,quant_financial_fact_derived as derived
from deepfocus_api import quant_financial_read as archive,quant_recovery as recovery
from deepfocus_api.tests.test_quant_financial_fact_derived import clock,ledger,setup,read
from deepfocus_api.tests.test_quant_financial_vintages import fixture
from deepfocus_api.tests.test_quant_financial_api import app,params


def rename_fixture(issuer):
    m,s=fixture()
    for row in m['vintages']:
        old=row['index_sha256'];index=json.loads(s.pop(old));file=m['source_files'].pop(old)
        index['announcements'][0]['orgId']=issuer
        raw=json.dumps(index).encode();sha=hashlib.sha256(raw).hexdigest();s[sha]=raw;m['source_files'][sha]=file
        row.update(issuer_id='cninfo:'+issuer,index_sha256=sha)
    return m,s


@pytest.mark.parametrize('issuer',['gfbj0835710','nssc1000172','AbCdE12345','12345','Z'*40])
def test_new_ascii_orgs_exact_binding_and_case_preserved(clock,issuer):
    m,s=rename_fixture(issuer);receipt=base.put('a','explicit synthetic identity',m,s)
    value=base.review('a',receipt['id'],issuer_id='cninfo:'+issuer,symbol='002109',period_end='2025-03-31',as_of='2026-10-02T00:00:00+08:00',mode='as_recorded',required_metrics=['parent_net_profit'])
    assert value['status']=='available' and value['metrics']['parent_net_profit']=='-118.00'
    assert value['panel']['issuer_id']=='cninfo:'+issuer and value['issuer_identity_version']=='cninfo-ascii-org-v2'
    assert value['validation_version']=='exact-financial-magnitude-v2' and not value['promotion_allowed']
    if issuer.lower()!=issuer:
        other=base.review('a',receipt['id'],issuer_id='cninfo:'+issuer.lower(),symbol='002109',period_end='2025-03-31',as_of='2026-10-02T00:00:00+08:00',mode='as_recorded',required_metrics=['parent_net_profit'])
        assert other['status']=='unavailable'  # no case normalization or cross-issuer borrowing


@pytest.mark.parametrize('issuer',['abcd','x'*41,'a_bcd','a/bcd','a%20b','中文abc','１２３４５','abcde\n'])
def test_invalid_identity_domain_rejected_even_with_matching_index(clock,issuer):
    with pytest.raises(ValueError,match='issuer'):base.put('a','bad identity',*rename_fixture(issuer))


def test_alphanumeric_index_mismatch_still_rejected(clock):
    m,s=rename_fixture('gfbj0835710');m['vintages'][0]['issuer_id']='cninfo:GFBJ0835710'
    with pytest.raises(ValueError,match='Indexed issuer'):base.put('a','wrong case',m,s)


def mutate_owned(path,table,identity,mutate):
    with closing(sqlite3.connect(path)) as c:
        c.row_factory=sqlite3.Row;row=c.execute('SELECT * FROM '+table+' WHERE id=?',(identity,)).fetchone();v=json.loads(row['payload']);mutate(v)
        trigger=table+'_update';sql=c.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?",(trigger,)).fetchone()[0]
        c.execute('DROP TRIGGER '+trigger)
        c.execute('UPDATE '+table+' SET payload=?,fingerprint=?,input_hash=? WHERE id=?',(json.dumps(v),base.digest(v),base.digest(v['input']),identity));c.execute(sql);c.commit()
    return v


def old_packet(path):
    parent,packet,_=setup()
    parent_v=mutate_owned(path,'qr_financial_bundle',parent['id'],lambda v:v.update(source_pins=copy.deepcopy(base.KNOWN_V3_SOURCE_PINS)))
    mutate_owned(path,'qr_financial_fact_packet',packet['id'],lambda v:v.update(source_pins=copy.deepcopy(facts.KNOWN_V1_SOURCE_PINS),parent_fingerprint=base.digest(parent_v)))
    return parent,packet


def snapshot(path):
    with closing(sqlite3.connect(path)) as c:
        return {t:c.execute('SELECT * FROM '+t+' ORDER BY 1,2').fetchall() for t in ['qr_financial_bundle','qr_financial_source','qr_financial_fact_packet']}


def test_exact_old_parent_and_typed_packet_still_read_without_rewriting_and_cold_restore(clock,monkeypatch):
    parent,packet=old_packet(clock);before=snapshot(clock);v=read(packet)
    assert v['ttm_value']=='-395.00' and v['endpoint_average_assets']['value_exact_yuan']=='105000.015'
    assert v['receipt']['archive_source_status']=='known_v1_revalidated' and not v['receipt']['archive_source_current']
    assert archive.detail('a',packet['id'])['archive_source_status']=='known_v1_revalidated'
    assert base.source_profile(json.loads(before['qr_financial_bundle'][0][-1]))=='known_v3_revalidated'
    assert snapshot(clock)==before
    assert read(packet,as_of='2026-10-02T23:59:59+08:00')['result'] is None
    with pytest.raises(LookupError):archive.detail('b',packet['id'])
    proof=recovery.prepare(clock,clock.parent/'cold');assert proof['verified'] and not proof['activation_authorized']
    cold=clock.parent/'cold/research.sqlite3';assert snapshot(cold)==before and snapshot(clock)==before
    monkeypatch.setenv('DEEPFOCUS_QUANT_RESEARCH_DB_PATH',str(cold))
    assert read(packet)==v


@pytest.mark.parametrize('damage',['extra','missing','hash','profile-label'])
def test_unknown_typed_sources_rejected_even_if_row_hash_recomputed(clock,damage):
    _,packet=old_packet(clock)
    def change(v):
        if damage=='extra':v['source_pins']['unknown.py']='0'*64
        elif damage=='missing':v['source_pins'].pop('quant_financial_fact_store.py')
        elif damage=='hash':v['source_pins']['quant_financial_fact_store.py']='0'*64
        else:v['source_pins']['quant_financial_vintages.py']='0'*64;v['archive_source_status']='known_v1_revalidated'
    mutate_owned(clock,'qr_financial_fact_packet',packet['id'],change)
    with pytest.raises(ValueError):read(packet)
    with pytest.raises(ValueError):recovery.prepare(clock,clock.parent/'rejected')


@pytest.mark.parametrize('layer',['parent','typed'])
def test_old_source_claim_cannot_expand_legacy_identity_domain(clock,layer):
    m,s=rename_fixture('gfbj0835710');parent=base.put('a','new alpha synthetic',m,s)
    if layer=='parent':
        mutate_owned(clock,'qr_financial_bundle',parent['id'],lambda v:v.update(source_pins=copy.deepcopy(base.KNOWN_V3_SOURCE_PINS)))
        with closing(sqlite3.connect(clock)) as c:
            c.row_factory=sqlite3.Row;row=c.execute('SELECT * FROM qr_financial_bundle WHERE id=?',(parent['id'],)).fetchone()
            with pytest.raises(ValueError,match='Legacy financial'):base.verify_row(c,row)
    else:
        doc=m['vintages'][0];packet=facts.put('a',parent['id'],'synthetic fact',[dict(document_id=doc['document_id'],source_sha256=doc['source_sha256'],metric='consolidated_net_profit',column='current_period',basis='year_to_date',period_start=doc['period_start'],period_end=doc['period_end'],value='-130.00',source_page=2)])
        mutate_owned(clock,'qr_financial_fact_packet',packet['id'],lambda v:v.update(source_pins=copy.deepcopy(facts.KNOWN_V1_SOURCE_PINS)))
        with pytest.raises(ValueError,match='Legacy typed'):archive.detail('a',packet['id'])


@pytest.mark.asyncio
@pytest.mark.parametrize('route',['derive','profitability'])
async def test_HTTP_both_routes_preserve_alpha_org_and_reject_foreign_case(clock,route):
    m,s=rename_fixture('gfbj0835710');parent=base.put('a','alpha synthetic',m,s);doc=m['vintages'][0]
    packet=facts.put('a',parent['id'],'synthetic fact',[dict(document_id=doc['document_id'],source_sha256=doc['source_sha256'],metric='consolidated_net_profit',column='current_period',basis='year_to_date',period_start=doc['period_start'],period_end=doc['period_end'],value='-130.00',source_page=2)])
    args=params(issuer_id='cninfo:gfbj0835710')
    async with AsyncClient(transport=ASGITransport(app=app()),base_url='http://test') as client:
        url='/api/quant-research/financial-extractions/'+packet['id']+'/'+route
        response=await client.get(url,params=args);assert response.status_code==200,response.text
        value=response.json();under=value.get('inputs',value);assert under['request']['issuer_id']=='cninfo:gfbj0835710'
        assert value['status']=='unavailable'  # deliberately missing TTM/assets; not fake data coverage
        assert response.headers['cache-control']=='no-store'
        for invalid in ['cninfo:gfbj0835710\n','cninfo:中文abc','cninfo:a_bcd']:
            response=await client.get(url,params=params(issuer_id=invalid));assert response.status_code==422
