#!/usr/bin/env python3
"""Owned loopback UI QA with real JWT and reviewed issuer PDF/model positions.
Never deploy this test login. All databases are new and isolated, no live orders.
"""
import argparse,hashlib,json,os,secrets,socket,sys
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--workspace',type=Path,required=True);p.add_argument('--plan',type=Path,required=True);a=p.parse_args()
if not a.workspace.is_absolute() or a.workspace.exists() or not a.plan.is_absolute() or not a.plan.is_file():p.error('New isolated absolute workspace and explicit plan required')
a.workspace.mkdir(mode=0o700)
os.environ.update(DEEPFOCUS_DATABASE_URL='sqlite:///'+str(a.workspace/'auth.sqlite3'),DEEPFOCUS_DATA_DIR=str(a.workspace),DEEPFOCUS_QUANT_RESEARCH_DB_PATH=str(a.workspace/'research.sqlite3'),DEEPFOCUS_JWT_SECRET=secrets.token_hex(32),DEEPFOCUS_AUTH_REQUIRED='true')
for name in ('DEEPFOCUS_ADMIN_EMAIL','DEEPFOCUS_ADMIN_PASSWORD'):os.environ.pop(name,None)
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root/'backend'))
from fastapi import FastAPI,Request
from fastapi.responses import HTMLResponse,JSONResponse
from deepfocus_api import auth,quant_research_api as api,quant_research as lifecycle,quant_share_store as store
import uvicorn
raw=a.plan.read_bytes()
if len(raw)>2*1024**2:raise ValueError('Bounded plan required')
plan=json.loads(raw);sources={}
for sha,name in plan.pop('sources').items():
 with Path(name).open('rb') as f:body=f.read(10*1024**2+1)
 if len(body)>10*1024**2 or hashlib.sha256(body).hexdigest()!=sha:raise ValueError('Retained PDF differs')
 sources[sha]=body
auth.init_auth();users={k:auth.create_user(None,'isolated-share-'+k,secrets.token_urlsafe(24),role=auth.ROLE_ANALYST) for k in ('a','b')}
whole=store.put(users['a'].id,'真实公告 · 假设登记持仓100股',plan,sources)
store.review(users['a'].id,whole['id'],'qa-historical',mode='historical_reconstruction',as_of='2023-04-26T12:00:00+08:00')
store.review(users['a'].id,whole['id'],'qa-as-recorded',mode='as_recorded',as_of='2023-04-26T12:00:00+08:00')
fraction=json.loads(json.dumps(plan));fraction['closing_lots'][0]['qty']=1
pending=store.put(users['a'].id,'零碎待决 · 假设登记持仓1股',fraction,sources)
store.review(users['a'].id,pending['id'],'qa-pending',mode='historical_reconstruction',as_of='2023-04-26T12:00:00+08:00')
app=FastAPI();app.include_router(api.router);app.add_middleware(auth.AuthMiddleware);lifecycle.attach_lifespan(app)
@app.middleware('http')
async def no_mutation(request,call_next):
 if request.url.path.startswith('/api/') and request.method not in ('GET','HEAD','OPTIONS'):return JSONResponse({'detail':'隔离验收禁用写接口'},status_code=503)
 return await call_next(request)
@app.get('/',response_class=HTMLResponse)
def landing():
 return HTMLResponse('<meta charset="utf-8"><h1>股份档案 · 本机隔离验收</h1><p>真实公告原文，明确假设持仓，测试账户。无生产或实盘。</p><form method="post" action="/qa/login/a"><button>登录隔离账户 A</button></form><form method="post" action="/qa/login/b"><button>登录隔离账户 B</button></form>')
@app.post('/qa/login/{account}',response_class=HTMLResponse)
def login(account:str):
 if account not in users:return HTMLResponse('未知隔离账户',status_code=404)
 token=auth.create_access_token(users[account],auth.rotate_session(users[account].id))
 return HTMLResponse('<script>localStorage.setItem("auth_token",'+json.dumps(token)+');location.replace("/api/quant-research/workspace?view=shares")</script>',headers={'Cache-Control':'no-store'})
@app.get('/qa/health')
def health():return {'ready':True,'isolated':True,'counterfactual_positions':True,'production_writes':0}
listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(128);port=listener.getsockname()[1]
files=['quant_share_api.py','quant_share_read.py','quant_share_ui.py','quant_research_api.py','quant_research_ui.py','quant_share_store.py','quant_share_entitlements.py','auth.py']
meta={'pid':os.getpid(),'port':port,'workspace':str(a.workspace),'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'source_pins':{n:hashlib.sha256((root/'backend/deepfocus_api'/n).read_bytes()).hexdigest() for n in files},'position_role':'counterfactual_model','broker_account_proof':False,'provider_requests':0,'production_writes':0,'promotion_allowed':False}
(a.workspace/'qa.json').write_text(json.dumps(meta,indent=2));print(json.dumps({'pid':os.getpid(),'port':port,'isolated':True}),flush=True)
server=uvicorn.Server(uvicorn.Config(app,log_level='warning',access_log=False));server.run(sockets=[listener])
