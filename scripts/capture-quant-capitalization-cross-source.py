#!/usr/bin/env python3
"""One bounded Sina raw-daily capture for the already reviewed CATL window.

The endpoint requires a trailing history count: retain its response unchanged,
then compare only the declared five dates. No retry, strategy or production use.
"""
import argparse,hashlib,json,subprocess,sys,urllib.request,urllib.error
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from deepfocus_api import ai_fund_market as market

URL='https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol=sz300750&scale=240&ma=no&datalen=1023'
SOURCE_SHA=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        raise urllib.error.HTTPError(req.full_url,code,'Redirect outside fixed one-request scope',headers,fp)


def worker(w):
    report={'schema':'capitalization-sina-raw-capture-v1','request_url':URL,'collector_sha256':SOURCE_SHA,
            'parser_sha256':hashlib.sha256(Path(market.__file__).read_bytes()).hexdigest(),
            'started_at':datetime.now(timezone.utc).isoformat(),'provider':'sina','symbol':'300750','price_adjustment':'none',
            'transport':'process_only_direct_default_TLS','maximum_response_bytes':market.MAX_BODY,'automatic_retries':0,
            'scope_dates':['2023-04-24','2023-04-25','2023-04-26','2023-04-27','2023-04-28'],
            'errors':[],'strategy_replays':0,'production_writes':0,'data_rights_verified':False,'promotion_allowed':False}
    try:
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        request=urllib.request.Request(URL,headers={'User-Agent':'Mozilla/5.0','Referer':'https://finance.sina.com.cn/'})
        with opener.open(request,timeout=15) as response:
            body=response.read(market.MAX_BODY+1);report.update(status=response.status,final_url=response.url)
        if len(body)>market.MAX_BODY:raise ValueError('Response exceeds fixed 1MiB bound')
        (w/'response.bin').write_bytes(body);observed=datetime.now(timezone.utc)
        report.update(response_sha256=hashlib.sha256(body).hexdigest(),bytes=len(body),observed_at=observed.isoformat())
        snapshot=market.parse(body,'300750','sina',URL,fetched_at=observed)
        (w/'normalized.json').write_text(json.dumps(snapshot,indent=2))
        chosen=[b for b in snapshot['bars'] if b['d'] in report['scope_dates']]
        if [b['d'] for b in chosen]!=report['scope_dates']:raise ValueError('Response does not cover the five declared dates')
        report.update(normalized_rows=len(snapshot['bars']),sample_rows=len(chosen),identity_check=snapshot['meta']['identity_check'])
        if report['parser_sha256']!=hashlib.sha256(Path(market.__file__).read_bytes()).hexdigest() or SOURCE_SHA!=hashlib.sha256(Path(__file__).read_bytes()).hexdigest():raise ValueError('Capture source changed')
    except Exception as exc:report['errors'].append({'type':type(exc).__name__,'error':str(exc)[:250]})
    (w/'manifest.json').write_text(json.dumps(report,indent=2));print(json.dumps({'sample_rows':report.get('sample_rows'),'errors':report['errors']}),flush=True)
    return 1 if report['errors'] else 0


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--workspace',type=Path,required=True);p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS);a=p.parse_args()
    if a.worker:return worker(a.workspace)
    if not a.workspace.is_absolute() or a.workspace.exists() or not a.workspace.parent.is_dir():p.error('NEW absolute isolated workspace required')
    a.workspace.mkdir(mode=0o700)
    (a.workspace/'scope.json').write_text(json.dumps({'request_url':URL,'maximum_http_requests':1,'redirects_allowed':False,'response_limit':market.MAX_BODY,'socket_timeout_seconds':15,'parent_timeout_seconds':25,'collector_sha256':SOURCE_SHA},indent=2))
    try:return subprocess.run([sys.executable,str(Path(__file__).resolve()),'--workspace',str(a.workspace),'--worker'],timeout=25).returncode
    except subprocess.TimeoutExpired:
        (a.workspace/'FAILED.json').write_text(json.dumps({'error':'OwnedSinaCaptureExceeded25Seconds','retry_automatically':False}));return 1


if __name__=='__main__':raise SystemExit(main())
