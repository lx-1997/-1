"""Bounded calendar-quality tasks sharing QR ownership, leases and cancellation."""
import asyncio
import hashlib
from pathlib import Path

from . import quant_research as lifecycle, quant_research_store as jobs
from . import quant_calendar_store as evidence
from .quant_exchange_schedule import check_dataset
from .quant_research_engine import digest

KERNEL = digest({name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                 for name in ('quant_calendar_service.py', 'quant_calendar_store.py',
                              'quant_exchange_schedule.py', 'quant_research_store.py', 'quant_worker_slots.py')})


def reference(owner, parent_id, bundle_id):
    parent = jobs.get(owner, parent_id)
    if parent is None:
        raise LookupError('原实验不存在')
    if parent['status'] != 'completed' or parent['request'].get('kind'):
        raise ValueError('请选择已完成的原价格实验进行日历核验')
    if (digest(parent['result']) != parent['result_digest']
            or digest(parent['dataset']) != parent['result']['dataset_digest']):
        raise ValueError('原实验完整性核对失败')
    info = evidence.summary(owner, bundle_id)
    if info is None:
        raise LookupError('日历证据包不存在')
    return parent, info


async def submit(owner, parent_id, bundle_id, key, verification_of=None):
    parent, info = await lifecycle._paper_io(reference, owner, parent_id, bundle_id)
    request = {'kind': 'calendar_review', 'name': parent['request']['name']+' · 日历核验',
               'symbols': parent['request']['symbols'], 'parent_id': parent_id,
               'parent_digest': parent['result_digest'], 'parent_request_digest': digest(parent['request']),
               'reference_kernel': parent['kernel'], 'bundle_id': bundle_id,
               'bundle_digest': info['fingerprint']}
    if verification_of:
        original = await lifecycle._paper_io(jobs.get, owner, verification_of)
        if original is None:
            raise LookupError('原日历核验不存在')
        if (original['status'] != 'completed' or original['kernel'] != KERNEL
                or original['request'].get('kind') != 'calendar_review'
                or digest(original['result']) != original['result_digest']):
            raise ValueError('原日历核验或冻结版本不可用于复核')
        for k in ('parent_id', 'parent_digest', 'parent_request_digest', 'reference_kernel', 'bundle_id', 'bundle_digest'):
            if original['request'].get(k) != request[k]:
                raise ValueError('日历核验的冻结引用不一致')
        request.update(verification_of=verification_of, expected_result_digest=original['result_digest'])
    return await lifecycle.admit_job(owner,key,request,KERNEL,execute)


@lifecycle.research_worker
async def execute(owner, identity, request):
    def update(**kwargs):
        return jobs.update(owner, identity, **kwargs)
    try:
        if not await lifecycle._paper_io(update, progress=10, stage='核对原实验与日历证据指纹'):
            return
        parent, info = await lifecycle._paper_io(reference, owner, request['parent_id'], request['bundle_id'])
        if (parent['result_digest'] != request['parent_digest'] or info['fingerprint'] != request['bundle_digest']
                or digest(parent['request']) != request['parent_request_digest']
                or parent['kernel'] != request['reference_kernel']):
            raise ValueError('冻结引用已变化')
        info, manifest, sources = await lifecycle._paper_io(evidence.load, owner, request['bundle_id'])
        if not await lifecycle._paper_io(update, progress=45, stage='官方原文已核对，检查每条行情的计划日期'):
            return
        req = parent['request']
        # Evidence and prices were observed before this task; the result contains
        # no execution-clock field, so an immutable frozen verification is exact.
        quality = await asyncio.wait_for(lifecycle._paper_io(
            check_dataset, manifest, parent['dataset'], req['start_date'], req['end_date'], as_of=jobs.now()), 60)
        result = {'quality': quality, 'evidence': info, 'parent_digest': parent['result_digest'],
                  'parent_request_digest': digest(parent['request']), 'reference_kernel': parent['kernel'],
                  'source_dataset_digest': parent['result']['dataset_digest'],
                  'validation_status': 'retrospective_annual_plan_only',
                  'gate': {'passed': False, 'promotion_allowed': False,
                           'reasons': ['年度计划核对不构成策略晋级', '临时停市及历史证券状态仍未完整核验']}}
        if request.get('expected_result_digest') and digest(result) != request['expected_result_digest']:
            raise ValueError('冻结日历核验结果不一致')
        stage = ('冻结日历核验结果一致' if request.get('verification_of') else
                 '年度休市计划核对一致' if quality['status']=='matches_plan' else '日历核验发现缺口，差异已留档')
        await lifecycle._paper_io(update, status='completed', progress=100, stage=stage,
                                extra={'result': result, 'result_digest': digest(result)})
    except asyncio.CancelledError:
        await lifecycle._paper_io(update, status='failed', stage='执行进程关闭，日历核验已中断')
        raise
    except Exception as exc:
        await lifecycle._paper_io(update, status='failed', stage='日历证据或行情不符合核验要求，失败记录已保留',
                                extra={'error_code': type(exc).__name__})
