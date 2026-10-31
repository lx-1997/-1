#!/usr/bin/env python3
"""One anonymous historical raw-price query for the reviewed CATL action.

Retains SDK-decoded protocol (not exchange-certified wire/PIT). No strategy runs,
provider retries, production writes or changes to old sealed market snapshots.
"""
import argparse,hashlib,importlib.metadata,json,socket,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path

QUERY={'code':'sz.300750','fields':'date,code,open,high,low,close,preclose,volume,amount,adjustflag,tradestatus,pctChg,isST',
       'start_date':'2023-04-24','end_date':'2023-04-28','frequency':'d','adjustflag':'3'}
SOURCE_SHA=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def worker(w):
    if importlib.metadata.version('baostock')!='0.9.1':raise ValueError('Only inspected SDK 0.9.1 supported')
    import baostock as bs
    from baostock.util import socketutil
    socket.setdefaulttimeout(5);package=Path(bs.__file__).resolve().parent
    def pins():return {str(p.relative_to(package)):hashlib.sha256(p.read_bytes()).hexdigest() for p in package.rglob('*.py')}
    original_pins=pins();actual=socketutil.send_msg;captures=[];calls=0;phase='login';rows=[];fields=[];errors=[]
    def retain(msg):
        nonlocal calls
        calls+=1
        if calls>4:raise ValueError('SDK request budget exceeded')
        response=actual(msg)
        if response is not None:
            raw=response.encode('utf-8') if isinstance(response,str) else response
            if not isinstance(raw,bytes) or len(raw)>4*1024**2:raise ValueError('Decoded response budget exceeded')
            name='sdk-response-'+str(len(captures))+'.bin';(w/name).write_bytes(raw)
            captures.append({'file':name,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),
                             'phase':phase,'observed_at':datetime.now(timezone.utc).isoformat()})
        return response
    socketutil.send_msg=retain
    try:
        response=bs.login()
        if response.error_code!='0':raise ValueError('Anonymous login failed')
        phase='query';response=bs.query_history_k_data_plus(**QUERY);fields=response.fields
        while response.error_code=='0' and response.next():
            rows.append(response.get_row_data())
            if len(rows)>10:raise ValueError('Sample row budget exceeded')
        if response.error_code!='0' or not rows:raise ValueError('Failed or empty raw-price query')
        phase='logout';bs.logout()
    except Exception as exc:errors.append({'phase':phase,'type':type(exc).__name__,'error':str(exc)[:200]})
    if pins()!=original_pins or hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=SOURCE_SHA:errors.append({'error':'SourceChanged'})
    report={'schema':'capitalization-raw-price-capture-v1','query':QUERY,'sdk_version':'0.9.1','sdk_source_pins':original_pins,
            'collector_sha256':SOURCE_SHA,'captures':captures,'sdk_requests':calls,'fields':fields,'rows':rows,'errors':errors,
            'capture_layer':'SDK_decoded_protocol_not_wire','retrospective_only':True,'data_rights_verified':False,
            'exchange_certified':False,'strategy_replays':0,'production_writes':0,'promotion_allowed':False}
    (w/'manifest.json').write_text(json.dumps(report,indent=2));print(json.dumps({'rows':len(rows),'errors':len(errors)}),flush=True)
    return 1 if errors else 0


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--workspace',type=Path,required=True);p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS);a=p.parse_args()
    if a.worker:return worker(a.workspace)
    if not a.workspace.is_absolute() or a.workspace.exists() or not a.workspace.parent.is_dir():p.error('NEW absolute isolated workspace required')
    a.workspace.mkdir(mode=0o700);(a.workspace/'scope.json').write_text(json.dumps({'query':QUERY,'collector_sha256':SOURCE_SHA,'maximum_sdk_requests':4,'parent_timeout_seconds':30,'automatic_retries':0},indent=2))
    try:return subprocess.run([sys.executable,str(Path(__file__).resolve()),'--workspace',str(a.workspace),'--worker'],timeout=30).returncode
    except subprocess.TimeoutExpired:
        (a.workspace/'FAILED.json').write_text(json.dumps({'error':'OwnedCaptureExceeded30Seconds','retry_automatically':False}));return 1


if __name__=='__main__':raise SystemExit(main())
