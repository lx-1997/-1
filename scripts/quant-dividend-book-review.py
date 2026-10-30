#!/usr/bin/env python3
"""Reconcile sealed fills through a cash/FIFO/dividend-tax book, offline only."""
import argparse
from collections import defaultdict
from datetime import datetime,timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from deepfocus_api import quant_dividend_book as book
from deepfocus_api.quant_corporate_actions import CashDividend
from deepfocus_api.quant_research_analysis import diagnose,replay_oos
from deepfocus_api.quant_research_engine import digest,validate_dataset

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--experiments',type=Path,required=True)
p.add_argument('--events',type=Path,required=True)
p.add_argument('--evidence-dir',type=Path,required=True)
p.add_argument('--investor',choices=[book.INVESTOR],required=True)
p.add_argument('--mode',choices=['as_recorded','historical_reconstruction'],default='as_recorded')
p.add_argument('--output',type=Path,required=True)
a=p.parse_args()
if a.output.exists():p.error('拒绝覆盖既有研究证据')
project=Path(__file__).resolve().parents[1]
files=[project/'backend/deepfocus_api'/n for n in ('quant_dividend_book.py','quant_corporate_actions.py',
    'quant_research_engine.py','quant_research_analysis.py','ai_fund_ledger.py','ai_fund_policy.py')]+[Path(__file__).resolve()]
pins=lambda:{str(f.relative_to(project)):hashlib.sha256(f.read_bytes()).hexdigest() for f in files}
code_pins=pins();manifest=json.loads(a.events.read_text());events=[CashDividend(**e) for e in manifest['events']]
root=a.evidence_dir.resolve();sources={}
for sha in {e.source_sha256 for e in events}:
    file=(root/manifest['source_files'][sha]).resolve()
    if root not in file.parents or not file.is_file() or file.stat().st_size>10*1024*1024:p.error('原文路径越界、缺失或过大')
    body=file.read_bytes()
    if not body.startswith(b'%PDF-'):p.error('原始证据不是 PDF')
    sources[sha]=body
results=[]
for x in json.loads(a.experiments.read_text()):
    diagnose(x);req=x['request'];ds=x['dataset'];run=replay_oos(ds,req['symbols'],x['result'],req['capital'])
    selected=[e for e in events if e.symbol in req['symbols']];curve={r['date']:r for r in run['curve']}
    sessions=validate_dataset(ds);first,last=min(curve),max(curve);index=sessions.index(first)
    b=book.initial_book(req['capital'],sessions[index-1],investor=a.investor,mode=a.mode)
    by_day=defaultdict(list)
    for f in run['fills']:by_day[f['date']].append(f)
    prices={s:{v['date']:v['close'] for v in ds[s]['bars']} for s in req['symbols']}
    days=sorted(set(list(curve)+[d for e in selected for d in (e.record_date,e.ex_date,e.pay_date) if first<=d<=last]))
    history=[]
    for d in days:
        marks={s:prices[s][d] for s in req['symbols']} if d in sessions else {}
        b=book.apply_day(b,d,by_day[d],marks,selected,sources,sessions,mode=a.mode)
        if d in curve:
            val=b['valuation'];expected=Decimal(str(curve[d]['nav']))+Decimal(b['paid_gross'])+Decimal(val['dividend_receivable'])-Decimal(b['tax_assessed'])
            if abs(Decimal(val['nav_after_assessed_dividend_tax'])-expected)>Decimal('.02'):
                raise ValueError('逐日账务与封存成交无法对账，拒绝输出')
            history.append({'date':d,'sealed_nav':curve[d]['nav'],**val})
    if Decimal(b['tax_settled'])+Decimal(b['valuation']['pending_dividend_tax'])!=Decimal(b['tax_assessed']):
        raise ValueError('已付与待付税款不守恒')
    results.append({'experiment':x['id'],'original_result_digest':x['result_digest'],
        'symbols':req['symbols'],'curve':history,'final_book':b,'daily_reconciliation_passed':True})
if pins()!=code_pins:raise RuntimeError('运行中源码改变，不能输出混合版本证据')
out={'created_at':datetime.now(timezone.utc).isoformat(),'source_pins':code_pins,'policy':book.POLICY,
     'event_manifest_digest':digest(manifest),'mode':a.mode,'results':results,
     'signal_and_risk_replayed':False,'complete_action_coverage':False,'promotion_allowed':False,'commercial_ready':False}
a.output.parent.mkdir(parents=True,exist_ok=True)
with a.output.open('x') as f:json.dump(out,f,ensure_ascii=False,indent=2,allow_nan=False)
print(json.dumps({'output':str(a.output),'results':[{'gross':r['final_book']['paid_gross'],
    'tax_assessed':r['final_book']['tax_assessed'],'tax_settled':r['final_book']['tax_settled']} for r in results],
    'signal_and_risk_replayed':False,'commercial_ready':False},ensure_ascii=False))
