import asyncio
import copy
import json

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from deepfocus_api import quant_calendar as calendar
from deepfocus_api import quant_research as service, quant_research_store as store
from deepfocus_api import quant_research_engine as engine, quant_research_api as api
from deepfocus_api import quant_event_service as event_service
from deepfocus_api.tests.test_quant_research_loop import dataset, request


@pytest_asyncio.fixture(autouse=True)
async def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('DEEPFOCUS_QUANT_RESEARCH_DB_PATH', str(tmp_path/'research.sqlite3'))
    monkeypatch.setattr(service, '_tasks', {})
    yield
    for task in service._tasks.values():
        task.cancel()
    await asyncio.gather(*list(service._tasks.values()), return_exceptions=True)


def check(data):
    return calendar.require_aligned(data, benchmark='000300', expected_symbols=['600519', '000300'])


@pytest.mark.parametrize('symbol,index', [('600519', 0), ('600519', 150), ('600519', -1), ('000300', 150)])
def test_missing_interior_start_tail_or_benchmark_is_not_trimmed(symbol, index):
    data = dataset()
    missing = data[symbol]['bars'].pop(index)['date']
    original = copy.deepcopy(data)
    # Preserve the old calculation kernel for exact historical reproduction.
    assert len(engine.validate_dataset(data)) == 419
    with pytest.raises(calendar.CalendarAlignmentError) as error:
        check(data)
    report = error.value.report
    assert report['reason'] == 'series_dates_differ'
    assert report['symbols'][symbol]['missing_vs_union'] == [missing]
    assert report['union_sessions'] == 420 and report['common_sessions'] == 419
    assert error.value.dataset == original and data == original


def test_mutual_alignment_does_not_claim_independent_exchange_calendar():
    data = dataset()
    for p in data.values():
        p['bars'].pop(150)
    result = check(data)
    assert result['status'] == 'aligned' and result['common_sessions'] == 419
    assert not result['exchange_calendar_verified']
    assert not result['all_series_missing_same_session_detectable']
    assert not result['forward_fill'] and not result['intersection_trimming']


def test_explicit_zero_volume_bar_is_retained_without_inventing_suspension():
    data = dataset()
    data['600519']['bars'][150]['volume'] = 0
    report = check(data)
    assert report['common_sessions'] == 420
    assert not report['missing_bar_is_suspension']
    assert data['600519']['bars'][150]['volume'] == 0


@pytest.mark.parametrize('damage', ['scope', 'empty', 'nan', 'date'])
def test_scope_or_malformed_input_fails_without_provider_text_leak(damage):
    data = dataset()
    if damage == 'scope':
        data.pop('000300')
    elif damage == 'empty':
        data['600519']['bars'] = []
    elif damage == 'nan':
        data['600519']['bars'][0]['close'] = float('nan')
    else:
        data['600519']['bars'][0]['date'] = 'secret-provider-token-canary'
    with pytest.raises(calendar.CalendarAlignmentError) as exc:
        check(data)
    assert 'canary' not in str(exc.value)
    assert 'canary' not in json.dumps(exc.value.report)
    if damage != 'scope':
        assert exc.value.dataset is None


@pytest.mark.asyncio
async def test_real_fetch_adapter_boundary_rejects_provider_calendar_gap(monkeypatch):
    data = dataset()
    data['600519']['bars'].pop(150)
    async def provider(meta, start, end):
        return copy.deepcopy(data[meta['symbol']])
    monkeypatch.setattr(service, '_fetch_sina_cn', provider)
    with pytest.raises(calendar.CalendarAlignmentError) as exc:
        await service.fetch_dataset(['600519', '000300'], '2023-01-01', '2024-08-09')
    assert exc.value.report['anchor_symbol'] == '000300'
    assert exc.value.dataset['600519']['bars'] == data['600519']['bars']


@pytest.mark.asyncio
async def test_failed_research_keeps_private_gap_snapshot_and_export_has_no_prices(monkeypatch):
    data = dataset()
    data['600519']['bars'].pop(150)
    async def fetch(*args):
        return copy.deepcopy(data)
    def forbidden(*args):
        raise AssertionError('No training may begin with missing calendar data')
    monkeypatch.setattr(service, 'fetch_dataset', fetch)
    monkeypatch.setattr(engine, 'walk_forward', forbidden)
    created = service.submit('owner', 'calendar-gap-key', request())
    await service._tasks[created['id']]
    failed = store.get('owner', created['id'])
    assert failed['status'] == 'failed' and failed['dataset'] == data
    assert failed['error_code'] == 'calendar_alignment_rejected'
    assert failed['quality']['reason'] == 'series_dates_differ'
    assert 'result' not in failed and failed['quality']['status'] == 'rejected'
    app = FastAPI(); app.include_router(api.router)
    app.dependency_overrides[api.owner] = lambda: 'owner'
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://fixture') as client:
        path = '/api/quant-research/experiments/'+created['id']
        for suffix in ('', '/export'):
            response = await client.get(path+suffix)
            assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
            assert 'dataset' not in response.json() and 'bars' not in response.text
        app.dependency_overrides[api.owner] = lambda: 'other'
        assert (await client.get(path)).status_code == 404
        assert (await client.get(path+'/export')).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize('where', ['stored', 'fresh'])
async def test_paper_gap_refuses_without_touching_active_financial_ledger(monkeypatch, where):
    data = dataset()
    old = {s: {**p, 'bars': copy.deepcopy(p['bars'][:200])} for s, p in data.items()}
    result = engine.replay(old, ['600519'], engine.CANDIDATES[0])
    assert result['fills'] and result['state']['positions']
    if where == 'stored':
        old['600519']['bars'].pop(100)
    else:
        # Old history could otherwise mask the fresh provider's omitted day.
        data['000300']['bars'].pop(100)
    paper = store.create_paper('owner', 'isolated-gap-paper', {
        'request': request(), 'kernel': service.kernel_identity(), 'dataset': old,
        'state': result['state'], 'parameters': engine.CANDIDATES[0],
        'fills': result['fills'], 'curve': result['curve']})
    before = copy.deepcopy(store.get('owner', paper['id'], True))
    async def fetch(*args):
        return data
    monkeypatch.setattr(service, 'fetch_dataset', fetch)
    with pytest.raises(calendar.CalendarAlignmentError):
        await service.refresh_paper('owner', paper['id'])
    assert store.get('owner', paper['id'], True) == before


def test_forged_passed_gate_cannot_bypass_calendar_at_promotion_or_event_reference():
    data = dataset()
    data['000300']['bars'].pop(150)
    # Deliberately minimal test-only passed fixture: calendar is an independent guard.
    result = {'dataset_digest': engine.digest(data), 'gate': {'passed': True}, 'folds': [1]}
    job, _ = store.create('owner', 'bad-calendar-promote', request(), service.kernel_identity())
    store.update('owner', job['id'], status='completed',
                 extra={'dataset': data, 'result': result, 'result_digest': engine.digest(result)})
    with pytest.raises(calendar.CalendarAlignmentError):
        service.promote('owner', job['id'])
    with pytest.raises(calendar.CalendarAlignmentError):
        event_service.reference('owner', job['id'], 'unused-bundle')
    assert store.list_records('owner', True) == []
