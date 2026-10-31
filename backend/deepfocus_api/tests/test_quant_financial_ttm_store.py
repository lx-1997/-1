"""Owned stored-time TTM; synthetic sources remain isolated from applications."""
import copy,json,sqlite3,subprocess,sys
from pathlib import Path
import pytest
from deepfocus_api import quant_financial_ttm_store as query,quant_financial_store as store
from deepfocus_api.tests.test_quant_financial_store import ledger
from deepfocus_api.tests.test_quant_financial_flows import fixture,METRICS


def review(identity,owner='a',clock='2026-10-02T00:00:00+08:00',mode='as_recorded',**kw):
    args=dict(issuer_id='cninfo:9900002009',symbol='002109',period_end='2025-03-31',as_of=clock,mode=mode,required_metrics=METRICS)
    args.update(kw);return query.review(owner,identity,**args)


def put():return store.put('a','PRIVATE_PACKET_LABEL',*fixture())['id']


def test_all_raw_components_visible_does_not_bypass_stored_next_day_boundary(ledger,monkeypatch):
    identity=put();clock='2026-10-01T23:59:59+08:00';m,s=fixture()
    assert query.flows.derive_ttm(m,s,issuer_id='cninfo:9900002009',symbol='002109',period_end='2025-03-31',as_of=clock,mode='as_recorded',required_metrics=METRICS)['status']=='arithmetic_available'
    def forbidden(*a,**k):raise AssertionError('TTM must not execute before stored visibility')
    monkeypatch.setattr(query.flows,'derive_ttm',forbidden)
    r=review(identity,clock=clock)
    assert r['status']=='unavailable' and r['metrics'] is None and r['result'] is None
    assert r['availability_reason']=='receipt_not_yet_visible' and r['stored_visibility_applied']
    assert not any(k in r['receipt'] for k in ('label','vintage_count','source_count','input_digest','fingerprint'))
    assert 'PRIVATE_PACKET_LABEL' not in json.dumps(r)
    assert not r['usable_for_strategy'] and not r['promotion_allowed']


def test_boundary_equality_and_historical_reconstruction_are_distinct(ledger):
    identity=put();r=review(identity)
    assert r['status']=='arithmetic_available'
    assert r['metrics']==dict(zip(METRICS,['1539.00','-33.00','153.00']))
    assert all(x['selection']['as_of']==r['request']['as_of'] for x in r['result']['components'])
    assert all(x['selection']['panel']['recorded_at']==r['receipt']['recorded_at'] for x in r['result']['components'])
    assert r['uses_immutable_store_recording_clock'] and not r['externally_attested_time']
    old=review(identity,clock='2025-06-01T09:30:00+08:00',mode='historical_reconstruction')
    assert old['metrics']['parent_net_profit']=='-10.00' and not old['stored_visibility_applied']
    assert not old['historical_pit_feed_certified'] and not old['usable_for_strategy']


def test_missing_prior_period_still_unknown_after_visibility(ledger):
    m,s=fixture();m['vintages']=[r for r in m['vintages'] if r['period_end']=='2025-03-31']
    identity=store.put('a','incomplete',m,s)['id'];r=review(identity)
    assert r['status']=='unavailable' and r['metrics'] is None and r['result']['metrics'] is None
    assert len(r['result']['missing_components'])==2
    assert r['availability_reason']=='financial_components_not_available'


@pytest.mark.parametrize('kw',[{'period_end':'2025-02-28'},{'required_metrics':['unknown']},{'required_metrics':[]},
                              {'issuer_id':'bad'},{'symbol':'BAD'},{'as_of':'2099-01-01T00:00:00+08:00'}])
def test_blocked_queries_still_validate_input_not_silently_accept_invalid_requests(ledger,kw):
    identity=put()
    with pytest.raises((ValueError,TypeError)):review(identity,clock='2026-10-01T23:59:59+08:00',**kw)


def test_owner_and_missing_id_rejected_without_returning_any_receipt(ledger):
    identity=put()
    for who,item in [('b',identity),('a','missing')]:
        with pytest.raises(LookupError):review(item,owner=who)


def test_uses_one_readonly_transaction_no_schema_or_write_calls(ledger,monkeypatch):
    identity=put();connect=sqlite3.connect;statements=[];connections=[]
    def tracked(*args,**kwargs):
        assert 'mode=ro' in args[0] and kwargs['uri'] is True
        c=connect(*args,**kwargs);connections.append(c);c.set_trace_callback(statements.append);return c
    monkeypatch.setattr(query.sqlite3,'connect',tracked)
    r=review(identity);assert r['single_readonly_receipt_snapshot'] and len(connections)==1
    assert any(s=='BEGIN' for s in statements)
    assert all(s.lstrip().upper().startswith(('SELECT','PRAGMA QUERY_ONLY','BEGIN')) for s in statements)
    with pytest.raises(sqlite3.ProgrammingError):connections[0].execute('SELECT 1')


def test_wal_snapshot_remains_consistent_when_an_external_writer_corrupts_blob(ledger,monkeypatch):
    identity=put();real=store.verify_row;observed=[]
    def tamper_after_verified(c,row):
        result=real(c,row);sha=next(iter(result[1]));before=c.execute('select body from qr_financial_source where owner=? and sha=?',('a',sha)).fetchone()[0]
        writer=sqlite3.connect(ledger)
        try:
            writer.execute('drop trigger qr_financial_source_update')
            writer.execute('update qr_financial_source set body=? where owner=? and sha=?',(b'CORRUPTED','a',sha));writer.commit()
        finally:writer.close()
        assert c.execute('select body from qr_financial_source where owner=? and sha=?',('a',sha)).fetchone()[0]==before
        observed.append(True);return result
    monkeypatch.setattr(store,'verify_row',tamper_after_verified)
    assert review(identity)['metrics']['parent_net_profit']=='-33.00' and observed
    monkeypatch.setattr(store,'verify_row',real)
    with pytest.raises(ValueError):review(identity)


def test_query_source_drift_fails_before_database_creation(ledger,monkeypatch):
    monkeypatch.setattr(query,'_pins',lambda:{**query.LOADED_PINS,'quant_financial_ttm_store.py':'0'*64})
    with pytest.raises(ValueError,match='source changed'):review('missing')
    assert not ledger.exists()


def test_cli_retains_blocked_json_exit_two_and_foreign_owner_has_no_identifying_output(ledger):
    identity=put();script=Path(__file__).resolve().parents[3]/'scripts/quant-financial-receipt.py'
    common=[sys.executable,str(script),'--db',str(ledger),'--owner','a','ttm','--id',identity,
        '--issuer-id','cninfo:9900002009','--symbol','002109','--period-end','2025-03-31',
        '--as-of','2026-10-01T23:59:59+08:00','--mode','as_recorded','--metrics',*METRICS]
    r=subprocess.run(common,capture_output=True,text=True);assert r.returncode==2,r.stderr
    value=json.loads(r.stdout);assert value['result'] is None and value['metrics'] is None
    common[5]='b';r=subprocess.run(common,capture_output=True,text=True)
    assert r.returncode==1 and identity not in r.stdout and '002109' not in r.stdout


def test_incomplete_receipt_never_borrows_periods_from_another_complete_receipt(ledger):
    m,s=fixture();partial=copy.deepcopy(m)
    partial['vintages']=[r for r in partial['vintages'] if r['period_end']=='2025-03-31']
    incomplete=store.put('a','incomplete receipt',partial,s)['id']
    complete=store.put('a','complete receipt',m,s)['id']
    assert review(complete)['status']=='arithmetic_available'
    r=review(incomplete);assert r['metrics'] is None and r['result']['metrics'] is None
    assert len(r['result']['missing_components'])==2


def test_new_year_visibility_is_beijing_calendar_day_not_a_fixed_24_hour_delay(ledger,monkeypatch):
    from datetime import datetime
    m,s=fixture();m['vintages']=[r for r in m['vintages'] if r['revision']==1]
    for row in m['vintages']:
        row['observed_at']='2025-12-30T12:00:00+08:00';row['recorded_at']='2025-12-30T13:00:00+08:00'
    monkeypatch.setattr(store,'_now',lambda:datetime.fromisoformat('2025-12-31T15:59:59+00:00'))
    p=store.put('a','new year clock fixture',m,s)
    assert p['stored_visible_at']=='2026-01-01T00:00:00+08:00'
    assert review(p['id'],clock='2025-12-31T23:59:59+08:00')['result'] is None
    assert review(p['id'],clock='2026-01-01T00:00:00+08:00')['metrics']['parent_net_profit']=='-10.00'
