"""Owner-scoped TTM from one verified immutable receipt and SQLite snapshot.

Kept separate from ingestion so a derived-query upgrade does not relabel or
invalidate already recorded source archives. No public API or writable reads.
"""
import copy
import hashlib
from pathlib import Path
import sqlite3

from . import quant_financial_store as store,quant_financial_flows as flows
from .quant_corporate_actions import timestamp

VERSION='owned-financial-ttm-query-v1'


def _pins():
    return {**store.LOADED_PINS,**flows.source_pins(),
            'quant_financial_ttm_store.py':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


LOADED_PINS=_pins()


def require_current_source():
    store.require_current_source();flows.require_current_source()
    if _pins()!=LOADED_PINS:raise ValueError('Loaded owned TTM query source changed')


def review(owner,identity,*,issuer_id,symbol,period_end,as_of,mode,required_metrics):
    require_current_source()
    request=copy.deepcopy(dict(issuer_id=issuer_id,symbol=symbol,period_end=period_end,
                              as_of=as_of,mode=mode,required_metrics=required_metrics))
    # Validate the quarter schedule too, even if storage visibility will block it.
    flows.components(period_end)
    path=store.jobs.db.data_path('.quant_research.sqlite3','DEEPFOCUS_QUANT_RESEARCH_DB_PATH').resolve()
    c=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2);c.row_factory=sqlite3.Row
    try:
        c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
        row=c.execute('SELECT * FROM qr_financial_bundle WHERE owner=? AND id=?',(owner,identity)).fetchone()
        if row is None:raise LookupError('Financial receipt not found')
        value,sources=store.verify_row(c,row)
        # Reuse the source reader's identity/mode/metrics/future-clock checks;
        # discard its private single-panel value. TTM is never computed early.
        store.vintages.select_panel(value['effective_manifest'],sources,**request)
        blocked=mode=='as_recorded' and timestamp(as_of)<timestamp(value['stored_visible_at'])
        receipt=store._summary(row,value)
        if blocked:
            # Neither component amounts/documents nor future packet labels/counts
            # are a historical input before this conservative storage boundary.
            receipt={k:receipt[k] for k in ('id','recorded_at','stored_visible_at','visibility_policy',
                'validator_version','archive_source_status','archive_source_current','archive_rewritten')}
        derived=None if blocked else flows.derive_ttm(value['effective_manifest'],sources,**request)
        result={'schema':VERSION,'request':request,'receipt':receipt,'stored_visibility_policy':store.VISIBILITY,
                'source_pins':dict(LOADED_PINS),'status':'unavailable' if blocked else derived['status'],
                'availability_reason':('receipt_not_yet_visible' if blocked else 'derived_from_verified_receipt'
                    if derived['status']=='arithmetic_available' else 'financial_components_not_available'),
                'metrics':None if blocked else derived['metrics'],'result':derived,
                'uses_immutable_store_recording_clock':True,'stored_visibility_applied':mode=='as_recorded',
                'single_readonly_receipt_snapshot':True,'declared_operator_clocks_are_storage_proof':False,
                'externally_attested_time':False,'historical_pit_feed_certified':False,
                'accounting_comparability_verified':False,'usable_for_strategy':False,
                'promotion_allowed':False,'commercial_ready':False}
        require_current_source()
        return result
    finally:c.close()
