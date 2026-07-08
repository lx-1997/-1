from __future__ import annotations

import hashlib as _hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

"""OpenClaw/飞书定时摘要：聚合过去 N 小时的四类内容（快讯/研报/机构纪要/文章），供外部定时任务
（飞书机器人）拉取后自己组织人话摘要再推送。见 [[wechat-push-channel]] 的准推送边界，本模块是它的
「飞书/OpenClaw 版」姊妹口径，但不复用微信那套鉴权——走合作方 API Key（/api/v1/openclaw/digest）。

只读口径——本模块绝不在请求路径里触发新的 AI 解读或新的 ZSXQ 下载：
- 快讯/机构纪要本身没有 AI 解读闸，直接返回原文，随时能拉到。
- 研报/文章只有命中后台已有的 AI 解读缓存（run_research_prewarm/run_news_prewarm 常态预热，
  通常发布后 30-90 分钟内即缓存好）才带 summary/df_take；未命中的只给标题等基础信息 + pending=True。
  刻意不在这里现抓现解：研报下载受 ZSXQ「单自然日下载量」限额约束（daily_max，见 run_research_prewarm），
  在请求路径里临时触发解读会和后台预热任务抢同一份每日配额，撞上限额会让当天所有下载被拒——
  交给背景预热下一轮（最长约 30 分钟一轮）自然补上，稳定性优先于「这一条也要立刻有解读」。
"""


def _cutoff(hours: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=max(1, int(hours or 12)))


def _parse_dt(s: str) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:  # noqa: BLE001 - 解析不了的时间戳不参与窗口过滤，宁可多带一条也不漏
        return None


async def build_digest(hours: int = 12) -> dict[str, Any]:
    from .realtime_messages import list_realtime_messages
    from .research_wire import fetch_research_wire_online
    from .zsxq_stream import fetch_stream
    from .metrics_store import get_ai_cache as _get_ai_cache

    since_dt = _cutoff(hours)
    since_iso = since_dt.isoformat()

    # 1) 快讯：无 AI 解读闸，直接给原文
    news_out = [
        {"time": m.created_at, "title": m.title, "content": m.content or ""}
        for m in list_realtime_messages(topic="快讯", since=since_iso, limit=200)
    ]

    # 2) 文章：命中缓存才带解读结果，未命中只给标题 + pending（不现场触发解读）
    articles_out: list[dict[str, Any]] = []
    for m in list_realtime_messages(topic="文章", since=since_iso, limit=100):
        title = (m.title or "").strip()
        content = (m.content or "").strip()
        cache_key = "news:" + _hashlib.sha1(f"{title}\n{content}".encode("utf-8")).hexdigest()[:20]
        cached = _get_ai_cache(cache_key)
        item: dict[str, Any] = {"time": m.created_at, "title": title, "url": m.url or ""}
        if cached:
            item["summary"] = cached.get("summary") or cached.get("one_liner") or ""
            item["df_take"] = cached.get("df_take") or ""
            item["pending"] = False
        else:
            item["pending"] = True
        articles_out.append(item)

    # 3) 研报：海外投行/ZSXQ 源，按 created_at 过滤窗口；命中缓存（按 file_id）才带解读
    try:
        wire = await fetch_research_wire_online(limit=200)
    except Exception:  # noqa: BLE001 - 源不可用不拖垮整个摘要，研报段留空即可
        wire = {"items": []}
    reports_out: list[dict[str, Any]] = []
    for it in (wire.get("items") or []):
        dt = _parse_dt(it.get("created_at") or "")
        if dt is not None and dt < since_dt:
            continue
        fid = str(it.get("file_id") or "").strip()
        cached = _get_ai_cache(fid) if fid else None
        item: dict[str, Any] = {
            "time": it.get("created_at") or it.get("date") or "",
            "title": it.get("title") or "",
        }
        if cached:
            item["summary"] = cached.get("summary") or cached.get("one_liner") or ""
            item["df_take"] = cached.get("df_take") or ""
            item["rating"] = cached.get("rating")
            item["pending"] = False
        else:
            item["pending"] = True
        reports_out.append(item)

    # 4) 机构纪要：知识星球普通帖子，无 AI 解读闸，直接给原文（不透出星球来源，与站内口径一致）
    try:
        stream = await fetch_stream(limit=40)
    except Exception:  # noqa: BLE001
        stream = {"items": []}
    notes_out: list[dict[str, Any]] = []
    for it in (stream.get("items") or []):
        dt = _parse_dt(it.get("create_time") or "")
        if dt is not None and dt < since_dt:
            continue
        notes_out.append({
            "time": it.get("create_time") or it.get("date") or "",
            "title": it.get("title") or "",
            "content": it.get("text") or "",
        })

    return {
        "since": since_iso,
        "hours": hours,
        "news": news_out,
        "articles": articles_out,
        "reports": reports_out,
        "notes": notes_out,
        "counts": {
            "news": len(news_out),
            "articles": len(articles_out),
            "articles_pending": sum(1 for a in articles_out if a.get("pending")),
            "reports": len(reports_out),
            "reports_pending": sum(1 for r in reports_out if r.get("pending")),
            "notes": len(notes_out),
        },
    }
