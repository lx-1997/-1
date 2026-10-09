"""Long-form, source-bound research drafts.

The existing ``research_vision`` path is deliberately a compact card.  This
module adds the next layer: a structured article which can be rendered by a
mobile reader (thesis, sections, tables, watch list and provenance).  It is
kept independent from ``main.py`` so the endpoint can be mounted with one
small ``app.include_router(router)`` change while the large application file
is being edited by other work.

Important design rules:

* The model is asked to *synthesise supplied pages*, never to fill gaps from
  general knowledge.
* Every generated claim may carry a page/excerpt.  Missing evidence is kept as
  ``原文未提供`` rather than silently invented.
* If the model, PDF, or workbench is unavailable, a deterministic extraction
  fallback still returns the same response shape with a low confidence and a
  visible disclaimer.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Union
from urllib.parse import urlparse

import fitz  # PyMuPDF
import httpx
from fastapi import APIRouter, HTTPException

from .llm import CloudResearchLLM
from .research_vision import _strip_brand_text, analyze_pdf_vision, extract_pdf_ocr_text
from .research_multi_agent import analyze_pdf_adaptive
from .research_workbench import WORKBENCH_DIR
from .schemas import (
    ResearchDeepDraftCoverage,
    ResearchDeepDraftEvidence,
    ResearchDeepDraftMetric,
    ResearchDeepDraftRequest,
    ResearchDeepDraftResponse,
    ResearchDeepDraftRisk,
    ResearchDeepDraftSection,
    ResearchDeepDraftSource,
    ResearchDeepDraftTable,
    ResearchDeepDraftWatchItem,
)


router = APIRouter()

_DEFAULT_MAX_PAGES = 32
_MAX_PAGES = 60
_MAX_PROMPT_CHARS = 58_000
# ``CloudResearchLLM.complete_json`` may make up to three attempts (initial
# JSON call plus format retries).  Keep each attempt and the whole deep-draft
# request below the client's 360s timeout so a malformed-model response cannot
# occupy the global semaphore indefinitely after the browser has given up.
_DEEP_LLM_CALL_TIMEOUT_SECONDS = 240  # 长报告（5万+字符提示词）生成需 >100s：100s 会超时静默失败落兜底
_DEEP_LLM_TOTAL_TIMEOUT_SECONDS = 315
# Image-only deep drafts use the same page budget as the compact report.  The
# previous six-page cap made long decks look empty even when the key chart or
# target price appeared later in the report.
_DEEP_VISION_MAX_PAGES = 14
# Below this many extractable chars the "text layer" is a cover-page stub on an
# otherwise scanned deck; synthesizing from it yields a zero-confidence stub,
# so the document is routed to the page-parallel vision path instead.
_DEEP_TEXT_FLOOR_CHARS = 600
_MAX_SECTION_COUNT = 12
_MAX_PARAGRAPHS_PER_SECTION = 8
_MAX_BULLETS_PER_SECTION = 8
_MAX_EVIDENCE_PER_SECTION = 8
_MAX_TABLES = 8
_MAX_METRICS = 6
_MAX_WATCHLIST = 10
_MAX_RISKS = 10
_MAX_INSTRUMENTS = 16
# Evidence chips are provenance hints, not a copy of the third-party report.
# Keep excerpts short even when a model ignores the prompt's 80-character rule.
_MAX_EXCERPT_CHARS = 80
_MISSING = "原文未提供"
_UNSPECIFIED = "原文未明确说明"
_DISCLAIMER = (
    "本内容由 AI 生成，仅供参考，不构成投资建议。以下为对所列机构/研报观点的结构化整理，"
    "观点归属于原作者/机构，不代表 DeepFocus 投资建议；本深度稿仅整理原文可见信息，"
    "非逐句法律/事实核验；没有证据的字段会标注‘原文未提供’，请以原始研报为准。"
)
_FALLBACK_DISCLAIMER = (
    "本内容由 AI 生成，仅供参考，不构成投资建议。以下为对所列机构/研报观点的结构化整理，"
    "观点归属于原作者/机构，不代表 DeepFocus 投资建议。当前未能调用云端模型；本稿只展示少量短摘录，"
    "不替代原始研报，缺失内容标注为‘原文未提供’，请以原始研报为准。"
)


def _safe_disclaimer(value: Any, *, fallback: str = _DISCLAIMER) -> str:
    """Apply the shared neutral wording and explicit AI label exactly once."""

    text = _clean(value, 1_000) or fallback
    try:
        from .compliance import ai_label, neutralize_text

        return ai_label(neutralize_text(text))
    except Exception:
        # Keep this module usable in minimal extraction/test environments where
        # optional compliance dependencies are not installed.
        return text if "AI 生成" in text or "AI生成" in text else f"{text}\n\n（本内容由 AI 生成，仅供参考，不构成投资建议）"


# ``neutralize_deep`` is intentionally broad and is useful for ordinary AI
# prose.  A research draft also contains source metadata and short quotations,
# though; rewriting a quoted phrase such as “建议买入” would corrupt the
# evidence rather than make it safer.  Neutralise generated/narrative fields
# while preserving provenance and structured source values.
_RAW_DRAFT_CONTAINERS = frozenset({"evidence", "sources"})
_RAW_DRAFT_KEYS = frozenset({
    "excerpt", "quote", "source_id", "url", "page", "pages", "kind",
    "generated_at", "provider", "symbol", "instruments",
})


def neutralize_deep_draft(value: Any) -> Any:
    """Neutralise user-facing draft prose without mutating source quotations.

    The function is deliberately kept in this module so both the generator
    and the authenticated FastAPI route apply exactly the same boundary guard.
    Unknown future fields remain protected by default; only values nested in
    an explicitly provenance-oriented container are treated as raw source.
    """

    try:
        from .compliance import neutralize_text
    except Exception:
        return value

    def walk(current: Any, path: tuple[Any, ...] = ()) -> Any:
        if isinstance(current, str):
            # Source/evidence records are quotations/metadata, not platform
            # advice.  Keep them intact (apart from the hard short-excerpt cap)
            # so page checks remain useful.
            if path and path[-1] in {"excerpt", "quote"}:
                return current[:_MAX_EXCERPT_CHARS]
            if any(part in _RAW_DRAFT_CONTAINERS for part in path):
                return current
            if path and path[-1] in _RAW_DRAFT_KEYS:
                return current
            return neutralize_text(current)
        if isinstance(current, list):
            return [walk(item, path) for item in current]
        if isinstance(current, dict):
            return {key: walk(item, (*path, key)) for key, item in current.items()}
        return current

    return walk(value)


@dataclass
class SourceDocument:
    """Normalised input document used by the synthesis and fallback paths."""

    source_id: str
    title: str
    pdf_bytes: bytes = b""
    url: Optional[str] = None
    filename: Optional[str] = None
    page_texts: list[tuple[int, str]] = field(default_factory=list)
    total_pages: int = 0
    kind: str = "pdf"

    @property
    def text(self) -> str:
        return "\n".join(
            f"[第 {page} 页]\n{text}" for page, text in self.page_texts if text.strip()
        )

    @property
    def chars_read(self) -> int:
        return sum(len(text) for _, text in self.page_texts)

    @property
    def pages_read(self) -> int:
        return len(self.page_texts)


def _clean(value: Any, limit: int = 2_000) -> str:
    """Convert model values to bounded display strings without raising."""

    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return ""
    text = str(value).strip()
    return text[:limit]


def _list(value: Any, limit: int = 8, item_limit: int = 600) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _clean(item, item_limit)
        key = re.sub(r"\s+", "", text).casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _clamp(value: Any, default: float = 0.25) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _page(value: Any, total_pages: int = 0) -> Optional[int]:
    # A page number is meaningful only when the server knows the page range.
    # Treat an unknown range as unverifiable instead of accepting arbitrary
    # model-supplied numbers (especially when multiple sources omit source_id).
    if not total_pages:
        return None
    try:
        page = int(value)
    except (TypeError, ValueError):
        return None
    if page < 1:
        return None
    if total_pages and page > total_pages:
        return None
    return page


def _pages(value: Any, total_pages: int = 0) -> Optional[str]:
    """Normalise a model-supplied page/range label against known pages.

    ``page`` is the canonical numeric citation.  ``pages`` is only a display
    hint, so keep harmless labels such as ``全文`` but never expose numeric
    pages when the server did not receive that page from a source document.
    """

    if value is None:
        return None
    if isinstance(value, (list, tuple, set)):
        raw = " ".join(_clean(item, 20) for item in value)
    else:
        raw = _clean(value, 60)
    if not raw:
        return None
    numbers: list[int] = []
    for token in re.findall(r"\d+", raw):
        try:
            number = int(token)
        except (TypeError, ValueError):
            continue
        if number not in numbers:
            numbers.append(number)
    if not numbers:
        # A non-numeric label is not a fabricated page claim (e.g. “全文”).
        return raw
    if total_pages <= 0:
        return None
    numbers = [number for number in numbers if 1 <= number <= total_pages]
    if not numbers:
        return None
    separator = "–" if re.search(r"[-–—]", raw) and len(numbers) > 1 else ","
    return separator.join(str(number) for number in numbers)


def _normalise_max_pages(value: Any) -> int:
    try:
        return max(1, min(_MAX_PAGES, int(value)))
    except (TypeError, ValueError):
        return _DEFAULT_MAX_PAGES


def _source_label(doc: SourceDocument) -> str:
    return _clean(doc.title or doc.filename or doc.source_id or "研报", 160) or "研报"


def _read_page_limit(doc: SourceDocument) -> int:
    """Highest page actually available to the synthesis prompt.

    For text PDFs this is intentionally based on ``page_texts`` rather than
    the document's full page count: a request may cap reading at page 32 of a
    60-page report, and a model must not cite page 55.  Image-only documents
    have no text markers, so their known PDF page count remains the safe upper
    bound for visual evidence.
    """

    read_max = max((page for page, text in doc.page_texts if text.strip()), default=0)
    return read_max or max(0, int(doc.total_pages or 0))


def _extract_page_texts(pdf_bytes: bytes, max_pages: int) -> tuple[list[tuple[int, str]], int]:
    """Extract page-aware text, stripping the site's own PDF footer watermark."""

    if not pdf_bytes:
        return [], 0
    page_texts: list[tuple[int, str]] = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        total = doc.page_count
        for index in range(min(max_pages, total)):
            raw = doc.load_page(index).get_text("text") or ""
            try:
                text = _strip_brand_text(raw).strip()
            except Exception:
                text = raw.strip()
            if text:
                page_texts.append((index + 1, text))
    return page_texts, total


def _make_document(
    pdf_bytes: bytes,
    *,
    source_id: str,
    title: str,
    url: Optional[str] = None,
    filename: Optional[str] = None,
    max_pages: int = _DEFAULT_MAX_PAGES,
) -> SourceDocument:
    try:
        page_texts, total_pages = _extract_page_texts(pdf_bytes, max_pages)
    except Exception:
        page_texts, total_pages = [], 0
    return SourceDocument(
        source_id=source_id or title or "source-1",
        title=title or filename or source_id or "研报",
        pdf_bytes=pdf_bytes,
        url=url,
        filename=filename,
        page_texts=page_texts,
        total_pages=total_pages,
    )


def _safe_workbench_path(out: str, filename: str) -> Path:
    """Resolve a workbench file while preventing path traversal."""

    root = WORKBENCH_DIR.resolve()
    base = (root / (out or "downloads/海外投行报告")).resolve()
    try:
        base.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="抓取目录不在研报工作台内。") from exc
    if not filename or Path(filename).name in {"manifest.json", "files.csv"}:
        raise HTTPException(status_code=400, detail="该文件不是可入库研报。")
    path = (base / filename).resolve()
    try:
        path.relative_to(base)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="文件路径越界。") from exc
    if path.suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="深度研报稿仅支持 PDF 文件。")
    if not path.is_file():
        # A missing source is a normal resource miss.  The frontend checks the
        # detail text before treating a 404 as an old-route compatibility
        # signal, so we can keep conventional HTTP semantics without burning a
        # second compact-generation request.
        raise HTTPException(status_code=404, detail="抓取文件不存在。")
    return path


def _allowed_pdf_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.lower().rstrip(".")
    # Match the existing research endpoint's Eastmoney source.  Arbitrary or
    # loopback URLs would turn this public-ish endpoint into an SSRF proxy; use
    # ``file_id``/``workbench_filename`` for local workbench files instead.
    return (
        host == "pdf.dfcfw.com"
        or host == "eastmoney.com"
        or host.endswith(".eastmoney.com")
    )


async def _fetch_pdf_url(url: str) -> bytes:
    if not _allowed_pdf_url(url):
        raise HTTPException(status_code=400, detail="仅支持东方财富研报 PDF 直链。")
    try:
        async with httpx.AsyncClient(
            trust_env=False,
            timeout=httpx.Timeout(connect=15, read=90, write=30, pool=15),
            follow_redirects=True,
            headers={"Referer": "https://data.eastmoney.com/", "User-Agent": "Mozilla/5.0"},
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            # ``follow_redirects`` is enabled for Eastmoney's occasional CDN
            # hop, but the final host must remain on the allowlist as well.
            if not _allowed_pdf_url(str(getattr(response, "url", url))):
                raise HTTPException(status_code=400, detail="研报来源重定向到了不受支持的地址。")
            content = response.content
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"研报 PDF 拉取失败：{str(exc)[:120]}") from exc
    if not content.startswith(b"%PDF"):
        raise HTTPException(status_code=502, detail="来源未返回有效 PDF。")
    return content


async def _fetch_file_id(file_id: str, filename: str = "") -> bytes:
    """Use the existing workbench downloader at request time (avoids import cycles)."""

    # A branded file-id cache is the cheapest and most reliable path.
    try:
        from .pdf_brand import get_cached_by_file_id  # type: ignore

        cached = get_cached_by_file_id(file_id)
        if cached:
            return cached
    except Exception:
        pass
    try:
        # main imports this module, so importing the helper lazily is important.
        from .main import _fetch_research_online_pdf  # type: ignore

        content, _ = await _fetch_research_online_pdf(file_id, filename)
        return content
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"在线研报拉取失败：{str(exc)[:120]}") from exc


async def resolve_source_documents(
    request: ResearchDeepDraftRequest,
    *,
    loader: Any = None,
) -> list[SourceDocument]:
    """Resolve the request's primary source plus optional ``source_ids``.

    ``loader`` is an intentionally small injection point for tests and for a
    future archive service.  It receives an id and may return bytes, a
    ``SourceDocument``, or ``None``.
    """

    max_pages = _normalise_max_pages(request.max_pages)
    docs: list[SourceDocument] = []
    seen: set[str] = set()

    async def add(
        value: Any,
        *,
        title: str = "",
        filename: str = "",
        url: str = "",
        source_id: str = "",
    ) -> None:
        if isinstance(value, SourceDocument):
            # A loader may return a generic ``SourceDocument`` id.  The id
            # supplied by ``source_ids`` is authoritative so two selected
            # reports with the same display title do not collapse into one.
            if source_id:
                value.source_id = source_id
            if title and not value.title:
                value.title = title
            if value.source_id in seen:
                return
            if not value.page_texts and value.pdf_bytes:
                value.page_texts, value.total_pages = _extract_page_texts(value.pdf_bytes, max_pages)
            docs.append(value)
            seen.add(value.source_id)
            return
        raw = value if isinstance(value, (bytes, bytearray)) else b""
        if not raw:
            return
        sid = (source_id or url or filename or title or f"source-{len(docs) + 1}").strip()
        if sid in seen:
            return
        doc = _make_document(bytes(raw), source_id=sid, title=title or filename or sid, url=url or None, filename=filename or None, max_pages=max_pages)
        if not doc.page_texts:
            ocr = await asyncio.to_thread(extract_pdf_ocr_text, bytes(raw), max_pages=min(max_pages, 32))
            if ocr:
                doc.page_texts = [(1, ocr)]
        docs.append(doc)
        seen.add(sid)

    async def load_id(source_id: str) -> None:
        sid = str(source_id or "").strip()
        if not sid or sid in seen:
            return
        if loader is not None:
            result = loader(sid)
            if asyncio.iscoroutine(result):
                result = await result
            await add(
                result,
                title=request.title or sid,
                filename=request.filename or "",
                source_id=sid,
            )
            return
        if sid.startswith(("http://", "https://")):
            await add(await _fetch_pdf_url(sid), title=request.title or sid, url=sid, source_id=sid)
            return
        # Source ids are normally workbench file ids.  A local filename is a
        # useful fallback for archive imports and development environments.
        try:
            await add(await _fetch_file_id(sid, request.filename or ""), title=request.title or sid, source_id=sid)
        except HTTPException as remote_exc:
            # Only filename-shaped ids are eligible for a local fallback.  For
            # a real remote file id, preserve the downloader's 502/404 instead
            # of masking it with a confusing local-path error.
            looks_like_filename = sid.lower().endswith(".pdf") or "/" in sid or "\\" in sid
            if not looks_like_filename:
                raise
            try:
                path = _safe_workbench_path(request.workbench_out, sid)
            except HTTPException:
                raise remote_exc
            await add(path.read_bytes(), title=request.title or path.name, filename=path.name, source_id=sid)

    # Preserve the user's ordering: primary source first, then selected ids.
    if request.file_id:
        await load_id(request.file_id)
    elif request.workbench_filename:
        filename = request.workbench_filename
        path = _safe_workbench_path(request.workbench_out, filename)
        await add(path.read_bytes(), title=request.title or path.name, filename=path.name)
    elif request.pdf_url:
        await add(await _fetch_pdf_url(request.pdf_url), title=request.title or request.pdf_url, url=request.pdf_url)
    elif request.filename:
        # ``filename`` is also accepted as a local-only convenience when no
        # file_id/URL was supplied; keeping it last avoids silently ignoring a
        # caller's explicit remote PDF URL.
        path = _safe_workbench_path(request.workbench_out, request.filename)
        await add(path.read_bytes(), title=request.title or path.name, filename=path.name)

    for source_id in request.source_ids:
        await load_id(source_id)
    return docs


def _prompt_for_documents(
    request: ResearchDeepDraftRequest,
    docs: list[SourceDocument],
) -> str:
    """Build a bounded, page-labelled synthesis prompt."""

    chunks: list[str] = []
    remaining = _MAX_PROMPT_CHARS
    for index, doc in enumerate(docs, 1):
        text = doc.text
        if not text:
            continue
        # Keep each source represented even when one very long report fills the
        # context window.  At least 4k chars are reserved for later sources.
        reserve = max(0, (len(docs) - index) * 4_000)
        take = min(len(text), max(2_000, remaining - reserve))
        if take <= 0:
            break
        chunks.append(
            f"=== 来源 {index}：{_source_label(doc)}（source_id={doc.source_id}）===\n"
            + text[:take]
        )
        remaining -= take
    material = "\n\n".join(chunks) or _MISSING
    target = " ".join(x for x in (request.title, request.symbol) if x).strip() or "未知主题"
    schema = r'''{
  "subtitle":"副标题，概括研究对象与主线，40字内",
  "subject":"研究对象；原文未提及则写原文未提供",
  "one_liner":"首屏一句话结论，只依据材料，60字内",
  "plain_language_summary":"给第一次看研报的人看的白话解释，1-2句、120字内，不增加原文没有的因果",
  "executive_summary":"3-5句首屏摘要，覆盖关键数字/时间点/主体",
  "core_conclusion":"核心结论，明确材料支持与不支持的边界",
  "thesis":"完整主线：需求/供给/盈利或其他原文因果，最多180字",
  "decision_implication":"研究含义：对后续观察/判断有什么影响，最多120字，不写买卖建议",
  "metrics":[{"label":"指标名","value":"当前值","change":"相对变化","context":"白话解释","period":"期间","unit":"单位"}],
  "glossary":[{"term":"原文术语","meaning":"给非专业读者的一句话解释"}],
  "sections":[{"id":"s1","title":"判断式章节标题","summary":"本节摘要",
    "paragraphs":["2-5段正文，每段80-350字"],"bullets":["本节硬事实或判断"],
    "evidence":[{"page":1,"excerpt":"可在原文核对的短摘录","label":"证据标签","source_id":"来源 id","kind":"pdf"}]}],
  "tables":[{"title":"仅在原文有成组数据时生成","columns":["列1"],"rows":[["值1"]],"note":"口径"}],
  "watchlist":[{"title":"跟踪项","item":"标的/指标","signal":"方向","window":"时间窗",
    "metric":"指标","trigger":"原文给出的触发/反转条件","why":"为什么重要",
    "evidence":[{"page":1,"excerpt":"证据"}]}],
  "risks":[{"title":"风险标题","detail":"材料明确写出的风险/限制","evidence":[{"page":1,"excerpt":"证据"}]}],
  "instruments":["材料明确提及的上市公司/商品/加密资产"],
  "sources":[{"title":"来源标题","label":"来源标签","page":1,"excerpt":"短摘录","kind":"pdf","source_id":"来源 id"}],
  "confidence":0.0
}'''
    return (
        "你是专业投研编辑，负责把给定研报材料整理成可公开阅读的深度稿。"
        "严格只使用材料中实际出现的事实、数字、预测、公司和时间点；不要补充行业常识，"
        "不要给交易指令。多来源时要去重并标明来源，不要把不同报告的结论混为一谈。"
        "文章要有清晰的‘结论→证据→机制→影响→后续验证’叙事，按材料信息量生成4-12节，"
        "而不是为了凑长度重复句子。首屏字段必须互相分工：one_liner只给判断，plain_language_summary给小白解释，"
        "executive_summary补充数字和时间，core_conclusion说明证据边界，decision_implication说明研究含义；不要把同一段话改写四遍。"
        "metrics只选3-6个最重要、可在原文核对的数字；glossary只解释材料中实际出现且可能难懂的术语。"
        "只有材料明确存在成组数据时才生成表格；没有就返回空数组。"
        "每个证据 page 必须是输入页码，excerpt 不得超过80字；找不到证据时写‘原文未提供’，"
        "watchlist 的 trigger/window 也不能凭常识猜。输出严格 JSON object，不要 Markdown、解释或思考过程。\n"
        f"输出结构：{schema}\n主题线索：{target}\n"
        f"输入材料（共 {len(docs)} 个来源，页码已标注）：\n{material}"
    )


def _evidence(value: Any, *, docs: list[SourceDocument], default_kind: str = "pdf") -> list[dict[str, Any]]:
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    totals = {doc.source_id: _read_page_limit(doc) for doc in docs}
    known_ids = set(totals)
    default_total = _read_page_limit(docs[0]) if len(docs) == 1 else 0
    out: list[dict[str, Any]] = []
    for item in value[:_MAX_EVIDENCE_PER_SECTION]:
        if isinstance(item, str):
            item = {"excerpt": item}
        if not isinstance(item, dict):
            continue
        raw_sid = _clean(item.get("source_id"), 160) or None
        # A model cannot introduce a new provenance id.  Keeping an unknown id
        # (or silently attaching it to the only input document) would make a
        # citation look source-bound while the server never read that source.
        if raw_sid and raw_sid not in known_ids:
            continue
        sid = raw_sid
        total_for_source = totals.get(sid or "", 0) if sid else default_total
        page = _page(item.get("page"), total_for_source) if total_for_source else None
        pages = _pages(item.get("pages"), total_for_source)
        excerpt = _clean(item.get("excerpt") or item.get("text"), _MAX_EXCERPT_CHARS)
        label = _clean(item.get("label") or item.get("title"), 120)
        kind = _clean(item.get("kind"), 40) or default_kind
        if not excerpt and not page and not label:
            continue
        out.append({"page": page, "pages": pages, "excerpt": excerpt, "label": label, "source_id": sid, "kind": kind})
    return out


def _table(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    out: list[dict[str, Any]] = []
    for item in value[:_MAX_TABLES]:
        if not isinstance(item, dict):
            continue
        title = _clean(item.get("title"), 160)
        columns = _list(item.get("columns"), 12, 120)
        rows_raw = item.get("rows") if isinstance(item.get("rows"), list) else []
        rows: list[list[str]] = []
        width = len(columns)
        for row in rows_raw[:30]:
            if isinstance(row, dict):
                row = [row.get(col, "") for col in columns]
            if not isinstance(row, (list, tuple)):
                continue
            vals = [_clean(cell, 180) for cell in row[:12]]
            if width:
                vals = (vals + [""] * width)[:width]
            if any(vals):
                rows.append(vals)
        if title and columns and rows:
            out.append({"title": title, "columns": columns, "rows": rows, "note": _clean(item.get("note"), 300) or None})
    return out


def _metrics(value: Any, docs: list[SourceDocument]) -> list[dict[str, Any]]:
    """Normalise the small signal strip shown before the long-form article."""

    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(value[:_MAX_METRICS], 1):
        if isinstance(item, (str, int, float)):
            item = {"label": str(item)}
        if not isinstance(item, dict):
            continue
        label = _clean(
            item.get("label") or item.get("title") or item.get("name")
            or item.get("metric_label") or item.get("metric_key"),
            160,
        )
        key = re.sub(r"\s+", "", label).casefold()
        if not label or key in seen:
            continue
        seen.add(key)
        evidence = _evidence(item.get("evidence"), docs=docs)
        raw_value = item.get("value", item.get("raw_value", item.get("normalized_value", item.get("number", ""))))
        value = raw_value if isinstance(raw_value, (str, int, float)) else _clean(raw_value, 120)
        out.append({
            "label": label,
            "value": value,
            "change": _clean(item.get("change") or item.get("delta") or item.get("comparison") or item.get("change_text"), 120),
            "context": _clean(item.get("context") or item.get("description") or item.get("note") or item.get("meaning"), 300),
            "period": _clean(item.get("period") or item.get("window") or item.get("date"), 100),
            "unit": _clean(item.get("unit"), 40),
            "status": _clean(item.get("status") or item.get("signal") or item.get("direction"), 80),
            "evidence": evidence,
        })
    return out


def _glossary(value: Any) -> list[dict[str, str]]:
    """Keep only short, material-grounded term explanations for the reader."""

    if isinstance(value, dict):
        value = [{"term": key, "meaning": item} for key, item in value.items()]
    if not isinstance(value, (list, tuple)):
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in value[:8]:
        if not isinstance(item, dict):
            continue
        term = _clean(item.get("term") or item.get("label") or item.get("name"), 60)
        meaning = _clean(item.get("meaning") or item.get("definition") or item.get("explanation") or item.get("description"), 240)
        key = re.sub(r"\s+", "", term).casefold()
        if not term or not meaning or key in seen:
            continue
        seen.add(key)
        out.append({"term": term, "meaning": meaning})
    return out


def _normalise_sections(value: Any, docs: list[SourceDocument]) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    out: list[dict[str, Any]] = []
    for index, item in enumerate(value[:_MAX_SECTION_COUNT], 1):
        if not isinstance(item, dict):
            continue
        title = _clean(item.get("title") or item.get("heading"), 180)
        summary = _clean(item.get("summary") or item.get("abstract"), 800)
        paragraphs = _list(item.get("paragraphs") or item.get("body"), _MAX_PARAGRAPHS_PER_SECTION, 1_600)
        bullets = _list(item.get("bullets") or item.get("points"), _MAX_BULLETS_PER_SECTION, 500)
        evidence = _evidence(item.get("evidence"), docs=docs)
        tables = _table(item.get("tables"))
        if not title:
            title = f"第{index}节"
        if not summary and not paragraphs and not bullets:
            continue
        out.append({"id": _clean(item.get("id"), 40) or f"s{index}", "title": title, "summary": summary, "paragraphs": paragraphs, "bullets": bullets, "evidence": evidence, "tables": tables})
    return out


def _normalise_watchlist(value: Any, docs: list[SourceDocument]) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    out: list[dict[str, Any]] = []
    for item in value[:_MAX_WATCHLIST]:
        if isinstance(item, str):
            item = {"item": item}
        if not isinstance(item, dict):
            continue
        row = {
            "title": _clean(item.get("title") or item.get("name"), 180),
            "item": _clean(item.get("item") or item.get("subject"), 180),
            "signal": _clean(item.get("signal") or item.get("direction"), 160),
            "window": _clean(item.get("window") or item.get("time"), 160),
            "metric": _clean(item.get("metric") or item.get("indicator"), 180),
            "trigger": _clean(item.get("trigger") or item.get("condition"), 500),
            "why": _clean(item.get("why") or item.get("rationale"), 500),
            "evidence": _evidence(item.get("evidence"), docs=docs),
        }
        if any(row[key] for key in ("title", "item", "signal", "window", "metric", "trigger", "why")):
            out.append(row)
    return out


def _normalise_risks(value: Any, docs: list[SourceDocument]) -> list[Union[dict[str, Any], str]]:
    if isinstance(value, (str, dict)):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    out: list[Union[dict[str, Any], str]] = []
    for item in value[:_MAX_RISKS]:
        if isinstance(item, str):
            text = _clean(item, 600)
            if text:
                out.append(text)
            continue
        if not isinstance(item, dict):
            continue
        title = _clean(item.get("title") or item.get("name"), 180)
        detail = _clean(item.get("detail") or item.get("description") or item.get("risk"), 800)
        evidence = _evidence(item.get("evidence"), docs=docs)
        if title or detail or evidence:
            out.append({"title": title, "detail": detail, "evidence": evidence})
    return out


def _normalise_sources(value: Any, docs: list[SourceDocument]) -> list[dict[str, Any]]:
    supplied: list[Any] = []
    if isinstance(value, dict):
        supplied = [value]
    elif isinstance(value, (list, tuple)):
        supplied = list(value)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    totals_by_id = {_doc.source_id: _read_page_limit(_doc) for _doc in docs if _doc.source_id}
    totals_by_title = {_doc.title: _read_page_limit(_doc) for _doc in docs if _doc.title}
    docs_by_title = {_doc.title: _doc for _doc in docs if _doc.title}
    known_ids = set(totals_by_id)
    default_total = _read_page_limit(docs[0]) if len(docs) == 1 else 0
    for item in supplied[: len(docs) + 12]:
        if isinstance(item, str):
            item = {"title": item}
        if not isinstance(item, dict):
            continue
        raw_sid = _clean(item.get("source_id"), 160) or None
        if raw_sid and raw_sid not in known_ids:
            continue
        sid = raw_sid
        candidate_title = _clean(item.get("title") or item.get("name"), 180)
        # If the model omitted source_id, recover it only from an exact title
        # match among the documents supplied to the server.
        matched_doc = docs_by_title.get(candidate_title)
        if not sid and matched_doc is not None:
            sid = matched_doc.source_id
        # Do not display a model-invented source row.  The actual input
        # documents are appended below, so dropping an unresolvable row is
        # preferable to presenting an unverifiable citation.
        if not sid and matched_doc is None:
            continue
        total_for_source = totals_by_id.get(sid or "", 0)
        if not total_for_source and not sid and len(docs) == 1:
            total_for_source = default_total
        if not total_for_source:
            total_for_source = totals_by_title.get(candidate_title, 0)
        raw_url = _clean(item.get("url"), 500) or None
        # Source links are navigable in the client.  Only retain an exact URL
        # that was itself supplied as an input document; this prevents a model
        # from inventing an arbitrary outbound link in an otherwise trusted
        # citation block.
        allowed_urls = {doc.url for doc in docs if doc.url}
        safe_url = raw_url if raw_url in allowed_urls else None
        row = {
            "title": candidate_title,
            "label": _clean(item.get("label"), 160),
            # Never expose a page that was not present in the supplied PDF.
            # Unknown source ids are left unnumbered rather than guessed.
            "page": _page(item.get("page"), total_for_source) if total_for_source else None,
            "pages": _pages(item.get("pages"), total_for_source),
            "url": safe_url,
            "excerpt": _clean(item.get("excerpt") or item.get("text"), _MAX_EXCERPT_CHARS),
            "kind": _clean(item.get("kind"), 40) or "pdf",
            "source_id": sid,
        }
        key = sid or row["title"] or row["url"] or ""
        if key and key not in seen:
            out.append(row)
            seen.add(key)
    # Always expose the actual inputs, even if the model forgot the source list.
    for doc in docs:
        if doc.source_id in seen:
            continue
        out.append({
            "title": _source_label(doc), "label": _source_label(doc), "page": None,
            "pages": str(doc.pages_read) if doc.pages_read else None, "url": doc.url,
            "excerpt": "", "kind": doc.kind, "source_id": doc.source_id,
        })
        seen.add(doc.source_id)
    return out


def _source_stats(docs: list[SourceDocument], sections: list[dict[str, Any]], tables: list[dict[str, Any]], watchlist: list[dict[str, Any]], sources: list[dict[str, Any]]) -> dict[str, int]:
    evidence_count = 0
    for section in sections:
        evidence_count += len(section.get("evidence") or [])
        for table in section.get("tables") or []:
            evidence_count += 1 if table.get("rows") else 0
    for item in watchlist:
        evidence_count += len(item.get("evidence") or [])
    evidence_count += sum(1 for source in sources if source.get("page") or source.get("excerpt"))
    pages = sum(doc.pages_read for doc in docs)
    chars = sum(doc.chars_read for doc in docs)
    total = sum(doc.total_pages for doc in docs)
    return {"pages_read": pages, "chars_read": chars, "cited_claims": int(evidence_count), "verified_claims": 0, "source_count": len(docs), "total_pages": total}


def _base_response(
    request: ResearchDeepDraftRequest,
    docs: list[SourceDocument],
    *,
    data: Optional[dict[str, Any]] = None,
    provider: str = "local-fallback",
    disclaimer: str = _DISCLAIMER,
    confidence: Optional[float] = None,
) -> ResearchDeepDraftResponse:
    """Normalise model JSON (or an empty object) into the public contract."""

    data = data if isinstance(data, dict) else {}
    sections = _normalise_sections(data.get("sections"), docs)
    tables = _table(data.get("tables"))
    # Section-level tables are useful to renderers but are also exposed at the
    # top level for compact clients.
    if not tables:
        tables = [table for section in sections for table in section.get("tables", [])][:_MAX_TABLES]
    metrics = _metrics(
        data.get("metrics") or data.get("metric_cards") or data.get("key_metrics"),
        docs,
    )
    glossary = _glossary(data.get("glossary") or data.get("terms") or data.get("definitions"))
    watchlist = _normalise_watchlist(data.get("watchlist"), docs)
    risks = _normalise_risks(data.get("risks"), docs)
    sources = _normalise_sources(data.get("sources"), docs)
    coverage = _source_stats(docs, sections, tables, watchlist, sources)
    given_coverage = data.get("source_coverage")
    if isinstance(given_coverage, dict):
        # Trust model counts only when they cannot exceed measured input; this
        # prevents an LLM from claiming it verified pages it never received.
        for key in ("pages_read", "chars_read", "source_count", "total_pages"):
            try:
                coverage[key] = min(coverage[key], max(0, int(given_coverage.get(key, coverage[key]))))
            except (TypeError, ValueError):
                pass
        # Model-reported verification counts are advisory.  Cap them to the
        # evidence we actually normalised from the response and enforce the
        # invariant verified_claims <= cited_claims.
        measured_cited = coverage["cited_claims"]
        try:
            coverage["cited_claims"] = min(
                measured_cited, max(0, int(given_coverage.get("cited_claims", measured_cited)))
            )
        except (TypeError, ValueError):
            coverage["cited_claims"] = measured_cited
        try:
            coverage["verified_claims"] = min(
                coverage["cited_claims"],
                measured_cited,
                max(0, int(given_coverage.get("verified_claims", 0))),
            )
        except (TypeError, ValueError):
            coverage["verified_claims"] = 0
    else:
        coverage["verified_claims"] = min(coverage["verified_claims"], coverage["cited_claims"])
    coverage["verified_claims"] = min(coverage["verified_claims"], coverage["cited_claims"])
    source_count = len(docs)
    confidence_value = _clamp(data.get("confidence"), 0.2 if docs else 0.05) if confidence is None else _clamp(confidence, 0.2 if docs else 0.05)
    if not docs:
        confidence_value = min(confidence_value, 0.1)
    elif not coverage["cited_claims"]:
        confidence_value = min(confidence_value, 0.45)
    title = _clean(data.get("title"), 240) or _clean(request.title, 240) or "研报深度解读"
    subject = _clean(data.get("subject"), 240) or _clean(request.symbol, 120)
    one_liner = _clean(data.get("one_liner"), 500)
    plain_language = _clean(
        data.get("plain_language_summary") or data.get("reader_summary")
        or data.get("simple_summary") or data.get("beginner_summary"),
        1_200,
    )
    executive = _clean(data.get("executive_summary"), 2_500)
    core = _clean(data.get("core_conclusion"), 2_500)
    thesis = _clean(data.get("thesis"), 3_000)
    decision_implication = _clean(
        data.get("decision_implication") or data.get("research_implication") or data.get("so_what"),
        1_200,
    )
    # Keep aliases useful when a compact/old model response is supplied.
    summary = _clean(data.get("summary"), 2_500)
    if not executive:
        executive = summary
    if not executive and sections:
        first_section = sections[0]
        executive = _clean(first_section.get("summary"), 2_500)
        if not executive:
            paragraphs = first_section.get("paragraphs") or []
            executive = _clean(paragraphs[0] if paragraphs else "", 2_500)
    if not core:
        core = thesis or one_liner
    if not thesis:
        thesis = core or executive or one_liner
    if not one_liner:
        one_liner = core or executive or _MISSING
    if not executive:
        executive = _MISSING
    if not core:
        core = _MISSING
    if not thesis:
        thesis = _MISSING
    if not sections:
        sections = [{
            "id": "s1", "title": "原文概览", "summary": executive,
            "paragraphs": [executive] if executive and executive != _MISSING else [_MISSING],
            "bullets": [], "evidence": [], "tables": [],
        }]
    # Reading time describes the generated article, not the raw PDF.  A
    # 32-page source may contain tens of thousands of characters while the
    # finished editorial draft is only a few minutes long; using source size
    # here made the UI claim “60 分钟阅读” for an ordinary report.
    article_chars = len(plain_language) + len(decision_implication)
    article_chars += len(executive) + len(core) + len(thesis) + len(one_liner)
    article_chars += sum(
        len(str(section.get("summary") or ""))
        + sum(len(str(item)) for item in (section.get("paragraphs") or []))
        + sum(len(str(item)) for item in (section.get("bullets") or []))
        for section in sections
    )
    read_time = max(1, min(30, round(article_chars / 500))) if article_chars else max(1, min(10, len(sections)))
    instruments = _list(data.get("instruments"), _MAX_INSTRUMENTS, 80)
    if request.symbol and request.symbol.strip() and request.symbol.strip() not in instruments:
        instruments.insert(0, request.symbol.strip()[:80])
    response = ResearchDeepDraftResponse(
        title=title,
        subtitle=_clean(data.get("subtitle"), 500),
        subject=subject,
        symbol=request.symbol,
        generated_at=datetime.now(timezone.utc),
        read_time_minutes=read_time,
        source_count=source_count,
        confidence=confidence_value,
        one_liner=one_liner,
        plain_language_summary=plain_language,
        executive_summary=executive,
        core_conclusion=core,
        thesis=thesis,
        sections=[ResearchDeepDraftSection.model_validate(section) for section in sections],
        metrics=[ResearchDeepDraftMetric.model_validate(metric) for metric in metrics],
        decision_implication=decision_implication,
        glossary=glossary,
        tables=[ResearchDeepDraftTable.model_validate(table) for table in tables],
        watchlist=[ResearchDeepDraftWatchItem.model_validate(item) for item in watchlist],
        risks=[ResearchDeepDraftRisk.model_validate(item) if isinstance(item, dict) else item for item in risks],
        instruments=instruments,
        sources=[ResearchDeepDraftSource.model_validate(source) for source in sources],
        disclaimer=_safe_disclaimer(disclaimer, fallback=_DISCLAIMER),
        mode="deep_draft",
        pages_analyzed=coverage["pages_read"],
        provider=provider,
        source_coverage=ResearchDeepDraftCoverage.model_validate(coverage),
    )
    # Prompt constraints are soft; run the same hard neutralisation used by
    # the compact research endpoint before exposing any model prose.  The
    # draft-specific walker protects short source quotations and provenance
    # fields from being rewritten while still covering unknown future prose.
    try:
        response = ResearchDeepDraftResponse.model_validate(
            neutralize_deep_draft(response.model_dump())
        )
    except Exception:
        pass
    return response


def _fallback_sections(docs: list[SourceDocument]) -> tuple[list[dict[str, Any]], str]:
    """Make a *short* source preview without echoing a third-party report.

    This path is used when the cloud model is unavailable.  Returning whole
    PDF paragraphs here would turn a graceful degradation into an accidental
    redistribution channel, so each page contributes at most a short snippet
    and the total preview remains deliberately small.
    """

    sections: list[dict[str, Any]] = []
    first_text = ""
    for doc in docs:
        lines: list[str] = []
        for page, text in doc.page_texts:
            if not first_text:
                first_text = text.strip().splitlines()[0][:500] if text.strip() else ""
            # Paragraphise conservatively; preserve only a short source hint
            # rather than inventing a summary or echoing the whole report.
            for block in re.split(r"\n\s*\n+", text):
                block = re.sub(r"\s+", " ", block).strip()
                if len(block) >= 8:
                    snippet = block[:_MAX_EXCERPT_CHARS]
                    if len(block) > _MAX_EXCERPT_CHARS:
                        snippet = snippet.rstrip("，,；;。 ") + "…"
                    lines.append((page, snippet))
                    # One short excerpt per page is enough for a fallback
                    # preview and keeps the third-party text exposure bounded.
                    break
        if not lines:
            continue
        # Group a few page hints per source.  A real heading detector would risk
        # re-labelling arbitrary body text, so the title remains explicit.
        for start in range(0, len(lines), 4):
            batch = lines[start : start + 4]
            paragraphs = [text for _, text in batch]
            evidence = [{"page": page, "excerpt": text[:_MAX_EXCERPT_CHARS], "label": _source_label(doc), "source_id": doc.source_id, "kind": "pdf"} for page, text in batch[:3]]
            sections.append({
                "id": f"s{len(sections) + 1}",
                "title": f"{_source_label(doc)} · 页面线索 {len(sections) + 1}",
                "summary": paragraphs[0][:500],
                "paragraphs": paragraphs,
                "bullets": [],
                "evidence": evidence,
                "tables": [],
            })
        if len(sections) >= min(_MAX_SECTION_COUNT, 6):
            break
    if not sections:
        sections = [{"id": "s1", "title": "原文概览", "summary": _MISSING, "paragraphs": [_MISSING], "bullets": [], "evidence": [], "tables": []}]
    return sections[:_MAX_SECTION_COUNT], first_text


def _fallback_from_compact(
    compact: dict[str, Any],
    request: ResearchDeepDraftRequest,
    docs: list[SourceDocument],
    *,
    disclaimer: str = _FALLBACK_DISCLAIMER,
) -> ResearchDeepDraftResponse:
    """Turn an existing compact vision result into a safe deep-draft shape."""

    lines = compact.get("logic_lines") if isinstance(compact, dict) else []
    sections: list[dict[str, Any]] = []
    if isinstance(lines, list):
        for index, line in enumerate(lines[:_MAX_SECTION_COUNT], 1):
            if not isinstance(line, dict):
                continue
            body = "；".join(_clean(line.get(key), 500) for key in ("evidence", "chain", "impact", "watch") if _clean(line.get(key)))
            if body:
                sections.append({"id": f"s{index}", "title": _clean(line.get("title"), 180) or f"逻辑线 {index}", "summary": body[:700], "paragraphs": [], "bullets": [], "evidence": [], "tables": []})
    if not sections:
        summary = _clean(compact.get("summary") or compact.get("one_liner"), 1_200) or _MISSING
        sections = [{"id": "s1", "title": "模型读取摘要", "summary": summary, "paragraphs": [], "bullets": _list(compact.get("bullish") or compact.get("key_points"), 6), "evidence": [], "tables": []}]
    data = {
        "subtitle": "基于研报页面的结构化深度整理",
        "subject": compact.get("subject") or request.symbol or "",
        "one_liner": compact.get("one_liner") or compact.get("summary") or _MISSING,
        "executive_summary": compact.get("summary") or _MISSING,
        "core_conclusion": compact.get("core_logic") or _MISSING,
        "thesis": compact.get("core_logic") or compact.get("summary") or _MISSING,
        "sections": sections,
        "watchlist": [],
        "risks": _list(compact.get("bearish") or compact.get("risks"), 8),
        "instruments": compact.get("instruments") or [],
        "confidence": min(_clamp(compact.get("confidence"), 0.25), 0.55),
    }
    response = _base_response(request, docs, data=data, provider="vision-compact-fallback", disclaimer=disclaimer)
    # ``page_texts`` is empty for image-only PDFs, but the compact visual
    # analyzer reports how many rendered pages it actually saw.  Preserve that
    # coverage instead of returning the misleading default zero.
    try:
        visual_pages = max(0, int(compact.get("pages_analyzed") or 0))
    except (TypeError, ValueError):
        visual_pages = 0
    if visual_pages:
        max_known = sum(max(0, int(doc.total_pages or 0)) for doc in docs)
        response.pages_analyzed = min(visual_pages, max_known) if max_known else visual_pages
        response.source_coverage.pages_read = response.pages_analyzed
    response.source_coverage.verified_claims = 0
    return response


async def generate_deep_draft(
    request: ResearchDeepDraftRequest,
    *,
    documents: Optional[list[SourceDocument]] = None,
    source_error: str = "",
) -> ResearchDeepDraftResponse:
    """Generate a deep draft, falling back to deterministic source extraction."""

    docs = list(documents or [])
    if not docs and not source_error:
        try:
            docs = await resolve_source_documents(request)
        except HTTPException:
            raise
        except Exception as exc:
            source_error = str(exc)[:200]

    # Text path: preserve page markers and let one synthesis call build the
    # article.  This is deliberately separate from the compact 7k-char prompt.
    text_docs = [doc for doc in docs if doc.text]
    if sum(len(doc.text or "") for doc in text_docs) < _DEEP_TEXT_FLOOR_CHARS:
        text_docs = []
    if text_docs:
        prompt = _prompt_for_documents(request, docs)
        try:
            llm = CloudResearchLLM()
            if llm.provider != "mock":
                data = await asyncio.wait_for(
                    llm.complete_json(
                        prompt,
                        max_tokens=7_200,
                        timeout_seconds=_DEEP_LLM_CALL_TIMEOUT_SECONDS,
                        retry_schema_hint='必须包含 sections, sources, source_coverage；没有证据写“原文未提供”。',
                    ),
                    timeout=_DEEP_LLM_TOTAL_TIMEOUT_SECONDS,
                )
                if isinstance(data, dict) and data:
                    provider = _clean(getattr(llm, "model", "cloud"), 120) or "cloud"
                    return _base_response(request, docs, data=data, provider=provider, disclaimer=_DISCLAIMER)
        except Exception as exc:
            # A model outage should never erase the readable source extraction.
            print(f"[deep-draft] 文本路径 LLM 失败：{type(exc).__name__}: {str(exc)[:120]}")

    # Image-only PDFs (no extractable text layer) skip the text path above.
    # The renderer caps the selected window, so long scans can still be read
    # through the page-parallel path instead of being rejected solely because
    # their total page count is larger than the vision budget.
    if not text_docs:
        total_pages = sum(max(0, int(doc.total_pages or 0)) for doc in docs)
        vision_bytes = next((doc.pdf_bytes for doc in docs if doc.pdf_bytes), b"")
        if vision_bytes and total_pages > 0:
            try:
                compact = await asyncio.wait_for(
                    analyze_pdf_adaptive(
                        vision_bytes,
                        title=request.title,
                        symbol=request.symbol,
                        max_pages=min(
                            _DEEP_VISION_MAX_PAGES,
                            max(1, int(request.max_pages or _DEEP_VISION_MAX_PAGES)),
                        ),
                    ),
                    timeout=_DEEP_LLM_TOTAL_TIMEOUT_SECONDS,
                )
                if isinstance(compact, dict) and compact:
                    return _fallback_from_compact(compact, request, docs, disclaimer=_DISCLAIMER)
            except Exception:
                # Vision is best-effort; the deterministic skeleton below stays
                # honest about what could and could not be read.
                pass

    # Deterministic fallback.  It is intentionally honest about missing
    # semantics: excerpts are labelled source text, while unsupported thesis,
    # watch triggers and risks remain explicit placeholders.
    sections, first_text = _fallback_sections(docs)
    evidence = sections[0].get("evidence", []) if sections else []
    summary = first_text[:700] if first_text else _MISSING
    data = {
        "subtitle": "基于原文页面的结构化整理（待模型补充编辑）",
        "subject": request.symbol or "",
        "one_liner": summary if summary != _MISSING else _MISSING,
        "executive_summary": summary,
        "core_conclusion": _MISSING,
        "thesis": _MISSING,
        "sections": sections,
        "watchlist": [],
        "risks": [],
        "instruments": [request.symbol] if request.symbol else [],
        "sources": [{"title": _source_label(doc), "label": _source_label(doc), "source_id": doc.source_id, "kind": doc.kind, "pages": str(doc.pages_read) if doc.pages_read else None} for doc in docs],
        "confidence": 0.12 if docs else 0.03,
    }
    response = _base_response(request, docs, data=data, provider="local-fallback", disclaimer=_FALLBACK_DISCLAIMER, confidence=0.12 if docs else 0.03)
    # Make the absence of model semantics visible in the article itself.
    if source_error and response.subtitle:
        response.subtitle = f"{response.subtitle}；{_MISSING}（{source_error[:100]}）"
    response.source_coverage.verified_claims = 0
    return response


# Friendly aliases used by tests and callers that prefer a ``build_*`` name.
build_deep_draft = generate_deep_draft
normalise_deep_draft = _base_response
normalize_deep_draft = _base_response
# Both spellings are kept for callers following the surrounding codebase
# (``research_vision`` uses American ``normalize`` in a few integrations).
_normalize_sources = _normalise_sources
_normalize_sections = _normalise_sections


@router.post("/api/research/deep-draft", response_model=ResearchDeepDraftResponse)
async def api_research_deep_draft(request: ResearchDeepDraftRequest) -> ResearchDeepDraftResponse:
    """Standalone route; main.py may add its normal quota wrapper when mounted."""

    return await generate_deep_draft(request)


__all__ = [
    "SourceDocument",
    "api_research_deep_draft",
    "build_deep_draft",
    "generate_deep_draft",
    "normalise_deep_draft",
    "normalize_deep_draft",
    "neutralize_deep_draft",
    "resolve_source_documents",
    "router",
]
