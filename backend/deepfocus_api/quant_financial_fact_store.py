"""Typed supplemental extractions, versioned beside unchanged financial receipts.

Operator-reviewed facts only. A new extraction has its own actual storage clock;
source disclosure revisions and extraction corrections are separate histories.
"""
import copy
from datetime import date,datetime,time,timedelta,timezone
import hashlib,json,re,sqlite3,time as monotonic,uuid
from pathlib import Path

from . import quant_financial_store as base
from .quant_corporate_actions import BJ,timestamp
from .quant_research_engine import digest

VERSION='qr-typed-financial-extraction-v1'
# Exact typed extraction implementation before the org identity upgrade.
KNOWN_V1_SOURCE_PINS={**base.KNOWN_V3_SOURCE_PINS,
    'quant_financial_fact_store.py':'9f5f4c27f77179ff66bc1c4085c5da4c8e273923eaf943713aaa01d230f3ce55'}
KNOWN_V2_SOURCE_PINS={**base.KNOWN_V4_SOURCE_PINS,
    'quant_financial_fact_store.py':'7966de573e89c1e80252843633d9dd62e09269ef99cf67fcc75d818056dbd972'}
# Exact pre-capacity-upgrade typed source, with both exact PDF URL cases.
KNOWN_V3_SOURCE_PINS={'quant_financial_vintages.py': '16481fb39bd8ec6a49b7109b3aa85f50e189db8e93a5969aceaab1091ab43c7c', 'quant_corporate_actions.py': '89fadf0d5369ef7d7c8a0aeec368124cef8eaca5dcb5371298c4204206ee3996', 'quant_research_engine.py': 'b3852688c331aebfe06515f54fc2b7a35191e90a587697f4424e771eea9893d5', 'quant_financial_store.py': 'd387866948aa5e82180aec2f1ffd409d789d63aaebe53400f53d48cbb35cde8f', 'quant_financial_fact_store.py': '7d565efb59bc2e418980977adb5eab168427229efca372d60b026e3dfcf5df7a'}
VALIDATOR_VERSION='qr-typed-source-validator-v4'
FLOW_METRICS={'consolidated_net_profit','minority_net_profit','cashflow_asset_impairment',
 'cashflow_fixed_asset_depreciation','cashflow_right_of_use_depreciation','cashflow_intangible_amortization',
 'cashflow_deferred_expense_amortization','cashflow_long_asset_disposal_loss','cashflow_scrapping_loss',
 'cashflow_fair_value_loss','cashflow_finance_expense','cashflow_investment_loss',
 'cashflow_deferred_tax_asset_decrease','cashflow_deferred_tax_liability_increase',
 'cashflow_inventory_decrease','cashflow_operating_receivable_decrease',
 'cashflow_operating_payable_increase','cashflow_other'}
STOCK_METRICS={'total_assets'}
METRICS=FLOW_METRICS|STOCK_METRICS
COLUMNS={'current_period','previous_year_end','previous_year_end_adjusted'}
MAX_PACKETS=100
MAX_OWNER_BYTES=10*1024*1024


def _pins():
    return {**base.LOADED_PINS,'quant_financial_fact_store.py':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


LOADED_PINS=_pins()


def require_current_source():
    base.require_current_source()
    if _pins()!=LOADED_PINS:raise ValueError('Loaded typed financial source changed')


def source_profile(value):
    archived=value.get('source_pins')
    if archived==LOADED_PINS:return 'current'
    if archived==KNOWN_V1_SOURCE_PINS:return 'known_v1_revalidated'
    if archived==KNOWN_V2_SOURCE_PINS:return 'known_v2_revalidated'
    if archived==KNOWN_V3_SOURCE_PINS:return 'known_v3_revalidated'
    raise ValueError('Typed extraction source identity is unknown')


def _now():return datetime.now(timezone.utc)


def _key(f):return (f['document_id'],f['metric'],f['column'])


def _boundary(stamp):
    return datetime.combine(timestamp(stamp).astimezone(BJ).date()+timedelta(days=1),time(),BJ).isoformat()


def validate_facts(facts,parent):
    if not isinstance(facts,list) or len(facts)>1000:raise ValueError('Bounded explicit fact snapshot required')
    docs={r['document_id']:r for r in parent['effective_manifest']['vintages']};seen=set()
    if len(docs)!=len(parent['effective_manifest']['vintages']):raise ValueError('Ambiguous parent document binding')
    for f in facts:
        if not isinstance(f,dict) or set(f)!={'document_id','source_sha256','metric','column','basis','period_start','period_end','value','source_page'}:
            raise ValueError('Explicit typed fact fields required')
        if (not isinstance(f['document_id'],str) or f['document_id'] not in docs
            or not isinstance(f['metric'],str) or f['metric'] not in METRICS
            or not isinstance(f['column'],str) or f['column'] not in COLUMNS):raise ValueError('Unknown document/metric/column')
        r=docs[f['document_id']]
        if f['source_sha256']!=r['source_sha256']:raise ValueError('Fact source differs from parent document')
        if type(f['source_page']) is not int or not 1<=f['source_page']<=1000:raise ValueError('Explicit bounded physical page required')
        if _key(f) in seen:raise ValueError('Duplicate fact key')
        seen.add(_key(f))
        if f['metric'] in FLOW_METRICS:
            if (f['basis']!='year_to_date' or f['column']!='current_period'
                or f['period_start']!=r['period_start'] or f['period_end']!=r['period_end']):
                raise ValueError('Flow facts require the parent current YTD interval')
        else:
            end=(r['period_end'] if f['column']=='current_period' else str(date.fromisoformat(r['period_end']).year-1)+'-12-31')
            if f['basis']!='point_in_time_stock' or f['period_start'] is not None or f['period_end']!=end:
                raise ValueError('Stock facts require a column-specific point date, never a flow interval')
        v=f['value']
        if v is not None:
            if not isinstance(v,str) or not re.fullmatch(r'-?(?:0|[1-9]\d{0,18})(?:\.\d{1,2})?',v):raise ValueError('Plain CNY amount or explicit null required')
            parts=v.lstrip('-').split('.');cents=int(parts[0])*100+int((parts[1] if len(parts)==2 else '').ljust(2,'0'))
            if cents>10**20:raise ValueError('CNY amount exceeds unchanged 1e18 bound')
            if f['metric']=='total_assets' and v.startswith('-') and cents:raise ValueError('Negative total assets unsupported')
    return {_key(f):f for f in facts}


def _validate_revision(incoming,previous):
    facts=incoming['facts'];notes=incoming['correction_notes']
    if not isinstance(notes,list) or len(notes)>1000:raise ValueError('Explicit correction-note list required')
    notes_by_key={}
    for n in notes:
        if (not isinstance(n,dict) or set(n)!={'document_id','metric','column','reason'}
            or any(not isinstance(n[k],str) for k in n)
            or not 8<=len(n['reason'].strip())<=500):raise ValueError('Bounded explicit extraction correction reason required')
        key=_key(n)
        if key in notes_by_key:raise ValueError('Duplicate correction note')
        notes_by_key[key]=n
    if previous is None:
        if not facts or notes or incoming['supersedes_packet_id'] is not None:raise ValueError('Root extraction needs facts and no correction predecessor')
    else:
        before={_key(f):f for f in previous['input']['facts']};after={_key(f):f for f in facts}
        changed={k for k in before if before[k]!=after.get(k)}
        if set(notes_by_key)!=changed:raise ValueError('Every changed/withdrawn old fact needs exactly one correction note')
        if before==after:raise ValueError('New extraction must add, correct or withdraw facts')


def _parent(c,owner,identity,cache):
    key=('parent',owner,identity)
    if key not in cache:
        row=c.execute('SELECT * FROM qr_financial_bundle WHERE owner=? AND id=?',(owner,identity)).fetchone()
        if row is None:raise LookupError('Financial parent not found')
        cache[key]=base.verify_row(c,row)[0]
    return cache[key]


def verify_row(c,row,cache=None):
    require_current_source();cache={} if cache is None else cache
    key=('packet',row['owner'],row['id'])
    if key in cache:return cache[key]
    v=json.loads(row['payload']);incoming=v['input']
    profile=source_profile(v)
    if (v.get('schema')!=VERSION or digest(v)!=row['fingerprint']
        or digest(incoming)!=row['input_hash'] or incoming['parent_id']!=row['parent_id']
        or v['recorded_at']!=row['created'] or v['stored_visible_at']!=_boundary(row['created'])
        or type(row['revision']) is not int or not 1<=row['revision']<=MAX_PACKETS
        or v['revision']!=row['revision'] or timestamp(row['created'])>_now()):raise ValueError('Typed extraction identity/clock/source changed')
    parent=_parent(c,row['owner'],row['parent_id'],cache)
    if profile=='known_v1_revalidated' and any(not re.fullmatch(r'cninfo:[0-9]{5,20}',r['issuer_id'])
                                  for r in parent['effective_manifest']['vintages']):
        raise ValueError('Legacy typed source requires numeric issuer identity')
    if profile not in {'current','known_v3_revalidated'} and any(not r['source_url'].endswith('.PDF') for r in parent['effective_manifest']['vintages']):
        raise ValueError('Legacy typed source requires uppercase PDF URL')
    if v['parent_fingerprint']!=digest(parent) or timestamp(row['created'])<timestamp(parent['recorded_at']):raise ValueError('Parent provenance or clock changed')
    validate_facts(incoming['facts'],parent)
    prior=None;previous_id=incoming['supersedes_packet_id']
    if row['revision']==1:
        if previous_id is not None:raise ValueError('Root predecessor invalid')
    else:
        prior_row=c.execute('SELECT * FROM qr_financial_fact_packet WHERE owner=? AND id=? AND parent_id=?',
                            (row['owner'],previous_id,row['parent_id'])).fetchone()
        if prior_row is None or prior_row['revision']!=row['revision']-1:raise ValueError('Extraction predecessor missing or cross-parent')
        prior=verify_row(c,prior_row,cache)
        if timestamp(prior['recorded_at'])>timestamp(row['created']):raise ValueError('Extraction clock reversed')
    _validate_revision(incoming,prior)
    if not isinstance(incoming['label'],str) or not 1<=len(incoming['label'].strip())<=80:raise ValueError('Bounded extraction label required')
    cache[key]=v;return v


def _summary(row,v):
    return {'id':row['id'],'parent_id':row['parent_id'],'revision':row['revision'],
            'recorded_at':row['created'],'stored_visible_at':v['stored_visible_at'],
            'fingerprint':row['fingerprint'],'parent_fingerprint':v['parent_fingerprint'],
            'supersedes_packet_id':v['input']['supersedes_packet_id'],'archive_rewritten':False,
            'parent_rewritten':False,'externally_attested_time':False,'promotion_allowed':False,
            'validator_version':VALIDATOR_VERSION,'archive_source_status':source_profile(v),
            'archive_source_current':v['source_pins']==LOADED_PINS,
            'archive_source_digest':digest(v['source_pins']),'validated_source_digest':digest(LOADED_PINS)}


def put(owner,parent_id,label,facts,*,supersedes_packet_id=None,correction_notes=None):
    require_current_source()
    if not isinstance(owner,str) or not 1<=len(owner.strip())<=200:raise ValueError('Bounded owner required')
    incoming=copy.deepcopy({'parent_id':parent_id,'label':label,'facts':facts,'supersedes_packet_id':supersedes_packet_id,
                           'correction_notes':[] if correction_notes is None else correction_notes})
    if not isinstance(label,str) or not 1<=len(label.strip())<=80:raise ValueError('Bounded extraction label required')
    if len(json.dumps(incoming,allow_nan=False).encode())>2*1024*1024:raise ValueError('Extraction metadata exceeds 2MiB')
    ih=digest(incoming)
    with base.connect() as c:
        c.execute('CREATE TABLE IF NOT EXISTS qr_financial_fact_packet(id TEXT PRIMARY KEY,owner TEXT NOT NULL,parent_id TEXT NOT NULL,revision INTEGER NOT NULL,created TEXT NOT NULL,input_hash TEXT NOT NULL,fingerprint TEXT NOT NULL,payload TEXT NOT NULL,UNIQUE(owner,input_hash),UNIQUE(owner,parent_id,revision))')
        for op in ('UPDATE','DELETE'):
            c.execute(f"CREATE TRIGGER IF NOT EXISTS qr_financial_fact_packet_{op.lower()} BEFORE {op} ON qr_financial_fact_packet BEGIN SELECT RAISE(ABORT,'immutable financial extraction'); END")
        c.commit();c.execute('BEGIN IMMEDIATE');started=monotonic.monotonic();stamp=_now().isoformat();cache={}
        parent=_parent(c,owner,parent_id,cache);validate_facts(incoming['facts'],parent)
        old=c.execute('SELECT * FROM qr_financial_fact_packet WHERE owner=? AND input_hash=?',(owner,ih)).fetchone()
        if old is not None:return _summary(old,verify_row(c,old,cache))
        last=c.execute('SELECT * FROM qr_financial_fact_packet WHERE owner=? AND parent_id=? ORDER BY revision DESC LIMIT 1',(owner,parent_id)).fetchone()
        if (last is None and supersedes_packet_id is not None) or (last is not None and last['id']!=supersedes_packet_id):raise ValueError('Explicit latest extraction predecessor required; branching forbidden')
        previous=None if last is None else verify_row(c,last,cache)
        _validate_revision(incoming,previous)
        size=c.execute('SELECT count(*),coalesce(sum(length(CAST(payload AS BLOB))),0) FROM qr_financial_fact_packet WHERE owner=?',(owner,)).fetchone()
        if size[0]>=MAX_PACKETS:raise RuntimeError('Extraction count quota exhausted')
        revision=1 if last is None else last['revision']+1
        v={'schema':VERSION,'input':incoming,'revision':revision,'recorded_at':stamp,'stored_visible_at':_boundary(stamp),
           'parent_fingerprint':digest(parent),'source_pins':LOADED_PINS}
        payload=json.dumps(v,ensure_ascii=False,allow_nan=False)
        if size[1]+len(payload.encode())>MAX_OWNER_BYTES:raise RuntimeError('Extraction metadata quota exhausted')
        identity=uuid.uuid4().hex
        c.execute('INSERT INTO qr_financial_fact_packet VALUES(?,?,?,?,?,?,?,?)',(identity,owner,parent_id,revision,stamp,ih,digest(v),payload))
        row=c.execute('SELECT * FROM qr_financial_fact_packet WHERE id=?',(identity,)).fetchone();verify_row(c,row,cache);require_current_source()
        now=_now()
        if now<timestamp(stamp) or now>=timestamp(v['stored_visible_at']) or monotonic.monotonic()-started>60:raise ValueError('Extraction transaction clock or budget exceeded')
        c.commit();return _summary(row,v)


def review(owner,identity,*,issuer_id,symbol,report_period_end,metric,column,as_of,mode):
    require_current_source()
    if not isinstance(metric,str) or metric not in METRICS or column not in COLUMNS:raise ValueError('Explicit supported typed metric/column required')
    if metric in FLOW_METRICS and column!='current_period':raise ValueError('YTD flow requires current period column')
    day=date.fromisoformat(report_period_end)
    if day.isoformat()!=report_period_end or report_period_end[-5:] not in {'03-31','06-30','09-30','12-31'}:raise ValueError('Explicit report quarter required')
    path=base.jobs.db.data_path('.quant_research.sqlite3','DEEPFOCUS_QUANT_RESEARCH_DB_PATH').resolve()
    c=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2);c.row_factory=sqlite3.Row
    try:
        c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
        row=c.execute('SELECT * FROM qr_financial_fact_packet WHERE owner=? AND id=?',(owner,identity)).fetchone()
        if row is None:raise LookupError('Typed extraction not found')
        cache={};v=verify_row(c,row,cache);parent=_parent(c,owner,row['parent_id'],cache)
        # Original selector validates identity/mode/future clock and chooses the report
        # revision before looking for a new metric. No older complete-document fill.
        sources={}
        for sha in parent['effective_manifest']['source_files']:
            sources[sha]=c.execute('SELECT body FROM qr_financial_source WHERE owner=? AND sha=?',(owner,sha)).fetchone()['body']
        selection=base.vintages.select_panel(parent['effective_manifest'],sources,issuer_id=issuer_id,symbol=symbol,
                    period_end=report_period_end,as_of=as_of,mode=mode,required_metrics=['parent_net_profit'])
        blocked=mode=='as_recorded' and timestamp(as_of)<max(timestamp(parent['stored_visible_at']),timestamp(v['stored_visible_at']))
        panel=selection['panel'] if selection['status']!='unavailable' else None
        fact=None
        if not blocked and panel is not None:
            fact=next((f for f in v['input']['facts'] if _key(f)==(panel['document_id'],metric,column)),None)
        result={'schema':VERSION,'request':{'issuer_id':issuer_id,'symbol':symbol,'report_period_end':report_period_end,
                'metric':metric,'column':column,'as_of':as_of,'mode':mode},'receipt':_summary(row,v),
                'status':'available' if fact is not None and fact['value'] is not None else 'unavailable',
                'availability_reason':'receipt_not_yet_visible' if blocked else 'report_not_yet_visible' if panel is None
                    else 'missing_selected_document_fact' if fact is None else 'reported_blank' if fact['value'] is None else 'reviewed_fact_available',
                'fact':copy.deepcopy(fact),'uses_both_immutable_recording_boundaries':True,
                'source_pins':dict(LOADED_PINS),'single_readonly_snapshot':True,
                'extraction_correction_is_source_revision':False,'automatic_pdf_fact_verification':False,
                'accounting_comparability_verified':False,'historical_pit_feed_certified':False,
                'usable_for_strategy':False,'promotion_allowed':False,'commercial_ready':False}
        require_current_source();return result
    finally:c.close()
