"""JWT-owned research API; no account may see another account's experiments."""
import asyncio
from datetime import date, timedelta
from typing import Annotated
from typing import Literal
import uuid
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator
from .auth import require_current_user
from . import quant_research as service, quant_research_store as store

def private_headers(response: Response):
    response.headers['Cache-Control']='no-store'

router = APIRouter(prefix='/api/quant-research', tags=['quant-research'], dependencies=[Depends(private_headers)])

def today(): return date.fromisoformat(service.complete_day())


class ExperimentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex, min_length=8, max_length=96, pattern=r'^[A-Za-z0-9_-]+$')
    name: str = Field(default='动量策略研究', min_length=1, max_length=80)
    symbols: list[str] = Field(min_length=1, max_length=5)
    benchmark: str = '000300'
    start_date: date = Field(default_factory=lambda: today()-timedelta(days=1100))
    end_date: date = Field(default_factory=today)
    capital: float = Field(default=1000000, ge=10000, le=100000000, allow_inf_nan=False)

    @field_validator('symbols')
    @classmethod
    def symbols_valid(cls, values):
        if len(set(values)) != len(values) or any(not (len(s)==6 and s.isdigit() and s[0] in '036') or s in service.BENCHMARKS for s in values):
            raise ValueError('请填写不重复的沪深股票六位代码')
        return values

    @field_validator('benchmark')
    @classmethod
    def benchmark_valid(cls, value):
        if value not in service.BENCHMARKS: raise ValueError('请选择支持的基准')
        return value

    @model_validator(mode='after')
    def period(self):
        if self.start_date >= self.end_date or self.end_date > today() or (self.end_date-self.start_date).days > 1800:
            raise ValueError('日期无效：开始早于结束，不得选择未来日期，最长 1800 天')
        return self


def owner(user=Depends(require_current_user)):
    value = user.get('sub')
    if not isinstance(value, str) or not value.strip(): raise HTTPException(401, '请重新登录')
    return value


def call(fn, *args):
    try: return fn(*args)
    except LookupError as e: raise HTTPException(404, str(e)) from e
    except ValueError as e: raise HTTPException(422, str(e)) from e
    except RuntimeError as e: raise HTTPException(409, str(e)) from e


def visible(item):
    if not item: raise HTTPException(404, '记录不存在')
    current=service.kernel_identity()
    if item.get('request',{}).get('kind')=='event_replay':
        from .quant_event_service import KERNEL
        current=KERNEL
    elif item.get('request',{}).get('kind')=='calendar_review':
        from .quant_calendar_service import KERNEL
        current=KERNEL
    return {**{k: v for k,v in item.items() if k != 'dataset'}, 'kernel_current': item.get('kernel') == current}


@router.post('/experiments', status_code=202)
async def create(payload: ExperimentRequest, uid=Depends(owner)):
    values = payload.model_dump(mode='json'); key = values.pop('request_id')
    try: return await service.submit_async(uid, key, values)
    except LookupError as e: raise HTTPException(404, str(e)) from e
    except ValueError as e: raise HTTPException(422, str(e)) from e
    except RuntimeError as e: raise HTTPException(429, str(e)) from e


@router.get('/experiments')
def listing(uid=Depends(owner)):
    store.expire_interrupted()
    return {'items': store.list_records(uid)}


@router.get('/experiments/{identity}')
def detail(identity: str, uid=Depends(owner)):
    store.expire_interrupted()
    return visible(store.get(uid, identity))


@router.delete('/experiments/{identity}')
def cancel(identity: str, uid=Depends(owner)): return visible(store.cancel(uid, identity))


@router.get('/experiments/{identity}/export')
def export(identity: str, uid=Depends(owner)):
    job = store.get(uid, identity)
    if not job: raise HTTPException(404, '实验不存在')
    # Snapshot belongs to this account; raw market data is deliberately excluded
    # from download until redistribution licensing has been established.
    response = visible(job)
    return JSONResponse(response, headers={'Content-Disposition': 'attachment; filename="quant-experiment.json"', 'Cache-Control': 'no-store'})


@router.post('/experiments/{identity}/paper', status_code=201)
def promote(identity: str, uid=Depends(owner)):
    item=store.get(uid,identity)
    if item and item.get('request',{}).get('kind')=='event_replay':
        raise HTTPException(422,'事件重放仍为开发研究，禁止直接晋级模拟账户')
    if item and item.get('request',{}).get('kind')=='calendar_review':
        raise HTTPException(422,'日历核验不构成策略晋级依据，不能创建模拟账户')
    return visible(call(service.promote, uid, identity))


@router.get('/paper')
def papers(uid=Depends(owner)): return {'items': store.list_records(uid, True)}


@router.get('/paper/{identity}')
def paper(identity: str, uid=Depends(owner)): return visible(store.get(uid, identity, True))


@router.post('/paper/{identity}/refresh')
async def refresh(identity: str, uid=Depends(owner)):
    try: return visible(await service.refresh_paper(uid, identity))
    except LookupError as e: raise HTTPException(404, str(e)) from e
    except ValueError as e: raise HTTPException(422, str(e)) from e
    except RuntimeError as e: raise HTTPException(409, str(e)) from e
    except Exception as e: raise HTTPException(503, '未能确认本次同步结果，请刷新查看账户状态后重试') from e


@router.post('/paper/{identity}/pause')
def pause(identity: str, uid=Depends(owner)): return visible(call(service.pause, uid, identity))


@router.get('/workspace', response_class=HTMLResponse, include_in_schema=False)
def workspace(view:Literal['strategies','shares']='strategies'):
    if view=='shares':
        from .quant_share_ui import HTML
    else:
        from .quant_research_ui import HTML
    import hashlib,base64
    script=HTML.split('<script>')[1].split('</script>')[0]
    sha=base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    policy=f"default-src 'self'; script-src 'sha256-{sha}'; style-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'self'"
    return HTMLResponse(HTML,headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'same-origin','Content-Security-Policy':policy})


class ReplayRequest(BaseModel):
    request_id: str = Field(default_factory=lambda:uuid.uuid4().hex,min_length=8,max_length=96,pattern=r'^[A-Za-z0-9_-]+$')

@router.post('/experiments/{identity}/replay',status_code=202)
async def replay(identity:str,payload:ReplayRequest,uid=Depends(owner)):
    item=await asyncio.to_thread(store.get,uid,identity)
    if item and item.get('request',{}).get('kind')=='calendar_review':
        from .quant_calendar_service import submit
        req=item['request']
        try:return visible(await submit(uid,req['parent_id'],req['bundle_id'],payload.request_id,verification_of=identity))
        except LookupError as e:raise HTTPException(404,str(e)) from e
        except ValueError as e:raise HTTPException(422,str(e)) from e
        except RuntimeError as e:raise HTTPException(429,str(e)) from e
    if item and item.get('request',{}).get('kind')=='event_replay':
        from .quant_event_service import submit
        req=item['request']
        try:return visible(await submit(uid,req['parent_id'],req['bundle_id'],payload.request_id,req['knowledge_mode'],verification_of=identity))
        except LookupError as e:raise HTTPException(404,str(e)) from e
        except ValueError as e:raise HTTPException(422,str(e)) from e
        except RuntimeError as e:raise HTTPException(429,str(e)) from e
    try:return visible(await service.reproduce_async(uid,identity,payload.request_id))
    except LookupError as e:raise HTTPException(404,str(e)) from e
    except ValueError as e:raise HTTPException(422,str(e)) from e
    except RuntimeError as e:raise HTTPException(429,str(e)) from e


@router.get('/evidence')
def evidence_list(uid=Depends(owner)):
    from .quant_evidence_store import listing
    return {'items':listing(uid)}


@router.get('/evidence/{identity}')
def evidence_detail(identity:str,uid=Depends(owner)):
    from .quant_evidence_store import summary
    item=summary(uid,identity)
    if item is None:raise HTTPException(404,'证据包不存在')
    return item


class EventReplayRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id:str=Field(default_factory=lambda:uuid.uuid4().hex,min_length=8,max_length=96,pattern=r'^[A-Za-z0-9_-]+$')
    bundle_id:str=Field(pattern=r'^[0-9a-f]{32}$')
    knowledge_mode:Literal['as_recorded','historical_reconstruction']='as_recorded'


@router.post('/experiments/{identity}/event-replay',status_code=202)
async def event_replay(identity:str,payload:EventReplayRequest,uid=Depends(owner)):
    from .quant_event_service import submit
    try:return visible(await submit(uid,identity,payload.bundle_id,payload.request_id,payload.knowledge_mode))
    except LookupError as e:raise HTTPException(404,str(e)) from e
    except ValueError as e:raise HTTPException(422,str(e)) from e
    except RuntimeError as e:raise HTTPException(429,str(e)) from e


@router.get('/calendar-evidence')
def calendar_evidence_list(uid=Depends(owner)):
    from .quant_calendar_store import listing
    return {'items':listing(uid)}


@router.get('/calendar-evidence/{identity}')
def calendar_evidence_detail(identity:str,uid=Depends(owner)):
    from .quant_calendar_store import summary
    item=summary(uid,identity)
    if item is None:raise HTTPException(404,'日历证据包不存在')
    return item


class CalendarReviewRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id:str=Field(default_factory=lambda:uuid.uuid4().hex,min_length=8,max_length=96,pattern=r'^[A-Za-z0-9_-]+$')
    bundle_id:str=Field(pattern=r'^[0-9a-f]{32}$')


@router.post('/experiments/{identity}/calendar-review',status_code=202)
async def calendar_review(identity:str,payload:CalendarReviewRequest,uid=Depends(owner)):
    from .quant_calendar_service import submit
    try:return visible(await submit(uid,identity,payload.bundle_id,payload.request_id))
    except LookupError as e:raise HTTPException(404,str(e)) from e
    except ValueError as e:raise HTTPException(422,str(e)) from e
    except RuntimeError as e:raise HTTPException(429,str(e)) from e


from .quant_share_api import build_router as build_share_router
router.include_router(build_share_router(owner))
