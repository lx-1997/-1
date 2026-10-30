#!/usr/bin/env python3
"""Capture report-period discovery pages for sealed research securities.

This is not an economic event feed and does not create execution events.
Partial/failing captures remain on disk; offline review refuses them.
"""
import argparse,hashlib,json,re,sys
from datetime import datetime,timezone
from pathlib import Path
import httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from deepfocus_api.quant_action_coverage import parse_catalog
from deepfocus_api.quant_research_engine import digest

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--experiments',type=Path,required=True)
p.add_argument('--workspace',type=Path,required=True)
a=p.parse_args()
if not a.workspace.is_absolute() or a.workspace.exists():p.error('Use a new absolute workspace')
experiments=json.loads(a.experiments.read_text());symbols=set()
for exp in experiments:
    if digest(exp['result'])!=exp['result_digest'] or digest(exp['dataset'])!=exp['result']['dataset_digest']:
        p.error('Sealed experiment content modified')
    symbols.update(exp['request']['symbols'])
if not 1<=len(symbols)<=20 or any(not re.fullmatch(r'[036]\d{5}',s) for s in symbols):p.error('Expected 1..20 declared A-share stocks')
a.workspace.mkdir(mode=0o700,parents=True)
captures=[];errors=[];bodies={}
with httpx.Client(trust_env=False,timeout=20) as c:
    for symbol in sorted(symbols):
        try:
            page=1;pages=1
            while page<=pages:
                params={'reportName':'RPT_SHAREBONUS_DET','columns':'ALL','pageSize':'100','pageNumber':str(page),
                        'sortColumns':'EX_DIVIDEND_DATE','sortTypes':'-1','filter':f'(SECURITY_CODE="{symbol}")'}
                raw=bytearray()
                with c.stream('GET','https://datacenter-web.eastmoney.com/api/data/v1/get',params=params,
                              headers={'Referer':'https://data.eastmoney.com/','User-Agent':'Mozilla/5.0'}) as response:
                    url=str(response.url);response.raise_for_status()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw)>2*1024*1024:raise ValueError('Catalog exceeds 2MiB')
                body=bytes(raw);name=f'{symbol}-page{page}.json'
                with (a.workspace/name).open('xb') as f:f.write(body)
                observed=datetime.now(timezone.utc).isoformat();sha=hashlib.sha256(body).hexdigest()
                captures.append({'symbol':symbol,'page':page,'url':url,'observed_at':observed,'sha256':sha,'body_file':name})
                bodies[sha]=body
                payload=json.loads(body);total=(payload.get('result') or {}).get('pages')
                if type(total) is not int or not 0<=total<=20:raise ValueError('Invalid page count')
                if page>1 and pages!=max(1,total):raise ValueError('Catalog pages changed during collection')
                pages=max(1,total);page+=1
            print(json.dumps({'symbol':symbol,'pages_captured':pages}),flush=True)
        except Exception as exc:
            errors.append({'symbol':symbol,'page':page,'error':type(exc).__name__})
    if not errors:
        try:parse_catalog(captures,bodies,as_of=datetime.now(timezone.utc).isoformat())
        except (ValueError,KeyError,TypeError) as exc:errors.append({'error':type(exc).__name__,'stage':'catalog_validation'})
manifest={'schema':'action-catalog-captures-v1','created_at':datetime.now(timezone.utc).isoformat(),
          'captures':captures,'errors':errors,'declared_symbols':sorted(symbols),
          'usage':'internal_research','completeness_attested':False,
          'collector_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
with (a.workspace/'catalog-captures.json').open('x') as f:json.dump(manifest,f,ensure_ascii=False,indent=2)
print(json.dumps({'captured':len(captures),'errors':errors,'economic_coverage_verified':False}))
sys.exit(1 if errors else 0)
