"""JWT-owned financial extraction metadata and bounded read-only derivation."""
from datetime import date,datetime,timedelta,timezone
from typing import Optional,Literal
from fastapi import APIRouter,Depends,HTTPException,Query
from . import quant_financial_read as archive,quant_financial_fact_store as facts,quant_financial_fact_derived as derived
from .quant_share_api import read
from .quant_corporate_actions import timestamp


def parameters(issuer_id,symbol,period_end,metric,as_of,mode,begin_report_period_end,begin_column,end_report_period_end,end_column):
    try:
        if metric not in facts.FLOW_METRICS:raise ValueError('Unsupported flow metric')
        derived.flows.components(period_end);end=date.fromisoformat(period_end)
        if timestamp(as_of)>datetime.now(timezone.utc):raise ValueError('Future query clock')
        spec=[begin_report_period_end,begin_column,end_report_period_end,end_column]
        endpoints=None
        if any(s is not None for s in spec):
            if not all(s is not None for s in spec):raise ValueError('Both endpoints required')
            endpoints={'beginning':{'report_period_end':begin_report_period_end,'column':begin_column},'ending':{'report_period_end':end_report_period_end,'column':end_column}}
            start=date(end.year,1,1) if period_end.endswith('12-31') else date(end.year-1,end.month,end.day)+timedelta(days=1)
            derived._stock_source(endpoints['beginning'],start-timedelta(days=1));derived._stock_source(endpoints['ending'],end)
        return dict(issuer_id=issuer_id,symbol=symbol,period_end=period_end,metric=metric,as_of=as_of,mode=mode,asset_endpoints=endpoints)
    except (ValueError,TypeError,KeyError):raise HTTPException(422,'请核对指标、知识时点与资产端点日期；不接受未来时点或存量TTM') from None


def build_router(owner):
    router=APIRouter(tags=['financial-research'])
    @router.get('/financial-extractions')
    async def listing(cursor:Optional[str]=Query(default=None,max_length=128),uid=Depends(owner)):
        return await read(uid,archive.listing,cursor=cursor)
    @router.get('/financial-extractions/{identity}')
    async def detail(identity:str,uid=Depends(owner)):
        return await read(uid,archive.detail,identity)
    @router.get('/financial-extractions/{identity}/derive')
    async def query(identity:str,issuer_id:str=Query(pattern=r'^cninfo:\d{5,20}$'),symbol:str=Query(pattern=r'^\d{6}$'),
        period_end:str=Query(max_length=10),metric:str=Query(max_length=64),as_of:str=Query(max_length=64),
        mode:Literal['as_recorded','historical_reconstruction']='as_recorded',
        begin_report_period_end:Optional[str]=Query(default=None,max_length=10),begin_column:Optional[str]=Query(default=None,max_length=32),
        end_report_period_end:Optional[str]=Query(default=None,max_length=10),end_column:Optional[str]=Query(default=None,max_length=32),uid=Depends(owner)):
        args=parameters(issuer_id,symbol,period_end,metric,as_of,mode,begin_report_period_end,begin_column,end_report_period_end,end_column)
        return await read(uid,archive.query,identity,**args)
    return router
