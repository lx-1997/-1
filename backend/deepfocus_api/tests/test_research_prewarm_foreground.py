"""Focused executable safety tests using actual candidate/baseline function bodies.

The application imports production secrets/services from main. Extract just the
production AST function bodies to exercise real scheduling and cancellation with
fake PDF/provider calls, without importing main, networking or consuming quotas.
"""
from __future__ import annotations

import ast
import asyncio
import gc
import os
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional
import unittest

ROOT = Path(__import__("deepfocus_api", fromlist=["x"]).__file__).parent
BASE = ROOT
BATCH_SOURCE = Path(os.environ.get('RESEARCH_SAFETY_BATCH_SOURCE', ROOT / 'research_multi_agent.py'))


def selected(source, names, namespace=None):
    namespace = dict(namespace or {})
    tree = ast.parse(Path(source).read_text())
    body = [ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)]
    body.extend(node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in names)
    exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), str(source), 'exec'), namespace)
    return namespace


def batch_namespace():
    ns = dict(asyncio=asyncio, asynccontextmanager=asynccontextmanager, Optional=Optional, Any=Any,
              _batch_sem=None, _batch_sem_loop=None, _batch_foreground_waiters=0,
              _batch_background_owner=None, _batch_preempted=set(), _QUEUE_TIMEOUT_SECONDS=0.8,
              VisionUnavailableError=type('VisionUnavailableError', (RuntimeError,), {}))
    return selected(BATCH_SOURCE, {
        'ResearchMultiAgentBusy', '_batch_semaphore', '_batch_lease', '_analyze_images_per_page',
    }, ns)


def draft_namespace(analyzer, busy):
    return selected(ROOT / 'research_digest.py', {'generate_deep_draft'}, dict(
        asyncio=asyncio, Any=Any, Optional=Optional, ResearchMultiAgentBusy=busy,
        analyze_pdf_adaptive=analyzer, _DEEP_TEXT_FLOOR_CHARS=600,
        _DEEP_VISION_MAX_PAGES=14, _DEEP_LLM_TOTAL_TIMEOUT_SECONDS=2,
        _DISCLAIMER='AI', _FALLBACK_DISCLAIMER='AI fallback', _MISSING='原文未提供',
        _fallback_from_compact=lambda result, *_args, **_kwargs: result,
        _fallback_sections=lambda _docs: ([], ''), _source_label=lambda _doc: 'source',
        _base_response=lambda *_args, **kwargs: SimpleNamespace(
            provider=kwargs['provider'], source_coverage=SimpleNamespace(verified_claims=0)),
    ))


def inputs():
    request = SimpleNamespace(title='行业研究', symbol=None, max_pages=32)
    docs = [SimpleNamespace(text='', total_pages=20, pdf_bytes=b'scan', kind='pdf',
                            source_id='fid', pages_read=3)]
    return request, docs


class DraftSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.loop = asyncio.get_running_loop()
        self.old_handler = self.loop.get_exception_handler()
        self.loop_errors = []
        self.loop.set_exception_handler(lambda _loop, context: self.loop_errors.append(context))

    async def asyncTearDown(self):
        gc.collect()
        await asyncio.sleep(0)
        self.loop.set_exception_handler(self.old_handler)
        self.assertEqual([str(error.get('message')) for error in self.loop_errors], [],
                         'no unhandled task/future exceptions may remain after cleanup')

    async def test_existing_limiter_keeps_one_slot_for_foreground(self):
        ns = selected(BASE / 'analysis_limiter.py', {'AnalysisBusy', 'AnalysisLimiter'},
                      dict(asyncio=asyncio, asynccontextmanager=asynccontextmanager))
        gate = ns['AnalysisLimiter'](2)
        async with gate.background():
            with self.assertRaises(ns['AnalysisBusy']):
                async with gate.background():
                    self.fail('only one background generation may run')
            async with gate:
                self.assertTrue(gate.locked())
        self.assertFalse(gate.locked())

    async def test_capacity_one_safely_disables_background(self):
        ns = selected(BASE / 'analysis_limiter.py', {'AnalysisBusy', 'AnalysisLimiter'},
                      dict(asyncio=asyncio, asynccontextmanager=asynccontextmanager))
        gate = ns['AnalysisLimiter'](1)
        with self.assertRaises(ns['AnalysisBusy']):
            async with gate.background():
                self.fail('foreground reservation would be exhausted')
        async with gate:
            self.assertTrue(gate.locked())

    async def test_background_reaches_adaptive_and_caps_pages(self):
        seen = []
        async def analyzer(*args, **kwargs):
            seen.append(kwargs)
            return {'provider': 'fake-cloud', 'ready': True}
        ma = batch_namespace()
        ns = draft_namespace(analyzer, ma['ResearchMultiAgentBusy'])
        request, docs = inputs()
        result = await ns['generate_deep_draft'](request, documents=docs, background=True)
        self.assertTrue(result['ready'])
        self.assertTrue(seen[0]['background'])
        self.assertEqual(seen[0]['max_pages'], 14)
        await ns['generate_deep_draft'](request, documents=docs)
        self.assertFalse(seen[1]['background'])

    async def test_displaced_background_retries_instead_of_becoming_ready(self):
        ma = batch_namespace()
        async def analyzer(*_args, **_kwargs):
            raise ma['ResearchMultiAgentBusy']('yield')
        ns = draft_namespace(analyzer, ma['ResearchMultiAgentBusy'])
        request, docs = inputs()
        with self.assertRaises(ma['ResearchMultiAgentBusy']):
            await ns['generate_deep_draft'](request, documents=docs, background=True)

    async def test_degraded_background_vision_cannot_become_ready(self):
        ma = batch_namespace()
        for payload in ({'vision_unavailable': True}, {'provider': 'text-only-fallback'}):
            with self.subTest(payload=payload):
                async def analyzer(*_args, **_kwargs):
                    return payload
                ns = draft_namespace(analyzer, ma['ResearchMultiAgentBusy'])
                ns['_fallback_from_compact'] = lambda *_args, **_kwargs: self.fail(
                    'degraded background result must not be wrapped as ready')
                request, docs = inputs()
                result = await ns['generate_deep_draft'](request, documents=docs, background=True)
                self.assertEqual(result.provider, 'local-fallback')

    async def test_degraded_foreground_keeps_existing_compact_fallback(self):
        ma = batch_namespace()
        for payload in ({'vision_unavailable': True}, {'provider': 'text-only-fallback'}):
            with self.subTest(payload=payload):
                async def analyzer(*_args, **_kwargs):
                    return payload
                ns = draft_namespace(analyzer, ma['ResearchMultiAgentBusy'])
                ns['_fallback_from_compact'] = lambda *_args, **_kwargs: {
                    'provider': 'vision-compact-fallback'}
                request, docs = inputs()
                result = await ns['generate_deep_draft'](request, documents=docs)
                self.assertEqual(result['provider'], 'vision-compact-fallback')

    async def test_foreground_busy_keeps_existing_source_fallback(self):
        ma = batch_namespace()
        async def analyzer(*_args, **_kwargs):
            raise ma['ResearchMultiAgentBusy']('queue timeout')
        ns = draft_namespace(analyzer, ma['ResearchMultiAgentBusy'])
        request, docs = inputs()
        result = await ns['generate_deep_draft'](request, documents=docs)
        self.assertEqual(result.provider, 'local-fallback')

    async def test_provider_error_still_returns_honest_source_fallback(self):
        ma = batch_namespace()
        async def analyzer(*_args, **_kwargs):
            raise RuntimeError('provider offline')
        ns = draft_namespace(analyzer, ma['ResearchMultiAgentBusy'])
        request, docs = inputs()
        result = await ns['generate_deep_draft'](request, documents=docs, background=True)
        self.assertEqual(result.provider, 'local-fallback')

    async def test_shutdown_cancellation_is_not_busy_or_success(self):
        ma = batch_namespace()
        started = asyncio.Event()
        async def analyzer(*_args, **_kwargs):
            started.set()
            await asyncio.Event().wait()
        ns = draft_namespace(analyzer, ma['ResearchMultiAgentBusy'])
        request, docs = inputs()
        task = asyncio.create_task(ns['generate_deep_draft'](request, documents=docs, background=True))
        await asyncio.wait_for(started.wait(), 0.8)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_foreground_waits_for_all_displaced_page_tasks_to_drain(self):
        ma = batch_namespace()
        pages_started = asyncio.Event()
        cleanup_started = asyncio.Event()
        allow_cleanup = asyncio.Event()
        counts = {'started': 0, 'cleaned': 0}
        cache_writes = []
        async def page(*_args, **_kwargs):
            counts['started'] += 1
            if counts['started'] == 3:
                pages_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleanup_started.set()
                await allow_cleanup.wait()
                counts['cleaned'] += 1
        ma.update(_page_summary_key=lambda *_args: 'scan', _cache_get=lambda _key: None,
                  _cache_put=lambda *args: cache_writes.append(args),
                  CloudResearchLLM=lambda: SimpleNamespace(provider='cloud', has_vision_provider=lambda: True),
                  rv=SimpleNamespace(render_pdf_to_pngs=lambda *_args, **_kwargs: [b'1', b'2', b'3']),
                  _call_page=page, _PAGE_TOTAL_TIMEOUT_SECONDS=2)
        ns = draft_namespace(ma['_analyze_images_per_page'], ma['ResearchMultiAgentBusy'])
        request, docs = inputs()
        background = asyncio.create_task(ns['generate_deep_draft'](request, documents=docs, background=True))
        await asyncio.wait_for(pages_started.wait(), 0.8)
        entered = []
        async def foreground(index):
            async with ma['_batch_lease']():
                entered.append(index)
                self.assertEqual(counts['cleaned'], 3)
        first = asyncio.create_task(foreground(1))
        await asyncio.wait_for(cleanup_started.wait(), 0.8)
        second = asyncio.create_task(foreground(2))
        await asyncio.sleep(0)
        self.assertEqual(entered, [])
        self.assertTrue(ma['_batch_semaphore']().locked())
        allow_cleanup.set()
        await asyncio.wait_for(asyncio.gather(first, second), 0.8)
        with self.assertRaises(ma['ResearchMultiAgentBusy']):
            await background
        self.assertEqual(entered, [1, 2])
        self.assertEqual(cache_writes, [])
        self.assertFalse(ma['_batch_semaphore']().locked())
        self.assertIsNone(ma['_batch_background_owner'])
        self.assertEqual(ma['_batch_foreground_waiters'], 0)

    async def test_page_batch_success_preserves_cache_and_result(self):
        ma = batch_namespace()
        writes = []
        async def page(_llm, index, *_args, **_kwargs):
            return {'page': index, 'text': 'source'}
        async def synthesize(_llm, pages, **_kwargs):
            return {'provider': 'cloud', 'pages': [page['page'] for page in pages]}
        ma.update(_page_summary_key=lambda *_args: 'scan', _cache_get=lambda _key: None,
                  _cache_put=lambda *args: writes.append(args),
                  CloudResearchLLM=lambda: SimpleNamespace(provider='cloud', has_vision_provider=lambda: True),
                  rv=SimpleNamespace(render_pdf_to_pngs=lambda *_args, **_kwargs: [b'1', b'2', b'3']),
                  _call_page=page, _PAGE_TOTAL_TIMEOUT_SECONDS=1,
                  _page_result_is_usable=lambda page: isinstance(page, dict),
                  _synthesize_page_results=synthesize)
        result = await ma['_analyze_images_per_page'](
            b'scan', title='test', symbol=None, max_pages=14, background=True)
        self.assertEqual(result, {'provider': 'cloud', 'pages': [1, 2, 3]})
        self.assertEqual(writes, [('scan', result)])
        self.assertFalse(ma['_batch_semaphore']().locked())

    async def test_page_timeout_drains_children_and_original_aggregate(self):
        ma = batch_namespace()
        cleaned = []
        writes = []
        async def page(_llm, index, *_args, **_kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.append(index)
        ma.update(_page_summary_key=lambda *_args: 'scan', _cache_get=lambda _key: None,
                  _cache_put=lambda *args: writes.append(args),
                  CloudResearchLLM=lambda: SimpleNamespace(provider='cloud', has_vision_provider=lambda: True),
                  rv=SimpleNamespace(render_pdf_to_pngs=lambda *_args, **_kwargs: [b'1', b'2', b'3']),
                  _call_page=page, _PAGE_TOTAL_TIMEOUT_SECONDS=0.02)
        with self.assertRaises(asyncio.TimeoutError):
            await ma['_analyze_images_per_page'](
                b'scan', title='test', symbol=None, max_pages=14, background=True)
        self.assertEqual(sorted(cleaned), [1, 2, 3])
        self.assertEqual(writes, [])
        self.assertFalse(ma['_batch_semaphore']().locked())

    async def test_page_shutdown_stays_cancelled_after_drain(self):
        ma = batch_namespace()
        all_started = asyncio.Event()
        started, cleaned = [], []
        async def page(_llm, index, *_args, **_kwargs):
            started.append(index)
            if len(started) == 3:
                all_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.append(index)
        ma.update(_page_summary_key=lambda *_args: 'scan', _cache_get=lambda _key: None,
                  CloudResearchLLM=lambda: SimpleNamespace(provider='cloud', has_vision_provider=lambda: True),
                  rv=SimpleNamespace(render_pdf_to_pngs=lambda *_args, **_kwargs: [b'1', b'2', b'3']),
                  _call_page=page, _PAGE_TOTAL_TIMEOUT_SECONDS=1)
        task = asyncio.create_task(ma['_analyze_images_per_page'](
            b'scan', title='test', symbol=None, max_pages=14, background=True))
        await asyncio.wait_for(all_started.wait(), 0.8)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(sorted(cleaned), [1, 2, 3])
        self.assertFalse(ma['_batch_semaphore']().locked())
        self.assertIsNone(ma['_batch_background_owner'])

    async def test_busy_background_does_not_join_batch_queue(self):
        ma = batch_namespace()
        async with ma['_batch_lease']():
            with self.assertRaises(ma['ResearchMultiAgentBusy']):
                async with ma['_batch_lease'](background=True):
                    self.fail('background must yield immediately')
        self.assertFalse(ma['_batch_semaphore']().locked())


if __name__ == '__main__':
    unittest.main(verbosity=2)
