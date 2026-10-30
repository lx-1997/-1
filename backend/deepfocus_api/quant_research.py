"""Durable, bounded research orchestration and end-of-day paper tracking."""
import asyncio
from functools import wraps
import uuid
from contextvars import ContextVar
from threading import Lock
import hashlib
import json
from functools import lru_cache
from datetime import datetime, timezone, timedelta
from pathlib import Path
from . import quant_research_engine as engine, quant_research_store as store
from .backtest_data import _fetch_sina_cn, normalize_market_symbol
from .quant_calendar import CalendarAlignmentError, require_aligned

_tasks = {}
_refreshing = set()
_refresh_tasks = {}
_admission_tasks = {}
_admissions_open = True
MAX_PENDING_ADMISSIONS = 16
MAX_OWNER_ADMISSIONS = 4
_paper_attempt = ContextVar('paper_refresh_attempt', default=None)


class _PaperAttempt:
    def __init__(self):
        self.lock = Lock()
        self.cancelled = False
        self.commit_admitted = False

    def cancel(self):
        # The mutex protects only flags; never hold it over SQLite or replay.
        with self.lock:
            self.cancelled = True

    def admit_commit(self):
        with self.lock:
            if self.cancelled:
                raise InterruptedError('模拟同步已取消，未进入账户提交阶段')
            self.commit_admitted = True


async def _paper_io(function, *args, **kwargs):
    """Own the real worker until it drains, even if its caller is cancelled."""
    worker = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        attempt = _paper_attempt.get()
        if attempt is not None: attempt.cancel()
        # Do not free concurrency capacity while a thread still holds/waits for
        # a database lock or computes a financial replay. Consume its outcome.
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if worker.done() and not worker.cancelled():
            try: worker.result()
            except Exception: pass
        raise
BENCHMARKS = {'000300': '沪深300', '000905': '中证500', '000852': '中证1000'}


def research_worker(function):
    """Claim once and retain the DB slot until every owned thread has drained."""
    @wraps(function)
    async def run(owner,identity,request):
        attempt = _PaperAttempt()
        context = _paper_attempt.set(attempt)
        ticket = uuid.uuid4().hex
        try:
            if not await _paper_io(store.claim_worker,owner,identity,ticket): return
            return await function(owner,identity,request)
        finally:
            try: await _paper_io(store.finish_worker,owner,identity,ticket)
            finally:
                _paper_attempt.reset(context)
                if _tasks.get(identity) is asyncio.current_task(): _tasks.pop(identity,None)
    return run


@lru_cache(maxsize=1)
def kernel_identity():
    from .quant_cpu_process import pins
    return engine.digest(pins())


async def _compute_price(owner, identity, request, dataset, attempt):
    from .quant_cpu_process import run
    return await _wait_price_worker(_paper_io(run, owner, identity, request, dataset, kernel_identity(), attempt))


async def _wait_price_worker(coroutine):
    """Own timeout cancellation too; repeated outer cancel cannot skip drain."""
    task = asyncio.create_task(coroutine)
    try:
        return await asyncio.wait_for(asyncio.shield(task),180)
    except BaseException:
        task.cancel()
        while not task.done():
            try:await asyncio.shield(task)
            except asyncio.CancelledError:continue
            except Exception:break
        if task.done() and not task.cancelled():task.exception()
        raise


def complete_day():
    # Completed prior dates only, including intraday requests and data vendors
    # that publish today's incomplete daily candle. Daily tracking is deliberate.
    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


async def fetch_dataset(symbols, start, end):
    async def one(symbol):
        meta = normalize_market_symbol(symbol, 'CN')
        if symbol in BENCHMARKS: meta['provider_symbol'] = 'sh'+symbol
        p = await _fetch_sina_cn(meta, start, end)
        p.update(volume_unit='shares', fetched_at=store.now())
        today = complete_day()
        after_close = datetime.now(timezone(timedelta(hours=8))).strftime('%H:%M') >= '16:30'
        p['bars'] = [b for b in p['bars'] if b['date'] < today or (b['date'] == today and after_close)]
        return p
    values = await asyncio.wait_for(asyncio.gather(*(one(s) for s in symbols)), 40)
    dataset = dict(zip(symbols, values))
    anchors = [s for s in symbols if s in BENCHMARKS]
    require_aligned(dataset, benchmark=anchors[0] if len(anchors) == 1 else None, expected_symbols=symbols)
    return dataset


def quality(dataset, start, end, *, benchmark=None, expected_symbols=None):
    calendar = require_aligned(dataset, benchmark=benchmark, expected_symbols=expected_symbols)
    dates = engine.validate_dataset(dataset)
    return {'common_sessions': len(dates), 'first': dates[0] if dates else None, 'last': dates[-1] if dates else None,
            'requested_start': start, 'requested_end': end, 'calendar': 'strict_observed_date_alignment',
            'calendar_contract': calendar,
            'symbols': {s: {'bars': len(p['bars']), 'source': p['source'], 'adjustment': p['adjustment'],
                            'volume_unit': p['volume_unit'], 'fetched_at': p['fetched_at'],
                            'excluded_non_common': len(p['bars'])-len(dates)} for s, p in dataset.items()},
            'warnings': ['公开数据并非历史时点数据仓库，存在选择偏差', '现金分红、除权和特殊交易状态未经完整核验',
                         '所有序列日期严格对齐；不裁剪交集、不填充缺价，缺少日线不能推定停牌',
                         '尚未独立核验完整交易所日历，各序列同时漏日仍可能无法发现',
                         '当前采用不复权价格，收益不含分红；不可与含分红指数直接等同']}


@research_worker
async def execute(owner, identity, request):
    async def update(**kwargs):
        return await _paper_io(store.update, owner, identity, **kwargs)
    try:
        if not await update(progress=8, stage='获取真实不复权日线'): return
        reference = None
        if request.get('replay_of'):
            reference = await _paper_io(store.get, owner, request['replay_of'])
            if not reference or reference['status'] != 'completed' or reference['kernel'] != kernel_identity():
                raise ValueError('原实验或冻结内核不可用于复核')
            dataset = reference['dataset']
        else:
            dataset = await fetch_dataset(request['symbols']+[request['benchmark']], request['start_date'], request['end_date'])
        q = quality(dataset, request['start_date'], request['end_date'],
                    benchmark=request['benchmark'], expected_symbols=request['symbols']+[request['benchmark']])
        if not await update(progress=30, stage='真实行情已封存，开始训练与滚动验证', extra={'dataset': dataset, 'quality': q}): return
        # SQLite callback also observes cancellation; the CPU worker then exits
        # at the next fold, and its terminal result is never written over cancel.
        attempt = _paper_attempt.get()
        result = await _compute_price(owner, identity, request, dataset, attempt)
        coverage_reasons = []
        from datetime import date
        if q['first'] and (date.fromisoformat(q['first'])-date.fromisoformat(request['start_date'])).days > 20:
            coverage_reasons.append('数据未覆盖所请求的起始区间')
        if q['last'] and (date.fromisoformat(request['end_date'])-date.fromisoformat(q['last'])).days > 10:
            coverage_reasons.append('末端行情覆盖不足或过期')
        if coverage_reasons:
            result['gate']['passed'] = False
            result['gate']['reasons'].extend(coverage_reasons)
        if reference and engine.digest(result) != reference['result_digest']:
            raise ValueError('复核结果与冻结实验不一致，禁止晋级')
        await update(status='completed', progress=100, stage='冻结数据复核一致' if reference else '实验完成，研究门槛已核对',
                     extra={'result': result, 'result_digest': engine.digest(result)})
    except asyncio.CancelledError:
        await update(status='failed', stage='执行进程关闭，实验已中断')
        raise
    except InterruptedError:
        pass
    except CalendarAlignmentError as exc:
        extra = {'error_code': 'calendar_alignment_rejected', 'quality': exc.report}
        if exc.dataset is not None:
            extra['dataset'] = exc.dataset
        await update(status='failed', stage=str(exc), extra=extra)
    except Exception as exc:
        # No provider response bodies, keys or connection strings in public errors.
        message = str(exc) if isinstance(exc, ValueError) else '数据源或计算暂不可用，请稍后重试；失败实验已保留'
        await update(status='failed', stage=message, extra={'error_code': type(exc).__name__})


def submit(owner, key, request):
    job, fresh = store.create(owner, key, request, kernel_identity())
    if fresh:
        _tasks[job['id']] = asyncio.create_task(execute(owner, job['id'], request))
    return {k: v for k, v in job.items() if k not in {'dataset'}}


async def admit_job(owner, key, request, kernel, executor):
    """Own DB admission beyond an HTTP disconnect, bounded until real exit.

    Disconnecting a POST does not cancel a possibly committed job. Explicit
    DELETE cancels research. A retry with the same key finds the same record.
    Shutdown, unlike disconnect, cancels/drains admissions before workers.
    """
    if (not _admissions_open or len(_admission_tasks) >= MAX_PENDING_ADMISSIONS
            or sum(value == owner for value in _admission_tasks.values()) >= MAX_OWNER_ADMISSIONS):
        raise RuntimeError('研究提交队列已满或服务正在关闭，请用相同提交标识重试')
    request = json.loads(json.dumps(request, allow_nan=False))
    async def admission():
        captured = []
        def reserve():
            result = store.create(owner, key, request, kernel)
            captured.append(result)
            return result
        try:
            job, fresh = await _paper_io(reserve)
            if not _admissions_open:
                raise asyncio.CancelledError()
            if fresh:
                _tasks[job['id']] = asyncio.create_task(executor(owner, job['id'], request))
            return job
        except BaseException:
            # _paper_io has drained the DB thread before raising. Only a fresh
            # reservation belongs to this admission; never cancel an old retry.
            if captured and captured[0][1]:
                try:
                    await _paper_io(store.cancel, owner, captured[0][0]['id'])
                except asyncio.CancelledError:
                    pass  # cleanup thread has also drained
            raise
    task = asyncio.create_task(admission())
    _admission_tasks[task] = owner
    def finished(done):
        _admission_tasks.pop(done, None)
        if not done.cancelled():
            done.exception()  # consume failures after a disconnected caller
    task.add_done_callback(finished)
    return await asyncio.shield(task)


async def submit_async(owner, key, request):
    job = await admit_job(owner, key, request, kernel_identity(), execute)
    return {k:v for k,v in job.items() if k != 'dataset'}


async def _cancel_and_drain(tasks):
    """Repeated cancellation of shutdown must not abandon owned work."""
    tasks = list(tasks)
    for task in tasks: task.cancel()
    group = asyncio.gather(*tasks, return_exceptions=True)
    interrupted = False
    while not group.done():
        try:
            await asyncio.shield(group)
        except asyncio.CancelledError:
            interrupted = True
    group.result()
    if interrupted: raise asyncio.CancelledError()


def _calendar_veto(owner, parent, *, required=True):
    from .quant_paper_calendar import review
    return review(owner, parent, required=required)


def promote(owner, identity):
    job = store.get(owner, identity)
    if not job: raise LookupError('实验不存在')
    if job['request'].get('kind'):
        raise ValueError('质量核验或事件研究不能直接晋级模拟账户')
    if job['status'] != 'completed': raise ValueError('实验尚未完成')
    if job['kernel'] != kernel_identity(): raise ValueError('执行内核已升级，请重新验证实验')
    if engine.digest(job['result']) != job['result_digest'] or engine.digest(job['dataset']) != job['result']['dataset_digest']:
        raise ValueError('实验完整性核对失败，禁止晋级')
    require_aligned(job['dataset'], benchmark=job['request']['benchmark'],
                    expected_symbols=job['request']['symbols']+[job['request']['benchmark']])
    calendar = _calendar_veto(owner, job, required=False)
    if not job['result']['gate']['passed']: raise ValueError('未通过研究门槛，不能转入模拟跟踪')
    if (job['result'].get('oos', {}).get('risk_paused') is not False
            or job['result'].get('holdout', {}).get('state', {}).get('paused') is not False):
        raise ValueError('验证路径已永久暂停或缺少风险状态，不能新建模拟账户绕过暂停')
    if calendar is None:
        from .quant_paper_calendar import MISSING
        raise ValueError(MISSING)
    # Start flat after the sealed experiment; never backdate a live paper account.
    from .quant_paper_calendar import commit_guard
    guard = commit_guard(owner, identity, calendar)
    today = complete_day()
    state = engine.initial_state(job['request']['capital']); state['cursor'] = today
    return store.create_paper(owner, identity, {'request': job['request'], 'parameters': job['result']['parameters'],
                  'kernel': job['kernel'], 'dataset': job['dataset'], 'state': state, 'fills': [], 'curve': [],
                  'started': store.now(), 'start_after': today, 'last_check': None,
                  'experiment_digest': job['result_digest'], 'calendar_admission': calendar['proof'], 'policy': 'research_only_not_broker_execution'}, guard=guard)


async def _refresh_paper(owner, identity):
    paper = await _paper_io(store.get, owner, identity, True)
    if not paper: raise LookupError('模拟账户不存在')
    if paper['status'] != 'active': raise ValueError('模拟跟踪已暂停')
    if paper['kernel'] != kernel_identity(): raise ValueError('冻结内核与当前版本不一致，已拒绝执行')
    parent = await _paper_io(store.get, owner, paper['experiment'])
    calendar = await _paper_io(_calendar_veto, owner, parent)
    if paper.get('experiment_digest') != parent['result_digest'] or paper['request'] != parent['request']:
        raise ValueError('模拟账户与来源实验引用不一致，未更新账户')
    req, old = paper['request'], paper['dataset']
    scope = req['symbols']+[req['benchmark']]
    require_aligned(old, benchmark=req['benchmark'], expected_symbols=scope)
    from .quant_paper_calendar import check_stored
    await _paper_io(check_stored, calendar, paper)
    # Fetch a sufficiently overlapping history and refuse revisions instead of
    # silently rewriting signals or historical fills.
    end = complete_day()
    fetched = await fetch_dataset(req['symbols']+[req['benchmark']], req['start_date'], end)
    require_aligned(fetched, benchmark=req['benchmark'], expected_symbols=scope)
    merged = {}
    for s, p in old.items():
        oldmap = {b['date']: b for b in p['bars']}
        for b in fetched[s]['bars']:
            if b['date'] in oldmap and b != oldmap[b['date']]:
                raise ValueError(f'{s} 历史行情发生修订，需单独复核，未改写模拟账户')
        combined = {**oldmap, **{b['date']: b for b in fetched[s]['bars'] if b['date'] not in oldmap}}
        merged[s] = {**fetched[s], 'bars': [combined[d] for d in sorted(combined)]}
    require_aligned(merged, benchmark=req['benchmark'], expected_symbols=scope)
    from .quant_paper_calendar import check_update
    # Check the fresh response independently, before old bars could hide a
    # common provider gap in the merged snapshot. Both must match the plan.
    fresh_calendar = await _paper_io(check_update, calendar, fetched, req['start_date'], end)
    merged_calendar = await _paper_io(check_update, calendar, merged, req['start_date'], end)
    result = await _paper_io(engine.replay, merged, req['symbols'], paper['parameters'], state=paper['state'])
    excluded = {'id','experiment','revision','status','updated'}
    payload = {k: v for k, v in paper.items() if k not in excluded}
    payload.pop('last_error', None)
    payload.update(state=result['state'], dataset=merged, fills=paper['fills']+result['fills'], curve=paper['curve']+result['curve'],
                   last_check=store.now(), blocked_last_update=result['blocked'],
                   calendar_admission=calendar['proof'], calendar_update={'fresh': fresh_calendar, 'merged': merged_calendar})
    local = datetime.now(timezone(timedelta(hours=8)))
    if local.strftime('%H:%M') >= '16:30' and engine.validate_dataset(merged)[-1] == local.date().isoformat():
        payload['last_eod_date'] = local.date().isoformat()
    from .quant_paper_calendar import commit_guard
    frozen_guard = commit_guard(owner, parent['id'], calendar)
    attempt = _paper_attempt.get()
    def guard(c):
        frozen_guard(c)
        if attempt is not None: attempt.admit_commit()
    return await _paper_io(store.save_paper, owner, identity, paper['revision'], payload, guard=guard)


def pause(owner, identity):
    paper = store.get(owner, identity, True)
    if not paper: raise LookupError('模拟账户不存在')
    return store.save_paper(owner, identity, paper['revision'],
                            {k: v for k,v in paper.items() if k not in {'id','revision','status','updated','experiment'}}, status='paused')


def paper_due(payload, local):
    # A manual midday check must not suppress the closing-session update.
    return local.weekday()<5 and local.strftime('%H:%M')>='16:30' and payload.get('last_eod_date') != local.date().isoformat()


async def scheduler():
    import json
    while True:
        try:
            await asyncio.to_thread(store.expire_interrupted)
            local = datetime.now(timezone(timedelta(hours=8)))
            if local.weekday()<5 and local.strftime('%H:%M')>='16:30':
                for row in await asyncio.to_thread(store.active_papers):
                    payload = json.loads(row['payload'])
                    if not paper_due(payload,local):
                        continue
                    try:
                        await asyncio.wait_for(refresh_paper(row['owner'],row['id']),60)
                    except asyncio.CancelledError: raise
                    except Exception:
                        # Preserve cash/fills on failure; persist an owner-visible
                        # attempt separately without advancing the account cursor.
                        paper=await _paper_io(store.get,row['owner'],row['id'],True)
                        if paper and paper['status']=='active':
                            p={k:v for k,v in paper.items() if k not in {'id','experiment','revision','status','updated'}}
                            p['last_error']='未能确认本次同步结果，请核对账户状态后重试'
                            try: await _paper_io(store.save_paper,row['owner'],row['id'],paper['revision'],p)
                            except RuntimeError: pass
        except asyncio.CancelledError: raise
        except Exception: pass
        await asyncio.sleep(900)


def attach_lifespan(app):
    from contextlib import asynccontextmanager
    if getattr(app,'_quant_research_attached',False): return
    original=app.router.lifespan_context
    @asynccontextmanager
    async def composed(application):
        global _admissions_open
        async with original(application) as state:
            # Fail startup before the blue/green ready gate if the new ledger is not writable.
            def initialize():
                with store.connect() as c: c.execute("SELECT count(*) FROM qr_experiment").fetchone()
            await asyncio.to_thread(initialize)
            _admissions_open = True
            task=asyncio.create_task(scheduler())
            try: yield state
            finally:
                _admissions_open = False
                task.cancel()
                admissions = list(_admission_tasks)
                try:
                    await _cancel_and_drain(admissions)
                finally:
                    # No admission can schedule a worker after this snapshot.
                    pending=list(_tasks.values())+list(_refresh_tasks.values())
                    await _cancel_and_drain([task,*pending])
    app.router.lifespan_context=composed
    app._quant_research_attached=True


def reproduce(owner, identity, key):
    job=store.get(owner, identity)
    if not job: raise LookupError('实验不存在')
    if job['status']!='completed': raise ValueError('仅可复核已完成实验')
    if job['kernel']!=kernel_identity(): raise ValueError('执行内核已变化，不能冒充原版本复核')
    return submit(owner,key,{**job['request'],'replay_of':identity})


async def reproduce_async(owner, identity, key):
    job = await _paper_io(store.get, owner, identity)
    if not job: raise LookupError('实验不存在')
    if job['status'] != 'completed': raise ValueError('仅可复核已完成实验')
    if job['kernel'] != kernel_identity(): raise ValueError('执行内核已变化，不能冒充原版本复核')
    return await submit_async(owner, key, {**job['request'], 'replay_of':identity})


async def refresh_paper(owner, identity):
    key=(owner,identity)
    if key in _refreshing: raise RuntimeError('这个模拟组合正在同步，请稍后刷新')
    if len(_refreshing)>=4 or sum(k[0]==owner for k in _refreshing)>=2:
        raise RuntimeError('同步请求已满，请稍后重试')
    attempt = _PaperAttempt()
    async def run():
        token = _paper_attempt.set(attempt)
        try: return await _refresh_paper(owner, identity)
        finally: _paper_attempt.reset(token)
    def finished(task):
        if _refresh_tasks.get(key) is task:
            _refresh_tasks.pop(key, None)
            _refreshing.discard(key)
        if not task.cancelled(): task.exception()  # consume abandoned failures
    _refreshing.add(key)
    task = asyncio.create_task(run())
    _refresh_tasks[key] = task
    task.add_done_callback(finished)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        attempt.cancel()
        task.cancel()
        raise
    finally:
        if task.done(): finished(task)


# Pin bytes during module import, before traffic or a later source publisher.
# An old worker must never advertise a newer on-disk kernel as its own code.
kernel_identity()
