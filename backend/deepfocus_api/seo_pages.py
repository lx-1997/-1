"""公开 SEO 落地页（曝光增长）：每日复盘页 + 个股速判卡页 + 聚合页 + 站点地图。

与 share_snapshots.py（一次性结论快照）互补：这里是**机器每天自动生产的常青内容**——
- /review/{date}  每个交易日一篇收盘复盘 → 长期积累成可收录页面库
- /stock/{symbol} 个股速判卡落地页 → 承接「XX股票 怎么样/估值」长尾搜索
- /review、/stocks 聚合页 + /sitemap.xml、/robots.txt → 给爬虫一条完整的发现路径

页面之间互相内链（复盘→近期复盘、个股→相关热门个股），既是站内推荐也是 SEO 内链。
所有用户/数据内容均 html.escape 后注入；薄内容页（数据不足）输出 noindex。
"""
from __future__ import annotations

import html
import json
import os
from typing import Any, Optional

# 站点绝对地址（sitemap/canonical 用）；默认生产域名，可被环境变量覆盖。
BASE_URL = (os.getenv("DEEPFOCUS_PUBLIC_BASE_URL", "https://daocaijing.com").strip().rstrip("/")
            or "https://daocaijing.com")
APP_URL = os.getenv("DEEPFOCUS_PUBLIC_APP_URL", "/").strip() or "/"

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


def _json_ld(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")


def _page(
    *,
    title: str,
    description: str,
    body: str,
    canonical: str = "",
    json_ld: Optional[dict[str, Any]] = None,
    noindex: bool = False,
) -> str:
    """统一页面外壳：meta/og/结构化数据 + 与分享页同源的暗色排版。入参 title/description 需未转义原文。"""
    t = _esc(title)
    desc = _esc(" ".join(str(description).split())[:200])
    canonical_tags = (
        f'<link rel="canonical" href="{_esc(canonical)}">\n<meta property="og:url" content="{_esc(canonical)}">\n'
        if canonical else ""
    )
    robots = "noindex,nofollow" if noindex else "index,follow"
    ld_tag = f'<script type="application/ld+json">{_json_ld(json_ld)}</script>' if json_ld else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{t} · DeepFocus 投研</title>
<meta name="description" content="{desc}">
<meta name="robots" content="{robots}">
{canonical_tags}<meta property="og:type" content="article">
<meta property="og:title" content="{t}">
<meta property="og:description" content="{desc}">
<meta property="og:site_name" content="DeepFocus 投研工作台">
<meta name="twitter:card" content="summary">
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
  .cta {{ display:inline-block; margin-top:26px; padding:11px 20px; border-radius:10px; background:#10b981; color:#04130d; font-weight:600; text-decoration:none; }}
  footer {{ margin-top:36px; padding-top:16px; border-top:1px solid #20262c; color:#6b7782; font-size:12px; }}
  footer a {{ color:#6b7782; }}
</style>
</head>
<body>
<main class="wrap">
  <div class="brand"><a href="{_esc(BASE_URL)}/">◆ DeepFocus 投研工作台</a></div>
  {body}
  <a class="cta" href="{_esc(APP_URL)}">在 DeepFocus 上做深度研究 →</a>
  <footer>{_esc(_DISCLAIMER)} · <a href="{_esc(BASE_URL)}/review">每日复盘</a> · <a href="{_esc(BASE_URL)}/stocks">热门个股速判</a></footer>
</main>
</body>
</html>"""


# --------------------------------------------------------------------------- #
# 每日复盘页
# --------------------------------------------------------------------------- #
def render_review_page_html(review: dict[str, Any], recent: list[dict[str, Any]], page_url: str = "") -> str:
    date = str(review.get("date") or "")
    session_label = str(review.get("session_label") or ("午盘复盘" if review.get("session") == "midday" else "收盘复盘"))
    nar = review.get("narrative") or {}
    one_liner = str(nar.get("one_liner") or "")
    title = f"{date} A股{session_label}：{one_liner[:40]}" if one_liner else f"{date} A股{session_label}"

    parts: list[str] = [f"<h1>{_esc(title)}</h1>"]
    parts.append(f'<div class="meta">{_esc(date)} · {_esc(session_label)} · DeepFocus 自动复盘</div>')
    if one_liner:
        parts.append(f'<div class="lead">{_esc(one_liner)}</div>')

    indices = review.get("indices") or []
    if indices:
        rows = "".join(
            f"<tr><td>{_esc(i.get('name'))}</td><td>{_esc(i.get('close'))}</td>"
            f"<td class=\"{'up' if (i.get('pct') or 0) > 0 else 'down'}\">{_esc(_fmt_pct(i.get('pct')))}</td></tr>"
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
                f"<div class=\"dim\"><div class=\"hl\">{_esc(e.get('name'))} · {_esc(e.get('theme') or e.get('kind'))} "
                f"<span class=\"{'up' if (e.get('pct') or 0) > 0 else 'down'}\">{_esc(_fmt_pct(e.get('pct')))}</span></div>"
                f"<ul><li>{_esc(lead_txt)}在 DeepFocus 出现相关信号：{_esc((e.get('evidence') or '')[:160])}</li></ul></div>"
            )
        parts.append("<h2>我们提前发现了什么</h2>" + "".join(rows))

    for label, key in (("市场怎么走", "market"), ("板块在交易什么", "sectors"), ("资金动向", "funds"), ("下一交易日", "tomorrow")):
        text = str(nar.get(key) or "").strip()
        if text:
            paras = "".join(f"<p>{_esc(line)}</p>" for line in text.splitlines() if line.strip())
            parts.append(f"<h2>{_esc(label)}</h2>{paras}")

    others = [r for r in recent if r.get("date") and r.get("date") != date][:10]
    if others:
        links = "".join(
            f'<a href="{_esc(BASE_URL)}/review/{_esc(r["date"])}">{_esc(r["date"])} {_esc(r.get("session_label") or "")}</a>'
            for r in others
        )
        parts.append(f'<h2>近期复盘</h2><div class="chips">{links}</div>')

    json_ld = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": title[:110],
        "description": one_liner[:200],
        "datePublished": str(review.get("generated_at") or date),
        "author": {"@type": "Organization", "name": "DeepFocus 投研工作台"},
        "publisher": {"@type": "Organization", "name": "DeepFocus 投研工作台"},
        **({"url": page_url, "mainEntityOfPage": page_url} if page_url else {}),
    }
    return _page(
        title=title,
        description=one_liner or f"{date} A股{session_label}：大盘、板块、资金与我们提前发现的资讯复盘。",
        body="".join(parts),
        canonical=page_url or f"{BASE_URL}/review/{date}",
        json_ld=json_ld,
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
    return _page(
        title="A股每日复盘归档",
        description="DeepFocus 每个交易日自动生成的 A 股复盘：大盘指数、领涨领跌板块、资金动向，以及我们提前发现的资讯如何被当日行情验证。",
        body=body,
        canonical=f"{BASE_URL}/review",
    )


# --------------------------------------------------------------------------- #
# 个股速判卡页
# --------------------------------------------------------------------------- #
def render_stock_page_html(ts: dict[str, Any], related: list[dict[str, Any]], page_url: str = "") -> str:
    symbol = str(ts.get("symbol") or "").upper()
    name = str(ts.get("name") or symbol)
    verdict = str(ts.get("overall_verdict") or "数据不足")
    thin = verdict == "数据不足"  # 薄内容不进索引，避免拉低站点质量
    title = f"{name}({symbol}) 多维证据速判：{verdict}"

    price_bit = ""
    if ts.get("price") is not None:
        price_bit = f"{ts.get('currency') or ''} {ts.get('price')}（{_fmt_pct(ts.get('change_percent'))}）"
    conf = ts.get("confidence")
    conf_txt = f"置信度 {float(conf) * 100:.0f}%" if isinstance(conf, (int, float)) else ""

    parts = [f"<h1>{_esc(title)}</h1>"]
    parts.append(
        f'<div class="meta">{_esc(price_bit)} · 综合分 {_esc(ts.get("overall_score"))} · {_esc(conf_txt)}'
        f' · 生成于 {_esc(str(ts.get("generated_at") or "")[:16].replace("T", " "))}（UTC）</div>'
    )
    narrative = str(ts.get("narrative") or "").strip()
    if narrative:
        parts.append(f'<div class="lead">{_esc(narrative[:400])}</div>')

    dims = ts.get("dimensions") or []
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

    if related:
        links = "".join(
            f'<a href="{_esc(BASE_URL)}/stock/{_esc(r["symbol"])}">{_esc(r.get("name") or r["symbol"])}</a>'
            for r in related if r.get("symbol")
        )
        parts.append(f'<h2>大家也在看</h2><div class="chips">{links}</div>')

    json_ld = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": title[:110],
        "description": narrative[:200] or f"{name}({symbol}) 多维证据速判卡。",
        "datePublished": str(ts.get("generated_at") or ""),
        "about": {"@type": "Corporation", "name": name, "tickerSymbol": symbol},
        "author": {"@type": "Organization", "name": "DeepFocus 投研工作台"},
        "publisher": {"@type": "Organization", "name": "DeepFocus 投研工作台"},
        **({"url": page_url, "mainEntityOfPage": page_url} if page_url else {}),
    }
    return _page(
        title=title,
        description=narrative or f"{name}({symbol}) 动量/催化/估值/资金面等多维证据速判，信号、证据与置信度一页看清。",
        body="".join(parts),
        canonical=page_url or f"{BASE_URL}/stock/{symbol}",
        json_ld=json_ld,
        noindex=thin,
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
    return _page(
        title="热门个股多维证据速判",
        description="DeepFocus 用户近期最关注的股票：动量、催化、估值、资金面等多维证据速判，信号与置信度一页看清。",
        body=body,
        canonical=f"{BASE_URL}/stocks",
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

    json_ld = {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": title[:110],
        "description": teaser[:200],
        "datePublished": str(article.get("created_at") or ""),
        "author": {"@type": "Organization", "name": source},
        "publisher": {"@type": "Organization", "name": "DeepFocus 投研工作台"},
        **({"url": page_url, "mainEntityOfPage": page_url} if page_url else {}),
    }
    return _page(
        title=title,
        description=teaser or f"{title} · 在 DeepFocus 阅读全文。",
        body="".join(parts),
        canonical=page_url or f"{BASE_URL}/article/{aid}",
        json_ld=json_ld,
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
    return _page(
        title="财经资讯文章",
        description="DeepFocus 聚合的财经资讯文章：登录后阅读全文，并解锁实时行情、自选与 AI 解读。",
        body=body,
        canonical=f"{BASE_URL}/articles",
    )


# --------------------------------------------------------------------------- #
# 站点地图 / robots
# --------------------------------------------------------------------------- #
def render_sitemap_xml(review_dates: list[str], symbols: list[str], article_ids: Optional[list[str]] = None) -> str:
    urls = [f"{BASE_URL}/", f"{BASE_URL}/review", f"{BASE_URL}/stocks", f"{BASE_URL}/articles"]
    urls += [f"{BASE_URL}/review/{d}" for d in review_dates]
    urls += [f"{BASE_URL}/stock/{s}" for s in symbols]
    urls += [f"{BASE_URL}/article/{a}" for a in (article_ids or [])]
    entries = "".join(f"<url><loc>{html.escape(u)}</loc></url>" for u in urls)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + entries + "</urlset>"
    )


def render_robots_txt() -> str:
    return f"User-agent: *\nAllow: /\nDisallow: /api/\nSitemap: {BASE_URL}/sitemap.xml\n"


def render_error_html(message: str = "页面暂时无法生成，请稍后再试。") -> str:
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<title>暂时无法生成 · DeepFocus</title><meta name="robots" content="noindex"></head>'
        '<body style="font-family:sans-serif;max-width:520px;margin:60px auto;padding:0 20px;color:#333">'
        f"<h1>稍后再试</h1><p>{html.escape(message)}</p></body></html>"
    )
