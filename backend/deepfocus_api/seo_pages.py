"""公开 SEO / GEO 落地页：每日复盘页 + 个股速判卡页 + 文章软墙页 + 聚合页 + 站点地图 + llms.txt。

与 share_snapshots.py（一次性结论快照）互补：这里是**机器每天自动生产的常青内容**——
- /review/{date}  每个交易日一篇收盘复盘 → 长期积累成可收录页面库
- /stock/{symbol} 个股速判卡落地页 → 承接「XX股票 怎么样/估值」长尾搜索
- /article/{id}   财经资讯软墙落地页（标题+来源+短导语公开可收录，全文需登录）
- /review、/stocks、/articles 聚合页 + /sitemap.xml、/robots.txt、/llms.txt → 给搜索/AI 爬虫一条完整的发现路径

设计要点：
- 页面之间互相内链（复盘→近期复盘、个股→相关热门个股），既是站内推荐也是 SEO 内链。
- 所有用户/数据内容均 html.escape 后注入；薄内容页（数据不足）输出 noindex 且不发结构化数据。
- 结构化数据用单一 JSON-LD @graph：站级 Organization + WebSite(SearchAction) + 面包屑 + 主实体，
  publisher/author 用 @id 互引——这正是 AI 生成式引擎做归因/引用时抓取的实体骨架（GEO）。
- og:image + summary_large_image：微信/微博/AI 分享卡片有大图。
- 站点验证 meta（百度/谷歌/Bing）由环境变量驱动，留空即不输出。
"""
from __future__ import annotations

import html
import json
import os
from typing import Any, Optional

from .compliance import neutralize_text
from .privacy_guard import scrub_internal_text

# 站点绝对地址（sitemap/canonical 用）；默认生产域名，可被环境变量覆盖。
BASE_URL = (os.getenv("DEEPFOCUS_PUBLIC_BASE_URL", "https://daocaijing.com").strip().rstrip("/")
            or "https://daocaijing.com")
APP_URL = os.getenv("DEEPFOCUS_PUBLIC_APP_URL", "/").strip() or "/"
DEFAULT_OG_IMAGE = (os.getenv("DEEPFOCUS_OG_IMAGE", "").strip() or f"{BASE_URL}/og-cover.png")

# 站级实体根（GEO 归因骨架）：所有页面的 publisher/author 用 @id 互引，引擎只需解析一次即知作者方。
ORG_NAME = "DeepFocus 金融数据"
ORG_ID = f"{BASE_URL}/#org"
WEBSITE_ID = f"{BASE_URL}/#website"
# 官方可验证社媒账号（sameAs 是 AI 引擎归因的最高杠杆信号）；站长提供后用逗号分隔填入环境变量。
# 品牌红线：禁出现「道财经」字样，对外只用 daocaijing.com。
ORG_SAME_AS = [s.strip() for s in os.getenv("DEEPFOCUS_ORG_SAMEAS", "").split(",") if s.strip()]

# 站点验证（留空即不输出对应 meta）：百度站长 / Google Search Console / Bing Webmaster。
_VERIFY_METAS = {
    "baidu-site-verification": os.getenv("DEEPFOCUS_BAIDU_SITE_VERIFICATION", "").strip(),
    "google-site-verification": os.getenv("DEEPFOCUS_GOOGLE_SITE_VERIFICATION", "").strip(),
    "msvalidate.01": os.getenv("DEEPFOCUS_BING_SITE_VERIFICATION", "").strip(),
    "sogou_site_verification": os.getenv("DEEPFOCUS_SOGOU_SITE_VERIFICATION", "").strip(),
    "360-site-verification": os.getenv("DEEPFOCUS_360_SITE_VERIFICATION", "").strip(),
}

# 备案号（中国机房 SEO 信任 + 合规）：留空即不显示。
ICP_BEIAN = os.getenv("DEEPFOCUS_ICP_BEIAN", "").strip()

_SIGNAL_LABEL = {
    "bullish": ("▲ 偏多", "#10b981"),
    "bearish": ("▼ 偏空", "#ef4444"),
    "neutral": ("◆ 中性", "#8b939b"),
    "insufficient": ("· 数据不足", "#5b6470"),
}

_DISCLAIMER = "本页内容由 DeepFocus 证据引擎自动生成，仅供研究参考，不构成任何投资建议。"


def _esc(value: Any) -> str:
    return html.escape(str(value)) if value is not None else ""


def _fmt_pct(value: Any) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{'+' if v > 0 else ''}{v:.2f}%"


def _num(value: Any) -> float:
    """上游 pct/close 偶发字符串（如 "1.2%"），涨跌判色前强转，防 str>int TypeError 拖垮整页。"""
    try:
        return float(str(value).rstrip("%")) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _iso(value: Any) -> str:
    """尽量归一成 ISO8601；纯日期(YYYY-MM-DD)补 T00:00:00Z，已带 T 的原样返回，空串返回空。"""
    s = str(value or "").strip()
    if not s:
        return ""
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return f"{s}T00:00:00Z"
    return s


def _compact(value: Any) -> Any:
    """递归去掉空值（None / "" / [] / {}），避免空字段污染 / 破坏结构化数据校验；保留 0 与 False。"""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            cv = _compact(v)
            if cv is None or cv == "" or cv == [] or cv == {}:
                continue
            out[k] = cv
        return out
    if isinstance(value, list):
        items = [_compact(v) for v in value]
        return [v for v in items if not (v is None or v == "" or v == [] or v == {})]
    return value


def _json_ld(payload: dict[str, Any]) -> str:
    return json.dumps(_compact(payload), ensure_ascii=False).replace("<", "\\u003c")


def _org_node() -> dict[str, Any]:
    return {
        "@type": "Organization",
        "@id": ORG_ID,
        "name": ORG_NAME,
        "url": f"{BASE_URL}/",
        "logo": {"@type": "ImageObject", "url": f"{BASE_URL}/logo512.png"},
        "sameAs": ORG_SAME_AS,
    }


def _website_node() -> dict[str, Any]:
    return {
        "@type": "WebSite",
        "@id": WEBSITE_ID,
        "url": f"{BASE_URL}/",
        "name": ORG_NAME,
        "inLanguage": "zh-CN",
        "publisher": {"@id": ORG_ID},
        "potentialAction": {
            "@type": "SearchAction",
            "target": {"@type": "EntryPoint", "urlTemplate": f"{BASE_URL}/stock/{{search_term_string}}"},
            "query-input": "required name=search_term_string",
        },
    }


def _breadcrumb_node(trail: list[tuple[str, str]]) -> dict[str, Any]:
    """trail: [(名称, 绝对url), ...] 从站点根到当前页。"""
    return {
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "name": name, "item": url}
            for i, (name, url) in enumerate(trail)
        ],
    }


def _graph(*nodes: Optional[dict[str, Any]]) -> dict[str, Any]:
    """组装单一 JSON-LD @graph：站级 Org + WebSite 永远在前（实体骨架），其后跟面包屑/主实体。"""
    body = [_org_node(), _website_node()]
    body += [n for n in nodes if n]
    return {"@context": "https://schema.org", "@graph": body}


def _verify_meta_tags() -> str:
    return "".join(
        f'<meta name="{name}" content="{_esc(code)}">\n'
        for name, code in _VERIFY_METAS.items() if code
    )


def _page(
    *,
    title: str,
    description: str,
    body: str,
    canonical: str = "",
    graph: Optional[dict[str, Any]] = None,
    noindex: bool = False,
    image: str = "",
    cta_href: str = "",
    cta_text: str = "",
    ai_generated: bool = False,
) -> str:
    """统一页面外壳：meta/og/twitter/结构化数据 + 站点验证 + 与分享页同源的暗色排版。

    入参 title/description 需未转义原文。graph 为已组装好的 JSON-LD @graph（noindex 页传 None）。
    ai_generated=True → head 加隐式元数据标识 + 页脚加可见「AI 生成」声明
    （《人工智能生成合成内容标识办法》2025-09-01 施行的硬要求，含 AI 叙述/解读的页面必须开）。
    """
    t = _esc(title)
    # 标题后缀治理：短标题补品牌后缀利于识别；标题本身已长（≥42）则不补，避免 SERP 截断丢关键词。
    suffix = "" if len(str(title)) >= 42 else " · DeepFocus 投研"
    desc = _esc(" ".join(str(description).split())[:200])
    img = _esc(image or DEFAULT_OG_IMAGE)
    canonical_tags = (
        f'<link rel="canonical" href="{_esc(canonical)}">\n<meta property="og:url" content="{_esc(canonical)}">\n'
        if canonical else ""
    )
    robots = "noindex,nofollow" if noindex else "index,follow"
    ld_tag = f'<script type="application/ld+json">{_json_ld(graph)}</script>' if graph else ""
    ai_meta = '<meta name="ai-generated" content="true">\n' if ai_generated else ""
    ai_notice = "🤖 本页 AI 解读/叙述内容由 AI 生成，仅供参考，不构成投资建议 · " if ai_generated else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{t}{suffix}</title>
<meta name="description" content="{desc}">
<meta name="robots" content="{robots}">
<meta name="googlebot" content="{robots}">
<meta name="baiduspider" content="{robots}">
{_verify_meta_tags()}{ai_meta}{canonical_tags}<meta property="og:type" content="article">
<meta property="og:locale" content="zh_CN">
<meta property="og:title" content="{t}">
<meta property="og:description" content="{desc}">
<meta property="og:site_name" content="DeepFocus 金融数据">
<meta property="og:image" content="{img}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{t}">
<meta name="twitter:description" content="{desc}">
<meta name="twitter:image" content="{img}">
{ld_tag}
<style>
  :root {{ color-scheme: light dark; }}
  body {{ margin:0; background:#0b0e11; color:#e6e8eb; font:16px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif; }}
  .wrap {{ max-width:760px; margin:0 auto; padding:44px 20px 64px; }}
  .brand {{ color:#10b981; font-weight:600; font-size:14px; letter-spacing:.02em; }}
  .brand a {{ color:inherit; text-decoration:none; }}
  h1 {{ font-size:25px; line-height:1.35; margin:14px 0 6px; }}
  h2 {{ font-size:17px; margin:28px 0 10px; color:#d7dce0; border-left:3px solid #10b981; padding-left:10px; }}
  p {{ margin:0 0 12px; color:#c7ccd1; }}
  .meta {{ color:#8b939b; font-size:13px; margin-bottom:18px; }}
  .lead {{ font-size:17px; color:#e6e8eb; background:#11161b; border:1px solid #20262c; border-radius:12px; padding:14px 16px; margin:16px 0 8px; }}
  .tldr {{ font-size:15px; color:#cfeee0; background:#0e1714; border:1px solid #1c3a30; border-radius:12px; padding:12px 16px; margin:14px 0 6px; }}
  .tldr b {{ color:#10b981; }}
  table {{ width:100%; border-collapse:collapse; font-size:14px; margin:8px 0 4px; }}
  th, td {{ text-align:left; padding:7px 8px; border-bottom:1px solid #1b2127; }}
  th {{ color:#8b939b; font-weight:500; }}
  .up {{ color:#ef4444; }} .down {{ color:#10b981; }}
  .chips a {{ display:inline-block; margin:4px 8px 4px 0; padding:6px 12px; border:1px solid #233039; border-radius:999px; color:#9fd9c3; font-size:13px; text-decoration:none; }}
  .chips a:hover {{ border-color:#10b981; }}
  .dim {{ background:#11161b; border:1px solid #20262c; border-radius:12px; padding:12px 14px; margin:0 0 10px; }}
  .dim .sig {{ font-size:13px; font-weight:600; }}
  .dim .hl {{ margin:4px 0 2px; color:#e6e8eb; }}
  .dim ul {{ margin:6px 0 0; padding-left:18px; color:#9aa3ab; font-size:13px; }}
  .faq dt {{ font-weight:600; color:#e6e8eb; margin:14px 0 4px; }}
  .faq dd {{ margin:0 0 8px; color:#c7ccd1; }}
  .cta {{ display:inline-block; margin-top:26px; padding:11px 20px; border-radius:10px; background:#10b981; color:#04130d; font-weight:600; text-decoration:none; }}
  footer {{ margin-top:36px; padding-top:16px; border-top:1px solid #20262c; color:#6b7782; font-size:12px; }}
  footer a {{ color:#6b7782; }}
</style>
</head>
<body>
<main class="wrap">
  <div class="brand"><a href="{_esc(BASE_URL)}/">◆ DeepFocus 金融数据</a></div>
  {body}
  <a class="cta" href="{_esc(cta_href or APP_URL)}">{_esc(cta_text or "在 DeepFocus 上做深度研究 →")}</a>
  <footer>{ai_notice}{_esc(_DISCLAIMER)} · <a href="{_esc(BASE_URL)}/review">每日复盘</a> · <a href="{_esc(BASE_URL)}/stocks">热门个股速判</a> · <a href="{_esc(BASE_URL)}/articles">财经资讯</a>{_icp_footer()}</footer>
</main>
</body>
</html>"""


def _icp_footer() -> str:
    if not ICP_BEIAN:
        return ""
    return f' · <a href="https://beian.miit.gov.cn/" rel="nofollow" target="_blank">{_esc(ICP_BEIAN)}</a>'


# --------------------------------------------------------------------------- #
# 每日复盘页
# --------------------------------------------------------------------------- #
def render_review_page_html(review: dict[str, Any], recent: list[dict[str, Any]], page_url: str = "") -> str:
    date = str(review.get("date") or "")
    session_label = str(review.get("session_label") or ("午盘复盘" if review.get("session") == "midday" else "收盘复盘"))
    nar = review.get("narrative") or {}
    # 源头中性化：one_liner 同时进 H1/标题/TL;DR/meta description，统一过一遍护栏，保证各处一致。
    one_liner = neutralize_text(str(nar.get("one_liner") or ""))
    title = f"{date} A股{session_label}：{one_liner[:40]}" if one_liner else f"{date} A股{session_label}"
    canonical = page_url or f"{BASE_URL}/review/{date}"

    # C5 内链：复盘里点名的个股 → /stock/{code}（最新内容→个股页，给爬虫一条到全市场速判面的发现路径）。
    try:
        from . import stock_name_index
        _name2code = stock_name_index.all_name_code()
    except Exception:  # noqa: BLE001
        _name2code = {}

    def _slink(name: Any) -> str:
        code = _name2code.get(str(name or "").strip())
        return (f'<a style="color:#9fd9c3;text-decoration:none" href="{_esc(BASE_URL)}/stock/{_esc(code)}">{_esc(name)}</a>'
                if code else _esc(name))

    parts: list[str] = [f"<h1>{_esc(title)}</h1>"]
    parts.append(f'<div class="meta">{_esc(date)} · {_esc(session_label)} · DeepFocus 自动复盘</div>')
    if one_liner:
        # answer-first：开门见山一句可被 AI 引擎整段抽取的结论（自带日期，脱离上下文也成立）。
        parts.append(f'<div class="tldr"><b>一句话</b>：截至 {_esc(date)} A股{_esc(session_label)}，{_esc(neutralize_text(one_liner))}</div>')

    # C10 数字密集可抽取句（自带日期/单位、自洽，AI 引擎整段引用友好）。
    _br = review.get("breadth") or {}
    _topsec = (review.get("sectors") or {}).get("top") or []
    _qbits = [f"{i.get('name')} {_fmt_pct(i.get('pct'))}" for i in (review.get("indices") or [])[:3] if i.get("name")]
    if _br.get("total"):
        _qbits.append(f"全市场上涨 {_br.get('advancers')} 家、下跌 {_br.get('decliners')} 家")
    if _topsec:
        _qbits.append(f"领涨 {_topsec[0].get('name')}（{_fmt_pct(_topsec[0].get('pct'))}）")
    if _qbits:
        parts.append(f'<p><strong>数据速览</strong>（截至 {_esc(date)}）：' + _esc("；".join(_qbits)) + "。</p>")

    indices = review.get("indices") or []
    if indices:
        rows = "".join(
            f"<tr><td>{_esc(i.get('name'))}</td><td>{_esc(i.get('close'))}</td>"
            f"<td class=\"{'up' if _num(i.get('pct')) > 0 else 'down'}\">{_esc(_fmt_pct(i.get('pct')))}</td></tr>"
            for i in indices[:6]
        )
        parts.append(f"<h2>大盘指数</h2><table><tr><th>指数</th><th>收盘</th><th>涨跌</th></tr>{rows}</table>")

    breadth = review.get("breadth") or {}
    if breadth.get("total"):
        parts.append(
            "<p>"
            + _esc(
                f"全市场 {breadth.get('total')} 家：上涨 {breadth.get('advancers')} / 下跌 {breadth.get('decliners')}，"
                f"涨停 {breadth.get('limit_up')} 家、跌停 {breadth.get('limit_down')} 家。"
            )
            + "</p>"
        )

    sectors = review.get("sectors") or {}
    sector_bits = []
    for label, key in (("领涨板块", "top"), ("领跌板块", "bottom")):
        items = sectors.get(key) or []
        if items:
            txt = "、".join(f"{s.get('name')}({_fmt_pct(s.get('pct'))})" for s in items[:5])
            sector_bits.append(f"<p><strong>{_esc(label)}</strong>：{_esc(txt)}</p>")
    if sector_bits:
        parts.append("<h2>板块脉络</h2>" + "".join(sector_bits))

    # 差异化卖点：我们提前发现的资讯 × 当日行情验证
    edges = review.get("our_edge") or []
    if edges:
        rows = []
        for e in edges[:8]:
            lead = e.get("lead_hours")
            lead_txt = f"提前 {lead:.0f} 小时" if isinstance(lead, (int, float)) and lead > 0 else "盘前"
            rows.append(
                f"<div class=\"dim\"><div class=\"hl\">{_slink(e.get('name'))} · {_esc(e.get('theme') or e.get('kind'))} "
                f"<span class=\"{'up' if _num(e.get('pct')) > 0 else 'down'}\">{_esc(_fmt_pct(e.get('pct')))}</span></div>"
                f"<ul><li>{_esc(lead_txt)}在 DeepFocus 出现相关信号：{_esc(str(e.get('evidence') or '')[:160])}</li></ul></div>"
            )
        parts.append("<h2>我们提前发现了什么</h2>" + "".join(rows))

    for label, key in (("市场怎么走", "market"), ("板块在交易什么", "sectors"), ("资金动向", "funds"), ("明日关注", "tomorrow")):
        text = str(nar.get(key) or "").strip()
        if text:
            paras = "".join(f"<p>{_esc(neutralize_text(line))}</p>" for line in text.splitlines() if line.strip())
            parts.append(f"<h2>{_esc(label)}</h2>{paras}")

    others = [r for r in recent if r.get("date") and r.get("date") != date][:10]
    if others:
        links = "".join(
            f'<a href="{_esc(BASE_URL)}/review/{_esc(r["date"])}">{_esc(r["date"])} {_esc(r.get("session_label") or "")}</a>'
            for r in others
        )
        parts.append(f'<h2>近期复盘</h2><div class="chips">{links}</div>')

    published = _iso(review.get("generated_at") or date)
    article = {
        "@type": "Article",
        "headline": title[:110],
        "description": (one_liner or f"{date} A股{session_label}复盘")[:200],
        "inLanguage": "zh-CN",
        "datePublished": published,
        "dateModified": published,
        "articleSection": "A股复盘",
        "image": DEFAULT_OG_IMAGE,
        "author": {"@id": ORG_ID},
        "publisher": {"@id": ORG_ID},
        "isAccessibleForFree": True,
        **({"mainEntityOfPage": canonical} if canonical else {}),
    }
    trail = [("首页", f"{BASE_URL}/"), ("每日复盘", f"{BASE_URL}/review"), (f"{date} {session_label}", canonical)]
    return _page(
        title=title,
        description=one_liner or f"{date} A股{session_label}：大盘、板块、资金与我们提前发现的资讯复盘。",
        body="".join(parts),
        canonical=canonical,
        graph=_graph(_breadcrumb_node(trail), article),
        image=f"{BASE_URL}/og/review/{date}.png",
        ai_generated=True,  # 复盘叙述为 AI 合成 → 显式+隐式标识（《标识办法》硬要求）
    )


def render_review_fallback_html(review: dict[str, Any], page_url: str = "") -> str:
    """复盘页降级兜底：完整渲染抛错时输出「日期+指数表+免责」摘要版，宁可薄不可 500。

    只用防御式格式化（_esc/_fmt_pct/_num），不碰 narrative/our_edge 等复杂字段。
    """
    date = _esc(str(review.get("date") or ""))
    session_label = _esc(str(review.get("session_label") or "收盘复盘"))
    title = f"{date} A股{session_label}"
    parts: list[str] = [f"<h1>{title}</h1>",
                        f'<div class="meta">{date} · {session_label} · DeepFocus 自动复盘（摘要版）</div>']
    rows = "".join(
        f"<tr><td>{_esc(i.get('name'))}</td><td>{_esc(i.get('close'))}</td>"
        f"<td class=\"{'up' if _num(i.get('pct')) > 0 else 'down'}\">{_esc(_fmt_pct(i.get('pct')))}</td></tr>"
        for i in (review.get("indices") or [])[:6] if isinstance(i, dict) and i.get("name")
    )
    if rows:
        parts.append(f"<h2>大盘指数</h2><table><tr><th>指数</th><th>收盘</th><th>涨跌</th></tr>{rows}</table>")
    parts.append("<p>本期完整复盘正文暂时无法展示，可先查看近期其他复盘。</p>")
    return _page(
        title=title,
        description=f"{title}：大盘指数摘要。",
        body="".join(parts),
        canonical=page_url or f"{BASE_URL}/review/{review.get('date') or ''}",
        ai_generated=True,
    )


def render_review_hub_html(items: list[dict[str, Any]]) -> str:
    rows = "".join(
        f'<div class="dim"><div class="hl"><a style="color:#9fd9c3;text-decoration:none" '
        f'href="{_esc(BASE_URL)}/review/{_esc(it.get("date"))}">{_esc(it.get("date"))} · {_esc(it.get("session_label") or "收盘复盘")}</a></div>'
        f'<ul><li>{_esc((it.get("one_liner") or "")[:120])}</li></ul></div>'
        for it in items if it.get("date")
    ) or "<p>暂无复盘，交易日 15:35 后自动生成。</p>"
    body = (
        "<h1>A股每日复盘归档</h1>"
        '<div class="meta">每个交易日 11:40 / 15:35 自动生成：大盘 × 板块 × 资金 × 我们提前发现的资讯</div>' + rows
    )
    trail = [("首页", f"{BASE_URL}/"), ("每日复盘", f"{BASE_URL}/review")]
    return _page(
        title="A股每日复盘归档",
        description="DeepFocus 每个交易日自动生成的 A 股复盘：大盘指数、领涨领跌板块、资金动向，以及我们提前发现的资讯如何被当日行情验证。",
        body=body,
        canonical=f"{BASE_URL}/review",
        graph=_graph(_breadcrumb_node(trail)),
    )


# --------------------------------------------------------------------------- #
# 个股速判卡页
# --------------------------------------------------------------------------- #
def stock_indexable(ts: dict[str, Any], min_live_dims: int = 3) -> bool:
    """C7 质量门控：个股页够不够格进索引/sitemap。

    比「只看 overall_verdict」更严：要求结论非「数据不足」**且**至少 min_live_dims 个维度有真实信号
    （非 insufficient）。挡住停牌/冷门小票的批量薄页——百度对批量金融薄页惩罚极重，会反噬优质页。
    薄页仍会渲染（noindex），可被内链发现，只是不进索引、不进 sitemap。
    """
    if str(ts.get("overall_verdict") or "数据不足") == "数据不足":
        return False
    live = sum(1 for d in (ts.get("dimensions") or [])
               if str(d.get("signal")) in ("bullish", "bearish", "neutral"))
    return live >= min_live_dims


def _stock_faq(name: str, symbol: str, verdict: str, score: Any, conf_txt: str, date: str,
               narrative: str, dims: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """生成「问句式」FAQ（可见 HTML + FAQPage 节点必须答案一致），AI 引擎偏好问答结构、利于被引用。"""
    score_txt = f"，综合分 {score}" if score not in (None, "") else ""
    a1 = neutralize_text(
        f"截至 {date}，DeepFocus 多维证据引擎对 {name}（{symbol}）的综合研判为「{verdict}」{score_txt}"
        + (f"，{conf_txt}" if conf_txt else "") + "。"
        + (narrative[:160] if narrative else "")
    ).strip()
    qas = [(f"{name}（{symbol}）现在怎么看？", a1)]
    bull = [d for d in dims if str(d.get("signal")) == "bullish"]
    bear = [d for d in dims if str(d.get("signal")) == "bearish"]
    if bull:
        a = neutralize_text("；".join(f"{_d.get('label')}：{_d.get('headline')}" for _d in bull[:3])) + "。"
        qas.append((f"{name}有哪些偏多证据？", a))
    if bear:
        a = neutralize_text("；".join(f"{_d.get('label')}：{_d.get('headline')}" for _d in bear[:3])) + "。"
        qas.append((f"{name}有哪些偏空风险？", a))
    qas = [(q, a) for q, a in qas if a and a.strip()]

    html_block = ""
    if qas:
        dts = "".join(f"<dt>{_esc(q)}</dt><dd>{_esc(a)}</dd>" for q, a in qas)
        html_block = f'<h2>常见问题</h2><dl class="faq">{dts}</dl>'
    faq_node = {
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": q,
             "acceptedAnswer": {"@type": "Answer", "text": a}}
            for q, a in qas
        ],
    } if qas else None
    return html_block, ([faq_node] if faq_node else [])


def render_stock_page_html(ts: dict[str, Any], related: list[dict[str, Any]], page_url: str = "") -> str:
    symbol = str(ts.get("symbol") or "").upper()
    name = str(ts.get("name") or symbol)
    verdict = str(ts.get("overall_verdict") or "数据不足")
    thin = not stock_indexable(ts)  # C7 质量门控：结论不足 / live 维度太少 → noindex 不发结构化数据
    title = f"{name}({symbol}) 多维证据速判：{verdict}"
    canonical = page_url or f"{BASE_URL}/stock/{symbol}"
    date = str(ts.get("generated_at") or "")[:10]
    # C2 激活深链：CTA 带 ?watch={symbol} → 前端 handler 落地即「加自选 + 开盯盘」(把高意图搜索访客转成留存)。
    _app = APP_URL if APP_URL.startswith("http") else f"{BASE_URL}{APP_URL}"
    _watch_href = f"{_app}{'&' if '?' in _app else '?'}watch={_esc(symbol)}&from=seo"

    price_bit = ""
    if ts.get("price") is not None:
        price_bit = f"{ts.get('currency') or ''} {ts.get('price')}（{_fmt_pct(ts.get('change_percent'))}）"
    conf = ts.get("confidence")
    conf_txt = f"置信度 {float(conf) * 100:.0f}%" if isinstance(conf, (int, float)) else ""
    # 源头中性化：narrative 同时进 lead/FAQ/meta description，统一过一遍护栏（prompt 之外第二道硬护栏）。
    narrative = neutralize_text(str(ts.get("narrative") or "").strip())
    dims = ts.get("dimensions") or []

    parts = [f"<h1>{_esc(title)}</h1>"]
    parts.append(
        f'<div class="meta">{_esc(price_bit)} · 综合分 {_esc(ts.get("overall_score"))} · {_esc(conf_txt)}'
        f' · 生成于 {_esc(str(ts.get("generated_at") or "")[:16].replace("T", " "))}（UTC）</div>'
    )
    # answer-first TL;DR：自带日期与研判，便于 AI 引擎整段引用。
    if not thin:
        tldr = neutralize_text(
            f"截至 {date or '近期'}，DeepFocus 对 {name}（{symbol}）的多维证据综合研判为「{verdict}」"
            + (f"（{conf_txt}）" if conf_txt else "") + "。"
        )
        parts.append(f'<div class="tldr"><b>速判</b>：{_esc(tldr)}</div>')
    if narrative:
        parts.append(f'<div class="lead">{_esc(neutralize_text(narrative[:400]))}</div>')

    if dims:
        cards = []
        for d in dims:
            sig_label, sig_color = _SIGNAL_LABEL.get(str(d.get("signal")), _SIGNAL_LABEL["insufficient"])
            evid = "".join(f"<li>{_esc(e)}</li>" for e in (d.get("evidence") or [])[:3])
            cards.append(
                f'<div class="dim"><span class="sig" style="color:{sig_color}">{_esc(sig_label)}</span> '
                f'<strong>{_esc(d.get("label"))}</strong>'
                f'<div class="hl">{_esc(d.get("headline"))}</div><ul>{evid}</ul></div>'
            )
        parts.append(f"<h2>{len(dims)} 维证据</h2>" + "".join(cards))

    faq_nodes: list[dict[str, Any]] = []
    if not thin:
        faq_html, faq_nodes = _stock_faq(name, symbol, verdict, ts.get("overall_score"), conf_txt, date or "近期", narrative, dims)
        if faq_html:
            parts.append(faq_html)

    if related:
        links = "".join(
            f'<a href="{_esc(BASE_URL)}/stock/{_esc(r["symbol"])}">{_esc(r.get("name") or r["symbol"])}</a>'
            for r in related if r.get("symbol")
        )
        parts.append(f'<h2>大家也在看</h2><div class="chips">{links}</div>')
        # C11 横向对比入口（给爬虫发现 /compare）：与首个相关个股对比
        r0 = next((r for r in related if r.get("symbol")), None)
        if r0 and not thin:
            rs = str(r0["symbol"]).upper()
            parts.append(
                f'<div class="chips"><a href="{_esc(BASE_URL)}/compare/{_esc(symbol)}-vs-{_esc(rs)}">'
                f'{_esc(name)} vs {_esc(r0.get("name") or rs)} 多维对比 →</a></div>')

    graph = None
    if not thin:
        published = _iso(ts.get("generated_at"))
        article = {
            "@type": "Article",
            "headline": title[:110],
            "description": (narrative[:200] or f"{name}({symbol}) 多维证据速判卡。"),
            "inLanguage": "zh-CN",
            "datePublished": published,
            "dateModified": published,
            "articleSection": "个股研判",
            "image": DEFAULT_OG_IMAGE,
            "about": {"@type": "Corporation", "name": name, "tickerSymbol": symbol},
            "author": {"@id": ORG_ID},
            "publisher": {"@id": ORG_ID},
            "isAccessibleForFree": True,
            **({"mainEntityOfPage": canonical} if canonical else {}),
        }
        trail = [("首页", f"{BASE_URL}/"), ("热门个股", f"{BASE_URL}/stocks"), (f"{name}({symbol})", canonical)]
        graph = _graph(_breadcrumb_node(trail), article, *faq_nodes)

    return _page(
        title=title,
        description=narrative or f"{name}({symbol}) 动量/催化/估值/资金面等多维证据速判，信号、证据与置信度一页看清。",
        body="".join(parts),
        canonical=canonical,
        graph=graph,
        noindex=thin,
        image=f"{BASE_URL}/og/stock/{symbol}.png",
        cta_href=_watch_href,
        cta_text=f"在 DeepFocus 盯盘 {name} →",
        ai_generated=True,  # 速判卡 narrative 为 AI 合成 → 标识
    )


def render_stocks_hub_html(items: list[dict[str, Any]]) -> str:
    """热门个股聚合页。items: [{symbol, name?, verdict?, change_percent?, views?}]"""
    rows = []
    for it in items:
        sym = str(it.get("symbol") or "").upper()
        if not sym:
            continue
        label = f"{it.get('name') or sym}"
        extra = " · ".join(x for x in (str(it.get("verdict") or ""), _fmt_pct(it.get("change_percent")) if it.get("change_percent") is not None else "") if x)
        rows.append(
            f'<div class="dim"><div class="hl"><a style="color:#9fd9c3;text-decoration:none" href="{_esc(BASE_URL)}/stock/{_esc(sym)}">'
            f"{_esc(label)}（{_esc(sym)}）</a></div><ul><li>{_esc(extra or '查看多维证据速判')}</li></ul></div>"
        )
    body = (
        "<h1>热门个股 · 多维证据速判</h1>"
        '<div class="meta">按 DeepFocus 用户近期关注热度排序，每只股票一页看清信号 / 证据 / 置信度</div>'
        + ("".join(rows) or "<p>暂无数据。</p>")
    )
    trail = [("首页", f"{BASE_URL}/"), ("热门个股", f"{BASE_URL}/stocks")]
    return _page(
        title="热门个股多维证据速判",
        description="DeepFocus 用户近期最关注的股票：动量、催化、估值、资金面等多维证据速判，信号与置信度一页看清。",
        body=body,
        canonical=f"{BASE_URL}/stocks",
        graph=_graph(_breadcrumb_node(trail)),
    )


STOCKS_ALL_PER_PAGE = 200  # 每页枚举的个股数（分页喂爬虫发现全市场 /stock/{code}）


def render_stocks_all_html(entries: list[tuple[str, str]], page: int, total_pages: int, total_count: int) -> str:
    """全市场发现页（C1）：把全 A 名录分页成可爬内链，引导爬虫发现每一个 /stock/{code}。

    entries 为本页的 (名称, 代码) 切片。落地页本身薄（只是链接索引），不进 sitemap、不发主实体结构化数据，
    但 index,follow 让爬虫顺着链接抓个股页——个股页自带 C7 质量门控（薄的自己 noindex）。
    """
    links = "".join(
        f'<a href="{_esc(BASE_URL)}/stock/{_esc(code)}">{_esc(name)}（{_esc(code)}）</a>'
        for name, code in entries
    ) or "<p>名录加载中，稍后再试。</p>"
    pager = ""
    if total_pages > 1:
        nums = " ".join(
            (f'<strong>{p}</strong>' if p == page
             else f'<a href="{_esc(BASE_URL)}/stocks/all?page={p}">{p}</a>')
            for p in range(1, total_pages + 1)
        )
        pager = f'<h2>更多页</h2><div class="chips">{nums}</div>'
    title = "全部 A 股 · 多维证据速判索引" if page <= 1 else f"全部 A 股速判索引（第 {page} 页）"
    body = (
        f"<h1>{_esc(title)}</h1>"
        f'<div class="meta">覆盖全 A {total_count} 只个股，每只一页看清动量 / 催化 / 估值 / 资金面信号与置信度'
        f'（第 {page}/{total_pages} 页）</div>'
        f'<div class="chips">{links}</div>{pager}'
    )
    canonical = f"{BASE_URL}/stocks/all" + (f"?page={page}" if page > 1 else "")
    trail = [("首页", f"{BASE_URL}/"), ("热门个股", f"{BASE_URL}/stocks"), ("全部A股", f"{BASE_URL}/stocks/all")]
    return _page(
        title=title,
        description="DeepFocus 覆盖全部 A 股的多维证据速判索引：每只股票一页看清动量、催化、估值、资金面信号与置信度。",
        body=body,
        canonical=canonical,
        graph=_graph(_breadcrumb_node(trail)),
    )


# --------------------------------------------------------------------------- #
# 个股横向对比页（C11）：逐维证据并排，只描述差异、不作孰优孰劣判断（无牌照红线）
# --------------------------------------------------------------------------- #
def render_compare_page_html(a: dict[str, Any], b: dict[str, Any], page_url: str = "") -> str:
    asym = str(a.get("symbol") or "").upper()
    an = str(a.get("name") or asym)
    bsym = str(b.get("symbol") or "").upper()
    bn = str(b.get("name") or bsym)
    av = str(a.get("overall_verdict") or "数据不足")
    bv = str(b.get("overall_verdict") or "数据不足")
    thin = not (stock_indexable(a) and stock_indexable(b))  # 任一薄 → 不进索引
    title = f"{an}（{asym}）vs {bn}（{bsym}）多维证据对比"
    canonical = page_url or f"{BASE_URL}/compare/{asym}-vs-{bsym}"

    parts = [f"<h1>{_esc(title)}</h1>",
             '<div class="meta">逐维证据横向对比 · 仅供研究参考，不构成买卖建议</div>',
             f'<div class="lead">{_esc(an)} 综合研判「{_esc(av)}」，{_esc(bn)} 综合研判「{_esc(bv)}」。'
             '下表逐维列出双方的信号方向与要点，仅供横向参考，不对孰优孰劣作判断。</div>']
    adims = {str(d.get("label")): d for d in (a.get("dimensions") or [])}
    bdims = {str(d.get("label")): d for d in (b.get("dimensions") or [])}
    rows = ""
    for lab in list(dict.fromkeys(list(adims) + list(bdims))):
        da, db = adims.get(lab) or {}, bdims.get(lab) or {}
        la, ca = _SIGNAL_LABEL.get(str(da.get("signal")), _SIGNAL_LABEL["insufficient"])
        lb, cb = _SIGNAL_LABEL.get(str(db.get("signal")), _SIGNAL_LABEL["insufficient"])
        rows += (f"<tr><td>{_esc(lab)}</td>"
                 f'<td style="color:{ca}">{_esc(la)} {_esc(neutralize_text(str(da.get("headline") or "")))}</td>'
                 f'<td style="color:{cb}">{_esc(lb)} {_esc(neutralize_text(str(db.get("headline") or "")))}</td></tr>')
    parts.append(f'<h2>逐维对比</h2><table><tr><th>维度</th><th>{_esc(an)}</th><th>{_esc(bn)}</th></tr>{rows}</table>')
    parts.append(
        f'<h2>单只详情</h2><div class="chips">'
        f'<a href="{_esc(BASE_URL)}/stock/{_esc(asym)}">{_esc(an)} 速判</a>'
        f'<a href="{_esc(BASE_URL)}/stock/{_esc(bsym)}">{_esc(bn)} 速判</a></div>')
    graph = None
    if not thin:
        article = {"@type": "Article", "headline": title[:110], "inLanguage": "zh-CN",
                   "description": f"{an}（{asym}）与 {bn}（{bsym}）多维证据逐项对比。",
                   "author": {"@id": ORG_ID}, "publisher": {"@id": ORG_ID}, "image": DEFAULT_OG_IMAGE,
                   **({"mainEntityOfPage": canonical} if canonical else {})}
        trail = [("首页", f"{BASE_URL}/"), ("热门个股", f"{BASE_URL}/stocks"), (f"{an} vs {bn}", canonical)]
        graph = _graph(_breadcrumb_node(trail), article)
    return _page(
        title=title,
        description=f"{an}（{asym}）与 {bn}（{bsym}）动量 / 催化 / 估值 / 资金面等多维证据逐项对比。",
        body="".join(parts),
        canonical=canonical,
        graph=graph,
        noindex=thin,
        ai_generated=True,  # 对比页含速判 AI 叙述 → 标识
    )


# --------------------------------------------------------------------------- #
# 文章公开落地页（软墙：标题+来源+短导语公开可分享/收录，全文需登录在 App 内看）
# --------------------------------------------------------------------------- #
def _app_article_url(article_id: str) -> str:
    """登录后看全文的深链：打开终端 App 并定位到该文章。"""
    base = APP_URL if APP_URL.startswith("http") else f"{BASE_URL}{APP_URL}"
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}article={_esc(article_id)}"


def _teaser(content: str, limit: int = 120) -> str:
    """从正文取一段短导语（仅预览，第三方全文不上公开页）。"""
    flat = " ".join(str(content or "").split())
    return flat[:limit] + ("…" if len(flat) > limit else "")


def _public_source(name: str) -> str:
    """对外署名：内部聚合源名(DAO财经/道财经等)一律收敛为 DeepFocus(品牌红线，不外露)。"""
    n = (name or "").strip()
    if not n or "DAO" in n.upper() or "道财经" in n or "财经" in n:
        return "DeepFocus"
    return n


def render_article_page_html(article: dict[str, Any], recent: list[dict[str, Any]], page_url: str = "") -> str:
    aid = str(article.get("id") or "")
    title = str(article.get("title") or "资讯文章").strip()
    source = _public_source(article.get("source_name") or "")
    when = str(article.get("created_at") or "")[:16].replace("T", " ")
    teaser = _teaser(article.get("content") or "", 300)
    symbol = str(article.get("symbol") or "").strip()
    canonical = page_url or f"{BASE_URL}/article/{aid}"

    meta_bits = [_esc(source), f"{_esc(when)}（UTC）", "资讯文章"]
    if symbol:
        meta_bits.append(_esc(symbol))
    parts = [f"<h1>{_esc(title)}</h1>", f'<div class="meta">{" · ".join(meta_bits)}</div>']
    if teaser and teaser.strip() != title.strip():  # 正文与标题相同则不重复展示导语
        parts.append(f'<h2>内容摘要</h2><div class="lead">{_esc(teaser)}</div>')
    # 软墙：全文需登录在 App 内看
    parts.append(f'<a class="cta" href="{_app_article_url(aid)}">登录 DeepFocus 看全文 →</a>')
    # 价值点：登录能看到什么（填充页面 + 说明为何登录）
    parts.append(
        '<h2>登录后你可以</h2>'
        '<div class="dim"><ul style="margin:0;padding-left:18px;color:#c7ccd1">'
        '<li>阅读本文<strong>完整原文</strong></li>'
        '<li>实时 A 股 / 港美股<strong>行情与自选盯盘</strong></li>'
        '<li>个股 / 研报 / 快讯的<strong> AI 解读</strong>，以及每日 A 股收盘复盘</li>'
        '</ul><p style="margin:8px 0 0;color:#8b939b;font-size:13px">行情与资讯免费 · 登录即用</p></div>'
    )

    others = [r for r in recent if r.get("id") and r.get("id") != aid][:12]
    if others:
        cards = ""
        for r in others:
            rt = str(r.get("title") or "")
            rteaser = _teaser(r.get("content") or "", 56)
            sub = f'<ul><li>{_esc(rteaser)}</li></ul>' if rteaser and rteaser.strip() != rt.strip() else ""
            cards += (
                f'<div class="dim"><div class="hl"><a style="color:#9fd9c3;text-decoration:none" '
                f'href="{_esc(BASE_URL)}/article/{_esc(r["id"])}">{_esc(rt[:48])}</a></div>{sub}</div>'
            )
        parts.append(f'<h2>更多资讯</h2>{cards}')

    published = _iso(article.get("created_at"))
    news = {
        "@type": "NewsArticle",
        "headline": title[:110],
        "description": teaser[:200],
        "inLanguage": "zh-CN",
        "datePublished": published,
        "dateModified": published,
        "image": DEFAULT_OG_IMAGE,
        "author": {"@type": "Organization", "name": source},
        "publisher": {"@id": ORG_ID},
        "isAccessibleForFree": False,  # 软墙：公开仅标题+摘要，全文需登录
        **({"mainEntityOfPage": canonical} if canonical else {}),
    }
    trail = [("首页", f"{BASE_URL}/"), ("财经资讯", f"{BASE_URL}/articles"), (title[:30], canonical)]
    return _page(
        title=title,
        description=teaser or f"{title} · 在 DeepFocus 阅读全文。",
        body="".join(parts),
        canonical=canonical,
        graph=_graph(_breadcrumb_node(trail), news),
    )


def render_articles_hub_html(items: list[dict[str, Any]]) -> str:
    rows = "".join(
        f'<div class="dim"><div class="hl"><a style="color:#9fd9c3;text-decoration:none" '
        f'href="{_esc(BASE_URL)}/article/{_esc(it.get("id"))}">{_esc((it.get("title") or "")[:48])}</a></div>'
        f'<ul><li>{_esc(_public_source(it.get("source_name") or ""))} · {_esc(_teaser(it.get("content") or "", 60))}</li></ul></div>'
        for it in items if it.get("id")
    ) or "<p>暂无文章。</p>"
    body = (
        "<h1>财经资讯文章</h1>"
        '<div class="meta">DeepFocus 聚合的财经资讯，登录后阅读全文并解锁行情 / 自选 / AI 解读</div>' + rows
    )
    trail = [("首页", f"{BASE_URL}/"), ("财经资讯", f"{BASE_URL}/articles")]
    return _page(
        title="财经资讯文章",
        description="DeepFocus 聚合的财经资讯文章：登录后阅读全文，并解锁实时行情、自选与 AI 解读。",
        body=body,
        canonical=f"{BASE_URL}/articles",
        graph=_graph(_breadcrumb_node(trail)),
    )


# --------------------------------------------------------------------------- #
# 机构纪要分享落地页（/note/{id}）：白名单用户可对外分享单条机构纪要（用户拍板 2026-07-06）。
# ⚠️第三方付费社群内容合规护栏：①只出标题+≤100字导语钩子,绝不放全文;②不透星球来源(与站内一致);
# ③noindex+不进 sitemap——仅可链接转发,绝不做搜索引擎收录(收录第三方付费内容是最高风险项);
# ④全文仍锁站内白名单,故 CTA 是品牌引流「看更多市场纪要」而非承诺本条全文(注册也是白名单外看不到)。
# --------------------------------------------------------------------------- #
def render_note_page_html(topic: dict[str, Any], page_url: str = "") -> str:
    nid = str(topic.get("id") or "")
    title = str(topic.get("title") or "机构纪要").strip()
    lead = str(topic.get("lead") or "").strip()
    when = str(topic.get("date") or "")[:10]
    canonical = page_url or f"{BASE_URL}/note/{nid}"

    meta_bits = ["机构纪要"]
    if when:
        meta_bits.append(_esc(when))
    parts = [f"<h1>{_esc(title)}</h1>", f'<div class="meta">{" · ".join(meta_bits)}</div>']
    if lead and lead.strip() != title.strip():
        parts.append(f'<h2>摘要</h2><div class="lead">{_esc(lead)}</div>')
    parts.append(
        '<div class="dim"><ul style="margin:0;padding-left:18px;color:#c7ccd1">'
        '<li>盘中<strong>机构调研纪要 / 个股动态点评</strong>持续更新</li>'
        '<li>实时 A 股 / 港美股<strong>行情与自选盯盘</strong></li>'
        '<li>个股 / 研报 / 快讯的<strong> AI 解读</strong>与每日 A 股收盘复盘</li>'
        '</ul><p style="margin:8px 0 0;color:#8b939b;font-size:13px">行情与资讯免费 · 打开即用</p></div>'
    )
    trail = [("首页", f"{BASE_URL}/"), ("机构纪要", f"{BASE_URL}/notes"), (title[:30], canonical)]
    published = _iso(when)
    article = {  # GEO：给搜索/AI 引擎结构化的可抽取实体（仅 teaser，非全文）
        "@type": "Article",
        "headline": title[:110],
        "description": lead[:200],
        "inLanguage": "zh-CN",
        "articleSection": "机构纪要",
        "image": DEFAULT_OG_IMAGE,
        "author": {"@id": ORG_ID},
        "publisher": {"@id": ORG_ID},
        "isAccessibleForFree": True,
        **({"datePublished": published, "dateModified": published} if published else {}),
        **({"mainEntityOfPage": canonical} if canonical else {}),
    }
    return _page(
        title=title,
        description=lead or f"{title} · DeepFocus 机构纪要。",
        body="".join(parts),
        canonical=canonical,
        cta_href=APP_URL,
        cta_text="打开 DeepFocus 看完整机构纪要 →",   # 用户拍板放开匿名可见→CTA 不再要求登录
        graph=_graph(_breadcrumb_node(trail), article),
        # 用户拍板放开 SEO 收录（2026-07-06）：落地页仅标题+≤100字导语钩子（全文在 SPA 不入 HTML→
        # 搜索引擎只收录 teaser 非全文）；noindex 已去除。⚠️第三方付费内容收录风险已知并接受。
    )


def render_notes_hub_html(items: list[dict[str, Any]]) -> str:
    """机构纪要公开列表页（/notes）：给搜索/AI 引擎一条发现全部机构纪要的入口 + 内链到每条 /note/{id}。"""
    rows = "".join(
        f'<div class="dim"><div class="hl"><a style="color:#9fd9c3;text-decoration:none" '
        f'href="{_esc(BASE_URL)}/note/{_esc(it.get("id"))}">{_esc((it.get("title") or "机构纪要")[:56])}</a></div>'
        f'<ul><li>{_esc(str(it.get("date") or ""))} · {_esc((it.get("lead") or "")[:70])}</li></ul></div>'
        for it in items if it.get("id")
    ) or "<p>暂无机构纪要。</p>"
    body = (
        "<h1>机构纪要</h1>"
        '<div class="meta">机构调研纪要 / 个股动态点评聚合，仅供研究参考，不构成投资建议。打开 DeepFocus 看完整内容。</div>' + rows
    )
    trail = [("首页", f"{BASE_URL}/"), ("机构纪要", f"{BASE_URL}/notes")]
    return _page(
        title="机构纪要 · 机构调研纪要与个股动态点评",
        description="DeepFocus 机构纪要：机构调研会议纪要、个股动态点评聚合。每条含标题与摘要，打开 App 看完整内容。仅供研究参考，不构成投资建议。",
        body=body,
        canonical=f"{BASE_URL}/notes",
        graph=_graph(_breadcrumb_node(trail)),
    )


# --------------------------------------------------------------------------- #
# 研报「AI 解读」可分享落地页（软墙）：分享的是我们自己的解读（增值内容），不外露第三方研报原文/PDF。
# 与文章页同构：标题 + 机构 + 解读导语公开可收录/转发，完整解读需登录在 App 内看（[[report_share]]）。
# --------------------------------------------------------------------------- #
def _app_report_url(report_id: str) -> str:
    """登录后看完整解读的深链：打开终端 App 并定位到该研报解读。"""
    base = APP_URL if APP_URL.startswith("http") else f"{BASE_URL}{APP_URL}"
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}report={_esc(report_id)}"


def render_report_page_html(report: dict[str, Any], recent: list[dict[str, Any]], page_url: str = "") -> str:
    rid = str(report.get("id") or "")
    title = str(report.get("title") or "研报解读").strip()
    source = _public_source(report.get("source_name") or "")
    when = str(report.get("created_at") or "")[:16].replace("T", " ")
    teaser = _teaser(report.get("summary") or "", 300)
    symbol = str(report.get("symbol") or "").strip()
    canonical = page_url or f"{BASE_URL}/report/{rid}"

    meta_bits = [_esc(source), f"{_esc(when)}（UTC）", "研报 AI 解读"]
    if symbol:
        meta_bits.append(_esc(symbol))
    parts = [f"<h1>{_esc(title)}</h1>", f'<div class="meta">{" · ".join(meta_bits)}</div>']
    parts.append('<div class="lead" style="color:#9fd9c3">✦ DeepFocus AI 研报速读</div>')
    if teaser:
        parts.append(f'<h2>解读摘要</h2><div class="lead">{_esc(teaser)}</div>')
    # 软墙：完整解读需登录在 App 内看
    parts.append(f'<a class="cta" href="{_app_report_url(rid)}">登录 DeepFocus 看完整解读 →</a>')
    parts.append(
        '<h2>登录后你可以</h2>'
        '<div class="dim"><ul style="margin:0;padding-left:18px;color:#c7ccd1">'
        '<li>阅读这份研报的<strong>完整 AI 解读</strong>（投资逻辑 / 利好利空 / 一句话启示）</li>'
        '<li>实时 A 股 / 港美股<strong>行情与自选盯盘</strong></li>'
        '<li>个股 / 研报 / 快讯的<strong> AI 解读</strong>，以及每日 A 股收盘复盘</li>'
        '</ul><p style="margin:8px 0 0;color:#8b939b;font-size:13px">行情与资讯免费 · 登录即用</p></div>'
    )

    others = [r for r in recent if r.get("id") and r.get("id") != rid][:12]
    if others:
        cards = ""
        for r in others:
            rt = str(r.get("title") or "")
            rteaser = _teaser(r.get("summary") or "", 56)
            sub = f'<ul><li>{_esc(rteaser)}</li></ul>' if rteaser else ""
            cards += (
                f'<div class="dim"><div class="hl"><a style="color:#9fd9c3;text-decoration:none" '
                f'href="{_esc(BASE_URL)}/report/{_esc(r["id"])}">{_esc(rt[:48])}</a></div>{sub}</div>'
            )
        parts.append(f'<h2>更多研报解读</h2>{cards}')

    published = _iso(report.get("created_at"))
    article = {
        "@type": "Article",
        "headline": title[:110],
        "description": teaser[:200],
        "inLanguage": "zh-CN",
        "datePublished": published,
        "dateModified": published,
        "image": DEFAULT_OG_IMAGE,
        "author": {"@id": ORG_ID},
        "publisher": {"@id": ORG_ID},
        "isAccessibleForFree": False,  # 软墙：公开仅标题+解读导语，完整解读需登录
        **({"mainEntityOfPage": canonical} if canonical else {}),
    }
    trail = [("首页", f"{BASE_URL}/"), ("研报解读", f"{BASE_URL}/reports"), (title[:30], canonical)]
    return _page(
        title=f"{title} · 研报 AI 解读",
        description=teaser or f"{title} · 在 DeepFocus 查看完整研报 AI 解读。",
        body="".join(parts),
        canonical=canonical,
        graph=_graph(_breadcrumb_node(trail), article),
        ai_generated=True,  # 研报解读为 AI 生成 → 标识
    )


def render_reports_hub_html(items: list[dict[str, Any]]) -> str:
    rows = "".join(
        f'<div class="dim"><div class="hl"><a style="color:#9fd9c3;text-decoration:none" '
        f'href="{_esc(BASE_URL)}/report/{_esc(it.get("id"))}">{_esc((it.get("title") or "")[:48])}</a></div>'
        f'<ul><li>{_esc(_public_source(it.get("source_name") or ""))} · {_esc(_teaser(it.get("summary") or "", 60))}</li></ul></div>'
        for it in items if it.get("id")
    ) or "<p>暂无研报解读。</p>"
    body = (
        "<h1>研报 AI 解读</h1>"
        '<div class="meta">DeepFocus 对券商 / 投行研报的 AI 速读，登录后查看完整解读并解锁行情 / 自选 / 复盘</div>' + rows
    )
    trail = [("首页", f"{BASE_URL}/"), ("研报解读", f"{BASE_URL}/reports")]
    return _page(
        title="研报 AI 解读",
        description="DeepFocus 对券商 / 投行研报的 AI 速读解读：登录后查看完整解读，并解锁实时行情、自选与每日复盘。",
        body=body,
        canonical=f"{BASE_URL}/reports",
        graph=_graph(_breadcrumb_node(trail)),
    )


# --------------------------------------------------------------------------- #
# 「提前覆盖」战绩公开页：确定性统计（零 AI 叙述），只做事实表述。
# ⚠️措辞铁律：只用「提前覆盖 N 次」类事实表述——绝不出现命中率/准确率/收益归因
# （帮你抓住 X% 涨幅等），那是变相宣传预测能力（无牌照投顾红线）。
# --------------------------------------------------------------------------- #
def render_track_record_page_html(record: dict[str, Any], page_url: str = "") -> str:
    """近 30 天「提前覆盖」明细页：每条 = 标的/主题 + 日期 + 领先小时数 + 佐证链接（可溯源）。

    record 为 track_record.platform_track_record() 输出：
    {hit_count, avg_lead_hours, max_lead_hours, sector_hits, stock_hits, days_covered, days, recent:[…]}。
    确定性统计（非 AI 生成）→ ai_generated=False。"""
    rec = record or {}
    days = rec.get("days") or 30
    n = rec.get("hit_count") or 0
    avg_lead = rec.get("avg_lead_hours") or 0
    max_lead = rec.get("max_lead_hours") or 0
    days_covered = rec.get("days_covered") or 0
    canonical = page_url or f"{BASE_URL}/track-record"

    parts: list[str] = [f"<h1>信息时效记录：近 {_esc(days)} 天提前覆盖 {_esc(n)} 次</h1>"]
    parts.append(
        '<div class="meta">DeepFocus 聚合快讯/研报信号的发布时间 × 次日盘面事实的自动比对 · 每条均可溯源</div>'
    )
    if n:
        # answer-first：一句纯事实概括（次数/领先小时数都是时间戳比对结果，非预测能力表述）。
        parts.append(
            f'<div class="tldr"><b>事实速览</b>：近 {_esc(days)} 天，DeepFocus 站内信号在相关标的/板块被市场'
            f"关注之前平均提前 {_esc(avg_lead)} 小时出现，共提前覆盖 {_esc(n)} 次、最长领先 {_esc(max_lead)} 小时，"
            f"覆盖 {_esc(days_covered)} 个交易日（板块 {_esc(rec.get('sector_hits') or 0)} 次 / "
            f"个股 {_esc(rec.get('stock_hits') or 0)} 次）。</div>"
        )
    else:
        parts.append("<p>近期暂无可展示的提前覆盖记录，交易日复盘生成后自动更新。</p>")

    rows: list[str] = []
    for h in (rec.get("recent") or []):
        name = str(h.get("name") or "").strip()
        if not name:
            continue
        lead = h.get("lead_hours")
        lead_txt = f"提前 {lead:.0f} 小时" if isinstance(lead, (int, float)) and lead > 0 else "盘前已覆盖"
        kind_txt = "板块" if h.get("kind") == "sector" else "个股"
        sig_bits: list[str] = []
        for s in (h.get("signals") or [])[:3]:
            st = str(s.get("title") or "").strip()
            if not st:
                continue
            su = str(s.get("url") or "").strip()
            if su.startswith("http"):
                sig_bits.append(
                    f'<li>佐证：<a style="color:#9fd9c3" href="{_esc(su)}" rel="nofollow">{_esc(st[:80])}</a></li>'
                )
            else:
                sig_bits.append(f"<li>佐证：{_esc(st[:80])}</li>")
        if not sig_bits and (h.get("reason") or "").strip():
            sig_bits.append(f"<li>{_esc(str(h.get('reason'))[:120])}</li>")
        rows.append(
            f'<div class="dim"><div class="hl">{_esc(h.get("date"))} · {_esc(name)}'
            f'<span style="color:#8b939b;font-size:13px">（{_esc(kind_txt)}）</span> · {_esc(lead_txt)}</div>'
            f'<ul>{"".join(sig_bits)}</ul></div>'
        )
    if rows:
        parts.append(f"<h2>近 {_esc(days)} 天提前覆盖明细</h2>" + "".join(rows))

    parts.append(
        "<h2>统计口径</h2>"
        "<p>「提前覆盖」指：站内快讯/研报信号的发布时间戳，早于该标的/板块出现在次日复盘盘面事实中的时间，"
        "且经复核确认相关（有判定理由或多条佐证）。本页只统计时间先后这一客观事实，"
        "不代表因果关系，不代表未来会重复，不构成任何投资建议。</p>"
    )
    trail = [("首页", f"{BASE_URL}/"), ("提前覆盖记录", canonical)]
    return _page(
        title=f"信息时效记录：近{days}天提前覆盖{n}次",
        description=f"DeepFocus 近 {days} 天信息时效记录：站内信号提前覆盖 {n} 次、平均领先 {avg_lead} 小时，"
                    "每条附时间戳与佐证链接，可逐条溯源。仅为发布时间事实统计，不构成投资建议。",
        body="".join(parts),
        canonical=canonical,
        graph=_graph(_breadcrumb_node(trail)),
        cta_href=APP_URL,
        cta_text="到 DeepFocus 看实时信号 →",
    )


# --------------------------------------------------------------------------- #
# 龙虎榜每日全榜公开页（/lhb）：交易所公开事实的确定性榜单（零 AI 叙述）。
# ⚠️措辞铁律：只呈现榜单事实 + 来源标注 + 免责，不加「游资看好/资金抢筹」类任何解读。
# --------------------------------------------------------------------------- #
def _fmt_net_yi(net: Any) -> str:
    """净买额（元）→ 「±X.XX 亿」/「±X 万」可读文本；缺失 → —。"""
    if not isinstance(net, (int, float)):
        return "—"
    if abs(net) >= 1e8:
        return f"{net / 1e8:+.2f} 亿"
    return f"{net / 1e4:+.0f} 万"


def render_lhb_page_html(data: Optional[dict], page_url: str = "") -> str:
    """某日 A股龙虎榜全榜单页。data 为 dragon_tiger.fetch_daily_billboard() 输出（可 None=暂无数据）。

    确定性交易所公开数据（非 AI 生成）→ ai_generated=False。"""
    rec = data or {}
    date = str(rec.get("date") or "").strip()
    items = rec.get("items") or []
    title = f"{date} A股龙虎榜全榜单" if date else "A股龙虎榜全榜单"
    canonical = page_url or f"{BASE_URL}/lhb"

    parts: list[str] = [f"<h1>{_esc(title)}</h1>"]
    parts.append(
        '<div class="meta">交易所每日公布的异常波动个股买卖席位榜单 · 数据来源：交易所公开信息（东方财富数据中心整理） · 仅为事实呈现，不构成投资建议</div>'
    )
    if items:
        parts.append(
            f'<div class="tldr"><b>榜单速览</b>：{_esc(date)} 共 {_esc(len(items))} 只个股登上龙虎榜。'
            "下表为各股当日涨跌幅、上榜原因与龙虎榜买卖净额（交易所公开数据）。</div>"
        )
        rows = []
        for it in items:
            pct = it.get("change_rate")
            pct_txt = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else "—"
            pct_cls = "up" if isinstance(pct, (int, float)) and pct >= 0 else "down"
            rows.append(
                f'<tr><td>{_esc(it.get("code"))}</td><td>{_esc(it.get("name"))}</td>'
                f'<td class="{pct_cls}">{_esc(pct_txt)}</td>'
                f'<td>{_esc((it.get("reason") or "")[:40])}</td>'
                f'<td>{_esc(_fmt_net_yi(it.get("net")))}</td></tr>'
            )
        parts.append(
            "<table><thead><tr><th>代码</th><th>名称</th><th>涨跌幅</th><th>上榜原因</th><th>净买额</th></tr></thead>"
            f'<tbody>{"".join(rows)}</tbody></table>'
        )
    else:
        parts.append("<p>该日暂无龙虎榜数据（非交易日或数据源暂不可用），交易日收盘后自动更新。</p>")
    parts.append(
        "<h2>关于龙虎榜</h2>"
        "<p>龙虎榜是交易所公布的当日异常波动（如涨跌幅、换手率居前）个股的买卖席位明细，用于提高交易透明度。"
        "本页仅按交易所公开口径罗列榜单事实，不代表任何方向性判断，不构成投资建议。</p>"
    )
    trail = [("首页", f"{BASE_URL}/"), ("龙虎榜", f"{BASE_URL}/lhb")]
    if date:
        trail.append((title[:30], canonical))
    return _page(
        title=title,
        description=f"{title}：{len(items)} 只上榜个股的代码、涨跌幅、上榜原因与买卖净额，交易所公开数据每日更新。仅为事实呈现，不构成投资建议。",
        body="".join(parts),
        canonical=canonical,
        graph=_graph(_breadcrumb_node(trail)),
        ai_generated=False,
    )


# --------------------------------------------------------------------------- #
# 投研问答公开页（C3）：微信真实高频提问 + 已落库答案 → 可索引/可被 AI 引用的 Q&A 页
# --------------------------------------------------------------------------- #
def qa_public_answer(raw: str) -> str:
    """把缓存的 wx_qa 答案过公开出口双护栏：合规中性化 + 泄密扫描（剥数据源/工具名/密钥）。

    与微信 1:1 回复出口同款护栏（weixin_channel._clean）；公开页是更高曝光面，必须同样净化。
    """
    return scrub_internal_text(neutralize_text(str(raw or ""))).strip()


def qa_indexable(qa: dict[str, Any]) -> bool:
    """C7 同源门控：问题与净化后答案都够实才进索引/sitemap（挡空/极短答案薄页）。"""
    q = str(qa.get("q") or "").strip()
    ans = qa_public_answer(qa.get("answer") or "")
    return bool(q) and len(ans) >= 80


def render_qa_page_html(qa: dict[str, Any], related: Optional[list[dict[str, Any]]] = None,
                        page_url: str = "") -> str:
    q = str(qa.get("q") or "").strip()
    answer = qa_public_answer(qa.get("answer") or "")
    slug = str(qa.get("slug") or qa.get("fp") or "")
    thin = not qa_indexable(qa)
    title = (q[:58] if q else "投研问答")
    canonical = page_url or f"{BASE_URL}/qa/{slug}"

    parts = [f"<h1>{_esc(q or '投研问答')}</h1>",
             '<div class="meta">DeepFocus AI 投研问答 · 自动生成，仅供研究参考</div>']
    # answer-first：首段直答（AI 引擎偏好整段抽取）
    paras = [ln for ln in answer.splitlines() if ln.strip()] or [answer]
    parts.append(f'<div class="lead">{_esc(paras[0][:600])}</div>')
    for ln in paras[1:]:
        parts.append(f"<p>{_esc(ln)}</p>")
    parts.append('<a class="cta" href="' + _esc(APP_URL) + '">在 DeepFocus 问更多 →</a>')

    rel_chips = "".join(
        f'<a href="{_esc(BASE_URL)}/qa/{_esc(r.get("slug") or r.get("fp"))}">{_esc((r.get("q") or "")[:24])}</a>'
        for r in (related or [])[:10]
        if (r.get("slug") or r.get("fp")) and (r.get("slug") or r.get("fp")) != slug and (r.get("q") or "").strip()
    )
    if rel_chips:
        parts.append(f'<h2>相关问答</h2><div class="chips">{rel_chips}</div>')

    graph = None
    if not thin:
        faq = {"@type": "FAQPage", "inLanguage": "zh-CN",
               "mainEntity": [{"@type": "Question", "name": q,
                               "acceptedAnswer": {"@type": "Answer", "text": answer[:1800]}}]}
        trail = [("首页", f"{BASE_URL}/"), ("投研问答", f"{BASE_URL}/qa"), (title, canonical)]
        graph = _graph(_breadcrumb_node(trail), faq)
    return _page(
        title=title,
        description=answer[:200] or "DeepFocus AI 投研问答。",
        body="".join(p for p in parts if p),
        canonical=canonical,
        graph=graph,
        noindex=thin,
        image=f"{BASE_URL}/og/qa/{slug}.png",
        ai_generated=True,  # 问答答案为 AI 生成 → 标识
    )


def render_qa_hub_html(items: list[dict[str, Any]]) -> str:
    rows = "".join(
        f'<div class="dim"><div class="hl"><a style="color:#9fd9c3;text-decoration:none" '
        f'href="{_esc(BASE_URL)}/qa/{_esc(it.get("slug") or it.get("fp"))}">{_esc((it.get("q") or "")[:48])}</a></div>'
        f'<ul><li>{_esc(qa_public_answer(it.get("answer") or "")[:80])}</li></ul></div>'
        for it in items if (it.get("slug") or it.get("fp")) and (it.get("q") or "").strip()
    ) or "<p>暂无问答。</p>"
    body = ("<h1>投研问答 · 大家都在问</h1>"
            '<div class="meta">DeepFocus 用户高频提问与 AI 解答，自动沉淀，仅供研究参考</div>' + rows)
    trail = [("首页", f"{BASE_URL}/"), ("投研问答", f"{BASE_URL}/qa")]
    return _page(
        title="投研问答 · 大家都在问",
        description="DeepFocus 用户最关心的股票与市场问题，AI 多维取数解答，每问一页。仅供研究参考，不构成投资建议。",
        body=body,
        canonical=f"{BASE_URL}/qa",
        graph=_graph(_breadcrumb_node(trail)),
    )


# --------------------------------------------------------------------------- #
# 财经术语科普页（C12）：什么是市盈率/换手率/MACD… 常青教育长尾 + 内链到个股/问答
# --------------------------------------------------------------------------- #
def render_learn_page_html(term: dict[str, Any], related: Optional[list[dict[str, Any]]] = None,
                           page_url: str = "") -> str:
    slug = str(term.get("slug") or "")
    name = str(term.get("term") or "")
    defi = str(term.get("definition") or "")
    title = f"{name}是什么？一文看懂"
    canonical = page_url or f"{BASE_URL}/learn/{slug}"
    parts = [f"<h1>{_esc(name)}</h1>",
             '<div class="meta">DeepFocus 财经术语 · 投资科普 · 仅供学习参考</div>',
             f'<div class="lead">{_esc(defi)}</div>']
    if term.get("aliases"):
        parts.append(f'<p style="color:#8b939b;font-size:13px">又称：{_esc("、".join(term["aliases"]))}</p>')
    if related:
        chips = "".join(
            f'<a href="{_esc(BASE_URL)}/learn/{_esc(r.get("slug"))}">{_esc(r.get("term"))}</a>'
            for r in related if r.get("slug"))
        if chips:
            parts.append(f'<h2>相关术语</h2><div class="chips">{chips}</div>')
    parts.append(
        '<h2>在 DeepFocus 用它做研究</h2><div class="chips">'
        f'<a href="{_esc(BASE_URL)}/stocks">热门个股速判</a>'
        f'<a href="{_esc(BASE_URL)}/stocks/all">全部 A 股</a>'
        f'<a href="{_esc(BASE_URL)}/qa">投研问答</a></div>')
    faq = {"@type": "FAQPage", "inLanguage": "zh-CN",
           "mainEntity": [{"@type": "Question", "name": f"{name}是什么？",
                           "acceptedAnswer": {"@type": "Answer", "text": defi}}]}
    trail = [("首页", f"{BASE_URL}/"), ("财经术语", f"{BASE_URL}/learn"), (name, canonical)]
    return _page(
        title=title,
        description=defi[:200],
        body="".join(parts),
        canonical=canonical,
        graph=_graph(_breadcrumb_node(trail), faq),
    )


def render_learn_hub_html(terms: list[dict[str, Any]]) -> str:
    rows = "".join(
        f'<a href="{_esc(BASE_URL)}/learn/{_esc(t.get("slug"))}">{_esc(t.get("term"))}</a>'
        for t in terms if t.get("slug"))
    body = ("<h1>财经术语库 · 投资科普</h1>"
            '<div class="meta">市盈率 / 换手率 / MACD / 杯柄形态… 一文看懂，纯科普，不构成投资建议</div>'
            f'<div class="chips">{rows}</div>')
    trail = [("首页", f"{BASE_URL}/"), ("财经术语", f"{BASE_URL}/learn")]
    return _page(
        title="财经术语库 · 投资科普",
        description="DeepFocus 财经术语科普：市盈率、市净率、ROE、换手率、北向资金、MACD、杯柄形态等常用概念一文看懂。",
        body=body,
        canonical=f"{BASE_URL}/learn",
        graph=_graph(_breadcrumb_node(trail)),
    )


# --------------------------------------------------------------------------- #
# 站点地图 / robots / llms.txt
# --------------------------------------------------------------------------- #
def _sitemap_url(loc: str, lastmod: str = "", changefreq: str = "", priority: str = "") -> str:
    bits = [f"<loc>{html.escape(loc)}</loc>"]
    iso = _iso(lastmod)
    if iso:
        bits.append(f"<lastmod>{html.escape(iso)}</lastmod>")
    if changefreq:
        bits.append(f"<changefreq>{changefreq}</changefreq>")
    if priority:
        bits.append(f"<priority>{priority}</priority>")
    return "<url>" + "".join(bits) + "</url>"


def render_sitemap_xml(
    review_dates: list[str],
    symbols: list[str],
    article_ids: Optional[list[str]] = None,
    lastmod_map: Optional[dict[str, str]] = None,
    qa_slugs: Optional[list[str]] = None,
    report_ids: Optional[list[str]] = None,
    flash_ids: Optional[list[str]] = None,
    note_ids: Optional[list[str]] = None,
) -> str:
    """站点地图：静态页固定优先级，内容页带真实 lastmod（lastmod_map 按完整 URL 提供时间戳）。"""
    lm = lastmod_map or {}
    entries: list[str] = [
        _sitemap_url(f"{BASE_URL}/", changefreq="daily", priority="1.0"),
        _sitemap_url(f"{BASE_URL}/review", changefreq="daily", priority="0.9"),
        _sitemap_url(f"{BASE_URL}/stocks", changefreq="daily", priority="0.8"),
        _sitemap_url(f"{BASE_URL}/stocks/all", changefreq="daily", priority="0.7"),
        _sitemap_url(f"{BASE_URL}/qa", changefreq="daily", priority="0.7"),
        _sitemap_url(f"{BASE_URL}/articles", changefreq="hourly", priority="0.7"),
        _sitemap_url(f"{BASE_URL}/reports", changefreq="daily", priority="0.7"),
        _sitemap_url(f"{BASE_URL}/track-record", changefreq="daily", priority="0.7"),
        _sitemap_url(f"{BASE_URL}/lhb", changefreq="daily", priority="0.7"),
        _sitemap_url(f"{BASE_URL}/notes", changefreq="hourly", priority="0.7"),
    ]
    for slug in (qa_slugs or []):
        u = f"{BASE_URL}/qa/{slug}"
        entries.append(_sitemap_url(u, lastmod=lm.get(u), changefreq="monthly", priority="0.5"))
    from .glossary import GLOSSARY  # 财经术语科普页（静态常青）
    entries.append(_sitemap_url(f"{BASE_URL}/learn", changefreq="weekly", priority="0.6"))
    for t in GLOSSARY:
        entries.append(_sitemap_url(f"{BASE_URL}/learn/{t['slug']}", changefreq="monthly", priority="0.5"))
    for d in review_dates:
        u = f"{BASE_URL}/review/{d}"
        entries.append(_sitemap_url(u, lastmod=lm.get(u) or d, changefreq="weekly", priority="0.8"))
    for s in symbols:
        u = f"{BASE_URL}/stock/{s}"
        entries.append(_sitemap_url(u, lastmod=lm.get(u), changefreq="daily", priority="0.6"))
    for a in (article_ids or []):
        u = f"{BASE_URL}/article/{a}"
        entries.append(_sitemap_url(u, lastmod=lm.get(u), changefreq="weekly", priority="0.5"))
    for fid in (flash_ids or []):        # 快讯：同 /article/{id} 落地页，时效性高→hourly/低优先级
        u = f"{BASE_URL}/article/{fid}"
        entries.append(_sitemap_url(u, lastmod=lm.get(u), changefreq="hourly", priority="0.4"))
    for nid in (note_ids or []):         # 机构纪要 → /note/{id}
        u = f"{BASE_URL}/note/{nid}"
        entries.append(_sitemap_url(u, lastmod=lm.get(u), changefreq="weekly", priority="0.5"))
    for rid in (report_ids or []):
        u = f"{BASE_URL}/report/{rid}"
        entries.append(_sitemap_url(u, lastmod=lm.get(u), changefreq="weekly", priority="0.5"))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + "".join(entries) + "</urlset>"
    )


# 显式放行各搜索/AI 爬虫（默认即放行，但显式声明对国内引擎与 AI 引擎更稳，且文档化我们的态度=欢迎引用）。
_FRIENDLY_BOTS = [
    # 通用搜索
    "Baiduspider", "Sogou web spider", "360Spider", "Bytespider", "YisouSpider",
    "Googlebot", "Bingbot", "Yandex", "DuckDuckBot", "Applebot",
    # 生成式 AI 引擎（GEO：希望被它们抓取与引用）
    "GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot", "anthropic-ai", "Claude-Web",
    "PerplexityBot", "Perplexity-User", "Google-Extended", "cohere-ai", "Amazonbot",
    "Bytespider", "Diffbot", "Meta-ExternalAgent",
]


def render_robots_txt() -> str:
    lines = ["# DeepFocus / daocaijing.com — 欢迎搜索与生成式 AI 引擎抓取公开内容（仅供研究参考，不构成投资建议）"]
    # 每个已知友好爬虫一条 stanza：放行全站、屏蔽 API。
    for bot in dict.fromkeys(_FRIENDLY_BOTS):  # 去重保序
        lines.append(f"User-agent: {bot}")
        lines.append("Allow: /")
        lines.append("Disallow: /api/")
        lines.append("")
    # 兜底：其余爬虫同样放行公开内容、屏蔽 API。
    lines += ["User-agent: *", "Allow: /", "Disallow: /api/", ""]
    lines.append(f"Sitemap: {BASE_URL}/sitemap.xml")
    return "\n".join(lines) + "\n"


def render_llms_txt() -> str:
    """llms.txt（GEO）：给 AI 引擎一张「这站有什么可引用内容、在哪」的速查表（markdown）。"""
    return f"""# DeepFocus 金融数据 (daocaijing.com)

> 面向中文投资者的 AI 投研工作台：A股每日收盘复盘、个股多维证据速判、财经资讯聚合与 AI 解读。
> 下列页面为证据引擎每日自动生成的常青内容，结构清晰、自带日期与证据，欢迎检索与引用。
> 免责声明：所有内容由机器自动生成，仅供研究参考，不构成任何投资建议。

## 可引用内容
- [A股每日复盘归档]({BASE_URL}/review)：每个交易日自动生成的大盘指数 / 领涨领跌板块 / 资金动向复盘，并标注我们提前发现的资讯如何被当日行情验证。
- [热门个股多维证据速判]({BASE_URL}/stocks)：动量 / 催化 / 估值 / 资金面等多维证据，含信号方向、证据与置信度。单只个股见 {BASE_URL}/stock/{{symbol}}（如 {BASE_URL}/stock/AAPL）；全市场索引见 {BASE_URL}/stocks/all。
- [投研问答]({BASE_URL}/qa)：用户高频股票 / 市场问题的 AI 多维取数解答，answer-first + FAQ 结构，每问一页。
- [财经术语科普]({BASE_URL}/learn)：市盈率 / 换手率 / ROE / MACD / 杯柄形态等常用概念一文看懂（纯科普）。
- [财经资讯文章]({BASE_URL}/articles)：聚合财经资讯（公开为标题 + 来源 + 摘要，全文需登录）。
- [实时快讯]({BASE_URL}/articles)：A股实时财经快讯，比券商 App 早一步。每条快讯见 {BASE_URL}/article/{{id}}（标题 + 摘要公开可引用）。
- [机构纪要]({BASE_URL}/notes)：机构调研会议纪要 / 个股动态点评聚合，每条含标题与摘要。单条见 {BASE_URL}/note/{{id}}。

## 站点地图
- {BASE_URL}/sitemap.xml
- {BASE_URL}/feed.xml（RSS 增量）

## 引用规范
- 来源请注明 DeepFocus（{BASE_URL}）。
- 内容随行情每日更新，引用时请带上页面标注的日期。
- 不要把页面中的研判当作投资建议；它们是确定性多因子证据引擎的中性化结论。
"""


def render_feed_xml(reviews: list[dict[str, Any]], articles: list[dict[str, Any]]) -> str:
    """RSS 2.0（C6）：每日复盘 + 资讯增量发现通道，喂百度/豆包/Kimi 等的 feed 抓取。"""
    items: list[tuple[str, str, str]] = []
    for r in reviews[:25]:
        d = str(r.get("date") or "")
        if not d:
            continue
        label = str(r.get("session_label") or "收盘复盘")
        items.append((f"{d} A股{label}", f"{BASE_URL}/review/{d}",
                      neutralize_text(str(r.get("one_liner") or ""))))
    for a in articles[:25]:
        aid = str(a.get("id") or "")
        if not aid:
            continue
        items.append((str(a.get("title") or "资讯"), f"{BASE_URL}/article/{aid}",
                      _teaser(a.get("content") or "", 160)))
    item_xml = "".join(
        f"<item><title>{html.escape(t)}</title><link>{html.escape(link)}</link>"
        f"<guid>{html.escape(link)}</guid><description>{html.escape(desc)}</description></item>"
        for t, link, desc in items
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel>'
        '<title>DeepFocus · A股每日复盘与财经资讯</title>'
        f'<link>{html.escape(BASE_URL)}/</link>'
        '<description>每个交易日自动生成的 A 股复盘与财经资讯（仅供研究参考，不构成投资建议）。</description>'
        '<language>zh-CN</language>'
        f'{item_xml}</channel></rss>'
    )


def render_error_html(message: str = "页面暂时无法生成，请稍后再试。") -> str:
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<title>暂时无法生成 · DeepFocus</title><meta name="robots" content="noindex"></head>'
        '<body style="font-family:sans-serif;max-width:520px;margin:60px auto;padding:0 20px;color:#333">'
        f"<h1>稍后再试</h1><p>{html.escape(message)}</p></body></html>"
    )
