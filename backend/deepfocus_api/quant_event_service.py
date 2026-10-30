"""Bounded owner-owned event replay jobs, sharing the existing task lifecycle."""
import asyncio
import hashlib
from pathlib import Path
from . import quant_research as lifecycle, quant_research_store as jobs
from . import quant_evidence_store as evidence, quant_event_replay as engine
from .quant_research_engine import digest
from .quant_dividend_book import INVESTOR
from .quant_calendar import CalendarAlignmentError, require_aligned


KERNEL=digest({'execution':engine.KERNEL,**{name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
    for name in ('quant_event_service.py','quant_calendar.py','quant_evidence_store.py','quant_research_store.py')}})


def reference(owner,parent_id,bundle_id):
    parent=jobs.get(owner,parent_id)
    if parent is None:raise LookupError('原实验不存在')
    if parent['status']!='completed' or parent['request'].get('kind'):
        raise ValueError('仅支持已完成的原价格研究实验，不递归改写事件研究')
    if digest(parent['result'])!=parent['result_digest'] or digest(parent['dataset'])!=parent['result']['dataset_digest']:
        raise ValueError('原实验完整性核对失败')
    require_aligned(parent['dataset'], benchmark=parent['request']['benchmark'],
                    expected_symbols=parent['request']['symbols']+[parent['request']['benchmark']])
    if not parent['result'].get('folds'):raise ValueError('原实验没有冻结参数窗口')
    info=evidence.summary(owner,bundle_id)
    if info is None:raise LookupError('证据包不存在')
    if not set(parent['request']['symbols'])&set(info['symbols']):raise ValueError('证据包与原研究股票无交集')
    return parent,info


async def submit(owner,parent_id,bundle_id,key,mode,verification_of=None):
    if mode not in ('as_recorded','historical_reconstruction'):raise ValueError('知识可用模式未登记')
    parent,info=await asyncio.to_thread(reference,owner,parent_id,bundle_id)
    request={**{k:v for k,v in parent['request'].items() if k not in ('replay_of','request_id')},
        'kind':'event_replay','name':parent['request']['name']+' · 事件重放',
        'parent_id':parent_id,'parent_digest':parent['result_digest'],'reference_kernel':parent['kernel'],
        'bundle_id':bundle_id,'bundle_digest':info['fingerprint'],'knowledge_mode':mode,
        'investor_scenario':INVESTOR,'parameter_policy':'original_frozen_oos_schedule_no_refit'}
    if verification_of:
        original=await asyncio.to_thread(jobs.get,owner,verification_of)
        if original is None:raise LookupError('原事件研究不存在')
        if original['status']!='completed' or original['kernel']!=KERNEL or digest(original['result'])!=original['result_digest']:
            raise ValueError('原事件研究或冻结版本不能用于复核')
        for k in ('parent_id','parent_digest','bundle_id','bundle_digest','knowledge_mode'):
            if original['request'].get(k)!=request[k]:raise ValueError('冻结复核引用不一致')
        request.update(verification_of=verification_of,expected_result_digest=original['result_digest'])
    job,fresh=await asyncio.to_thread(jobs.create,owner,key,request,KERNEL)
    if fresh:
        # The parent lifespan cancels/gathers this registry on shutdown. Pure
        # CPU work also observes cancellation via the bounded progress callback.
        lifecycle._tasks[job['id']]=asyncio.create_task(execute(owner,job['id'],request))
    return job


async def execute(owner,identity,request):
    def update(**kw):return jobs.update(owner,identity,**kw)
    try:
        if not await asyncio.to_thread(update,progress=10,stage='核对原实验与证据指纹'):return
        parent,info=await asyncio.to_thread(reference,owner,request['parent_id'],request['bundle_id'])
        if parent['result_digest']!=request['parent_digest'] or info['fingerprint']!=request['bundle_digest']:
            raise ValueError('冻结引用已改变，禁止继续')
        info,events,sources=await asyncio.to_thread(evidence.load,owner,request['bundle_id'])
        selected=[e for e in events if e.symbol in parent['request']['symbols']]
        schedule=[{k:f[k] for k in ('train_end','test_start','test_end','parameters')} for f in parent['result']['folds']]
        if not await asyncio.to_thread(update,progress=25,stage='证据已封存，按原冻结参数重放'):return
        def progress(done,total,d):
            if not update(progress=30+int(60*done/max(total,1)),stage=f'已重放 {done}/{total} 个日期 · {d}'):
                raise InterruptedError('cancelled or lease expired')
        result=await asyncio.wait_for(asyncio.to_thread(engine.replay,parent['dataset'],parent['request']['symbols'],
            schedule[0]['parameters'],selected,sources,investor=INVESTOR,mode=request['knowledge_mode'],
            capital=parent['request']['capital'],start=schedule[0]['test_start'],end=schedule[-1]['test_end'],
            schedule=schedule,progress=progress),180)
        result.update(parent_oos={k:v for k,v in parent['result']['oos'].items() if k!='curve'},
            evidence=info,source_dataset_digest=parent['result']['dataset_digest'],
            validation_status='seen_history_development_only',holdout_replayed=False)
        if request.get('expected_result_digest') and digest(result)!=request['expected_result_digest']:
            raise ValueError('事件复核结果与冻结研究不一致，禁止发布')
        await asyncio.to_thread(update,status='completed',progress=100,stage='冻结事件重放核对一致' if request.get('verification_of') else '事件重放完成，抽样数据不得晋级',
            extra={'result':result,'result_digest':digest(result)})
    except asyncio.CancelledError:
        await asyncio.to_thread(update,status='failed',stage='执行进程关闭，事件研究已中断')
        raise
    except InterruptedError:pass
    except CalendarAlignmentError as exc:
        await asyncio.to_thread(update,status='failed',stage=str(exc),
            extra={'error_code':'calendar_alignment_rejected','quality':exc.report})
    except Exception as exc:
        message=str(exc) if isinstance(exc,ValueError) else '证据或计算暂不可用，失败实验已留档'
        await asyncio.to_thread(update,status='failed',stage=message,extra={'error_code':type(exc).__name__})
    finally:lifecycle._tasks.pop(identity,None)
