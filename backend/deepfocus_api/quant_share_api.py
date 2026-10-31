"""Read-only JWT-owned archive routes; no public upload or quantity mutation."""
import asyncio
import sqlite3
from typing import Optional
from fastapi import APIRouter,Depends,HTTPException,Query
from . import quant_share_read as archive,quant_research as lifecycle

_reading={}


async def read(owner,function,*args,**kwargs):
    if len(_reading)>=4 or sum(uid==owner for uid in _reading.values())>=2:
        raise HTTPException(429,'档案读取繁忙，请稍后重试',headers={'Cache-Control':'no-store'})
    task=asyncio.current_task();_reading[task]=owner
    try:
        return await lifecycle._paper_io(function,owner,*args,**kwargs)
    except LookupError as exc:raise HTTPException(404,'档案不存在',headers={'Cache-Control':'no-store'}) from exc
    except (ValueError,TypeError,KeyError) as exc:raise HTTPException(409,'档案校验未通过，请联系内部管理员核对版本或原文',headers={'Cache-Control':'no-store'}) from exc
    except (OSError,sqlite3.DatabaseError) as exc:raise HTTPException(503,'档案暂不可用，请稍后重试',headers={'Cache-Control':'no-store'}) from exc
    finally:_reading.pop(task,None)


def build_router(owner):
    router=APIRouter(tags=['share-archives'])
    @router.get('/share-evidence')
    async def bundles(cursor:Optional[str]=Query(default=None,max_length=128),uid=Depends(owner)):
        return await read(uid,archive.listing,cursor=cursor)
    @router.get('/share-evidence/{identity}')
    async def bundle(identity:str,uid=Depends(owner)):
        return await read(uid,archive.bundle,identity)
    @router.get('/share-reviews')
    async def reviews(bundle_id:Optional[str]=Query(default=None,max_length=32),cursor:Optional[str]=Query(default=None,max_length=128),uid=Depends(owner)):
        return await read(uid,archive.listing,kind='review',bundle_id=bundle_id,cursor=cursor)
    @router.get('/share-reviews/{identity}')
    async def review(identity:str,uid=Depends(owner)):
        return await read(uid,archive.review,identity)
    return router
