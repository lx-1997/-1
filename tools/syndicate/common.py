# -*- coding: utf-8 -*-
"""复盘内容分发的共享层：取复盘 → 组标题/正文(markdown & 微信HTML) → 渲染图文卡 → 合规中性化。

被 wx_mp_export.py(微信订阅号草稿箱) 与 headline_pack.py(头条/百家号一稿多发包) 复用。
设计：standalone(只依赖 stdlib + Pillow)，默认打 prod 本机 8300(绕 nginx 前端标识守卫)，
DRY_RUN 友好(只写本地文件不外发)。品牌红线：对外只用 DeepFocus / daocaijing.com，禁「道财经」。
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Optional

SITE = os.getenv("DEEPFOCUS_PUBLIC_BASE_URL", "https://daocaijing.com").strip().rstrip("/")
API_BASE = os.getenv("DEEPFOCUS_API_BASE", "http://127.0.0.1:8300").strip().rstrip("/")
# 经 nginx 取数时带前端标识令牌(df_web_ok 白名单)；直连 8300 则不需要。
FRONT_TOKEN = os.getenv("DEEPFOCUS_FRONT_TOKEN", "").strip()
_UA = "Mozilla/5.0 (compatible; DeepFocusSyndicate/1.0)"
_DISCLAIMER = "本文由 DeepFocus 证据引擎自动生成，仅供研究参考，不构成任何投资建议。"

# 合规中性化：优先用后端单一可信源；脱离后端运行时退回内置精简词表。
try:  # 在 prod 的 backend 目录下能 import 到
    from deepfocus_api.compliance import neutralize_text  # type: ignore
except Exception:  # pragma: no cover
    _MAP = [("强烈建议买入", "偏多关注"), ("建议买入", "偏多关注"), ("建议卖出", "偏空规避"),
            ("满仓", "偏多"), ("加仓", "偏多"), ("减仓", "偏空"), ("清仓", "偏空规避"),
            ("买入", "偏多"), ("卖出", "偏空"), ("必涨", "或有上行"), ("必跌", "或有下行"),
            ("稳赚不赔", "存在不确定性"), ("稳赚", "存在机会"), ("翻倍", "弹性较大"),
            ("暴涨", "明显上行"), ("暴跌", "明显下行"), ("目标价", "观察价位")]

    def neutralize_text(s: str) -> str:
        out = s or ""
        for bad, good in _MAP:
            out = out.replace(bad, good)
        return out


def fetch_review(date: Optional[str] = None) -> dict[str, Any]:
    """取复盘 JSON。date=None→最新一期(/api/review/today)，否则 /api/review/{date}。"""
    if date:
        url = f"{API_BASE}/api/review/{date}"
    else:
        url = f"{API_BASE}/api/review/today"
    if FRONT_TOKEN:
        url += ("&" if "?" in url else "?") + f"w={FRONT_TOKEN}"
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    rv = data.get("review") if isinstance(data, dict) else None
    if not rv or not rv.get("date"):
        raise SystemExit(f"无可用复盘（{url} 返回 exists=false 或空）")
    return rv


def _fmt_pct(v: Any) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "—"
    return f"{'+' if f > 0 else ''}{f:.2f}%"


def backlink(review: dict) -> str:
    return f"{SITE}/review/{review.get('date')}"


def _ctx(review: dict) -> dict:
    nar = review.get("narrative") or {}
    label = review.get("session_label") or "收盘复盘"
    one = neutralize_text(str(nar.get("one_liner") or "")).strip()
    return {"date": review.get("date") or "", "label": label, "one": one, "nar": nar}


def title_candidates(review: dict) -> list[str]:
    """5 个标题候选（人工挑一个用）。全部中性、无诱导、无品牌红线词。"""
    c = _ctx(review)
    d, label, one = c["date"], c["label"], c["one"]
    md = d[5:].replace("-", "月") + "日" if len(d) == 10 else d
    idx = (review.get("indices") or [{}])[0]
    idx_bit = f"{idx.get('name')} {_fmt_pct(idx.get('pct'))}" if idx.get("name") else ""
    cands = [
        f"{md} A股{label}｜{one}" if one else f"{md} A股{label}",
        f"A股复盘：{one}" if one else f"{md} A股{label}复盘",
        f"{md}收评｜{idx_bit}，板块与资金一图看清" if idx_bit else f"{md} A股{label}：板块与资金复盘",
        f"今日A股怎么走？{md}大盘/板块/资金全复盘",
        f"{md} A股{label}：我们提前发现的资讯与今日验证",
    ]
    seen, out = set(), []
    for t in cands:
        t = neutralize_text(t).strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t[:30])  # 头条/公众号标题宜短
    return out[:5]


def _sections(review: dict) -> list[tuple[str, str]]:
    """(小标题, 正文) 段落列表，正文已中性化。"""
    c = _ctx(review)
    nar = c["nar"]
    out: list[tuple[str, str]] = []
    # 大盘指数
    idxs = review.get("indices") or []
    if idxs:
        rows = "；".join(f"{i.get('name')} {i.get('close')}（{_fmt_pct(i.get('pct'))}）" for i in idxs[:5])
        out.append(("大盘指数", rows + "。"))
    br = review.get("breadth") or {}
    if br.get("total"):
        out.append(("市场广度", f"全市场 {br.get('total')} 家：上涨 {br.get('advancers')} / 下跌 {br.get('decliners')}，"
                                f"涨停 {br.get('limit_up')} 家、跌停 {br.get('limit_down')} 家。"))
    sec = review.get("sectors") or {}
    bits = []
    for lab, key in (("领涨", "top"), ("领跌", "bottom")):
        items = sec.get(key) or []
        if items:
            bits.append(f"{lab}：" + "、".join(f"{s.get('name')}({_fmt_pct(s.get('pct'))})" for s in items[:5]))
    if bits:
        out.append(("板块脉络", "；".join(bits) + "。"))
    edges = review.get("our_edge") or []
    if edges:
        lines = []
        for e in edges[:6]:
            lines.append(f"· {e.get('name')}（{e.get('theme') or e.get('kind')}）{_fmt_pct(e.get('pct'))}："
                         + neutralize_text(str(e.get('evidence') or ''))[:80])
        out.append(("我们提前发现了什么", "\n".join(lines)))
    for lab, key in (("市场怎么走", "market"), ("板块在交易什么", "sectors"),
                     ("资金动向", "funds"), ("明日关注", "tomorrow")):
        t = neutralize_text(str(nar.get(key) or "")).strip()
        if t:
            out.append((lab, t))
    return out


def body_markdown(review: dict) -> str:
    """头条/百家号/雪球用的 markdown 正文（末尾带回站链接 + 免责）。"""
    c = _ctx(review)
    parts = [f"# {c['date']} A股{c['label']}", ""]
    if c["one"]:
        parts += [f"> {c['one']}", ""]
    for h, body in _sections(review):
        parts += [f"## {h}", body, ""]
    parts += ["---", f"完整复盘与个股证据速判：{backlink(review)}", "", f"_{_DISCLAIMER}_"]
    return "\n".join(parts)


def body_html_wechat(review: dict) -> str:
    """微信图文正文（内联样式的安全 HTML 子集）。"""
    c = _ctx(review)
    css_h = "font-size:17px;color:#10b981;margin:18px 0 8px;font-weight:600;"
    css_p = "font-size:15px;line-height:1.8;color:#333;margin:0 0 10px;white-space:pre-wrap;"
    html = []
    if c["one"]:
        html.append(f'<p style="font-size:16px;font-weight:600;color:#111;margin:0 0 14px">{_esc(c["one"])}</p>')
    for h, body in _sections(review):
        html.append(f'<p style="{css_h}">{_esc(h)}</p><p style="{css_p}">{_esc(body)}</p>')
    html.append(f'<p style="font-size:13px;color:#888;margin-top:18px">完整复盘与个股证据速判见：'
                f'{_esc(backlink(review))}</p>')
    html.append(f'<p style="font-size:12px;color:#aaa;margin-top:8px">{_esc(_DISCLAIMER)}</p>')
    return "".join(html)


def _esc(s: Any) -> str:
    import html as _h
    return _h.escape(str(s if s is not None else ""))


# --------------------------------------------------------------------------- #
# 图文卡渲染（PIL）
# --------------------------------------------------------------------------- #
_FONT_CANDIDATES = [
    os.getenv("DEEPFOCUS_CARD_FONT", "").strip(),
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/wenquanyi/wqy-zenhei/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",  # 无 CJK 会出方块，但不崩
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
]


def _font(size: int):
    from PIL import ImageFont
    for p in _FONT_CANDIDATES:
        if p and os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _wrap(draw, text: str, font, max_w: int) -> list[str]:
    lines, cur = [], ""
    for ch in str(text):
        if ch == "\n":
            lines.append(cur); cur = ""; continue
        test = cur + ch
        if draw.textlength(test, font=font) <= max_w:
            cur = test
        else:
            lines.append(cur); cur = ch
    if cur:
        lines.append(cur)
    return lines


def render_cards(review: dict, outdir: str) -> list[str]:
    """渲染 4 张图文卡(1080x1440 暗色)：封面 / 大盘 / 板块·资金 / 我们提前发现·明日。返回路径。"""
    from PIL import Image, ImageDraw
    os.makedirs(outdir, exist_ok=True)
    c = _ctx(review)
    W, H, M = 1080, 1440, 80
    bg, fg, dim, accent = (11, 14, 17), (230, 232, 235), (139, 147, 155), (16, 185, 129)
    secs = _sections(review)
    cover_sub = c["one"] or "大盘 × 板块 × 资金 × 我们提前发现的资讯"
    pages = [("封面", f"{c['date']} A股{c['label']}", cover_sub, True)]
    # 把 sections 两两打包成 3 张内容卡
    chunks = [secs[i:i + 2] for i in range(0, len(secs), 2)][:3]
    for ch in chunks:
        pages.append(("内容", None, ch, False))

    paths = []
    for i, page in enumerate(pages):
        img = Image.new("RGB", (W, H), bg)
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, 14, H], fill=accent)
        d.text((M, 60), "◆ DeepFocus 投研", font=_font(34), fill=accent)
        y = 150
        if page[3]:  # 封面
            for ln in _wrap(d, page[1], _font(64), W - 2 * M):
                d.text((M, y), ln, font=_font(64), fill=fg); y += 86
            y += 30
            for ln in _wrap(d, page[2], _font(40), W - 2 * M):
                d.text((M, y), ln, font=_font(40), fill=dim); y += 58
        else:
            for h, body in page[2]:
                d.text((M, y), h, font=_font(44), fill=accent); y += 64
                for ln in _wrap(d, body, _font(34), W - 2 * M):
                    if y > H - 200:
                        break
                    d.text((M, y), ln, font=_font(34), fill=fg); y += 48
                y += 24
        d.text((M, H - 120), f"{c['date']} · daocaijing.com", font=_font(28), fill=dim)
        d.text((M, H - 75), "仅供研究参考，不构成投资建议", font=_font(26), fill=dim)
        p = os.path.join(outdir, f"card_{i+1}.png")
        img.save(p, "PNG")
        paths.append(p)
    return paths
