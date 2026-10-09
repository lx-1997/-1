"""开发者分发通道聚合：细分 RSS、策展 OpenAPI 规范（Coze/Dify/GPTs 插件导入）、开发者门户页。

- /feed/research.xml、/feed/themes.xml：被动订阅通道（RSS 阅读器/量化管道/聚合站）。
- /api/v1/openapi.json：只含 /api/v1 开放端点的精简规范，Coze(扣子)/Dify/GPTs 导入即成插件。
- /developers：开发者总入口 + Webhook 订阅管理台（同源 localStorage 登录态，与 /api/mcp/setup 同款）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from xml.sax.saxutils import escape as _xesc

from fastapi import APIRouter, Response
from fastapi.responses import HTMLResponse, JSONResponse

ISSUER = "https://daocaijing.com"
BRAND = "稻草财经"

router = APIRouter()


# --------------------------------------------------------------------------- #
# RSS
# --------------------------------------------------------------------------- #
def _rfc822(date_str: str) -> str:
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime((date_str or "").strip()[:19], fmt)
            return dt.strftime("%a, %d %b %Y %H:%M:%S +0800")
        except ValueError:
            continue
    return datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")


def _rss(title: str, link: str, description: str, items: list[dict]) -> Response:
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0"><channel>',
        f"<title>{_xesc(title)}</title>",
        f"<link>{_xesc(link)}</link>",
        f"<description>{_xesc(description)}</description>",
        f"<language>zh-cn</language>",
        f"<lastBuildDate>{datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000')}</lastBuildDate>",
        f"<generator>{BRAND} Open Channel</generator>",
    ]
    for it in items[:80]:
        parts.append("<item>")
        parts.append(f"<title>{_xesc(str(it.get('title') or '（无标题）'))}</title>")
        parts.append(f"<link>{_xesc(str(it.get('link') or link))}</link>")
        guid = it.get("guid") or it.get("link") or uuid.uuid4().hex
        parts.append(f"<guid isPermaLink=\"false\">{_xesc(str(guid))}</guid>")
        if it.get("pubDate"):
            parts.append(f"<pubDate>{_xesc(it['pubDate'])}</pubDate>")
        if it.get("description"):
            parts.append(f"<description>{_xesc(str(it['description'])[:1000])}</description>")
        parts.append("</item>")
    parts.append("</channel></rss>")
    return Response("\n".join(parts), media_type="application/rss+xml; charset=utf-8")


@router.get("/feed/research.xml")
async def feed_research() -> Response:
    from .research_wire import fetch_research_wire_online

    try:
        data = await fetch_research_wire_online(limit=50)
    except Exception:  # noqa: BLE001 - 源不可用时给空 channel，别 5xx 拖垮订阅器
        data = {}
    items = []
    for it in (data.get("items") or []) if isinstance(data, dict) else []:
        title = str(it.get("title") or "").strip()
        if not title:
            continue
        org = it.get("org") or it.get("source") or it.get("bank") or ""
        date = it.get("date") or it.get("published_at") or ""
        items.append({
            "title": f"{title}（{org}）" if org else title,
            "link": str(it.get("url") or it.get("link") or f"{ISSUER}/articles"),
            "guid": it.get("id") or it.get("url") or title,
            "description": it.get("summary") or it.get("abstract") or "",
            "pubDate": _rfc822(str(date)) if date else "",
        })
    return _rss(f"{BRAND} · 投行研报流", f"{ISSUER}/articles", "海外投行研报标题与机构（元数据，原文见官网）", items)


@router.get("/feed/themes.xml")
async def feed_themes() -> Response:
    from .theme_navigation import fetch_concept_boards

    try:
        boards = await fetch_concept_boards(limit=40)
    except Exception:  # noqa: BLE001
        boards = []
    items = []
    for b in boards or []:
        name = str(b.get("name") or "").strip()
        if not name:
            continue
        pct = b.get("change_percent") or b.get("pct") or ""
        leaders = b.get("leader_stocks") or b.get("stocks") or []
        leader_names = "、".join(
            str(s.get("name") or s) if isinstance(s, dict) else str(s) for s in (leaders[:5] if isinstance(leaders, list) else [])
        )
        desc = f"涨幅 {pct}%。" if pct != "" else ""
        if leader_names:
            desc += f" 领涨：{leader_names}"
        items.append({
            "title": f"【题材】{name}" + (f" {pct}%" if pct != "" else ""),
            "link": f"{ISSUER}/terminal",
            "guid": f"theme:{b.get('code') or name}:{datetime.now().strftime('%Y-%m-%d')}",
            "description": desc,
            "pubDate": _rfc822(datetime.now().strftime("%Y-%m-%d")),
        })
    return _rss(f"{BRAND} · A股题材热点", f"{ISSUER}/terminal", "当日概念板块涨幅榜与领涨股（确定性板块归属，非荐股）", items)


@router.get("/feed", response_class=HTMLResponse)
async def feed_index() -> HTMLResponse:
    rows = [
        ("/feed.xml", "综合订阅", "A股收盘复盘 + 资讯快讯（已有主 feed）"),
        ("/feed/research.xml", "研报流", "海外投行研报标题/机构/日期（元数据）"),
        ("/feed/themes.xml", "题材热点", "当日概念板块涨幅榜 + 领涨股"),
    ]
    lis = "\n".join(
        f'<div class="ep"><span class="m">RSS</span><code>{ISSUER}{p}</code><p class="sub">{n}——{d}</p></div>'
        for p, n, d in rows
    )
    return HTMLResponse(
        f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{BRAND} RSS 订阅源</title><style>body{{font-family:-apple-system,'PingFang SC',sans-serif;background:#0b0d12;color:#e6ebf2;max-width:760px;margin:0 auto;padding:32px 20px;line-height:1.7}}
h1{{font-size:22px}}a{{color:#6ab0ff}}code{{background:#0c1018;border:1px solid #222733;border-radius:4px;padding:1px 6px;font-size:13px;color:#9fd0ff;word-break:break-all}}
.ep{{background:#12151c;border:1px solid #222733;border-radius:10px;padding:12px 16px;margin:10px 0}}
.m{{color:#2bd96a;font-weight:700;margin-right:8px}}.sub{{color:#8a93a3;font-size:13.5px;margin:6px 0 0}}</style></head><body>
<h1>{BRAND} RSS 订阅源</h1><p class="sub">把任意 RSS 阅读器（Feedly/Inoreader/NetNewsWire…）的订阅地址粘贴即可。全部只读、无需鉴权。</p>
{lis}
<p class="sub">开发者通道（MCP / Webhook / Open API）见 <a href="/developers">/developers</a>。</p>
</body></html>"""
    )


# --------------------------------------------------------------------------- #
# 策展 OpenAPI（Coze 扣子 / Dify / GPTs 插件导入用）
# --------------------------------------------------------------------------- #
_OPENAPI: dict[str, Any] = {
    "openapi": "3.0.3",
    "info": {
        "title": "稻草财经 Open API",
        "description": "稻草财经（daocaijing.com）面向合作方/开发者的只读内容 API：A股复盘、个股速判卡、资讯流、研报元数据、定时摘要。鉴权：Header `X-API-Key`。内容仅供研究参考，不构成投资建议。",
        "version": "1.0.0",
    },
    "servers": [{"url": "https://daocaijing.com"}],
    "components": {
        "securitySchemes": {
            "ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-API-Key",
                           "description": "合作方密钥 dfk_…（联系稻草财经签发）"}
        }
    },
    "security": [{"ApiKeyAuth": []}],
    "paths": {
        "/api/v1/review/today": {
            "get": {"operationId": "getReviewToday", "summary": "最新一期A股复盘（盘中=午盘版，收盘后=收盘版）",
                    "responses": {"200": {"description": "复盘 JSON（exists=false 表示暂无）"}}}
        },
        "/api/v1/review/{date}": {
            "get": {"operationId": "getReviewByDate", "summary": "指定日期(YYYY-MM-DD)的完整复盘",
                    "parameters": [{"name": "date", "in": "path", "required": True,
                                    "schema": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"}}],
                    "responses": {"200": {"description": "复盘 JSON"}, "404": {"description": "该日期暂无复盘"}}}
        },
        "/api/v1/reviews": {
            "get": {"operationId": "listReviews", "summary": "历史复盘列表（轻量摘要）",
                    "parameters": [{"name": "limit", "in": "query", "schema": {"type": "integer", "default": 30, "maximum": 120}}],
                    "responses": {"200": {"description": "复盘列表"}}}
        },
        "/api/v1/stock/{symbol}/verdict": {
            "get": {"operationId": "getStockVerdict", "summary": "个股证据速判卡（确定性引擎：多维证据+信号灯+可信度）",
                    "parameters": [
                        {"name": "symbol", "in": "path", "required": True, "schema": {"type": "string"},
                         "description": "如 AAPL / 00700 / 600519"},
                        {"name": "name", "in": "query", "schema": {"type": "string"}, "description": "名称辅助消歧"},
                        {"name": "market", "in": "query", "schema": {"type": "string", "enum": ["US", "CN", "HK"]}}],
                    "responses": {"200": {"description": "速判卡 JSON"}, "502": {"description": "生成失败，稍后重试"}}}
        },
        "/api/v1/news": {
            "get": {"operationId": "listNews", "summary": "资讯流（快讯/文章，可按标的与关键词过滤）",
                    "parameters": [
                        {"name": "topic", "in": "query", "schema": {"type": "string", "enum": ["快讯", "文章"]}},
                        {"name": "symbol", "in": "query", "schema": {"type": "string"}},
                        {"name": "q", "in": "query", "schema": {"type": "string"}},
                        {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 80, "maximum": 200}}],
                    "responses": {"200": {"description": "{count, messages: []}"}}}
        },
        "/api/v1/research": {
            "get": {"operationId": "listResearch", "summary": "投行研报流（标题/机构/日期元数据）",
                    "parameters": [
                        {"name": "q", "in": "query", "schema": {"type": "string"}, "description": "关键词，留空=最新"},
                        {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 60, "maximum": 200}}],
                    "responses": {"200": {"description": "{count, items: []}"}}}
        },
        "/api/v1/openclaw/digest": {
            "get": {"operationId": "getDigest", "summary": "过去 N 小时快讯+研报+纪要+文章聚合摘要（机器人定时播报用）",
                    "parameters": [{"name": "hours", "in": "query", "schema": {"type": "integer", "default": 12, "maximum": 48}}],
                    "responses": {"200": {"description": "聚合摘要 JSON"}}}
        },
    },
}


@router.get("/api/v1/openapi.json", include_in_schema=False)
async def curated_openapi() -> JSONResponse:
    return JSONResponse(_OPENAPI)


# --------------------------------------------------------------------------- #
# 开发者门户（/developers）
# --------------------------------------------------------------------------- #
_DEV_PAGE_TMPL = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>稻草财经 · 开发者平台</title>
<style>
:root{--bg:#0b0d12;--panel:#12151c;--line:#222733;--text:#e6ebf2;--mute:#8a93a3;--amber:#ffb000;--green:#2bd96a;--blue:#6ab0ff;--red:#ff6b6b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:920px;margin:0 auto;padding:32px 20px 80px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:18px;margin:34px 0 10px;padding-top:10px;border-top:1px solid var(--line)}
h3{font-size:15px;margin:16px 0 6px;color:var(--amber)}
.sub{color:var(--mute)}code{background:#0c1018;border:1px solid var(--line);border-radius:4px;padding:1px 6px;font-family:ui-monospace,Menlo,monospace;font-size:13px;color:#9fd0ff;word-break:break-all}
pre{background:#0c1018;border:1px solid var(--line);border-radius:8px;padding:12px;overflow:auto;font-family:ui-monospace,Menlo,monospace;font-size:12.5px;color:#cdd6e3;white-space:pre-wrap}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:12px 0}
.note{background:rgba(255,176,0,.08);border:1px solid rgba(255,176,0,.3);border-radius:8px;padding:10px 14px;margin:12px 0;font-size:13.5px}
table{border-collapse:collapse;width:100%;margin:8px 0;font-size:13px}th,td{border:1px solid var(--line);padding:6px 9px;text-align:left}th{color:var(--mute);font-weight:600}
button{background:var(--blue);border:none;color:#08111c;font-weight:700;border-radius:7px;padding:8px 14px;cursor:pointer;font-size:13.5px}
button.ghost{background:transparent;border:1px solid var(--line);color:var(--text)}
button.danger{background:transparent;border:1px solid rgba(255,107,107,.5);color:var(--red)}
input{background:#0c1018;border:1px solid var(--line);border-radius:7px;color:var(--text);padding:8px 10px;font-size:13.5px;width:300px}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.tok{font-family:ui-monospace,Menlo,monospace;background:rgba(43,217,106,.1);border:1px dashed rgba(43,217,106,.5);border-radius:7px;padding:8px 10px;word-break:break-all;color:var(--green);font-size:13px}
.mut{color:var(--mute);font-size:12.5px}.ft{margin-top:40px;color:var(--mute);font-size:12px;border-top:1px solid var(--line);padding-top:14px}
a{color:var(--blue)}
</style></head><body><div class="wrap">
<h1>稻草财经 · 开发者平台</h1>
<p class="sub">把稻草财经的 A股复盘、个股速判卡、资讯、研报与 AI 投研问答，接入你的 AI 客户端、自动化工作流、机器人或网站。内容仅供研究参考，不构成投资建议。完整用法与最佳实践见 <a href="https://daocaijing.com/developers/guide">《开发者接入指南》</a>。</p>

<h2>① MCP —— 让任意 AI 软件「长出」稻草财经能力（推荐）</h2>
<div class="panel"><p class="sub">Claude Desktop / Claude Code / Cursor / Cherry Studio 等任何支持 MCP 的客户端，添加远程服务器：</p>
<pre>https://daocaijing.com/api/mcp</pre>
<p class="sub">首次连接会自动跳转浏览器完成<b>登录 + 授权</b>（无需手工令牌）。也可以在
<a href="/api/mcp/setup">MCP 接入控制台</a> 创建个人令牌（Bearer 方式）。12 个工具：行情/复盘/速判卡/资讯/研报元数据/题材双向/AI 问答。</p></div>

<h2>② Webhook 事件推送 —— 平台主动推进你的系统</h2>
<div class="panel">
<p class="sub">注册回调 URL，快讯入库等事件实时 POST 给你（HMAC-SHA256 签名防伪造）。适合 n8n / Dify / 量化脚本 / 自建机器人。</p>
<div id="loginHint" class="note" style="display:none">尚未检测到登录态：请先 <a href="/">打开稻草财经登录</a> 后回到本页。</div>
<div class="row"><input id="whUrl" placeholder="https://你的服务/webhook/daocaijing"><input id="whDesc" placeholder="备注（可空）" style="width:160px"><button onclick="createWh()">创建订阅</button><button class="ghost" onclick="loadWh()">刷新</button></div>
<div id="newWh" style="display:none" class="note"><b>签名密钥（仅此一次显示）：</b><div class="tok" id="newWhVal"></div></div>
<div id="whList"></div>
<h3>接收端验签示例</h3>
<pre>import hmac, hashlib
# header: X-DaoCaijing-Signature: t=1696745123,v1=ab3f...
sig = dict(kv.split('=', 1) for kv in header.split(','))
expect = hmac.new(whsec.encode(), f"{sig['t']}.{raw_body}".encode(), hashlib.sha256).hexdigest()
assert hmac.compare_digest(expect, sig['v1'])   # 校验失败 = 非稻草财经发出</pre>
<p class="sub">事件载荷：<code>{"id":"evt_…","type":"news","created_at":"…","data":{快讯字段}}</code>；连续失败 20 次自动停用。</p>
</div>

<h2>③ Open API —— 服务端只读内容接口</h2>
<div class="panel"><p class="sub">Header 传 <code>X-API-Key</code> 调用 <code>/api/v1/*</code>（合作方密钥，<a href="/api/v1/docs">完整文档</a>）。扣子(Coze)/Dify/GPTs 插件可直接导入策展规范：</p>
<pre>https://daocaijing.com/api/v1/openapi.json</pre>
<p class="sub">端点：复盘（今日/指定/历史）、个股速判卡、资讯流、研报流、定时聚合摘要。</p></div>

<h2>④ 机器人 —— 群里直接问 / 定时播报</h2>
<div class="panel"><p class="sub">飞书 / 钉钉 / 企业微信群机器人，三步接入定时播报：</p>
<pre>1. 在机器人后台建一个自定义 Webhook 机器人，拿到机器人 Webhook 地址
2. 联系稻草财经签发一把合作方密钥（X-API-Key）
3. 定时任务（cron）每 N 小时拉一次摘要并推送：
   curl -s "https://daocaijing.com/api/v1/openclaw/digest?hours=8" \\
        -H "X-API-Key: dfk_xxx"   # 返回快讯+研报+纪要+文章的聚合 JSON，自己组织文案后推群</pre>
<p class="sub">要在群里 @机器人 问答？用 MCP：把 <code>/api/mcp</code> 接进你的机器人框架（OpenClaw / Dify Agent / Coze Agent 均原生支持 MCP）。</p></div>

<h2>⑤ RSS —— 被动订阅</h2>
<div class="panel"><p class="sub">综合（复盘+快讯）<code>/feed.xml</code>；研报 <code>/feed/research.xml</code>；题材热点 <code>/feed/themes.xml</code>。索引页：<a href="/feed">/feed</a></p></div>

<p class="ft">© 稻草财经 · daocaijing.com · 内容仅供研究参考，不构成投资建议</p>
</div>
<script>
let jwt=''; try{ jwt=localStorage.getItem('auth_token')||''; }catch(e){}
const API='/api/account/webhooks';
function hdrs(){ return {'Content-Type':'application/json','Authorization':'Bearer '+jwt}; }
async function loadWh(){
  if(!jwt){ document.getElementById('loginHint').style.display='block'; return; }
  try{
    const r=await fetch(API,{headers:hdrs()});
    if(r.status===401){ document.getElementById('loginHint').style.display='block'; return; }
    const d=await r.json();
    const rows=(d.webhooks||[]).map(w=>{
      const st=w.active? '<span style="color:var(--green)">启用</span>':'<span class="mut">已停用</span>';
      return `<tr><td><code>${w.url.slice(0,42)}…</code></td><td>${w.description||'—'}</td><td>${st}</td><td>${w.delivered_count||0} 达 / ${w.fail_count||0} 败</td><td>${w.last_status??'—'}</td><td><button class="ghost" onclick="testWh('${w.id}')">测试</button> <button class="danger" onclick="delWh('${w.id}')">删除</button></td></tr>`;
    }).join('');
    document.getElementById('whList').innerHTML = rows? `<table><tr><th>回调 URL</th><th>备注</th><th>状态</th><th>投递</th><th>最近状态码</th><th></th></tr>${rows}</table>` : '<span class="mut">还没有订阅，上面创建一个。</span>';
  }catch(e){ document.getElementById('whList').innerHTML='<span class="mut">加载失败：'+e+'</span>'; }
}
async function createWh(){
  if(!jwt){ document.getElementById('loginHint').style.display='block'; return; }
  const r=await fetch(API,{method:'POST',headers:hdrs(),body:JSON.stringify({url:document.getElementById('whUrl').value.trim(),events:['news'],description:document.getElementById('whDesc').value.trim()})});
  const d=await r.json().catch(()=>({}));
  if(!r.ok){ alert(d.detail||('创建失败 '+r.status)); return; }
  document.getElementById('newWh').style.display='block';
  document.getElementById('newWhVal').textContent=d.secret;
  loadWh();
}
async function delWh(id){ if(!confirm('删除该订阅？')) return; await fetch(API+'/'+id,{method:'DELETE',headers:hdrs()}); loadWh(); }
async function testWh(id){ const r=await fetch(API+'/'+id+'/test',{method:'POST',headers:hdrs()}); const d=await r.json().catch(()=>({})); alert(d.detail||d.error||('HTTP '+r.status)); }
if(jwt) loadWh(); else document.getElementById('loginHint').style.display='block';
</script></body></html>"""


@router.get("/developers", response_class=HTMLResponse, include_in_schema=False)
async def developers_page() -> HTMLResponse:
    return HTMLResponse(_DEV_PAGE_TMPL)


_GUIDE_CANDIDATES = (
    "docs/开发者接入指南.md",
)


def _guide_md_path():
    import os
    from pathlib import Path

    custom = (os.getenv("DEEPFOCUS_DEV_GUIDE_PATH") or "").strip()
    if custom:
        p = Path(custom)
        if p.is_file():
            return p
    here = Path(__file__).resolve()
    for base in (here.parents[2], here.parents[1]):  # Mac 仓库布局 / 服务器部署布局
        for rel in _GUIDE_CANDIDATES:
            p = base / rel
            if p.is_file():
                return p
    return None


@router.get("/developers/guide", response_class=HTMLResponse, include_in_schema=False)
async def developers_guide() -> HTMLResponse:
    """《开发者接入指南》：源 docs/开发者接入指南.md，服务端渲染。markdown 库缺失时降级纯文本，不 500。"""
    p = _guide_md_path()
    if p is None:
        return HTMLResponse("<h1>开发者接入指南</h1><p>文档文件未部署，请稍后再试。</p>")
    raw = p.read_text(encoding="utf-8")
    try:
        import markdown

        body = markdown.markdown(raw, extensions=["tables", "fenced_code"])
    except Exception:  # noqa: BLE001 - 降级为等宽纯文本
        body = f"<pre>{_xesc(raw)}</pre>"
    return HTMLResponse(
        f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>稻草财经 · 开发者接入指南</title>
<style>body{{font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;background:#0b0d12;color:#e6ebf2;max-width:860px;margin:0 auto;padding:32px 20px 80px;line-height:1.75}}
h1{{font-size:24px}}h2{{font-size:19px;margin-top:30px;padding-top:12px;border-top:1px solid #222733}}h3{{font-size:16px;color:#ffb000}}
code{{background:#0c1018;border:1px solid #222733;border-radius:4px;padding:1px 6px;font-family:ui-monospace,Menlo,monospace;font-size:13px;color:#9fd0ff;word-break:break-all}}
pre{{background:#0c1018;border:1px solid #222733;border-radius:8px;padding:12px;overflow:auto;font-size:12.5px;white-space:pre-wrap}}
pre code{{border:none;padding:0;background:none}}
table{{border-collapse:collapse;width:100%;font-size:13.5px;margin:10px 0}}th,td{{border:1px solid #222733;padding:6px 9px;text-align:left}}th{{color:#8a93a3}}
a{{color:#6ab0ff}}blockquote{{color:#8a93a3;border-left:3px solid #222733;margin:0;padding-left:14px}}
.ft{{margin-top:40px;border-top:1px solid #222733;padding-top:14px;color:#8a93a3;font-size:12.5px}}</style></head><body>
{body}
<p class="ft">© 稻草财经 · daocaijing.com · <a href="/developers">返回开发者平台</a></p>
</body></html>"""
    )
