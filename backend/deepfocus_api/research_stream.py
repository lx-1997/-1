"""研报深度解读 SSE 流式端点：阶段进度实时回报 + 最终完整结果。

POST /api/research/deep-draft/stream
请求体与 ``/api/research/deep-draft`` 完全一致；响应为 ``text/event-stream``：

    data: {"type": "stage", "detail": "正在获取研报原文…"}
    data: {"type": "ready", "detail": "原文已获取 · 24 页 · 文本层 8.3 万字符"}
    data: {"type": "tick", "elapsed": 12}
    data: {"type": "stage", "detail": "生成超时，当前为原文摘录版…"}   # 仅兜底时
    data: {"type": "done", "data": {…ResearchDeepDraftResponse…}}

错误以 ``{"type": "error", "status": 402, "detail": "…"}`` 回报后关流。
鉴权/配额/缓存/合规整形与 POST 端点同源（懒导入 main 复用，避免循环依赖）；
前端用 fetch 流式读取（EventSource 不携带 Authorization 头），解析失败自动
回退普通 POST。
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, AsyncIterator, Optional

import anyio
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from .auth import optional_current_user
from .analysis_limiter import AnalysisBusy
from .cost_quotas import quota_scope
from .research_digest import (
    ResearchDeepDraftRequest,
    ResearchDeepDraftResponse,
    generate_deep_draft,
    neutralize_deep_draft,
    resolve_source_documents,
)
from .research_quick import generate_deep_quick
from .streamed_prose import streamed_prose, REPORT_PROSE_DELTA

router = APIRouter()
_STREAM_TOTAL_TIMEOUT_SECONDS = max(
    60.0, float(os.getenv("DEEPFOCUS_RESEARCH_STREAM_TOTAL_SECONDS", "330") or 330),
)
_STREAM_HEARTBEAT_SECONDS = 3.0


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


def _draft_prefix_preview(prefix: str) -> dict[str, str]:
    """Read only complete top-level JSON strings; unfinished tokens never escape."""
    decoder = json.JSONDecoder()
    text = prefix.lstrip()
    if not text.startswith("{"):
        return {}
    values: dict[str, str] = {}
    position = 1
    for _ in range(24):
        try:
            while position < len(text) and text[position].isspace():
                position += 1
            key, position = decoder.raw_decode(text, position)
            if not isinstance(key, str):
                break
            while position < len(text) and text[position].isspace():
                position += 1
            if position >= len(text) or text[position] != ":":
                break
            position += 1
            while position < len(text) and text[position].isspace():
                position += 1
            value, position = decoder.raw_decode(text, position)
            if isinstance(value, str) and key in ("one_liner", "plain_language_summary", "executive_summary", "subject"):
                values[key] = value
            while position < len(text) and text[position].isspace():
                position += 1
            if position >= len(text) or text[position] != ",":
                break
            position += 1
        except (ValueError, IndexError, RecursionError):
            break
    summary = values.get("plain_language_summary") or values.get("executive_summary")
    if not values.get("one_liner") or not summary:
        return {}
    from .compliance import neutralize_text

    return {
        "one_liner": neutralize_text(values["one_liner"][:400]),
        "summary": neutralize_text(summary[:1200]),
        "subject": neutralize_text(values.get("subject", "")[:200]),
    }


def _valid_cached_draft(value: Any) -> bool:
    from .research_cache import is_valid_deep_draft_cache

    return is_valid_deep_draft_cache(value)


def _finish(value: Any) -> ResearchDeepDraftResponse:
    """合规整形：与 main 端点同源（中性化 + AI 标识 + 品牌化 provider）。"""
    from .compliance import AI_CONTENT_NOTICE, ai_label, neutralize_text  # noqa: PLC0415

    response = value if isinstance(value, ResearchDeepDraftResponse) else ResearchDeepDraftResponse.model_validate(value or {})
    try:
        response = ResearchDeepDraftResponse.model_validate(neutralize_deep_draft(response.model_dump()))
        disc = neutralize_text(response.disclaimer or "").strip()
        if not any(marker in disc for marker in ("AI 生成", "AI生成", "AI 辅助生成")):
            disc = ai_label(disc or AI_CONTENT_NOTICE)
        response.disclaimer = disc
    except Exception:
        if "AI 生成" not in (response.disclaimer or ""):
            response.disclaimer = (response.disclaimer or "") + "\n\n（本内容由 AI 生成，仅供参考，不构成投资建议）"
    from .main import _AI_BRAND  # noqa: PLC0415 - 懒导入避免循环

    response.provider = _AI_BRAND
    return response


@router.post("/api/research/deep-draft/stream")
@quota_scope
async def api_research_deep_draft_stream(
    request: ResearchDeepDraftRequest,
    http_req: Request,
    _user: Optional[dict] = Depends(optional_current_user),
) -> StreamingResponse:
    async def event_stream() -> AsyncIterator[str]:
        from . import metrics_store  # noqa: PLC0415
        from .main import (  # noqa: PLC0415 - 懒导入：main 在模块加载期引用本包路由
            _AI_ANALYZE_SEM,
            _check_ai_quota,
            _complete_cost_quota,
            deep_draft_cache_key,
            legacy_deep_draft_cache_key,
            metrics_get_ai_cache,
            metrics_incr,
            metrics_incr_ai_ref,
            metrics_set_ai_cache,
        )

        loop = asyncio.get_running_loop()
        started = loop.time()
        deadline = started + _STREAM_TOTAL_TIMEOUT_SECONDS
        owned_tasks: set[asyncio.Task] = set()

        async def within_deadline(awaitable, *, limit: Optional[float] = None):
            remaining = max(0.0, deadline - loop.time())
            return await asyncio.wait_for(awaitable, timeout=min(remaining, limit) if limit is not None else remaining)

        def own(awaitable) -> asyncio.Task:
            task = asyncio.create_task(awaitable)
            owned_tasks.add(task)
            return task

        def budget_task(work) -> asyncio.Task:
            # Create the execution only after its supervisor starts. Closing
            # at a yielded frame cannot leave an unawaited inner coroutine.
            async def budgeted():
                return await within_deadline(work())
            return own(budgeted())

        def tick() -> str:
            # Elapsed time only: no invented model progress while queued.
            return _sse({"type": "tick", "elapsed": int(loop.time() - started)})

        async def waiting(task: asyncio.Task) -> AsyncIterator[str]:
            while not task.done():
                done, _ = await asyncio.wait({task}, timeout=_STREAM_HEARTBEAT_SECONDS, return_when=asyncio.FIRST_COMPLETED)
                if not done:
                    yield tick()

        title = (request.title or "研报深度解读").strip()
        cache_key = deep_draft_cache_key(request)
        legacy_cache_key = legacy_deep_draft_cache_key(request) if cache_key else ""
        quick_cache_key = f"quick:{cache_key}" if cache_key else ""
        quota_key = None
        quota_completed = False

        def complete_once() -> None:
            nonlocal quota_completed
            # Only the response iterator owns quota completion. Worker tasks
            # inherit its context but never reserve, complete or release leases.
            if quota_key and not quota_completed:
                _complete_cost_quota(quota_key)
                quota_completed = True

        async def cached_draft():
            cached = await asyncio.to_thread(metrics_get_ai_cache, cache_key) if cache_key else None
            if _valid_cached_draft(cached):
                return cached
            if legacy_cache_key:
                cached = await asyncio.to_thread(metrics_get_ai_cache, legacy_cache_key)
                if _valid_cached_draft(cached):
                    await asyncio.to_thread(metrics_set_ai_cache, cache_key, cached)
                    return cached
            return None

        try:
            yield _sse({"type": "stage", "stage": "resolve", "detail": "正在获取研报原文…"})
            lookup_task = budget_task(cached_draft)
            async for heartbeat in waiting(lookup_task):
                yield heartbeat
            try:
                cached = lookup_task.result()
            except asyncio.TimeoutError:
                yield _sse({"type": "error", "status": 504, "detail": "研报解读缓存查询超过整体等待时限，请稍后重试"})
                return
            if cached is not None:
                try:
                    quota_key = _check_ai_quota(_user, "yb", http_req, cached=True)
                except Exception as exc:
                    yield _sse({"type": "error", "status": getattr(exc, "status_code", 503), "detail": str(getattr(exc, "detail", "") or str(exc))[:160]})
                    return
                complete_once()
                yield _sse({"type": "stage", "stage": "hit", "detail": "已有解读，直接展示"})
                yield _sse({"type": "done", "data": _finish(cached).model_dump(mode="json")})
                return

            try:
                quota_key = _check_ai_quota(_user, "yb", http_req, cached=False)
            except Exception as exc:
                yield _sse({"type": "error", "status": getattr(exc, "status_code", 403), "detail": str(getattr(exc, "detail", "") or str(exc))[:160]})
                return
            metrics_incr("ai_research")
            metrics_incr_ai_ref((request.file_id or request.workbench_filename or request.filename or "").strip(), title)

            # Parsing owns the same deadline and emits heartbeats while waiting.
            resolve_task = budget_task(lambda: resolve_source_documents(request))
            async for heartbeat in waiting(resolve_task):
                yield heartbeat
            try:
                docs = resolve_task.result()
            except asyncio.TimeoutError:
                yield _sse({"type": "error", "status": 504, "detail": "研报原文获取超过整体等待时限，请稍后重试"})
                return
            except Exception as exc:
                yield _sse({"type": "error", "status": 502, "detail": f"研报原文获取失败：{str(exc)[:120]}"})
                return
            pages = sum(max(0, int(d.total_pages or 0)) for d in docs)
            text_chars = sum(len(d.text or "") for d in docs)
            if docs:
                yield _sse({
                    "type": "ready", "pages": pages, "chars": text_chars,
                    "detail": f"原文已获取 · {pages} 页 · " + ("文本层 %d 字符" % text_chars if text_chars else "扫描版，走视觉解读"),
                })

            delta_queue: asyncio.Queue = asyncio.Queue(maxsize=64)
            preview_prefix = ""
            prefix_preview: dict[str, str] = {}
            body_preview: dict = {}

            def _progress(kind: str, value: Any) -> None:
                nonlocal preview_prefix, prefix_preview, body_preview
                if kind == "llm_reset":
                    preview_prefix = ""; prefix_preview = {}; body_preview = {}
                    return
                if kind == "llm_delta" and value:
                    # Keep the exact bounded prefix before the display queue can
                    # drop fragments. This preview never enters either cache.
                    if len(preview_prefix) < 128000:
                        preview_prefix = (preview_prefix + str(value))[:128000]
                        if not prefix_preview: prefix_preview = _draft_prefix_preview(preview_prefix)
                        body_preview = streamed_prose(preview_prefix)
                    try:
                        if delta_queue.full():
                            delta_queue.get_nowait()
                        delta_queue.put_nowait(str(value)[-512:])
                    except Exception:
                        pass

            async def quick_cached():
                value = await asyncio.to_thread(metrics_get_ai_cache, quick_cache_key) if quick_cache_key else None
                return value if isinstance(value, dict) and value.get("one_liner") else None

            async def _quick():
                # Optional work never joins the foreground queue. Admit it
                # before deep starts only if the limiter can still reserve an
                # interactive slot; overload skips generation, not the report.
                try:
                    async with _AI_ANALYZE_SEM.background():
                        value = await quick_cached()
                        if value is None:
                            value = await generate_deep_quick(request, documents=docs, allow_vision=False)
                            if isinstance(value, dict) and value.get("one_liner") and quick_cache_key:
                                try:
                                    await asyncio.to_thread(metrics_set_ai_cache, quick_cache_key, value)
                                except Exception:
                                    # A useful preview can still be delivered
                                    # when best-effort cache persistence fails.
                                    pass
                        return value
                except AnalysisBusy:
                    return await quick_cached()

            async def optional_quick():
                try:
                    return await within_deadline(_quick(), limit=60)
                except Exception:
                    # A preview/cache/provider failure cannot terminate deep.
                    return None

            async def _generate() -> dict[str, Any]:
                late_cached = await asyncio.to_thread(metrics_get_ai_cache, cache_key)
                if _valid_cached_draft(late_cached):
                    return late_cached
                async with _AI_ANALYZE_SEM:
                    token = REPORT_PROSE_DELTA.set(lambda piece: _progress("llm_reset", True) if piece is None else _progress("llm_delta", piece))
                    try:
                        result = await generate_deep_draft(request, documents=docs, progress=_progress)
                    finally:
                        REPORT_PROSE_DELTA.reset(token)
                payload = result.model_dump(mode="json") if hasattr(result, "model_dump") else dict(result or {})
                if _valid_cached_draft(payload):
                    await asyncio.to_thread(metrics_set_ai_cache, cache_key, payload)
                    if legacy_cache_key and legacy_cache_key != cache_key:
                        await asyncio.to_thread(metrics_set_ai_cache, legacy_cache_key, payload)
                return payload

            # Starting quick first allows optional admission on an idle server;
            # neither task waits for the other's model result.
            quick_task = own(optional_quick())
            gen_task = budget_task(_generate)
            yield _sse({"type": "stage", "stage": "generate", "detail": "正在排队或生成深度解读，速览就绪后先展示"})
            get_task = None
            quick_delivered = False
            body_delivered = False
            body_sent = {}
            quick_pending = True
            try:
                while True:
                    get_task = own(delta_queue.get())
                    wait_set = {gen_task, get_task} | ({quick_task} if quick_pending else set())
                    done, _ = await asyncio.wait(wait_set, timeout=_STREAM_HEARTBEAT_SECONDS, return_when=asyncio.FIRST_COMPLETED)
                    fragments = []
                    if get_task in done:
                        fragments.append(get_task.result())
                    else:
                        get_task.cancel()
                        await asyncio.gather(get_task, return_exceptions=True)
                    owned_tasks.discard(get_task)
                    get_task = None
                    while not delta_queue.empty():
                        fragments.append(delta_queue.get_nowait())
                    if body_preview and body_preview != body_sent:
                        from .compliance import neutralize_text
                        from .privacy_guard import scrub_internal_text
                        def clean(value):
                            if isinstance(value, str): return neutralize_text(scrub_internal_text(value))
                            if isinstance(value, list): return [clean(item) for item in value]
                            if isinstance(value, dict): return {key: clean(item) for key, item in value.items()}
                            return value
                        body_sent = body_preview
                        body_delivered = True
                        yield _sse({"type": "body", "data": clean(body_preview), "provisional": True})
                    if prefix_preview and not quick_delivered:
                        complete_once()
                        quick_delivered = True
                        yield _sse({"type": "quick", "data": {
                            **prefix_preview, "title": request.title,
                            "provider": "AI 速览", "quick": True,
                            "source_note": "AI 初步解读 · 正文生成中，完成后自动替换；风险与证据请以完整解读为准",
                            "bullish": [], "bearish": [], "key_points": [], "instruments": [],
                        }})
                    if quick_pending and quick_task in done:
                        quick_pending = False
                        quick = quick_task.result()
                        if isinstance(quick, dict) and quick.get("one_liner"):
                            complete_once()
                            quick_delivered = True
                            yield _sse({"type": "quick", "data": quick})
                    if gen_task in done:
                        payload = gen_task.result()
                        break
                    if not done:
                        yield tick()
            except asyncio.TimeoutError:
                preview_note = "；速览仍可查看" if quick_delivered else ""
                yield _sse({"type": "error", "status": 504, "detail": f"研报解读超过整体等待时限，已停止本轮生成{preview_note}，请稍后重试"})
                return
            except AnalysisBusy as exc:
                yield _sse({"type": "error", "status": 503, "detail": str(exc)[:160]})
                return
            except Exception as exc:
                status = getattr(exc, "status_code", 502)
                detail = str(getattr(exc, "detail", "") or str(exc))[:160]
                yield _sse({"type": "error", "status": status, "detail": f"深度稿生成失败：{detail}"})
                return
            # Deep won: stop optional work before exposing the final result.
            if not quick_task.done():
                quick_task.cancel()
                with anyio.CancelScope(shield=True):
                    await asyncio.gather(quick_task, return_exceptions=True)
            complete_once()
            if isinstance(payload, dict) and payload.get("provider") == "local-fallback":
                if quick_delivered or body_delivered:
                    yield _sse({"type": "error", "status": 502, "detail": "完整解读未生成，已保留初步速览；可查看原文或稍后手动重试"})
                    return
                yield _sse({"type": "stage", "stage": "fallback", "detail": "本轮模型未完成，先展示原文摘录版；稍后重试可获取完整解读"})
            yield _sse({"type": "done", "data": _finish(payload).model_dump(mode="json")})
        finally:
            # Includes parse/cache/preview/generation and queue waits, including
            # cancellation while the response is suspended at any yielded frame.
            for task in owned_tasks:
                if not task.done():
                    task.cancel()
            with anyio.CancelScope(shield=True):
                await asyncio.gather(*owned_tasks, return_exceptions=True)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
