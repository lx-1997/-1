"""名人观点：名人头像墙 → 各自的观点条目（图片 / 文字 / 可播放语音 / 出处）。

与「人物专题」(people_voices) 的关键差异：
1. **可插拔数据源**：观点条目不再写死单一来源（人物专题靠 Google News，在生产被墙取空）。
   这里把来源抽象成「适配器」(SOURCES)：
     - ``curated``：运营在配置/看板里录入的观点（图文 + 语音），即时可用、生产可达；
     - ``zsxq``：从知识星球某个「名人观点」星球按名字检索（群 ID 可配，给了再开）；
     - 以后要接微博/公众号/自有内容只需再加一个适配器、登记进 SOURCES。
2. **富媒体条目**：每条观点带 body(正文) + image_urls(图片) + audio_url(可播放语音)。

设计沿用 people_voices / community_config 的成熟做法：真实取数 → 失败优雅降级
（返回缓存或空 + 结构化告警，而不是 5xx），配置/媒体落本地（运营可改、零发版）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import struct
import time
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import httpx

from .schemas import (
    CelebrityProfile,
    CelebrityViewComment,
    CelebrityViewItem,
    CelebrityViewsResponse,
    DataQuality,
)
from .shared_utils import dedupe, safe_error, utc_now_iso

logger = logging.getLogger("deepfocus.celebrity")

CST = timezone(timedelta(hours=8))

_DIR = Path(__file__).resolve().parents[1]  # backend/
_CFG_PATH = _DIR / ".celebrity_views.json"
_MEDIA_DIR = _DIR / "celebrity_media"

CACHE_TTL_SECONDS = int(os.getenv("DEEPFOCUS_CELEBRITY_CACHE_TTL", "300"))
MAX_ITEMS_PER_CELEB = int(os.getenv("DEEPFOCUS_CELEBRITY_MAX_ITEMS", "50"))

# 同机 Node 研报工作台地址（知识星球检索经它代理；与 research_wire 一致）
_WORKBENCH_BASE = f"http://127.0.0.1:{os.getenv('RESEARCH_WORKBENCH_INTERNAL_PORT', '3927').strip()}"

# 允许上传/引用的媒体类型（图片 + 语音）
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_AUDIO_SUFFIXES = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus"}
_MEDIA_SUFFIXES = _IMAGE_SUFFIXES | _AUDIO_SUFFIXES

SAMPLE_AUDIO_NAME = "sample-voice.wav"


# ============================ 默认配置 / 名人花名册 ============================
# 默认花名册为「投资大佬」，姓名/职务/简介均为公开事实；observation 条目仅录入运营核实过的
# 公开观点（或下面这条明确标注的「示例」占位），绝不由系统编造名人言论。
_DEFAULT: dict[str, Any] = {
    "enabled": True,
    # 启用的数据源（按顺序合并）。curated=运营录入(即时)；zsxq=知识星球帖子(正文+图片)。
    "sources": ["zsxq", "curated"],
    # 全局后备星球 ID（名人未单独配 zsxq_group 时用）；各名人也可在 items 同级配 zsxq_group/zsxq_keyword。
    "zsxq_group": "",
    # 名人观点用的知识星球登录态(cookie 串，如 "zsxq_access_token=...")。留空则复用研报的 cookie(env/override)。
    # 注意：是机密，存运行时配置文件(.celebrity_views.json，已 gitignore)，绝不写进源码。
    "zsxq_cookie": "",
    "zsxq_max_per_celeb": 40,
    "disclaimer": "名人观点为公开信息聚合或示例展示，不代表本平台立场，不构成任何投资建议，请独立判断。",
    "celebrities": [
        {
            "id": "honghao",
            "name": "洪灝",
            "en_name": "Hong Hao",
            "role": "首席经济学家 / 宏观策略",
            "org": "思睿集团",
            "image": "/people/honghao.jpg",
            "avatar": "📈",
            "monogram": "HH",
            "accent": "#2563eb",
            "bio": "知名宏观策略分析师，长期跟踪全球流动性、大类资产与中国市场周期，观点在投资圈影响广泛。",
            "topics": ["宏观策略", "大类资产", "流动性", "周期"],
            "why_it_matters": "其对周期与大类资产的判断，是观察宏观拐点与风险偏好的重要参照。",
            # 洪灝的知识星球：整库即其本人观点，留空 keyword=拉最新帖子；可填 keyword 做主题检索。
            "zsxq_group": "88885882121542",
            "zsxq_keyword": "",
            "items": [],
        },
    ],
}

# 运营可改写的顶层标量字段（白名单 + 长度上限，防脏串）。zsxq_cookie 是机密、长度放宽。
_TEXT_FIELDS = {"disclaimer": 200, "zsxq_group": 40, "zsxq_cookie": 4000}


# ============================ 配置读写 ============================
def get_config() -> dict[str, Any]:
    """读取配置：本地文件覆盖默认值（缺字段回退默认）。"""
    cfg = json.loads(json.dumps(_DEFAULT))  # 深拷贝默认
    try:
        if _CFG_PATH.exists():
            saved = json.loads(_CFG_PATH.read_text("utf-8"))
            if isinstance(saved, dict):
                for k in ("enabled", "sources", "zsxq_group", "zsxq_cookie", "zsxq_max_per_celeb",
                          "disclaimer", "celebrities"):
                    if k in saved:
                        cfg[k] = saved[k]
    except Exception as exc:  # noqa: BLE001 - 文件缺失/损坏按默认处理
        logger.warning("读取名人观点配置失败：%s", exc)
    cfg["enabled"] = bool(cfg.get("enabled", True))
    if not isinstance(cfg.get("sources"), list) or not cfg["sources"]:
        cfg["sources"] = ["curated"]
    if not isinstance(cfg.get("celebrities"), list):
        cfg["celebrities"] = []
    return cfg


def set_config(updates: dict[str, Any]) -> dict[str, Any]:
    """写入配置（白名单字段）。celebrities/sources 支持整体替换，运营据此增删名人与观点条目。"""
    cfg = get_config()
    out = {
        "enabled": cfg["enabled"],
        "sources": cfg["sources"],
        "zsxq_group": cfg["zsxq_group"],
        "zsxq_cookie": cfg.get("zsxq_cookie", ""),
        "zsxq_max_per_celeb": cfg["zsxq_max_per_celeb"],
        "disclaimer": cfg["disclaimer"],
        "celebrities": cfg["celebrities"],
    }
    if "enabled" in updates:
        out["enabled"] = bool(updates["enabled"])
    if isinstance(updates.get("sources"), list):
        clean = [str(s).strip() for s in updates["sources"] if str(s).strip() in SOURCES]
        if clean:
            out["sources"] = clean
    if isinstance(updates.get("zsxq_max_per_celeb"), int):
        out["zsxq_max_per_celeb"] = max(1, min(int(updates["zsxq_max_per_celeb"]), 30))
    for field, limit in _TEXT_FIELDS.items():
        if field in updates and isinstance(updates[field], str):
            out[field] = updates[field].strip()[:limit]
    if isinstance(updates.get("celebrities"), list):
        out["celebrities"] = _sanitize_celebrities(updates["celebrities"])
    try:
        _CFG_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), "utf-8")
        _CACHE.clear()  # 配置变更立即失效缓存
    except Exception as exc:  # noqa: BLE001
        logger.warning("写入名人观点配置失败：%s", exc)
    return get_config()


def _sanitize_celebrities(raw: list[Any]) -> list[dict[str, Any]]:
    """清洗运营提交的名人花名册：限定字段、长度，丢弃无 id/name 的条目。"""
    out: list[dict[str, Any]] = []
    for c in raw[:60]:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("id") or "").strip()[:40]
        name = str(c.get("name") or "").strip()[:40]
        if not cid or not name:
            continue
        items = c.get("items") if isinstance(c.get("items"), list) else []
        out.append({
            "id": cid,
            "name": name,
            "en_name": str(c.get("en_name") or "").strip()[:60],
            "role": str(c.get("role") or "").strip()[:60],
            "org": str(c.get("org") or "").strip()[:80],
            "image": str(c.get("image") or "").strip()[:300],
            "image_credit": str(c.get("image_credit") or "").strip()[:60],
            "avatar": str(c.get("avatar") or "").strip()[:8],
            "monogram": str(c.get("monogram") or "").strip()[:4],
            "accent": str(c.get("accent") or "#2563eb").strip()[:16],
            "bio": str(c.get("bio") or "").strip()[:400],
            "topics": [str(t).strip()[:16] for t in (c.get("topics") or [])[:8] if str(t).strip()],
            "why_it_matters": str(c.get("why_it_matters") or "").strip()[:200],
            "zsxq_group": str(c.get("zsxq_group") or "").strip()[:40],
            "zsxq_keyword": str(c.get("zsxq_keyword") or "").strip()[:60],
            "zsxq_cookie": str(c.get("zsxq_cookie") or "").strip()[:4000],
            "items": [_sanitize_item(it) for it in items[:50] if isinstance(it, dict)],
        })
    return out


def _sanitize_item(it: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(it.get("id") or "").strip()[:64],
        "title": str(it.get("title") or "").strip()[:200],
        "body": str(it.get("body") or "").strip()[:2000],
        "image_urls": [str(u).strip()[:400] for u in (it.get("image_urls") or [])[:9] if str(u).strip()],
        "audio_url": str(it.get("audio_url") or "").strip()[:400],
        "source_name": str(it.get("source_name") or "").strip()[:80],
        "source_url": str(it.get("source_url") or "").strip()[:400],
        "date": str(it.get("date") or "").strip()[:10],
        "tags": [str(t).strip()[:16] for t in (it.get("tags") or [])[:6] if str(t).strip()],
    }


# ============================ 媒体存储（图片 / 语音） ============================
def _safe_media_name(name: str) -> Optional[str]:
    """清洗媒体文件名：仅允许 [A-Za-z0-9._-]，且后缀在图片/语音白名单内。"""
    base = os.path.basename((name or "").strip())
    if not base or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", base):
        return None
    if Path(base).suffix.lower() not in _MEDIA_SUFFIXES:
        return None
    return base


def save_media(name: str, data: bytes) -> Optional[str]:
    """保存上传的图片/语音，返回落盘后的文件名（供 audio_url/image_urls 引用）。"""
    safe = _safe_media_name(name)
    if safe is None or not data:
        return None
    try:
        _MEDIA_DIR.mkdir(exist_ok=True)
        (_MEDIA_DIR / safe).write_bytes(data)
        return safe
    except Exception as exc:  # noqa: BLE001
        logger.warning("保存名人观点媒体失败：%s", exc)
        return None


def media_file(name: str) -> Optional[Path]:
    """按文件名取媒体路径（不存在/非法返回 None）。示例语音缺失时即时生成。"""
    safe = _safe_media_name(name)
    if safe is None:
        return None
    if safe == SAMPLE_AUDIO_NAME:
        _ensure_sample_media()
    p = _MEDIA_DIR / safe
    return p if p.exists() else None


def media_content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp", ".gif": "image/gif",
        ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac",
        ".wav": "audio/wav", ".ogg": "audio/ogg", ".opus": "audio/ogg",
    }.get(suffix, "application/octet-stream")


def _ensure_sample_media() -> None:
    """首次需要时生成一段极短的占位语音（纯标准库合成的正弦音），让语音播放器即时可演示。

    仅作「此处播放语音」的占位，运营/数据源接入后用真实语音替换；不入 git，按需生成。
    """
    p = _MEDIA_DIR / SAMPLE_AUDIO_NAME
    if p.exists():
        return
    try:
        _MEDIA_DIR.mkdir(exist_ok=True)
        framerate = 8000
        duration = 1.2
        freq = 440.0
        frames = bytearray()
        for i in range(int(framerate * duration)):
            # 加一个缓入缓出包络，避免起止爆音
            env = min(1.0, i / 800.0, (framerate * duration - i) / 800.0)
            val = int(env * 9000 * math.sin(2 * math.pi * freq * (i / framerate)))
            frames += struct.pack("<h", val)
        with wave.open(str(p), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(framerate)
            wf.writeframes(bytes(frames))
    except Exception as exc:  # noqa: BLE001
        logger.warning("生成示例语音失败：%s", exc)


# ============================ 数据源适配器 ============================
def _today_cst() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d")


def _stable_id(celeb_id: str, seed: str) -> str:
    digest = hashlib.sha1(f"{celeb_id}:{seed}".encode("utf-8")).hexdigest()[:16]
    return f"cv-{celeb_id}-{digest}"


def _curated_items(celeb: dict[str, Any]) -> list[CelebrityViewItem]:
    """运营录入的观点条目（图文 + 语音），即时可用、生产可达。"""
    out: list[CelebrityViewItem] = []
    today = _today_cst()
    for raw in (celeb.get("items") or []):
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        body = str(raw.get("body") or "").strip()
        if not title and not body:
            continue
        date = (str(raw.get("date") or "").strip() or today)[:10]
        item_id = str(raw.get("id") or "").strip() or _stable_id(celeb["id"], title or body[:24])
        out.append(CelebrityViewItem(
            id=item_id if item_id.startswith("cv-") else _stable_id(celeb["id"], item_id),
            title=title or (body[:40] + ("…" if len(body) > 40 else "")),
            body=body,
            image_urls=[str(u).strip() for u in (raw.get("image_urls") or []) if str(u).strip()][:9],
            audio_url=str(raw.get("audio_url") or "").strip(),
            source_name=str(raw.get("source_name") or "运营录入").strip(),
            source_url=str(raw.get("source_url") or "").strip(),
            source_type="curated",
            published_at=None,
            reported_date=date,
            tags=[str(t).strip() for t in (raw.get("tags") or []) if str(t).strip()][:6],
            importance_score=60,
        ))
    return out


def _comment_to_model(c: Any) -> Optional[CelebrityViewComment]:
    """工作台透出的一条评论 → 模型；空评论(无文字无作者)丢弃。"""
    if not isinstance(c, dict):
        return None
    author = str(c.get("author") or "").strip()[:40]
    text = str(c.get("text") or "").strip()[:2000]
    if not author and not text:
        return None
    return CelebrityViewComment(
        author=author,
        text=text,
        create_time=str(c.get("create_time") or "").strip(),
        likes_count=max(0, int(c.get("likes_count") or 0)),
        sticky=bool(c.get("sticky")),
        reply_to=str(c.get("reply_to") or "").strip()[:40],
    )


def _topic_to_item(celeb: dict[str, Any], t: dict[str, Any]) -> Optional[CelebrityViewItem]:
    text = str(t.get("text") or "").strip()
    images = [str(u).strip() for u in (t.get("images") or []) if str(u).strip()][:9]
    image_fulls = [str(u).strip() for u in (t.get("image_fulls") or []) if str(u).strip()][:9]
    if not text and not images:
        return None
    first_line = (text.split("\n", 1)[0]).strip()
    title = (first_line[:60] + ("…" if len(first_line) > 60 else "")) if first_line else "图片动态"
    ct = str(t.get("create_time") or "").strip()
    tags: list[str] = ["精华"] if t.get("digested") else []
    comments = [m for m in (_comment_to_model(c) for c in (t.get("comments") or [])[:10]) if m is not None]
    return CelebrityViewItem(
        id=_stable_id(celeb["id"], str(t.get("topicId") or title)),
        title=title,
        body=text,
        image_urls=images,
        image_fulls=image_fulls or images,  # 缺原图则回退用小图
        audio_url="",
        source_name=str(t.get("author") or "知识星球").strip() or "知识星球",
        source_url=str(t.get("url") or "").strip(),
        source_type="zsxq",
        published_at=ct or None,
        reported_date=(ct[:10] or None),
        tags=tags,
        importance_score=70 if t.get("digested") else 58,
        topic_id=str(t.get("topicId") or "").strip(),
        comments=comments,
        comments_count=max(int(t.get("comments_count") or 0), len(comments)),
    )


async def _zsxq_topics(
    celeb: dict[str, Any], *, group: str, max_n: int, cookie: str = "", end_time: str = "",
) -> dict[str, Any]:
    """从知识星球某星球检索该名人的【帖子(topics)】——正文 + 图片(已签名URL) + 作者/时间。

    群 ID 优先取名人自带 zsxq_group，否则用全局后备；keyword 留空=拉该星球最新帖子。
    end_time 作游标：给定 → 从该时间点往更早翻（「加载更早」）；否则从最新开始。
    返回 {items, next_before(下一页游标), has_more}。复用同机 Node 工作台 /api/search-topics（它签名+兜底 cookie）。
    """
    g = str(celeb.get("zsxq_group") or group or "").strip()
    if not g:
        return {"items": [], "next_before": "", "has_more": False}
    keyword = str(celeb.get("zsxq_keyword") or "").strip()
    # searchPages 给足上限(每页20)，循环命中 resultLimit 即提前停；用于回溯更早历史。
    payload: dict[str, Any] = {"group": g, "keyword": keyword, "resultLimit": max_n, "searchPages": 6}
    if str(end_time or "").strip():
        payload["endTime"] = str(end_time).strip()
    # cookie 优先级：名人自带 > 名人观点全局 cookie > 研报的 override/env(auth_payload) > 工作台 env
    ck = str(celeb.get("zsxq_cookie") or cookie or "").strip()
    if ck:
        payload["cookie"] = ck
    else:
        from .research_wire import auth_payload  # 局部引入避免循环导入
        payload.update(auth_payload())
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            resp = await client.post(f"{_WORKBENCH_BASE}/api/search-topics", json=payload, timeout=60)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(safe_error(exc)) from exc
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(str(data.get("error"))[:160])

    out: list[CelebrityViewItem] = []
    for t in (data.get("items") or [])[:max_n]:
        if isinstance(t, dict):
            it = _topic_to_item(celeb, t)
            if it is not None:
                out.append(it)
    return {
        "items": out,
        "next_before": str(data.get("nextEndTime") or "").strip(),
        "has_more": bool(data.get("hasMore")),
    }


async def _zsxq_items(
    celeb: dict[str, Any], *, group: str, max_n: int, cookie: str = "",
) -> list[CelebrityViewItem]:
    """墙/聚合用：只取首屏 items（丢弃分页游标）。"""
    return (await _zsxq_topics(celeb, group=group, max_n=max_n, cookie=cookie))["items"]


async def fetch_celebrity_more(celeb_id: str, *, before: str = "", limit: int = 20) -> Optional[dict[str, Any]]:
    """「加载更早」：取该名人 before 时间点之前的更早帖子。未知名人返回 None。"""
    cfg = get_config()
    celeb = next((c for c in cfg.get("celebrities", []) if isinstance(c, dict) and c.get("id") == celeb_id), None)
    if celeb is None:
        return None
    n = max(1, min(int(limit or 20), 40))
    try:
        res = await _zsxq_topics(
            celeb, group=cfg.get("zsxq_group", ""), max_n=n,
            cookie=cfg.get("zsxq_cookie", ""), end_time=before,
        )
    except Exception:  # noqa: BLE001 - 游标异常/上游故障 → 优雅停在「没有更多」，绝不 500
        return {"items": [], "next_before": "", "has_more": False}
    return {
        "items": [it.model_dump() for it in res["items"]],
        "next_before": res["next_before"],
        "has_more": res["has_more"],
    }


async def fetch_topic_comments(celeb_id: str, topic_id: str, *, limit: int = 100) -> Optional[dict[str, Any]]:
    """「加载全部评论」：拉某帖的完整评论列表（列表接口随帖只带前几条预览）。

    未知名人返回 None(→404)；上游故障返回 {comments:[], error:...} 而不是 5xx，前端可提示重试。
    cookie 优先级与 _zsxq_topics 一致：名人自带 > 名人观点全局 > 研报 override/env。
    """
    cfg = get_config()
    celeb = next((c for c in cfg.get("celebrities", []) if isinstance(c, dict) and c.get("id") == celeb_id), None)
    if celeb is None:
        return None
    tid = re.sub(r"\D", "", str(topic_id or ""))
    if not tid:
        return {"comments": [], "count": 0, "has_more": False, "error": "缺少帖子 ID"}
    payload: dict[str, Any] = {"topicId": tid, "limit": max(1, min(int(limit or 100), 300))}
    ck = str(celeb.get("zsxq_cookie") or cfg.get("zsxq_cookie") or "").strip()
    if ck:
        payload["cookie"] = ck
    else:
        from .research_wire import auth_payload  # 局部引入避免循环导入
        payload.update(auth_payload())
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            resp = await client.post(f"{_WORKBENCH_BASE}/api/topic-comments", json=payload, timeout=60)
            resp.raise_for_status()
            data = resp.json()
        if isinstance(data, dict) and data.get("error"):
            raise RuntimeError(str(data.get("error"))[:160])
    except Exception as exc:  # noqa: BLE001 - 上游故障优雅降级，绝不 500
        logger.warning("celebrity topic comments failed: %s", safe_error(exc))
        return {"comments": [], "count": 0, "has_more": False, "error": "评论加载失败，请稍后重试"}
    comments = [m.model_dump() for m in (_comment_to_model(c) for c in (data.get("comments") or [])) if m is not None]
    return {"comments": comments, "count": len(comments), "has_more": bool(data.get("hasMore"))}


# 数据源登记表：source_id → 取数函数。curated 为同步、zsxq 为异步，统一在 _gather_items 里适配。
SOURCES: dict[str, Callable[..., Any]] = {
    "curated": _curated_items,
    "zsxq": _zsxq_items,
}


# ============================ 聚合 / 取数 ============================
_CACHE: dict[str, Any] = {}


def _config_signature(cfg: dict[str, Any]) -> str:
    """配置指纹：配置一变（运营改了名人/条目/数据源）即令缓存失效。"""
    blob = json.dumps(
        {k: cfg.get(k) for k in ("enabled", "sources", "zsxq_group", "zsxq_cookie", "celebrities")},
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def _normalize(value: str) -> str:
    return re.sub(r"[\s\W_]+", "", (value or "").lower())


async def _gather_items(celeb: dict[str, Any], cfg: dict[str, Any], warnings: list[str]) -> list[CelebrityViewItem]:
    """对单个名人，按启用的数据源依次取数，合并 → 去重 → 排序 → 截断。"""
    merged: list[CelebrityViewItem] = []
    for sid in cfg.get("sources", []):
        fn = SOURCES.get(sid)
        if fn is None:
            continue
        try:
            if sid == "curated":
                merged.extend(_curated_items(celeb))
            elif sid == "zsxq":
                merged.extend(await _zsxq_items(
                    celeb, group=cfg.get("zsxq_group", ""),
                    max_n=int(cfg.get("zsxq_max_per_celeb") or 8),
                    cookie=cfg.get("zsxq_cookie", ""),
                ))
        except Exception as exc:  # noqa: BLE001 - 单源失败不拖垮整体
            warnings.append(f"{celeb.get('name')}：数据源「{sid}」暂不可用（{safe_error(exc)}）。")

    # 跨源按标题去重（保留先出现的，即按 sources 顺序优先）
    seen: set[str] = set()
    deduped: list[CelebrityViewItem] = []
    for it in merged:
        key = _normalize(it.title)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(it)
    deduped.sort(key=lambda x: (x.reported_date or "", x.importance_score), reverse=True)
    return deduped[:MAX_ITEMS_PER_CELEB]


def _live_quality(detail: str) -> DataQuality:
    return DataQuality(level="live", label="已聚合", detail=detail, reasons=[])


def _degraded_quality(detail: str) -> DataQuality:
    return DataQuality(level="degraded", label="降级兜底", detail=detail, reasons=[])


def _build_profile(celeb: dict[str, Any], items: list[CelebrityViewItem], warnings: list[str]) -> CelebrityProfile:
    latest = items[0].reported_date if items else None
    if items:
        quality = _live_quality("观点条目来自运营录入或知识星球等来源，带出处与时间。")
    else:
        quality = _degraded_quality("暂未录入/取到该名人的观点条目。")
    return CelebrityProfile(
        id=celeb["id"],
        name=celeb["name"],
        en_name=celeb.get("en_name", ""),
        role=celeb.get("role", ""),
        org=celeb.get("org", ""),
        image=celeb.get("image", ""),
        image_credit=celeb.get("image_credit", ""),
        avatar=celeb.get("avatar", ""),
        monogram=celeb.get("monogram", ""),
        accent=celeb.get("accent", "#2563eb"),
        bio=celeb.get("bio", ""),
        topics=list(celeb.get("topics") or []),
        why_it_matters=celeb.get("why_it_matters", ""),
        items=items,
        item_count=len(items),
        latest_date=latest,
        warnings=dedupe(warnings),
        data_quality=quality,
    )


async def fetch_celebrity_views(*, refresh: bool = False) -> CelebrityViewsResponse:
    """聚合全部名人的观点，组装名人观点墙响应（带 TTL 缓存 + 配置指纹失效）。"""
    cfg = get_config()
    sig = _config_signature(cfg)
    now = time.monotonic()

    cached = _CACHE.get("resp")
    if (
        not refresh and cached
        and cached.get("sig") == sig
        and (now - cached.get("at", 0)) < CACHE_TTL_SECONDS
    ):
        resp: CelebrityViewsResponse = cached["resp"].model_copy(deep=True)
        resp.cache_age_seconds = int(now - cached["at"])
        return resp

    if not cfg.get("enabled", True):
        return CelebrityViewsResponse(
            provider="fallback",
            generated_at=utc_now_iso(),
            figures=[],
            total_items=0,
            sources=list(cfg.get("sources") or []),
            warnings=["名人观点模块当前为关闭状态。"],
            data_quality=_degraded_quality("模块已被运营关闭。"),
        )

    warnings: list[str] = []
    profiles: list[CelebrityProfile] = []
    for celeb in cfg.get("celebrities", []):
        if not isinstance(celeb, dict) or not celeb.get("id"):
            continue
        per_warn: list[str] = []
        items = await _gather_items(celeb, cfg, per_warn)
        warnings.extend(per_warn)
        profiles.append(_build_profile(celeb, items, per_warn))

    total_items = sum(p.item_count for p in profiles)
    live_count = sum(1 for p in profiles if p.data_quality.level == "live")
    if profiles and live_count == len(profiles):
        overall = _live_quality("全部名人均有观点条目，带出处与时间。")
    elif live_count == 0:
        overall = _degraded_quality("暂无可展示的名人观点条目，请稍后或联系运营录入。")
    else:
        overall = DataQuality(
            level="degraded", label="部分降级",
            detail=f"{live_count}/{len(profiles)} 位名人有观点条目，其余暂空。", reasons=[],
        )

    resp = CelebrityViewsResponse(
        provider="mixed" if len(cfg.get("sources", [])) > 1 else (cfg.get("sources") or ["curated"])[0],
        generated_at=utc_now_iso(),
        figures=profiles,
        total_items=total_items,
        sources=list(cfg.get("sources") or []),
        cache_age_seconds=0,
        warnings=dedupe(warnings),
        data_quality=overall,
    )
    # 仅当取到了条目才缓存；全空不缓存，便于运营录入后下次即时生效。
    if total_items:
        _CACHE["resp"] = {"at": now, "sig": sig, "resp": resp.model_copy(deep=True)}
    return resp


async def fetch_celebrity(celeb_id: str, *, refresh: bool = False) -> Optional[CelebrityProfile]:
    """单个名人的档案 + 观点（供 AI 综述下钻）。未知 id 返回 None。"""
    resp = await fetch_celebrity_views(refresh=refresh)
    for fig in resp.figures:
        if fig.id == celeb_id:
            return fig
    return None


def celebrity_ids() -> list[str]:
    return [str(c.get("id")) for c in get_config().get("celebrities", []) if c.get("id")]
