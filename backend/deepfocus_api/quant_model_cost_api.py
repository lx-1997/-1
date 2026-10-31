"""JWT-owned read-only exact-cost archive routes, shared archive read limits."""
from typing import Optional
from fastapi import APIRouter,Depends,Query
from . import quant_model_cost_read as archive
from .quant_share_api import read


def build_router(owner):
    router=APIRouter(tags=['model-cost-archives'])
    @router.get('/model-cost-archives')
    async def listing(cursor:Optional[str]=Query(default=None,max_length=128),uid=Depends(owner)):
        return await read(uid,archive.listing,cursor=cursor)
    @router.get('/model-cost-archives/{identity}')
    async def detail(identity:str,uid=Depends(owner)):
        return await read(uid,archive.detail,identity)
    return router
