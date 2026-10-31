"""Same-OS-instance worker reservations, independent of user-visible terminal status.

No timer may reclaim a live/unknown process. Unknown or foreign boot/PID scopes
stay reserved; controlled migration/recovery is separate from this local fence.
"""
import hashlib
import ctypes
import os
from pathlib import Path
import sys
import sqlite3
from functools import lru_cache


def _darwin_boot_uuid():
    """Read the fixed boot identity without creating an orphanable helper."""
    call=ctypes.CDLL(None,use_errno=True).sysctlbyname
    call.argtypes=[ctypes.c_char_p,ctypes.c_void_p,ctypes.POINTER(ctypes.c_size_t),ctypes.c_void_p,ctypes.c_size_t]
    call.restype=ctypes.c_int
    size=ctypes.c_size_t()
    key=b'kern.bootsessionuuid'
    if call(key,None,ctypes.byref(size),None,0)!=0:raise OSError(ctypes.get_errno(),'Boot identity unavailable')
    if not 2<=size.value<=128:raise ValueError('Invalid boot identity size')
    buffer=ctypes.create_string_buffer(size.value)
    if call(key,buffer,ctypes.byref(size),None,0)!=0:raise OSError(ctypes.get_errno(),'Boot identity unavailable')
    if not 2<=size.value<=len(buffer) or buffer.raw[size.value-1]!=0:raise ValueError('Invalid boot identity response')
    boot=buffer.raw[:size.value-1].decode('ascii')
    import uuid
    if str(uuid.UUID(boot))!=boot.lower():raise ValueError('Invalid boot UUID')
    return boot


@lru_cache(maxsize=1)
def process_scope():
    try:
        if sys.platform.startswith('linux'):
            boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            namespace=os.readlink('/proc/self/ns/pid')
        elif sys.platform=='darwin':
            boot=_darwin_boot_uuid()
            namespace='host-pid-namespace'
        else:return None
        return hashlib.sha256((sys.platform+'|'+boot+'|'+namespace).encode()).hexdigest() if boot else None
    except (OSError,ValueError,AttributeError):return None


def schema(c):
    c.execute('CREATE TABLE IF NOT EXISTS qr_worker_slot(experiment TEXT PRIMARY KEY,owner TEXT NOT NULL,pid INTEGER NOT NULL,scope TEXT,phase TEXT NOT NULL,token TEXT,child_pid INTEGER,child_scope TEXT)')
    for name,kind in (('child_pid','INTEGER'),('child_scope','TEXT')):
        if name not in {row[1] for row in c.execute('PRAGMA table_info(qr_worker_slot)')}:
            try:c.execute('ALTER TABLE qr_worker_slot ADD COLUMN '+name+' '+kind)
            except sqlite3.OperationalError:
                # A simultaneous connection may have completed this additive DDL.
                if name not in {row[1] for row in c.execute('PRAGMA table_info(qr_worker_slot)')}:raise
    c.execute('CREATE INDEX IF NOT EXISTS qr_worker_owner ON qr_worker_slot(owner)')


def reserve(c,owner,identity):
    c.execute('INSERT INTO qr_worker_slot(experiment,owner,pid,scope,phase,token) VALUES(?,?,?,?,?,NULL)',(identity,owner,os.getpid(),process_scope(),'reserved'))


def retire_unclaimed(c):
    c.execute("DELETE FROM qr_worker_slot WHERE phase='reserved' AND NOT EXISTS(SELECT 1 FROM qr_experiment e WHERE e.id=qr_worker_slot.experiment AND e.owner=qr_worker_slot.owner AND e.status='running')")


def definitely_dead(pid,scope):
    if type(pid) is not int or pid<=1 or scope is None or scope!=process_scope():return False
    try:os.kill(pid,0)
    except ProcessLookupError:return True
    except (PermissionError,OSError):pass
    return False


def reap_dead(c,stamp):
    scope=process_scope()
    if scope is None:return
    for row in c.execute('SELECT * FROM qr_worker_slot WHERE scope=?',(scope,)).fetchall():
        if definitely_dead(row['pid'],row['scope']):
            if row['child_pid'] is not None and not definitely_dead(row['child_pid'],row['child_scope']):continue
            if row['child_pid'] is None and row['child_scope'] is not None:continue
            c.execute("UPDATE qr_experiment SET status='failed',stage='执行工作进程已退出，失败任务保留',updated=? WHERE owner=? AND id=? AND status='running'",(stamp,row['owner'],row['experiment']))
            c.execute('DELETE FROM qr_worker_slot WHERE experiment=? AND owner=? AND pid=? AND scope=?',(row['experiment'],row['owner'],row['pid'],scope))


def counts(c,owner):
    # Include legacy active rows without slots, and orphaned slots without rows.
    own=c.execute('SELECT count(*) FROM qr_worker_slot WHERE owner=?',(owner,)).fetchone()[0]
    total=c.execute('SELECT count(*) FROM qr_worker_slot').fetchone()[0]
    query="SELECT count(*) FROM qr_experiment e WHERE e.status IN ('queued','running') AND NOT EXISTS(SELECT 1 FROM qr_worker_slot s WHERE s.experiment=e.id)"
    return own+c.execute(query+' AND e.owner=?',(owner,)).fetchone()[0],total+c.execute(query).fetchone()[0]


def claim(c,owner,identity,token):
    result=c.execute("UPDATE qr_worker_slot SET phase='active',token=? WHERE experiment=? AND owner=? AND phase='reserved' AND pid=? AND scope IS ? AND EXISTS(SELECT 1 FROM qr_experiment WHERE id=? AND owner=? AND status='running')",
                     (token,identity,owner,os.getpid(),process_scope(),identity,owner))
    return result.rowcount==1


def finish(c,owner,identity,token,stamp):
    row=c.execute("SELECT * FROM qr_worker_slot WHERE experiment=? AND owner=? AND phase='active' AND token=? AND pid=? AND scope IS ?",(identity,owner,token,os.getpid(),process_scope())).fetchone()
    if row is None:return False
    if row['child_pid'] is not None and not definitely_dead(row['child_pid'],row['child_scope']):return False
    if row['child_pid'] is None and row['child_scope'] is not None:return False
    c.execute("UPDATE qr_experiment SET status='failed',stage='计算已退出但未完成，失败任务保留',updated=? WHERE owner=? AND id=? AND status='running'",(stamp,owner,identity))
    c.execute('DELETE FROM qr_worker_slot WHERE experiment=? AND owner=? AND token=?',(identity,owner,token))
    return True


def attach_child(c,owner,identity,token,pid):
    if type(pid) is not int or pid<=1 or pid==os.getpid() or process_scope() is None:
        raise ValueError('Explicit owned child PID and known OS scope required')
    result=c.execute("UPDATE qr_worker_slot SET child_pid=?,child_scope=? WHERE experiment=? AND owner=? AND token=? AND pid=? AND scope IS ? AND phase='active' AND child_pid IS NULL AND child_scope IS NULL AND EXISTS(SELECT 1 FROM qr_experiment WHERE id=? AND owner=? AND status='running')",
        (pid,process_scope(),identity,owner,token,os.getpid(),process_scope(),identity,owner))
    if result.rowcount!=1:raise ValueError('CPU child admission is not owned or was cancelled')


def clear_exited_child(c,owner,identity,token,pid):
    """Only the owning supervisor calls this after its Popen.wait() returned.

    PID reuse after wait is possible; the owned Popen handle, not kill(pid,0),
    supplies normal-exit proof. Dead-parent reclamation uses conservative probes.
    """
    result=c.execute("UPDATE qr_worker_slot SET child_pid=NULL,child_scope=NULL WHERE experiment=? AND owner=? AND token=? AND pid=? AND scope IS ? AND child_pid=? AND child_scope IS ?",
        (identity,owner,token,os.getpid(),process_scope(),pid,process_scope()))
    if result.rowcount!=1:raise ValueError('CPU child exit belongs to another reservation')
