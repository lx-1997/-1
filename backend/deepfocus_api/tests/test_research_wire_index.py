import asyncio
from contextlib import closing
import json
import sqlite3
import threading
import time
from types import SimpleNamespace
import pytest
from deepfocus_api import content_ontology as ontology, research_wire as wire, research_wire_index as index
from deepfocus_api.bounded_cache import ByteBoundedTTLCache


def items(count=1):
    return [dict(file_id=f'report-{n}', filename=f'半导体需求报告{n}.pdf', title=f'半导体需求报告{n}',
                 org='海外投行', date='2026-10-01', created_at='2026-10-01T08:00:00Z') for n in range(count)]


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    path=tmp_path/'ontology.sqlite3'
    monkeypatch.setenv('DEEPFOCUS_CONTENT_ONTOLOGY_DB_PATH',str(path))
    monkeypatch.setattr(index,'_SLOT',threading.BoundedSemaphore(1))
    monkeypatch.setattr(wire,'_ANNOTATED_WIRE_IDS',set())
    monkeypatch.setattr(wire,'_WIRE_REFRESH_TASKS',{})
    monkeypatch.setattr(wire,'_WIRE_CACHE',ByteBoundedTTLCache(max_entries=24,max_bytes=16*1024**2,ttl_seconds=3600))
    monkeypatch.setattr(wire,'_auth_payload',lambda:{})
    class Response:
        def raise_for_status(self): pass
        def json(self): return {'items':[{'fileId':i['file_id'],'name':i['filename'],'createTime':i['created_at']} for i in items(2)]}
    class Client:
        def __init__(self,**kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def post(self,*args,**kwargs): return Response()
    monkeypatch.setattr(wire.httpx,'AsyncClient',Client)
    return path


def counts(path):
    with closing(sqlite3.connect(path)) as c:
        return [c.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in
                ['content_objects','ontology_tags','content_tag_links','ontology_entities','content_entity_links']]


@pytest.mark.asyncio
async def test_wire_normalization_really_persists_ids_tags_clock_with_one_closed_connection(isolated,monkeypatch):
    opened=[]; real=ontology._connect
    def connect(**kwargs):
        c=real(**kwargs);opened.append(c);return c
    monkeypatch.setattr(ontology,'_connect',connect)
    result=await wire.fetch_research_wire_online()
    assert result['indexing']=={'indexed_ids':2,'pending_reports_upper_bound':0,'complete':True}
    assert wire._ANNOTATED_WIRE_IDS=={'report-0','report-1'} and len(opened)==1
    with pytest.raises(sqlite3.ProgrammingError):opened[0].execute('SELECT 1')
    with closing(sqlite3.connect(isolated)) as c:
        rows=c.execute('SELECT id,title,summary,metadata_json FROM content_objects ORDER BY id').fetchall()
        assert [r[0] for r in rows]==['wire:report-0','wire:report-1']
        assert rows[0][2]=='海外投行 2026-10-01'
        assert json.loads(rows[0][3])['collected_at']==result['fetched_at']
        assert not c.execute('PRAGMA foreign_key_check').fetchall()
    assert counts(isolated)[2]>0


@pytest.mark.asyncio
async def test_metadata_cannot_overwrite_existing_reviewed_content(isolated):
    ontology.annotate_content(content_id='wire:report-0',content_type='research',title='Reviewed title',text='Reviewed full content',source_name='Reviewed source')
    with closing(sqlite3.connect(isolated)) as c:before=c.execute("SELECT * FROM content_objects WHERE id='wire:report-0'").fetchone()
    result=await wire.fetch_research_wire_online()
    with closing(sqlite3.connect(isolated)) as c:after=c.execute("SELECT * FROM content_objects WHERE id='wire:report-0'").fetchone()
    assert after==before and result['indexing']['complete']


@pytest.mark.asyncio
async def test_midbatch_failure_rolls_back_every_table_and_is_retryable(isolated):
    ontology.init_content_ontology_db()
    with closing(sqlite3.connect(isolated)) as c:
        c.execute("CREATE TRIGGER reject_second BEFORE INSERT ON content_objects WHEN NEW.id='wire:report-1' BEGIN SELECT RAISE(ABORT,'test failure'); END");c.commit()
    result=await wire.fetch_research_wire_online()
    assert not result['indexing']['complete'] and result['indexing']['reason']=='IntegrityError'
    assert counts(isolated)==[0,0,0,0,0] and not wire._ANNOTATED_WIRE_IDS
    with closing(sqlite3.connect(isolated)) as c:c.execute('DROP TRIGGER reject_second');c.commit()
    result=await wire.fetch_research_wire_online(use_cache=False)
    assert result['indexing']['complete'] and counts(isolated)[0]==2


@pytest.mark.asyncio
async def test_cancellation_retains_real_thread_capacity_and_event_loop_is_live(isolated,monkeypatch):
    entered,release=threading.Event(),threading.Event(); real=ontology.persist_annotations; threads=[]
    def blocking(*args,**kwargs):
        threads.append(threading.get_ident());entered.set()
        if not release.wait(2):raise TimeoutError('test worker release')
        return real(*args,**kwargs)
    monkeypatch.setattr(ontology,'persist_annotations',blocking)
    prior=set(asyncio.all_tasks())
    first=asyncio.create_task(index.index_wire_metadata(items(),observed_at='observed',seen=wire._ANNOTATED_WIRE_IDS))
    try:
        async def wait_entered():
            while not entered.is_set():await asyncio.sleep(.001)
        await asyncio.wait_for(wait_entered(),1)
        assert asyncio.all_tasks()-prior=={first}
        assert threads==[threads[0]] and threads[0]!=threading.get_ident()
        first.cancel();await asyncio.sleep(.01)
        assert not first.done() and not wire._ANNOTATED_WIRE_IDS
        second=await index.index_wire_metadata(items(),observed_at='observed',seen=wire._ANNOTATED_WIRE_IDS)
        assert not second['complete'] and second['reason']=='busy' and len(threads)==1
    finally:
        release.set();out=await asyncio.gather(first,return_exceptions=True)
    assert isinstance(out[0],asyncio.CancelledError)
    assert counts(isolated)[0]==1 and wire._ANNOTATED_WIRE_IDS=={'report-0'}


@pytest.mark.asyncio
async def test_real_writer_lock_yields_pending_then_retries(isolated):
    ontology.init_content_ontology_db();locker=sqlite3.connect(isolated);locker.execute('BEGIN IMMEDIATE')
    task=asyncio.create_task(index.index_wire_metadata(items(),observed_at='observed',seen=wire._ANNOTATED_WIRE_IDS))
    try:
        await asyncio.sleep(.05);assert not task.done()
        result=await asyncio.wait_for(asyncio.shield(task),4)
        assert not result['complete'] and result['reason']=='OperationalError'
        assert not wire._ANNOTATED_WIRE_IDS
    finally:
        locker.rollback();locker.close();await asyncio.gather(task,return_exceptions=True)
    result=await index.index_wire_metadata(items(),observed_at='observed',seen=wire._ANNOTATED_WIRE_IDS)
    assert result['complete'] and counts(isolated)[0]==1


def test_expired_deadline_and_duplicate_ids_fail_before_creating_database(isolated):
    a=ontology.annotate_content(content_id='one',content_type='research',title='title',persist=False)
    with pytest.raises(TimeoutError):ontology.persist_annotations([a],deadline=time.monotonic()-1)
    with pytest.raises(ValueError,match='Unique'):ontology.persist_annotations([a,a])
    with pytest.raises(ValueError,match='500'):ontology.persist_annotations([a]*501)
    assert not isolated.exists()


def test_deadline_midbatch_rolls_back_data(isolated,monkeypatch):
    clock=[0.0];monkeypatch.setattr(ontology,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    real=ontology._persist_annotation_record
    def persist(*args):real(*args);clock[0]=2
    monkeypatch.setattr(ontology,'_persist_annotation_record',persist)
    values=[ontology.annotate_content(content_id=str(n),content_type='research',title='title',persist=False) for n in range(2)]
    with pytest.raises(TimeoutError):ontology.persist_annotations(values,deadline=1)
    assert counts(isolated)==[0,0,0,0,0]


@pytest.mark.asyncio
async def test_input_budget_failure_does_not_consume_capacity_or_mark_success(isolated):
    bad=items();bad[0]['title']='x'*16385
    result=await index.index_wire_metadata(bad,observed_at='observed',seen=wire._ANNOTATED_WIRE_IDS)
    assert not result['complete'] and not isolated.exists() and not wire._ANNOTATED_WIRE_IDS
    assert (await index.index_wire_metadata(items(),observed_at='observed',seen=wire._ANNOTATED_WIRE_IDS))['complete']


@pytest.mark.asyncio
async def test_executor_rejection_returns_permit(isolated,monkeypatch):
    loop=asyncio.get_running_loop();real=loop.run_in_executor
    def rejected(*args,**kwargs):raise RuntimeError('executor unavailable')
    monkeypatch.setattr(loop,'run_in_executor',rejected)
    result=await index.index_wire_metadata(items(),observed_at='observed',seen=wire._ANNOTATED_WIRE_IDS)
    assert not result['complete'] and not isolated.exists()
    monkeypatch.setattr(loop,'run_in_executor',real)
    assert (await index.index_wire_metadata(items(),observed_at='observed',seen=wire._ANNOTATED_WIRE_IDS))['complete']


@pytest.mark.asyncio
async def test_incompatible_existing_identity_keeps_old_content_and_rolls_back_new(isolated):
    ontology.annotate_content(content_id='wire:report-1',content_type='flash',title='Existing other kind')
    before=counts(isolated)
    result=await wire.fetch_research_wire_online()
    assert not result['indexing']['complete'] and result['indexing']['reason']=='ValueError'
    assert not wire._ANNOTATED_WIRE_IDS and counts(isolated)==before
    with closing(sqlite3.connect(isolated)) as c:
        assert c.execute('SELECT id,title FROM content_objects').fetchall()==[('wire:report-1','Existing other kind')]
