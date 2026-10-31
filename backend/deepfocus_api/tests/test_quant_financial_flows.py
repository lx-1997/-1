"""Exact period arithmetic with owned synthetic filing/index fixtures, no orders."""
import copy
from decimal import localcontext
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
from deepfocus_api import quant_financial_flows as flows

METRICS=['revenue','parent_net_profit','net_operating_cashflow']


def fixture(quarter='03-31'):
    sources={};files={};rows=[]
    pub={'03-31':'04-24','06-30':'08-01','09-30':'11-01'}[quarter]
    panels=[('2025-'+quarter,'2025-'+pub,1,'111',None,['839.00','-95.00','23.00']),
            ('2025-'+quarter,'2026-04-30',2,'222','111',['839.00','-118.00','23.00']),
            ('2024-12-31','2025-03-10',1,'333',None,['1000.00','100.00','150.00']),
            ('2024-'+quarter,'2024-'+pub,1,'444',None,['300.00','15.00','20.00'])]
    for end,published,revision,doc,prior,values in panels:
        pdf=b'%PDF-UNIT-FIXTURE-NOT-A-FILING-'+doc.encode();sha=hashlib.sha256(pdf).hexdigest()
        path='finalpage/'+published+'/'+doc+'.PDF'
        index=json.dumps({'announcements':[{'secCode':'002109','orgId':'9900002009','adjunctUrl':path,
            'announcementId':doc,'announcementTime':int(datetime.fromisoformat(published+'T00:00:00+08:00').timestamp()*1000)}]}).encode()
        indexsha=hashlib.sha256(index).hexdigest();sources.update({sha:pdf,indexsha:index});files.update({sha:doc+'.pdf',indexsha:doc+'.json'})
        rows.append(dict(issuer_id='cninfo:9900002009',symbol='002109',period_start=end[:4]+'-01-01',period_end=end,
            scope='consolidated',basis='year_to_date',currency='CNY',revision=revision,document_id=doc,published_date=published,
            observed_at='2026-09-01T12:00:00+08:00',recorded_at='2026-09-02T12:00:00+08:00',
            source_url='https://static.cninfo.com.cn/'+path,source_sha256=sha,index_sha256=indexsha,source_page=1,
            metrics=dict(zip(METRICS,values)),supersedes_document_id=prior))
    return dict(schema='reviewed-financial-vintages-v1',coverage_kind='reviewed_sample',coverage_verified=False,
                usage='internal_research',source_files=files,vintages=rows),sources


def derive(m,s,**kw):
    args=dict(issuer_id='cninfo:9900002009',symbol='002109',period_end='2025-03-31',as_of='2025-12-01T09:30:00+08:00',
        mode='historical_reconstruction',required_metrics=METRICS)
    args.update(kw);return flows.derive_ttm(m,s,**args)


@pytest.mark.parametrize('quarter,start',[('03-31','2024-04-01'),('06-30','2024-07-01'),('09-30','2024-10-01')])
def test_exact_three_component_ttm_and_calendar_window(quarter,start):
    m,s=fixture(quarter);r=derive(m,s,period_end='2025-'+quarter)
    assert r['status']=='arithmetic_available' and r['period_start']==start
    assert r['metrics']==dict(zip(METRICS,['1539.00','-10.00','153.00']))
    assert [x['coefficient'] for x in r['components']]==[1,1,-1]
    assert {x['selection']['as_of'] for x in r['components']}=={r['as_of']}
    assert not r['usable_for_strategy'] and not r['accounting_comparability_verified'] and not r['promotion_allowed']


def test_annual_is_one_exact_panel_not_a_three_panel_double_count():
    m,s=fixture();r=derive(m,s,period_end='2024-12-31')
    assert len(r['components'])==1 and r['period_start']=='2024-01-01'
    assert r['metrics']==dict(zip(METRICS,['1000.00','100.00','150.00']))


def test_report_period_and_publication_day_not_known_early():
    m,s=fixture()
    for clock in ('2025-03-31T23:59:59+08:00','2025-04-24T23:59:59+08:00'):
        r=derive(m,s,as_of=clock);assert r['status']=='unavailable' and r['metrics'] is None
    assert derive(m,s,as_of='2025-04-25T00:00:00+08:00')['metrics']['parent_net_profit']=='-10.00'


def test_current_correction_changes_only_when_known_and_all_components_stay_auditable():
    m,s=fixture();old=derive(m,s);new=derive(m,s,as_of='2026-05-01T00:00:00+08:00')
    assert old['metrics']['parent_net_profit']=='-10.00' and new['metrics']['parent_net_profit']=='-33.00'
    assert new['components'][0]['selection']['panel']['document_id']=='222'
    assert old['components'][0]['selection']['panel']['document_id']=='111'
    m['vintages'][1]['metrics']['parent_net_profit']='999999.00'
    assert derive(m,s)['metrics']==old['metrics']


@pytest.mark.parametrize('drop',[0,2,3])
def test_missing_component_never_becomes_zero_or_falls_back_to_annual(drop):
    m,s=fixture()
    m['vintages']=[r for r in m['vintages'] if r['period_end']!=m['vintages'][drop]['period_end']]
    r=derive(m,s);assert r['status']=='unavailable' and r['metrics'] is None and r['missing_components']


def test_revised_missing_metric_never_fills_from_older_revision():
    m,s=fixture();m['vintages'][1]['metrics']['net_operating_cashflow']=None
    r=derive(m,s,as_of='2026-05-01T00:00:00+08:00')
    assert r['status']=='incomplete' and r['metrics'] is None
    assert r['missing_components'][0]['missing_metrics']==['net_operating_cashflow']
    assert derive(m,s)['metrics']['net_operating_cashflow']=='153.00'


def test_recorded_mode_waits_for_all_components_not_just_current_report():
    m,s=fixture();m['vintages'][3]['recorded_at']='2026-09-03T12:00:00+08:00'
    assert derive(m,s,mode='as_recorded',as_of='2026-09-03T11:59:59+08:00')['metrics'] is None
    assert derive(m,s,mode='as_recorded',as_of='2026-09-03T12:00:00+08:00')['metrics']['parent_net_profit']=='-33.00'


def test_input_order_output_mutation_and_ambient_decimal_context_do_not_change_arithmetic():
    m,s=fixture();before=copy.deepcopy(m);expected=derive(m,s);m['vintages'].reverse()
    with localcontext() as ctx:
        ctx.prec=2
        for key in ctx.traps:ctx.traps[key]=True
        r=derive(m,s)
        assert ctx.prec==2
    assert r['metrics']==expected['metrics']
    r['metrics']['revenue']='0.00';r['components'][0]['selection']['panel']['metrics']['revenue']='0.00'
    assert derive(m,s)['metrics']==expected['metrics'];m['vintages'].reverse();assert m==before


def test_large_signed_cent_arithmetic_and_zero_are_exact_under_small_decimal_context():
    m,s=fixture()
    for row in m['vintages']:row['metrics'].update(revenue='999999999999999999.99',parent_net_profit='0.00')
    m['vintages'][3]['metrics']['revenue']='-999999999999999999.99'
    with localcontext() as ctx:
        ctx.prec=3;r=derive(m,s)
    assert r['metrics']['revenue']=='2999999999999999999.97' and r['metrics']['parent_net_profit']=='0.00'


@pytest.mark.parametrize('end',['2025-02-28','2025-03-30','2025-04-01','2025-3-31'])
def test_nonquarter_end_is_refused(end):
    m,s=fixture()
    with pytest.raises(ValueError):derive(m,s,period_end=end)


@pytest.mark.parametrize('damage',['unit','scope','basis','float','future','source','latest_snapshot'])
def test_wrong_basis_or_evidence_is_refused(damage):
    m,s=fixture()
    if damage=='unit':m['vintages'][2]['currency']='CNY_10000'
    elif damage=='scope':m['vintages'][2]['scope']='parent_only'
    elif damage=='basis':m['vintages'][2]['basis']='single_quarter'
    elif damage=='float':m['vintages'][2]['metrics']['revenue']=1000.
    elif damage=='source':s[m['vintages'][2]['source_sha256']]+=b'changed'
    elif damage=='latest_snapshot':m={'report_date':'2025-03-31','revenue':839}
    else:
        with pytest.raises(ValueError):derive(m,s,as_of='2099-01-01T00:00:00+08:00')
        return
    with pytest.raises((ValueError,KeyError,TypeError)):derive(m,s)


def test_source_drift_refused(monkeypatch):
    m,s=fixture();old=flows._pins
    monkeypatch.setattr(flows,'_pins',lambda:{**old(),'quant_financial_flows.py':'0'*64})
    with pytest.raises(ValueError,match='source changed'):derive(m,s)


def test_cli_retains_incomplete_report_exit_two_and_refuses_overwrite(tmp_path):
    m,s=fixture();m['vintages']=[r for r in m['vintages'] if r['period_end']=='2025-03-31']
    manifest=tmp_path/'manifest.json';manifest.write_text(json.dumps(m));folder=tmp_path/'evidence';folder.mkdir()
    for sha,name in m['source_files'].items():(folder/name).write_bytes(s[sha])
    output=tmp_path/'report.json';script=Path(__file__).resolve().parents[3]/'scripts/quant-financial-flow-review.py'
    cmd=[sys.executable,str(script),'--manifest',str(manifest),'--evidence-dir',str(folder),'--output',str(output),
        '--issuer-id','cninfo:9900002009','--symbol','002109','--period-end','2025-03-31','--as-of','2025-12-01T09:30:00+08:00',
        '--mode','historical_reconstruction','--metrics',*METRICS]
    r=subprocess.run(cmd,capture_output=True,text=True);assert r.returncode==2,r.stdout+r.stderr
    value=json.loads(output.read_text());assert value['metrics'] is None and value['status']=='unavailable'
    before=output.read_bytes();assert subprocess.run(cmd,capture_output=True).returncode!=0;assert output.read_bytes()==before



def append_revision(m,s,index,doc,values):
    row=copy.deepcopy(m['vintages'][index]);old=row['document_id'];row.update(document_id=doc,revision=row['revision']+1,
        supersedes_document_id=old,published_date='2026-05-15',metrics={**row['metrics'],**values})
    row['source_url']='https://static.cninfo.com.cn/finalpage/2026-05-15/'+doc+'.PDF'
    pdf=b'%PDF-UNIT-REVISION-'+doc.encode();sha=hashlib.sha256(pdf).hexdigest()
    raw=json.dumps({'announcements':[{'secCode':'002109','orgId':'9900002009',
        'adjunctUrl':'finalpage/2026-05-15/'+doc+'.PDF','announcementId':doc,
        'announcementTime':int(datetime.fromisoformat('2026-05-15T00:00:00+08:00').timestamp()*1000)}]}).encode()
    indexsha=hashlib.sha256(raw).hexdigest();row.update(source_sha256=sha,index_sha256=indexsha)
    s.update({sha:pdf,indexsha:raw});m['source_files'].update({sha:doc+'.pdf',indexsha:doc+'.json'});m['vintages'].append(row)


@pytest.mark.parametrize('index,expected',[(2,'103.00'),(3,'73.00')])
def test_prior_annual_and_comparable_revisions_obey_same_knowledge_clock(index,expected):
    m,s=fixture();append_revision(m,s,index,'555',{'net_operating_cashflow':'100.00'})
    old=derive(m,s,as_of='2026-05-15T23:59:59+08:00')
    new=derive(m,s,as_of='2026-05-16T00:00:00+08:00')
    assert old['metrics']['net_operating_cashflow']=='153.00' and new['metrics']['net_operating_cashflow']==expected
    assert new['components'][1 if index==2 else 2]['selection']['panel']['document_id']=='555'


def test_missing_revised_annual_cannot_use_previous_complete_annual():
    m,s=fixture();append_revision(m,s,2,'555',{'net_operating_cashflow':None})
    r=derive(m,s,as_of='2026-05-16T00:00:00+08:00')
    assert r['status']=='incomplete' and r['metrics'] is None
    assert r['missing_components'][0]['role']=='previous_annual'


def test_overbound_source_amount_refused_even_with_low_ambient_precision():
    m,s=fixture();m['vintages'][2]['metrics']['revenue']='1000000000000000000.01'
    with localcontext() as ctx:
        ctx.prec=3
        with pytest.raises(ValueError):derive(m,s)


def test_cli_refuses_mutated_input_after_calculation(tmp_path,monkeypatch):
    import importlib.util
    m,s=fixture();path=tmp_path/'manifest.json';path.write_text(json.dumps(m));folder=tmp_path/'evidence';folder.mkdir()
    for sha,name in m['source_files'].items():(folder/name).write_bytes(s[sha])
    out=tmp_path/'report.json';script=Path(__file__).resolve().parents[3]/'scripts/quant-financial-flow-review.py'
    spec=importlib.util.spec_from_file_location('flow_cli',script);cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    original=cli.derive_ttm
    def changed(*args,**kwargs):
        result=original(*args,**kwargs);path.write_bytes(path.read_bytes()+b' ');return result
    monkeypatch.setattr(cli,'derive_ttm',changed)
    monkeypatch.setattr(sys,'argv',[str(script),'--manifest',str(path),'--evidence-dir',str(folder),'--output',str(out),
        '--issuer-id','cninfo:9900002009','--symbol','002109','--period-end','2025-03-31','--as-of','2025-12-01T09:30:00+08:00',
        '--mode','historical_reconstruction','--metrics',*METRICS])
    assert cli.main()==2 and not out.exists()
