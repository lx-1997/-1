"""Typed extraction revision, as-recorded, owner and recovery failure controls."""
import copy,json,sqlite3,subprocess,sys
from datetime import datetime
from decimal import localcontext,Inexact,Rounded
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pytest
from deepfocus_api import quant_financial_fact_store as store,quant_financial_store as base,quant_recovery as recovery
from deepfocus_api.tests.test_quant_financial_store import ledger
from deepfocus_api.tests.test_quant_financial_vintages import fixture


@pytest.fixture
def clock(ledger,monkeypatch):
    monkeypatch.setattr(store,'_now',lambda:datetime.fromisoformat('2026-10-02T08:00:00+00:00'))
    return ledger


def setup():
    m,s=fixture();p=base.put('a','parent',m,s)
    rows=[]
    for r in m['vintages']:
        rows.append({'document_id':r['document_id'],'source_sha256':r['source_sha256'],'metric':'consolidated_net_profit',
            'column':'current_period','basis':'year_to_date','period_start':r['period_start'],'period_end':r['period_end'],
            'value':'-130.00' if r['revision']==1 else '-175.00','source_page':8})
    return p,rows


def put(p,f,**kwargs):return store.put('a',p['id'],'extraction',f,**kwargs)


def read(p,**kwargs):
    args=dict(issuer_id='cninfo:9900002009',symbol='002109',report_period_end='2025-03-31',metric='consolidated_net_profit',
        column='current_period',as_of='2026-10-03T00:00:00+08:00',mode='as_recorded')
    args.update(kwargs);return store.review('a',p['id'],**args)


def rows(db,table):
    c=sqlite3.connect(db)
    try:return c.execute('select * from '+table+' order by id').fetchall()
    finally:c.close()


def test_new_extraction_cannot_use_older_parent_storage_clock(clock):
    p,f=setup();q=put(p,f);before=rows(clock,'qr_financial_bundle')
    assert q['stored_visible_at']=='2026-10-03T00:00:00+08:00'
    for at in ['2025-06-01T00:00:00+08:00','2026-10-02T00:00:00+08:00','2026-10-02T23:59:59+08:00']:
        r=read(q,as_of=at);assert r['status']=='unavailable' and r['fact'] is None
        assert r['availability_reason']=='receipt_not_yet_visible'
    assert read(q)['fact']['value']=='-175.00'
    assert read(q,as_of='2025-06-01T00:00:00+08:00',mode='historical_reconstruction')['fact']['value']=='-130.00'
    assert rows(clock,'qr_financial_bundle')==before
    assert not read(q)['usable_for_strategy']


def test_missing_revised_fact_never_fills_from_prior_document(clock):
    p,f=setup();q=put(p,f[:1]);r=read(q)
    assert r['status']=='unavailable' and r['fact'] is None and r['availability_reason']=='missing_selected_document_fact'


def test_stock_fact_has_column_date_and_never_a_flow_interval(clock):
    p,f=setup();s=copy.deepcopy(f[-1]);s.update(metric='total_assets',basis='point_in_time_stock',period_start=None,
                                            column='previous_year_end',period_end='2024-12-31',value='100000000.01')
    q=put(p,[s]);r=read(q,metric='total_assets',column='previous_year_end')
    assert r['fact']['period_start'] is None and r['fact']['period_end']=='2024-12-31'
    assert r['fact']['value']=='100000000.01' and r['fact']['basis']=='point_in_time_stock'


@pytest.mark.parametrize('damage',['stock_flow','stock_date','flow_column','flow_period','source','unknown_metric','duplicate','page_bool','overflow','nan','wrong_document','negative_assets'])
def test_typed_invalid_input_fails_without_creating_packet(clock,damage):
    p,f=setup()
    if damage=='stock_flow':f[0].update(metric='total_assets')
    if damage=='stock_date':f[0].update(metric='total_assets',basis='point_in_time_stock',period_start=None,column='previous_year_end')
    if damage=='flow_column':f[0]['column']='previous_year_end'
    if damage=='flow_period':f[0]['period_start']='2024-01-01'
    if damage=='source':f[0]['source_sha256']='0'*64
    if damage=='unknown_metric':f[0]['metric']='fake_alpha'
    if damage=='duplicate':f.append(copy.deepcopy(f[0]))
    if damage=='page_bool':f[0]['source_page']=True
    if damage=='overflow':f[0]['value']='1000000000000000000.01'
    if damage=='nan':f[0]['value']='NaN'
    if damage=='wrong_document':f[0]['document_id']='9999999999'
    if damage=='negative_assets':f[0].update(metric='total_assets',basis='point_in_time_stock',period_start=None,value='-1.00')
    with pytest.raises(ValueError):put(p,f)
    assert rows(clock,'qr_financial_fact_packet')==[]


def note(f):return {**{k:f[k] for k in ('document_id','metric','column')},'reason':'Operator corrected the reviewed source cell extraction'}


def test_add_correct_withdraw_keeps_all_prior_snapshots_and_parent_unchanged(clock,monkeypatch):
    p,f=setup();q1=put(p,f);parent=rows(clock,'qr_financial_bundle');old=rows(clock,'qr_financial_fact_packet')
    assert put(p,f)==q1
    new=copy.deepcopy(f);extra=copy.deepcopy(f[0]);extra.update(metric='minority_net_profit',value='-35.00');new.append(extra)
    q2=put(p,new,supersedes_packet_id=q1['id']);assert q2['revision']==2
    fixed=copy.deepcopy(new);fixed[-1]['value']='-36.00'
    with pytest.raises(ValueError,match='correction note'):put(p,fixed,supersedes_packet_id=q2['id'])
    monkeypatch.setattr(store,'_now',lambda:datetime.fromisoformat('2026-10-03T08:00:00+00:00'))
    q3=put(p,fixed,supersedes_packet_id=q2['id'],correction_notes=[note(extra)])
    assert read(q3)['fact'] is None  # correction has its own next-day visibility.
    assert read(q2)['fact']['value']=='-175.00'
    q4=put(p,fixed[:-1],supersedes_packet_id=q3['id'],correction_notes=[note(extra)])
    assert q4['revision']==4
    assert old[0] in rows(clock,'qr_financial_fact_packet') and rows(clock,'qr_financial_bundle')==parent


def test_revisions_require_explicit_latest_predecessor_and_exact_notes(clock):
    p,f=setup();q=put(p,f);new=copy.deepcopy(f);new[0]['value']='-131.00'
    for predecessor,notes in [(None,[note(f[0])]),('foreign',[note(f[0])]),(q['id'],[]),(q['id'],[note(f[1])])]:
        with pytest.raises(ValueError):put(p,new,supersedes_packet_id=predecessor,correction_notes=notes)
    with pytest.raises(ValueError,match='must add'):put(p,f,supersedes_packet_id=q['id'])


def test_explicit_blank_retained_not_turned_into_zero(clock):
    p,f=setup();f[-1]['value']=None;q=put(p,f);r=read(q)
    assert r['fact']['value'] is None and r['availability_reason']=='reported_blank' and r['status']=='unavailable'


def test_cross_owner_and_parent_scope_rejected(clock):
    p,f=setup();q=put(p,f)
    with pytest.raises(LookupError):store.put('b',p['id'],'foreign',f)
    with pytest.raises(LookupError):store.review('b',q['id'],issuer_id='cninfo:9900002009',symbol='002109',report_period_end='2025-03-31',metric='total_assets',column='current_period',as_of='2026-10-03T00:00:00+08:00',mode='as_recorded')
    other=base.put('a','other parent',*fixture())
    with pytest.raises(ValueError):store.put('a',other['id'],'cross parent',f,supersedes_packet_id=q['id'])


def test_query_future_and_wrong_identity_rejected_even_before_boundary(clock):
    p,f=setup();q=put(p,f)
    with pytest.raises(ValueError):read(q,as_of='2099-01-01T00:00:00+08:00')
    with pytest.raises(ValueError):read(q,symbol='BAD')
    assert read(q,symbol='600519')['status']=='unavailable'


def test_owner_quota_and_clock_failure_roll_back(clock,monkeypatch):
    p,f=setup();monkeypatch.setattr(store,'MAX_OWNER_BYTES',1)
    with pytest.raises(RuntimeError):put(p,f)
    assert rows(clock,'qr_financial_fact_packet')==[]
    monkeypatch.setattr(store,'MAX_OWNER_BYTES',10*1024**2)
    monkeypatch.setattr(store,'_now',lambda:datetime.fromisoformat('2026-09-01T08:00:00+00:00'))
    with pytest.raises(ValueError):put(p,f)
    assert rows(clock,'qr_financial_fact_packet')==[]


def test_same_input_concurrent_import_is_one_packet(clock):
    p,f=setup()
    with ThreadPoolExecutor(max_workers=2) as pool:ids=list(pool.map(lambda _:put(p,f)['id'],range(2)))
    assert ids[0]==ids[1] and len(rows(clock,'qr_financial_fact_packet'))==1


def test_decimal_caller_precision_does_not_change_boundary(clock):
    p,f=setup();f[0]['value']='1000000000000000000.00'
    with localcontext() as ctx:
        ctx.prec=1;ctx.traps[Inexact]=True;ctx.traps[Rounded]=True
        q=put(p,f);assert read(q,as_of='2025-06-01T00:00:00+08:00',mode='historical_reconstruction')['fact']['value']==f[0]['value']


def test_current_source_guard_runs_before_creation(clock,monkeypatch):
    monkeypatch.setattr(store,'_pins',lambda:{})
    with pytest.raises(ValueError,match='source changed'):store.put('a','missing','x',[])
    assert not clock.exists()


def test_cold_restore_preserves_parent_and_full_extraction_chain(clock):
    p,f=setup();q=put(p,f);new=copy.deepcopy(f);new[0]['value']='-131.00'
    put(p,new,supersedes_packet_id=q['id'],correction_notes=[note(f[0])])
    before=rows(clock,'qr_financial_fact_packet');parents=rows(clock,'qr_financial_bundle');target=clock.parent/'cold'
    proof=recovery.prepare(clock,target);assert proof['verified'] and not proof['activation_authorized']
    assert rows(target/'research.sqlite3','qr_financial_fact_packet')==before and rows(target/'research.sqlite3','qr_financial_bundle')==parents


def test_immutable_trigger_and_recomputed_corruption_rejected_on_read_and_recovery(clock):
    p,f=setup();q=put(p,f);c=sqlite3.connect(clock)
    try:
        with pytest.raises(sqlite3.IntegrityError):c.execute('delete from qr_financial_fact_packet')
        c.rollback();row=c.execute('select payload from qr_financial_fact_packet').fetchone();v=json.loads(row[0])
        v['input']['facts'][0]['basis']='point_in_time_stock'
        c.execute('drop trigger qr_financial_fact_packet_update')
        c.execute('update qr_financial_fact_packet set payload=?,input_hash=?,fingerprint=?',(json.dumps(v),store.digest(v['input']),store.digest(v)));c.commit()
    finally:c.close()
    with pytest.raises(ValueError):read(q)
    with pytest.raises(ValueError):recovery.prepare(clock,clock.parent/'broken-cold')


def test_cli_actual_storage_clock_and_foreign_owner_generic_error(clock,tmp_path):
    p,f=setup();script=Path(__file__).resolve().parents[3]/'scripts/quant-financial-facts.py';q=put(p,f)
    cmd=[sys.executable,str(script),'--db',str(clock),'--owner','a','review','--id',q['id'],'--issuer-id','cninfo:9900002009','--symbol','002109','--report-period-end','2025-03-31','--metric','consolidated_net_profit','--column','current_period','--as-of','2025-06-01T00:00:00+08:00','--mode','as_recorded']
    r=subprocess.run(cmd,capture_output=True,text=True);assert r.returncode==2 and json.loads(r.stdout)['fact'] is None
    cmd[5]='b';r=subprocess.run(cmd,capture_output=True,text=True);assert r.returncode==1 and q['id'] not in r.stdout and '002109' not in r.stdout


def test_query_one_readonly_snapshot_and_close(clock,monkeypatch):
    p,f=setup();q=put(p,f);real=sqlite3.connect;connections=[];statements=[]
    def tracked(*args,**kwargs):
        assert 'mode=ro' in args[0] and kwargs['uri']
        c=real(*args,**kwargs);connections.append(c);c.set_trace_callback(statements.append);return c
    monkeypatch.setattr(store.sqlite3,'connect',tracked)
    assert read(q)['fact']['value']=='-175.00' and len(connections)==1
    assert all(s.lstrip().upper().startswith(('SELECT','BEGIN','PRAGMA QUERY_ONLY')) for s in statements)
    with pytest.raises(sqlite3.ProgrammingError):connections[0].execute('select 1')


def test_withdrawal_is_explicit_and_never_borrows_old_packet_fact(clock):
    p,f=setup();q=put(p,f)
    new=put(p,[],supersedes_packet_id=q['id'],correction_notes=[note(x) for x in f])
    r=read(new);assert r['status']=='unavailable' and r['fact'] is None
    assert r['availability_reason']=='missing_selected_document_fact'
    assert read(q)['fact']['value']=='-175.00'


def test_tampered_extraction_clock_rejected_even_with_new_payload_hash(clock):
    p,f=setup();q=put(p,f);c=sqlite3.connect(clock)
    try:
        v=json.loads(c.execute('select payload from qr_financial_fact_packet').fetchone()[0]);v['stored_visible_at']='2025-01-01T00:00:00+08:00'
        c.execute('drop trigger qr_financial_fact_packet_update')
        c.execute('update qr_financial_fact_packet set payload=?,fingerprint=?',(json.dumps(v),store.digest(v)));c.commit()
    finally:c.close()
    with pytest.raises(ValueError):read(q)


def test_no_forged_cross_owner_predecessor_even_with_recomputed_metadata(clock):
    p,f=setup();q=put(p,f);modified=copy.deepcopy(f);modified[0]['value']='-131.00'
    q2=put(p,modified,supersedes_packet_id=q['id'],correction_notes=[note(f[0])]);c=sqlite3.connect(clock)
    try:
        c.execute('drop trigger qr_financial_fact_packet_update');c.execute('update qr_financial_fact_packet set owner=? where id=?',('b',q['id']));c.commit()
    finally:c.close()
    with pytest.raises(ValueError,match='predecessor missing'):read(q2)


def test_ambiguous_parent_document_period_not_silently_overwritten(clock):
    m,s=fixture();other=copy.deepcopy(m['vintages'][0]);other.update(period_start='2024-01-01',period_end='2024-12-31')
    m['vintages'].append(other);p=base.put('a','ambiguous parent',m,s)
    row=m['vintages'][0]
    f=[{'document_id':row['document_id'],'source_sha256':row['source_sha256'],'metric':'consolidated_net_profit',
        'column':'current_period','basis':'year_to_date','period_start':row['period_start'],
        'period_end':row['period_end'],'value':'-130.00','source_page':8}]
    with pytest.raises(ValueError,match='Ambiguous parent'):put(p,f)
