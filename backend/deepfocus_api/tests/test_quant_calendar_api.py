import asyncio
import copy
import hashlib
import json
import sqlite3
import subprocess
import sys

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from deepfocus_api import quant_calendar_store as evidence, quant_calendar_service as service
from deepfocus_api import quant_research_store as jobs, quant_research_api as api
from deepfocus_api.quant_exchange_schedule import planned_sessions
from deepfocus_api.quant_research_engine import digest
from deepfocus_api.tests.test_quant_exchange_schedule import reviewed


@pytest_asyncio.fixture(autouse=True)
async def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv('DEEPFOCUS_QUANT_RESEARCH_DB_PATH', str(tmp_path/'research.sqlite3'))
    monkeypatch.setattr(service.lifecycle, '_tasks', {})
    yield
    pending = list(service.lifecycle._tasks.values())
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


def parent(manifest, *, gap=False):
    days = planned_sessions(manifest, '2024-01-02', '2024-03-29')
    if gap:
        days.pop(20)
    data = {s: {'source': 'unit_test_fixture', 'fetched_at': '2026-10-30T06:36:00+00:00',
                'adjustment': 'none', 'volume_unit': 'shares', 'is_synthetic': False,
                'bars': [{'date': d, 'open': 10., 'close': 10., 'high': 11., 'low': 9.,
                          'volume': 1000000} for d in days]} for s in ('600519', '000300')}
    request = {'name': 'isolated-calendar', 'symbols': ['600519'], 'benchmark': '000300',
               'start_date': '2024-01-02', 'end_date': '2024-03-29', 'capital': 1000000}
    result = {'dataset_digest': digest(data), 'gate': {'passed': False}}
    row, _ = jobs.create('a', 'parent-gap' if gap else 'parent', request, 'sealed-test-parent')
    jobs.update('a', row['id'], status='completed', extra={'dataset': data, 'result': result, 'result_digest': digest(result)})
    return jobs.get('a', row['id'])


def test_immutable_owned_idempotent_notices_and_quota(reviewed, monkeypatch):
    manifest, sources = reviewed
    bundle = evidence.put('a', 'annual fixture', manifest, sources)
    assert evidence.put('a', 'annual fixture', manifest, sources) == bundle
    assert evidence.summary('b', bundle['id']) is None and evidence.listing('b') == []
    assert not bundle['actual_exchange_sessions_certified'] and not bundle['promotion_allowed']
    assert 'calendar' not in bundle and 'source_files' not in bundle
    assert evidence.load('a', bundle['id'])[2] == sources
    with evidence.connect() as c:
        with pytest.raises(sqlite3.IntegrityError):
            c.execute('DELETE FROM qr_calendar_bundle WHERE id=?', (bundle['id'],))
        with pytest.raises(sqlite3.IntegrityError):
            c.execute('UPDATE qr_calendar_source SET body=? WHERE owner=?', (b'changed', 'a'))
    monkeypatch.setattr(evidence, 'MAX_OWNER', 1)
    with pytest.raises(RuntimeError, match='额度'):
        evidence.put('b', 'too large', manifest, sources)
    with evidence.connect() as c:
        assert c.execute('SELECT count(*) FROM qr_calendar_source WHERE owner=?', ('b',)).fetchone()[0] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('gap', [False, True])
async def test_owned_api_task_exact_verification_export_and_no_promotion(reviewed, gap):
    manifest, sources = reviewed
    p = parent(manifest, gap=gap)
    bundle = evidence.put('a', 'annual fixture', manifest, sources)
    app = FastAPI(); app.include_router(api.router); app.dependency_overrides[api.owner] = lambda: 'a'
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://fixture') as client:
        proof = await client.get('/api/quant-research/calendar-evidence')
        assert proof.status_code == 200 and proof.json()['items'] == [bundle]
        assert 'source_files' not in proof.text and 'synthetic fixture' not in proof.text
        path = f"/api/quant-research/experiments/{p['id']}/calendar-review"
        payload = {'request_id': 'calendar-key', 'bundle_id': bundle['id']}
        submitted = await client.post(path, json=payload)
        assert submitted.status_code == 202
        identity = submitted.json()['id']
        assert (await client.post(path, json=payload)).json()['id'] == identity
        task = service.lifecycle._tasks.get(identity)
        if task:
            await task
        row = jobs.get('a', identity)
        assert row['status'] == 'completed' and row['progress'] == 100
        assert row['result']['quality']['status'] == ('rejected' if gap else 'matches_plan')
        assert not row['result']['gate']['passed'] and jobs.get('a', p['id']) == p
        detail = await client.get('/api/quant-research/experiments/'+identity)
        assert detail.json()['kernel_current'] and detail.headers['cache-control'] == 'no-store'
        replayed = await client.post(f'/api/quant-research/experiments/{identity}/replay', json={'request_id': 'calendar-verify'})
        assert replayed.status_code == 202
        task = service.lifecycle._tasks.get(replayed.json()['id'])
        if task:
            await task
        assert jobs.get('a', replayed.json()['id'])['result_digest'] == row['result_digest']
        exported = await client.get(f'/api/quant-research/experiments/{identity}/export')
        assert exported.status_code == 200 and 'dataset' not in exported.json()
        assert 'bars' not in exported.text and 'synthetic fixture' not in exported.text
        assert (await client.post(f'/api/quant-research/experiments/{identity}/paper')).status_code == 422
        assert (await client.post(path, json={**payload, 'request_id': 'bad-fields', 'path': '/tmp/arbitrary'})).status_code == 422
        assert jobs.list_records('a', True) == []
        app.dependency_overrides[api.owner] = lambda: 'b'
        assert (await client.get('/api/quant-research/calendar-evidence')).json() == {'items': []}
        for url in (f'/calendar-evidence/{bundle["id"]}', f'/experiments/{identity}', f'/experiments/{identity}/export'):
            assert (await client.get('/api/quant-research'+url)).status_code == 404
        assert (await client.post(path, json=payload)).status_code == 404


@pytest.mark.asyncio
async def test_anonymous_calendar_endpoints_are_not_public():
    app = FastAPI(); app.include_router(api.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://fixture') as client:
        assert (await client.get('/api/quant-research/calendar-evidence')).status_code == 401
        assert (await client.post('/api/quant-research/experiments/'+'a'*32+'/calendar-review', json={'bundle_id': 'b'*32})).status_code == 401


@pytest.mark.asyncio
async def test_cancel_after_computation_cannot_publish_result(reviewed, monkeypatch):
    manifest, sources = reviewed
    p = parent(manifest); bundle = evidence.put('a', 'fixture', manifest, sources)
    actual = service.check_dataset
    def cancelled(*args, **kwargs):
        row = next(r for r in jobs.list_records('a') if r['request'].get('kind') == 'calendar_review')
        jobs.cancel('a', row['id'])
        return actual(*args, **kwargs)
    monkeypatch.setattr(service, 'check_dataset', cancelled)
    created = await service.submit('a', p['id'], bundle['id'], 'cancel-calendar')
    task = service.lifecycle._tasks.get(created['id'])
    if task:
        await task
    row = jobs.get('a', created['id'])
    assert row['status'] == 'cancelled' and 'result' not in row


@pytest.mark.asyncio
async def test_worker_error_is_safe_and_new_tasks_share_existing_limits(reviewed, monkeypatch):
    manifest, sources = reviewed
    p = parent(manifest); bundle = evidence.put('a', 'fixture', manifest, sources)
    def bad(*args, **kwargs):
        raise ValueError('secret-provider-token-canary')
    monkeypatch.setattr(service, 'check_dataset', bad)
    created = await service.submit('a', p['id'], bundle['id'], 'bad-calendar')
    task = service.lifecycle._tasks.get(created['id'])
    if task:
        await task
    row = jobs.get('a', created['id'])
    assert row['status'] == 'failed' and 'canary' not in json.dumps(row)
    for n in range(2):
        jobs.create('a', 'existing-job-'+str(n), {'name': str(n)}, 'fixture')
    with pytest.raises(RuntimeError, match='任务已满'):
        await service.submit('a', p['id'], bundle['id'], 'limited-calendar')


def test_cold_backup_retains_manifest_raw_notices_and_owner_scope(tmp_path, reviewed, monkeypatch):
    manifest, sources = reviewed
    bundle = evidence.put('a', 'fixture', manifest, sources)
    target = tmp_path/'cold.sqlite3'
    with evidence.connect() as source:
        backup = sqlite3.connect(target)
        try:
            source.backup(backup)
            backup.execute('PRAGMA journal_mode=DELETE')
        finally:
            backup.close()
    code = ("import sqlite3,sys,json,hashlib; c=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True); "
            "print(json.dumps({'sources':{r[0]:hashlib.sha256(r[1]).hexdigest() for r in c.execute('SELECT sha,body FROM qr_calendar_source WHERE owner=?',('a',))},"
            "'other':c.execute('SELECT count(*) FROM qr_calendar_bundle WHERE owner=?',('b',)).fetchone()[0]})); c.close()")
    first_read = subprocess.run([sys.executable, '-c', code, str(target)], capture_output=True, text=True, check=True)
    cold = json.loads(first_read.stdout)
    assert cold['other'] == 0 and cold['sources'] == {sha: sha for sha in sources}
    with monkeypatch.context() as restored:
        restored.setenv('DEEPFOCUS_QUANT_RESEARCH_DB_PATH', str(target))
        assert evidence.load('a', bundle['id'])[2] == sources
        assert evidence.summary('b', bundle['id']) is None
    assert all(hashlib.sha256(v).hexdigest() == k for k, v in sources.items())


@pytest.mark.asyncio
async def test_changed_execution_version_and_corrupt_retained_html_are_rejected(reviewed, monkeypatch):
    manifest, sources = reviewed
    p = parent(manifest); bundle = evidence.put('a', 'fixture', manifest, sources)
    created = await service.submit('a', p['id'], bundle['id'], 'version-calendar')
    task = service.lifecycle._tasks.get(created['id'])
    if task:
        await task
    with monkeypatch.context() as upgraded:
        upgraded.setattr(service, 'KERNEL', 'different-version')
        with pytest.raises(ValueError, match='冻结版本'):
            await service.submit('a', p['id'], bundle['id'], 'wrong-version', verification_of=created['id'])
    # Isolated corruption drill deliberately bypasses the normal immutable trigger.
    with evidence.connect() as c:
        c.execute('DROP TRIGGER qr_calendar_source_update')
        c.execute('UPDATE qr_calendar_source SET body=? WHERE owner=? AND sha=?', (b'corrupt', 'a', next(iter(sources))))
    with pytest.raises(ValueError):
        evidence.load('a', bundle['id'])
    bad = await service.submit('a', p['id'], bundle['id'], 'corrupt-calendar')
    task = service.lifecycle._tasks.get(bad['id'])
    if task:
        await task
    failed = jobs.get('a', bad['id'])
    assert failed['status'] == 'failed' and 'result' not in failed


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['request', 'kernel'])
async def test_frozen_review_binds_parent_request_and_kernel(reviewed, field):
    manifest, sources = reviewed
    p = parent(manifest); bundle = evidence.put('a', 'fixture', manifest, sources)
    created = await service.submit('a', p['id'], bundle['id'], 'bind-calendar')
    task = service.lifecycle._tasks.get(created['id'])
    if task:
        await task
    # Isolated storage-corruption simulation, not a public mutation endpoint.
    with jobs.connect() as c:
        row = c.execute('SELECT payload FROM qr_experiment WHERE id=?', (p['id'],)).fetchone()
        payload = json.loads(row['payload'])
        if field == 'request':
            payload['request']['end_date'] = '2024-03-28'
        else:
            payload['kernel'] = 'changed-parent-kernel'
        c.execute('UPDATE qr_experiment SET payload=? WHERE id=?', (json.dumps(payload), p['id']))
    with pytest.raises(ValueError, match='冻结引用'):
        await service.submit('a', p['id'], bundle['id'], 'changed-parent', verification_of=created['id'])


@pytest.mark.asyncio
async def test_recorded_gap_vetoes_parent_promotion_and_preserves_existing_paper(reviewed, monkeypatch):
    manifest, sources = reviewed
    p = parent(manifest, gap=True); bundle = evidence.put('a', 'fixture', manifest, sources)
    created = await service.submit('a', p['id'], bundle['id'], 'veto-calendar')
    task = service.lifecycle._tasks.get(created['id'])
    if task:
        await task
    monkeypatch.setattr(service.lifecycle, 'kernel_identity', lambda: p['kernel'])
    with pytest.raises(ValueError, match='日历差异'):
        service.lifecycle.promote('a', p['id'])
    paper = jobs.create_paper('a', p['id'], {'request': p['request'], 'kernel': p['kernel'],
        'dataset': p['dataset'], 'state': {'cash': 998000, 'positions': {'600519': {'qty': 200, 'cost': 10}}},
        'fills': [], 'curve': []})
    before = copy.deepcopy(paper)
    async def forbidden(*args):
        raise AssertionError('Known invalid history must not fetch or advance a paper ledger')
    monkeypatch.setattr(service.lifecycle, 'fetch_dataset', forbidden)
    with pytest.raises(ValueError, match='日历差异'):
        await service.lifecycle.refresh_paper('a', paper['id'])
    assert jobs.get('a', paper['id'], True) == before

    app = FastAPI(); app.include_router(api.router); app.dependency_overrides[api.owner] = lambda: 'a'
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://fixture') as client:
        for path in (f'/experiments/{p["id"]}/paper', f'/paper/{paper["id"]}/refresh'):
            rejected = await client.post('/api/quant-research'+path)
            assert rejected.status_code == 422 and '日历差异' in rejected.json()['detail']
    assert jobs.get('a', paper['id'], True) == before


@pytest.mark.asyncio
async def test_paper_without_source_experiment_is_not_advanced(monkeypatch):
    paper = jobs.create_paper('a', 'missing-source', {'kernel': service.lifecycle.kernel_identity(),
        'state': {'cash': 998000, 'positions': {'600519': {'qty': 200, 'cost': 10}}},
        'fills': [], 'curve': []})
    async def forbidden(*args):
        raise AssertionError('Orphan paper must fail before data fetch')
    monkeypatch.setattr(service.lifecycle, 'fetch_dataset', forbidden)
    with pytest.raises(ValueError, match='来源实验不存在'):
        await service.lifecycle.refresh_paper('a', paper['id'])
    assert jobs.get('a', paper['id'], True) == paper
