from __future__ import annotations

import html
import json
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Optional

from .shared_utils import utc_now_iso
from .schemas import ShareSnapshotCreateRequest, ShareSnapshotRecord

"""
公开只读分享页（拉新 / SEO）。

把一条 AI 结论存成快照，对外提供：
- JSON：POST /api/share/snapshots、GET /api/share/snapshots/{id}
- 可被搜索引擎收录、社交可预览的服务端渲染 HTML：GET /s/{id}

HTML 由后端直出（带 <title> / description / og:* meta），免登录、不依赖前端 JS——
这是 SPA 架构下拿到真 SEO 与社交预览的关键。所有用户内容均 HTML 转义后注入。
"""

DB_PATH = Path(
    os.getenv(
        "DEEPFOCUS_SHARE_SNAPSHOT_DB_PATH",
        str(Path(__file__).resolve().parents[1] / ".share_snapshots.sqlite3"),
    )
)

# 公开页底部「打开 DeepFocus」CTA 指向的应用地址（部署后配置；缺省相对根路径）。
APP_URL = os.getenv("DEEPFOCUS_PUBLIC_APP_URL", "/").strip() or "/"
# 社交预览封面图（og:image）；配置了才输出，避免坏图。部署后指向静态/动态封面。
OG_IMAGE = os.getenv("DEEPFOCUS_PUBLIC_OG_IMAGE", "").strip()


def init_share_snapshot_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS share_snapshots (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                byline TEXT,
                kind TEXT NOT NULL,
                views INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def create_share_snapshot(request: ShareSnapshotCreateRequest) -> ShareSnapshotRecord:
    init_share_snapshot_db()
    record = {
        "id": uuid.uuid4().hex[:12],
        "title": (request.title or "").strip()[:200] or "DeepFocus 投研结论",
        "summary": (request.summary or "").strip()[:8000],
        "byline": (request.byline or "").strip() or None,
        "kind": (request.kind or "conclusion").strip()[:40] or "conclusion",
        "views": 0,
        "created_at": utc_now_iso(),
    }
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO share_snapshots (id, title, summary, byline, kind, views, created_at)
            VALUES (:id, :title, :summary, :byline, :kind, :views, :created_at)
            """,
            record,
        )
        conn.commit()
    return _row_to_snapshot(record)


def get_share_snapshot(snapshot_id: str) -> Optional[ShareSnapshotRecord]:
    init_share_snapshot_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM share_snapshots WHERE id = ?", (snapshot_id,)
        ).fetchone()
    return _row_to_snapshot(dict(row)) if row else None


def increment_share_views(snapshot_id: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE share_snapshots SET views = views + 1 WHERE id = ?", (snapshot_id,))
        conn.commit()


def render_share_page_html(record: ShareSnapshotRecord, page_url: str = "") -> str:
    title = html.escape(record.title)
    # description / og 摘要：压成单行、截断，供搜索与社交预览。
    desc_raw = " ".join(record.summary.split())[:200]
    description = html.escape(desc_raw)
    byline = html.escape(record.byline) if record.byline else ""
    paragraphs = "".join(
        f"<p>{html.escape(line)}</p>"
        for line in record.summary.splitlines()
        if line.strip()
    ) or f"<p>{description}</p>"
    cta = html.escape(APP_URL)

    # 规范链接 + og:url（有页面地址时）。
    canonical = html.escape(page_url) if page_url else ""
    canonical_tags = (
        f'<link rel="canonical" href="{canonical}">\n<meta property="og:url" content="{canonical}">\n'
        if canonical else ""
    )
    # 社交封面：配置了才输出，并升级为大图卡片。
    image_tags = (
        f'<meta property="og:image" content="{html.escape(OG_IMAGE)}">\n'
        f'<meta name="twitter:image" content="{html.escape(OG_IMAGE)}">\n'
        if OG_IMAGE else ""
    )
    twitter_card = "summary_large_image" if OG_IMAGE else "summary"
    # schema.org 结构化数据（富搜索结果）。转义 < 防 </script> 突破。
    json_ld = json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "Article",
            "headline": record.title[:110],
            "description": desc_raw,
            "datePublished": record.created_at,
            "author": {"@type": "Organization", "name": record.byline or "DeepFocus 投研工作台"},
            "publisher": {"@type": "Organization", "name": "DeepFocus 投研工作台"},
            **({"url": page_url, "mainEntityOfPage": page_url} if page_url else {}),
        },
        ensure_ascii=False,
    ).replace("<", "\\u003c")

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · DeepFocus 投研</title>
<meta name="description" content="{description}">
<meta name="robots" content="index,follow">
{canonical_tags}<meta property="og:type" content="article">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<meta property="og:site_name" content="DeepFocus 投研工作台">
{image_tags}<meta name="twitter:card" content="{twitter_card}">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{description}">
<script type="application/ld+json">{json_ld}</script>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ margin:0; background:#0b0e11; color:#e6e8eb; font:16px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif; }}
  .wrap {{ max-width:680px; margin:0 auto; padding:48px 20px 64px; }}
  .brand {{ color:#10b981; font-weight:600; font-size:14px; letter-spacing:.02em; }}
  h1 {{ font-size:26px; line-height:1.35; margin:14px 0 18px; }}
  article p {{ margin:0 0 14px; color:#c7ccd1; }}
  .byline {{ margin-top:20px; color:#8b939b; font-size:13px; }}
  .cta {{ display:inline-block; margin-top:28px; padding:11px 20px; border-radius:10px; background:#10b981; color:#04130d; font-weight:600; text-decoration:none; }}
  footer {{ margin-top:36px; padding-top:16px; border-top:1px solid #20262c; color:#6b7782; font-size:12px; }}
</style>
</head>
<body>
<main class="wrap">
  <div class="brand">◆ DeepFocus 投研工作台</div>
  <h1>{title}</h1>
  <article>{paragraphs}</article>
  {f'<div class="byline">{byline}</div>' if byline else ''}
  <a class="cta" href="{cta}">在 DeepFocus 上做深度研究 →</a>
  <footer>本页为 AI 生成的投研结论快照，仅供研究参考，不构成投资建议。</footer>
</main>
</body>
</html>"""


def render_not_found_html() -> str:
    return (
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<title>结论不存在 · DeepFocus</title><meta name=\"robots\" content=\"noindex\"></head>"
        "<body style=\"font-family:sans-serif;max-width:520px;margin:60px auto;padding:0 20px;color:#333\">"
        "<h1>结论不存在或已过期</h1><p>这条分享链接可能已被删除。</p></body></html>"
    )


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_snapshot(row: dict[str, Any]) -> ShareSnapshotRecord:
    return ShareSnapshotRecord(
        id=row["id"],
        title=row["title"],
        summary=row.get("summary") or "",
        byline=row.get("byline"),
        kind=row.get("kind") or "conclusion",
        views=int(row.get("views") or 0),
        created_at=row["created_at"],
    )
