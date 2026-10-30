#!/usr/bin/env python3
"""Offline, registered temporal alignment of frozen curves and index references."""
import argparse,json,hashlib,sys
from datetime import datetime,timezone
from decimal import Decimal
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from deepfocus_api.quant_benchmark import parse_capture,reference_comparison,VARIANTS,stamp
from deepfocus_api.quant_research_engine import digest,validate_dataset
from deepfocus_api.quant_research_analysis import diagnose

p=argparse.ArgumentParser(description=__doc__)
for flag in ('captures','experiments','controls','protocol','output'):p.add_argument('--'+flag,type=Path,required=True)
a=p.parse_args()
if a.output.exists():p.error('Use a new output path; preserve earlier reference reports')
def file_sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
try:
    protocol=json.loads(a.protocol.read_text());manifest=json.loads(a.captures.read_text());control_raw=a.controls.read_bytes();control=json.loads(control_raw)
    pins={f.name:file_sha(f) for f in [Path(__file__),Path(__file__).parents[1]/'backend/deepfocus_api/quant_benchmark.py']}
    if protocol.get('schema')!='benchmark-reference-review-v1' or protocol.get('methods')!=['prior_close_cash_anchor','observed_close_to_close']:
        raise ValueError('Unknown registered benchmark comparison')
    if stamp(protocol['registered_at'])>datetime.now(timezone.utc) or protocol.get('purpose')!='diagnostic_no_gate_replacement':raise ValueError('Invalid reference registration')
    if (protocol['source_pins']!=pins or protocol['capture_manifest_digest']!=digest(manifest) or
        protocol['control_report_sha256']!=hashlib.sha256(control_raw).hexdigest() or protocol['experiments_sha256']!=file_sha(a.experiments)):
        raise ValueError('Frozen benchmark review inputs/source changed')
    if protocol.get('promotion_allowed') is not False or manifest.get('schema')!='csi-reference-captures-v1' or manifest.get('errors'):
        raise ValueError('Reference cannot promote a strategy or accept failed captures')
    if len(manifest['captures'])!=3 or {i['index_code'] for i in manifest['captures']}!=set(VARIANTS):raise ValueError('Expected three unique index variants')
    series={}
    for item in manifest['captures']:
        target=(a.captures.parent/item['file']).resolve()
        if target.parent!=a.captures.parent.resolve() or not target.is_file() or target.stat().st_size>4*1024*1024:raise ValueError('Invalid raw capture path')
        series[item['index_code']]=parse_capture(item,target.read_bytes(),as_of=datetime.now(timezone.utc).isoformat())
    if digest(control['registered_protocol'])!=control['protocol_digest'] or control.get('schema')!='expanded-cash-event-controls-v1':raise ValueError('Invalid frozen event control report')
    exps=json.loads(a.experiments.read_text());rows=[]
    if not isinstance(exps,list) or not 1<=len(exps)<=20:raise ValueError('Expected a nonempty bounded sealed experiment scope')
    if len(control['results'])!=len(exps):raise ValueError('Control experiment count differs')
    for e,c in zip(exps,control['results']):
        diagnose(e)
        if c['experiment']!=e['id'] or c['original_result_digest']!=e['result_digest']:raise ValueError('Control experiment identity differs')
        calendar=validate_dataset(e['dataset']);expected=[r['date'] for r in e['result']['oos']['curve']]
        arms={}
        if set(c['cases'])!={'no_events','original_two_events','expanded_events','expanded_as_recorded'}:raise ValueError('Control report lacks registered arms')
        for name,result in c['cases'].items():
            if [r['date'] for r in result['curve']]!=expected:raise ValueError('Control curve evaluation dates changed')
            arms[name]=reference_comparison(result['curve'],e['request']['capital'],series,calendar,starts_flat=True)
        original=e['dataset']['000300']['bars'];official={r['date']:r for r in series['000300']['rows']};diffs=[]
        for bar in original:
            if bar['date'] not in official:raise ValueError('Official price index lacks sealed history date')
            for key in ('open','high','low','close'):
                if official[bar['date']][key] is None:raise ValueError('Official price reference lacks original OHLC')
                delta=abs(Decimal(str(bar[key]))-Decimal(str(official[bar['date']][key])))
                if delta:diffs.append(delta)
        rows.append({'experiment':e['id'],'original_result_digest':e['result_digest'],
                     'original_gate_preserved':e['result']['gate'],'arms':arms,
                     'sealed_price_source_comparison':{'nonidentical_ohlc_fields':len(diffs),'max_abs_level_difference':str(max(diffs,default=Decimal(0))),
                         'interpretation':'raw precision differences retained; not an overwrite or historical revision certification'}})
    if pins!={f.name:file_sha(f) for f in [Path(__file__),Path(__file__).parents[1]/'backend/deepfocus_api/quant_benchmark.py']}:raise ValueError('Source changed during review')
    report={'schema':protocol['schema'],'registered_protocol':protocol,'protocol_digest':digest(protocol),'results':rows,
            'benchmark_series':{k:{key:s[key] for key in ('code','basis','dividend_tax_assumption','all_ohlc_present','source_sha256')} for k,s in series.items()},
            'original_gates_modified':False,'promotion_allowed':False,'oos_validation':False,'commercial_ready':False}
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('x') as f:json.dump(report,f,ensure_ascii=False,indent=2,allow_nan=False)
    print(json.dumps({'output':str(a.output),'experiments':len(rows),'promotion_allowed':False},ensure_ascii=False))
except (ValueError,KeyError,TypeError,AssertionError,OSError) as exc:
    print(json.dumps({'ok':False,'error':str(exc)},ensure_ascii=False));sys.exit(1)
