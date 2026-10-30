#!/usr/bin/env python3
"""Offline, isolated journal exercise on a scoped read-only production snapshot.

This is NOT an original production tick replay: news/fundamental/flow/regime
inputs missing in the snapshot stay declared unavailable. Always trade=False.
No network, no production database, no original-trade/history reconstruction.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import sys

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--snapshot',type=Path,required=True)
p.add_argument('--workspace',type=Path,required=True)
a=p.parse_args()
if not a.workspace.is_absolute():p.error('Use an absolute new workspace')
if a.workspace.exists():p.error('Workspace must not exist; preserve previous evidence')
if a.snapshot.stat().st_size>4*1024*1024:p.error('Snapshot exceeds 4MiB')
raw=a.snapshot.read_bytes();snapshot=json.loads(raw)
if snapshot.get('read_only') is not True:p.error('A scoped read-only snapshot is required')
a.workspace.mkdir(parents=True,mode=0o700)
os.environ['DEEPFOCUS_DATA_DIR']=str(a.workspace)
os.environ['DEEPFOCUS_AIFUND_DB_PATH']=str(a.workspace/'paper.sqlite3')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from deepfocus_api import ai_fund as af,ai_fund_audit as audit

def no_network(*args,**kwargs):raise RuntimeError('Network is disabled in isolated observation exercise')
socket.socket.connect=no_network
context={'scenario':snapshot['scope'],'snapshot_sha256':hashlib.sha256(raw).hexdigest(),
         'harness_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
         'unavailable':snapshot['unavailable'],'production_modified':False,'original_tick_replayed':False}
original_append=audit.append
def marked_append(conn,identity,fund_id,ts,status,sections):
    return original_append(conn,identity,fund_id,ts,status,{**sections,'capture_context':context})
audit.append=marked_append
# Explicit missing inputs, never infer that no historical information existed.
af._our_content=lambda name:[]
af._sentiment=lambda items:None
af._market_regime_now=lambda:{'regime':None,'quality':'unavailable_in_snapshot'}
af.emotion_cycle.current_cycle=lambda **kw:{'stage':'neutral','quality':'unavailable_placeholder_for_observation_only'}
af._market_data=lambda codes,priority=None:{s:{'ohlc':snapshot['klines'].get(s,[])} for s in codes}
def quote(code):
    mark=snapshot['marks'].get(code)
    return {'latest':mark['price'],'time':mark['ts'],'changeRatio':None,
            'source':'retained_production_mark_for_observation_only'} if mark else None
af._quote=quote
af._deposit_musing=lambda *args:None
# No ancillary delivery should run: imported state excludes all original outbox/trades.
af.data_store.record_confirmed=no_network
with af._connect() as c:pass  # create only the explicitly isolated DB path
af.init_ai_fund_db()
with af._connect() as c:
    c.execute('BEGIN IMMEDIATE')
    for fund,facts in snapshot['funds'].items():
        st=facts['state']
        c.execute('UPDATE aif_state SET cash=?,started_at=?,started_nav=?,last_tick_at=? WHERE fund_id=?',
                  (st['cash'],st['started_at'],st['started_nav'],st['last_tick_at'],fund))
        for pos in facts['positions']:
            columns=('fund_id','symbol','name','qty','avg_cost','opened_at','updated_at','high_water')
            c.execute('INSERT INTO aif_position VALUES(?,?,?,?,?,?,?,?)',tuple(pos[k] for k in columns))
        c.execute('INSERT OR REPLACE INTO aif_risk_state VALUES(?,?,?)',(fund,facts['risk']['peak'],facts['risk']['worst']))
    for symbol,mark in snapshot['marks'].items():c.execute('INSERT INTO aif_marks VALUES(?,?,?)',(symbol,mark['price'],mark['ts']))
    c.commit()
results=[]
for cfg in af.ROSTER:
    result=af.run_tick(trade=False,cfg=cfg)
    assert result['traded']==[]
    with af._connect() as c:
        record=audit.load(c,cfg.fund_id,result['audit']['id'])
        proof=audit.verify_analysis(record)
        chain=audit.verify_chain(c,cfg.fund_id)
    results.append({'fund_id':cfg.fund_id,'audit':result['audit'],'status':record['manifest']['status'],
                    'candidate_count':len(record['sections']['candidates']),'score_verification':proof,'chain':chain})
with af._connect() as c:
    assert c.execute('SELECT count(*) FROM aif_trade').fetchone()[0]==0
    assert c.execute('SELECT count(*) FROM aif_trade_outbox').fetchone()[0]==0
report={'context':context,'results':results,'trade_count':0,'network_enabled':False}
with (a.workspace/'observation-report.json').open('x') as f:json.dump(report,f,ensure_ascii=False,indent=2)
print(json.dumps({'workspace':str(a.workspace),'funds':len(results),'analyses_matched':sum(r['score_verification']['analysis_count'] for r in results),
                  'trade_count':0,'original_tick_replayed':False,'production_modified':False},ensure_ascii=False))
