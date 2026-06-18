"""资讯内容过滤：在「入库之前」对竞品「斧头财信 / futoucaixin」内容做两档处理。

两档语义（与运营决策一致）：
1) **广告 → 丢弃**：明显的引流广告（如「微信群 + 客服微信/官网 futoucaixin/倒卖渠道」），
   `block_reason` 命中即不入库、不广播、不召回。
2) **正经研报夹带竞品域名 → 隐去后保留**：内容有价值、只是 PDF/图片链接挂在竞品域名
   `futoucaixin.cn` 上——`scrub` 把竞品域名/品牌字眼抹掉但保留文章本身。

所有快讯/文章（DAO 桥接、手动推送、数据源同步）都过这道闸。大小写不敏感 + 去空格匹配
（兼容 "fu tou" / "斧 头" 拆字绕过）。可用 env 追加屏蔽词/广告词，运营无需改代码：
    DEEPFOCUS_NEWS_BANNED_TERMS   追加「竞品品牌/域名」词（命中→参与判定与抹除）
    DEEPFOCUS_NEWS_AD_MARKERS     追加「广告特征」词（与竞品词同现→整条丢弃）
"""
from __future__ import annotations

import os
import re
from typing import Optional, Tuple

# 竞品品牌/域名词（命中即「需处理」：要么丢广告、要么抹域名保留）。用户点名：斧头 / futou。
_DEFAULT_BANNED: Tuple[str, ...] = (
    "futoucaixin", "斧头财信", "斧头财经", "斧头", "futou", "fǔtóu",
)
# 广告特征词：与竞品词「同现」即判定为引流广告 → 整条丢弃（不误伤只夹带域名的研报）。
_DEFAULT_AD_MARKERS: Tuple[str, ...] = (
    "微信群", "客服微信", "加微信", "扫码加", "扫码进", "二维码", "倒卖渠道",
    "认准我们", "认准官网", "唯一官网", "加群", "进群",
)
# 纯广告标题（本身就是引流标题，无需正文同现）。
_AD_TITLES: Tuple[str, ...] = ("微信群", "加群", "进群", "客服微信")

_WS_RE = re.compile(r"\s+")
# 竞品域名 URL（带/不带 scheme），整段抹除
_URL_RE = re.compile(r"(https?://)?[\w.-]*futoucaixin\.cn[^\s)）」』】\]]*", re.I)


def _env_terms(var: str) -> list:
    raw = os.getenv(var, "") or ""
    return [w.strip().lower() for w in re.split(r"[,，;；\n]", raw) if w.strip()]


def banned_terms() -> list:
    """竞品词（默认 + env 追加），小写去重。"""
    out: list = []
    for w in [*(d.lower() for d in _DEFAULT_BANNED), *_env_terms("DEEPFOCUS_NEWS_BANNED_TERMS")]:
        if w and w not in out:
            out.append(w)
    return out


def ad_markers() -> list:
    out: list = []
    for w in [*(d.lower() for d in _DEFAULT_AD_MARKERS), *_env_terms("DEEPFOCUS_NEWS_AD_MARKERS")]:
        if w and w not in out:
            out.append(w)
    return out


def _contains_any(hay: str, hay_nospace: str, terms: list) -> Optional[str]:
    for w in terms:
        if not w:
            continue
        if w in hay or _WS_RE.sub("", w) in hay_nospace:
            return w
    return None


def block_reason(title: str, content: str = "") -> Optional[str]:
    """是否「整条丢弃」——仅针对引流广告：竞品词 + 广告特征同现，或本身是纯广告标题。
    只夹带竞品域名的正经研报不在此丢弃（交给 scrub 抹域名保留）。返回原因或 None。"""
    t = (title or "").strip().lower()
    if _WS_RE.sub("", t) in [a.lower() for a in _AD_TITLES]:
        return "ad-title"
    hay = f"{title or ''}\n{content or ''}".lower()
    if not hay.strip():
        return None
    hay_ns = _WS_RE.sub("", hay)
    comp = _contains_any(hay, hay_ns, banned_terms())
    if not comp:
        return None
    mark = _contains_any(hay, hay_ns, ad_markers())
    if mark:
        return f"ad:{comp}+{mark}"
    return None  # 有竞品词但无广告特征 → 不丢，交给 scrub


def scrub(title: str, content: str = "", url: str = "") -> Tuple[str, str, str, bool]:
    """抹掉**可见正文/标题**里的竞品域名/品牌字眼，保留文章本身。

    ⚠️只动用户可见的 title/content（诉求是「内容出现 futou/斧头」）；结构化 `url` 原文链接**不在此处理**——
    聚合源(DAO财经/futoucaixin)几乎所有文章 url 都挂该域名，批量清空会让全部文章丢「原文」跳转，
    属另一个需单独决策的范围。返回 (title, content, url, changed)。"""
    def _clean(s: str) -> str:
        if not s:
            return s
        out = _URL_RE.sub("[链接已隐藏]", s)
        for w in ("futoucaixin", "斧头财信", "斧头财经"):
            out = re.sub(re.escape(w), "", out, flags=re.I)
        return out
    nt, nc = _clean(title or ""), _clean(content or "")
    changed = (nt != (title or "")) or (nc != (content or ""))
    return nt, nc, (url or ""), changed


def should_block(title: str, content: str = "") -> bool:
    return block_reason(title, content) is not None
