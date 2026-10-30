"""Synthetic benchmark references: no opening-price inference or calendar hiding."""
import hashlib,json
from urllib.parse import urlencode
import pytest
from deepfocus_api import quant_benchmark as benchmark

ASOF='2026-10-30T13:30:00+00:00'
CALENDAR=['2024-01-02','2024-01-03','2024-01-04','2024-01-05']


def fixture(code='H00300',rows=None,**capture_changes):
    if rows is None:
        rows=[{'tradeDate':d.replace('-',''),'indexCode':code,'indexNameCnAll':benchmark.VARIANTS[code]['name'],
              'close':100+i,'open':None,'high':None,'low':None} for i,d in enumerate(CALENDAR)]
    raw=json.dumps({'code':'200','data':rows}).encode()
    query=urlencode({'indexCode':code,'startDate':'20240102','endDate':'20240105'})
    capture={'index_code':code,'url':'https://www.csindex.com.cn/csindex-home/perf/index-perf?'+query,
             'sha256':hashlib.sha256(raw).hexdigest(),'observed_at':ASOF,**capture_changes}
    return capture,raw


def series():
    return {code:benchmark.parse_capture(*fixture(code),as_of=ASOF) for code in benchmark.VARIANTS}


def test_close_only_gross_index_keeps_null_open_and_explicit_tax_limitations():
    s=series();gross=s['H00300']
    assert not gross['all_ohlc_present'] and all(r['open'] is None for r in gross['rows'])
    assert not gross['tradable'] and not gross['historical_point_in_time_verified']
    assert s['N00300']['dividend_tax_assumption']=='10%'


def test_two_fixed_time_alignments_do_not_mix_start_capital_and_first_close():
    s=series();curve=[{'date':d,'nav':nav} for d,nav in zip(CALENDAR[1:],[990,1020,1040])]
    out=benchmark.reference_comparison(curve,1000,s,CALENDAR,starts_flat=True)
    assert out['strategy_original_start_capital_return_pct']==pytest.approx(4)
    assert out['strategy_observed_close_return_pct']==pytest.approx((1040/990-1)*100)
    r=out['references']['H00300']
    assert r['prior_close_to_final_close_pct']==pytest.approx(3)
    assert r['observed_close_to_close_pct']==pytest.approx((103/101-1)*100)
    assert out['observed_close_comparison_excludes_first_session_pnl']
    assert not r['investor_tax_matched'] and not out['original_gate_modified'] and not out['promotion_allowed']


@pytest.mark.parametrize('damage',['response_code','name','duplicate','order','negative','nan','bool','partial_ohlc','bad_ohlc','outside_date'])
def test_semantically_bad_provider_rows_are_not_used(damage):
    cap,raw=fixture();rows=json.loads(raw)['data']
    if damage=='response_code':rows[0]['indexCode']='000300'
    elif damage=='name':rows[0]['indexNameCnAll']='沪深300净收益指数'
    elif damage=='duplicate':rows.append(dict(rows[-1]))
    elif damage=='order':rows.reverse()
    elif damage in ('negative','nan','bool'):rows[0]['close']={'negative':-1,'nan':'NaN','bool':True}[damage]
    elif damage=='partial_ohlc':rows[0]['open']=100
    elif damage=='bad_ohlc':rows[0].update(open=100,high=99,low=98)
    else:rows[-1]['tradeDate']='20250101'
    with pytest.raises(ValueError):benchmark.parse_capture(*fixture(rows=rows),as_of=ASOF)


@pytest.mark.parametrize('damage',['hash','request','future_capture','unfinished_session','timezone'])
def test_evidence_identity_and_clock_are_enforced(damage):
    cap,raw=fixture()
    if damage=='hash':raw+=b' '
    elif damage=='request':cap['url']=cap['url'].replace('indexCode=H00300','indexCode=000300')
    elif damage=='future_capture':cap['observed_at']='2099-01-01T00:00:00+00:00'
    elif damage=='unfinished_session':cap['observed_at']='2024-01-05T08:29:59+00:00'
    else:cap['observed_at']='2024-01-05T20:00:00'
    with pytest.raises(ValueError):benchmark.parse_capture(cap,raw,as_of=ASOF)


def test_missing_dates_and_variants_refuse_comparison_instead_of_intersecting():
    s=series();s['H00300']['rows'].pop(1)
    with pytest.raises(ValueError,match='missing'):benchmark.align(s,CALENDAR)
    s=series();s.pop('N00300')
    with pytest.raises(ValueError,match='together'):benchmark.align(s,CALENDAR)


def test_extra_provider_dates_are_reported_not_appended_to_frozen_period():
    s=series();coverage=benchmark.align(s,CALENDAR[:-1])
    assert coverage['extra_fetched_sessions']==['2024-01-05']


@pytest.mark.parametrize('damage',['nonflat','gap','zero_nav','duplicate','no_anchor'])
def test_unsupported_strategy_alignment_is_rejected(damage):
    curve=[{'date':d,'nav':1000} for d in CALENDAR[1:]];flat=True
    if damage=='nonflat':flat=False
    elif damage=='gap':curve.pop(1)
    elif damage=='zero_nav':curve[0]['nav']=0
    elif damage=='duplicate':curve.append(dict(curve[-1]))
    else:curve=[{'date':CALENDAR[0],'nav':1000}]+curve
    with pytest.raises(ValueError):benchmark.reference_comparison(curve,1000,series(),CALENDAR,starts_flat=flat)


@pytest.mark.parametrize('damage',['hash','empty_scope','path','overwrite','future_protocol'])
def test_offline_cli_refuses_changed_or_empty_scope_and_preserves_prior_report(tmp_path,damage):
    from pathlib import Path
    import subprocess,sys
    from deepfocus_api.quant_research_engine import digest
    script=Path(__file__).resolve().parents[3]/'scripts/quant-benchmark-review.py';module=script.parents[1]/'backend/deepfocus_api/quant_benchmark.py'
    captures=[]
    for code in benchmark.VARIANTS:
        cap,raw=fixture(code);name=code+'.json';(tmp_path/name).write_bytes(raw);cap['file']=name;captures.append(cap)
    if damage=='path':captures[0]['file']='../outside.json'
    manifest={'schema':'csi-reference-captures-v1','captures':captures,'errors':[]}
    capture_path=tmp_path/'manifest.json';capture_path.write_text(json.dumps(manifest))
    experiments=tmp_path/'experiments.json';experiments.write_text('[]')
    controls=tmp_path/'controls.json';controls.write_text(json.dumps({'schema':'expanded-cash-event-controls-v1','registered_protocol':{},'protocol_digest':digest({}),'results':[]}))
    p={'schema':'benchmark-reference-review-v1','registered_at':'2099-01-01T00:00:00+00:00' if damage=='future_protocol' else '2026-10-30T00:00:00+00:00',
       'purpose':'diagnostic_no_gate_replacement','methods':['prior_close_cash_anchor','observed_close_to_close'],'promotion_allowed':False,
       'source_pins':{f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in (script,module)},'capture_manifest_digest':digest(manifest),
       'control_report_sha256':hashlib.sha256(controls.read_bytes()).hexdigest(),'experiments_sha256':hashlib.sha256(experiments.read_bytes()).hexdigest()}
    protocol=tmp_path/'protocol.json';protocol.write_text(json.dumps(p));output=tmp_path/'review.json'
    if damage=='hash':controls.write_text(controls.read_text()+' ')
    if damage=='overwrite':output.write_bytes(b'prior report retained')
    run=subprocess.run([sys.executable,str(script),'--captures',str(capture_path),'--experiments',str(experiments),'--controls',str(controls),
        '--protocol',str(protocol),'--output',str(output)],capture_output=True,text=True)
    assert run.returncode!=0,run.stdout+run.stderr
    if damage=='overwrite':assert output.read_bytes()==b'prior report retained'
    else:assert not output.exists()
