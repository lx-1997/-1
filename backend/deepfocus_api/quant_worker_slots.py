"""Same-OS-instance worker reservations, independent of user-visible terminal status.

No timer may reclaim a live/unknown process. Unknown or foreign boot/PID scopes
stay reserved; controlled migration/recovery is separate from this local fence.
"""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
from functools import lru_cache


@lru_cache(maxsize=1)
def process_scope():
    try:
        if sys.platform.startswith('linux'):
            boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            namespace=os.readlink('/proc/self/ns/pid')
        elif sys.platform=='darwin':
            boot=subprocess.check_output(['/usr/sbin/sysctl','-n','kern.bootsessionuuid'],timeout=1,text=True).strip()
            namespace='host-pid-namespace'
        else:return None
        return hashlib.sha256((sys.platform+'|'+boot+'|'+namespace).encode()).hexdigest() if boot else None
    except (OSError,subprocess.SubprocessError):return None


def schema(c):
    c.execute('CREATE TABLE IF NOT EXISTS qr_worker_slot(experiment TEXT PRIMARY KEY,owner TEXT NOT NULL,pid INTEGER NOT NULL,scope TEXT,phase TEXT NOT NULL,token TEXT)')
    c.execute('CREATE INDEX IF NOT EXISTS qr_worker_owner ON qr_worker_slot(owner)')


def reserve(c,owner,identity):
    c.execute('INSERT INTO qr_worker_slot VALUES(?,?,?,?,?,NULL)',(identity,owner,os.getpid(),process_scope(),'reserved'))


def retire_unclaimed(c):
    c.execute("DELETE FROM qr_worker_slot WHERE phase='reserved' AND NOT EXISTS(SELECT 1 FROM qr_experiment e WHERE e.id=qr_worker_slot.experiment AND e.owner=qr_worker_slot.owner AND e.status='running')")


def reap_dead(c,stamp):
    scope=process_scope()
    if scope is None:return
    for row in c.execute('SELECT * FROM qr_worker_slot WHERE scope=?',(scope,)).fetchall():
        if row['pid']<=0:continue
        try:os.kill(row['pid'],0)
        except ProcessLookupError:
            c.execute("UPDATE qr_experiment SET status='failed',stage='执行工作进程已退出，失败任务保留',updated=? WHERE owner=? AND id=? AND status='running'",(stamp,row['owner'],row['experiment']))
            c.execute('DELETE FROM qr_worker_slot WHERE experiment=? AND owner=? AND pid=? AND scope=?',(row['experiment'],row['owner'],row['pid'],scope))
        except (PermissionError,OSError):continue


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
    row=c.execute("SELECT 1 FROM qr_worker_slot WHERE experiment=? AND owner=? AND phase='active' AND token=? AND pid=? AND scope IS ?",(identity,owner,token,os.getpid(),process_scope())).fetchone()
    if row is None:return False
    c.execute("UPDATE qr_experiment SET status='failed',stage='计算已退出但未完成，失败任务保留',updated=? WHERE owner=? AND id=? AND status='running'",(stamp,owner,identity))
    c.execute('DELETE FROM qr_worker_slot WHERE experiment=? AND owner=? AND token=?',(identity,owner,token))
    return True
