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
from typing import Any, AsyncIterator, Optional

import anyio
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from .auth import optional_current_user
from .cost_quotas import quota_scope
from .research_digest import (
    ResearchDeepDraftRequest,
    ResearchDeepDraftResponse,
    generate_deep_draft,
    neutralize_deep_draft,
    resolve_source_documents,
)
from .research_quick import generate_deep_quick

router = APIRouter()


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


def _valid_cached_draft(value: Any) -> bool:
    """与 main.deep-draft 端点同源的结构校验 + 兜底拒缓存。"""
    if not isinstance(value, dict) or not value:
        return False
    if value.get("mode") != "deep_draft":
        return False
    if not isinstance(value.get("sections"), list):
        return False
    if not isinstance(value.get("source_coverage"), dict):
        return False
    # 模型失败的确定性兜底不当长期缓存（瞬时故障下次重试生成）
    if value.get("provider") == "local-fallback":
        return False
    try:
        ResearchDeepDraftResponse.model_validate(value)
    except Exception:
        return False
    return True


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

        title = (request.title or "研报深度解读").strip()
        cache_key = deep_draft_cache_key(request)
        legacy_cache_key = legacy_deep_draft_cache_key(request) if cache_key else ""

        yield _sse({"type": "stage", "stage": "resolve", "detail": "正在获取研报原文…"})

        cached = metrics_get_ai_cache(cache_key) if cache_key else None
        if not _valid_cached_draft(cached):
            cached = None
        if cached is None and legacy_cache_key:
            legacy_cached = metrics_get_ai_cache(legacy_cache_key)
            if _valid_cached_draft(legacy_cached):
                cached = legacy_cached
                metrics_set_ai_cache(cache_key, legacy_cached)
        if cached is not None:
            try:
                quota_key = _check_ai_quota(_user, "yb", http_req, cached=True)
            except Exception as exc:
                yield _sse({"type": "error", "status": getattr(exc, "status_code", 503), "detail": str(getattr(exc, "detail", "") or str(exc))[:160]})
                return
            if quota_key:
                _complete_cost_quota(quota_key)
            yield _sse({"type": "stage", "stage": "hit", "detail": "已有解读，直接展示"})
            yield _sse({"type": "done", "data": _finish(cached).model_dump(mode="json")})
            return

        try:
            quota_key = _check_ai_quota(_user, "yb", http_req, cached=False)
        except Exception as exc:
            status = getattr(exc, "status_code", 403)
            detail = str(getattr(exc, "detail", "") or str(exc))[:160]
            yield _sse({"type": "error", "status": status, "detail": detail})
            return
        metrics_incr("ai_research")
        metrics_incr_ai_ref((request.file_id or request.workbench_filename or request.filename or "").strip(), title)

        # 原文解析放在生成之外：能把「拿到几页/多少文本」作为真实进度回报
        try:
            docs = await resolve_source_documents(request)
        except Exception as exc:
            yield _sse({"type": "error", "status": 502, "detail": f"研报原文获取失败：{str(exc)[:120]}"})
            return
        pages = sum(max(0, int(d.total_pages or 0)) for d in docs)
        text_chars = sum(len(d.text or "") for d in docs)
        if docs:
            yield _sse({
                "type": "ready",
                "pages": pages,
                "chars": text_chars,
                "detail": f"原文已获取 · {pages} 页 · " + ("文本层 %d 字符" % text_chars if text_chars else "扫描版，走视觉解读"),
            })

        # 快轨：先推一版十秒级速览（独立缓存 quick:<key>），深度稿随后继续生成。
        # 速览失败静默跳过——它只是增强层，绝不阻塞深稿主链路。
        quick_cache_key = f"quick:{cache_key}" if cache_key else ""
        quick = metrics_get_ai_cache(quick_cache_key) if quick_cache_key else None
        if not isinstance(quick, dict) or not quick.get("one_liner"):
            yield _sse({"type": "stage", "stage": "quick", "detail": "速览生成中（约 10 秒，先出方向感）…"})
            try:
                quick = await asyncio.wait_for(
                    generate_deep_quick(request, documents=docs),
                    timeout=60,
                )
            except Exception as exc:
                print(f"[deep-quick] 快轨异常跳过：{type(exc).__name__}: {str(exc)[:120]}")
                quick = None
            if isinstance(quick, dict) and quick.get("one_liner") and quick_cache_key:
                metrics_set_ai_cache(quick_cache_key, quick)
        if isinstance(quick, dict) and quick.get("one_liner"):
            # A useful preview already fulfils the request even if the client
            # disconnects before the deep draft; completing the same lease
            # again below is idempotent and never charges a second time.
            if quota_key:
                _complete_cost_quota(quota_key)
            yield _sse({"type": "quick", "data": quick})

        yield _sse({"type": "stage", "stage": "generate", "detail": "模型解读中（长报告约 1-3 分钟，完成即缓存秒开）"})

        started = asyncio.get_event_loop().time()
        delta_queue: asyncio.Queue = asyncio.Queue()
        _delta_buf: list[str] = []

        def _progress(kind: str, value: Any) -> None:
            if kind == "llm_delta" and value:
                try:
                    delta_queue.put_nowait(str(value))
                except Exception:
                    pass

        async def _generate() -> dict[str, Any]:
            late_cached = metrics_get_ai_cache(cache_key)
            if _valid_cached_draft(late_cached):
                return late_cached
            async with _AI_ANALYZE_SEM:
                result = await generate_deep_draft(request, documents=docs, progress=_progress)
            payload = result.model_dump(mode="json") if hasattr(result, "model_dump") else dict(result or {})
            metrics_set_ai_cache(cache_key, payload)
            if legacy_cache_key and legacy_cache_key != cache_key:
                metrics_set_ai_cache(legacy_cache_key, payload)
            return payload

        gen_task = asyncio.create_task(_generate())
        get_task: Optional[asyncio.Task] = None
        try:
            while True:
                get_task = asyncio.create_task(delta_queue.get()) if delta_queue.empty() and not _delta_buf else None
                wait_set = {gen_task} | ({get_task} if get_task else set())
                done, _pending = await asyncio.wait(wait_set, timeout=3.0, return_when=asyncio.FIRST_COMPLETED)
                if get_task:
                    if get_task in done:
                        _delta_buf.append(get_task.result())
                    else:
                        get_task.cancel()
                        await asyncio.gather(get_task, return_exceptions=True)
                    get_task = None
                while not delta_queue.empty():
                    _delta_buf.append(delta_queue.get_nowait())
                if _delta_buf:
                    chunk = "".join(_delta_buf)[-240:]
                    del _delta_buf[:]
                    yield _sse({"type": "thought", "delta": chunk})
                if gen_task in done:
                    break
                elapsed = int(asyncio.get_event_loop().time() - started)
                yield _sse({"type": "tick", "elapsed": elapsed})
            payload = gen_task.result()
        except Exception as exc:
            status = getattr(exc, "status_code", 502)
            detail = str(getattr(exc, "detail", "") or str(exc))[:160]
            yield _sse({"type": "error", "status": status, "detail": f"深度稿生成失败：{detail}"})
            return
        finally:
            tasks = [task for task in (gen_task, get_task) if task is not None]
            for task in tasks:
                if not task.done():
                    task.cancel()
            with anyio.CancelScope(shield=True):
                await asyncio.gather(*tasks, return_exceptions=True)
        if quota_key:
            _complete_cost_quota(quota_key)
        if isinstance(payload, dict) and payload.get("provider") == "local-fallback":
            yield _sse({"type": "stage", "stage": "fallback", "detail": "本轮模型超时，先展示原文摘录版；稍后重试可获取完整解读"})
        yield _sse({"type": "done", "data": _finish(payload).model_dump(mode="json")})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
