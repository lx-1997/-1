"""Multi-page reviewed panels and both retained source generations."""
import copy
import pytest
from deepfocus_api import quant_financial_vintages as v,quant_financial_store as store
from deepfocus_api.tests.test_quant_financial_vintages import fixture,read
from deepfocus_api.tests.test_quant_financial_store import ledger
from deepfocus_api.tests.test_quant_financial_source_upgrade import change_owned_fixture,retained


def test_one_document_multiple_metric_pages_retains_exact_value_and_source():
    m,s=fixture();row=m['vintages'][0];row['source_page']=7
    row['metric_source_pages']={'revenue':7,'parent_net_profit':7,'net_operating_cashflow':8}
    r=read(m,s)
    assert r['panel']['metric_source_pages']==row['metric_source_pages'] and r['panel']['source_page']==7
    assert r['metrics']==row['metrics'] and not r['promotion_allowed']


@pytest.mark.parametrize('bad',[{}, {'revenue':2}, {'revenue':2,'parent_net_profit':2,'net_operating_cashflow':True},
    {'revenue':2,'parent_net_profit':2,'net_operating_cashflow':0}, {'revenue':2,'parent_net_profit':2,'net_operating_cashflow':1001},
    {'revenue':3,'parent_net_profit':3,'net_operating_cashflow':4}])
def test_partial_or_invalid_metric_pages_refused(bad):
    m,s=fixture();m['vintages'][0]['metric_source_pages']=bad
    with pytest.raises(ValueError,match='page references'):read(m,s)


def test_legacy_anchor_does_not_invent_per_metric_pages():
    m,s=fixture();before=copy.deepcopy(m);r=read(m,s)
    assert r['panel']['metric_source_pages'] is None and m==before
    assert r['page_reference_version']=='per_metric_pages_v1_legacy_anchor_retained'


def test_known_v2_archive_is_revalidated_without_rewriting(ledger):
    original=store.put('a','fixture',*fixture())
    change_owned_fixture(ledger,original['id'],lambda x:x.update(source_pins=copy.deepcopy(store.KNOWN_V2_SOURCE_PINS)))
    before=retained(ledger)
    r=store.review('a',original['id'],issuer_id='cninfo:9900002009',symbol='002109',period_end='2025-03-31',
        as_of='2025-06-01T09:30:00+08:00',mode='historical_reconstruction',required_metrics=['parent_net_profit'])
    assert r['receipt']['archive_source_status']=='known_v2_revalidated' and retained(ledger)==before
    assert not r['receipt']['archive_source_current'] and r['metrics']['parent_net_profit']=='-95.00'



def test_absent_and_explicit_null_page_defaults_do_not_conflict_with_retained_facts(ledger):
    m,s=fixture();old=store.put('a','legacy default',m,s)
    for row in m['vintages']:row['metric_source_pages']=None
    current=store.put('a','explicit default metadata',m,s)
    assert current['id']!=old['id']
    assert current['archive_source_current']
    assert len(retained(ledger))==2
