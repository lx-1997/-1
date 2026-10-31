"""Known v1 source revalidation never rewrites archives or exempts old amounts."""
import copy
from decimal import localcontext
import json
import sqlite3
import pytest
from deepfocus_api import quant_financial_store as store,quant_recovery as recovery
from deepfocus_api.tests.test_quant_financial_store import ledger,read,counts
from deepfocus_api.tests.test_quant_financial_vintages import fixture


def change_owned_fixture(path,identity,mutate):
    """Construct a synthetic legacy row, restoring its immutable trigger after edit."""
    c=sqlite3.connect(path);c.row_factory=sqlite3.Row
    try:
        row=c.execute('select * from qr_financial_bundle where id=?',(identity,)).fetchone();value=json.loads(row['payload'])
        mutate(value)
        sql=c.execute("select sql from sqlite_master where type='trigger' and name='qr_financial_bundle_update'").fetchone()[0]
        c.execute('drop trigger qr_financial_bundle_update')
        c.execute('update qr_financial_bundle set payload=?,fingerprint=?,input_hash=? where id=?',
            (json.dumps(value,ensure_ascii=False),store.digest(value),store.digest(value['input']),identity))
        c.execute(sql);c.commit()
    finally:c.close()


def legacy(path):
    receipt=store.put('a','fixture',*fixture())
    change_owned_fixture(path,receipt['id'],lambda v:v.update(source_pins=copy.deepcopy(store.KNOWN_V1_SOURCE_PINS)))
    return receipt['id']


def retained(path):
    c=sqlite3.connect(path)
    try:return c.execute('select id,input_hash,fingerprint,created,payload from qr_financial_bundle order by id').fetchall()
    finally:c.close()


def test_legacy_is_revalidated_and_labeled_without_rewriting_or_reclocking(ledger):
    identity=legacy(ledger);before=retained(ledger);result=read(identity)
    assert result['metrics']['parent_net_profit']=='-118.00'
    assert result['validation_version']=='exact-financial-magnitude-v2'
    p=result['receipt'];assert p['archive_source_status']=='known_v1_revalidated'
    assert not p['archive_source_current'] and not p['archive_rewritten']
    assert p['archive_source_digest']==store.digest(store.KNOWN_V1_SOURCE_PINS)
    assert p['validated_source_digest']==store.digest(store.LOADED_PINS) and p['archive_source_digest']!=p['validated_source_digest']
    assert retained(ledger)==before
    assert read(identity,clock='2026-10-01T23:59:59+08:00')['status']=='unavailable'
    assert read(identity,clock='2025-06-01T09:30:00+08:00',mode='historical_reconstruction')['metrics']['parent_net_profit']=='-95.00'


def test_idempotent_reimport_preserves_old_identity_and_new_receipt_is_separate(ledger):
    identity=legacy(ledger);before=retained(ledger)
    repeat=store.put('a','fixture',*fixture())
    assert repeat['id']==identity and repeat['archive_source_status']=='known_v1_revalidated'
    assert retained(ledger)==before and counts(ledger)==(4,1)
    fresh=store.put('a','new separately recorded receipt',*fixture())
    assert fresh['id']!=identity and fresh['archive_source_current'] and fresh['archive_source_status']=='current'
    assert fresh['validator_version']==store.VALIDATOR_VERSION
    assert read(identity)['receipt']['archive_source_status']=='known_v1_revalidated'
    assert next(row for row in retained(ledger) if row[0]==identity)==before[0]


@pytest.mark.parametrize('damage',['partial','extra','vintage','store','schema','forged_profile'])
def test_unknown_source_identity_is_rejected_even_with_recomputed_row_hash(ledger,damage):
    identity=legacy(ledger)
    def mutate(v):
        if damage=='partial':v['source_pins'].pop('quant_financial_store.py')
        elif damage=='extra':v['source_pins']['unknown.py']='0'*64
        elif damage=='schema':v['schema']='unknown-financial-schema'
        else:
            v['source_pins']['quant_financial_vintages.py' if damage!='store' else 'quant_financial_store.py']='0'*64
            if damage=='forged_profile':v['archive_source_status']='known_v1_revalidated'
    change_owned_fixture(ledger,identity,mutate)
    with pytest.raises(ValueError):read(identity)
    with pytest.raises(ValueError):recovery.prepare(ledger,ledger.parent/('rejected-'+damage))


@pytest.mark.parametrize('amount',['1000000000000000000.01','-1000000000000000000.01'])
def test_old_unsafe_amount_not_grandfathered_even_when_all_hashes_recomputed(ledger,amount):
    identity=legacy(ledger)
    def mutate(v):
        for m in (v['input']['manifest'],v['effective_manifest']):m['vintages'][0]['metrics']['revenue']=amount
    change_owned_fixture(ledger,identity,mutate)
    with localcontext() as ctx:
        ctx.prec=1
        with pytest.raises(ValueError):read(identity)
        with pytest.raises(ValueError):recovery.prepare(ledger,ledger.parent/'unsafe-cold')


def test_legacy_still_requires_own_sources_and_unchanged_storage_clock(ledger):
    identity=legacy(ledger)
    with pytest.raises(LookupError):read(identity,owner='other')
    def mutate(v):v['stored_visible_at']='2026-09-01T00:00:00+08:00'
    change_owned_fixture(ledger,identity,mutate)
    with pytest.raises(ValueError,match='clock changed'):read(identity)


def test_mixed_known_old_and_current_archives_cold_restore_with_original_rows(ledger,monkeypatch):
    identity=legacy(ledger);store.put('a','current fixture',*fixture());before=retained(ledger)
    workspace=ledger.parent/'cold';proof=recovery.prepare(ledger,workspace)
    assert proof['verified'] and proof['activation_authorized'] is False
    assert retained(ledger)==before and retained(workspace/'research.sqlite3')==before
    monkeypatch.setenv('DEEPFOCUS_QUANT_RESEARCH_DB_PATH',str(workspace/'research.sqlite3'))
    result=read(identity);assert result['receipt']['archive_source_status']=='known_v1_revalidated'
    assert result['metrics']['parent_net_profit']=='-118.00'


def test_known_source_versions_do_not_bypass_runtime_disk_source_guard(ledger,monkeypatch):
    identity=legacy(ledger)
    monkeypatch.setattr(store,'_pins',lambda:{**store.LOADED_PINS,'quant_financial_store.py':'0'*64})
    with pytest.raises(ValueError,match='source changed'):read(identity)
