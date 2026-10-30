#!/usr/bin/env python3
"""Bounded anonymous historical status capture for explicit sealed experiments.

SDK-decoded protocol messages are retained, not claimed original wire bytes or
exchange-certified/PIT status. No historical account, dataset or rule is edited.
"""
import argparse,hashlib,importlib.metadata,json,socket,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path

LOADED_COLLECTOR_SHA=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def worker(spec,work):
    if (spec.get('collector_sha256')!=LOADED_COLLECTOR_SHA
            or hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=LOADED_COLLECTOR_SHA):
        raise ValueError('Collector source changed before worker start')
    import baostock as bs
    from baostock.util import socketutil
    socket.setdefaulttimeout(5);captures=[];results=[];errors=[];request_context=None
    actual=socketutil.send_msg
    def retain(msg):
        response=actual(msg)
        if response is not None:
            body=response.encode('utf-8') if isinstance(response,str) else response
            if len(body)>4*1024*1024:raise ValueError('Decoded response exceeds 4MiB')
            name='sdk-response-'+str(len(captures))+'.bin'
            with (work/name).open('xb') as f:f.write(body)
            captures.append({'file':name,'sha256':hashlib.sha256(body).hexdigest(),
                'bytes':len(body),'observed_at':datetime.now(timezone.utc).isoformat(),
                'request':request_context,'capture_layer':'SDK_decoded_protocol_not_wire',
                'request_sha256':hashlib.sha256(msg.encode()).hexdigest()})
        return response
    socketutil.send_msg=retain
    package=Path(bs.__file__).resolve().parent
    sdk={str(p.relative_to(package)):hashlib.sha256(p.read_bytes()).hexdigest() for p in package.rglob('*.py')}
    try:
        login=bs.login()
        if login.error_code!='0':raise ValueError('Anonymous provider login failed')
        for query in spec['requests']:
            request_context=query;start=len(captures)
            rs=bs.query_history_k_data_plus(**query);rows=[]
            while rs.error_code=='0' and rs.next():
                rows.append(rs.get_row_data())
                if len(rows)>1500:raise ValueError('Status query exceeds 1500 rows')
            result={'request':query,'fields':rs.fields,'rows':rows,'error_code':rs.error_code,
                    'error_msg':rs.error_msg,'capture_indices':list(range(start,len(captures)))}
            results.append(result)
            if rs.error_code!='0' or not rows:errors.append({'stage':'query','code':query['code'],'error':'ProviderFailureOrEmpty'})
            print(json.dumps({'code':query['code'],'rows':len(rows),'error_code':rs.error_code}),flush=True)
        request_context=None;bs.logout()
    except Exception as exc:errors.append({'stage':'worker','type':type(exc).__name__,'message':str(exc)[:150]})
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=LOADED_COLLECTOR_SHA:
        errors.append({'stage':'source_changed','type':'CollectorChangedDuringCapture'})
    if sdk!={str(p.relative_to(package)):hashlib.sha256(p.read_bytes()).hexdigest() for p in package.rglob('*.py')}:
        errors.append({'stage':'source_changed','type':'SDKChangedDuringCapture'})
    out={'schema':'baostock-historical-security-status-v1','created_at':datetime.now(timezone.utc).isoformat(),
         'scope':spec,'sdk_version':importlib.metadata.version('baostock'),'sdk_source_pins':sdk,
         'capture_layer':'SDK_decoded_protocol_not_wire','captures':captures,'results':results,'errors':errors,
         'collector_sha256':LOADED_COLLECTOR_SHA,
         'historical_security_status_certified':False,'preopen_knowledge_verified':False,'data_rights_verified':False}
    with (work/'manifest.json').open('x') as f:json.dump(out,f,ensure_ascii=False,indent=2)
    return 1 if errors else 0


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--experiments',type=Path);p.add_argument('--workspace',type=Path,required=True)
    p.add_argument('--worker-spec',type=Path,help=argparse.SUPPRESS);a=p.parse_args()
    if a.worker_spec:return worker(json.loads(a.worker_spec.read_text()),a.workspace)
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
    from deepfocus_api.quant_research_engine import digest
    from deepfocus_api.quant_calendar import require_aligned
    if not a.experiments or not a.workspace.is_absolute() or a.workspace.exists():p.error('Explicit sealed experiments and new absolute workspace required')
    experiments=json.loads(a.experiments.read_text());intervals={};scope=[]
    for e in experiments:
        if digest(e['result'])!=e['result_digest'] or digest(e['dataset'])!=e['result']['dataset_digest']:raise ValueError('Modified sealed experiment')
        require_aligned(e['dataset'],expected_symbols=list(e['dataset']))
        for symbol in e['request']['symbols']:
            dates=[b['date'] for b in e['dataset'][symbol]['bars']];interval=(dates[0],dates[-1])
            if symbol in intervals and intervals[symbol]!=interval:raise ValueError('Conflicting status scope')
            if len(symbol)!=6 or not symbol.isdecimal() or symbol[0] not in '036':raise ValueError('Unsupported scoped security')
            intervals[symbol]=interval
        scope.append({'experiment':e['id'],'dataset_digest':e['result']['dataset_digest'],'result_digest':e['result_digest']})
    if not 1<=len(intervals)<=10:raise ValueError('Explicit scope requires one to ten stocks')
    requests=[dict(code=('sh.' if s.startswith('6') else 'sz.')+s,fields='date,code,close,volume,tradestatus,isST',
                   start_date=span[0],end_date=span[1],frequency='d',adjustflag='3') for s,span in sorted(intervals.items())]
    # Separate explicitly labelled data-quality sample, not added to experiments.
    requests.append(dict(code='sz.002109',fields='date,code,close,volume,tradestatus,isST',start_date='2026-04-24',end_date='2026-05-08',frequency='d',adjustflag='3'))
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=LOADED_COLLECTOR_SHA:raise ValueError('Collector source changed')
    a.workspace.mkdir(mode=0o700);spec={'experiments':scope,'requests':requests,'out_of_pool_status_sample':'sz.002109','collector_sha256':LOADED_COLLECTOR_SHA}
    specpath=a.workspace/'scope.json'
    with specpath.open('x') as f:json.dump(spec,f,indent=2)
    try:
        proc=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--workspace',str(a.workspace),'--worker-spec',str(specpath)],timeout=90)
        return proc.returncode
    except subprocess.TimeoutExpired:
        with (a.workspace/'FAILED.json').open('x') as f:json.dump({'error':'OwnedCaptureExceeded90Seconds','scope':spec,'retry_automatically':False},f)
        return 1


if __name__=='__main__':raise SystemExit(main())
