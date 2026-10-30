"""Read-only, process-isolated replay of a captured financial execution frame."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from . import ai_fund_audit as audit


def validate(record):
    m=record['manifest'];s=record['sections']
    if audit.digest(m)!=record['digest'] or set(s)!=set(m['refs']):raise audit.AuditError('Execution record manifest mismatch')
    if any(audit.digest(s[k])!=sha for k,sha in m['refs'].items()):raise audit.AuditError('Execution record content mismatch')
    if m['kernel']!=audit.KERNEL or audit.digest(s['source'])!=m['kernel']:raise audit.AuditError('Frozen execution source/runtime differs')
    if m['status'] not in {'committed','observation_only','no_quotes','unpriced_holdings'}:raise audit.AuditError('Failed attempts need separate fault-injection verification')
    if s['inputs'].get('policy_runtime')!={'learning':False,'rotation':False}:raise audit.AuditError('Experimental learning/rotation is not supported by this verifier')
    if not all(k in s for k in ('before','executed_at','entry_at','execution_history','marks_before','daily_before')):
        raise audit.AuditError('Historical record has no complete financial execution frame')
    order=s['inputs'].get('universe_order',[])
    if len(order)!=len(set(order)) or set(order)!=set(s['inputs']['universe']):raise audit.AuditError('Candidate order is missing or invalid')
    audit.history_rows(s)
    audit.verify_analysis(record)


def verify_execution(record):
    validate(record)
    with tempfile.TemporaryDirectory(prefix='paper-replay-') as folder:
        root=Path(folder);input_file=root/'record.json';output_file=root/'result.json'
        fd=os.open(input_file,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f:json.dump(record,f,ensure_ascii=False,allow_nan=False)
        env={k:os.environ[k] for k in ('PATH','HOME','LANG','LC_ALL','TZ','SYSTEMROOT') if k in os.environ}
        env.update(DEEPFOCUS_DATA_DIR=str(root/'data'),DEEPFOCUS_AIFUND_DB_PATH=str(root/'paper.sqlite3'))
        worker=Path(__file__).with_name('ai_fund_replay_worker.py')
        try:
            run=subprocess.run([sys.executable,str(worker),str(input_file),str(output_file)],cwd=root,env=env,
                               stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=45)
        except subprocess.TimeoutExpired as exc:raise audit.AuditError('Isolated execution replay timed out') from exc
        if not output_file.exists():raise audit.AuditError('Isolated execution replay failed without a result')
        result=json.loads(output_file.read_text())
        if run.returncode or not result.get('ok'):raise audit.AuditError(result.get('error','Isolated execution replay failed'))
        return result
