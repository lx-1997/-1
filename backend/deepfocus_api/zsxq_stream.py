"""知识星球「星球纪要」流：星球普通帖子（调研纪要 / 个股动态点评 / 观点）的独立信息流模块。

「研报」标签只覆盖了星球里的【文件】（PDF 研报）；星球里大量高价值内容其实是【普通帖子】
（调研会议纪要、动态点评、组合观点，如「水木调研纪要」的文字帖）。本模块把帖子流作为
独立标签展示：复用同机 Node 工作台 /api/search-topics（与名人观点同一条链路），
默认拉 ZSXQ_GROUP（水木调研纪要-2.0）最新帖子，支持关键词搜索与「加载更早」游标翻页。

⚠️ 第三方付费社群内容：站内信息流仅白名单账号可见（与 iFinD / 研报原文同口径），main.py 端点侧硬门控。
分享=用户拍板可对外（2026-07-06）：白名单用户可把单条纪要分享成 /note/{id} 公开软墙页，但只出
**标题+≤100字导语钩子**（不放全文、不透星球来源、noindex+不进 sitemap，仅可链接转发不做搜索收录），
全文仍锁在站内白名单后。持久化见 _persist_share_topics/get_share_topic。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from pathlib import Path
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

    return [{"id": ZSXQ_GROUP, "name": "机构纪要"}]


# 星球上传【文件】(研报 PDF / 会议音频等)时会自动生成一条"纯标签"占位帖，正文就是
# 「#海外投行报告#」「#会议音频#」这类 hashtag，无正文、无图。这些文件本体已经在「研报」
# 标签里；机构纪要只要真正的调研纪要正文，故把纯标签占位帖过滤掉（用户反馈）。
_HASHTAG_RE = re.compile(r"#[^#\n]{1,40}#")
_STRIP_RESIDUE_RE = re.compile(r"[\s　·|｜—\-：:、,，。.]+")


def _is_file_marker_only(text: str, images: list) -> bool:
    """判断是否为"文件上传占位帖"：剥掉 hashtag 与分隔残渣后无任何正文，且无图片。"""
    if images:
        return False  # 图片型纪要（如"公募加仓行业图"）保留
    prose = _STRIP_RESIDUE_RE.sub("", _HASHTAG_RE.sub("", text or ""))
    return len(prose) == 0


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
    if _is_file_marker_only(text, images):
        return None  # 文件上传占位帖（研报 PDF 等，已在「研报」标签）→ 不进机构纪要
    ct = str(t.get("create_time") or "").strip()
    comments = [m for m in (_norm_comment(c) for c in (t.get("comments") or [])[:10]) if m]
    links: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for link in (t.get("links") or [])[:12]:
        if not isinstance(link, dict):
            continue
        url = str(link.get("url") or "").strip()
        if not re.match(r"^https?://", url, re.I) or url in seen_urls:
            continue
        label = re.sub(r"知识星球(?:\s*[-—|·]\s*安全中心)?", "", str(link.get("label") or "")).strip()
        links.append({"label": (label or "查看网页")[:120], "url": url[:1200]})
        seen_urls.add(url)
    first_line = (text.split("\n", 1)[0]).strip()
    # ⭐不透出来源:响应里刻意不带 author(星球号名)与 url(星球帖子链接)——
    # 展示面不体现具体星球来源,也不提供跳回原文的入口(用户拍板)。
    return {
        "id": str(t.get("topicId") or "").strip(),
        "title": (first_line[:80] + ("…" if len(first_line) > 80 else "")) or "图片动态",
        "text": text,
        "links": links,
        "images": images,
        "image_fulls": image_fulls or images,  # 缺原图回退小图
        "create_time": ct,
        "date": ct[:10],
        "digested": bool(t.get("digested")),
        "comments_count": max(int(t.get("comments_count") or 0), len(comments)),
        "comments": comments,
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
    _persist_share_topics(items)  # 只落 id/标题/短导语/日期，供 /note/{id} 分享落地页（不存全文）
    return out


# --------------------------------------------------------------------------- #
# 分享落地页持久化：白名单用户分享机构纪要 → /note/{id} 公开软墙页（标题+短导语钩子，
# 不放全文/不透来源/noindex）。只存构建钩子所需的最小字段，不存全文——降低第三方内容留存面。
# --------------------------------------------------------------------------- #
SHARE_DB = Path(
    os.getenv(
        "DEEPFOCUS_ZSXQ_SHARE_DB_PATH",
        str(Path(__file__).resolve().parents[1] / ".zsxq_share.sqlite3"),
    )
)
_SHARE_MAX = 8000        # 行数上限，滚动裁剪（1.8G 机器守内存/磁盘）
_SHARE_LEAD_LEN = 100    # 公开导语长度：够钩子、远不够全文


def _init_share_db() -> None:
    SHARE_DB.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(SHARE_DB) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS zsxq_share "
            "(id TEXT PRIMARY KEY, title TEXT NOT NULL, lead TEXT NOT NULL, date TEXT, created_at REAL NOT NULL)"
        )
        conn.commit()


def _persist_share_topics(items: list[dict[str, Any]]) -> None:
    """把本次拉到的帖子的 id/标题/短导语落库（幂等 upsert）。失败不阻塞主流程。"""
    rows = []
    for it in items or []:
        tid = str(it.get("id") or "").strip()
        if not tid:
            continue
        title = str(it.get("title") or "").strip()[:120]
        flat = " ".join(str(it.get("text") or "").split())
        lead = flat[:_SHARE_LEAD_LEN] + ("…" if len(flat) > _SHARE_LEAD_LEN else "")
        rows.append((tid, title, lead, str(it.get("date") or ""), time.time()))
    if not rows:
        return
    try:
        _init_share_db()
        with sqlite3.connect(SHARE_DB) as conn:
            conn.executemany(
                "INSERT INTO zsxq_share (id,title,lead,date,created_at) VALUES (?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET title=excluded.title, lead=excluded.lead, date=excluded.date",
                rows,
            )
            conn.execute(
                "DELETE FROM zsxq_share WHERE id NOT IN "
                "(SELECT id FROM zsxq_share ORDER BY created_at DESC LIMIT ?)",
                (_SHARE_MAX,),
            )
            conn.commit()
    except sqlite3.Error:
        pass


def get_share_topic(topic_id: str) -> Optional[dict[str, Any]]:
    """按 id 取分享落地页所需字段（title/lead/date）；不存在返回 None。公开路由用，不需鉴权。"""
    tid = re.sub(r"\D", "", str(topic_id or ""))
    if not tid:
        return None
    try:
        _init_share_db()
        with sqlite3.connect(SHARE_DB) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT id,title,lead,date FROM zsxq_share WHERE id=?", (tid,)).fetchone()
        return dict(row) if row else None
    except sqlite3.Error:
        return None


def recent_share_topics(limit: int = 200) -> list[dict[str, Any]]:
    """最近落库的机构纪要（供公开 /notes hub 列表 + sitemap 收录）；按入库时间倒序，滤掉无正文的图片帖。"""
    try:
        _init_share_db()
        with sqlite3.connect(SHARE_DB) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id,title,lead,date FROM zsxq_share WHERE lead != '' "
                "ORDER BY created_at DESC LIMIT ?", (max(1, min(int(limit or 200), 500)),)
            ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return []


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
