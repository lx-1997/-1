"""Text-only reader for deep articles.

Some upstream deep articles arrive as a short lead plus a long screenshot. The
reader retrieves that file on the server, transcribes it, and returns paragraphs
only. The browser never receives the source PDF or image.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import io
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException, status

from .async_singleflight import AsyncSingleFlight
from .file_tools import MAX_UPLOAD_BYTES, extract_file_bytes
from .llm import CloudResearchLLM
from .report_url_ingest import (
    REPORT_URL_HEADERS,
    REPORT_URL_TIMEOUT,
    _looks_like_html,
    _validate_report_url,
)
from .schemas import RealtimeMessageRecord


_URL_RE = re.compile(r"https?://[^\s<>]+", re.I)
_URL_TRAILING_CHARS = "，。；;）)\"'"
_SOURCE_RE = re.compile(r"^\s*(?:来源|原文|source)\s*[:：].*$", re.I)
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif"})
_PDF_SUFFIXES = frozenset({".pdf"})
# Long-form article captures can be much taller than a normal screenshot. Ten
# tiles silently dropped the lower half of some stories; keep a generous cap
# while bounding the maximum work a single request can trigger.
_MAX_OCR_TILES = 48
_OCR_TILE_HEIGHT = 1800
_OCR_MAX_WIDTH = 1800
_OCR_CONCURRENCY = 4
_CACHE_SECONDS = 30 * 86400
_FALLBACK_CACHE_SECONDS = 15 * 60
_MAX_CACHE_ENTRIES = 512
_ARTICLE_TEXT_FLIGHT: AsyncSingleFlight["ArticleOriginalText"] = AsyncSingleFlight()
# Deliberately process-local: the generic data store has public history
# endpoints, so member-only original text must never be persisted there.
_ARTICLE_TEXT_CACHE: dict[str, tuple[float, "ArticleOriginalText"]] = {}
_HTML_BLOCK_MARKUP_RE = re.compile(
    r"<\s*/?\s*(?:br|p|div|li|h[1-6]|blockquote|article|section|header|footer)\b[^>]*>",
    re.I,
)
_HTML_TAG_RE = re.compile(r"<\s*/?\s*[a-z][^>]*>", re.I)


@dataclass
class _HTMLNode:
    tag: str
    attrs: dict[str, str]
    children: list[object]


class _ArticleHTMLParser(HTMLParser):
    """Small dependency-free DOM used only for article-body extraction.

    The generic report URL parser intentionally turns an entire HTML document
    into text. That is unsafe for an article reader because page chrome and
    related stories live in the same document. Keeping a tiny DOM here lets us
    select the semantic article container before extracting block text.
    """

    _VOID_TAGS = frozenset({
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr",
    })

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _HTMLNode("document", {}, [])
        self._stack: list[_HTMLNode] = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        normalized_attrs = {str(key).lower(): str(value or "") for key, value in attrs}
        node = _HTMLNode(tag.lower(), normalized_attrs, [])
        self._stack[-1].children.append(node)
        if node.tag not in self._VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        self.handle_starttag(tag, attrs)
        if self._stack[-1].tag == tag.lower():
            self._stack.pop()

    def handle_endtag(self, tag: str) -> None:
        wanted = tag.lower()
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == wanted:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if data:
            self._stack[-1].children.append(data)


_HTML_SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "svg", "canvas", "iframe"})
_HTML_BLOCK_TAGS = frozenset({"h1", "h2", "h3", "h4", "p", "li", "blockquote", "pre"})
_HTML_CONTAINER_TAGS = frozenset({"body", "main", "article", "section", "div"})
_ARTICLE_BODY_MARKER_RE = re.compile(
    r"(?:article[-_]?body|article[-_]?content|article__body|article__content|"
    r"story[-_]?body|story[-_]?content|post[-_]?content|entry[-_]?content|"
    r"articlebody|body[-_]?content|content[-_]?body)",
    re.I,
)
_ARTICLE_GENERIC_MARKER_RE = re.compile(r"(?:article|story|post|entry|content|body)", re.I)
_ARTICLE_CHROME_MARKER_RE = re.compile(
    r"(?:^|[-_ ])(?:nav|navigation|menu|header|footer|aside|related|recommend|"
    r"read[-_ ]?next|latest|newsletter|cookie|comment|social|share|subscribe|"
    r"login|sign[-_ ]?in|breadcrumb|topic|topics|author)(?:$|[-_ ])",
    re.I,
)
_ARTICLE_STOP_LINE_RE = re.compile(
    r"^(?:suggested\s+topics?\b|read\s+next\b|latest\b|more\s+from\b|"
    r"our\s+standards\b|purchase\s+licen[cs]ing\s+rights\b|lseg\s+products\b|"
    r"stay\s+informed\b|follow\s+us\b|topic\s+sitemap\b|article\s+sitemap\b|"
    r"manage\s+cookies\b|terms?\s*(?:and|&)\s*conditions\b|privacy\b|"
    r"©\s*\d{4})",
    re.I,
)
_ARTICLE_NEWSLETTER_LINE_RE = re.compile(
    r"^(?:jumpstarts?\s+your\s+morning\b|learns?\s+about\s+.*\bnewsletter\b|"
    r"通过《.*》的订阅服务\b|subscribes?\s+to\b)",
    re.I,
)
_ARTICLE_BYLINE_RE = re.compile(
    r"^(?:by\b|作者\s*[:：]|撰文\s*[:：]|记者\s*[:：]|根据.{0,18}(?:报道|消息))",
    re.I,
)
# The reader's collapsed-original toggle label is UI copy, not article text.
# Copies taken from the rendered reader (or an OCR of it) carry the label back
# into the paragraph stream, where it pairs with adjacent Chinese prose.
_ARTICLE_TOGGLE_RE = re.compile(r"^(?:查看英文原文|view\s+english\s+original)\s*·\s*english\s+original$", re.I)
# Bloomberg full-page footer menu: a long run of bilingual section labels.
# Each line alone looks like a short heading, so drop them by name and cut the
# stored window when a run of them appears.
_ARTICLE_NAV_LABELS = {
    "home 主页", "btv+", "market data 市场数据", "opinion 意见/看法", "audio 音频",
    "originals 原创作品/独家作品", "magazine 杂志/期刊", "events 活动/事件", "news 新闻",
    "markets 市场/行情", "economics 经济学", "technology 技术", "politics 政治",
    "green 绿色", "crypto 加密货币", "ai 人工智能", "work & life 工作与生活",
    "wealth 财富", "pursuits 追求/努力实现的目标", "citylab", "sports 体育运动",
    "equality 平等", "management & work 管理与工作", "stocks 股票/证券",
    "commodities 大宗商品", "rates & bonds 利率与债券", "currencies 货币/现金",
    "futures 期货", "sectors 行业/领域", "economic calendar 经济日历",
}
_ARTICLE_DATELINE_RE = re.compile(
    r"^(?:[A-Z][A-Za-z .'-]{2,48},\s+)?(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|"
    r"Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|"
    r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2}\b.*\((?:Reuters|Bloomberg)\)\s*-",
    re.I,
)
_ARTICLE_CHROME_LINES = frozenset({
    "exclusive news, data and analytics for financial market professionals",
    "lseg", "reuters", "bloomberg", "my news", "sign in", "subscribe", "browse", "home",
    "authors", "archive", "legal", "business", "world", "markets", "technology",
    "download the app (ios)", "download the app (android)", "newsletters",
    "advertise with us", "careers", "reuters news agency", "advertising guidelines",
})
_ARTICLE_PAGE_CHROME_LINES = frozenset({
    "daocaijing", "稻财经", "deepfocus ai", "收起", "主菜单", "我的股票", "深度文章",
    "机构纪要", "投行研报", "财报", "财经资讯", "文章", "快讯", "研报", "公告",
    "行情", "markets 市场", "每日复盘", "搜索深度文章", "★ 头条", "ai 解读", "原文", "全文", "分享",
    "资讯同步", "浅色模式", "登录", "注册", "更多", "来源 · daoc财经", "来源 · dao财经",
    "subscribe 订阅", "save 保存 translate 翻译结果", "takeaways 外卖食品",
})
_ARTICLE_INLINE_CHROME_RE = re.compile(
    r"^(?:translate\s+翻译结果|takeaways\s+外卖食品|\d{1,2}:\d{2}\s*/\s*\d{1,2}:\d{2})$",
    re.I,
)
_ARTICLE_PAGE_CHROME_RE = re.compile(
    r"^(?:≡\s*)?bloomberg$|^the company\s*&\s*its products\b|"
    r"^bloomberg terminal demo request\b|^bloomberg anywhere login\b|^customer support$|"
    r"^subscribe\b|^\[?免费翻译服务\]?|^建议升级为\s*pro\s*会员|^错误原因\s*[:：]|"
    r"^免费试用\s*pro\s*会员|^切换到.*翻译.*重试$|^.*(?:save\s+保存|translate\s+翻译结果).*$|"
    r"^.*\d{1,2}:\d{2}\s*/\s*\d{1,2}:\d{2}.*$|^\*?photographer\s*[:：]|^摄影师\s*[:：]|"
    r"^(?:↗\s*)?current market cap$|^[a-z][a-z ]{0,30}['’]s\s+market cap shrinks$|"
    r"^[\$]?\s*\d+(?:\.\d+)?\s*[bm]?$|^[\d\s$,.]+$",
    re.I,
)


def _iter_html_nodes(node: _HTMLNode):
    for child in node.children:
        if isinstance(child, _HTMLNode):
            yield child
            yield from _iter_html_nodes(child)


def _html_node_text(node: _HTMLNode, *, include_skipped: bool = False) -> str:
    if node.tag in _HTML_SKIP_TAGS and not include_skipped:
        return ""
    parts: list[str] = []
    for child in node.children:
        if isinstance(child, _HTMLNode):
            value = _html_node_text(child, include_skipped=include_skipped)
        else:
            value = str(child)
        if value and value.strip():
            parts.append(value.strip())
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def _html_has_block_descendant(node: _HTMLNode) -> bool:
    return any(
        isinstance(child, _HTMLNode)
        and (child.tag in _HTML_BLOCK_TAGS or _html_has_block_descendant(child))
        for child in node.children
    )


def _collect_html_blocks(node: _HTMLNode) -> list[tuple[str, str]]:
    """Collect paragraph-like blocks without duplicating nested div text."""
    if node.tag in _HTML_SKIP_TAGS:
        return []
    if node.tag in _HTML_BLOCK_TAGS:
        value = _html_node_text(node)
        return [(node.tag, value)] if value else []

    blocks: list[tuple[str, str]] = []
    for child in node.children:
        if isinstance(child, _HTMLNode):
            blocks.extend(_collect_html_blocks(child))
    # Some sites render article copy as div/span text without <p>. Only use a
    # container's own text when it contains no paragraph-like descendants.
    if not blocks and node.tag in _HTML_CONTAINER_TAGS and not _html_has_block_descendant(node):
        value = _html_node_text(node)
        if len(value) >= 24:
            blocks.append((node.tag, value))
    return blocks


def _html_marker_text(node: _HTMLNode) -> str:
    values = [node.tag]
    for key in ("id", "class", "role", "aria-label", "data-testid"):
        if node.attrs.get(key):
            values.append(node.attrs[key])
    return " ".join(values).lower()


def _article_container_score(node: _HTMLNode, blocks: list[tuple[str, str]]) -> int:
    marker = _html_marker_text(node)
    score = 0
    if node.tag == "article":
        score += 100
    elif node.tag == "main":
        score += 38
    elif node.attrs.get("role", "").lower() == "main":
        score += 42
    if _ARTICLE_BODY_MARKER_RE.search(marker):
        score += 92
    elif _ARTICLE_GENERIC_MARKER_RE.search(marker):
        score += 25
    if _ARTICLE_CHROME_MARKER_RE.search(marker):
        score -= 90
    text_len = sum(len(text) for _, text in blocks)
    return score + min(30, text_len // 250)


def _jsonld_article_body(root: _HTMLNode) -> str:
    def visit(value: object):
        if isinstance(value, dict):
            body = value.get("articleBody")
            item_type = value.get("@type")
            types = item_type if isinstance(item_type, list) else [item_type]
            if isinstance(body, str) and len(body.strip()) >= 24 and (
                not item_type or any("article" in str(item).lower() for item in types)
            ):
                yield body
            for child in value.values():
                yield from visit(child)
        elif isinstance(value, list):
            for child in value:
                yield from visit(child)

    for node in _iter_html_nodes(root):
        if node.tag != "script" or "ld+json" not in node.attrs.get("type", "").lower():
            continue
        raw = _html_node_text(node, include_skipped=True)
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        for body in visit(payload):
            return body
    return ""


def _clean_article_html_blocks(blocks: list[tuple[str, str]]) -> list[str]:
    # If a full-page fallback contains an h1, discard everything before it:
    # that region is normally the publisher header/navigation.
    first_h1 = next((index for index, (tag, _) in enumerate(blocks) if tag == "h1"), None)
    if first_h1 is not None:
        blocks = blocks[first_h1:]

    cleaned: list[str] = []
    for tag, raw in blocks:
        value = re.sub(r"\s+", " ", raw or "").strip()
        if not value:
            continue
        if _ARTICLE_STOP_LINE_RE.match(value):
            break
        if tag == "h1":
            # The reader already renders the localized title in its header.
            continue
        if _is_page_chrome_line(value):
            continue
        if cleaned and value == cleaned[-1]:
            continue
        cleaned.append(value)
    return cleaned


def _html_to_article_text(raw: bytes) -> tuple[str, bool]:
    """Extract article copy from a web page, not the publisher's whole shell."""
    parser = _ArticleHTMLParser()
    try:
        parser.feed(_decode_article_html(raw))
        parser.close()
    except Exception:
        # Malformed HTML should still get the safe generic fallback below.
        parser = _ArticleHTMLParser()

    candidates: list[tuple[int, list[tuple[str, str]]]] = []
    for node in (parser.root, *_iter_html_nodes(parser.root)):
        if node.tag not in {"article", "main", "section", "div"} and node.attrs.get("role", "").lower() != "main":
            continue
        blocks = _collect_html_blocks(node)
        if sum(len(text) for _, text in blocks) >= 120:
            candidates.append((_article_container_score(node, blocks), blocks))

    blocks: list[tuple[str, str]] = []
    if candidates:
        _, blocks = max(candidates, key=lambda item: item[0])
    cleaned = _clean_article_html_blocks(blocks)

    # JSON-LD is a useful second path for sites whose visible body is rendered
    # client-side or whose class names are obfuscated.
    if sum(len(item) for item in cleaned) < 180:
        json_body = _jsonld_article_body(parser.root)
        if json_body:
            cleaned = _clean_article_html_blocks(
                [("p", paragraph) for paragraph in _stored_paragraphs(json_body)]
            )

    # Last resort: collect block tags from the document, then apply explicit
    # publisher-shell cutoffs. This is still safer than stripping every HTML
    # tag in the document and returning all navigation/footer text.
    if not cleaned:
        cleaned = _clean_article_html_blocks(_collect_html_blocks(parser.root))

    text = "\n".join(cleaned).strip()
    return text[:80_000], len(text) > 80_000


def _decode_article_html(raw: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


@dataclass(frozen=True)
class ArticleOriginalText:
    paragraphs: list[str]
    parser: str
    truncated: bool = False

    @property
    def content(self) -> str:
        return "\n\n".join(self.paragraphs)


@dataclass(frozen=True)
class _DownloadedSource:
    raw: bytes
    filename: str
    content_type: str


def _file_like_url(value: str) -> bool:
    parsed = urlparse(value or "")
    path = parsed.path.lower()
    return (
        Path(path).suffix in (_IMAGE_SUFFIXES | _PDF_SUFFIXES)
        or "/uploads/" in path
        or "/upload/" in path
        or "/images/" in path
    )


def _media_urls_from_text(value: str) -> list[str]:
    """Extract image/PDF URLs from a raw DAO event without exposing raw HTML."""
    urls: list[str] = []
    for raw_url in _URL_RE.findall(value or ""):
        candidate = raw_url.rstrip(_URL_TRAILING_CHARS)
        if candidate.startswith(("http://", "https://")) and _file_like_url(candidate) and candidate not in urls:
            urls.append(candidate)
    return urls


def _article_media_db_paths() -> list[Path]:
    configured = [
        os.getenv("DEEPFOCUS_ARTICLE_MEDIA_DB_PATH", "").strip(),
        os.getenv("DEEPFOCUS_DAO_DB_PATH", "").strip(),
        "/DAO财经/.dao_events.sqlite3",
    ]
    paths: list[Path] = []
    for raw_path in configured:
        if not raw_path:
            continue
        path = Path(raw_path)
        if path not in paths and path.is_file():
            paths.append(path)
    return paths


def _local_article_media_urls(message: RealtimeMessageRecord) -> list[str]:
    """Recover source screenshots kept by the local DAO collector.

    The public realtime bridge deliberately stores a text lead and the source
    webpage URL. The collector's private event DB still has the article image
    URL (and, for older rows, the raw HTML in ``raw_json``). Prefer that local
    media because it is the full article capture that can be OCR'd reliably.
    """
    source_id = str(message.source_id or "").strip()
    title = (message.title or "").strip()
    if not source_id and not title:
        return []
    for db_path in _article_media_db_paths():
        rows: list[sqlite3.Row] = []
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
            conn.row_factory = sqlite3.Row
            try:
                if source_id:
                    rows = conn.execute(
                        "SELECT url, raw_json, title FROM events "
                        "WHERE type = 'article' AND source_id = ? ORDER BY id DESC LIMIT 4",
                        (source_id,),
                    ).fetchall()
                if not rows and title:
                    rows = conn.execute(
                        "SELECT url, raw_json, title FROM events "
                        "WHERE type = 'article' AND title = ? ORDER BY id DESC LIMIT 4",
                        (title,),
                    ).fetchall()
                if not rows and title:
                    # Titles can differ only by a source prefix or punctuation
                    # after the bridge's pre-read step. Scan a bounded recent
                    # window rather than making the article endpoint expensive.
                    wanted = _page_key(title)
                    candidates = conn.execute(
                        "SELECT url, raw_json, title FROM events "
                        "WHERE type = 'article' ORDER BY id DESC LIMIT 5000"
                    ).fetchall()
                    rows = [row for row in candidates if wanted and _page_key(str(row["title"] or "")) == wanted][:4]
            finally:
                conn.close()
        except (OSError, sqlite3.Error):
            continue

        urls: list[str] = []
        for row in rows:
            direct = str(row["url"] or "").strip()
            if _file_like_url(direct) and direct not in urls:
                urls.append(direct)
            raw_json = str(row["raw_json"] or "")
            if raw_json:
                try:
                    payload = json.loads(raw_json)
                    raw_content = payload.get("content", "") if isinstance(payload, dict) else ""
                    for candidate in _media_urls_from_text(str(raw_content or "")):
                        if candidate not in urls:
                            urls.append(candidate)
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
        if urls:
            return urls
    return []


def _source_urls(message: RealtimeMessageRecord) -> list[str]:
    """Return source candidates in reliability order: capture, then webpage."""
    structured = (message.url or "").strip()
    candidates: list[str] = []
    if structured.startswith(("http://", "https://")) and _file_like_url(structured):
        candidates.append(structured)
    candidates.extend(_local_article_media_urls(message))
    if structured.startswith(("http://", "https://")):
        candidates.append(structured)
    candidates.extend(
        raw_url.rstrip(_URL_TRAILING_CHARS)
        for raw_url in _URL_RE.findall(message.content or "")
    )
    return list(dict.fromkeys(url for url in candidates if url.startswith(("http://", "https://"))))


def _source_url(message: RealtimeMessageRecord) -> str:
    """Return the best source candidate; kept for diagnostics and compatibility."""
    urls = _source_urls(message)
    return urls[0] if urls else ""


def _page_key(value: str) -> str:
    return re.sub(r"[\s\W_]+", "", str(value or "").casefold())


def _is_page_chrome_line(value: str) -> bool:
    key = re.sub(r"\s+", " ", value or "").strip().casefold()
    key = re.sub(r"^#{1,6}\s+", "", key)
    key = re.sub(r"^\*{2,3}\s*|\s*\*{2,3}$", "", key)
    return (
        key in _ARTICLE_CHROME_LINES
        or key in _ARTICLE_PAGE_CHROME_LINES
        or key.startswith("daocaijing ")
        or _ARTICLE_INLINE_CHROME_RE.fullmatch(key) is not None
        or _ARTICLE_PAGE_CHROME_RE.match(key) is not None
        or re.fullmatch(r"(?:subscribe\s+订阅|save\s+保存(?:\s+translate\s+翻译结果)?)", key, re.I) is not None
    )


def _is_title_line(value: str, title: str) -> bool:
    title_key = _page_key(title)
    line_key = _page_key(value)
    if not title_key or len(title_key) < 8 or not line_key:
        return False
    return line_key == title_key or (
        len(title_key) > 16 and (title_key in line_key or line_key in title_key)
    )


def _is_toggle_line(value: str) -> bool:
    return _ARTICLE_TOGGLE_RE.match(re.sub(r"\s+", " ", (value or "").strip())) is not None


def _is_nav_label_line(value: str) -> bool:
    key = re.sub(r"\s+", " ", (value or "").strip()).lower()
    return key in _ARTICLE_NAV_LABELS


def _looks_like_prose(value: str) -> bool:
    text = (value or "").strip()
    return len(text) >= 40 or bool(re.search(r"[。！？!?]$", text))


# Timestamp lines (「September 10, 2026 at 5:54 PM GMT+8」) are header metadata,
# never recommendation-card headlines even though they are short.
_ARTICLE_TIMESTAMP_RE = re.compile(
    r"^(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+\d{4}\b",
    re.I,
)


def _is_related_card_headline(lines: list[str], index: int) -> bool:
    """Detect a recommendation-card headline in the stored line stream.

    Related-read cards after the story body alternate short unpunctuated
    headlines with author credits (``作者：…``) and the reader toggle label.
    Body prose, by contrast, ends with sentence punctuation or runs long, and
    a real paragraph is never immediately followed by a bare author credit.
    """
    if not 0 <= index < len(lines):
        return False
    current = (lines[index] or "").strip()
    if not current or len(current) > 60 or re.search(r"[。！？!?]$", current):
        return False
    if (
        _is_toggle_line(current)
        or _ARTICLE_BYLINE_RE.match(current)
        or _is_page_chrome_line(current)
        or _ARTICLE_DATELINE_RE.match(current)
        or _ARTICLE_TIMESTAMP_RE.match(current)
    ):
        return False
    prev = (lines[index - 1] or "").strip() if index > 0 else ""
    if not (_is_toggle_line(prev) or _looks_like_prose(prev)):
        return False
    # The card headline is followed (possibly after the toggle label) by
    # either an author credit or another card headline — never body prose.
    lookahead = [line.strip() for line in lines[index + 1:index + 3]]
    non_chrome = [line for line in lookahead if line and not _is_toggle_line(line)]
    if not non_chrome:
        return False
    if _ARTICLE_BYLINE_RE.match(non_chrome[0]):
        return True
    return len(non_chrome[0]) <= 60 and not re.search(r"[。！？!?]$", non_chrome[0])


def _trim_stored_article_page(lines: list[str], title: str = "") -> list[str]:
    """Keep the article window when a stored field is actually a whole web page.

    The ingest path often stores reader text rather than HTML. In that case
    there is no DOM boundary to select, so use the last article header marker
    (the opened article normally follows the feed) and the publisher's footer
    markers as conservative boundaries.
    """
    if not lines:
        return []

    title_matches = [index for index, line in enumerate(lines) if _is_title_line(line, title)]
    anchors = [
        index for index, line in enumerate(lines)
        if _ARTICLE_BYLINE_RE.match(line) or _ARTICLE_DATELINE_RE.match(line)
    ]

    start = 0
    # Related/top-read cards frequently contain more bylines than the opened
    # story. Pair the first article header anchor with its nearby title rather
    # than starting at the last byline on the full page.
    paired_anchor = next(
        (
            anchor for anchor in anchors
            if any(index <= anchor and anchor - index <= 14 for index in title_matches)
        ),
        None,
    )
    if paired_anchor is not None:
        nearby_titles = [index for index in title_matches if index <= paired_anchor and paired_anchor - index <= 14]
        start = nearby_titles[-1]
    elif title_matches:
        start = title_matches[-1]
    elif anchors:
        start = max(0, anchors[0] - 4)

    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if _is_toggle_line(line):
            continue
        if (
            _ARTICLE_STOP_LINE_RE.match(line)
            or _ARTICLE_NEWSLETTER_LINE_RE.match(line)
        ):
            end = index
            break
        # A run of Bloomberg footer-menu labels means the story already ended.
        if _is_nav_label_line(line) and index + 1 < len(lines) and _is_nav_label_line(lines[index + 1]):
            end = index
            break
        # Recommendation cards (short headline + author credit) after body prose.
        if index > start + 2 and _is_related_card_headline(lines, index):
            end = index
            break

    window = lines[start:end]
    return [
        line for line in window
        if not _is_page_chrome_line(line)
        and not _is_toggle_line(line)
        and not _is_nav_label_line(line)
    ]


def _stored_paragraphs(content: Optional[str], title: str = "") -> list[str]:
    lines: list[str] = []
    for raw in (content or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        # DAO articles may be stored as HTML fragments (for example
        # ``<p style=...>正文</p>``). Convert block tags to real line breaks
        # before stripping the remaining markup, otherwise the reader prints
        # the publisher's HTML as if it were article text.
        markup_cleaned = html.unescape(raw)
        markup_cleaned = _HTML_BLOCK_MARKUP_RE.sub("\n", markup_cleaned)
        markup_cleaned = _HTML_TAG_RE.sub(" ", markup_cleaned)
        for candidate in markup_cleaned.split("\n"):
            if _SOURCE_RE.match(candidate) or _URL_RE.fullmatch(candidate.strip()):
                continue
            cleaned = _URL_RE.sub("", candidate)
            cleaned = re.sub(r"\s+", " ", cleaned).strip(" \t-—")
            if cleaned and not _is_page_chrome_line(cleaned):
                lines.append(cleaned)
    trimmed = _trim_stored_article_page(lines, title)
    return _dedupe_paragraphs([line for line in trimmed if not _is_title_line(line, title)])


def _dedupe_paragraphs(items: list[str]) -> list[str]:
    paragraphs: list[str] = []
    for item in items:
        text = re.sub(r"\s+", " ", str(item or "")).strip()
        if not text:
            continue
        # OCR tile boundaries can repeat a whole line. Drop only adjacent
        # duplicates, never merely similar prose.
        if paragraphs and text == paragraphs[-1]:
            continue
        paragraphs.append(text)
    return paragraphs


def _looks_like_image(url: str, content_type: str, raw: bytes) -> bool:
    if (content_type or "").lower().startswith("image/"):
        return True
    if Path(urlparse(url).path).suffix.lower() in _IMAGE_SUFFIXES:
        return True
    return raw.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"RIFF"))


def _looks_like_pdf(url: str, content_type: str, raw: bytes) -> bool:
    return (raw.startswith(b"%PDF-") or (content_type or "").lower() == "application/pdf"
            or Path(urlparse(url).path).suffix.lower() in _PDF_SUFFIXES)


async def _download_source(url: str) -> _DownloadedSource:
    normalized = _validate_report_url(url)
    try:
        async with httpx.AsyncClient(
            timeout=REPORT_URL_TIMEOUT,
            follow_redirects=True,
            headers=REPORT_URL_HEADERS,
        ) as client:
            async with client.stream("GET", normalized) as response:
                if response.status_code >= 400:
                    raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"文章原文读取失败：HTTP {response.status_code}")
                content_length = response.headers.get("content-length")
                if content_length and content_length.isdigit() and int(content_length) > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="文章原文文件过大，当前限制为 12MB。")
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="文章原文文件过大，当前限制为 12MB。")
                    chunks.append(chunk)
                final_url = str(response.url)
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="文章原文读取失败，请稍后重试。") from exc

    filename = Path(urlparse(final_url).path).name or "article-original"
    return _DownloadedSource(raw=b"".join(chunks), filename=filename, content_type=content_type)


def _image_tiles(raw: bytes) -> tuple[list[bytes], bool]:
    """Split a long screenshot into readable, ordered OCR tiles in memory."""
    try:
        from PIL import Image, ImageOps

        with Image.open(io.BytesIO(raw)) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            if image.width > _OCR_MAX_WIDTH:
                height = max(1, round(image.height * _OCR_MAX_WIDTH / image.width))
                image = image.resize((_OCR_MAX_WIDTH, height), Image.Resampling.LANCZOS)
            boxes = [(0, top, image.width, min(top + _OCR_TILE_HEIGHT, image.height))
                     for top in range(0, image.height, _OCR_TILE_HEIGHT)]
            truncated = len(boxes) > _MAX_OCR_TILES
            out: list[bytes] = []
            for box in boxes[:_MAX_OCR_TILES]:
                buff = io.BytesIO()
                image.crop(box).save(buff, format="PNG", optimize=True)
                out.append(buff.getvalue())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="无法读取文章原图。") from exc
    return out, truncated


_OCR_PROMPT = (
    "你是 OCR 转录器。请按从上到下、从左到右的顺序，逐段转录这张财经文章截图中全部可辨识的正文文字。"
    "只输出原文文字，段落之间空一行；不要总结、翻译、评价、补写或解释。"
    "忽略水印、网页按钮、二维码和纯装饰元素；日期、数字、公司名和标点照原样保留。"
    "看不清的极少数字用「[无法辨认]」标记，不要猜测。"
)


async def _ocr_tiles(tiles: list[bytes], *, truncated: bool = False) -> ArticleOriginalText:
    llm = CloudResearchLLM()
    if llm.provider == "mock":
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="当前未配置可用的文字转录服务。")

    # A long capture may contain dozens of tiles. A small semaphore keeps the
    # request fast without bursting the vision provider and losing later tiles
    # to rate limiting.
    semaphore = asyncio.Semaphore(min(_OCR_CONCURRENCY, len(tiles)))

    async def transcribe(index: int, tile: bytes) -> str:
        prompt = f"{_OCR_PROMPT}\n\n这是第 {index + 1} / {len(tiles)} 段截图。"
        async with semaphore:
            return await llm.complete_vision(prompt, [tile], max_tokens=3200, timeout_seconds=90, force_json=False)

    # Independent vertical tiles can be read concurrently. This is OCR rather
    # than interpretation; text is concatenated below in source order.
    try:
        texts = await asyncio.gather(*(transcribe(index, tile) for index, tile in enumerate(tiles)))
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="文章文字提取失败，请稍后重试。") from exc
    paragraphs: list[str] = []
    for text in texts:
        paragraphs.extend(_stored_paragraphs(text))
    clean = _dedupe_paragraphs(paragraphs)
    if not clean:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="图片中未识别出可阅读文字。")
    return ArticleOriginalText(paragraphs=clean, parser="vision-ocr", truncated=truncated)


async def _ocr_image(raw: bytes) -> ArticleOriginalText:
    tiles, truncated = await asyncio.to_thread(_image_tiles, raw)
    return await _ocr_tiles(tiles, truncated=truncated)


async def _extract_remote_source(url: str) -> ArticleOriginalText:
    downloaded = await _download_source(url)
    if _looks_like_image(url, downloaded.content_type, downloaded.raw):
        return await _ocr_image(downloaded.raw)

    if _looks_like_html(downloaded.raw, downloaded.content_type, downloaded.filename):
        text, truncated = await asyncio.to_thread(_html_to_article_text, downloaded.raw)
        paragraphs = _stored_paragraphs(text)
        if paragraphs:
            return ArticleOriginalText(paragraphs=paragraphs, parser="html", truncated=truncated)

    try:
        extracted = await asyncio.to_thread(
            extract_file_bytes,
            downloaded.raw,
            filename=downloaded.filename,
            content_type=downloaded.content_type,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="文章原文无法转换为文字。") from exc

    paragraphs = _stored_paragraphs(extracted.text)
    if paragraphs:
        return ArticleOriginalText(paragraphs=paragraphs, parser=extracted.parser, truncated=extracted.truncated)
    if _looks_like_pdf(url, downloaded.content_type, downloaded.raw):
        # Scanned PDFs have no text layer. Remain text-only, using page images
        # for OCR instead of exposing the PDF to the browser.
        try:
            from .research_vision import render_pdf_to_pngs

            pages = await asyncio.to_thread(render_pdf_to_pngs, downloaded.raw, max_pages=6)
            if pages:
                return await _ocr_tiles(pages, truncated=True)
        except HTTPException:
            raise
        except Exception:
            pass
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="原文中没有可提取的文字。")


def _cache_key(message: RealtimeMessageRecord, source_urls: str) -> str:
    fingerprint = "\n".join((message.id, source_urls, message.content or ""))
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()


def _get_cached(key: str) -> Optional[ArticleOriginalText]:
    hit = _ARTICLE_TEXT_CACHE.get(key)
    if hit is None:
        return None
    cached_at, result = hit
    ttl = _FALLBACK_CACHE_SECONDS if result.parser == "stored-fallback" else _CACHE_SECONDS
    if time.monotonic() - cached_at > ttl:
        _ARTICLE_TEXT_CACHE.pop(key, None)
        return None
    return result


def _put_cached(key: str, result: ArticleOriginalText) -> None:
    if len(_ARTICLE_TEXT_CACHE) >= _MAX_CACHE_ENTRIES and key not in _ARTICLE_TEXT_CACHE:
        oldest = min(_ARTICLE_TEXT_CACHE, key=lambda item: _ARTICLE_TEXT_CACHE[item][0])
        _ARTICLE_TEXT_CACHE.pop(oldest, None)
    _ARTICLE_TEXT_CACHE[key] = (time.monotonic(), result)


def article_source_file_url(message: RealtimeMessageRecord) -> str:
    """返回文章的原始文件直链（截图/PDF）；网页型来源返回空串。

    前端用它做「真原文」直出渲染；空串表示该文章没有文件形态的原文，
    继续走文字提取阅读器。"""
    for url in _source_urls(message):
        if _file_like_url(url) and _media_is_image_or_pdf(url):
            return url
    return ""


def _media_is_image_or_pdf(url: str) -> bool:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in _IMAGE_SUFFIXES or suffix in _PDF_SUFFIXES:
        return True
    return "/uploads/" in url or "/upload/" in url


def looks_like_article_image(url: str, raw_head: bytes) -> bool:
    if raw_head.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"RIFF")):
        return True
    return Path(urlparse(url).path).suffix.lower() in _IMAGE_SUFFIXES


async def download_article_source(url: str) -> _DownloadedSource:
    return await _download_source(url)


async def extract_article_original_text(message: RealtimeMessageRecord) -> ArticleOriginalText:
    """Return only readable article text; this function never exposes source bytes."""
    source_urls = _source_urls(message)
    source_url = source_urls[0] if source_urls else ""
    key = _cache_key(message, "\n".join(source_urls))
    cached = _get_cached(key)
    if cached is not None:
        return cached

    stored = _stored_paragraphs(message.content, message.title or "")
    # A complete stored article is canonical and avoids an unnecessary fetch.
    # Screenshot/PDF sources are preferred because their database body normally
    # contains only a lead paragraph.
    is_file_source = any(_file_like_url(url) for url in source_urls)
    if not source_url or (len("".join(stored)) >= 600 and not is_file_source):
        if stored:
            result = ArticleOriginalText(stored, "stored-text")
            _put_cached(key, result)
            return result
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="该文章暂未收录可阅读正文。")

    async def produce() -> ArticleOriginalText:
        # Another click may have completed the transcription while this request
        # was waiting on the single-flight task.
        existing = _get_cached(key)
        if existing is not None:
            return existing
        result: Optional[ArticleOriginalText] = None
        last_error: Optional[Exception] = None
        for candidate in source_urls:
            try:
                result = await _extract_remote_source(candidate)
                break
            except Exception as exc:
                # One source can be blocked while the collector's screenshot
                # remains available (or vice versa). Continue to the next
                # candidate before falling back to the stored lead.
                last_error = exc
        if result is None:
            # Reuters/Bloomberg may reject server-side reads even when the
            # article's stored lead is available. Keep the reader useful for
            # any upstream/parser failure and retry the source after a short
            # cache window instead of surfacing a blank error panel to the user.
            if stored:
                result = ArticleOriginalText(stored, "stored-fallback", truncated=True)
            elif last_error is not None:
                raise last_error
            else:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="该文章暂未收录可阅读正文。")
        _put_cached(key, result)
        return result

    return await _ARTICLE_TEXT_FLIGHT.run(key, produce)


async def prewarm_article_original_text(message: RealtimeMessageRecord) -> None:
    """Extract an article as soon as it is ingested, without blocking ingest.

    Failures are intentionally not cached: the normal member-only reader can
    retry later when the upstream source or OCR provider recovers.
    """
    if (message.topic or "") != "文章":
        return
    try:
        await extract_article_original_text(message)
    except asyncio.CancelledError:
        raise
    except Exception:
        # Prewarming is best effort. The source may be temporarily unavailable
        # and the on-demand reader remains the retry path.
        return
