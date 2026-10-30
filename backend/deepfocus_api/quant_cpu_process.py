"""Owned price-computation subprocess; real exit precedes slot release.

JSON pipes/files only, no archived execution, broker or supplier operations.
The caller must be the already claimed research worker and drain this function.
"""
import hashlib
import json
import os
import select
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from . import quant_research_store as jobs, quant_worker_slots as slots
from .quant_research_engine import digest

FILES=('quant_research_engine.py','quant_research_candidates.py','quant_research.py',
       'quant_calendar.py','quant_paper_calendar.py','quant_calendar_service.py',
       'quant_calendar_store.py','quant_exchange_schedule.py','quant_research_store.py',
       'quant_worker_slots.py','backtest_data.py','ai_fund_ledger.py','ai_fund_policy.py',
       'quant_cpu_process.py','quant_cpu_worker.py')
MAX_INPUT=8*1024**2
MAX_OUTPUT=8*1024**2


def pins():
    return {name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in FILES}


def run(owner,identity,request,dataset,kernel,attempt=None):
    """Synchronous supervisor, called through the service's owned _paper_io."""
    frozen=pins()
    if digest(frozen)!=kernel:raise ValueError('CPU supervisor source differs from admitted kernel')
    with jobs.connect() as c:
        row=c.execute("SELECT * FROM qr_worker_slot WHERE experiment=? AND owner=? AND pid=? AND scope IS ? AND phase='active'",(identity,owner,os.getpid(),slots.process_scope())).fetchone()
        if row is None or not row['token']:raise ValueError('CPU work requires an owned active reservation')
        token=row['token']
        job=c.execute('SELECT payload FROM qr_experiment WHERE id=? AND owner=?',(identity,owner)).fetchone()
        payload=json.loads(job['payload']) if job is not None else {}
        if (payload.get('request')!=request or payload.get('kernel')!=kernel
                or digest(payload.get('dataset'))!=digest(dataset)):
            raise ValueError('CPU input differs from retained job snapshot')
    value={'owner':owner,'identity':identity,'request':request,'dataset':dataset,'dataset_digest':digest(dataset),
           'kernel':kernel,'source_pins':frozen,'parent_pid':os.getpid(),'slot_token':token}
    raw=json.dumps(value,ensure_ascii=False,allow_nan=False).encode()
    if len(raw)>MAX_INPUT:raise ValueError('CPU input exceeds fixed JSON budget')
    process=None;attached=False;started=time.monotonic()
    with tempfile.TemporaryFile() as output,tempfile.TemporaryFile() as errors:
        try:
            process=subprocess.Popen([sys.executable,'-B','-m','deepfocus_api.quant_cpu_worker'],
                stdin=subprocess.PIPE,stdout=output,stderr=errors,
                env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1])},start_new_session=True)
            with jobs.connect() as c:
                c.execute('BEGIN IMMEDIATE');slots.attach_child(c,owner,identity,token,process.pid)
            attached=True
            # No bytes/EOF reach the child until its real PID is durably attached.
            offset=0
            os.set_blocking(process.stdin.fileno(),False)
            while True:
                if attempt is not None:
                    with attempt.lock:
                        if attempt.cancelled:raise InterruptedError('CPU attempt cancelled')
                if time.monotonic()-started>180:raise TimeoutError('CPU computation budget exhausted')
                if pins()!=frozen:raise ValueError('CPU source changed during execution')
                if os.fstat(output.fileno()).st_size>MAX_OUTPUT or os.fstat(errors.fileno()).st_size>1024**2:
                    raise ValueError('CPU output budget exceeded')
                if process.poll() is not None:break
                if not process.stdin.closed:
                    ready=select.select([], [process.stdin], [], .1)[1]
                    if ready:
                        try:offset+=os.write(process.stdin.fileno(),raw[offset:offset+65536])
                        except BlockingIOError:pass
                        except BrokenPipeError:process.stdin.close()
                        if offset==len(raw):process.stdin.close()
                else:
                    try:process.wait(timeout=.1)
                    except subprocess.TimeoutExpired:pass
            output.seek(0);body=output.read(MAX_OUTPUT+1)
            if len(body)>MAX_OUTPUT:raise ValueError('CPU output budget exceeded')
            response=json.loads(body)
            if process.returncode or response.get('ok') is not True:
                if response.get('error_type')=='InterruptedError':raise InterruptedError('CPU computation cancelled')
                raise ValueError('CPU computation rejected retained input')
            if (response.get('kernel')!=kernel or response.get('dataset_digest')!=value['dataset_digest']
                    or response.get('result_digest')!=digest(response['result'])
                    or response['result'].get('dataset_digest')!=value['dataset_digest'] or pins()!=frozen):
                raise ValueError('CPU output identity mismatch')
            return response['result']
        finally:
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                    try:process.wait(timeout=2)
                    except subprocess.TimeoutExpired:process.kill();process.wait()
                else:process.wait()
                if process.stdin is not None:process.stdin.close()
                if attached:
                    with jobs.connect() as c:
                        c.execute('BEGIN IMMEDIATE');slots.clear_exited_child(c,owner,identity,token,process.pid)
