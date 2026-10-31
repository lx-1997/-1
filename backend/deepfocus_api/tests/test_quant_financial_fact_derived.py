"""Actual owned extraction reads: one packet, one clock, exact endpoint means."""
import copy,sqlite3,json,subprocess,sys
from datetime import datetime
from decimal import localcontext,Inexact,Rounded
from pathlib import Path
import pytest
from deepfocus_api import quant_financial_fact_derived as derived,quant_financial_fact_store as facts,quant_financial_store as base
from deepfocus_api.tests.test_quant_financial_store import ledger
from deepfocus_api.tests.test_quant_financial_flows import fixture


@pytest.fixture
def clock(ledger,monkeypatch):
    monkeypatch.setattr(facts,'_now',lambda:datetime.fromisoformat('2026-10-02T08:00:00+00:00'))
    return ledger


def endpoints(q='03-31'):
    return {'beginning':{'report_period_end':'2024-'+q,'column':'current_period'},
            'ending':{'report_period_end':'2025-'+q,'column':'current_period'}}


def setup(q='03-31',change=None):
    m,s=fixture(q);parent=base.put('a','parent',m,s);docs={r['document_id']:r for r in m['vintages']};fs=[]
    for doc,value in [('111','-130.00'),('222','-175.00'),('333','-370.00'),('444','-150.00')]:
        r=docs[doc];fs.append(dict(document_id=doc,source_sha256=r['source_sha256'],metric='consolidated_net_profit',column='current_period',basis='year_to_date',period_start=r['period_start'],period_end=r['period_end'],value=value,source_page=3))
    for doc,col,end,value in [('444','current_period','2024-'+q,'120000.01'),('111','current_period','2025-'+q,'100000.00'),('222','current_period','2025-'+q,'90000.02'),('444','previous_year_end_adjusted','2023-12-31','130000.03'),('333','current_period','2024-12-31','110000.00')]:
        fs.append(dict(document_id=doc,source_sha256=docs[doc]['source_sha256'],metric='total_assets',column=col,basis='point_in_time_stock',period_start=None,period_end=end,value=value,source_page=2))
    if change:change(fs)
    packet=facts.put('a',parent['id'],'typed fixture',fs)
    return parent,packet,fs


def read(packet,**kwargs):
    args=dict(issuer_id='cninfo:9900002009',symbol='002109',period_end='2025-03-31',metric='consolidated_net_profit',
              as_of='2026-10-03T00:00:00+08:00',mode='as_recorded',asset_endpoints=endpoints())
    args.update(kwargs);return derived.review('a',packet['id'],**args)


@pytest.mark.parametrize('q,start',[('03-31','2024-04-01'),('06-30','2024-07-01'),('09-30','2024-10-01')])
def test_three_quarters_use_exact_same_packet_and_clock(clock,q,start):
    _,p,_=setup(q);r=read(p,period_end='2025-'+q,asset_endpoints=endpoints(q),as_of='2025-12-01T00:00:00+08:00',mode='historical_reconstruction')
    assert r['status']=='arithmetic_available' and r['period_start']==start and r['ttm_value']=='-350.00'
    assert [n['coefficient'] for n in r['result']['flow_components']]==[1,1,-1]
    assert r['endpoint_average_assets']['value_exact_yuan']=='110000.005'
    assert r['endpoint_average_assets']['fraction_yuan']=={'numerator':'22000001','denominator':'200'}
    assert all(n['as_of']==r['request']['as_of'] and n['mode']==r['request']['mode'] for n in r['result']['flow_components']+r['result']['asset_components'])
    assert not r['usable_for_strategy'] and not r['ratios_or_scores_generated']


def test_annual_one_panel_and_explicit_adjusted_beginning_column(clock):
    _,p,_=setup();e={'beginning':{'report_period_end':'2024-03-31','column':'previous_year_end_adjusted'},'ending':{'report_period_end':'2024-12-31','column':'current_period'}}
    r=read(p,period_end='2024-12-31',asset_endpoints=e)
    assert r['ttm_value']=='-370.00' and len(r['result']['flow_components'])==1
    assert r['endpoint_average_assets']['value_exact_yuan']=='120000.015' and r['period_start']=='2024-01-01'


def test_correction_visible_next_day_does_not_rewrite_older_derived_view(clock):
    _,p,_=setup()
    for at,expected,mean in [('2026-04-30T23:59:59+08:00','-350.00','110000.005'),('2026-05-01T00:00:00+08:00','-395.00','105000.015')]:
        r=read(p,as_of=at,mode='historical_reconstruction');assert r['ttm_value']==expected
        assert r['endpoint_average_assets']['value_exact_yuan']==mean


def test_gate_blocks_entire_nested_result_and_does_not_compute(clock,monkeypatch):
    _,p,_=setup()
    def forbidden(*a):raise AssertionError('no derivation before both storage gates')
    monkeypatch.setattr(derived,'_compute',forbidden)
    r=read(p,as_of='2026-10-02T23:59:59+08:00')
    assert r['ttm_value'] is None and r['endpoint_average_assets'] is None and r['result'] is None
    assert r['status']=='unavailable' and r['availability_reason']=='receipt_not_yet_visible'


def test_visibility_equality_is_not_blocked(clock):
    _,p,_=setup();assert read(p)['ttm_value']=='-395.00'


@pytest.mark.parametrize('damage',['new_flow_missing','new_flow_blank','new_assets_missing','new_assets_blank','prior_annual_missing'])
def test_missing_or_blank_revised_components_do_not_fill_or_partially_derive(clock,damage):
    def mutate(fs):
        target=next(f for f in fs if f['document_id']==('333' if damage=='prior_annual_missing' else '222') and f['metric']==('total_assets' if 'assets' in damage else 'consolidated_net_profit'))
        if 'blank' in damage:target['value']=None
        else:fs.remove(target)
    _,p,_=setup(change=mutate);r=read(p)
    assert r['status']=='unavailable' and r['ttm_value'] is None and r['endpoint_average_assets'] is None
    assert r['result']['missing_components']


def test_old_packet_does_not_borrow_new_packet_assets_and_withdrawal_does_not_borrow_old_flow(clock):
    parent,p,fs=setup();onlyflows=[f for f in fs if f['metric']!='total_assets']
    withdrawn=[f for f in fs if f['document_id']!='222']
    notes=[{**{k:f[k] for k in ('document_id','metric','column')},'reason':'Explicitly withdraw unverified synthetic source extraction'} for f in fs if f['document_id']=='222']
    new=facts.put('a',parent['id'],'withdrawn',withdrawn,supersedes_packet_id=p['id'],correction_notes=notes)
    assert read(new)['ttm_value'] is None and read(p)['ttm_value']=='-395.00'
    # Different complete parent scope, not a cross-packet union.
    other=base.put('a','second parent',*fixture());minimal=facts.put('a',other['id'],'only flow',onlyflows)
    assert read(minimal)['ttm_value'] is None
    assert read(minimal,asset_endpoints=None)['ttm_value']=='-395.00'


@pytest.mark.parametrize('kw',[{'metric':'total_assets'},{'period_end':'2025-02-28'},{'asset_endpoints':{}},
 {'asset_endpoints':{'beginning':{'report_period_end':'2024-12-31','column':'current_period'},'ending':{'report_period_end':'2025-03-31','column':'current_period'}}},
 {'asset_endpoints':{'beginning':{'report_period_end':'2024-03-31','column':'current_period'},'ending':{'report_period_end':'2025-03-31','column':'previous_year_end'}}},
 {'as_of':'2099-01-01T00:00:00+08:00'},{'mode':'latest'},{'issuer_id':'BAD'}])
def test_invalid_requests_rejected_even_before_visibility(clock,kw):
    _,p,_=setup()
    with pytest.raises((ValueError,TypeError)):read(p,**kw)


def test_zero_endpoint_mean_never_divides_or_generates_ratio(clock):
    def mutate(fs):
        for f in fs:
            if f['metric']=='total_assets':f['value']='0.00'
    _,p,_=setup(change=mutate);r=read(p)
    assert r['endpoint_average_assets']['fraction_yuan']=={'numerator':'0','denominator':'1'}
    assert not r['endpoint_average_assets']['positive_for_possible_future_denominator'] and not r['ratios_or_scores_generated']


def test_ambient_decimal_traps_do_not_round_half_cents(clock):
    _,p,_=setup()
    with localcontext() as c:
        c.prec=1;c.traps[Inexact]=True;c.traps[Rounded]=True
        r=read(p);assert r['ttm_value']=='-395.00' and r['endpoint_average_assets']['value_exact_yuan']=='105000.015'


def test_one_readonly_transaction_close_and_source_guard(clock,monkeypatch):
    _,p,_=setup();real=sqlite3.connect;connections=[];trace=[]
    def tracked(*args,**kwargs):
        assert 'mode=ro' in args[0] and kwargs['uri'];c=real(*args,**kwargs);connections.append(c);c.set_trace_callback(trace.append);return c
    monkeypatch.setattr(derived.sqlite3,'connect',tracked)
    assert read(p)['ttm_value']=='-395.00' and len(connections)==1
    assert all(x.lstrip().upper().startswith(('SELECT','BEGIN','PRAGMA QUERY_ONLY')) for x in trace)
    with pytest.raises(sqlite3.ProgrammingError):connections[0].execute('SELECT 1')
    monkeypatch.setattr(derived,'_pins',lambda:{})
    with pytest.raises(ValueError,match='source changed'):read(p)


def test_private_request_snapshot_survives_caller_mutation(clock,monkeypatch):
    _,p,_=setup();e=endpoints();before=copy.deepcopy(e);real=facts.verify_row
    def mutate(c,row,cache=None):
        result=real(c,row,cache);e['ending']['column']='previous_year_end';return result
    monkeypatch.setattr(facts,'verify_row',mutate)
    r=read(p,asset_endpoints=e);assert r['request']['asset_endpoints']==before and r['ttm_value']=='-395.00'


def test_wal_snapshot_uses_verified_sources_then_next_read_rejects_corruption(clock,monkeypatch):
    _,p,_=setup();real=facts.verify_row
    def tamper(c,row,cache=None):
        value=real(c,row,cache);sha=value['input']['facts'][0]['source_sha256'];writer=sqlite3.connect(clock)
        try:
            writer.execute('drop trigger qr_financial_source_update');writer.execute('update qr_financial_source set body=? where owner=? and sha=?',(b'bad','a',sha));writer.commit()
        finally:writer.close()
        return value
    monkeypatch.setattr(facts,'verify_row',tamper)
    assert read(p)['ttm_value']=='-395.00'
    monkeypatch.setattr(facts,'verify_row',real)
    with pytest.raises(ValueError):read(p)


def test_foreign_owner_rejected(clock):
    _,p,_=setup()
    with pytest.raises(LookupError):derived.review('b',p['id'],issuer_id='cninfo:9900002009',symbol='002109',period_end='2025-03-31',metric='consolidated_net_profit',as_of='2026-10-03T00:00:00+08:00',mode='as_recorded')


def test_cli_returns_available_arithmetic_and_blocked_exit_two(clock):
    _,p,_=setup();script=Path(__file__).resolve().parents[3]/'scripts/quant-financial-facts.py'
    cmd=[sys.executable,str(script),'--db',str(clock),'--owner','a','ttm-assets','--id',p['id'],'--issuer-id','cninfo:9900002009','--symbol','002109','--period-end','2025-03-31','--metric','consolidated_net_profit','--as-of','2026-10-03T00:00:00+08:00','--mode','as_recorded','--begin-report-period-end','2024-03-31','--begin-column','current_period','--end-report-period-end','2025-03-31','--end-column','current_period']
    r=subprocess.run(cmd,capture_output=True,text=True);assert r.returncode==0,r.stderr
    assert json.loads(r.stdout)['endpoint_average_assets']['value_exact_yuan']=='105000.015'
    cmd[cmd.index('--as-of')+1]='2026-10-02T23:59:59+08:00';r=subprocess.run(cmd,capture_output=True,text=True)
    assert r.returncode==2 and json.loads(r.stdout)['result'] is None
    cmd[5]='b';r=subprocess.run(cmd,capture_output=True,text=True);assert r.returncode==1 and p['id'] not in r.stdout


def test_fraction_json_contract_preserves_amounts_beyond_double_safe_integer(clock):
    def mutate(fs):
        for f in fs:
            if f['metric']=='total_assets':f['value']='1000000000000000000.00' if f['document_id']=='222' else '999999999999999999.99'
    _,p,_=setup(change=mutate);r=read(p)
    # JS JSON.parse receives exact rational parts as strings, never unsafe numbers.
    transported=json.loads(json.dumps(r),parse_int=float)
    fraction=transported['endpoint_average_assets']['fraction_yuan']
    assert fraction=={'numerator':'199999999999999999999','denominator':'200'}
    assert transported['endpoint_average_assets']['value_exact_yuan']=='999999999999999999.995'
