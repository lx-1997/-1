"""Private worker; uses only current verified code and an isolated database.

Never executes archived source. Network, narrative/model calls, and production
DB access are absent. The actual financial engine is reused, not reimplemented.
"""
import copy
from datetime import datetime
import json
import os
from pathlib import Path
import socket
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from deepfocus_api import ai_fund as af,ai_fund_audit as audit
from deepfocus_api.ai_fund_replay import validate


def financial_checks(s):
    """Record-level identities independent of engine fill/cash/basis helpers.

    REAL storage is compared within 1e-8 yuan; cent identities stay exact.
    Unknown legacy basis remains unknown, never a reconstructed certificate.
    """
    from decimal import Decimal,ROUND_HALF_UP
    D=lambda v:Decimal(str(v))
    near=lambda a,b:abs(D(a)-D(b))<=D('0.00000001')
    fills=s.get('fills',[]);before=s['before'];after=s['after']
    quantities={p['symbol']:D(p['qty']) for p in before['positions']}
    bases=dict(before['position_basis']);cash=D(before['state']['cash'])
    checks={'recorded_price_quantity_matches_amount':True,'new_fill_price_amount_cent_aligned':True,
            'assumed_fee_matches':True,'known_cost_basis_and_realized_pnl_match':True}
    unknown=0
    for f in fills:
        code=f['symbol'];amount=D(f['amount']);fee=D(f['fee']);qty=D(f['qty'])
        checks['recorded_price_quantity_matches_amount'] &= D(f['price'])*qty==amount
        checks['new_fill_price_amount_cent_aligned'] &= D(f['price'])%D('.01')==0 and amount%D('.01')==0
        expected=(max(D('5'),amount*D('.00025'))+amount*D('.00001')+
                  (amount*D('.0005') if f['side']=='sell' else D(0))).quantize(D('.01'),rounding=ROUND_HALF_UP)
        checks['assumed_fee_matches'] &= fee==expected
        if f['side']=='buy':
            cash-=amount+fee;quantities[code]=quantities.get(code,D(0))+qty;bases[code]=str(amount+fee)
        else:
            cash+=amount-fee;quantities[code]=quantities.get(code,D(0))-qty
            basis=bases.pop(code,None)
            if basis is None:unknown+=1
            else:checks['known_cost_basis_and_realized_pnl_match'] &= near(f['realized_pnl'],amount-fee-D(basis))
    checks['cash_conserved']=near(after['state']['cash'],cash)
    checks['position_quantities_conserved']={k:v for k,v in quantities.items() if v}=={p['symbol']:D(p['qty']) for p in after['positions']}
    checks['position_basis_preserved']=bases==after['position_basis']
    nav=s.get('execution_observations',{}).get('nav')
    if nav:checks['nav_cash_market_value_conserved']=near(nav['nav'],D(nav['cash'])+D(nav['market_value'])) and near(nav['cash'],cash)
    return {**checks,'passed':all(checks.values()),'legacy_cost_basis_unverified':unknown,
            'opening_position_basis_complete':all(v is not None for v in before['position_basis'].values())}


def replay(record):
    validate(record);s=record['sections'];inp=s['inputs'];fund=record['manifest']['fund_id']
    cfg=af.AgentConfig(**s['configuration'])
    if cfg.fund_id!=fund:raise audit.AuditError('Execution frame owner mismatch')
    db_path=Path(os.environ['DEEPFOCUS_AIFUND_DB_PATH']).resolve()
    if db_path.parent!=Path.cwd().resolve() or db_path.exists():raise audit.AuditError('Replay requires a new isolated database')
    network_attempts=[]
    def offline(*args,**kwargs):network_attempts.append(1);raise RuntimeError('Network forbidden in financial replay')
    socket.socket.connect=offline;socket.socket.connect_ex=offline;socket.create_connection=offline
    af.init_ai_fund_db()
    with af._connect() as c:
        c.execute('BEGIN IMMEDIATE')
        st=s['before']['state']
        c.execute('UPDATE aif_state SET cash=?,started_nav=?,last_tick_at=? WHERE fund_id=?',(st['cash'],st['started_nav'],st['last_tick_at'],fund))
        for pos in s['before']['positions']:
            keys=('fund_id','symbol','name','qty','avg_cost','opened_at','updated_at','high_water')
            c.execute('INSERT INTO aif_position VALUES(?,?,?,?,?,?,?,?)',tuple(pos[k] for k in keys))
        for symbol,amount in s['before']['position_basis'].items():
            if amount is not None:c.execute('INSERT INTO aif_position_basis VALUES(?,?,?)',(fund,symbol,amount))
        if s['before']['risk']:
            r=s['before']['risk'];c.execute('INSERT OR REPLACE INTO aif_risk_state VALUES(?,?,?)',(fund,r['peak'],r['worst']))
        for symbol,mark in s['marks_before'].items():
            if mark:c.execute('INSERT INTO aif_marks VALUES(?,?,?)',(symbol,mark['price'],mark['ts']))
        for row in audit.history_rows(s):
            if row['fund_id']!=fund:raise audit.AuditError('Financial history owner mismatch')
            keys=list(audit.HISTORY_COLUMNS)
            c.execute('INSERT INTO aif_trade('+','.join(keys)+') VALUES('+','.join('?' for _ in keys)+')',tuple(row[k] for k in keys))
        if s['daily_before']:
            d=s['daily_before'];c.execute('INSERT INTO aif_nav_daily VALUES(?,?,?,?)',(fund,d['date'],d['ts'],d['nav']))
        c.commit()
    # Startup/memory/narrative are not financial inputs while learning stays disabled.
    af.init_ai_fund_db=lambda:None
    af._record_trade_memory=lambda *a,**k:None
    af._upsert_thesis=lambda *a,**k:None
    af._deposit_musing=lambda *a,**k:None
    af._llm_narratives=lambda *a,**k:{}
    af._run_debate=lambda *a,**k:None
    af.data_store.record_confirmed=lambda *a,**k:True
    active=[datetime.fromisoformat(s['entry_at'])];current=[None]
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return active[0].astimezone(tz) if tz else active[0].replace(tzinfo=None)
    af.datetime=Clock
    def execution_time():active[0]=datetime.fromisoformat(s['executed_at']);return s['executed_at']
    af.utc_now_iso=execution_time
    af._universe=lambda *a,**k:{code:inp['universe'][code] for code in inp['universe_order']}
    af._market_data=lambda *a,**k:copy.deepcopy(s['market_data'])
    af._market_regime_now=lambda:copy.deepcopy(inp['regime'])
    af.emotion_cycle.current_cycle=lambda **k:copy.deepcopy(inp['cycle'])
    def quote(code):
        current[0]=code
        if code in inp['analysis_at']:active[0]=datetime.fromisoformat(inp['analysis_at'][code])
        return copy.deepcopy(inp['quotes'].get(code))
    af._quote=quote
    af._our_content=lambda name:copy.deepcopy(inp['news'].get(current[0],[]))
    original=af._analyze
    def analyze(*args,**kwargs):
        code=current[0]
        kwargs.update(as_of=datetime.fromisoformat(inp['analysis_at'][code]),
                      sentiment_override=inp['analyses'][code]['sentiment_input'])
        return original(*args,**kwargs)
    af._analyze=analyze
    trace={'id':record['manifest']['id'],'started_at':record['manifest']['ts'],'sealed':False,'sections':{
        'configuration':copy.deepcopy(s['configuration']),'requested_trade':s['requested_trade'],'inputs':{},'candidates':{}}}
    result=af._run_tick(s['requested_trade'],cfg,trace)
    with af._connect() as c:actual=audit.load(c,fund,result['audit']['id'])
    actual_s=actual['sections'];checks={}
    for key in ('after','fills','risk_control_after','candidates','execution_observations','effective_trade','entry_session_allowed'):
        checks[key]=audit.digest(s.get(key))==audit.digest(actual_s.get(key))
    for key in ('ranked_candidates','execution_quote_accepted','risk_control_before','daily_buys_before','performance_brake','execution_trading_day','recent_trades'):
        checks['inputs.'+key]=audit.digest(inp.get(key))==audit.digest(actual_s['inputs'].get(key))
    checks['status']=record['manifest']['status']==actual['manifest']['status']
    checks['network_unused']=not network_attempts
    # Independent record-level check: repeatability alone must not certify bad accounting.
    fills=s.get('fills',[])
    financial=financial_checks(s)
    return {'ok':True,'execution_replayed':True,'matched':all(checks.values()),'comparisons':checks,
            'accepted':all(checks.values()) and financial['passed'],
            'fills_reproduced':len(fills),'financial_checks':financial,
            'oos_validation':False,'production_modified':False,'network_attempts':len(network_attempts),
            'scope':'ranking_risk_orders_cash_positions_marks_nav_outbox','excluded':'ancillary_memory_and_narrative'}


if __name__=='__main__':
    target=Path(sys.argv[2])
    try:
        source=Path(sys.argv[1])
        if source.stat().st_size>25*1024*1024:raise audit.AuditError('Execution record too large')
        result=replay(json.loads(source.read_text()))
        target.write_text(json.dumps(result,ensure_ascii=False));sys.exit(0)
    except Exception as exc:
        result={'ok':False,'error':str(exc) if isinstance(exc,audit.AuditError) else 'Isolated financial replay failed: '+type(exc).__name__}
        target.write_text(json.dumps(result,ensure_ascii=False));sys.exit(1)
