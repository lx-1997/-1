"""Adaptive multi-agent analysis for image-based research reports.

Text PDFs already have a fast single-call path.  The optional scanned-PDF mode
renders once, lets field-owning specialists inspect the selected pages in
parallel, then merges their disjoint JSON fields deterministically.  There is
deliberately no fourth "synthesis" LLM call: that would erase any latency win.
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
_CACHE_VERSION = "agents-v2"
_DISCLAIMER = (
    "本结论由多个 AI 角色并行阅读研报页面后合并生成，非逐句溯源，"
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
            "没有的信息留空，不做推测。"
        ),
        max_tokens=1800,
    ),
    _Role(
        key="thesis",
        label="核心逻辑 Agent",
        schema=(
            '{"one_liner":"看多/看空/中性+最关键理由，35字内", "summary":"最多2句", '
            '"core_logic":"核心因果链，最多2句", "bullish":["最多3条关键依据"], '
            '"instruments":["可交易标的"], "confidence":0.0}'
        ),
        instruction=(
            "只负责核心观点、驱动因果链和上行依据；优先保留数字、预测变化和催化剂，"
            "各字段不要重复。"
        ),
        max_tokens=2800,
    ),
    _Role(
        key="risk",
        label="风险反证 Agent",
        schema=(
            '{"bearish":["最多3条风险或反证，写明触发条件"], '
            '"instruments":["可交易标的"], "confidence":0.0}'
        ),
        instruction=(
            "站在买方风控视角，只找报告结论成立的前提、反证、下行触发条件和待验证指标；"
            "不得编造报告外事实。"
        ),
        max_tokens=1800,
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
        "仅依据图片中实际可见内容，用自己的话高度浓缩，不得还原或大段复述原文，"
        "不得给确定性交易指令。输出严格 JSON object，不要 Markdown、解释或思考过程。\n"
        f"字段：{role.schema}\n任务：{role.instruction}\n线索标的：{target}。"
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
        "takeaway": "",
        "df_take": "",
        "bullish": _as_list(thesis.get("bullish") or thesis.get("key_points"), 3),
        "bearish": _as_list(risk.get("bearish") or risk.get("risks"), 3),
        "instruments": instruments,
        "market": _pick(facts.get("market"), thesis.get("market")),
        "rating": _pick(facts.get("rating"), thesis.get("rating")),
        "target_price": _pick(facts.get("target_price"), thesis.get("target_price")),
        "confidence": mean(confidences) if confidences else 0.5,
    }
    return rv._normalize_result(
        merged, provider=provider, pages=pages, disclaimer=_DISCLAIMER,
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

        cached = data_store.latest("vision", key, max_age_seconds=14 * 86400)
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


async def analyze_pdf_adaptive(
    pdf_bytes: bytes,
    *,
    title: Optional[str] = None,
    symbol: Optional[str] = None,
    max_pages: int = 6,
    background: bool = False,
) -> dict[str, Any]:
    """Use the fast text agent, or parallel specialists for image-only reports."""
    if not _ENABLED:
        return await rv.analyze_pdf_auto(
            pdf_bytes, title=title, symbol=symbol, max_pages=max_pages,
        )
    try:
        # Preserve the established text-path coverage (up to MAX_VISION_PAGES);
        # ``max_pages`` historically controls only the slower visual route.
        return await rv.analyze_pdf_text(pdf_bytes, title=title, symbol=symbol)
    except rv.PdfTextUnavailable:
        return await _analyze_images_parallel(
            pdf_bytes, title=title, symbol=symbol, max_pages=max_pages, background=background,
        )
