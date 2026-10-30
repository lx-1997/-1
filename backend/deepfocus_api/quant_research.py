"""Durable, bounded research orchestration and end-of-day paper tracking."""
import asyncio
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
BENCHMARKS = {'000300': '沪深300', '000905': '中证500', '000852': '中证1000'}


@lru_cache(maxsize=1)
def kernel_identity():
    files = ('quant_research_engine.py', 'quant_research_candidates.py', 'quant_research.py', 'quant_calendar.py', 'backtest_data.py', 'ai_fund_ledger.py', 'ai_fund_policy.py')
    return engine.digest({f: hashlib.sha256(Path(__file__).with_name(f).read_bytes()).hexdigest() for f in files})


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


async def execute(owner, identity, request):
    try:
        store.update(owner, identity, progress=8, stage='获取真实不复权日线')
        reference = None
        if request.get('replay_of'):
            reference = store.get(owner, request['replay_of'])
            if not reference or reference['status'] != 'completed' or reference['kernel'] != kernel_identity():
                raise ValueError('原实验或冻结内核不可用于复核')
            dataset = reference['dataset']
        else:
            dataset = await fetch_dataset(request['symbols']+[request['benchmark']], request['start_date'], request['end_date'])
        q = quality(dataset, request['start_date'], request['end_date'],
                    benchmark=request['benchmark'], expected_symbols=request['symbols']+[request['benchmark']])
        if not store.update(owner, identity, progress=30, stage='真实行情已封存，开始训练与滚动验证', extra={'dataset': dataset, 'quality': q}): return
        # SQLite callback also observes cancellation; the CPU worker then exits
        # at the next fold, and its terminal result is never written over cancel.
        def progress(value, stage):
            if not store.update(owner, identity, progress=value, stage=stage): raise InterruptedError('cancelled')
        result = await asyncio.wait_for(asyncio.to_thread(engine.walk_forward, dataset, request['symbols'], request['benchmark'], request['capital'], progress), 180)
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
        store.update(owner, identity, status='completed', progress=100, stage='冻结数据复核一致' if reference else '实验完成，研究门槛已核对',
                     extra={'result': result, 'result_digest': engine.digest(result)})
    except asyncio.CancelledError:
        store.update(owner, identity, status='failed', stage='执行进程关闭，实验已中断')
        raise
    except InterruptedError:
        pass
    except CalendarAlignmentError as exc:
        extra = {'error_code': 'calendar_alignment_rejected', 'quality': exc.report}
        if exc.dataset is not None:
            extra['dataset'] = exc.dataset
        store.update(owner, identity, status='failed', stage=str(exc), extra=extra)
    except Exception as exc:
        # No provider response bodies, keys or connection strings in public errors.
        message = str(exc) if isinstance(exc, ValueError) else '数据源或计算暂不可用，请稍后重试；失败实验已保留'
        store.update(owner, identity, status='failed', stage=message, extra={'error_code': type(exc).__name__})
    finally:
        _tasks.pop(identity, None)


def submit(owner, key, request):
    job, fresh = store.create(owner, key, request, kernel_identity())
    if fresh:
        _tasks[job['id']] = asyncio.create_task(execute(owner, job['id'], request))
    return {k: v for k, v in job.items() if k not in {'dataset'}}


def _calendar_veto(owner, parent):
    """A recorded date defect on these exact inputs cannot be ignored by paper tracking."""
    if parent is None:
        raise ValueError('来源实验不存在，不能继续模拟跟踪')
    with store.connect() as c:
        rows = c.execute("SELECT payload FROM qr_experiment WHERE owner=? AND status='completed' AND json_extract(payload,'$.request.kind')='calendar_review' AND json_extract(payload,'$.request.parent_id')=?",
                         (owner, parent['id'])).fetchall()
    for row in rows:
        payload = json.loads(row['payload']); request = payload['request']
        if (request['parent_digest'] != parent['result_digest']
                or request['parent_request_digest'] != engine.digest(parent['request'])
                or request['reference_kernel'] != parent['kernel']):
            continue
        result = payload.get('result')
        if not result or engine.digest(result) != payload.get('result_digest'):
            raise ValueError('日历核验完整性异常，禁止继续模拟跟踪')
        if result['quality']['status'] != 'matches_plan':
            raise ValueError('该封存实验已发现日历差异，禁止晋级或继续模拟；请修正数据并新建实验')


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
    _calendar_veto(owner, job)
    if not job['result']['gate']['passed']: raise ValueError('未通过研究门槛，不能转入模拟跟踪')
    # Start flat after the sealed experiment; never backdate a live paper account.
    today = complete_day()
    state = engine.initial_state(job['request']['capital']); state['cursor'] = today
    return store.create_paper(owner, identity, {'request': job['request'], 'parameters': job['result']['parameters'],
                  'kernel': job['kernel'], 'dataset': job['dataset'], 'state': state, 'fills': [], 'curve': [],
                  'started': store.now(), 'start_after': today, 'last_check': None,
                  'experiment_digest': job['result_digest'], 'policy': 'research_only_not_broker_execution'})


async def _refresh_paper(owner, identity):
    paper = store.get(owner, identity, True)
    if not paper: raise LookupError('模拟账户不存在')
    if paper['status'] != 'active': raise ValueError('模拟跟踪已暂停')
    if paper['kernel'] != kernel_identity(): raise ValueError('冻结内核与当前版本不一致，已拒绝执行')
    _calendar_veto(owner, store.get(owner, paper['experiment']))
    req, old = paper['request'], paper['dataset']
    scope = req['symbols']+[req['benchmark']]
    require_aligned(old, benchmark=req['benchmark'], expected_symbols=scope)
    # Fetch a sufficiently overlapping history and refuse revisions instead of
    # silently rewriting signals or historical fills.
    fetched = await fetch_dataset(req['symbols']+[req['benchmark']], req['start_date'], complete_day())
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
    result = await asyncio.to_thread(engine.replay, merged, req['symbols'], paper['parameters'], state=paper['state'])
    excluded = {'id','experiment','revision','status','updated'}
    payload = {k: v for k, v in paper.items() if k not in excluded}
    payload.pop('last_error', None)
    payload.update(state=result['state'], dataset=merged, fills=paper['fills']+result['fills'], curve=paper['curve']+result['curve'],
                   last_check=store.now(), blocked_last_update=result['blocked'])
    local = datetime.now(timezone(timedelta(hours=8)))
    if local.strftime('%H:%M') >= '16:30' and engine.validate_dataset(merged)[-1] == local.date().isoformat():
        payload['last_eod_date'] = local.date().isoformat()
    return store.save_paper(owner, identity, paper['revision'], payload)


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
                        paper=store.get(row['owner'],row['id'],True)
                        if paper and paper['status']=='active':
                            p={k:v for k,v in paper.items() if k not in {'id','experiment','revision','status','updated'}}
                            p['last_error']='日线同步失败，账户未改写，等待下次核对'
                            try: store.save_paper(row['owner'],row['id'],paper['revision'],p)
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
        async with original(application) as state:
            # Fail startup before the blue/green ready gate if the new ledger is not writable.
            def initialize():
                with store.connect() as c: c.execute("SELECT count(*) FROM qr_experiment").fetchone()
            await asyncio.to_thread(initialize)
            task=asyncio.create_task(scheduler())
            try: yield state
            finally:
                task.cancel()
                pending=list(_tasks.values())
                for job in pending: job.cancel()
                await asyncio.gather(task,*pending,return_exceptions=True)
    app.router.lifespan_context=composed
    app._quant_research_attached=True


def reproduce(owner, identity, key):
    job=store.get(owner, identity)
    if not job: raise LookupError('实验不存在')
    if job['status']!='completed': raise ValueError('仅可复核已完成实验')
    if job['kernel']!=kernel_identity(): raise ValueError('执行内核已变化，不能冒充原版本复核')
    return submit(owner,key,{**job['request'],'replay_of':identity})


async def refresh_paper(owner, identity):
    key=(owner,identity)
    if key in _refreshing: raise RuntimeError('这个模拟组合正在同步，请稍后刷新')
    if len(_refreshing)>=4 or sum(k[0]==owner for k in _refreshing)>=2:
        raise RuntimeError('同步请求已满，请稍后重试')
    _refreshing.add(key)
    try: return await _refresh_paper(owner,identity)
    finally: _refreshing.discard(key)


# Pin bytes during module import, before traffic or a later source publisher.
# An old worker must never advertise a newer on-disk kernel as its own code.
kernel_identity()
