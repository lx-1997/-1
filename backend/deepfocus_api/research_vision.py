"""图片型研报的多模态「视觉解读」。

海外投行报告多为投行 PPT 截图的图片型 PDF（无文字层），RAG 文本管线无法入库。
这里用 PyMuPDF 把前若干页渲染成 PNG，直接交给视觉模型（MiniMax-M3 支持图像输入）
读图出买方视角解读。注意：这是基于页面图像的解读，**没有逐句溯源/结构化指标**，
可信度按视觉路径如实标注。
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from html import unescape as _html_unescape
from typing import Any, Optional

import fitz  # PyMuPDF
import httpx

from .llm import CloudResearchLLM, _extract_json

_SENSITIVE_CONTENT_RE = re.compile(r"content\[(\d+)\]")

RENDER_DPI = 120  # 适度降低 DPI：渲染更快、图更小、推理更快（摘要场景足够清晰）
MAX_VISION_PAGES = 8
MIN_TEXT_CHARS = 400  # 文本层够多时走快速文本通道（~5-10s），否则回退视觉（~30-50s）
MAX_TEXT_CHARS = 7000
_VISION_DISCLAIMER = "本结论基于研报页面图像的 AI 视觉解读，非逐句溯源，可能遗漏或误读，请以原文为准。"
_TEXT_DISCLAIMER = "本结论基于研报文本的 AI 摘要，可能遗漏图表信息，请以原文为准。"


import math

MAX_RENDER_PX = 2200  # 单张图最长边上限：保证可被视觉模型接收，又尽量清晰
_TALL_ASPECT = 1.9    # 高宽比超过此值视为「长图」（晨报/龙虎榜/日报常见），竖切成多块保清晰
_TALL_MAX_TILES = 5   # 长图最多切几块：控制单次视觉输入总量，避免过大导致返回空/截断（牺牲极长文档的尾部）


def _pix_png(page: "fitz.Page", zoom: float, clip: "Optional[fitz.Rect]" = None) -> bytes:
    return page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip).tobytes("png")


def render_pdf_to_pngs(pdf_bytes: bytes, *, max_pages: int = 6, dpi: int = RENDER_DPI) -> list[bytes]:
    """把 PDF 前若干页渲染成 PNG（总数封顶 MAX_VISION_PAGES）。

    关键：无文字层的「整张长图」研报（早知道/龙虎榜/日报），若整页等比缩小会把文字压糊、
    模型读不出具体个股与数据 → 解读很粗糙。这里对长图按高度**竖切成多块**分别渲染，
    每块只做温和缩放，保证文字清晰可读；普通页则整页渲染、最长边封顶。"""
    pages: list[bytes] = []
    base_zoom = max(72, dpi) / 72.0
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        page_count = doc.page_count
        for index in range(min(max(1, max_pages), page_count)):
            if len(pages) >= MAX_VISION_PAGES:
                break
            page = doc.load_page(index)
            rect = page.rect
            aspect = rect.height / max(1.0, rect.width)
            # 长图（且整篇页数不多，确认是整图型）→ 竖切成多块，每块两维都≤MAX_RENDER_PX，
            # 既保文字清晰、单块体积又可控（避免一次塞太多/太大像素导致模型返回空或截断）。
            if aspect > _TALL_ASPECT and page_count <= 2:
                zoom = min(base_zoom, MAX_RENDER_PX / max(1.0, rect.width))  # 宽度封顶
                if zoom <= 0:
                    zoom = base_zoom
                band_pt = MAX_RENDER_PX / zoom  # 每块高度（pt），使块高≤MAX_RENDER_PX
                n_tiles = min(_TALL_MAX_TILES, MAX_VISION_PAGES - len(pages),
                              max(2, math.ceil(rect.height / band_pt)))
                for t in range(n_tiles):
                    if len(pages) >= MAX_VISION_PAGES:
                        break
                    y0 = rect.y0 + t * band_pt
                    if y0 >= rect.y1:
                        break
                    clip = fitz.Rect(rect.x0, y0, rect.x1, min(rect.y1, y0 + band_pt))
                    pages.append(_pix_png(page, zoom, clip))
            else:
                zoom = base_zoom
                longest = max(rect.width, rect.height) * zoom
                if longest > MAX_RENDER_PX:
                    zoom *= MAX_RENDER_PX / longest
                pages.append(_pix_png(page, zoom))
    return pages


_SCHEMA_BLOCK = (
    "{\n"
    '  "subject": "标的：这篇研报研究的公司/股票名称（有代码就带上），无法判断则留空",\n'
    '  "one_liner": "一句话结论：点明「看多还是看空 + 核心理由」，30字内，专业但好懂",\n'
    '  "summary": "2-3句综述：研报的核心观点与买方该怎么看，专业但通俗、不堆黑话。**点名报告重点提及的具体公司/标的**（如英伟达、贵州茅台），不要只说「某科技龙头」这类泛称",\n'
    '  "core_logic": "投资逻辑：这篇报告看多/看空的核心驱动与因果链是什么（如「英伟达数据中心需求超预期→产能紧张→提价→盈利上修」），**点名具体标的**、2-3句讲清楚，不要空泛",\n'
    '  "bullish": ["利好/看涨理由，越具体越好，尽量带数字（如 营收+25%、目标价上调）；3-5条"],\n'
    '  "bearish": ["利空/风险/看空理由，具体；2-4条"],\n'
    '  "instruments": ["报告重点提及的可交易标的，最多8个、按重要性排序。只允许两类：'
    '①A股/美股/港股上市公司，用最短代号(美股代码如 NVDA；A股6位代码或简称如 贵州茅台；港股简称或代码如 腾讯/00700)；'
    '②大宗商品与加密：贵金属能源(黄金、原油、白银、天然气)、工业与电池金属(铜、铝、镍、钴、锂/碳酸锂/氢氧化锂、锡、铁矿石、稀土等)、主流加密货币(比特币、以太坊)——凡报告重点讨论的可交易品种都可列入。'
    '指数/宏观指标/外汇/未上市公司/行业泛称一律不要；没有就给空数组 []"],\n'
    '  "market": "这篇研报主要面向的股票市场，只填一个：A股 / 港股 / 美股 / 无（宏观或无明确单一市场）。'
    '判断依据是报告研究对象所在市场（如研究英伟达/苹果填美股，研究腾讯/港股公司填港股，研究沪深公司填A股）",\n'
    '  "rating": "评级（买入/增持/中性/减持等，无则空串）",\n'
    '  "target_price": "目标价（含货币与单位，如 US$315 / 港币66元，无则空串）",\n'
    '  "takeaway": "一句话启示：这份研报最该记住的一点（客观陈述这份报告的看法，不替读者做决定、不构成投资建议）",\n'
    '  "confidence": 0.0到1.0之间的数字（你对解读可靠度的信心）\n'
    "}"
)
_STYLE_RULE = (
    "要求：①面向有一定基础的普通投资者，专业、有信息量，但表达通俗清晰——"
    "用到稍冷门或易误解的术语时顺带半句话点明，不要堆黑话、也不要幼稚化；"
    "②利好/利空都按重要性从高到低排序，要具体、能落地，尽量带数字或事件；"
    "③一句话结论必须点明「看多还是看空」及核心理由；"
    "④投资逻辑要讲清驱动的因果链，不要空泛套话；"
    "⑤只依据材料中真实出现的信息，没有的字段留空，绝不编造；"
    "⑥**综述/投资逻辑/利好利空等叙述里要点名报告重点提及的具体公司或标的（与 instruments 一致）**，"
    "让读者一眼知道在说哪只票，不要用「某公司」「科技龙头」「头部厂商」这类泛称遮掉标的。"
)


def _build_vision_prompt(title: Optional[str], symbol: Optional[str]) -> str:
    target = " ".join(part for part in [title, symbol] if part)
    return (
        "你是顶尖买方投研分析师，擅长把复杂研报讲给普通投资者听。"
        "下面是某券商研报的前若干页截图（图片型 PDF，无文字层）。"
        "请只依据图片中实际可见的内容，输出严格 JSON object（不要 Markdown、不要解释、不要思考过程）：\n"
        f"{_SCHEMA_BLOCK}\n"
        f"线索标的：{target or '未知'}。{_STYLE_RULE}"
    )


def _as_str_list(value: Any, limit: int) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            out.append(text)
        if len(out) >= limit:
            break
    return out


_COMMODITY_CANON = {
    "黄金": "黄金", "金": "黄金", "gold": "黄金", "xau": "黄金", "au": "黄金", "现货黄金": "黄金", "沪金": "黄金", "comex黄金": "黄金",
    "原油": "原油", "石油": "原油", "oil": "原油", "crude": "原油", "wti": "原油", "brent": "原油",
    "布伦特": "原油", "布油": "原油", "美油": "原油", "sc原油": "原油", "原油期货": "原油",
    "白银": "白银", "银": "白银", "silver": "白银", "xag": "白银", "沪银": "白银",
    "比特币": "比特币", "比特幣": "比特币", "btc": "比特币", "bitcoin": "比特币", "比特": "比特币", "btc/usd": "比特币",
}


def _normalize_instruments(value: Any, limit: int = 8) -> list[str]:
    """归一化「提及标的」：大宗/加密映射为 黄金/原油/白银/比特币；公司保留短标签；去重、限长、限量。"""
    raw = value
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        label = str(item or "").strip().strip("，,、;；()（）")
        if not label or len(label) > 16:  # 过长多半是句子/行业泛称，丢弃
            continue
        canon = _COMMODITY_CANON.get(label.lower())
        if canon:
            label = canon
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(label)
        if len(out) >= limit:
            break
    return out


def _normalize_market(value: Any) -> str:
    """把模型给的市场归一到 A / HK / US / ''（无/宏观）。"""
    s = str(value or "").strip().lower()
    if not s or "无" in s or "宏观" in s or "mixed" in s or "混合" in s:
        return ""
    if "美" in s or "us" in s or "纳斯达" in s or "标普" in s:
        return "US"
    if "港" in s or "hk" in s or "香港" in s or "恒生" in s:
        return "HK"
    if "a股" in s or "a股市场" in s or "沪" in s or "深" in s or s == "a":
        return "A"
    return ""


def _clamp_confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, number))


def _normalize_result(data: Any, *, provider: str, pages: int, disclaimer: str) -> dict[str, Any]:
    """把模型 JSON 归一化成统一结构（标的/一句话/利好/利空/评级/目标价）。"""
    if not isinstance(data, dict):
        data = {}
    subject = str(data.get("subject") or "").strip()
    one_liner = str(data.get("one_liner") or "").strip()
    summary = str(data.get("summary") or "").strip()
    # 兼容旧字段：key_points→利好、risks→利空
    bullish = _as_str_list(data.get("bullish") or data.get("key_points"), 6)
    bearish = _as_str_list(data.get("bearish") or data.get("risks"), 5)
    if not summary and not bullish and not one_liner:
        raise RuntimeError("模型未返回可用解读")
    return {
        "subject": subject,
        "one_liner": one_liner,
        "summary": summary or one_liner or (bullish[0] if bullish else ""),
        "core_logic": str(data.get("core_logic") or "").strip(),
        "takeaway": str(data.get("takeaway") or "").strip(),
        "bullish": bullish,
        "bearish": bearish,
        "instruments": _normalize_instruments(data.get("instruments")),
        "market": _normalize_market(data.get("market")),
        # 保留旧字段，兼容仍在用 key_points/risks 的调用方
        "key_points": bullish,
        "risks": bearish,
        "rating": (str(data.get("rating") or "").strip() or None),
        "target_price": (str(data.get("target_price") or "").strip() or None),
        "confidence": _clamp_confidence(data.get("confidence")),
        "pages_analyzed": pages,
        "provider": provider,
        "disclaimer": disclaimer,
    }


def extract_pdf_text(pdf_bytes: bytes, *, max_pages: int = MAX_VISION_PAGES) -> str:
    """抽取 PDF 前若干页文字层（图片型 PDF 会返回很短/空）。"""
    parts: list[str] = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for index in range(min(max_pages, doc.page_count)):
            parts.append(doc.load_page(index).get_text("text"))
    return "\n".join(parts).strip()


def _build_text_prompt(title: Optional[str], symbol: Optional[str], text: str) -> str:
    target = " ".join(part for part in [title, symbol] if part)
    return (
        "你是顶尖买方投研分析师，擅长把复杂研报讲给普通投资者听。"
        "下面是某券商研报的正文文本节选。请只依据文本内容，输出严格 JSON object"
        "（不要 Markdown、不要解释、不要思考过程）：\n"
        f"{_SCHEMA_BLOCK}\n"
        f"线索标的：{target or '未知'}。{_STYLE_RULE}\n\n"
        f"=== 研报正文 ===\n{text[:MAX_TEXT_CHARS]}"
    )


async def analyze_pdf_text(
    pdf_bytes: bytes,
    *,
    title: Optional[str] = None,
    symbol: Optional[str] = None,
    max_pages: int = MAX_VISION_PAGES,
) -> dict[str, Any]:
    """文本快速通道：抽取文字层 → 文本模型出解读（远快于视觉）。文本过少则抛错由上层回退。"""
    text = await asyncio.to_thread(extract_pdf_text, pdf_bytes, max_pages=max_pages)
    if len(text) < MIN_TEXT_CHARS:
        raise RuntimeError("文本层过少，转视觉解读")

    llm = CloudResearchLLM()
    if llm.provider == "mock":
        raise RuntimeError("当前为本地演示模型，无法做 AI 解读；请配置云端模型。")

    data = await llm.complete_json(
        _build_text_prompt(title, symbol, text), max_tokens=2000, timeout_seconds=50,
    )
    return _normalize_result(
        data, provider=llm.model, pages=min(max_pages, MAX_VISION_PAGES), disclaimer=_TEXT_DISCLAIMER,
    )


async def analyze_pdf_auto(
    pdf_bytes: bytes,
    *,
    title: Optional[str] = None,
    symbol: Optional[str] = None,
    max_pages: int = 6,
) -> dict[str, Any]:
    """优先文本快速通道；图片型 PDF 或文本失败时回退多模态视觉解读。"""
    try:
        return await analyze_pdf_text(pdf_bytes, title=title, symbol=symbol)
    except Exception:
        return await analyze_pdf_vision(pdf_bytes, title=title, symbol=symbol, max_pages=max_pages)


_FETCH_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_STRIP_NOISE_RE = re.compile(r"(?is)<(script|style|noscript|template|svg)[^>]*>.*?</\1>")
_BLOCK_RE = re.compile(r"(?is)<(?:p|h[1-4]|li|blockquote|article|section|td)[^>]*>(.*?)</(?:p|h[1-4]|li|blockquote|article|section|td)>")
_TAG_RE = re.compile(r"(?s)<[^>]+>")
_WS_RE = re.compile(r"\s+")  # 块内空白(含换行/回车)统一折叠成单空格；段落间分隔由 join('\n') 保留


async def _fetch_article_text(url: Optional[str], max_chars: int = MAX_TEXT_CHARS) -> str:
    """尽力抓取原文正文(轻量提取，无第三方依赖)。

    很多聚合「文章/综述」入库时只有标题或一句导语，正文留在 url。这里直连(trust_env=False，
    与本仓东财/sina 抓取一致，prod 对国内站可达)取 HTML，正则抽 <p>/<li> 等段落文本拼成正文，
    抽不出再整页去标签兜底。任何失败(超时/被墙/反爬/付费墙)都返回空串 → 上层回退「仅据标题」。"""
    if not url or not url.startswith(("http://", "https://")):
        return ""
    try:
        async with httpx.AsyncClient(
            trust_env=False, timeout=8.0, follow_redirects=True,
            headers={"User-Agent": _FETCH_UA, "Accept": "text/html,application/xhtml+xml"},
        ) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            return ""
        html = resp.text or ""
    except Exception:
        return ""
    html = _STRIP_NOISE_RE.sub(" ", html)
    blocks: list[str] = []
    for m in _BLOCK_RE.finditer(html):
        txt = _WS_RE.sub(" ", _html_unescape(_TAG_RE.sub("", m.group(1)))).strip()
        if len(txt) >= 12:  # 滤掉导航/按钮/版权等短碎块
            blocks.append(txt)
    text = "\n".join(blocks).strip()
    if len(text) < 120:  # 段落抽取太少 → 整页去标签兜底
        text = _WS_RE.sub(" ", _html_unescape(_TAG_RE.sub(" ", html))).strip()
    return text[:max_chars]


def _build_news_prompt(title: Optional[str], content: str) -> str:
    return (
        "你是资深财经编辑，擅长把新闻讲给普通投资者听。根据下面这条财经新闻，"
        "输出严格 JSON object（不要 Markdown、不要解释、不要思考过程）：\n"
        f"{_SCHEMA_BLOCK}\n"
        "字段含义针对新闻调整：subject=新闻涉及的公司/行业/主题；"
        "bullish=对谁是利好/积极影响；bearish=对谁是利空/风险/需警惕之处；"
        "rating/target_price 新闻通常没有，没有就留空。"
        f"{_STYLE_RULE}\n\n"
        f"=== 新闻 ===\n标题：{title or ''}\n正文：{content[:MAX_TEXT_CHARS]}"
    )


async def analyze_news(title: Optional[str], content: str, url: Optional[str] = None) -> dict[str, Any]:
    """对一条财经新闻做大白话 AI 解读，返回与研报同构的结构（可复用同一前端卡片）。

    解读深度取决于喂进去的料：很多聚合「文章/综述」入库只有标题或一句导语，正文留在 url。
    这里在正文太薄且有链接时，先尽力抓回原文全文再喂模型（治本）；抓不到就照旧、并把
    source_note 标成「仅据标题概括」让前端诚实展示（治标，不误导）。"""
    body = (content or "").strip()
    if len((title or "") + body) < 8:
        raise RuntimeError("新闻内容过短，无需解读")
    # 正文太薄(<200字，多半只有标题/导语)且有原文链接 → 尽力补抓全文。抓取失败静默回退。
    source_note = ""
    if len(body) < 200 and url:
        fetched = await _fetch_article_text(url)
        if len(fetched) >= 300 and len(fetched) > len(body):
            body = fetched
            source_note = "已读取原文全文"
    thin = len(body) < 80  # 抓完仍极短 = 实质只有标题
    if thin:
        source_note = "⚠ 原文信息有限，本解读仅据标题概括"
    # 同一条资讯(标题+最终正文相同)的 AI 解读结果稳定，按内容哈希缓存 14 天。
    # 抓到全文后 body 变化→哈希变→自然产生新的(更优的)缓存，不会复用旧的薄版本。
    cache_key = "NEWS:" + hashlib.md5(((title or "") + "\x00" + body).encode("utf-8")).hexdigest()
    try:
        from . import data_store

        cached = data_store.latest("news_ai", cache_key, max_age_seconds=14 * 86400)
        if isinstance(cached, dict) and cached:
            return cached
    except Exception:
        data_store = None
    llm = CloudResearchLLM()
    if llm.provider == "mock":
        raise RuntimeError("当前为本地演示模型，无法做 AI 解读；请配置云端模型。")
    data = await llm.complete_json(
        _build_news_prompt(title, body), max_tokens=1600, timeout_seconds=45,
    )
    result = _normalize_result(data, provider=llm.model, pages=0, disclaimer=_TEXT_DISCLAIMER)
    if source_note:
        result["source_note"] = source_note
    if data_store is not None:
        try:
            data_store.record("news_ai", cache_key, result)
        except Exception:
            pass
    return result


async def analyze_pdf_vision(
    pdf_bytes: bytes,
    *,
    title: Optional[str] = None,
    symbol: Optional[str] = None,
    max_pages: int = 6,
) -> dict[str, Any]:
    """渲染 PDF 页面并用视觉模型出解读。返回归一化的结果字典。

    同一篇研报(PDF 字节相同)的视觉解读结果稳定，按内容哈希缓存 14 天：
    多用户重复打开同一篇直接复用，省下整套渲染+多模态读图(最贵的一类调用)。
    """
    cache_key = "VIS:" + hashlib.md5(
        pdf_bytes + str(max_pages).encode() + (symbol or "").encode() + (title or "").encode()
    ).hexdigest()
    try:
        from . import data_store

        cached = data_store.latest("vision", cache_key, max_age_seconds=14 * 86400)
        if isinstance(cached, dict) and cached:
            return cached
    except Exception:
        data_store = None

    images = await asyncio.to_thread(render_pdf_to_pngs, pdf_bytes, max_pages=max_pages)
    if not images:
        raise RuntimeError("PDF 没有可渲染的页面")

    llm = CloudResearchLLM()
    if llm.provider == "mock":
        raise RuntimeError("当前为本地演示模型，无法做视觉解读；请在设置中配置云端视觉模型。")

    prompt = _build_vision_prompt(title, symbol)
    attempt_images = list(images)
    raw = ""
    # MiniMax 视觉接口会对每张图做内容安全检查，偶尔把某页误判为 sensitive 而整批拒绝。
    # 解析报错里的 content[N]（content[0] 是文本提示），丢掉那一页重试，最多丢一半页。
    max_drops = max(1, len(images) // 2)
    for _ in range(max_drops + 1):
        if not attempt_images:
            raise RuntimeError("所有页面均被视觉模型安全策略拦截，无法解读。")
        try:
            raw = await llm.complete_vision(
                prompt, attempt_images, max_tokens=3600, timeout_seconds=160,
            )
            break
        except RuntimeError as exc:
            message = str(exc)
            match = _SENSITIVE_CONTENT_RE.search(message)
            if "sensitive" in message.lower() and match:
                image_index = int(match.group(1)) - 1  # content[0] 是文本
                if 0 <= image_index < len(attempt_images):
                    del attempt_images[image_index]
                    continue
            raise

    try:
        data = _extract_json(raw) if raw else {}
    except ValueError:
        data = {}
    if not isinstance(data, dict) or not data:
        # 解析失败/空 → 重试一次：要求更短的严格 JSON（常见于输出被截断或格式坏）
        retry_prompt = prompt + (
            "\n\n注意：上次输出不是合法或完整的 JSON。请**只输出**更短的严格 JSON object，"
            "每个数组字段最多 3 项、每项不超过 20 个字，不要任何解释、思考过程或 Markdown。"
        )
        try:
            raw2 = await llm.complete_vision(
                retry_prompt, attempt_images, max_tokens=2200, timeout_seconds=160,
            )
            data = _extract_json(raw2) if raw2 else {}
        except Exception:  # noqa: BLE001
            data = {}
    try:
        result = _normalize_result(
            data, provider=llm.model, pages=len(attempt_images), disclaimer=_VISION_DISCLAIMER,
        )
    except RuntimeError:
        raise RuntimeError("视觉模型未返回可用解读，可能页面过于模糊。")
    if data_store is not None:
        try:
            data_store.record("vision", cache_key, result)
        except Exception:
            pass
    return result
