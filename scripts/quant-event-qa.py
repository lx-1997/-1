#!/usr/bin/env python3
"""Loopback-only UI acceptance server, real routers/JWT and isolated fixture users.

Uses explicitly supplied sealed public-price experiments. No production DB,
credentials, live orders or network data fetch. Never deploy this QA entrypoint.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import sys

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--workspace',type=Path,required=True,help='New absolute isolated directory')
p.add_argument('--experiments',type=Path,required=True)
p.add_argument('--events',type=Path,required=True)
p.add_argument('--evidence-dir',type=Path,required=True)
p.add_argument('--port',type=int,default=3038)
a=p.parse_args()
if not a.workspace.is_absolute() or a.workspace.exists():p.error('须使用全新绝对目录，不能复用任何既有数据库')
a.workspace.mkdir(parents=True)
os.environ.update(DEEPFOCUS_DATABASE_URL='sqlite:///'+str(a.workspace/'auth.sqlite3'),
    DEEPFOCUS_DATA_DIR=str(a.workspace),DEEPFOCUS_QUANT_RESEARCH_DB_PATH=str(a.workspace/'research.sqlite3'),
    DEEPFOCUS_JWT_SECRET=secrets.token_hex(32),DEEPFOCUS_AUTH_REQUIRED='true')
for name in ('DEEPFOCUS_ADMIN_EMAIL','DEEPFOCUS_ADMIN_PASSWORD'):os.environ.pop(name,None)
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from fastapi import FastAPI,Request
from fastapi.responses import HTMLResponse,JSONResponse
from deepfocus_api import auth,quant_research_api as api,quant_research_store as jobs
from deepfocus_api import quant_research as lifecycle,quant_research_ui as ui
from deepfocus_api.quant_corporate_actions import CashDividend
from deepfocus_api.quant_evidence_store import put

auth.init_auth()
users={key:auth.create_user(None,'isolated-quant-'+key,secrets.token_urlsafe(24),role=auth.ROLE_ANALYST) for key in ('a','b')}
m=json.loads(a.events.read_text());events=[CashDividend(**e) for e in m['events']];root=a.evidence_dir.resolve();sources={}
for sha in {e.source_sha256 for e in events}:
    file=(root/m['source_files'][sha]).resolve()
    if root not in file.parents or not file.is_file():raise ValueError('证据路径越界或缺失')
    sources[sha]=file.read_bytes()
bundle=put(users['a'].id,'真实公告抽样 · 本机验收',events,sources,completeness_attested=m.get('completeness_attested',False))
parents=[]
for x in json.loads(a.experiments.read_text()):
    req={**x['request'],'name':'本机验收 · '+x['request']['name']}
    item,_=jobs.create(users['a'].id,'qa-parent-'+x['id'],req,x['kernel'])
    jobs.update(users['a'].id,item['id'],status='completed',progress=100,stage='真实封存样本，仅供隔离验收',
        extra={k:x[k] for k in ('dataset','quality','result','result_digest')})
    parents.append(item['id'])
ui.HTML=ui.HTML.replace('<body>','<body><div style="padding:8px 16px;background:#fff4d6;color:#685216;font-size:12px">本机隔离验收 · 真实封存行情 · 测试账户 · 不连接生产或实盘</div>',1)
app=FastAPI();app.include_router(api.router);app.add_middleware(auth.AuthMiddleware);lifecycle.attach_lifespan(app)

@app.middleware('http')
async def offline(request,call_next):
    if request.method=='POST' and request.url.path=='/api/quant-research/experiments':
        return JSONResponse({'detail':'隔离环境仅使用封存样本，禁用联网抓取'},status_code=503)
    return await call_next(request)

@app.get('/',response_class=HTMLResponse)
def landing():
    return HTMLResponse('<html lang="zh-CN"><meta charset="utf-8"><title>隔离研究验收</title><h1>本机隔离验收</h1><p>真实封存行情，测试账户；无生产数据库和真实交易。</p><form method="post" action="/qa/login/a"><button>登录隔离验收账户 A</button></form><form method="post" action="/qa/login/b"><button>登录隔离验收账户 B</button></form></html>',headers={'Cache-Control':'no-store'})

@app.post('/qa/login/{account}',response_class=HTMLResponse)
def login(account:str):
    if account not in users:return HTMLResponse('未知验收账户',status_code=404)
    token=auth.create_access_token(users[account])
    return HTMLResponse('<script>localStorage.setItem("auth_token",'+json.dumps(token)+');location.replace("/quant-research")</script>',headers={'Cache-Control':'no-store'})

@app.get('/quant-research',response_class=HTMLResponse)
def workspace():return api.workspace()

@app.get('/qa/health')
def health():return {'ready':True,'isolated':True,'sealed_parents':len(parents),'evidence_bundles':1}

if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=a.port,access_log=False,log_level='warning')
