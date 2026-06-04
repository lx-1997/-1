from __future__ import annotations

import hashlib
import html
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from fastapi import HTTPException, status

from .schemas import OfficialNewsItem, OfficialNewsResponse, OfficialNewsSource
from .shared_utils import dedupe, safe_error, utc_now_iso


HTTP_TIMEOUT_SECONDS = float(os.getenv("DEEPFOCUS_OFFICIAL_NEWS_TIMEOUT_SECONDS", "14"))
CACHE_TTL_SECONDS = int(os.getenv("DEEPFOCUS_OFFICIAL_NEWS_CACHE_TTL_SECONDS", "300"))
MAX_PARSE_ITEMS = int(os.getenv("DEEPFOCUS_OFFICIAL_NEWS_MAX_PARSE_ITEMS", "120"))

PUBLIC_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
}

SOURCE_CONFIG: dict[str, dict[str, str]] = {
    "xinwenlianbo": {
        "name": "新闻联播",
        "url": "https://tv.cctv.com/lm/xwlb/index.shtml",
        "section": "新闻联播",
    },
    "cctv-news": {
        "name": "央视新闻",
        "url": "https://news.cctv.com/",
        "extra_urls": "https://news.cctv.com/china/|https://news.cctv.com/world/",
        "section": "新闻频道",
    },
    "cctv-economy": {
        "name": "央视财经",
        "url": "https://finance.cctv.com/",
        "extra_urls": "https://jingji.cctv.com/",
        "section": "财经频道",
    },
}

SOURCE_ALIASES = {
    "xwlb": "xinwenlianbo",
    "news": "cctv-news",
    "cctv": "cctv-news",
    "economy": "cctv-economy",
    "finance": "cctv-economy",
}

ARTICLE_HOST_RE = re.compile(r"^(?:news|tv|jingji|finance)\.cctv\.com$", re.I)
ARTICLE_PATH_RE = re.compile(r"/\d{4}/\d{2}/\d{2}/(?:ARTI|VIDE)[^/]*\.shtml$", re.I)
ANCHOR_RE = re.compile(
    r"<a\b[^>]*\bhref=[\"'](?P<href>[^\"']+)[\"'][^>]*>(?P<body>.*?)</a>",
    re.I | re.S,
)
DATE_RE = re.compile(r"/(\d{4})/(\d{2})/(\d{2})/")

POLICY_TERMS = ("习近平", "国务院", "中央", "政治局", "人大", "政协", "部委", "政策", "规定", "意见", "会议", "制度")
MACRO_TERMS = ("经济", "消费", "投资", "出口", "进口", "外贸", "物价", "就业", "财政", "货币", "金融", "房地产")
INDUSTRY_TERMS = ("制造", "科技", "能源", "航天", "汽车", "半导体", "人工智能", "农业", "医药", "交通", "物流")
RISK_TERMS = ("风险", "预警", "汛期", "灾害", "冲突", "制裁", "调查", "处罚", "事故", "安全")
GLOBAL_TERMS = ("美国", "欧盟", "日本", "韩国", "国际", "全球", "伊朗", "俄罗斯", "贸易摩擦", "地缘")

_cache: dict[str, tuple[datetime, OfficialNewsResponse]] = {}


async def fetch_official_news(
    *,
    source: str = "xinwenlianbo",
    limit: int = 30,
    refresh: bool = False,
) -> OfficialNewsResponse:
    source_key = _normalize_source(source)
    safe_limit = max(1, min(limit, 80))
    now = datetime.now(timezone.utc)
    cached = _cache.get(source_key)
    if cached and not refresh:
        fetched_at, response = cached
        age = int((now - fetched_at).total_seconds())
        if age <= CACHE_TTL_SECONDS:
            return _slice_response(response, safe_limit, cache_age_seconds=age)

    config = SOURCE_CONFIG[source_key]
    urls = _source_urls(config)
    warnings: list[str] = []
    pages: list[tuple[str, str, str, list[OfficialNewsItem]]] = []
    last_error: Optional[Exception] = None
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True, headers=PUBLIC_HEADERS) as client:
        for url in urls:
            try:
                response = await client.get(url)
                response.raise_for_status()
                html_text = response.text
                page_title = _extract_page_title(html_text) or config["name"]
                page_items = _parse_official_news_items(
                    html_text,
                    source_key=source_key,
                    source_name=config["name"],
                    section=config["section"],
                    base_url=str(response.url),
                )
                pages.append((url, str(response.url), page_title, page_items))
                if not page_items:
                    warnings.append(f"{config['name']}页面未解析到条目：{url}")
            except Exception as exc:
                last_error = exc
                warnings.append(f"{config['name']}页面暂不可用：{url} ({safe_error(exc)})")

    if not pages:
        if cached:
            fetched_at, cached_response = cached
            age = int((now - fetched_at).total_seconds())
            fallback = _slice_response(cached_response, safe_limit, cache_age_seconds=age)
            fallback.warnings.append(f"官方页面暂不可用，已返回缓存：{safe_error(last_error) if last_error else 'unknown'}")
            return fallback
        # 优雅降级：上游 403/不可达且无缓存时，返回空结果 + 结构化告警（200），
        # 而不是 502——让前端正常渲染骨架并展示"暂不可用"提示。
        warnings.append(
            f"央视官方页面暂不可用：{safe_error(last_error) if last_error else 'unknown'}"
        )
        empty_response = OfficialNewsResponse(
            generated_at=utc_now_iso(),
            source=source_key,  # type: ignore[arg-type]
            source_name=config["name"],
            source_url=urls[0] if urls else "",
            total_found=0,
            returned_count=0,
            cache_age_seconds=0,
            warnings=warnings,
            items=[],
        )
        return _slice_response(empty_response, safe_limit, cache_age_seconds=0)

    page_title = pages[0][2]
    source_url = pages[0][1]
    items = _merge_items([page_items for *_prefix, page_items in pages])
    if not items:
        warnings.append("官方页面已返回，但暂未解析到新闻条目，可能是页面结构变化。")

    full_response = OfficialNewsResponse(
        generated_at=utc_now_iso(),
        source=source_key,  # type: ignore[arg-type]
        source_name=config["name"],
        source_url=source_url,
        total_found=len(items),
        returned_count=len(items),
        cache_age_seconds=0,
        warnings=warnings,
        items=items,
    )
    full_response.warnings.extend(_source_health_warnings(source_key, page_title, items))
    _cache[source_key] = (now, full_response)
    return _slice_response(full_response, safe_limit, cache_age_seconds=0)


def _normalize_source(source: str) -> OfficialNewsSource:
    key = SOURCE_ALIASES.get((source or "").strip(), (source or "").strip())
    if key not in SOURCE_CONFIG:
        allowed = " / ".join(SOURCE_CONFIG)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"不支持的央视新闻源：{source}，可选 {allowed}")
    return key  # type: ignore[return-value]


def _source_urls(config: dict[str, str]) -> list[str]:
    urls = [config["url"]]
    extra_urls = [url.strip() for url in (config.get("extra_urls") or "").split("|") if url.strip()]
    return dedupe([*urls, *extra_urls])


def _slice_response(response: OfficialNewsResponse, limit: int, *, cache_age_seconds: int) -> OfficialNewsResponse:
    items = response.items[:limit]
    return response.model_copy(
        deep=True,
        update={
            "items": items,
            "returned_count": len(items),
            "cache_age_seconds": cache_age_seconds,
        },
    )


def _merge_items(groups: list[list[OfficialNewsItem]]) -> list[OfficialNewsItem]:
    merged: list[OfficialNewsItem] = []
    seen_urls: set[str] = set()
    for group in groups:
        for item in group:
            if item.url in seen_urls:
                continue
            seen_urls.add(item.url)
            merged.append(item)
    indexed = list(enumerate(merged))
    indexed.sort(key=lambda pair: (pair[1].reported_date or "", -pair[0]), reverse=True)
    return [item for _index, item in indexed]


def _parse_official_news_items(
    html_text: str,
    *,
    source_key: OfficialNewsSource,
    source_name: str,
    section: str,
    base_url: str,
) -> list[OfficialNewsItem]:
    by_url: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    for match in ANCHOR_RE.finditer(html_text):
        href = html.unescape(match.group("href")).strip()
        absolute_url = urljoin(base_url, href)
        canonical_url = _canonical_article_url(absolute_url)
        if not canonical_url:
            continue

        text = _clean_fragment(match.group("body"))
        if _is_noise_text(text):
            continue

        candidate = _normalize_title(text, source_key)
        if _is_noise_text(candidate):
            continue

        current = by_url.get(canonical_url)
        if not current:
            current = {
                "title": "",
                "summary": "",
                "url": canonical_url,
                "source": source_key,
                "source_name": source_name,
                "section": section,
                "metadata": {"raw_url": absolute_url},
            }
            by_url[canonical_url] = current
            order.append(canonical_url)

        if not current["title"]:
            current["title"] = candidate
        elif candidate != current["title"] and len(candidate) > len(str(current.get("title") or "")) + 18:
            current["summary"] = _best_summary(current.get("summary") or "", candidate)
        elif candidate != current["title"] and not current.get("summary") and len(candidate) >= 24:
            current["summary"] = candidate

    items: list[OfficialNewsItem] = []
    for url in order:
        raw = by_url[url]
        title = str(raw.get("title") or "").strip()
        if not title:
            continue
        published_at, reported_date = _date_from_url(url)
        tags, importance = _classify_item(title, str(raw.get("summary") or ""), source_key)
        items.append(
            OfficialNewsItem(
                id=_stable_id(source_key, url),
                title=title[:220],
                summary=str(raw.get("summary") or "")[:520],
                url=url,
                source=source_key,
                source_name=source_name,
                section=section,
                published_at=published_at,
                reported_date=reported_date,
                tags=tags,
                importance_score=importance,
                metadata=raw.get("metadata") or {},
            )
        )
        if len(items) >= MAX_PARSE_ITEMS:
            break

    return items


def _canonical_article_url(url: str) -> Optional[str]:
    parsed = urlsplit(url)
    if not ARTICLE_HOST_RE.match(parsed.netloc.lower()):
        return None
    if not ARTICLE_PATH_RE.search(parsed.path):
        return None
    return urlunsplit((parsed.scheme or "https", parsed.netloc, parsed.path, "", ""))


def _clean_fragment(value: str) -> str:
    text = re.sub(r"<script\b.*?</script>", "", value, flags=re.I | re.S)
    text = re.sub(r"<style\b.*?</style>", "", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _normalize_title(value: str, source_key: OfficialNewsSource) -> str:
    text = re.sub(r"^(?:完整版)?\s*", "", value).strip()
    if source_key == "xinwenlianbo":
        text = re.sub(r"^完整版\s*", "", text).strip()
    return text


def _best_summary(current: str, candidate: str) -> str:
    if not current:
        return candidate
    return candidate if len(candidate) > len(current) else current


def _is_noise_text(value: str) -> bool:
    text = (value or "").strip()
    if len(text) < 4:
        return True
    if text in {"首页", "新闻", "国内", "国际", "经济", "社会", "法治", "图片", "文娱", "科技", "生活", "军事", "专题"}:
        return True
    if text.startswith("扫码") or text.startswith("更多") or text.startswith("点击"):
        return True
    return False


def _date_from_url(url: str) -> tuple[Optional[str], Optional[str]]:
    match = DATE_RE.search(url)
    if not match:
        return None, None
    year, month, day = match.groups()
    reported_date = f"{year}-{month}-{day}"
    return f"{reported_date}T00:00:00+08:00", reported_date


def _classify_item(title: str, summary: str, source_key: OfficialNewsSource) -> tuple[list[str], int]:
    text = f"{title} {summary}"
    tags: list[str] = [SOURCE_CONFIG[source_key]["name"], "央视"]
    score = 50

    if source_key == "xinwenlianbo":
        tags.append("新闻联播")
        score += 8
    if source_key == "cctv-economy":
        tags.append("财经")
        score += 5

    if any(term in text for term in POLICY_TERMS):
        tags.append("政策")
        score += 16
    if any(term in text for term in MACRO_TERMS):
        tags.append("宏观")
        score += 12
    if any(term in text for term in INDUSTRY_TERMS):
        tags.append("产业")
        score += 8
    if any(term in text for term in RISK_TERMS):
        tags.append("风险")
        score += 10
    if any(term in text for term in GLOBAL_TERMS):
        tags.append("国际")
        score += 7

    if "快讯" in text:
        tags.append("快讯")
    if "《新闻联播》" in text:
        tags.append("完整节目")
        score += 8

    return dedupe(tags), max(0, min(100, score))


def _extract_page_title(html_text: str) -> str:
    match = re.search(r"<title>(.*?)</title>", html_text, flags=re.I | re.S)
    return _clean_fragment(match.group(1)) if match else ""


def _source_health_warnings(source_key: OfficialNewsSource, page_title: str, items: list[OfficialNewsItem]) -> list[str]:
    warnings: list[str] = []
    if source_key == "xinwenlianbo" and not any("《新闻联播》" in item.title for item in items[:5]):
        warnings.append("新闻联播页面已返回，但前几条未出现完整节目条目，请复核页面结构。")
    if "403" in page_title or "Forbidden" in page_title:
        warnings.append("官方页面疑似返回访问限制页。")
    return warnings


def _stable_id(source_key: OfficialNewsSource, url: str) -> str:
    digest = hashlib.sha1(f"{source_key}:{url}".encode("utf-8")).hexdigest()[:16]
    return f"cctv-{source_key}-{digest}"
