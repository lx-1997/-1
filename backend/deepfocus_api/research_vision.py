"""图片型研报的多模态「视觉解读」。

海外投行报告多为投行 PPT 截图的图片型 PDF（无文字层），RAG 文本管线无法入库。
这里用 PyMuPDF 把前若干页渲染成 PNG，直接交给视觉模型（MiniMax-M3 支持图像输入）
读图做原文忠实整理。注意：这是基于页面图像的解读，**没有逐句溯源/结构化指标**，
可信度按视觉路径如实标注。
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import subprocess
from html import unescape as _html_unescape
from typing import Any, Optional

import fitz  # PyMuPDF
import httpx

from .llm import CloudResearchLLM, _extract_json

_SENSITIVE_CONTENT_RE = re.compile(r"content\[(\d+)\]")


def _escape_inner_quotes(s: str) -> str:
    """转义 JSON 字符串值内未转义的裸双引号（如 "看多："新主承包商""）。
    模型偶发输出这类嵌套引号 → 标准解析直接炸 → 整次(最贵的)视觉解读报废。
    启发式：串内遇到 " 时看下一个非空白字符，是 , : } ] 或结尾才算真闭合，否则转义。"""
    out: list[str] = []
    in_str = False
    escaped = False
    n = len(s)
    for i, ch in enumerate(s):
        if not in_str:
            if ch == '"':
                in_str = True
            out.append(ch)
            continue
        if escaped:
            out.append(ch)
            escaped = False
            continue
        if ch == "\\":
            out.append(ch)
            escaped = True
            continue
        if ch == '"':
            j = i + 1
            while j < n and s[j] in " \t\r\n":
                j += 1
            nxt = s[j] if j < n else ""
            if nxt in ",:}]" or not nxt:
                in_str = False
                out.append(ch)
            else:
                out.append('\\"')
            continue
        out.append(ch)
    return "".join(out)


def _parse_model_json(raw: str) -> dict:
    """解析模型返回的 JSON：先走标准 _extract_json，失败再做引号修复重试。失败返回 {}。"""
    if not raw:
        return {}
    try:
        data = _extract_json(raw)
        return data if isinstance(data, dict) else {}
    except ValueError:
        pass
    import json as _json

    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        data = _json.loads(_escape_inner_quotes(raw[start : end + 1]))
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}

RENDER_DPI = 120  # 适度降低 DPI：渲染更快、图更小、推理更快（摘要场景足够清晰）
MAX_VISION_PAGES = 14  # 厚 deck(如高盛十大产业主线 46 页)的重点标的在更深的页，读 6-8 页抽不到→读到 ~14 页才能抽出 NVDA/MSFT 等
MIN_TEXT_CHARS = 400  # 文本层够多时走快速文本通道（~5-10s），否则回退视觉（~30-50s）
MAX_TEXT_CHARS = 7000
OCR_MAX_PAGES = 32
_VISION_DISCLAIMER = "本结论基于研报页面图像的 AI 视觉解读，非逐句溯源，可能遗漏或误读，请以原文为准。"
_TEXT_DISCLAIMER = "本结论基于研报文本的 AI 摘要，可能遗漏图表信息，请以原文为准。"


class PdfTextUnavailable(RuntimeError):
    """The PDF has no usable text layer, so the vision path is appropriate."""


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
    '  "summary": "2-3句综述：只整理研报明确写出的核心观点、事实和影响对象，专业但通俗。**点名报告重点提及的具体公司/标的**（如英伟达、贵州茅台），不要只说「某科技龙头」这类泛称；原文未提及的内容不要补充",\n'
    '  "core_logic": "核心逻辑：只复述报告明确写出的驱动、因果关系和结论，2-3句讲清楚；没有明确因果关系就写「原文未明确说明」",\n'
    '  "logic_lines": [{"title":"原文明确出现的逻辑线标题", "evidence":"材料中可直接核对的事实或数字", "chain":"材料明确写出的因果传导；未写出则填「原文未明确说明」", "impact":"材料明确提及的受益/受压公司、行业或资产及方向；未提及则填「原文未提及」", "watch":"材料明确给出的验证指标、时间点或反转条件；未给出则填「原文未提及」"}],\n'
    '  "bullish": ["原文明确写出的利好/看涨理由；3-5条，原文未提及则留空"],\n'
    '  "bearish": ["原文明确写出的利空/风险/看空理由；2-4条，原文未提及则留空"],\n'
    '  "instruments": ["报告重点提及的可交易标的，最多8个、按重要性排序。只允许两类：'
    '①A股/美股/港股上市公司，用最短代号(美股代码如 NVDA；A股6位代码或简称如 贵州茅台；港股简称或代码如 腾讯/00700)；'
    '②大宗商品与加密：贵金属能源(黄金、原油、白银、天然气)、工业与电池金属(铜、铝、镍、钴、锂/碳酸锂/氢氧化锂、锡、铁矿石、稀土等)、主流加密货币(比特币、以太坊)——凡报告重点讨论的可交易品种都可列入。'
    '指数/宏观指标/外汇/未上市公司/行业泛称一律不要；没有就给空数组 []"],\n'
    '  "market": "这篇研报主要面向的股票市场，只填一个：A股 / 港股 / 美股 / 无（宏观或无明确单一市场）。'
    '判断依据是报告研究对象所在市场（如研究英伟达/苹果填美股，研究腾讯/港股公司填港股，研究沪深公司填A股）",\n'
    '  "rating": "评级（买入/增持/中性/减持等，无则空串）",\n'
    '  "target_price": "目标价（含货币与单位，如 US$315 / 港币66元，无则空串）",\n'
    '  "takeaway": "一句话摘要：只陈述这份研报明确给出的核心看法，不替读者做决定、不构成投资建议",\n'
    '  "confidence": 0.0到1.0之间的数字（你对解读可靠度的信心）\n'
    "}"
)

# 新闻解读和研报解读不是同一种任务。新闻通常只有一个事件，不能为了满足
# 研报的「四条逻辑线」契约而重复拆解，更不能把标题中的正面措辞直接升级成
# 看多判断。新闻使用这套更短、更中性的结构，字段名仍保持兼容前端/API。
# v4：面向双受众重写——新手能看懂「发生了什么、对我关心的股票意味着什么」，
# 专业用户能拿到「数字、代码、验证节点」；信息不足就诚实留空，绝不凑模板。
_NEWS_SCHEMA_BLOCK = (
    "{\n"
    '  "subject": "标的：涉及的公司、资产或主题；只填原文明确出现的名称/代码，无法判断则留空",\n'
    '  "one_liner": "核心结论：一句话说清「谁 + 发生了什么 + 直接影响」；有明确涨跌方向就点明（如利好/承压），没有就只述事实；避免『报道称』以外的转述套话，60字内",\n'
    '  "summary": "事件摘要：2-3句。第一句按『新手模式』写——只用大白话+最关键的一个数字，不堆术语；后续句按『专业模式』补齐原文的数字、时间、规模、对比基准。不补充外部背景，不重复核心结论",\n'
    '  "core_logic": "影响逻辑：仅在原文明确写出因果关系时，用『因为A → 所以B → 影响C』的一句式说明；原文没有明确因果关系则留空，不要写『逻辑不明』凑字",\n'
    '  "logic_lines": [{"title":"原文明确的独立信息点，用具体主体开头（如『华为涨价』而非『提价分析』）", "evidence":"可直接核对的事实或数字，保留原文单位（台/亿元/%）", "chain":"原文明确写出的关联/因果；没有则留空", "impact":"原文明确提及的影响对象或方向；没有则留空", "watch":"原文给出的指标、时间点或不确定性；没有则留空"}],\n'
    '  "bullish": ["原文明确写出的积极影响或上行因素，每条带上『为什么』的半句话；没有则给空数组，不要自行推导，最多3条"],\n'
    '  "bearish": ["原文明确写出的风险、不确定性或数据缺口，写清触发条件；没有则给空数组，不要为了完整而编造，最多3条"],\n'
    '  "instruments": ["原文明确提及的上市公司代码/简称或可交易品种；没有则给空数组"],\n'
    '  "market": "主要市场，只填 A股 / 港股 / 美股 / 无",\n'
    '  "rating": "原文/机构明确给出的评级；新闻通常留空",\n'
    '  "target_price": "原文明确给出的目标价；没有则留空",\n'
    '  "takeaway": "关注重点：只写原文给出的后续节点或需要核对的指标，写成『接下来盯什么』；没有则留空",\n'
    '  "confidence": 0.0到1.0之间的数字（表示信息是否充分，不表示涨跌预测把握）\n'
    "}"
)

# 研报解读定位：中性、完整地复述研报重点（结论 → 综述 → 逻辑线 → 核心因果 → 利好/风险），
# 不生成六维「综合判断与边界」，也不承担报告外的独家点评。
_REPORT_SCHEMA_BLOCK = (
    "{\n"
    '  "subject": "标的：公司/股票名称（有代码就带上），无法判断则留空",\n'
    '  "one_liner": "核心结论：看多/看空/中性 + 最关键理由，40字内",\n'
    '  "summary": "综合综述：2-4句，完整覆盖报告各部分的核心重点（各主体的关键数字、预测、时间点都要点到），按原文重要性组织；只整理报告明确写出的内容，措辞中性客观，不加主观评价，也不要自行推导",\n'
    '  "core_logic": "核心逻辑：最多3句、140字，只复述报告明确写出的核心驱动与因果传导，按原文主线串起来；报告没写的因果不要提，也不要罗列「原文未说明」式的缺口注记",\n'
    '  "logic_lines": [{"title":"原文明确出现的逻辑线标题", "evidence":"报告中可核对的事实/数字", "chain":"报告明确写出的因果传导；未写出则填「原文未明确说明」", "impact":"报告明确提及的受益/受压标的与方向；未提及则填「原文未提及」", "watch":"报告明确给出的验证指标、时间点或反转条件；未给出则填「原文未提及」"}],\n'
    '  "bullish": ["报告明确写出的利好/上行依据；最多4条，每条55字内，优先保留原文数字、预测变化或催化剂"],\n'
    '  "bearish": ["报告明确写出的风险/反方观点；最多4条，每条55字内，仅在原文给出时写触发条件"],\n'
    '  "instruments": ["报告重点提及的可交易标的，最多8个。上市公司用简称/代码；也可写黄金、原油、比特币等主流商品/加密资产。行业泛称、外汇、未上市公司不要写"],\n'
    '  "market": "主要市场，只填 A股 / 港股 / 美股 / 无",\n'
    '  "rating": "机构评级（无则空串）",\n'
    '  "target_price": "机构目标价，含货币与单位（无则空串）",\n'
    '  "confidence": 0.0到1.0之间的数字\n'
    "}"
)

_REPORT_STYLE_RULE = (
    "要求：整体定位是**中性、完整地复述研报重点**——不带主观色彩、不作倾向性评价、不输出报告之外的观点；"
    "首屏字段按「结论→综合综述→原文逻辑线→核心因果→原文利好/风险」组织信息，所有逻辑线都必须能在正文中找到依据。"
    "尽量拆出4条相互独立的原文逻辑线；每条只写材料明确出现的事实、因果传导、影响对象和验证点。材料未明确写出的字段必须写「原文未提及」或「原文未明确说明」，不得根据常识补齐。"
    "结论、综述、核心逻辑、利好和风险之间不得机械重复；不写背景铺垫和正确的废话。"
    "任务不是给出独立点评、投资建议或报告外推断，而是把原文重点忠实、浓缩地讲清楚。"
    "按原文重要性排序，数字、预测变化、目标价、原文给出的催化与风险触发条件优先。"
    "点名报告重点公司/标的，不用「某公司」「头部厂商」代替。"
    "只依据材料中真实出现的信息，没有就留空或写「原文未提及」，不得补写外部事实、行业常识或投资建议；所有表述必须高度浓缩且不得照搬原文。"
)
_STYLE_RULE = (
    "要求：①受众是两类人——没看过原文的新手（要一眼知道发生了什么、影响谁）和专业投资者（要拿到数字、代码、验证节点）；"
    "结论/摘要先用大白话讲清事实，再补专业细节；专业术语第一次出现时用半句话括注解释（如 HBM（高带宽内存）），之后可直接用；"
    "②不要把标题或单一正面措辞改写成‘看多/看空’、‘利好/利空’结论，除非原文明确如此判断；"
    "③只保留原文真实出现的事实、数字、时间、公司和因果关系，不得根据常识补全；"
    "④信息不足的字段直接留空——空比『原文未明确说明』『原文未提及』这种占位句子好得多，"
    "前端会隐藏空字段；已生成的字段之间不得机械重复；"
    "⑤logic_lines 最多3条、宁缺毋滥：只有存在彼此不同且可核对的信息点时才填一条，"
    "禁止为凑数把同一事实改写成多条，也禁止生成『事实：××』『关联：原文未明确说明』这类空洞行；"
    "⑥bullish/bearish 只是兼容字段，分别表示原文明确的积极因素与风险/不确定性，不代表系统独立观点；"
    "⑦不得加入报告之外的原创判断，不是给出独立点评或投资建议；"
    "⑧输出纯文本 JSON，不要 Markdown、HTML 标签或 HTML 实体。"
)


def _build_vision_prompt(title: Optional[str], symbol: Optional[str]) -> str:
    target = " ".join(part for part in [title, symbol] if part)
    return (
        "你是财经资料整理助手，擅长把复杂研报的原文内容讲清楚。"
        "下面是某券商研报的前若干页截图（图片型 PDF，无文字层）。"
        "请只依据图片中实际可见的内容，输出严格 JSON object（不要 Markdown、不要解释、不要思考过程）：\n"
        f"{_REPORT_SCHEMA_BLOCK}\n"
        f"线索标的：{target or '未知'}。{_REPORT_STYLE_RULE}"
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


def clean_generated_text(value: Any) -> str:
    """Make model prose safe for plain-text UI fields.

    News fields are rendered as React text rather than Markdown. Decode stray
    HTML entities and remove emphasis/code markers so malformed model output
    cannot leak strings such as ``***事实***&#x6807;题`` into the card.
    """
    text = _html_unescape(str(value or ""))
    text = re.sub(r"\*{1,3}([^*\n]+?)\*{1,3}", r"\1", text)
    text = re.sub(r"`([^`\n]+?)`", r"\1", text)
    return "\n".join(re.sub(r"[ \t\u00a0　]+", " ", line).strip() for line in text.splitlines()).strip()


def normalize_news_result(data: Any) -> dict[str, Any]:
    """Normalize a news result without the report-only four-line backfill."""
    result = dict(data) if isinstance(data, dict) else {}
    scalar_fields = ("subject", "one_liner", "summary", "core_logic", "takeaway", "df_take", "rating", "target_price")
    for field in scalar_fields:
        if field in result:
            result[field] = clean_generated_text(result.get(field))
    for field in ("bullish", "bearish", "key_points", "risks", "instruments"):
        if field in result:
            cleaned_items = [clean_generated_text(item) for item in _as_str_list(result.get(field), 8)]
            result[field] = [item for item in cleaned_items if item]
    result["logic_lines"] = [
        {field: clean_generated_text(line.get(field, "")) for field in _LOGIC_LINE_FIELDS}
        for line in _normalize_logic_lines(result.get("logic_lines"), 3)
        if isinstance(line, dict)
    ]
    return result


_LOGIC_LINE_FIELDS = ("title", "evidence", "chain", "impact", "watch")


def _normalize_logic_lines(value: Any, limit: int = 6) -> list[dict[str, str]]:
    """Normalize the integrated analysis tracks returned by either model path.

    New prompts return objects; accepting strings and common aliases keeps old
    caches and specialist-agent output readable while the cache is refreshed.
    """
    if isinstance(value, dict):
        value = [value]
    elif isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    aliases = {
        "title": ("title", "name", "label", "逻辑线", "主题"),
        "evidence": ("evidence", "fact", "facts", "事实", "依据"),
        "chain": ("chain", "mechanism", "logic", "causal_chain", "因果链", "传导"),
        "impact": ("impact", "beneficiaries", "effect", "影响", "受益对象"),
        "watch": ("watch", "tracking", "trigger", "validation", "验证", "跟踪", "反转条件"),
    }
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if isinstance(item, dict):
            row: dict[str, str] = {}
            for field, keys in aliases.items():
                text = ""
                for key in keys:
                    candidate = item.get(key)
                    if candidate is not None and str(candidate).strip():
                        text = str(candidate).strip()
                        break
                row[field] = text
        else:
            text = str(item or "").strip()
            row = {field: "" for field in _LOGIC_LINE_FIELDS}
            row["chain"] = text
        if not any(row.values()):
            continue
        if not row["title"]:
            row["title"] = f"逻辑线 {len(out) + 1}"
        marker = re.sub(r"\s+", "", "|".join(row.values())).lower()
        if marker in seen:
            continue
        seen.add(marker)
        out.append({field: row[field] for field in _LOGIC_LINE_FIELDS})
        if len(out) >= limit:
            break
    return out


def ensure_logic_lines(data: Any) -> dict[str, Any]:
    """Make the integrated analysis contract available for old/partial caches."""
    result = dict(data) if isinstance(data, dict) else {}
    lines = _normalize_logic_lines(result.get("logic_lines"), 6)
    core_logic = str(result.get("core_logic") or "").strip()
    bullish = _as_str_list(result.get("bullish") or result.get("key_points"), 4)
    bearish = _as_str_list(result.get("bearish") or result.get("risks"), 4)

    # Fallback is deliberately mechanical: it only reorganizes facts already
    # present in the result and labels the missing validation as a gap.
    if len(lines) < 4:
        candidates: list[dict[str, str]] = []
        if core_logic:
            candidates.append({
                "title": "核心传导线", "evidence": "报告核心逻辑",
                "chain": core_logic, "impact": "原文涉及的标的或对象",
                "watch": "报告未明确给出验证条件。",
            })
        for index, text in enumerate(bullish, 1):
            candidates.append({
                "title": f"上行线 {index}", "evidence": text, "chain": text,
                "impact": "原文将其列为上行依据；未说明其他影响。",
                "watch": "报告未明确给出验证条件。",
            })
        for index, text in enumerate(bearish, 1):
            candidates.append({
                "title": f"反证线 {index}", "evidence": text, "chain": text,
                "impact": "原文将其列为风险或反证；未说明其他影响。",
                "watch": "报告未明确给出验证条件。",
            })
        for candidate in candidates:
            marker = re.sub(r"\s+", "", "|".join(candidate.values())).lower()
            if marker not in {re.sub(r"\s+", "", "|".join(row.values())).lower() for row in lines}:
                lines.append(candidate)
            if len(lines) >= 4:
                break
    if not lines:
        lines = [{
            "title": "信息边界", "evidence": "原文未形成可独立核对的分项事实。",
            "chain": "原文未明确说明事实到影响的传导。", "impact": "原文未明确提及受益或受压对象。",
            "watch": "原文未给出验证条件。",
        }]
    result["logic_lines"] = lines[:6]
    return result


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


def report_depth_needs_refresh(data: Any) -> bool:
    """Return whether a cached report predates the logic-line output contract."""
    if not isinstance(data, dict):
        return True
    # v5 只要求完整的逻辑线层；历史缓存里的六维 df_take 不再触发重新生成，
    # 出口层（ensure_report_depth）会直接剥掉，避免为删字段烧一遍 token。
    return len(_normalize_logic_lines(data.get("logic_lines"), 6)) < 4


def ensure_report_depth(data: Any) -> dict[str, Any]:
    """研报结果只保留客观复述层。

    旧缓存里的六维「综合判断与边界」（df_take）已下线：这里在出口处直接清空，
    不回填占位、也不触发重新生成；新生成的结果本就不含该字段。
    """
    result = ensure_logic_lines(data)
    result["df_take"] = ""
    return result


def _normalize_result(
    data: Any, *, provider: str, pages: int, disclaimer: str, compact_report: bool = False,
    logic_line_limit: int = 6, backfill_logic_lines: bool = True,
) -> dict[str, Any]:
    """把模型 JSON 归一化成统一结构（标的/一句话/利好/利空/评级/目标价）。"""
    if not isinstance(data, dict):
        data = {}
    subject = str(data.get("subject") or "").strip()
    one_liner = str(data.get("one_liner") or "").strip()
    summary = str(data.get("summary") or "").strip()
    # 兼容旧字段：key_points→利好、risks→利空
    bullish = _as_str_list(data.get("bullish") or data.get("key_points"), 4 if compact_report else 9)
    bearish = _as_str_list(data.get("bearish") or data.get("risks"), 4 if compact_report else 8)
    if not summary and not bullish and not one_liner:
        raise RuntimeError("模型未返回可用解读")
    normalized = {
        "subject": subject,
        "one_liner": one_liner,
        "summary": summary or one_liner or (bullish[0] if bullish else ""),
        "core_logic": str(data.get("core_logic") or "").strip(),
        # 研报不再生成重复的 takeaway，也不再有六维 df_take——解读只做客观复述。
        # 前端负责默认收起，不能在接口层丢失内容。
        "takeaway": "" if compact_report else str(data.get("takeaway") or "").strip(),
        "df_take": "" if compact_report else str(data.get("df_take") or "").strip(),
        "logic_lines": _normalize_logic_lines(data.get("logic_lines"), logic_line_limit),
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
    if backfill_logic_lines:
        normalized = ensure_logic_lines(normalized)
    return ensure_report_depth(normalized) if compact_report else normalized


# 我方品牌页脚（pdf_brand 打的 DeepFocus Research + 域名）是真文字对象，
# 会被 get_text 一并抽出。图片型研报（原文无文字层）打完品牌水印后，抽出的“文本”
# 全是水印且轻松超过 MIN_TEXT_CHARS → 文本通道误判有正文，把纯水印喂给模型
# （解读成“原文只有网址水印”），且不再回退视觉。这里在长度判定/喂模型前剥掉水印字样。
# 注意：页边被裁切的斜排水印会以残缺片段进文本层（如 "ww.daocaijing.co"、"caijing"），
# 整串正则匹配不到 → 按「token 是水印串的子串」判定片段。
_BRAND_TEXT_RE = re.compile(
    r"DeepFocus\s+Research\s*[|｜]\s*www\.daocaijing\.com"
    r"|www\.daocaijing\.com|更多投研内容|DeepFocus|深度焦点"
    r"|股票投资信息与深度研究平台|让投资研究更有深度"
    r"|看懂公司\s*[·•]\s*看清趋势\s*[·•]\s*发现机会",
    re.I,
)
_BRAND_URL = "www.daocaijing.com"
_BRAND_HEADER = "更多投研内容|deepfocus深度焦点|www.daocaijing.com"
_BRAND_PUNCT = "|｜·—- \t　"


def _is_brand_fragment(token: str) -> bool:
    t = token.strip(_BRAND_PUNCT).lower()
    return (not t) or (t in _BRAND_URL) or (t in _BRAND_HEADER)


def _strip_brand_text(text: str) -> str:
    lines: list[str] = []
    for line in (text or "").splitlines():
        cleaned = _BRAND_TEXT_RE.sub("", line)  # 先剥完整水印串（含混进正文行的）
        tokens = cleaned.split()
        if all(_is_brand_fragment(tok) for tok in tokens):
            continue  # 整行只剩水印片段/分隔符 → 丢弃
        lines.append(cleaned)
    return "\n".join(lines).strip()


def extract_pdf_text(pdf_bytes: bytes, *, max_pages: int = MAX_VISION_PAGES) -> str:
    """抽取 PDF 前若干页文字层（图片型 PDF 会返回很短/空）。已剥离我方品牌水印字样。"""
    parts: list[str] = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for index in range(min(max_pages, doc.page_count)):
            parts.append(doc.load_page(index).get_text("text"))
    return _strip_brand_text("\n".join(parts))


def extract_pdf_ocr_text(pdf_bytes: bytes, *, max_pages: int = OCR_MAX_PAGES) -> str:
    """OCR scanned PDF pages into plain text; never asks a vision model to interpret them."""
    if not pdf_bytes:
        return ""
    parts: list[str] = []
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            for index in range(min(max(1, int(max_pages)), OCR_MAX_PAGES, doc.page_count)):
                page = doc.load_page(index)
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                try:
                    proc = subprocess.run(
                        ["tesseract", "stdin", "stdout", "-l", "chi_sim+eng", "--psm", "6"],
                        input=pix.tobytes("png"), capture_output=True, timeout=12, check=False,
                    )
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    return ""
                text = (proc.stdout or b"").decode("utf-8", "ignore").strip()
                if text:
                    parts.append(f"[第 {index + 1} 页]\n{text}")
    except Exception:
        return ""
    return _strip_brand_text("\n\n".join(parts))[:MAX_TEXT_CHARS * 8]


def _build_text_prompt(title: Optional[str], symbol: Optional[str], text: str) -> str:
    target = " ".join(part for part in [title, symbol] if part)
    return (
        "你是财经资料整理助手，擅长把复杂研报的原文内容讲清楚。"
        "下面是某券商研报的正文文本节选。请只依据文本内容，输出严格 JSON object"
        "（不要 Markdown、不要解释、不要思考过程）：\n"
        f"{_REPORT_SCHEMA_BLOCK}\n"
        f"线索标的：{target or '未知'}。{_REPORT_STYLE_RULE}\n\n"
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
    try:
        text = await asyncio.to_thread(extract_pdf_text, pdf_bytes, max_pages=max_pages)
    except Exception as exc:  # PDF 损坏/文本层读取失败属于视觉回退条件，模型调用失败不属于
        raise PdfTextUnavailable("无法读取 PDF 文本层，转视觉解读") from exc
    if len(text) < MIN_TEXT_CHARS:
        text = await asyncio.to_thread(extract_pdf_ocr_text, pdf_bytes, max_pages=max_pages)
        if len(text) < MIN_TEXT_CHARS:
            raise PdfTextUnavailable("未提取到可用文字")

    llm = CloudResearchLLM()
    if llm.provider == "mock":
        raise RuntimeError("当前为本地演示模型，无法做 AI 解读；请配置云端模型。")

    # Text reports should stay on the fast path. The schema is compact-report
    # shaped, so a 2.4k completion budget is enough and avoids long reasoning
    # tails on Qwen/GLM that make the UI look stuck.
    data = await llm.complete_json(
        _build_text_prompt(title, symbol, text), max_tokens=2400, timeout_seconds=45,
    )
    return _normalize_result(
        data, provider=llm.model, pages=min(max_pages, MAX_VISION_PAGES), disclaimer=_TEXT_DISCLAIMER,
        compact_report=True,
    )


async def analyze_pdf_auto(
    pdf_bytes: bytes,
    *,
    title: Optional[str] = None,
    symbol: Optional[str] = None,
    max_pages: int = 6,
) -> dict[str, Any]:
    """优先文本快速通道；仅图片型/无可读文本层的 PDF 回退视觉解读。"""
    try:
        return await analyze_pdf_text(pdf_bytes, title=title, symbol=symbol)
    except PdfTextUnavailable:
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
        "你是财经资料整理助手，擅长把财经新闻的原文内容讲清楚。根据下面这条财经新闻，"
        "输出严格 JSON object（不要 Markdown、不要解释、不要思考过程）：\n"
        f"{_NEWS_SCHEMA_BLOCK}\n"
        "字段含义针对新闻调整：subject=新闻涉及的公司/行业/主题；"
        "bullish=原文明确写出的积极影响；bearish=原文明确写出的风险、不确定性或数据缺口；"
        "rating/target_price 新闻通常没有，没有就留空。"
        f"{_STYLE_RULE}\n\n"
        f"=== 新闻 ===\n标题：{title or ''}\n正文：{content[:MAX_TEXT_CHARS]}"
    )


def _title_only_one_liner(title: Optional[str]) -> str:
    """正文不可得时的降级概括：只复述标题本身，不引入任何新事实。

    聚合类条目经常只有标题、正文留在原文链接且抓不到。此时宁可给用户一句
    "据标题"的诚实概括，也不该把整条 AI 解读判成失败（用户看到 502 会反复重试）。
    """
    text = (title or "").strip()
    if not text:
        return "该条目未提供正文，无法解读。"
    return f"该条目仅有标题、原文正文不可得，据标题概括：{text}"


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
    # v3 invalidates the old forced-four-line/news-stance interpretation.
    cache_key = "NEWS:v4:" + hashlib.md5(((title or "") + "\x00" + body).encode("utf-8")).hexdigest()
    try:
        from . import data_store

        cached = data_store.latest("news_ai", cache_key, max_age_seconds=14 * 86400)
        if isinstance(cached, dict) and cached:
            return normalize_news_result(cached)
    except Exception:
        data_store = None
    llm = CloudResearchLLM()
    if llm.provider == "mock":
        raise RuntimeError("当前为本地演示模型，无法做 AI 解读；请配置云端模型。")
    # News has fewer fields than a report and should feel instant. Keep the
    # output budget below the report budget; normalization supplies empty
    # optional arrays instead of waiting for padded prose.
    data = await llm.complete_json(
        _build_news_prompt(title, body), max_tokens=1800, timeout_seconds=30,
    )
    # 正文太薄(只有标题)时，模型常按"不得编造"的纪律返回全空字段（实测 one_liner/
    # summary 均为空串）。原样交给 _normalize_result 会抛「模型未返回可用解读」→ 接口
    # 502，用户看到"AI 解读失败"且重试同样失败。这里降级为只用标题本身的一句话概括
    # （不新增任何事实），并把 source_note 标成"仅据标题"，让前端照常展示取材边界。
    try:
        result = _normalize_result(
            data,
            provider=llm.model,
            pages=0,
            disclaimer=_TEXT_DISCLAIMER,
            logic_line_limit=3,
            backfill_logic_lines=False,
        )
    except RuntimeError:
        result = _normalize_result(
            {"one_liner": _title_only_one_liner(title)},
            provider=llm.model,
            pages=0,
            disclaimer=_TEXT_DISCLAIMER,
            logic_line_limit=3,
            backfill_logic_lines=False,
        )
        source_note = source_note or "⚠ 原文信息有限，本解读仅据标题概括，未逐条核验"
    result = normalize_news_result(result)
    if source_note:
        result["source_note"] = source_note
    if data_store is not None:
        try:
            await asyncio.to_thread(data_store.record, "news_ai", cache_key, result)
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
    # v4 = integrated logic lines + six-dimension boundary synthesis.
    cache_key = "VIS:v4:" + hashlib.md5(
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
                prompt, attempt_images, max_tokens=4200, timeout_seconds=180,
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

    data = _parse_model_json(raw)
    if not data:
        # 解析失败/空 → 重试一次：压缩单句长度，但保留完整的复述结构。
        retry_prompt = prompt + (
            "\n\n注意：上次输出不是合法或完整的 JSON。请**只输出**更紧凑的严格 JSON object，"
            "请保留至少4条 logic_lines，每条包含 evidence/chain/impact/watch；summary 与 core_logic 仍必须保留完整复述。"
            "不要任何解释、思考过程或 Markdown。"
        )
        try:
            raw2 = await llm.complete_vision(
                retry_prompt, attempt_images, max_tokens=3200, timeout_seconds=160,
            )
            data = _parse_model_json(raw2)
        except Exception:  # noqa: BLE001
            data = {}
    try:
        result = _normalize_result(
            data, provider=llm.model, pages=len(attempt_images), disclaimer=_VISION_DISCLAIMER,
            compact_report=True,
        )
    except RuntimeError:
        raise RuntimeError("视觉模型未返回可用解读，可能页面过于模糊。")
    if data_store is not None:
        try:
            data_store.record("vision", cache_key, result)
        except Exception:
            pass
    return result
