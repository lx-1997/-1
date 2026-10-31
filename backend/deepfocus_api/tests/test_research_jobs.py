import asyncio
import json
import os
from pathlib import Path
import sys
import time

import pytest

from deepfocus_api import research_jobs as jobs


def run(coro):
    return asyncio.run(coro)


def test_private_owner_idempotency_and_cancel(monkeypatch):
    async def scenario():
        async def slow(_payload):
            await asyncio.sleep(60)
        monkeypatch.setattr(jobs, "run_worker", slow)
        manager = jobs.ResearchJobs()
        first = await manager.create("alice", "funds", {"x": 1}, "request-001")
        same = await manager.create("alice", "funds", {"x": 1}, "request-001")
        assert first["id"] == same["id"]
        assert "owner" not in first and "digest" not in first
        with pytest.raises(jobs.ResearchJobError) as failure:
            manager.get(first["id"], "bob")
        assert failure.value.status == 404
        with pytest.raises(jobs.ResearchJobError) as failure:
            await manager.create("alice", "funds", {"x": 2}, "request-001")
        assert failure.value.status == 409
        cancelled = await manager.delete(first["id"], "alice")
        assert cancelled["state"] == "cancelled" and "result" not in cancelled
        await manager.shutdown()
    run(scenario())


def test_running_cancel_waits_for_cleanup(monkeypatch):
    async def scenario():
        started, cleaned = asyncio.Event(), asyncio.Event()
        async def worker(_payload):
            started.set()
            try:
                await asyncio.sleep(60)
            finally:
                cleaned.set()
        monkeypatch.setattr(jobs, "run_worker", worker)
        manager = jobs.ResearchJobs()
        first = await manager.create("alice", "funds", {}, "request-001")
        await started.wait()
        with pytest.raises(jobs.ResearchJobError) as failure:
            await manager.create("alice", "bonds", {}, "request-002")
        assert failure.value.status == 409
        await manager.delete(first["id"], "alice")
        assert cleaned.is_set()
        await manager.shutdown()
    run(scenario())


def test_one_compute_slot_and_shutdown_queue(monkeypatch):
    async def scenario():
        current, maximum = 0, 0
        started = asyncio.Event()
        async def worker(_payload):
            nonlocal current, maximum
            current += 1
            maximum = max(current, maximum)
            started.set()
            try:
                await asyncio.sleep(60)
            finally:
                current -= 1
        monkeypatch.setattr(jobs, "run_worker", worker)
        manager = jobs.ResearchJobs()
        for number in range(3):
            await manager.create("user" + str(number), "funds", {}, "request-001")
        await started.wait()
        await manager.shutdown()
        assert maximum == 1 and current == 0 and not manager.jobs
        with pytest.raises(jobs.ResearchJobError) as failure:
            await manager.create("alice", "funds", {}, "request-002")
        assert failure.value.status == 503
    run(scenario())


def test_result_expiry_and_input_guards(monkeypatch):
    async def scenario():
        async def worker(_payload):
            return {"title": "real computation"}
        monkeypatch.setattr(jobs, "run_worker", worker)
        manager = jobs.ResearchJobs()
        for data, status in [({"value": float("nan")}, 422), ({"data": "x" * (jobs.MAX_INPUT_BYTES + 1)}, 413)]:
            with pytest.raises(jobs.ResearchJobError) as failure:
                await manager.create("alice", "funds", data, "request-bad")
            assert failure.value.status == status
        first = await manager.create("alice", "funds", {}, "request-ok")
        await manager.jobs[first["id"]].task
        assert manager.get(first["id"], "alice").public()["result"]["title"] == "real computation"
        manager.jobs[first["id"]].touched -= jobs.RETENTION_SECONDS + 1
        with pytest.raises(jobs.ResearchJobError) as failure:
            manager.get(first["id"], "alice")
        assert failure.value.status == 404
    run(scenario())


def test_worker_environment_has_no_auth_or_account_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPFOCUS_JWT_SECRET", "test-private")
    monkeypatch.setenv("DEEPFOCUS_CORE_DB_PATH", "test-private.sqlite")
    monkeypatch.setenv("FMP_API_KEY", "test-private")
    env = jobs.worker_environment(str(tmp_path))
    assert not any(key in env for key in ["DEEPFOCUS_JWT_SECRET", "DEEPFOCUS_CORE_DB_PATH", "FMP_API_KEY"])
    assert env["OPENBLAS_NUM_THREADS"] == "1" and env["MPLBACKEND"] == "Agg"


def test_real_child_is_reaped_on_timeout(tmp_path, monkeypatch):
    script = tmp_path / "research_tools_worker.py"
    pid_file = tmp_path / "worker.pid"
    script.write_text("import json,sys,time,os\nfrom pathlib import Path\njson.load(sys.stdin)\nPath(" + repr(str(pid_file)) + ").write_text(str(os.getpid()))\ntime.sleep(60)\n")
    monkeypatch.setattr(jobs, "__file__", str(tmp_path / "research_jobs.py"))
    monkeypatch.setattr(jobs, "runtime_python", lambda: sys.executable)
    pids = []
    original_spawn = asyncio.create_subprocess_exec
    async def recorded_spawn(*args, **kwargs):
        process = await original_spawn(*args, **kwargs)
        pids.append(process.pid)
        return process
    monkeypatch.setattr(asyncio, "create_subprocess_exec", recorded_spawn)
    started = time.monotonic()
    with pytest.raises(jobs.ResearchJobError) as failure:
        run(jobs.run_worker({"tool": "funds", "input": {}}, timeout=.1))
    assert failure.value.status == 504 and time.monotonic() - started < 5
    assert len(pids) == 1
    with pytest.raises(ProcessLookupError):
        os.kill(pids[0], 0)


def test_real_running_child_reaped_on_cancel(tmp_path, monkeypatch):
    async def scenario():
        pid_file = tmp_path / "worker.pid"
        script = tmp_path / "research_tools_worker.py"
        script.write_text("import json,sys,time,os\nfrom pathlib import Path\njson.load(sys.stdin)\nPath(" + repr(str(pid_file)) + ").write_text(str(os.getpid()))\ntime.sleep(60)\n")
        monkeypatch.setattr(jobs, "__file__", str(tmp_path / "research_jobs.py"))
        monkeypatch.setattr(jobs, "runtime_python", lambda: sys.executable)
        task = asyncio.create_task(jobs.run_worker({"tool": "funds", "input": {}}))
        for _ in range(100):
            if pid_file.exists():
                break
            await asyncio.sleep(.02)
        assert pid_file.exists()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid_file.read_text()), 0)
    run(scenario())


def test_child_protocol_and_library_error(tmp_path, monkeypatch):
    script = tmp_path / "research_tools_worker.py"
    monkeypatch.setattr(jobs, "__file__", str(tmp_path / "research_jobs.py"))
    monkeypatch.setattr(jobs, "runtime_python", lambda: sys.executable)
    script.write_text('import json,sys\np=json.load(sys.stdin)\nprint(json.dumps({"ok":True,"result":{"echo":p["tool"]}}))\n')
    assert run(jobs.run_worker({"tool": "funds"})) == {"echo": "funds"}
    script.write_text('print("not-json")\n')
    with pytest.raises(jobs.ResearchJobError) as failure:
        run(jobs.run_worker({"tool": "funds"}))
    assert failure.value.status == 503


@pytest.mark.parametrize('stream,status,ignore_term', [('stdout',422,False),('stderr',503,False),('stdout',422,True)])
def test_live_output_limit_rejects_without_waiting_for_child_exit(tmp_path,monkeypatch,stream,status,ignore_term):
    limit=jobs.MAX_RESULT_BYTES if stream=='stdout' else jobs.MAX_STDERR_BYTES
    script=tmp_path/'research_tools_worker.py'
    prefix='import signal\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\n' if ignore_term else ''
    script.write_text(prefix+'import sys,time,os\nsys.stdin.buffer.read()\nsys.'+stream+'.buffer.write(b"x"*'+str(limit+1)+')\nsys.'+stream+'.buffer.flush()\ntime.sleep(60)\n')
    monkeypatch.setattr(jobs,'__file__',str(tmp_path/'research_jobs.py'))
    monkeypatch.setattr(jobs,'runtime_python',lambda:sys.executable)
    pids=[];spawn=asyncio.create_subprocess_exec
    async def record(*args,**kwargs):
        child=await spawn(*args,**kwargs);pids.append(child.pid);return child
    monkeypatch.setattr(asyncio,'create_subprocess_exec',record)
    started=time.monotonic()
    with pytest.raises(jobs.ResearchJobError) as exc:
        run(jobs.run_worker({'tool':'funds'},timeout=5))
    assert exc.value.status==status and time.monotonic()-started<4
    assert len(pids)==1
    with pytest.raises(ProcessLookupError):os.kill(pids[0],0)


def test_exact_stdout_limit_and_stderr_noise_are_drained_concurrently(tmp_path,monkeypatch):
    script=tmp_path/'research_tools_worker.py'
    envelope=json.dumps({'ok':True,'result':{'data':''}},separators=(',',':'))
    count=jobs.MAX_RESULT_BYTES-len(envelope.encode())
    script.write_text('import sys,json\nsys.stdin.buffer.read()\nsys.stderr.buffer.write(b"x"*'+str(jobs.MAX_STDERR_BYTES)+')\nsys.stderr.buffer.flush()\nsys.stdout.write(json.dumps({"ok":True,"result":{"data":"x"*'+str(count)+'}},separators=(",",":")))\n')
    monkeypatch.setattr(jobs,'__file__',str(tmp_path/'research_jobs.py'))
    monkeypatch.setattr(jobs,'runtime_python',lambda:sys.executable)
    result=run(jobs.run_worker({'tool':'funds'},timeout=5))
    assert len(result['data'])==count


@pytest.mark.parametrize('body,exit_code', [
    ('{"ok":true,"result":{}}',7),
    ('{"ok":"yes","result":{}}',0),
    ('{"ok":true,"result":null}',0),
    ('{"ok":true,"result":{"value":NaN}}',0),
    ('{"ok":true,"result":{"value":1e999}}',0),
    ('{"ok":false,"status":"invalid","error":"bad"}',0),
])
def test_child_exit_and_envelope_are_validated(tmp_path,monkeypatch,body,exit_code):
    script=tmp_path/'research_tools_worker.py'
    script.write_text('import sys\nsys.stdin.buffer.read()\nsys.stdout.write('+repr(body)+')\nsys.exit('+str(exit_code)+')\n')
    monkeypatch.setattr(jobs,'__file__',str(tmp_path/'research_jobs.py'))
    monkeypatch.setattr(jobs,'runtime_python',lambda:sys.executable)
    with pytest.raises(jobs.ResearchJobError) as exc:run(jobs.run_worker({'tool':'funds'}))
    assert exc.value.status==503


def test_worker_rejects_oversized_input_before_spawn(monkeypatch):
    async def forbidden(*args,**kwargs):raise AssertionError('must not spawn')
    monkeypatch.setattr(asyncio,'create_subprocess_exec',forbidden)
    with pytest.raises(jobs.ResearchJobError):run(jobs.run_worker({'data':'x'*jobs.MAX_INPUT_BYTES}))


def test_repeated_cancel_during_spawn_retains_child_until_cleanup(tmp_path,monkeypatch):
    async def scenario():
        script=tmp_path/'research_tools_worker.py';script.write_text('import sys,time\nsys.stdin.buffer.read()\ntime.sleep(60)\n')
        monkeypatch.setattr(jobs,'__file__',str(tmp_path/'research_jobs.py'))
        monkeypatch.setattr(jobs,'runtime_python',lambda:sys.executable)
        entered=asyncio.Event();release=asyncio.Event();pids=[];spawn=asyncio.create_subprocess_exec
        async def gated(*args,**kwargs):
            child=await spawn(*args,**kwargs);pids.append(child.pid);entered.set();await release.wait();return child
        monkeypatch.setattr(asyncio,'create_subprocess_exec',gated)
        prior=set(asyncio.all_tasks());task=asyncio.create_task(jobs.run_worker({'tool':'funds'}))
        try:
            await asyncio.wait_for(entered.wait(),1)
            task.cancel();await asyncio.sleep(0);task.cancel();await asyncio.sleep(0)
            assert not task.done()
        finally:release.set()
        with pytest.raises(asyncio.CancelledError):await asyncio.wait_for(task,4)
        assert len(pids)==1
        with pytest.raises(ProcessLookupError):os.kill(pids[0],0)
        assert not [t for t in asyncio.all_tasks()-prior if not t.done()]
    run(scenario())
