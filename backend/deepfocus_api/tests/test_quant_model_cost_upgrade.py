"""Synthetic source-mismatch fixtures; real prior receipts are tested separately."""
import json,sqlite3,hashlib
import pytest
from deepfocus_api import quant_model_cost_store as store,quant_model_cost_integrity as integrity,quant_model_cost_read as read,quant_recovery as recovery
from deepfocus_api.tests.test_quant_model_cost_store import original


def rewrite(path,change):
    c=sqlite3.connect(path)
    try:
        c.execute('DROP TRIGGER IF EXISTS qr_model_cost_archive_update')
        value=json.loads(c.execute('SELECT payload FROM qr_model_cost_archive').fetchone()[0]);change(value)
        value['request_hash']=integrity.digest({'label':value['label'],'input_sha':value['input_sha256'],'source_pins':value['source_pins']})
        c.execute('UPDATE qr_model_cost_archive SET payload=?,fingerprint=?,input_hash=?',(json.dumps(value),integrity.digest(value),value['request_hash']));c.commit()
    finally:c.close()


@pytest.fixture
def historical(tmp_path,monkeypatch,original):
    path=tmp_path/'source.sqlite3';monkeypatch.setenv('DEEPFOCUS_QUANT_RESEARCH_DB_PATH',str(path));receipt=store.put('a','unit historical',original)
    def old(value):
        value['source_pins'].pop('quant_model_cost_integrity.py')
        value['source_pins']['quant_model_cost_store.py']='0'*64
        value['report']['source_pins']=value['source_pins']
    rewrite(path,old);return path,receipt


def forbidden(*args,**kwargs):raise AssertionError('Storage verification must not execute a model or source-qualified validator')


def test_historical_storage_and_metadata_read_do_not_grant_current_math_status(historical,monkeypatch):
    path,receipt=historical;before=path.read_bytes()
    monkeypatch.setattr(store,'compute',forbidden);monkeypatch.setattr(store.exact,'run',forbidden);monkeypatch.setattr(store.engine,'walk_forward',forbidden)
    with store.connect_ro() as c:
        row=c.execute('SELECT * FROM qr_model_cost_archive').fetchone();value,body=integrity.verify_row(c,row)
    assert body and value['source_pins']!=store.LOADED_PINS
    detail=read.detail('a',receipt['id'])
    assert detail['experiments'] is None and detail['verification_level']=='metadata_only' and not detail['source_version_current']
    with pytest.raises(ValueError,match='Historical source'):store.inspect('a',receipt['id'],recompute=True)
    assert path.read_bytes()==before


def test_v5_cold_copy_preserves_historical_bytes_and_explicitly_no_math_replay(historical,tmp_path,monkeypatch):
    path,receipt=historical;before=path.read_bytes();monkeypatch.setattr(store,'verify_row',forbidden);monkeypatch.setattr(store,'compute',forbidden)
    workspace=tmp_path/'recovered';proof=recovery.prepare(path,workspace)
    assert proof['verified'] and not proof['activation_authorized']
    assert proof['model_cost_validation_scope']['mathematical_replay_performed'] is False
    assert proof['model_cost_validation_scope']['stored_source_current_required'] is False
    assert path.read_bytes()==before
    with store.connect_ro() as c:old=c.execute('SELECT payload,fingerprint FROM qr_model_cost_archive').fetchone()
    with recovery.connect_ro(workspace/'research.sqlite3') as c:new=c.execute('SELECT payload,fingerprint FROM qr_model_cost_archive').fetchone()
    assert tuple(old)==tuple(new)
    assert recovery.verify(workspace)['model_cost_validation_scope']==proof['model_cost_validation_scope']


@pytest.mark.parametrize('damage',['schema','pin_inventory','promotion','checkpoint_hash','checkpoint_source','original_gate','original_input','owner_link','duplicate_json'])
def test_historical_corruption_or_unknown_schema_is_not_excused_by_stale_source(historical,damage):
    path,_=historical
    def change(v):
        if damage=='schema':v['version']='qr-exact-model-archive-v99'
        if damage=='pin_inventory':v['source_pins']['unknown_execution.py']='0'*64;v['report']['source_pins']=v['source_pins']
        if damage=='promotion':v['report']['promotion_allowed']=True
        if damage=='checkpoint_hash':v['report']['results'][0]['cost_book']['state']['state_digest']='0'*64
        if damage=='checkpoint_source':
            state=v['report']['results'][0]['cost_book']['state'];state['source_sha256']='0'*64
            state['state_digest']=integrity.digest({k:value for k,value in state.items() if k!='state_digest'})
        if damage=='original_gate':v['report']['results'][0]['original_gate']['passed']=not v['report']['results'][0]['original_gate']['passed']
    if damage=='original_input':
        c=sqlite3.connect(path)
        try:
            c.execute('DROP TRIGGER qr_model_cost_input_update');value=json.loads(c.execute('SELECT body FROM qr_model_cost_input').fetchone()[0]);value[0]['dataset']['600519']['bars'][0]['close']+=1
            body=json.dumps(value).encode();sha=hashlib.sha256(body).hexdigest();c.execute('UPDATE qr_model_cost_input SET body=?,sha=?',(body,sha));c.commit()
        finally:c.close()
        def changed_input(v):v['input_sha256']=sha
        rewrite(path,changed_input)
        c=sqlite3.connect(path)
        try:c.execute('UPDATE qr_model_cost_archive SET input_sha=?',(sha,));c.commit()
        finally:c.close()
    else:rewrite(path,change)
    if damage in ['owner_link','duplicate_json']:
        c=sqlite3.connect(path)
        try:
            if damage=='owner_link':c.execute('UPDATE qr_model_cost_archive SET owner=?',('foreign',))
            else:
                raw=c.execute('SELECT payload FROM qr_model_cost_archive').fetchone()[0]
                c.execute('UPDATE qr_model_cost_archive SET payload=?',('{"version":"'+integrity.SCHEMA+'",'+raw[1:],))
            c.commit()
        finally:c.close()
    with store.connect_ro() as c:
        row=c.execute('SELECT * FROM qr_model_cost_archive').fetchone()
        with pytest.raises(ValueError):integrity.verify_row(c,row)


def test_unknown_historical_schema_still_aborts_recovery_without_ready(historical,tmp_path):
    path,_=historical;rewrite(path,lambda v:v.update(version='unknown-v99'));before=path.read_bytes();workspace=tmp_path/'failed'
    with pytest.raises(ValueError):recovery.prepare(path,workspace)
    assert (workspace/'FAILED.json').exists() and not (workspace/'READY.json').exists() and path.read_bytes()==before


def test_recovery_receipt_cannot_claim_a_math_replay_it_did_not_perform(historical,tmp_path):
    path,_=historical;workspace=tmp_path/'recovered';recovery.prepare(path,workspace)
    (workspace/'READY.json').unlink()
    receipt=json.loads((workspace/'receipt.json').read_bytes());receipt['model_cost_validation_scope']['mathematical_replay_performed']=True
    (workspace/'receipt.json').write_text(json.dumps(receipt))
    with pytest.raises(ValueError,match='scope'):recovery.verify(workspace)


def test_returned_scope_cannot_mutate_later_validation_protocol(historical,tmp_path):
    path,_=historical;workspace=tmp_path/'recovered';recovery.prepare(path,workspace)
    first=recovery.verify(workspace);first['model_cost_validation_scope']['mathematical_replay_performed']=True
    assert recovery.verify(workspace)['model_cost_validation_scope']['mathematical_replay_performed'] is False
