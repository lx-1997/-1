"""Private subprocess entrypoint; stdin remains closed until PID admission."""
import json
import os
import sys
import time


def reject_external_io(event,args):
    # The fixed numerical worker needs neither a network nor descendants.
    # Denying creation also makes the final waited-child RSS bound complete.
    if event in {'socket.connect','subprocess.Popen','os.fork','os.forkpty','os.posix_spawn','os.exec','os.system'}:
        raise PermissionError('Numerical worker cannot create processes or connect sockets')


if __name__=='__main__':sys.addaudithook(reject_external_io)

from . import quant_cpu_process as supervisor, quant_research_store as jobs
from . import quant_worker_slots as slots, quant_research_engine as engine


def calculate(value):
    frozen=supervisor.pins()
    if frozen!=value['source_pins'] or engine.digest(frozen)!=value['kernel']:
        raise ValueError('Child source does not match parent')
    owner,identity=value['owner'],value['identity'];started=time.monotonic()
    def bound():
        if os.getppid()!=value['parent_pid'] or time.monotonic()-started>180:
            raise InterruptedError('Parent exited or computation expired')
        with jobs.connect() as c:
            row=c.execute(
                "SELECT s.*,e.status,e.payload,e.input_hash FROM qr_worker_slot s JOIN qr_experiment e ON e.id=s.experiment AND e.owner=s.owner WHERE s.experiment=? AND s.owner=?",(identity,owner)).fetchone()
        if (row is None or row['status']!='running' or row['phase']!='active'
                or row['pid']!=value['parent_pid'] or row['token']!=value['slot_token']
                or row['child_pid']!=os.getpid() or row['child_scope']!=slots.process_scope()
                or row['scope']!=slots.process_scope()):raise InterruptedError('CPU reservation no longer active')
        payload=json.loads(row['payload'])
        if (payload['request']!=value['request'] or payload['kernel']!=value['kernel']
                or row['input_hash']!=engine.digest(value['request'])
                or engine.digest(payload.get('dataset'))!=value['dataset_digest']):
            raise ValueError('CPU request differs from stored admission')
    bound()
    if engine.digest(value['dataset'])!=value['dataset_digest']:raise ValueError('CPU dataset changed')
    def progress(percent,stage):
        bound()
        if supervisor.pins()!=frozen:raise ValueError('CPU source changed')
        if not jobs.update(owner,identity,progress=percent,stage=stage):raise InterruptedError('CPU job cancelled')
    request=value['request']
    result=engine.walk_forward(value['dataset'],request['symbols'],request['benchmark'],request['capital'],progress)
    bound()
    if supervisor.pins()!=frozen:raise ValueError('CPU source changed at completion')
    return {'ok':True,'result':result,'result_digest':engine.digest(result),
            'dataset_digest':value['dataset_digest'],'kernel':value['kernel']}


def main():
    try:
        body=sys.stdin.buffer.read(supervisor.MAX_INPUT+1)
        if len(body)>supervisor.MAX_INPUT:raise ValueError('CPU input exceeds budget')
        result=calculate(json.loads(body));out=json.dumps(result,allow_nan=False).encode()
        if len(out)>supervisor.MAX_OUTPUT:raise ValueError('CPU result exceeds budget')
        sys.stdout.buffer.write(out);sys.stdout.buffer.flush();return 0
    except BaseException as exc:
        sys.stdout.write(json.dumps({'ok':False,'error_type':type(exc).__name__}));sys.stdout.flush();return 2


if __name__=='__main__':raise SystemExit(main())
