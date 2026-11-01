"""Capacity changes must preserve the exact previous source domains and archives."""
import copy
from contextlib import closing
import json, sqlite3

import pytest
from deepfocus_api import quant_financial_store as base, quant_financial_fact_store as facts
from deepfocus_api import quant_financial_read as archive, quant_recovery as recovery
from deepfocus_api.tests.test_quant_financial_fact_derived import clock, ledger, setup, read
from deepfocus_api.tests.test_quant_financial_identity_upgrade import rename_fixture, mutate_owned, snapshot
from deepfocus_api.tests.test_quant_financial_path_upgrade import paths
from deepfocus_api.tests.test_quant_financial_vintages import fixture


def old_packet(db):
    parent, packet, _ = setup()
    old = mutate_owned(db, 'qr_financial_bundle', parent['id'], lambda v: v.update(source_pins=copy.deepcopy(base.KNOWN_V5_SOURCE_PINS)))
    mutate_owned(db, 'qr_financial_fact_packet', packet['id'], lambda v: v.update(source_pins=copy.deepcopy(facts.KNOWN_V3_SOURCE_PINS), parent_fingerprint=base.digest(old)))
    return parent, packet


def test_previous_full_arithmetic_archive_and_cold_restore_unchanged(clock, monkeypatch):
    parent, packet = old_packet(clock); before = snapshot(clock)
    value = read(packet)
    assert value['ttm_value'] == '-395.00' and value['endpoint_average_assets']['value_exact_yuan'] == '105000.015'
    assert value['receipt']['archive_source_status'] == 'known_v3_revalidated'
    assert not value['receipt']['archive_source_current']
    assert read(packet, as_of='2026-10-02T23:59:59+08:00')['result'] is None
    assert snapshot(clock) == before
    assert base.source_profile(json.loads(before['qr_financial_bundle'][0][-1])) == 'known_v5_revalidated'
    proof = recovery.prepare(clock, clock.parent/'cold')
    assert proof['verified'] and not proof['activation_authorized']
    cold = clock.parent/'cold/research.sqlite3'
    assert snapshot(cold) == before == snapshot(clock)
    monkeypatch.setenv('DEEPFOCUS_QUANT_RESEARCH_DB_PATH', str(cold))
    assert read(packet) == value
    with pytest.raises(LookupError): archive.detail('b', packet['id'])


@pytest.mark.parametrize('extension', ['PDF', 'pdf'])
def test_previous_source_retains_alpha_and_both_exact_PDF_cases(clock, extension):
    m, s = paths(*rename_fixture('gfbj0835710'), extension)
    parent = base.put('a', 'previous alpha exact path', m, s)
    d = m['vintages'][0]
    packet = facts.put('a', parent['id'], 'previous typed', [dict(document_id=d['document_id'], source_sha256=d['source_sha256'], metric='consolidated_net_profit', column='current_period', basis='year_to_date', period_start=d['period_start'], period_end=d['period_end'], value='-130.00', source_page=2)])
    old = mutate_owned(clock, 'qr_financial_bundle', parent['id'], lambda v: v.update(source_pins=copy.deepcopy(base.KNOWN_V5_SOURCE_PINS)))
    mutate_owned(clock, 'qr_financial_fact_packet', packet['id'], lambda v: v.update(source_pins=copy.deepcopy(facts.KNOWN_V3_SOURCE_PINS), parent_fingerprint=base.digest(old)))
    before = snapshot(clock); value = archive.detail('a', packet['id'])
    assert value['archive_source_status'] == 'known_v3_revalidated'
    assert value['securities'][0]['issuer_id'] == 'cninfo:gfbj0835710'
    assert snapshot(clock) == before


@pytest.mark.parametrize('damage', ['extra', 'missing', 'hash'])
def test_previous_source_pins_must_match_whole_registered_family(clock, damage):
    _, packet = old_packet(clock)
    def change(v):
        if damage == 'extra': v['source_pins']['unexpected.py'] = '0'*64
        if damage == 'missing': v['source_pins'].pop('quant_financial_store.py')
        if damage == 'hash': v['source_pins']['quant_financial_store.py'] = '0'*64
    mutate_owned(clock, 'qr_financial_fact_packet', packet['id'], change)
    with pytest.raises(ValueError, match='source identity'): archive.detail('a', packet['id'])


def test_owner_byte_budget_still_enforced_and_dedup_not_double_charged(clock, monkeypatch):
    m, s = fixture(); retained = sum(map(len, s.values()))
    monkeypatch.setattr(base, 'MAX_OWNER_BYTES', retained)
    base.put('a', 'first', m, s); base.put('a', 'same source second label', m, s)
    with closing(sqlite3.connect(clock)) as c:
        assert c.execute('SELECT sum(length(body)) FROM qr_financial_source WHERE owner=?', ('a',)).fetchone()[0] == retained
    def parent_state():
        with closing(sqlite3.connect(clock)) as c:
            return {t: c.execute('SELECT * FROM '+t+' ORDER BY 1,2').fetchall() for t in ('qr_financial_bundle', 'qr_financial_source')}
    before = parent_state()
    monkeypatch.setattr(base, 'MAX_OWNER_BYTES', retained-1)
    with pytest.raises(RuntimeError, match='source quota'): base.put('a', 'over quota', m, s)
    assert parent_state() == before
    monkeypatch.setattr(base, 'MAX_OWNER_BYTES', retained)
    base.put('b', 'other owner own budget', m, s)
    with closing(sqlite3.connect(clock)) as c:
        assert c.execute('SELECT sum(length(body)) FROM qr_financial_source WHERE owner=?', ('b',)).fetchone()[0] == retained
