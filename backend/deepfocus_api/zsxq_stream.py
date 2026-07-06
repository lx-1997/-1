"""知识星球「星球纪要」流：星球普通帖子（调研纪要 / 个股动态点评 / 观点）的独立信息流模块。

「研报」标签只覆盖了星球里的【文件】（PDF 研报）；星球里大量高价值内容其实是【普通帖子】
（调研会议纪要、动态点评、组合观点，如「水木调研纪要」的文字帖）。本模块把帖子流作为
独立标签展示：复用同机 Node 工作台 /api/search-topics（与名人观点同一条链路），
默认拉 ZSXQ_GROUP（水木调研纪要-2.0）最新帖子，支持关键词搜索与「加载更早」游标翻页。

⚠️ 第三方付费社群内容：仅白名单账号可见（与 iFinD / 研报原文同口径），
不进公开信息流 / SEO / 分享面，main.py 端点侧硬门控。
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Optional

import httpx

_WORKBENCH_PORT = os.getenv("RESEARCH_WORKBENCH_INTERNAL_PORT", "3927").strip()
_WORKBENCH_BASE = f"http://127.0.0.1:{_WORKBENCH_PORT}"

# 首页（无游标）TTL 缓存：星球接口一次 2-6s，缓存后重复打开秒回；翻页请求不缓存。
_CACHE: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_TTL = float(os.getenv("DEEPFOCUS_ZSXQ_STREAM_TTL", "180"))
_CACHE_MAX = 24


def stream_groups() -> list[dict[str, str]]:
    """可展示的星球列表：env DEEPFOCUS_ZSXQ_STREAM_GROUPS（JSON [{"id","name"}]）；
    缺省 = 研报同一星球（水木调研纪要）。也充当 group 白名单——防任意 group 探测。"""
    raw = os.getenv("DEEPFOCUS_ZSXQ_STREAM_GROUPS", "").strip()
    if raw:
        try:
            data = json.loads(raw)
            groups = [
                {"id": str(g.get("id") or "").strip(), "name": str(g.get("name") or "星球").strip()[:24]}
                for g in data
                if isinstance(g, dict) and str(g.get("id") or "").strip()
            ]
            if groups:
                return groups
        except Exception:  # noqa: BLE001 - env 配错回退默认，不拖垮模块
            pass
    from .research_wire import ZSXQ_GROUP  # 局部引入避免循环导入

    return [{"id": ZSXQ_GROUP, "name": "水木调研纪要"}]


def _norm_comment(c: Any) -> Optional[dict[str, Any]]:
    if not isinstance(c, dict):
        return None
    text = str(c.get("text") or "").strip()
    author = str(c.get("author") or "").strip()
    if not text and not author:
        return None
    return {
        "author": author[:40],
        "text": text,
        "create_time": str(c.get("create_time") or "").strip(),
        "likes_count": max(0, int(c.get("likes_count") or 0)),
        "sticky": bool(c.get("sticky")),
        "reply_to": str(c.get("reply_to") or "").strip()[:40],
    }


def _norm_topic(t: Any) -> Optional[dict[str, Any]]:
    """工作台 topicToViewItem → 前端可直接消费的帖子结构。空帖丢弃。"""
    if not isinstance(t, dict):
        return None
    text = str(t.get("text") or "").strip()
    images = [str(u).strip() for u in (t.get("images") or []) if str(u).strip()][:9]
    image_fulls = [str(u).strip() for u in (t.get("image_fulls") or []) if str(u).strip()][:9]
    if not text and not images:
        return None
    ct = str(t.get("create_time") or "").strip()
    comments = [m for m in (_norm_comment(c) for c in (t.get("comments") or [])[:10]) if m]
    first_line = (text.split("\n", 1)[0]).strip()
    return {
        "id": str(t.get("topicId") or "").strip(),
        "title": (first_line[:80] + ("…" if len(first_line) > 80 else "")) or "图片动态",
        "text": text,
        "images": images,
        "image_fulls": image_fulls or images,  # 缺原图回退小图
        "author": str(t.get("author") or "星球主理人").strip()[:40],
        "create_time": ct,
        "date": ct[:10],
        "digested": bool(t.get("digested")),
        "likes_count": max(0, int(t.get("likes_count") or 0)),
        "comments_count": max(int(t.get("comments_count") or 0), len(comments)),
        "comments": comments,
        "url": str(t.get("url") or "").strip(),
    }


async def fetch_stream(
    *, group: str = "", keyword: str = "", limit: int = 20, end_time: str = "", use_cache: bool = True,
) -> dict[str, Any]:
    """拉某星球的帖子流。end_time 为「加载更早」游标（上一页 next_before），空 = 最新首页。

    返回 {items, group, groups, keyword, next_before, has_more, total}。
    首页失败时回退上次成功缓存（stale-while-revalidate），绝不让用户看到空面板。
    """
    groups = stream_groups()
    g = (group or "").strip() or groups[0]["id"]
    if g not in {x["id"] for x in groups}:
        raise ValueError("未知星球")
    kw = (keyword or "").strip()[:40]
    n = max(1, min(int(limit or 20), 40))
    first_page = not (end_time or "").strip()
    ckey = (g, kw)
    now = time.monotonic()

    if use_cache and first_page:
        hit = _CACHE.get(ckey)
        if hit and (now - hit[0]) < _TTL:
            return hit[1]

    payload: dict[str, Any] = {"group": g, "resultLimit": n, "searchPages": 4}
    if kw:
        payload["keyword"] = kw
    if not first_page:
        payload["endTime"] = str(end_time).strip()
    from .research_wire import auth_payload  # 热更新 cookie 优先（与研报/名人观点一致）

    payload.update(auth_payload())
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            resp = await client.post(f"{_WORKBENCH_BASE}/api/search-topics", json=payload, timeout=60)
            resp.raise_for_status()
            data = resp.json()
        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError(str(data["error"])[:160])
    except Exception:
        hit = _CACHE.get(ckey)
        if first_page and hit:  # 上游故障 → 回退上次成功结果
            return hit[1]
        raise

    items = [it for it in (_norm_topic(t) for t in (data.get("items") or [])) if it]
    out: dict[str, Any] = {
        "items": items,
        "group": g,
        "groups": groups,
        "keyword": kw,
        "next_before": str(data.get("nextEndTime") or "").strip(),
        "has_more": bool(data.get("hasMore")),
        "total": len(items),
    }
    if first_page and items:  # 只缓存非空首页
        _CACHE[ckey] = (now, out)
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.pop(min(_CACHE.items(), key=lambda kv: kv[1][0])[0], None)
    return out


async def fetch_comments(topic_id: str, *, limit: int = 100) -> dict[str, Any]:
    """拉某帖的完整评论（列表接口随帖只带前几条预览）。上游故障返回 error 字段而非抛 5xx。"""
    tid = re.sub(r"\D", "", str(topic_id or ""))
    if not tid:
        return {"topic_id": "", "comments": [], "count": 0, "has_more": False, "error": "缺少帖子 ID"}
    payload: dict[str, Any] = {"topicId": tid, "limit": max(1, min(int(limit or 100), 300))}
    from .research_wire import auth_payload

    payload.update(auth_payload())
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            resp = await client.post(f"{_WORKBENCH_BASE}/api/topic-comments", json=payload, timeout=60)
            resp.raise_for_status()
            data = resp.json()
        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError(str(data["error"])[:160])
    except Exception as exc:  # noqa: BLE001
        return {"topic_id": tid, "comments": [], "count": 0, "has_more": False,
                "error": f"评论拉取失败：{str(exc)[:80]}"}
    comments = [m for m in (_norm_comment(c) for c in (data.get("comments") or [])) if m]
    return {
        "topic_id": tid,
        "comments": comments,
        "count": len(comments),
        "has_more": bool(data.get("hasMore")),
    }
