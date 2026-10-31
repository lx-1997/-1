"""Bounded best-effort metadata indexing outside the HTTP event loop."""
from __future__ import annotations
import asyncio
import contextvars
import hashlib
import logging
import threading
import time
from . import content_ontology as ontology

_SLOT = threading.BoundedSemaphore(1)
_MAX_REPORTS = 500
_WORK_SECONDS = 5.0
logger = logging.getLogger(__name__)


async def index_wire_metadata(items, *, observed_at, seen):
    pending = {'indexed_ids':0, 'pending_reports_upper_bound':len(items), 'complete':False}
    if not items or all(item.get('file_id') and item['file_id'] in seen for item in items):
        return {'indexed_ids':0, 'pending_reports_upper_bound':0, 'complete':True}
    if len(items)>_MAX_REPORTS:
        return {**pending, 'reason':'batch_budget'}
    try:
        path = ontology._db_path()
    except Exception as exc:
        return {**pending, 'reason':type(exc).__name__}
    slot = _SLOT
    if not slot.acquire(blocking=False):
        return {**pending, 'reason':'busy'}

    def work():
        try:
            deadline = time.monotonic()+_WORK_SECONDS
            annotations = {}; size = 0
            for item in items:
                if time.monotonic()>=deadline: raise TimeoutError('Metadata preparation budget')
                for field in ('file_id','filename','title','org','date','created_at'):
                    value = item.get(field) or ''
                    if not isinstance(value,str) or len(value)>16384: raise ValueError('Metadata field budget')
                    size += len(value.encode('utf-8'))
                if size>1024**2: raise ValueError('Metadata input budget')
                stable = item.get('file_id') or hashlib.sha1(item['filename'].encode()).hexdigest()[:20]
                if len(stable)>190: raise ValueError('Metadata identity budget')
                if stable in seen or stable in annotations: continue
                annotations[stable] = ontology.annotate_content(
                    content_id='wire:'+stable, content_type='research', title=item['title'],
                    text=f"{item.get('org') or '海外投行'} {item.get('date') or ''}",
                    source_name='海外投行研报', published_at=item.get('created_at') or item.get('date') or '',
                    collected_at=observed_at, persist=False,
                )
            ontology.persist_annotations(list(annotations.values()), database_path=path,
                timeout=2.0, deadline=deadline, insert_only=True)
            # Publish admission only after the entire data transaction commits.
            seen.update(annotations)
            while len(seen)>5000: seen.pop()
            return {'indexed_ids':len(annotations), 'pending_reports_upper_bound':0, 'complete':True}
        finally:
            # The permit belongs to the real thread, never its cancelled waiter.
            slot.release()

    # Keep a Future, not an intermediate Task that shutdown could cancel while
    # its real thread is still running. Capture the same context as to_thread.
    try:
        worker = asyncio.get_running_loop().run_in_executor(None, contextvars.copy_context().run, work)
    except Exception as exc:
        slot.release()
        return {**pending, 'reason':type(exc).__name__}
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        while not worker.done():
            try: await asyncio.shield(worker)
            except asyncio.CancelledError: continue
            except Exception: break
        if worker.done() and not worker.cancelled():
            try: worker.result()
            except Exception: pass
        raise
    except Exception as exc:
        logger.warning('Wire metadata indexing pending: %s',type(exc).__name__)
        return {**pending, 'reason':type(exc).__name__}
