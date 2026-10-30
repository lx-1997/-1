"""Realistic original/corrected report knowledge clocks; no trading exemptions."""
import copy,hashlib,json
from datetime import datetime
from pathlib import Path
import subprocess,sys

import pytest
from deepfocus_api import quant_financial_vintages as vintage


def fixture():
    sources={};files={};rows=[]
    for revision,published,identity,profit in [(1,'2025-04-24','111','-95.00'),(2,'2026-04-30','222','-118.00')]:
        pdf=b'%PDF-1.7\nsynthetic-unit-test-not-a-filing-'+str(revision).encode();sha=hashlib.sha256(pdf).hexdigest();sources[sha]=pdf;files[sha]=identity+'.pdf'
        path='finalpage/'+published+'/'+identity+'.PDF'
        index=json.dumps({'announcements':[{'secCode':'002109','orgId':'9900002009','adjunctUrl':path,
            'announcementId':identity,'announcementTime':int(datetime.fromisoformat(published+'T00:00:00+08:00').timestamp()*1000)}]}).encode()
        indexsha=hashlib.sha256(index).hexdigest();sources[indexsha]=index;files[indexsha]=identity+'.json'
        rows.append(dict(issuer_id='cninfo:9900002009',symbol='002109',period_start='2025-01-01',period_end='2025-03-31',
            scope='consolidated',basis='year_to_date',currency='CNY',revision=revision,document_id=identity,published_date=published,
            observed_at='2026-09-01T12:00:00+08:00',recorded_at='2026-09-02T12:00:00+08:00',
            source_url='https://static.cninfo.com.cn/'+path,source_sha256=sha,index_sha256=indexsha,source_page=2 if revision==1 else 1,
            metrics={'revenue':'839.00','parent_net_profit':profit,'net_operating_cashflow':'23.00'},
            supersedes_document_id=None if revision==1 else '111'))
    return {'schema':vintage.VERSION,'coverage_kind':'reviewed_sample','coverage_verified':False,
            'usage':'internal_research','source_files':files,'vintages':rows},sources


def read(manifest,sources,clock='2025-06-01T09:30:00+08:00',mode='historical_reconstruction',**kwargs):
    return vintage.select_panel(manifest,sources,issuer_id='cninfo:9900002009',symbol='002109',
        period_end='2025-03-31',as_of=clock,mode=mode,required_metrics=['revenue','parent_net_profit','net_operating_cashflow'],**kwargs)


def test_reporting_period_is_not_publication_and_correction_cannot_backfill():
    m,s=fixture();before=copy.deepcopy(m)
    assert read(m,s,'2025-03-31T15:00:00+08:00')['status']=='unavailable'
    assert read(m,s,'2025-04-24T23:59:59+08:00')['status']=='unavailable'
    old=read(m,s,'2025-04-25T00:00:00+08:00');assert old['panel']['revision']==1 and old['metrics']['parent_net_profit']=='-95.00'
    assert read(m,s,'2026-04-30T23:59:59+08:00')['panel']['revision']==1
    new=read(m,s,'2026-05-01T00:00:00+08:00');assert new['panel']['revision']==2 and new['metrics']['parent_net_profit']=='-118.00'
    assert new['late_collected_historical_reconstruction'] and not new['historical_pit_feed_certified']
    assert m==before and not new['promotion_allowed'] and not new['commercial_ready']


def test_as_recorded_waits_for_raw_observation_and_normalized_recording():
    m,s=fixture()
    assert read(m,s,mode='as_recorded')['status']=='unavailable'
    assert read(m,s,'2026-09-01T18:00:00+08:00',mode='as_recorded')['status']=='unavailable'
    assert read(m,s,'2026-09-02T11:59:59+08:00',mode='as_recorded')['status']=='unavailable'
    assert read(m,s,'2026-09-02T12:00:00+08:00',mode='as_recorded')['panel']['revision']==2


def test_missing_revised_metric_stays_unknown_not_filled_from_original_or_zero():
    m,s=fixture();m['vintages'][1]['metrics']['parent_net_profit']=None
    result=read(m,s,'2026-05-02T09:30:00+08:00')
    assert result['status']=='incomplete' and result['metrics']['parent_net_profit'] is None
    assert result['missing_metrics']==['parent_net_profit'] and result['panel']['revision']==2
    assert read(m,s)['metrics']['parent_net_profit']=='-95.00'


def test_unordered_input_and_future_value_changes_do_not_rewrite_visible_old_values():
    m,s=fixture();old=read(m,s);m['vintages'].reverse()
    assert read(m,s)['metrics']==old['metrics']
    m['vintages'][0]['metrics']['parent_net_profit']='-900.00'
    assert read(m,s)['metrics']==old['metrics']
    result=read(m,s);result['metrics']['parent_net_profit']='0.00'
    assert read(m,s)['metrics']['parent_net_profit']=='-95.00'


@pytest.mark.parametrize('damage',['missing_original','duplicate_revision','wrong_link','same_day','symbol','issuer','period','unit','basis','scope','nan','float','fraction','exponent','future_record','record_before_observe','naive','source','index','latest_only','false_coverage'])
def test_corrupt_or_ambiguous_vintages_reject_instead_of_becoming_historical_truth(damage):
    m,s=fixture();r=m['vintages'][1]
    if damage=='missing_original':m['vintages'].pop(0)
    elif damage=='duplicate_revision':r['revision']=1
    elif damage=='wrong_link':r['supersedes_document_id']='another-panel'
    elif damage=='same_day':r['published_date']=m['vintages'][0]['published_date']
    elif damage=='symbol':r['symbol']='000333'
    elif damage=='issuer':r['issuer_id']='cninfo:9900000000'
    elif damage=='period':r['period_end']='2025-06-30'
    elif damage=='unit':r['currency']='CNY_10000'
    elif damage=='basis':r['basis']='single_quarter'
    elif damage=='scope':r['scope']='parent_only'
    elif damage=='nan':r['metrics']['parent_net_profit']='NaN'
    elif damage=='float':r['metrics']['parent_net_profit']=-118.
    elif damage=='fraction':r['metrics']['parent_net_profit']='-118.001'
    elif damage=='exponent':r['metrics']['parent_net_profit']='1e999999999999'
    elif damage=='future_record':r['recorded_at']='2099-01-01T00:00:00+08:00'
    elif damage=='record_before_observe':r['recorded_at']='2026-08-01T00:00:00+08:00'
    elif damage=='naive':r['observed_at']='2026-09-01T12:00:00'
    elif damage=='source':s[r['source_sha256']]+=b'changed'
    elif damage=='index':s[r['index_sha256']]=b'{}'
    elif damage=='latest_only':m={'report_date':'2025-03-31','net_income':-118}
    else:m['coverage_verified']=True
    with pytest.raises((ValueError,TypeError)):read(m,s)


@pytest.mark.parametrize('damage',['issuer','oversized_epoch'])
def test_rebound_invalid_index_still_rejects_with_recomputed_sha(damage):
    m,s=fixture();r=m['vintages'][1];raw=json.loads(s.pop(r['index_sha256']));m['source_files'].pop(r['index_sha256'])
    if damage=='issuer':raw['announcements'][0]['secCode']='600519'
    else:raw['announcements'][0]['announcementTime']=10**100
    body=json.dumps(raw).encode();sha=hashlib.sha256(body).hexdigest()
    s[sha]=body;m['source_files'][sha]='wrong-stock.json';r['index_sha256']=sha
    with pytest.raises(ValueError,match='Indexed issuer'):read(m,s)


def test_same_day_revisions_reject_even_when_index_and_urls_match():
    m,s=fixture();r=m['vintages'][1];r['published_date']=m['vintages'][0]['published_date']
    r['source_url']='https://static.cninfo.com.cn/finalpage/'+r['published_date']+'/'+r['document_id']+'.PDF'
    old=r['index_sha256'];data=json.loads(s.pop(old));m['source_files'].pop(old)
    item=data['announcements'][0];item['announcementTime']=int(datetime.fromisoformat(r['published_date']+'T00:00:00+08:00').timestamp()*1000)
    item['adjunctUrl']=r['source_url'].replace('https://static.cninfo.com.cn/','')
    body=json.dumps(data).encode();sha=hashlib.sha256(body).hexdigest();s[sha]=body;m['source_files'][sha]='same-day.json';r['index_sha256']=sha
    with pytest.raises(ValueError,match='Same-day ordering unknown'):read(m,s)


def test_future_knowledge_clock_cannot_project_current_values_as_future_facts():
    m,s=fixture()
    with pytest.raises(ValueError,match='Future knowledge'):read(m,s,'2099-01-01T00:00:00+08:00')


def test_loaded_source_identity_is_not_relabelled_with_newer_disk_bytes(monkeypatch):
    m,s=fixture();original=vintage.source_pins();monkeypatch.setattr(vintage,'_read_source_pins',lambda:{'changed':'source'})
    assert vintage.source_pins()==original
    with pytest.raises(ValueError,match='source identity changed'):read(m,s)


def test_other_issuer_or_period_never_falls_back_to_latest():
    m,s=fixture()
    result=vintage.select_panel(m,s,issuer_id='cninfo:9900002009',symbol='002109',period_end='2024-12-31',
        as_of='2026-05-02T09:30:00+08:00',mode='historical_reconstruction',required_metrics=['parent_net_profit'])
    assert result['status']=='unavailable' and result['panel'] is None


@pytest.mark.parametrize('damage',['escape','overwrite','valid'])
def test_operator_cli_is_offline_bound_and_does_not_overwrite_prior_review(tmp_path,damage):
    m,s=fixture()
    for sha,name in m['source_files'].items():(tmp_path/name).write_bytes(s[sha])
    if damage=='escape':m['source_files'][next(iter(s))]='../outside.pdf'
    manifest=tmp_path/'manifest.json';manifest.write_text(json.dumps(m));output=tmp_path/'result.json'
    if damage=='overwrite':output.write_text('preserve')
    script=Path(__file__).resolve().parents[3]/'scripts/quant-financial-vintage-review.py'
    args=[sys.executable,str(script),'--manifest',str(manifest),'--evidence-dir',str(tmp_path),'--output',str(output),
          '--issuer-id','cninfo:9900002009','--symbol','002109','--period-end','2025-03-31',
          '--as-of','2025-06-01T09:30:00+08:00','--mode','historical_reconstruction','--metrics','parent_net_profit']
    p=subprocess.run(args,capture_output=True,text=True)
    if damage=='valid':assert p.returncode==0 and json.loads(output.read_text())['panel']['revision']==1
    elif damage=='overwrite':assert p.returncode!=0 and output.read_text()=='preserve'
    else:assert p.returncode!=0 and not output.exists()
