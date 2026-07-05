"""动态社交分享卡（C4）：为每个公开页生成 1200x630 的品牌 OG 图。

微信/微博/AI 链接预览会抓 og:image —— 把它从「所有页同一张 og-cover」升级成「每页一张带标的/结论/日期的卡」，
分享出去的每条链接都成了带回站 URL 的品牌广告。纯 PIL 渲染、进程内缓存、绝不触发重外取（取不到数据就降级成简卡）。
"""
from __future__ import annotations

import io
import os
from typing import Any, Optional

_W, _H = 1200, 630
_BG = (11, 14, 17)
_FG = (230, 232, 235)
_DIM = (139, 147, 155)
_ACCENT = (16, 185, 129)

_FONT_CANDIDATES = [
    os.getenv("DEEPFOCUS_CARD_FONT", "").strip(),
    "/opt/deepfocus/tools/syndicate/fonts/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
]

# 进程内缓存：og 图被社交/爬虫偶发抓取，渲染一次即复用，避免重复 PIL 开销。
_CACHE: dict[str, bytes] = {}
_CACHE_MAX = 512


def _font(size: int):
    from PIL import ImageFont
    for p in _FONT_CANDIDATES:
        if p and os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:  # noqa: BLE001
                continue
    return ImageFont.load_default()


def _wrap(draw, text: str, font, max_w: int, max_lines: int) -> list[str]:
    lines, cur = [], ""
    for ch in str(text):
        if draw.textlength(cur + ch, font=font) <= max_w:
            cur += ch
        else:
            lines.append(cur)
            cur = ch
            if len(lines) >= max_lines:
                break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    return lines


def render_og(title: str, subtitle: str = "", stats: Optional[list[str]] = None,
              badge: str = "DeepFocus 投研", footer: str = "daocaijing.com · 仅供研究参考，不构成投资建议") -> bytes:
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (_W, _H), _BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 16, _H], fill=_ACCENT)
    d.text((72, 60), f"◆ {badge}", font=_font(34), fill=_ACCENT)
    y = 150
    for ln in _wrap(d, title, _font(60), _W - 144, 3):
        d.text((72, y), ln, font=_font(60), fill=_FG)
        y += 80
    if subtitle:
        y += 8
        for ln in _wrap(d, subtitle, _font(34), _W - 144, 2):
            d.text((72, y), ln, font=_font(34), fill=_DIM)
            y += 46
    if stats:
        y = max(y, 430)
        chip = " · ".join(stats[:4])
        for ln in _wrap(d, chip, _font(30), _W - 144, 2):
            d.text((72, y), ln, font=_font(30), fill=(159, 217, 195))
            y += 42
    d.text((72, _H - 64), footer, font=_font(26), fill=_DIM)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def cached(key: str, render_fn) -> bytes:
    """key 命中即复用；未命中渲染并缓存（超量则清空，简单防膨胀）。"""
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    data = render_fn()
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.clear()
    _CACHE[key] = data
    return data


# --------------------------------------------------------------------------- #
# 各页型的卡面（取已缓存数据，取不到则降级简卡，绝不触发重建）
# --------------------------------------------------------------------------- #
def stock_card(ts: dict[str, Any], symbol: str) -> bytes:
    name = str((ts or {}).get("name") or symbol)
    verdict = str((ts or {}).get("overall_verdict") or "")
    title = f"{name}（{symbol}）" if name != symbol else symbol
    sub = f"多维证据速判：{verdict}" if verdict else "多维证据速判"
    stats = []
    if ts:
        if ts.get("price") is not None:
            stats.append(f"{ts.get('currency') or ''} {ts.get('price')}")
        if isinstance(ts.get("confidence"), (int, float)):
            stats.append(f"置信度 {float(ts['confidence']) * 100:.0f}%")
        if ts.get("overall_score") not in (None, ""):
            stats.append(f"综合分 {ts.get('overall_score')}")
    return cached(f"stock:{symbol}:{(ts or {}).get('generated_at','')}",
                  lambda: render_og(title, sub, stats))


def review_card(review: dict[str, Any], date: str) -> bytes:
    label = str((review or {}).get("session_label") or "收盘复盘")
    one = str(((review or {}).get("narrative") or {}).get("one_liner") or "")
    idx = (review or {}).get("indices") or []
    stats = [f"{i.get('name')} {'+' if (i.get('pct') or 0) > 0 else ''}{i.get('pct')}%" for i in idx[:3] if i.get("name")]
    return cached(f"review:{date}:{(review or {}).get('generated_at','')}",
                  lambda: render_og(f"{date} A股{label}", one, stats))


def qa_card(qa: dict[str, Any], slug: str) -> bytes:
    q = str((qa or {}).get("q") or "投研问答")
    return cached(f"qa:{slug}", lambda: render_og(q, "DeepFocus AI 投研问答", []))


def generic_card() -> bytes:
    return cached("generic", lambda: render_og("DeepFocus 投研工作台", "A股复盘 · 个股证据速判 · 投研问答", []))
