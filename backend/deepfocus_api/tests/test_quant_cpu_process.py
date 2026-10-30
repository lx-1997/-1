"""Actual owned child lifetime and retained mathematical result, not fake PID expiry."""
import copy
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time

import pytest
from deepfocus_api import quant_research_store as jobs,quant_worker_slots as slots
from deepfocus_api import quant_cpu_process as cpu,quant_cpu_worker as child,quant_research_engine as engine
from deepfocus_api.tests.test_quant_research_loop import dataset,request


@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setenv('DEEPFOCUS_QUANT_RESEARCH_DB_PATH',str(tmp_path/'qr.sqlite3'))


def admission():
    req=request();data=dataset();kernel=engine.digest(cpu.pins())
    row,_=jobs.create('a','cpu-test',req,kernel)
    jobs.update('a',row['id'],progress=30,extra={'dataset':data})
    assert jobs.claim_worker('a',row['id'],'owned-ticket')
    return row['id'],req,data,kernel


def occupancy():
    with jobs.connect() as c:return slots.counts(c,'a')


def attach(identity,pid,token='owned-ticket',owner='a'):
    with jobs.connect() as c:
        c.execute('BEGIN IMMEDIATE');slots.attach_child(c,owner,identity,token,pid)


def test_actual_subprocess_matches_original_math_and_is_reaped_before_finish():
    identity,req,data,kernel=admission()
    result=cpu.run('a',identity,req,data,kernel)
    assert engine.digest(result)==engine.digest(engine.walk_forward(data,req['symbols'],req['benchmark'],req['capital']))
    with jobs.connect() as c:
        row=c.execute('SELECT * FROM qr_worker_slot').fetchone();assert row['child_pid'] is None and row['child_scope'] is None
    assert occupancy()==(1,1) and jobs.finish_worker('a',identity,'owned-ticket') and occupancy()==(0,0)


def test_legacy_six_column_schema_adds_child_metadata_without_losing_reservation():
    identity,_,_,_=admission()
    with jobs.connect() as c:
        row=tuple(c.execute('SELECT * FROM qr_worker_slot').fetchone())[:6]
        c.execute('DROP TABLE qr_worker_slot')
        c.execute('CREATE TABLE qr_worker_slot(experiment TEXT PRIMARY KEY,owner TEXT NOT NULL,pid INTEGER NOT NULL,scope TEXT,phase TEXT NOT NULL,token TEXT)')
        c.execute('INSERT INTO qr_worker_slot VALUES(?,?,?,?,?,?)',row)
    with jobs.connect() as c:
        current=tuple(c.execute('SELECT * FROM qr_worker_slot').fetchone());assert current[:6]==row and current[6:]==(None,None)
    assert occupancy()==(1,1) and jobs.finish_worker('a',identity,'owned-ticket')


@pytest.mark.parametrize('damage',['owner','token','self_pid','duplicate'])
def test_child_attach_is_bound_to_owned_active_reservation(damage):
    identity,_,_,_=admission();process=subprocess.Popen([sys.executable,'-c','import time; time.sleep(20)'])
    try:
        if damage=='duplicate':attach(identity,process.pid)
        with pytest.raises(ValueError):attach(identity,os.getpid() if damage=='self_pid' else process.pid,
            token='wrong' if damage=='token' else 'owned-ticket',owner='other' if damage=='owner' else 'a')
        assert occupancy()==(1,1)
    finally:process.terminate();process.wait()
    assert jobs.finish_worker('a',identity,'owned-ticket')


def test_live_child_survives_terminal_status_and_parent_death_until_real_child_exit():
    identity,_,_,_=admission();process=subprocess.Popen([sys.executable,'-c','import time; time.sleep(20)'])
    parent=subprocess.Popen([sys.executable,'-c','pass']);parent.wait()
    try:
        attach(identity,process.pid)
        jobs.cancel('a',identity)
        assert not jobs.finish_worker('a',identity,'owned-ticket') and occupancy()==(1,1)
        with jobs.connect() as c:c.execute('UPDATE qr_worker_slot SET pid=?',(parent.pid,))
        jobs.expire_interrupted();assert occupancy()==(1,1)
        assert process.poll() is None
    finally:process.terminate();process.wait()
    jobs.expire_interrupted();assert occupancy()==(0,0)
    assert jobs.get('a',identity)['status']=='cancelled'


@pytest.mark.parametrize('unknown',[None,'foreign-scope'])
def test_dead_parent_cannot_reclaim_child_with_unknown_or_foreign_scope(unknown):
    identity,_,_,_=admission();dead=subprocess.Popen([sys.executable,'-c','pass']);dead.wait()
    attach(identity,dead.pid)
    with jobs.connect() as c:c.execute('UPDATE qr_worker_slot SET pid=?,child_scope=?',(dead.pid,unknown))
    jobs.expire_interrupted();assert occupancy()==(1,1)


@pytest.mark.parametrize('damage',['dataset','request','kernel'])
def test_supervisor_rejects_unretained_inputs_before_spawning(monkeypatch,damage):
    identity,req,data,kernel=admission()
    if damage=='dataset':data['600519']['bars'][0]['close']+=1
    elif damage=='request':req['capital']+=1
    else:kernel='0'*64
    def forbidden(*args,**kwargs):raise AssertionError('Must reject before spawn')
    monkeypatch.setattr(cpu.subprocess,'Popen',forbidden)
    with pytest.raises(ValueError):cpu.run('a',identity,req,data,kernel)
    assert occupancy()==(1,1)


def test_child_rejects_different_parent_before_financial_computation(monkeypatch):
    identity,req,data,kernel=admission()
    value={'owner':'a','identity':identity,'request':req,'dataset':data,'dataset_digest':engine.digest(data),
           'kernel':kernel,'source_pins':cpu.pins(),'parent_pid':os.getppid()+123456,'slot_token':'owned-ticket'}
    def forbidden(*args,**kwargs):raise AssertionError('Financial work before parent/slot admission')
    monkeypatch.setattr(child.engine,'walk_forward',forbidden)
    with pytest.raises(InterruptedError):child.calculate(value)


def test_supervisor_observes_attempt_cancel_and_exits_owned_process_before_detach(monkeypatch):
    from deepfocus_api.quant_research import _PaperAttempt
    identity,req,data,kernel=admission();attempt=_PaperAttempt();processes=[];actual=cpu.subprocess.Popen
    def spawn(*args,**kwargs):
        process=actual(*args,**kwargs);processes.append(process);attempt.cancel();return process
    monkeypatch.setattr(cpu.subprocess,'Popen',spawn)
    with pytest.raises(InterruptedError):cpu.run('a',identity,req,data,kernel,attempt)
    assert len(processes)==1 and processes[0].poll() is not None
    with jobs.connect() as c:assert c.execute('SELECT child_pid FROM qr_worker_slot').fetchone()[0] is None
    assert jobs.finish_worker('a',identity,'owned-ticket') and occupancy()==(0,0)


def test_partial_input_transfer_resumes_after_slow_child_startup(monkeypatch):
    identity,req,data,kernel=admission();actual=cpu.subprocess.Popen;processes=[]
    def delayed(args,**kwargs):
        process=actual([sys.executable,'-B','-c',
            "import time,runpy; time.sleep(.3); runpy.run_module('deepfocus_api.quant_cpu_worker',run_name='__main__')"],**kwargs)
        processes.append(process);return process
    monkeypatch.setattr(cpu.subprocess,'Popen',delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(cpu.run,'a',identity,req,data,kernel)
        try:result=future.result(timeout=10)
        finally:
            for process in processes:
                if process.poll() is None:process.terminate();process.wait()
    assert result['dataset_digest']==engine.digest(data)
    assert len(processes)==1 and processes[0].poll()==0
    assert jobs.finish_worker('a',identity,'owned-ticket') and occupancy()==(0,0)


@pytest.mark.asyncio
async def test_repeated_task_cancel_drains_actual_computing_child_before_releasing_slot(monkeypatch):
    from datetime import date,timedelta
    from deepfocus_api import quant_research as service
    data=dataset();bars=[];day=date(2018,1,2)
    while len(bars)<1000:
        if day.weekday()<5:
            price=20+len(bars)*.01
            bars.append({'date':day.isoformat(),'open':price,'high':price+.2,'low':price-.2,'close':price+.01,'volume':1000000})
        day+=timedelta(days=1)
    symbols=['600519','600036','000333','000001','300750']
    data={s:{**data['600519'],'bars':copy.deepcopy(bars)} for s in symbols+['000300']}
    req={**request(),'symbols':symbols,'start_date':bars[0]['date'],'end_date':bars[-1]['date']}
    async def fetch(*args):return data
    monkeypatch.setattr(service,'fetch_dataset',fetch)
    monkeypatch.setattr(service,'_tasks',{})
    processes=[];actual=cpu.subprocess.Popen;slots.process_scope()
    def capture(args,**kwargs):
        process=actual(args,**kwargs)
        if 'deepfocus_api.quant_cpu_worker' in args:processes.append(process)
        return process
    monkeypatch.setattr(cpu.subprocess,'Popen',capture)
    created=service.submit('a','native-cancel-test',req);task=service._tasks[created['id']]
    try:
        for _ in range(1000):
            row=jobs.get('a',created['id'])
            if row['progress']>30 and processes and processes[0].poll() is None:break
            assert not task.done(),row.get('stage')
            await asyncio.sleep(.005)
        else:raise AssertionError('Actual child never reached financial progress')
        assert occupancy()==(1,1)
        task.cancel();await asyncio.sleep(0);task.cancel()
        with pytest.raises(asyncio.CancelledError):await asyncio.wait_for(task,10)
        assert len(processes)==1 and processes[0].poll() is not None
        assert jobs.get('a',created['id'])['status']=='failed' and occupancy()==(0,0)
    finally:
        if not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)


def test_cold_recovery_quarantines_copy_but_keeps_live_source_child_ownership(tmp_path):
    from deepfocus_api import quant_recovery as recovery
    identity,_,_,_=admission();process=subprocess.Popen([sys.executable,'-c','import time; time.sleep(20)'])
    try:
        attach(identity,process.pid)
        path=jobs.db.data_path('.quant_research.sqlite3','DEEPFOCUS_QUANT_RESEARCH_DB_PATH')
        result=recovery.prepare(path,tmp_path/'restored')
        assert result['verified'] and not result['activation_authorized'] and process.poll() is None
        with jobs.connect() as c:assert c.execute('SELECT child_pid FROM qr_worker_slot').fetchone()[0]==process.pid
        c=recovery.connect_ro(tmp_path/'restored/research.sqlite3')
        try:assert c.execute('SELECT count(*) FROM qr_worker_slot').fetchone()[0]==0
        finally:c.close()
    finally:process.terminate();process.wait()
    assert jobs.finish_worker('a',identity,'owned-ticket')


def test_read_only_inspector_reports_live_child_independently_of_dead_parent():
    identity,_,_,_=admission();process=subprocess.Popen([sys.executable,'-c','import time; time.sleep(20)'])
    dead=subprocess.Popen([sys.executable,'-c','pass']);dead.wait()
    try:
        attach(identity,process.pid)
        with jobs.connect() as c:c.execute('UPDATE qr_worker_slot SET pid=?',(dead.pid,))
        path=jobs.db.data_path('.quant_research.sqlite3','DEEPFOCUS_QUANT_RESEARCH_DB_PATH')
        before=path.read_bytes()
        script=Path(__file__).resolve().parents[3]/'scripts/inspect-quant-worker-slots.py'
        out=subprocess.run([sys.executable,str(script),'--db',str(path)],capture_output=True,text=True,check=True)
        report=json.loads(out.stdout);item=report['items'][0]
        assert item['process_observation']=='dead_pid_same_scope' and item['child_process_observation']=='live_pid_or_pid_reused'
        assert report['read_only'] and not report['automatic_recovery_performed']
        assert path.read_bytes()==before and occupancy()==(1,1)
    finally:process.terminate();process.wait()
    jobs.expire_interrupted();assert occupancy()==(0,0)
