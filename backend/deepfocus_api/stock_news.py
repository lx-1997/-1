"""个股最近新闻/资讯抓取器——东方财富搜索源。

用户最高频问法「X股最近发生了啥」：返回某只股票近期带标题+日期+摘要+链接+来源的
新闻列表，按时间倒序、近期优先。配合「日期/数字别凭记忆」护栏，给模型真实可溯源的资讯。

数据源：https://search-api-web.eastmoney.com/search/jsonp（cmsArticleWebOld 资讯库，
sort=time 时间倒序）。直连绕代理（httpx trust_env=False，外部 403 是沙箱代理封的）。
失败/无数据 → []（优雅降级，绝不抛异常）。缓存 10 分钟。
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Optional

import httpx

from .shared_utils import clean_title, short_text

_SEARCH_URL = "https://search-api-web.eastmoney.com/search/jsonp"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://so.eastmoney.com/",
}

_CACHE: dict[str, tuple[float, list]] = {}
_CACHE_TTL = 600.0  # 10 分钟：资讯时效性强但不必每次现抓


def _strip_jsonp(text: str) -> Optional[dict]:
    """剥掉 jQuery(...) / cb(...) 外壳取 JSON。失败返回 None。"""
    if not text:
        return None
    try:
        match = re.match(r"^[^(]*\((.*)\)\s*;?\s*$", text.strip(), re.S)
        payload = match.group(1) if match else text
        return json.loads(payload)
    except Exception:
        return None


def _clean_summary(value: Any, limit: int = 140) -> str:
    """摘要去 <em> 高亮标签 + 折叠空白 + 截断（保留可读空格，区别于 clean_title 去全部空白）。"""
    text = re.sub(r"</?em>", "", str(value or ""), flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("　", " ").replace("&nbsp;", " ")
    return short_text(text, limit)


async def fetch_stock_news(
    symbol: str,
    market: Optional[str] = None,
    limit: int = 8,
) -> list[dict]:
    """抓取某只股票最近的新闻/资讯（时间倒序、近期优先）。

    入参 symbol 可为代码（600519）或带后缀（600519.SH），market 仅占位兼容签名。
    返回 list[{title, date, summary, url, source}]，每项已去高亮标签、清洗。
    无数据/任何失败 → []（绝不抛异常）。
    """
    code = re.sub(r"\D", "", symbol or "")
    if not code:
        return []
    try:
        limit = max(1, min(int(limit), 30))
    except (TypeError, ValueError):
        limit = 8

    cache_key = f"{code}:{limit}"
    hit = _CACHE.get(cache_key)
    if hit and (time.time() - hit[0]) < _CACHE_TTL:
        return hit[1]

    results: list[dict] = []
    try:
        param = {
            "uid": "",
            "keyword": code,
            "type": ["cmsArticleWebOld"],
            "client": "web",
            "clientType": "web",
            "clientVersion": "curr",
            "param": {
                "cmsArticleWebOld": {
                    "searchScope": "default",
                    "sort": "time",  # 时间倒序，近期优先
                    "pageIndex": 1,
                    "pageSize": limit,
                    "preTag": "",
                    "postTag": "",
                }
            },
        }
        params = {"cb": "jQuery", "param": json.dumps(param, ensure_ascii=False)}
        async with httpx.AsyncClient(trust_env=False, timeout=12.0) as client:
            r = await client.get(_SEARCH_URL, params=params, headers=_HEADERS)
        if r.status_code == 200:
            data = _strip_jsonp(r.text) or {}
            items = ((data.get("result") or {}).get("cmsArticleWebOld")) or []
            for it in items:
                title = clean_title(it.get("title") or "")
                if not title:
                    continue
                results.append(
                    {
                        "title": title,
                        "date": (it.get("date") or "")[:19] or None,
                        "summary": _clean_summary(it.get("content")) or None,
                        "url": it.get("url") or None,
                        # 用 source_name(新闻出处:财联社/界面新闻)：privacy_guard 保留 source_name、却会剥 source，
                        # 否则回灌前媒体名被删→模型无法标注"据财联社报道"。
                        "source_name": (it.get("mediaName") or "").strip() or None,
                    }
                )
                if len(results) >= limit:
                    break
    except Exception:
        results = []

    _CACHE[cache_key] = (time.time(), results)
    return results
