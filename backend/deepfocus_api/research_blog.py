"""研报 AI 解读「博客」数据层：把后台已预热的研报解读(run_research_prewarm 产出、
落在 ai_analysis_cache 的结构化解读)系统化成「可读全文 + SEO 友好」的文章流。

与 [[report_share]] 的区别:report_share 是**用户手动点分享才触发**的软墙钓鱼页(只露导语+登录墙);
这里是**机器每天自动生产**的常青博客——把已有解读(零新增算力)直接渲染成公开可读文章,承接
「XX 行业/个股 研报解读/怎么看」长尾搜索,同时给站点补一条 GEO 发现通道。

红线(版权安全,沿用 [[compliance]] / research_vision df_take 既定姿态):
- 只发我方 AI 产出的解读(转化创作),绝不外露研报原文/PDF/原文链接;
- 出处只署「机构类别 + 日期」(海外投行 / 券商),facts(评级/目标价)可列,不逐句复述原文;
- 渲染层过 compliance.neutralize + _page(ai_generated=True)(《AI 标识办法》硬要求)。

数据来源全复用现成件,不新建库:
- research_archive.query()  → 研报条目(id/title/org/date,按 date 倒序)
- metrics_store.get_ai_cache_many() → 已预热的结构化解读(one_liner/summary/bullish/bearish/…)

灰度开关:DEEPFOCUS_RESEARCH_BLOG_ENABLED,默认关(0)。开=公开 /research 博客;关=路由 404。
"""
from __future__ import annotations

import os
import re
from typing import Any, Optional

from . import metrics_store, research_archive
from .seo_pages import _public_source

_FID_RE = re.compile(r"^\d{6,20}$")

# 标题清洗:研报文件名常带日期码 / _原文 / _纪要 / 括号内代码表 / 英文双语原名尾巴 → 干净可读博客标题
_CODE_TAIL = re.compile(r"(20\d{6}|\d{6}_(原文|纪要))\s*$")
_PAREN_CODES = re.compile(r"[（(][^）)]*\d{6}[^）)]*[)）]")
_EN_TAIL = re.compile(r"[-—\s][A-Za-z0-9][A-Za-z0-9 ,.&'/\-]{6,}$")


def blog_enabled() -> bool:
    """研报博客灰度开关(默认关)。关时 /research 路由与 sitemap 收录均不生效。"""
    return os.getenv("DEEPFOCUS_RESEARCH_BLOG_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}


def is_valid_fid(fid: str) -> bool:
    return bool(_FID_RE.match((fid or "").strip()))


def _drop_en_original(t: str) -> str:
    """砍掉「中文标题-English Original Name」里那截英文原名尾巴(研报常见双语标题)。"""
    for sep in ("-", "—", "–", ":", "："):
        i = t.rfind(sep)
        if i > 4:
            tail = t[i + 1:].strip()
            if len(tail) >= 6 and sum(c.isascii() for c in tail) / max(1, len(tail)) >= 0.7:
                return t[:i].strip()
    return t


def clean_title(raw: str) -> str:
    t = " ".join(str(raw or "").split())
    t = _CODE_TAIL.sub("", t).strip()
    t = _PAREN_CODES.sub("", t).strip()
    t = _drop_en_original(t)
    t = _EN_TAIL.sub("", t).strip()
    t = re.sub(r"[_\-—:：]\s*$", "", t).strip()
    return t or "研报速读"


def _org_label(org: str, source_name: str = "") -> str:
    """对外出处署名:海外投行统一「海外投行」,其余走 _public_source(内部源名收敛为 DeepFocus)。"""
    o = (org or "").strip()
    if "投行" in o or "海外" in o:
        return "海外投行"
    return _public_source(source_name or o)


def _is_bloggable(item: dict[str, Any], interp: dict[str, Any]) -> bool:
    """够格上博客:非原文/纪要类,且解读结构完整(有一句话结论 + 至少一组要点/多空)。"""
    title = str(item.get("title") or "")
    if "_原文" in title or "_纪要" in title:
        return False
    if not str(interp.get("one_liner") or interp.get("summary") or "").strip():
        return False
    return bool(interp.get("key_points") or interp.get("bullish") or interp.get("summary"))


def _build_post(item: dict[str, Any], interp: dict[str, Any]) -> dict[str, Any]:
    """归一成渲染层直接可用的 post(标题/出处已清洗;解读原样带过去,字段抽取+中性化在渲染层做)。"""
    fid = str(item.get("file_id") or item.get("id") or "").strip()
    return {
        "fid": fid,
        "title": clean_title(item.get("title") or interp.get("subject") or ""),
        "org": _org_label(item.get("org") or "", interp.get("source_name") or ""),
        "date": str(item.get("date") or "")[:10],
        "interp": interp,
    }


def list_blog_posts(limit: int = 40) -> list[dict[str, Any]]:
    """最近、已预热解读、够格上博客的研报文章流(新→旧)。零新增算力:只读已有缓存,无缓存的跳过。"""
    limit = max(1, min(int(limit or 40), 200))
    try:
        archived = research_archive.query(limit=limit * 4)  # 多取,过滤掉无缓存/不够格的
    except Exception:  # noqa: BLE001
        return []
    by_fid: dict[str, dict[str, Any]] = {}
    for it in archived:
        fid = str(it.get("file_id") or it.get("id") or "").strip()
        if fid and is_valid_fid(fid) and fid not in by_fid:
            by_fid[fid] = it
    caches = metrics_store.get_ai_cache_many(list(by_fid.keys()))
    out: list[dict[str, Any]] = []
    for fid, it in by_fid.items():  # by_fid 保持 query 的 date 倒序
        interp = caches.get(fid)
        if isinstance(interp, dict) and _is_bloggable(it, interp):
            out.append(_build_post(it, interp))
        if len(out) >= limit:
            break
    return out


def get_blog_post(fid: str) -> Optional[dict[str, Any]]:
    """按 file_id 取单篇博客文章;无归档/无解读/不够格 → None。"""
    fid = (fid or "").strip()
    if not is_valid_fid(fid):
        return None
    interp = metrics_store.get_ai_cache(fid)
    if not isinstance(interp, dict):
        return None
    # 归档里找元数据(title/org/date);查不到就用解读里的兜底
    item: dict[str, Any] = {}
    try:
        for a in research_archive.query(limit=2000):
            if str(a.get("file_id") or a.get("id") or "").strip() == fid:
                item = a
                break
    except Exception:  # noqa: BLE001
        item = {}
    if not item:
        item = {"file_id": fid, "title": interp.get("subject") or "研报速读", "org": "", "date": ""}
    if not _is_bloggable(item, interp):
        return None
    return _build_post(item, interp)


def all_fids(limit: int = 300) -> list[str]:
    """sitemap 发现用:已上博客的研报 file_id(新→旧)。"""
    return [p["fid"] for p in list_blog_posts(limit) if p.get("fid")]
