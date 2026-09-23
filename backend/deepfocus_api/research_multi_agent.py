"""Adaptive multi-agent analysis for research reports.

When enabled, both text-layer and scanned PDFs let field-owning specialists
inspect the same material in parallel, then merge their disjoint JSON fields
deterministically.  There is deliberately no fourth "synthesis" LLM call:
that would erase any latency win.  With the flag off, the established single-
agent text/vision paths remain unchanged.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
from dataclasses import dataclass
from statistics import mean
from typing import Any, Optional

import fitz

from .llm import CloudResearchLLM
from . import research_vision as rv


_ENABLED = os.getenv("DEEPFOCUS_RESEARCH_MULTI_AGENT", "0").strip().lower() not in {
    "0", "false", "no", "off",
}
_MIN_IMAGES = max(3, int(os.getenv("DEEPFOCUS_RESEARCH_MULTI_AGENT_MIN_IMAGES", "4") or 4))
_AGENT_CONCURRENCY = max(1, int(os.getenv("DEEPFOCUS_RESEARCH_AGENT_CONCURRENCY", "3") or 3))
_AGENT_TIMEOUT_SECONDS = max(
    20.0, float(os.getenv("DEEPFOCUS_RESEARCH_AGENT_TIMEOUT_SECONDS", "60") or 60),
)
_TOTAL_TIMEOUT_SECONDS = max(
    25.0, float(os.getenv("DEEPFOCUS_RESEARCH_AGENT_TOTAL_SECONDS", "65") or 65),
)
_CACHE_VERSION = "agents-v4"


def _cache_max_age_seconds() -> float | None:
    """Keep the secondary vision cache on the same retention as the AI cache."""
    try:
        days = float(os.getenv("DEEPFOCUS_AI_CACHE_MAX_AGE_DAYS", "14") or 14)
    except (TypeError, ValueError):
        days = 14
    return days * 86400.0 if days > 0 else None


_DISCLAIMER = (
    "本结论由多个 AI 角色并行阅读研报材料后合并生成，非逐句溯源，"
    "可能遗漏或误读，请以原文为准。"
)
_SENSITIVE_CONTENT_RE = re.compile(r"content\[(\d+)\]")


class ResearchMultiAgentBusy(RuntimeError):
    """A background prewarm yielded to an interactive analysis batch."""


def multi_agent_enabled() -> bool:
    return _ENABLED


def analysis_cache_key(ref: str) -> str:
    """Version the long-lived main cache only while the experiment is enabled."""
    cleaned = str(ref or "").strip()
    if not cleaned or not _ENABLED:
        return cleaned
    return f"research:{_CACHE_VERSION}:{cleaned}"


@dataclass(frozen=True)
class _Role:
    key: str
    label: str
    schema: str
    instruction: str
    max_tokens: int


_ROLES = (
    _Role(
        key="facts",
        label="事实与估值 Agent",
        schema=(
            '{"subject":"标的", "instruments":["可交易标的"], "market":"A股/港股/美股/无", '
            '"rating":"机构评级或空串", "target_price":"目标价或空串", "confidence":0.0}'
        ),
        instruction=(
            "只提取页面明确出现的标的、市场、评级、目标价和关键数字；"
            "没有的信息留空。不要根据常识补充、推断或评价。"
        ),
        max_tokens=2400,
    ),
    _Role(
        key="thesis",
        label="核心逻辑 Agent",
        schema=(
            '{"one_liner":"看多/看空/中性+最关键理由，40字内", "summary":"最多2句", '
            '"core_logic":"核心因果链，最多3句", "bullish":["最多4条关键依据"], '
            '"logic_lines":[{"title":"独立逻辑线", "evidence":"可核对事实/数字", "chain":"因果传导", "impact":"受益/受压对象", "watch":"验证或反转条件"}], '
            '"instruments":["可交易标的"], "confidence":0.0}'
        ),
        instruction=(
            "只整理原文明确写出的核心观点、因果描述和上行依据；优先保留原文数字、预测变化和催化剂，"
            "原文没有就留空，不要根据常识补充或推导。逻辑线仅保留原文明确出现的事实、传导、影响和验证条件，"
            "尽量拆出至少4条相互独立的线，各字段不要重复。"
        ),
        max_tokens=3600,
    ),
    _Role(
        key="risk",
        label="风险反证 Agent",
        schema=(
            '{"bearish":["最多4条风险或反证，写明触发条件"], '
            '"instruments":["可交易标的"], "confidence":0.0}'
        ),
        instruction=(
            "只提取原文明确写出的风险、限制和触发条件；原文未提及就留空。"
            "不要站在报告外补充风险、反证、投资建议或待验证条件。"
        ),
        max_tokens=2600,
    ),
)


_sem: Optional[asyncio.Semaphore] = None
_sem_loop: Optional[asyncio.AbstractEventLoop] = None
_batch_sem: Optional[asyncio.Semaphore] = None
_batch_sem_loop: Optional[asyncio.AbstractEventLoop] = None


def _agent_semaphore() -> asyncio.Semaphore:
    """Return a loop-local semaphore (pytest uses several ``asyncio.run`` loops)."""
    global _sem, _sem_loop
    loop = asyncio.get_running_loop()
    if _sem is None or _sem_loop is not loop:
        _sem = asyncio.Semaphore(_AGENT_CONCURRENCY)
        _sem_loop = loop
    return _sem


def _batch_semaphore() -> asyncio.Semaphore:
    """Keep each three-Agent batch together so sibling reports cannot starve it."""
    global _batch_sem, _batch_sem_loop
    loop = asyncio.get_running_loop()
    if _batch_sem is None or _batch_sem_loop is not loop:
        _batch_sem = asyncio.Semaphore(1)
        _batch_sem_loop = loop
    return _batch_sem


def _build_prompt(role: _Role, title: Optional[str], symbol: Optional[str]) -> str:
    target = " ".join(part for part in (title, symbol) if part) or "未知"
    return (
        f"你是{role.label}。以下图片来自同一份券商研报，你只负责自己的字段。"
        "仅依据图片中实际可见内容，用自己的话高度浓缩，不得还原或大段复述原文；"
        "原文未提及的字段留空或写「原文未提及」，不得根据常识推断，不得给交易建议。"
        "输出严格 JSON object，不要 Markdown、解释或思考过程。\n"
        f"字段：{role.schema}\n任务：{role.instruction}\n线索标的：{target}。"
    )


def _build_text_prompt(
    role: _Role,
    title: Optional[str],
    symbol: Optional[str],
    text: str,
) -> str:
    target = " ".join(part for part in (title, symbol) if part) or "未知"
    return (
        f"你是{role.label}。以下是同一份券商研报的正文节选，你只负责自己的字段。"
        "仅依据正文中实际出现的内容，用自己的话高度浓缩，不得还原或大段复述原文；"
        "原文未提及的字段留空或写「原文未提及」，不得根据常识推断，不得给交易建议。"
        "输出严格 JSON object，不要 Markdown、解释或思考过程。\n"
        f"字段：{role.schema}\n任务：{role.instruction}\n线索标的：{target}。\n"
        f"=== 研报正文节选 ===\n{text[:rv.MAX_TEXT_CHARS]}"
    )


def _as_list(value: Any, limit: int) -> list[str]:
    if not isinstance(value, list):
        value = [value] if value else []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item or "").strip()
        marker = re.sub(r"\s+", "", text).lower()
        if not text or marker in seen:
            continue
        seen.add(marker)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _pick(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _merge_results(
    results: dict[str, dict[str, Any]], *, provider: str, pages: int,
) -> dict[str, Any]:
    """Merge only the fields owned by each role; no model arbitration is needed."""
    facts = results.get("facts") or {}
    thesis = results.get("thesis") or {}
    risk = results.get("risk") or {}

    instruments = _as_list(
        _as_list(facts.get("instruments"), 8)
        + _as_list(thesis.get("instruments"), 8)
        + _as_list(risk.get("instruments"), 8),
        8,
    )
    confidences: list[float] = []
    for data in (facts, thesis, risk):
        try:
            confidences.append(max(0.0, min(1.0, float(data.get("confidence")))))
        except (TypeError, ValueError):
            pass

    merged = {
        "subject": _pick(facts.get("subject"), thesis.get("subject"), risk.get("subject")),
        "one_liner": _pick(thesis.get("one_liner"), facts.get("one_liner")),
        "summary": _pick(thesis.get("summary"), thesis.get("one_liner")),
        "core_logic": _pick(thesis.get("core_logic")),
        "logic_lines": rv._normalize_logic_lines(thesis.get("logic_lines"), 6),
        "takeaway": "",
        "df_take": "",  # 六维「综合判断与边界」已下线，研报解读只做客观复述
        "bullish": _as_list(thesis.get("bullish") or thesis.get("key_points"), 4),
        "bearish": _as_list(risk.get("bearish") or risk.get("risks"), 4),
        "instruments": instruments,
        "market": _pick(facts.get("market"), thesis.get("market")),
        "rating": _pick(facts.get("rating"), thesis.get("rating")),
        "target_price": _pick(facts.get("target_price"), thesis.get("target_price")),
        "confidence": mean(confidences) if confidences else 0.5,
    }
    return rv._normalize_result(
        merged, provider=provider, pages=pages, disclaimer=_DISCLAIMER, compact_report=True,
    )


async def _call_role(
    llm: CloudResearchLLM,
    role: _Role,
    images: list[bytes],
    *,
    title: Optional[str],
    symbol: Optional[str],
) -> dict[str, Any]:
    """Run one specialist, dropping a falsely flagged sensitive page when possible."""
    prompt = _build_prompt(role, title, symbol)
    attempt_images = list(images)
    max_drops = max(1, len(images) // 2)
    for _ in range(max_drops + 1):
        if not attempt_images:
            break
        try:
            async with _agent_semaphore():
                raw = await llm.complete_vision(
                    prompt,
                    attempt_images,
                    max_tokens=role.max_tokens,
                    timeout_seconds=_AGENT_TIMEOUT_SECONDS,
                )
            data = rv._parse_model_json(raw)
            if not data:
                raise RuntimeError(f"{role.label}未返回有效 JSON")
            return data
        except RuntimeError as exc:
            match = _SENSITIVE_CONTENT_RE.search(str(exc))
            if "sensitive" in str(exc).lower() and match:
                image_index = int(match.group(1)) - 1
                if 0 <= image_index < len(attempt_images):
                    del attempt_images[image_index]
                    continue
            raise
    raise RuntimeError(f"{role.label}没有可分析页面")


async def _call_text_role(
    llm: CloudResearchLLM,
    role: _Role,
    text: str,
    *,
    title: Optional[str],
    symbol: Optional[str],
) -> dict[str, Any]:
    """Run one text specialist; text models return a decoded JSON object directly."""
    prompt = _build_text_prompt(role, title, symbol, text)
    async with _agent_semaphore():
        data = await llm.complete_json(
            prompt,
            max_tokens=role.max_tokens,
            timeout_seconds=_AGENT_TIMEOUT_SECONDS,
        )
    if not isinstance(data, dict) or not data:
        raise RuntimeError(f"{role.label}未返回有效 JSON")
    return data


def _cache_key(
    pdf_bytes: bytes, max_pages: int, title: Optional[str], symbol: Optional[str],
) -> str:
    digest = hashlib.md5(
        pdf_bytes + str(max_pages).encode() + (title or "").encode() + (symbol or "").encode()
    ).hexdigest()
    return f"VIS:{_CACHE_VERSION}:{digest}"


def _cache_get(key: str) -> Optional[dict[str, Any]]:
    try:
        from . import data_store

        cached = data_store.latest("vision", key, max_age_seconds=_cache_max_age_seconds())
        return cached if isinstance(cached, dict) and cached else None
    except Exception:
        return None


def _cache_put(key: str, result: dict[str, Any]) -> None:
    try:
        from . import data_store

        data_store.record("vision", key, result)
    except Exception:
        pass


def _pdf_page_count(pdf_bytes: bytes) -> int:
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        return doc.page_count


async def _analyze_images_parallel(
    pdf_bytes: bytes,
    *,
    title: Optional[str],
    symbol: Optional[str],
    max_pages: int,
    background: bool = False,
) -> dict[str, Any]:
    key = _cache_key(pdf_bytes, max_pages, title, symbol)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    page_count = await asyncio.to_thread(_pdf_page_count, pdf_bytes)
    if page_count < _MIN_IMAGES:
        # Check page count before rendering so the established single-agent path can
        # hit its own cache and never pays for a duplicate render.
        return await rv.analyze_pdf_vision(
            pdf_bytes, title=title, symbol=symbol, max_pages=max_pages,
        )

    # Queue before starting the per-report deadline.  With a 3-slot global child
    # semaphore, interleaving three reports (nine tasks) made later reports time
    # out while merely waiting for slots.
    batch_sem = _batch_semaphore()
    if background and batch_sem.locked():
        raise ResearchMultiAgentBusy("交互式研报解读正在运行，后台预热稍后重试")
    async with batch_sem:
        # A prewarm and an HTTP request can miss the inner cache concurrently.
        # Recheck after queueing so the follower does not pay for three duplicate calls.
        late_cached = _cache_get(key)
        if late_cached is not None:
            return late_cached
        images = await asyncio.to_thread(rv.render_pdf_to_pngs, pdf_bytes, max_pages=max_pages)
        if len(images) < _MIN_IMAGES:
            return await rv.analyze_pdf_vision(
                pdf_bytes, title=title, symbol=symbol, max_pages=max_pages,
            )

        llm = CloudResearchLLM()
        if llm.provider == "mock":
            raise RuntimeError("当前为本地演示模型，无法做多 Agent 研报解读；请配置云端视觉模型。")

        tasks: list[asyncio.Task[dict[str, Any]]] = []
        try:
            # Every specialist sees all selected pages.  Field ownership without
            # page coverage caused deterministic omissions (for example a target
            # price on a page assigned only to the risk role).
            tasks = [
                asyncio.create_task(
                    _call_role(llm, role, images, title=title, symbol=symbol),
                    name=f"research-{role.key}",
                )
                for role in _ROLES
            ]
            done, pending = await asyncio.wait(tasks, timeout=_TOTAL_TIMEOUT_SECONDS)
            if pending:
                labels = [role.label for role, task in zip(_ROLES, tasks) if task in pending]
                raise RuntimeError(f"多 Agent 研报解读超时：{'、'.join(labels)}")

            results: dict[str, dict[str, Any]] = {}
            errors: list[str] = []
            for role, task in zip(_ROLES, tasks):
                try:
                    results[role.key] = task.result()
                except Exception as exc:  # noqa: BLE001 - aggregate role failures into one clear error
                    errors.append(f"{role.label}: {str(exc)[:100]}")
            if errors:
                # Never cache a report that silently lost its facts/thesis/risk role.
                raise RuntimeError(f"多 Agent 研报解读未完整：{'；'.join(errors)}")
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    result = _merge_results(results, provider=f"{llm.model} · 3 agents", pages=len(images))
    result["agent_count"] = 3
    result["analysis_mode"] = "multi_agent_vision"
    _cache_put(key, result)
    return result


async def _analyze_text_parallel(
    pdf_bytes: bytes,
    *,
    title: Optional[str],
    symbol: Optional[str],
    max_pages: int,
    background: bool = False,
) -> dict[str, Any]:
    """Extract once, then run facts/thesis/risk text agents concurrently."""
    key = _cache_key(pdf_bytes, max_pages, title, symbol)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    try:
        # Long reports are deliberately text-only. Read a bounded 60-page
        # window so conclusions and targets in the tail are not missed.
        text = await asyncio.to_thread(
            rv.extract_pdf_text, pdf_bytes, max_pages=60,
        )
    except Exception as exc:  # PDF corruption belongs on the established vision fallback.
        raise rv.PdfTextUnavailable("无法读取 PDF 文本层，转视觉解读") from exc
    if len(text) < rv.MIN_TEXT_CHARS:
        raise rv.PdfTextUnavailable("文本层过少，转视觉解读")

    batch_sem = _batch_semaphore()
    if background and batch_sem.locked():
        raise ResearchMultiAgentBusy("交互式研报解读正在运行，后台预热稍后重试")
    async with batch_sem:
        late_cached = _cache_get(key)
        if late_cached is not None:
            return late_cached

        llm = CloudResearchLLM()
        if llm.provider == "mock":
            raise RuntimeError("当前为本地演示模型，无法做多 Agent 研报解读；请配置云端模型。")

        tasks: list[asyncio.Task[dict[str, Any]]] = [
            asyncio.create_task(
                _call_text_role(llm, role, text, title=title, symbol=symbol),
                name=f"research-text-{role.key}",
            )
            for role in _ROLES
        ]
        try:
            done, pending = await asyncio.wait(tasks, timeout=_TOTAL_TIMEOUT_SECONDS)
            if pending:
                labels = [role.label for role, task in zip(_ROLES, tasks) if task in pending]
                raise RuntimeError(f"多 Agent 研报解读超时：{'、'.join(labels)}")

            results: dict[str, dict[str, Any]] = {}
            errors: list[str] = []
            for role, task in zip(_ROLES, tasks):
                try:
                    results[role.key] = task.result()
                except Exception as exc:  # noqa: BLE001 - aggregate role failures into one clear error
                    errors.append(f"{role.label}: {str(exc)[:100]}")
            if errors:
                raise RuntimeError(f"多 Agent 研报解读未完整：{'；'.join(errors)}")
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    result = _merge_results(
        results,
        provider=f"{llm.model} · 3 agents",
        pages=min(rv.MAX_VISION_PAGES, max(1, max_pages)),
    )
    result["agent_count"] = 3
    result["analysis_mode"] = "multi_agent_text"
    _cache_put(key, result)
    return result


async def analyze_pdf_adaptive(
    pdf_bytes: bytes,
    *,
    title: Optional[str] = None,
    symbol: Optional[str] = None,
    max_pages: int = 6,
    background: bool = False,
    text_only: bool = False,
) -> dict[str, Any]:
    """Use text specialists; optionally refuse the expensive image fallback."""
    if not _ENABLED:
        if text_only:
            try:
                return await rv.analyze_pdf_text(
                    pdf_bytes, title=title, symbol=symbol, max_pages=60,
                )
            except rv.PdfTextUnavailable:
                return rv._normalize_result(
                    {
                        "subject": symbol or "",
                        "one_liner": "未提取到可用文字，未进行图片解读。",
                        "summary": "这份研报未检测到可读取的文字层，因此本次仅尝试文字解读，没有调用图片识别。",
                        "source_note": "文字层不可提取；未进行图片解读",
                    },
                    provider="text-only-fallback",
                    pages=0,
                    disclaimer="本次仅基于 PDF 可提取文字；未进行图片识别，请以原文为准。",
                    compact_report=True,
                )
        return await rv.analyze_pdf_auto(
            pdf_bytes, title=title, symbol=symbol, max_pages=max_pages,
        )
    try:
        return await _analyze_text_parallel(
            pdf_bytes, title=title, symbol=symbol, max_pages=max_pages, background=background,
        )
    except rv.PdfTextUnavailable:
        pass
    except (ResearchMultiAgentBusy, asyncio.CancelledError):
        raise
    except Exception as exc:  # noqa: BLE001 - preserve the established text fallback
        try:
            result = await rv.analyze_pdf_text(
                pdf_bytes, title=title, symbol=symbol,
            )
        except rv.PdfTextUnavailable:
            # A provider failure on the text route should not prevent a scanned
            # report from trying the visual route below.
            pass
        except Exception:
            raise
        else:
            result = dict(result)
            result.setdefault("analysis_mode", "single_agent_fallback")
            result.setdefault("multi_agent_warning", str(exc)[:240])
            return result

    if text_only:
        return rv._normalize_result(
            {
                "subject": symbol or "",
                "one_liner": "未提取到可用文字，未进行图片解读。",
                "summary": "这份研报未检测到可读取的文字层，因此本次仅尝试文字解读，没有调用图片识别。",
                "source_note": "文字层不可提取；未进行图片解读",
            },
            provider="text-only-fallback",
            pages=0,
            disclaimer="本次仅基于 PDF 可提取文字；未进行图片识别，请以原文为准。",
            compact_report=True,
        )

    try:
        return await _analyze_images_parallel(
            pdf_bytes, title=title, symbol=symbol, max_pages=max_pages, background=background,
        )
    except (ResearchMultiAgentBusy, asyncio.CancelledError):
        raise
    except Exception as exc:  # noqa: BLE001 - preserve availability when a specialist/provider misbehaves
        # A single malformed/slow specialist must not make the whole report
        # unreadable.  Partial multi-Agent output was never cached, so it is
        # safe to retry through the established visual path and cache only
        # that complete result at the caller.
        result = await rv.analyze_pdf_vision(
            pdf_bytes, title=title, symbol=symbol, max_pages=max_pages,
        )
        result = dict(result)
        result.setdefault("analysis_mode", "single_agent_fallback")
        result.setdefault("multi_agent_warning", str(exc)[:240])
        return result
