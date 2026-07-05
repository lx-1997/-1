"""研报「AI 解读」的可分享落地页存储层（与文章软墙 [[seo_pages]] 同构）。

⚠️ 红线：研报原文/PDF 是第三方版权，绝不外露；这里分享的是 **DeepFocus 自己产出的 AI 解读**（我们的增值内容），
版权安全、且凸显平台价值。用户在终端看完某篇研报的 AI 解读后点「分享」→ 这里把
【标题 + 机构 + 我们的解读正文】落一条短 id 记录 → 公开页 /report/{id} 软墙只露解读导语 +
「登录看完整解读」深链回终端(?report={id})。

持久化复用 data_store（与 wx_qa / seo_tear_sheet 同表），无需新建库；
id 由内容哈希派生（同一篇解读重复分享 → 同一 URL，天然去重）。
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Optional

from . import data_store
from .shared_utils import utc_now_iso

_NS = "report_share"            # 单条分享记录：key = 短 id
_INDEX_NS = "report_share_index"  # 最近分享索引：固定 key，驱动「更多解读」内链 + sitemap 发现
_INDEX_KEY = "ALL"
_INDEX_CAP = 600                # 索引只留最近这么多条（页面内链/收录足够，控制读放大）
_ID_RE = re.compile(r"^[0-9a-fA-F]{8,40}$")


def _make_id(title: str, summary: str, symbol: str) -> str:
    raw = f"{(symbol or '').strip()}|{(title or '').strip()}|{(summary or '').strip()}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def is_valid_id(rid: str) -> bool:
    """URL 里的 report id 是否合法（防注入/无意义查询）。"""
    return bool(_ID_RE.match((rid or "").strip()))


def save_share(*, title: str, summary: str, source_name: str = "", symbol: str = "") -> dict[str, Any]:
    """落一条可分享的研报解读记录；返回完整记录（含 id）。title/summary 为空 → ValueError。

    调用方（main 路由）须先对 summary 过 compliance + privacy 护栏再传入。"""
    title = " ".join((title or "").split())[:240]
    summary = (summary or "").strip()[:6000]
    if not title or not summary:
        raise ValueError("title 与 summary 均不能为空")
    rid = _make_id(title, summary, symbol)
    rec = {
        "id": rid,
        "title": title,
        "summary": summary,
        "source_name": " ".join((source_name or "").split())[:80],
        "symbol": (symbol or "").strip().upper()[:16],
        "created_at": utc_now_iso(),
    }
    data_store.record(_NS, rid, rec)
    _index_add(rid)
    return rec


def get_share(rid: str) -> Optional[dict[str, Any]]:
    """按 id 取一条分享记录（公开页 + 深链共用）；不存在/非法 → None。"""
    rid = (rid or "").strip()
    if not is_valid_id(rid):
        return None
    rec = data_store.latest(_NS, rid)
    return rec if isinstance(rec, dict) else None


def recent_shares(limit: int = 12) -> list[dict[str, Any]]:
    """最近分享过的研报解读（「更多解读」内链 + 聚合页）。失败 → []。"""
    out: list[dict[str, Any]] = []
    for rid in all_ids(max(1, int(limit)) * 3):
        rec = get_share(rid)
        if rec:
            out.append(rec)
        if len(out) >= limit:
            break
    return out


def all_ids(limit: int = 500) -> list[str]:
    """索引里的全部分享 id（新→旧，sitemap 发现用）。"""
    idx = data_store.latest(_INDEX_NS, _INDEX_KEY)
    ids = idx.get("ids") if isinstance(idx, dict) else None
    return list(ids or [])[: max(1, int(limit))]


def _index_add(rid: str) -> None:
    """把 rid 置顶进最近索引（去重 + 截顶）。读改写，分享低频、竞态可忽略；失败静默。"""
    try:
        idx = data_store.latest(_INDEX_NS, _INDEX_KEY)
        prev = (idx.get("ids") if isinstance(idx, dict) else None) or []
        ids = [rid] + [i for i in prev if i != rid]
        data_store.record(_INDEX_NS, _INDEX_KEY, {"ids": ids[:_INDEX_CAP]})
    except Exception:  # noqa: BLE001  索引更新失败不影响主记录（按 id 直查仍可用）
        pass
