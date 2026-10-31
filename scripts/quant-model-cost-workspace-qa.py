#!/usr/bin/env python3
"""Owned loopback real-JWT QA. Never deploy this isolated login or credentials."""
import argparse,hashlib,json,os,secrets,socket,sys
from pathlib import Path

p=argparse.ArgumentParser(description=__doc__);p.add_argument('--workspace',type=Path,required=True);p.add_argument('--experiments',type=Path,required=True);a=p.parse_args()
if not a.workspace.is_absolute() or a.workspace.exists() or not a.experiments.is_absolute():p.error('New isolated absolute workspace and scoped original export required')
a.workspace.mkdir(mode=0o700)
os.environ.update(DEEPFOCUS_DATABASE_URL='sqlite:///'+str(a.workspace/'auth.sqlite3'),DEEPFOCUS_DATA_DIR=str(a.workspace),DEEPFOCUS_QUANT_RESEARCH_DB_PATH=str(a.workspace/'research.sqlite3'),DEEPFOCUS_JWT_SECRET=secrets.token_hex(32),DEEPFOCUS_AUTH_REQUIRED='true')
for name in ['DEEPFOCUS_ADMIN_EMAIL','DEEPFOCUS_ADMIN_PASSWORD']:os.environ.pop(name,None)
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root/'backend'))
from fastapi import FastAPI,Request
from fastapi.responses import HTMLResponse,JSONResponse
from deepfocus_api import auth,quant_research_api as api,quant_model_cost_store as store
import uvicorn
with a.experiments.open('rb') as f:body=f.read(store.MAX_INPUT+1)
auth.init_auth();users={k:auth.create_user(None,'isolated-cost-'+k,secrets.token_urlsafe(24),role=auth.ROLE_ANALYST) for k in ['a','b']}
receipt=store.put(users['a'].id,'原两组样本外 · 精确账务核验',body)
app=FastAPI();app.include_router(api.router);app.add_middleware(auth.AuthMiddleware)
@app.middleware('http')
async def no_api_mutations(request,call_next):
    if request.url.path.startswith('/api/') and request.method not in ['GET','HEAD','OPTIONS']:return JSONResponse({'detail':'隔离验收禁用写接口'},status_code=503)
    return await call_next(request)
@app.get('/',response_class=HTMLResponse)
def landing():
    return HTMLResponse('<meta charset="utf-8"><h1>账务工作台 · 本机隔离验收</h1><p>原封存实验、测试账户。无生产或实盘。</p><form method="post" action="/qa/login/a"><button>登录隔离账户 A</button></form><form method="post" action="/qa/login/b"><button>登录隔离账户 B</button></form>')
@app.post('/qa/login/{account}',response_class=HTMLResponse)
def login(account:str):
    if account not in users:return HTMLResponse('未知隔离账户',status_code=404)
    token=auth.create_access_token(users[account],auth.rotate_session(users[account].id))
    return HTMLResponse('<script>localStorage.setItem("auth_token",'+json.dumps(token)+');location.replace("/api/quant-research/workspace?view=costs")</script>',headers={'Cache-Control':'no-store'})
@app.get('/qa/health')
def health():return {'ready':True,'isolated':True,'production_writes':0}
tokens={k:auth.create_access_token(u,auth.rotate_session(u.id)) for k,u in users.items()}
fd=os.open(a.workspace/'private-api-tokens.json',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
with os.fdopen(fd,'w') as f:json.dump(tokens,f)
listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(128);port=listener.getsockname()[1]
files=['quant_model_cost_api.py','quant_model_cost_read.py','quant_model_cost_ui.py','quant_share_api.py','quant_share_ui.py','quant_research_api.py','quant_research_ui.py','quant_model_cost_store.py','quant_exact_model_cost.py','auth.py']
meta={'pid':os.getpid(),'port':port,'workspace':str(a.workspace),'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'source_pins':{n:hashlib.sha256((root/'backend/deepfocus_api'/n).read_bytes()).hexdigest() for n in files},'input_sha256':hashlib.sha256(body).hexdigest(),'archive_id':receipt['id'],'production_writes':0,'promotion_allowed':False,'provider_requests':0,'account_b_has_no_archives':True}
(a.workspace/'qa.json').write_text(json.dumps(meta,indent=2));print(json.dumps({'pid':os.getpid(),'port':port,'isolated':True}),flush=True)
uvicorn.Server(uvicorn.Config(app,log_level='warning',access_log=False)).run(sockets=[listener])
