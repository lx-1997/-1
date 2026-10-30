"""Require owner-bound annual-plan evidence before paper admission and updates.

This prerequisite is not strategy qualification, actual-session certification,
PIT security status, or permission for broker orders. Never infer missing bars.
"""
import json
from datetime import date, time, timedelta

from . import quant_research_store as jobs, quant_calendar_store as evidence
from .quant_exchange_schedule import BJ, check_dataset, stamp
from .quant_research_engine import digest

MISSING = '缺少当前版本、同一输入的合格日历核验；请先完成该实验的日历核验'


def review(owner, parent, *, required=True):
    if parent is None:
        raise ValueError('来源实验不存在，不能继续模拟跟踪')
    if (parent['status'] != 'completed' or parent['request'].get('kind')
            or digest(parent['result']) != parent['result_digest']
            or digest(parent['dataset']) != parent['result']['dataset_digest']):
        raise ValueError('来源实验完整性异常，禁止继续模拟跟踪')
    # Runtime import avoids lifecycle/service imports during module startup.
    from .quant_calendar_service import KERNEL
    with jobs.connect() as c:
        rows = c.execute("SELECT id,payload FROM qr_experiment WHERE owner=? AND status='completed' "
                         "AND json_extract(payload,'$.request.kind')='calendar_review' "
                         "AND json_extract(payload,'$.request.parent_id')=? ORDER BY created,id",
                         (owner, parent['id'])).fetchall()
    eligible = []
    for row in rows:
        payload = json.loads(row['payload']); request = payload['request']
        if (request.get('parent_digest') != parent['result_digest']
                or request.get('parent_request_digest') != digest(parent['request'])
                or request.get('reference_kernel') != parent['kernel']):
            continue
        result = payload.get('result')
        if (not result or digest(result) != payload.get('result_digest')
                or any(result.get(k) != request.get(k) for k in
                       ('parent_digest', 'parent_request_digest', 'reference_kernel'))
                or result.get('source_dataset_digest') != parent['result']['dataset_digest']):
            raise ValueError('日历核验完整性异常，禁止继续模拟跟踪')
        # A known defect is a veto even if a newer successful review exists.
        if result['quality']['status'] != 'matches_plan':
            raise ValueError('该封存实验已发现日历差异，禁止晋级或继续模拟；请修正数据并新建实验')
        if payload['kernel'] == KERNEL:
            eligible.append((row['id'], request, result, payload['result_digest']))
    if not eligible:
        if required: raise ValueError(MISSING)
        return None
    # Deterministic oldest valid review. Never select a convenient calendar
    # after seeing results; every matching negative review vetoes above.
    identity, request, result, result_digest = eligible[0]
    info, manifest, _ = evidence.load(owner, request['bundle_id'])
    if info['fingerprint'] != request['bundle_digest'] or result['evidence'] != info:
        raise ValueError('日历证据引用发生变化，禁止继续模拟跟踪')
    req = parent['request']
    quality = check_dataset(manifest, parent['dataset'], req['start_date'], req['end_date'], as_of=jobs.now())
    if quality != result['quality'] or quality['status'] != 'matches_plan':
        raise ValueError('日历核验重算不一致，禁止继续模拟跟踪')
    return {'proof': {'review_id': identity, 'review_digest': result_digest, 'kernel': KERNEL,
                      'parent_digest': parent['result_digest'], 'bundle_id': info['id'],
                      'bundle_digest': info['fingerprint'], 'quality_digest': digest(quality),
                      'scope': 'retrospective_annual_plan_only',
                      'actual_exchange_sessions_certified': False, 'security_status_verified': False},
            'manifest': manifest}


def check_update(context, dataset, start, end):
    as_of = jobs.now()
    quality = check_dataset(context['manifest'], dataset, start, end, as_of=as_of)
    local = stamp(as_of).astimezone(BJ)
    completed = local.date() if local.time() >= time(16, 30) else local.date()-timedelta(days=1)
    if quality['completed_through'] != min(date.fromisoformat(end), completed).isoformat():
        raise ValueError('新增行情采集时钟过期，未更新模拟账户')
    if quality['status'] != 'matches_plan':
        raise ValueError('新增行情与年度日历计划存在差异，未更新模拟账户；请核对缺日或额外日期')
    return {'status': quality['status'], 'completed_through': quality['completed_through'],
            'requested_start': start, 'requested_end': end,
            'calendar_manifest_digest': quality['calendar_manifest_digest'],
            'quality_digest': digest(quality), 'scope': 'retrospective_annual_plan_only',
            'actual_exchange_sessions_certified': False, 'security_status_verified': False}


def check_stored(context, paper):
    previous = paper.get('calendar_update', {}).get('merged')
    end = previous['requested_end'] if previous else paper['request']['end_date']
    quality = check_dataset(context['manifest'], paper['dataset'], paper['request']['start_date'], end, as_of=jobs.now())
    expected = previous['quality_digest'] if previous else context['proof']['quality_digest']
    if quality['status'] != 'matches_plan' or digest(quality) != expected:
        raise ValueError('已存行情日历或冻结范围发生变化，未更新模拟账户')
