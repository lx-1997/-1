"""Synthetic catalog fixtures; true vendor omissions retained in local artifacts."""
from dataclasses import replace
import hashlib,json
from urllib.parse import urlencode
import pytest
from deepfocus_api.quant_action_coverage import parse_catalog,reconcile
from deepfocus_api.quant_corporate_actions import CashDividend

ASOF='2026-10-30T12:00:00+00:00'
SOURCE=b'%PDF- synthetic private fixture'
EVENT=CashDividend('fixture-2024',1,'300750','2024-04-29','2024-04-30','2024-04-30',
                   '5.028','5.0147304','2024-04-23T23:59:59+08:00',ASOF,
                   'https://example.org/fixture.pdf',hashlib.sha256(SOURCE).hexdigest())


def capture(rows,*,page=1,pages=1,count=None):
    raw=json.dumps({'success':True,'code':0,'result':{'data':rows,'pages':pages,'count':len(rows) if count is None else count}}).encode()
    params={'reportName':'RPT_SHAREBONUS_DET','columns':'ALL','pageSize':'100','pageNumber':str(page),
            'sortColumns':'EX_DIVIDEND_DATE','sortTypes':'-1','filter':'(SECURITY_CODE="300750")'}
    sha=hashlib.sha256(raw).hexdigest()
    return {'symbol':'300750','page':page,'url':'https://datacenter-web.eastmoney.com/api/data/v1/get?'+urlencode(params),
            'observed_at':ASOF,'sha256':sha},{sha:raw}


def row(**kw):
    return dict(SECURITY_CODE='300750',SECUCODE='300750.SZ',REPORT_DATE='2023-12-31 00:00:00',
                ASSIGN_PROGRESS='实施分配',PRETAX_BONUS_RMB='50.28',BONUS_RATIO=None,IT_RATIO=None,
                EQUITY_RECORD_DATE='2024-04-29 00:00:00',EX_DIVIDEND_DATE='2024-04-30 00:00:00',
                NOTICE_DATE='2024-04-23 00:00:00',**kw)


def review(rows,events=None):
    cap,body=capture(rows)
    return reconcile([cap],body,[EVENT] if events is None else events,{EVENT.source_sha256:SOURCE},
                     {'300750':('2023-10-26','2026-10-30')},as_of=ASOF)


def test_matching_catalog_still_cannot_certify_economic_completeness_or_promote():
    r=review([row()]);assert r['status_counts']=={'matches_operator_filing_evidence':1}
    assert r['catalog_pagination_verified'] and not r['economic_event_coverage_verified']
    assert not r['promotion_allowed'] and not r['execution_events_generated'] and not r['oos_validation']


def test_regular_only_cash_and_missing_special_dividend_are_reported_not_overwritten():
    normal=row();normal['PRETAX_BONUS_RMB']='20.11'
    special=replace(EVENT,event_id='fixture-special',record_date='2025-01-23',ex_date='2025-01-24',pay_date='2025-01-24',cash_per_share_gross='1.23',price_adjustment_per_share='1.2255331')
    r=review([normal],[EVENT,special]);assert r['status_counts']=={'cash_amount_conflict':1}
    assert r['filing_events_absent_from_catalog'][0]['event_id']=='fixture-special'


@pytest.mark.parametrize('kind',['no_filing','noncash','unknown_cash','wrong_date','ambiguous','proposal'])
def test_non_verified_paths_remain_explicit(kind):
    value=row();events=[EVENT]
    expected={'no_filing':'filing_missing','noncash':'noncash_action_unsupported','unknown_cash':'cash_amount_unknown',
              'wrong_date':'record_date_conflict','ambiguous':'ambiguous_same_day_events_requires_manual_review',
              'proposal':'not_implemented_or_unknown_status'}[kind]
    if kind=='no_filing':events=[]
    elif kind=='noncash':value['IT_RATIO']='1'
    elif kind=='unknown_cash':value['PRETAX_BONUS_RMB']=None
    elif kind=='wrong_date':value['EQUITY_RECORD_DATE']='2024-04-28'
    elif kind=='ambiguous':events.append(replace(EVENT,event_id='fixture-second'))
    else:value['ASSIGN_PROGRESS']='董事会预案'
    assert review([value],events)['outcomes'][0]['status']==expected


@pytest.mark.parametrize('damage',['wrong_request','wrong_row','hash','incomplete','duplicate_page','count','duplicate_row','future_clock','negative','bool','nan'])
def test_invalid_captures_refused_instead_of_being_treated_as_empty_history(damage):
    value=row()
    if damage=='wrong_row':value['SECURITY_CODE']='600519'
    if damage in ('negative','bool','nan'):value['PRETAX_BONUS_RMB']={'negative':-1,'bool':True,'nan':'NaN'}[damage]
    cap,body=capture([value,value] if damage=='duplicate_row' else [value],pages=2 if damage=='incomplete' else 1,count=101 if damage=='incomplete' else (2 if damage=='count' else None))
    caps=[cap]
    if damage=='wrong_request':cap['url']=cap['url'].replace('RPT_SHAREBONUS_DET','OTHER')
    elif damage=='hash':body[cap['sha256']]=b'{}'
    elif damage=='duplicate_page':caps.append(dict(cap))
    elif damage=='future_clock':cap['observed_at']='2026-10-31T12:00:00+00:00'
    with pytest.raises(ValueError):parse_catalog(caps,body,as_of=ASOF)


def test_complete_empty_catalog_does_not_erase_filing_event():
    r=review([]);assert not r['outcomes']
    assert r['filing_events_absent_from_catalog'] and not r['economic_event_coverage_verified']


def test_unknown_dates_retained_and_outside_interval_not_counted_as_covered():
    pending=row();pending['EX_DIVIDEND_DATE']=None
    old=row();old.update(REPORT_DATE='2022-12-31',EX_DIVIDEND_DATE='2023-04-26')
    r=review([pending,old]);assert len(r['unresolved_date_rows'])==1 and r['outside_interval_rows']==1
    assert not r['in_range_catalog_rows']


def test_missing_source_or_declared_security_refuses_review():
    cap,body=capture([row()])
    with pytest.raises(ValueError,match='原始公告'):reconcile([cap],body,[EVENT],{}, {'300750':('2023-10-26','2026-10-30')},as_of=ASOF)
    with pytest.raises(ValueError,match='exactly'):reconcile([cap],body,[EVENT],{EVENT.source_sha256:SOURCE}, {'600519':('2023-10-26','2026-10-30')},as_of=ASOF)


def test_offline_cli_binds_sealed_inputs_keeps_reports_and_refuses_path_escape(tmp_path):
    from pathlib import Path
    import subprocess,sys
    from dataclasses import asdict
    from deepfocus_api.quant_research_engine import digest
    cap,body=capture([row()]);cap['body_file']='raw.json'
    (tmp_path/'raw.json').write_bytes(body[cap['sha256']]);(tmp_path/'source.pdf').write_bytes(SOURCE)
    manifest=tmp_path/'captures.json';manifest.write_text(json.dumps({'schema':'action-catalog-captures-v1','errors':[],'captures':[cap]}))
    evidence=tmp_path/'events.json';evidence.write_text(json.dumps({'events':[asdict(EVENT)],'source_files':{EVENT.source_sha256:'source.pdf'}}))
    dataset={};result={'dataset_digest':digest(dataset)}
    exp=tmp_path/'experiments.json';exp.write_text(json.dumps([{'request':{'symbols':['300750'],'start_date':'2023-10-26','end_date':'2026-10-30'},'dataset':dataset,'result':result,'result_digest':digest(result)}]))
    output=tmp_path/'report.json';script=Path(__file__).resolve().parents[3]/'scripts/quant-action-coverage.py'
    args=[sys.executable,str(script),'--captures',str(manifest),'--experiments',str(exp),'--events',str(evidence),'--output',str(output)]
    first=subprocess.run(args,capture_output=True,text=True);assert first.returncode==0,first.stdout+first.stderr
    original=output.read_bytes();assert not json.loads(original)['promotion_allowed']
    assert subprocess.run(args,capture_output=True).returncode!=0 and output.read_bytes()==original
    cap['body_file']='../outside.json';manifest.write_text(json.dumps({'schema':'action-catalog-captures-v1','errors':[],'captures':[cap]}))
    args[-1]=str(tmp_path/'refused.json')
    assert subprocess.run(args,capture_output=True).returncode!=0 and not Path(args[-1]).exists()
