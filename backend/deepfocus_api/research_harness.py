"""统一投研 Harness。

AI 对话以前有三条互相平行的取数路径：tool-agent 自己选工具、Dulus 只跑少量
占位工具、research-loop 又有一套 cross-module 聚合。这里把「研究前置取数」收
敛成一个小而明确的 harness：先确定标的，再并行调用真实只读工具，最后把结构化
数据包交给任意回答器（快速回答、圆桌、长循环或 RAG 综合）。

Harness 不负责写入、下单或给结论；它只负责可追溯的数据和证据，单个数据源失败
不会拖垮整次研究。
"""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any, Optional

from .agent_tools import execute_tool, known_symbol_name, resolve_known_symbol
from .data_sources import list_data_items
from .research_index import build_research_index
from .schemas import StockSnapshot
from .stock_name_index import all_name_code, name_of_code, resolve_to_code


_MARKET_WORDS = re.compile(
    r"大盘|盘面|复盘|指数|黄金|原油|比特币|美元|汇率|美债|VIX|宏观|板块|题材|市场|股市|A股|行情走势|怎么走|为什么这样",
    re.I,
)
_STOCK_WORDS = re.compile(
    r"研究|分析|看看|评估|行情|估值|PE|PB|市值|财报|业绩|现金流|利润|ROE|资金|主力|研报|新闻|公告|分红|龙虎榜|持仓|股票|个股|能不能买|值得",
    re.I,
)
_TICKER_RE = re.compile(r"(?<![A-Za-z0-9])(?:\d{5}|[A-Z]{1,5})(?![A-Za-z0-9])", re.I)
_A_SHARE_CODE_RE = re.compile(r"(?<![\d.])\d{6}(?![\d.])")
_COMPARISON_RE = re.compile(
    r"比较|对比|谁更|更偏向|偏向谁|哪个好|哪家更|二选一|选哪只|怎么选|排序|孰优孰劣|相较|相比|优先看",
    re.I,
)

_RECENT_CONTENT_RE = re.compile(
    r"最近|近期|近\s*[一二两三四五六七八九十\d]+|过去\s*[一二两三四五六七八九十\d]+",
    re.I,
)
_RECENT_CONTENT_TYPES_RE = re.compile(r"机构纪要|快讯|文章|研报|资讯|内容|全部|所有", re.I)
_RECENT_NUMBERS = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def recent_content_window_days(objective: str) -> int | None:
    """Parse an explicit recent/all-content request into one bounded window."""
    text = str(objective or "").strip()
    if not (_RECENT_CONTENT_RE.search(text) and _RECENT_CONTENT_TYPES_RE.search(text)):
        return None
    match = re.search(r"(?:最近|近|过去)\s*([一二两三四五六七八九十\d]+)\s*天", text)
    if match:
        token = match.group(1)
        days = int(token) if token.isdigit() else _RECENT_NUMBERS.get(token, 2)
    elif re.search(r"最近一周|近一周|过去一周", text):
        days = 7
    else:
        days = 2
    return max(1, min(days, 30))

# 「推荐买哪些股票」不是单股研判，而是先定义投资范围再做候选筛选。旧链路会把
# 页面当前选中的股票或上一轮主题暗塞进这类问题，最终拿一只无关股票的资料回答
# 全市场问题。这里集中判定，供快速问答和 Dulus 深度入口共用。
_STOCK_SELECTION_PATTERNS = (
    re.compile(r"(?:推荐|建议|挑选?|筛选?|找).{0,12}(?:股票|个股|标的|票)", re.I),
    re.compile(r"(?:股票|个股|标的).{0,12}(?:推荐|值得买|可以买|看好|关注)", re.I),
    re.compile(r"(?:买|看好|关注).{0,8}(?:哪些|哪几只).{0,8}(?:股票|个股|票)?", re.I),
    re.compile(r"(?:哪些|哪几只).{0,8}(?:股票|个股|票).{0,10}(?:值得|可以买|推荐|看好)", re.I),
    re.compile(r"(?:筛|选|挑).{0,8}(?:[2-9]|两|三|四|五|六|七|八|九)\s*只", re.I),
    re.compile(r"(?:给我|来|推荐|建议|挑|选|筛).{0,8}(?:[1-9]|一|两|三|四|五|六|七|八|九)\s*只(?:股票|个股|标的|票)?", re.I),
    re.compile(r"(?:买|选|挑|看好|关注).{0,8}(?:什么|哪些|哪几只|哪几个)(?:股票|个股|标的|票)?", re.I),
    re.compile(r"重新.{0,4}(?:选股|挑股|筛股)", re.I),
    re.compile(r"^按默认方案[。！!]?$", re.I),
)
_SELECTION_SCOPE_RE = re.compile(
    r"A股|港股|美股|沪深|创业板|科创板|北交所|纳斯达克|标普|全球|海外|"
    r"短线|中线|长线|长期|波段|日内|[一二三四五六七八九十\d]+(?:天|周|月|年)|"
    r"稳健|均衡|进攻|激进|低估|价值|成长|高股息|红利|高景气|"
    r"行业|板块|主题|概念|赛道|算力|人工智能|AI|机器人|新能源|半导体|医药|消费|金融|"
    r"自选股|持仓|关注列表|默认方案",
    re.I,
)
_SELECTION_HISTORY_REF_RE = re.compile(r"上述|上面|刚才|前面|这些|那几只|其中|里面|从中|它们|自选股|持仓", re.I)
_SELECTION_MARKET_RE = re.compile(r"A股|港股|美股|沪深|创业板|科创板|北交所|纳斯达克|标普|全球|海外", re.I)
_SELECTION_HORIZON_RE = re.compile(
    r"短线|中线|长线|长期|波段|日内|"
    r"[1-9一二三四五六七八九十]+\s*(?:[—\-~至]\s*[1-9一二三四五六七八九十]+\s*)?(?:天|周|个?月|年)",
    re.I,
)
_SELECTION_RISK_RE = re.compile(r"稳健|均衡|进攻|激进|低估|价值|成长", re.I)
_SELECTION_DEFINED_UNIVERSE_RE = re.compile(
    r"行业|板块|主题|概念|赛道|算力|人工智能|AI|机器人|新能源|半导体|医药|消费|金融|高股息|红利|高景气|自选股|持仓|关注列表",
    re.I,
)


def is_stock_selection_request(text: str) -> bool:
    """是否在问一个股票候选池，而不是研究某一只已明确股票。"""
    query = (text or "").strip()
    if not query:
        return False
    # 已明确到单只股票时让单股链路处理，避免「推荐买贵州茅台这只股票吗」被误判。
    if resolve_to_code(query):
        return False
    return any(pattern.search(query) for pattern in _STOCK_SELECTION_PATTERNS)


def stock_selection_needs_clarification(text: str, context: str = "") -> bool:
    """泛选股需同时有市场/周期/风格；已指定主题池或上文候选池时可直接初筛。"""
    query = (text or "").strip()
    if not is_stock_selection_request(query):
        return False
    if re.fullmatch(r"按默认方案[。！!]?", query, re.I):
        return False
    if context.strip() and _SELECTION_HISTORY_REF_RE.search(query):
        return False
    if _SELECTION_DEFINED_UNIVERSE_RE.search(query):
        return False
    return not (
        _SELECTION_MARKET_RE.search(query)
        and _SELECTION_HORIZON_RE.search(query)
        and _SELECTION_RISK_RE.search(query)
    )


def sanitize_stock_selection_context(text: str, context: str = "") -> str:
    """全市场选股不继承页面标的；独立新问也不继承上一轮主题，附件仍保留。"""
    if not is_stock_selection_request(text):
        return context
    cleaned = re.sub(r"(?m)^当前标的：.*(?:\n|$)", "", context or "")
    if not _SELECTION_HISTORY_REF_RE.search(text or ""):
        # 网页端历史块以【最近对话】开头，附件块有独立标记；只删历史，保留用户附件。
        cleaned = re.sub(r"【最近对话[^】]*】.*?(?=【用户上传文件|\Z)", "", cleaned, flags=re.S)
    return cleaned.strip()


def stock_selection_clarification_text() -> str:
    return (
        "可以，但先把范围定清楚，否则随口给名单很容易答偏。请选择市场、持有周期和风险偏好。\n\n"
        "例如：**A股 · 6—12个月 · 均衡型**。我会据此筛 3—5 只候选，逐只说明入选依据、主要风险和淘汰条件。\n\n"
        "如果你不想设置，直接回复“按默认方案”，我就按 A 股中线均衡来筛。"
    )


def normalize_stock_selection_request(text: str) -> str:
    """将澄清卡里的「按默认方案」落到唯一可执行口径，避免依赖历史上下文猜。"""
    query = (text or "").strip()
    if re.fullmatch(r"按默认方案[。！!]?", query, re.I):
        return "按 A 股、6—12 个月、均衡型筛 3 只"
    return query


def _json_size(value: Any, limit: int = 4200) -> str:
    """稳定、有限的 JSON 摘要，避免一个新闻/研报列表撑爆圆桌上下文。"""
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    return text if len(text) <= limit else text[:limit] + "…"


def _result_source(result: dict[str, Any]) -> str:
    data = result.get("data") if isinstance(result, dict) else None
    if isinstance(data, dict):
        for key in ("provider", "source", "source_name"):
            if data.get(key):
                return str(data[key])
    for key in ("provider", "source", "source_name"):
        if result.get(key):
            return str(result[key])
    return "公开数据源"


def _result_url(result: dict[str, Any]) -> str:
    data = result.get("data") if isinstance(result, dict) else None
    candidates = [data, result]
    for item in candidates:
        if not isinstance(item, dict):
            continue
        for key in ("source_url", "original_url", "source_link", "url"):
            value = str(item.get(key) or "").strip()
            if value.startswith(("http://", "https://")):
                return value[:1200]
    return ""


def _has_data(value: Any) -> bool:
    """判断工具结果是否真的有可供模型引用的内容。

    有些工具用 ``{"items": [], "count": 0, "source": ...}`` 表示空结果，不能只按
    ``dict`` 是否为空判断，否则会把“已接入但没有命中”误报成已覆盖。
    """
    if value in (None, [], {}):
        return False
    if isinstance(value, dict):
        if isinstance(value.get("items"), list):
            return bool(value["items"])
        if isinstance(value.get("count"), (int, float)):
            return value["count"] > 0
        if set(value) <= {"note", "source", "provider", "warnings"}:
            return False
    return True


_LIVE_REFERENCE_LABELS = {
    "search_our_content": "站内资料",
    "get_site_fast_news": "快讯",
    "get_site_articles": "文章",
    "get_stock_research": "研报",
    "get_recent_research": "投行研报",
    "get_institution_notes": "机构纪要",
    "get_recent_content_digest": "资料",
    "get_daily_review": "复盘",
    "get_stock_news": "公开资讯",
    "get_stock_announcements": "公告",
    "evidence_rag": "证据资料",
}

_LIVE_REFERENCE_SOURCE_LABELS = {
    "search_our_content": "稻草财经",
    "get_site_fast_news": "稻草财经",
    "get_site_articles": "稻草财经",
    "get_stock_research": "公开研报库",
    "get_recent_research": "稻草财经投行研报",
    "get_institution_notes": "稻草财经机构纪要",
    "get_daily_review": "稻草财经复盘",
    "get_stock_news": "公开财经资讯",
    "get_stock_announcements": "公司公告",
}


def live_references_for_trace(
    tool: str,
    result: dict[str, Any],
    *,
    limit: int = 4,
) -> list[dict[str, Any]]:
    """从一次真实工具返回中投影可在流式思考区展示的资料引用。

    这是“已返回的资料条目”，不是模型隐藏思考链。只投影标题、资料类型、来源、
    时间、短摘要和可选链接；没有标题的数值快照不会被伪装成文章/研报。
    ``get_recent_content_digest`` 的五类资料嵌套在 ``sources`` 下，这里展开为统一
    的条目形状，保证它和逐类工具在前端显示一致。
    """
    if not isinstance(result, dict) or not result.get("ok"):
        return []
    data = result.get("data")
    rows: list[tuple[str, dict[str, Any]]] = []
    if isinstance(data, list):
        rows = [("", item) for item in data if isinstance(item, dict)]
    elif isinstance(data, dict):
        items = data.get("items")
        if isinstance(items, list):
            rows = [("", item) for item in items if isinstance(item, dict)]
        sources = data.get("sources")
        if isinstance(sources, dict):
            rows = []
            for category, payload in sources.items():
                if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
                    continue
                rows.extend(
                    (str(category), item)
                    for item in payload["items"]
                    if isinstance(item, dict)
                )
        # Some evidence adapters call the ranked list ``evidence`` or ``digest``.
        if not rows:
            for key in ("evidence", "digest", "results"):
                candidate = data.get(key)
                if isinstance(candidate, list):
                    rows = [("", item) for item in candidate if isinstance(item, dict)]
                    if rows:
                        break
    if not rows:
        return []

    fallback_source = str(
        _result_source(result) or _LIVE_REFERENCE_SOURCE_LABELS.get(tool) or "研究资料"
    ).strip()
    if fallback_source == "公开数据源":
        fallback_source = _LIVE_REFERENCE_SOURCE_LABELS.get(tool, "研究资料")
    default_category = _LIVE_REFERENCE_LABELS.get(tool, "研究资料")
    references: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for nested_category, item in rows:
        # 标题是“可引用”的最低条件；name/price 之类快照字段不应被显示为资料标题。
        title = str(
            item.get("title")
            or item.get("headline")
            or item.get("document_title")
            or item.get("report_title")
            or ""
        ).strip()
        if not title:
            continue
        category = str(item.get("topic") or item.get("category_label") or nested_category or default_category).strip()
        source = str(
            item.get("source_name")
            or item.get("source")
            or item.get("org")
            or item.get("institution")
            or fallback_source
        ).strip()
        published_at = str(
            item.get("published_at")
            or item.get("date")
            or item.get("created_at")
            or item.get("reported_date")
            or item.get("create_time")
            or ""
        ).strip()[:40]
        detail = str(
            item.get("summary")
            or item.get("snippet")
            or item.get("ai_summary")
            or item.get("text_preview")
            or item.get("takeaway")
            or item.get("text")
            or item.get("lead")
            or ""
        ).strip()[:220]
        url = str(item.get("url") or item.get("source_url") or item.get("source_link") or "").strip()[:1200]
        key = (category, title, source)
        if key in seen:
            continue
        seen.add(key)
        references.append({
            "id": str(item.get("id") or f"live:{tool}:{len(references)}"),
            "category": category,
            "title": title[:240],
            "detail": detail,
            "source": source[:120],
            "url": url,
            "published_at": published_at,
        })
        if len(references) >= max(1, min(int(limit or 4), 8)):
            break
    return references


def _trace(tool: str, result: dict[str, Any], *, args: dict[str, Any]) -> dict[str, Any]:
    ok = bool(result.get("ok"))
    data = result.get("data")
    source = _result_source(result)
    # 部分旧数据函数返回的是裸 dict/list，没有带 provider 字段；这里仍然给前端
    # 一个可读的数据源标签，避免用户只看到“公开数据源”而无法判断数据来自哪里。
    if source == "公开数据源":
        source = {
            "get_market_quote": "行情供应商（可能延迟）",
            "compare_stocks": "比较公开快照",
            "get_valuation": "东方财富估值",
            "get_financials": "东方财富财报",
            "get_financial_statements": "东方财富三表",
            "get_fund_flow": "东方财富资金流",
            "get_stock_news": "公开财经资讯",
            "get_site_fast_news": "稻草财经快讯",
            "get_site_articles": "稻草财经文章",
            "get_stock_research": "公开研报库（东财）",
            "get_recent_research": "稻草财经投行研报",
            "get_institution_notes": "稻草财经机构纪要",
            "get_recent_content_digest": "稻草财经四类资料统一索引",
            "get_celebrity_views": "名人观点（白名单）",
            "get_daily_review": "稻草财经复盘",
            "get_industry_context": "行业归属与行业资料",
            "get_peer_comparison": "同行候选",
            "get_supply_chain_context": "上下游资料",
        }.get(tool, source)
    has_industry_fact = isinstance(data, dict) and bool(data.get("industry") or data.get("board"))
    if ok and (_has_data(data) or has_industry_fact):
        item_rows = data.get("items") if isinstance(data, dict) and isinstance(data.get("items"), list) else data if isinstance(data, list) else []
        if isinstance(item_rows, list) and item_rows:
            titles = [
                str(item.get("title") or item.get("name") or item.get("headline") or "").strip()
                for item in item_rows[:2] if isinstance(item, dict)
            ]
            titles = [title[:54] for title in titles if title]
            summary = f"命中 {len(item_rows)} 条可复核条目"
            if titles:
                summary += "：" + "；".join(titles)
            if isinstance(data, dict) and (data.get("industry") or data.get("board")):
                summary += f"；行业 {data.get('industry') or data.get('board')}"
        elif isinstance(data, dict) and (data.get("industry") or data.get("board")):
            summary = f"识别行业：{str(data.get('industry') or data.get('board'))[:80]}"
        elif isinstance(data, dict) and isinstance(data.get("count"), (int, float)):
            summary = f"返回 {int(data.get('count') or 0)} 条数据"
        else:
            summary = "已返回真实数据"
    else:
        summary = str(result.get("note") or result.get("error") or "暂无数据")[:220]
    return {
        "tool": tool,
        "args": args,
        "ok": ok,
        "source": source,
        "url": _result_url(result),
        "summary": summary,
        "references": live_references_for_trace(tool, result),
    }


_PLATFORM_CATEGORIES = {
    "site_fast_news",
    "site_articles",
    "recent_research",
    "institution_notes",
    "celebrity_views",
    "industry_context",
    "peer_comparison",
    "supply_chain",
}

_EXTERNAL_CATEGORIES = {
    "stock_research",
    "stock_news",
    "evidence_rag",
}

_PUBLIC_DATA_TOOLS = {
    "get_market_quote",
    "get_valuation",
    "get_financials",
    "get_financial_statements",
    "get_fund_flow",
    "get_stock_news",
    "get_analyst_consensus",
    "get_stock_announcements",
    "get_dividend_history",
}

ResearchProgressCallback = Callable[[str, dict[str, Any]], Awaitable[None]]


def _evidence_references(
    research_index: dict[str, Any],
    traces: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """把索引条目和公开数据通道整理成用户可读的引用依据。

    这里展示的是已经执行、已经返回数据的证据，不是模型隐藏思考链。站内资料和
    外部公开资料分组后，前端可以直接展示标题、来源、时间和链接，而无需暴露内部
    ``get_xxx`` 函数名。
    """
    references: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for item in (research_index.get("items") or [])[:16]:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category") or "").strip()
        if category in _PLATFORM_CATEGORIES:
            group = "platform"
            group_label = "稻草财经平台"
        elif category in _EXTERNAL_CATEGORIES:
            group = "external"
            group_label = "外部公开资料"
        else:
            continue
        title = str(item.get("title") or "").strip()
        source = str(item.get("source_name") or "").strip()
        key = (group, title, source)
        if not title or key in seen:
            continue
        seen.add(key)
        credibility = None
        try:
            if item.get("source_score") is not None:
                credibility = round(float(item.get("source_score")), 3)
        except (TypeError, ValueError):
            credibility = None
        references.append({
            "id": str(item.get("id") or ""),
            "group": group,
            "group_label": group_label,
            "category": str(item.get("category_label") or category),
            "title": title[:240],
            "detail": str(item.get("summary") or "").strip()[:360],
            "source": source[:120],
            "url": str(item.get("url") or "").strip()[:1200],
            "published_at": str(item.get("published_at") or item.get("collected_at") or "").strip()[:40],
            "credibility": credibility,
        })

    # 数值事实没有标题可引用时，仍把真实返回的数据通道呈现出来；摘要只使用
    # trace 的可读结果，不把参数或内部工具标识直接交给用户。
    tool_labels = {
        "get_market_quote": "行情快照",
        "get_valuation": "估值快照",
        "get_financials": "财报摘要",
        "get_financial_statements": "财务三表",
        "get_fund_flow": "资金流向",
        "get_stock_news": "公开财经资讯",
        "get_analyst_consensus": "卖方一致预期",
        "get_stock_announcements": "公司公告",
        "get_dividend_history": "分红历史",
        "get_industry_context": "行业资料",
        "get_peer_comparison": "同行候选",
        "get_supply_chain_context": "上下游资料",
    }
    for trace in traces:
        if not isinstance(trace, dict) or trace.get("tool") not in _PUBLIC_DATA_TOOLS or not trace.get("ok"):
            continue
        title = tool_labels.get(str(trace.get("tool")), "公开数据")
        source = str(trace.get("source") or "公开数据源").strip()
        detail = str(trace.get("summary") or "已返回真实数据").strip()[:240]
        key = ("external", title, source)
        if key in seen:
            continue
        seen.add(key)
        references.append({
            "id": f"trace:{trace.get('tool')}",
            "group": "external",
            "group_label": "外部公开数据",
            "category": title,
            "title": title,
            "detail": detail,
            "source": source[:120],
            "url": str(trace.get("url") or "").strip()[:1200],
            "published_at": "",
            "credibility": None,
        })
    return references[:24]


def _friendly_gap(gap: str) -> str:
    """给模型上下文使用的可读缺口文案，避免内部函数名被原样回显。"""
    text = str(gap or "").strip()
    labels = {
        "get_site_fast_news": "稻草财经快讯",
        "get_site_articles": "稻草财经文章",
        "get_recent_research": "稻草财经投行研报",
        "get_institution_notes": "稻草财经机构纪要",
        "get_celebrity_views": "名人观点",
        "get_fund_flow": "外部资金流",
        "get_market_quote": "行情快照",
        "get_valuation": "估值快照",
        "get_financials": "财报摘要",
        "get_financial_statements": "财务三表",
        "evidence_rag": "站内证据库",
        "get_industry_context": "行业资料",
        "get_peer_comparison": "同行候选",
        "get_supply_chain_context": "上下游资料",
    }
    for key, label in labels.items():
        if text.startswith(key):
            suffix = text[len(key):].lstrip(" :：")
            return f"{label}{('：' + suffix) if suffix else ''}"
    return text[:180]


def _stock_from_text(text: str) -> Optional[StockSnapshot]:
    # 先认知名港/美股，再回退全 A 名称表；否则「建滔集团值得买吗」会被误当成
    # 没有具体标的的问题，无法触发行情、估值和资料全量扫描。
    code = resolve_known_symbol(text or "") or resolve_to_code(text or "")
    if not code:
        match = _TICKER_RE.search((text or "").upper())
        candidate = match.group(0) if match else ""
        # 过滤自然语言中的常见缩写，避免把「AI」「PE」误当成 ticker。
        if not candidate or len(candidate) == 1 or candidate in {"AI", "PE", "PB", "ROE", "VIX"}:
            return None
        return StockSnapshot(
            symbol=candidate,
            name=candidate,
            market="HK" if candidate.isdigit() else "US",
        )
    market = "HK" if str(code).isdigit() and len(str(code)) == 5 else "CN"
    return StockSnapshot(
        symbol=code,
        name=name_of_code(code) or known_symbol_name(code) or code,
        market=market,
    )


def _merge_stock_quote(stock: StockSnapshot, result: dict[str, Any]) -> StockSnapshot:
    data = result.get("data") if isinstance(result, dict) else None
    rows = data.get("quotes") if isinstance(data, dict) else None
    row = rows[0] if isinstance(rows, list) and rows else None
    if not isinstance(row, dict):
        return stock
    return stock.model_copy(update={
        "name": row.get("name") or stock.name,
        "market": row.get("market") or stock.market,
        "current_price": row.get("price") if row.get("price") is not None else stock.current_price,
        "change_percent": (
            row.get("change_percent")
            if row.get("change_percent") is not None
            else (row.get("change_pct") if row.get("change_pct") is not None else stock.change_percent)
        ),
    })


def is_research_question(objective: str) -> bool:
    """过滤问候/产品问题，避免深度入口对「hi」也发起全套爬取。"""
    text = (objective or "").strip()
    if not text:
        return False
    if re.fullmatch(r"(hi|hello|你好|嗨|在吗|谢谢|感谢)[!！。\s]*", text, re.I):
        return False
    return bool(_MARKET_WORDS.search(text) or _STOCK_WORDS.search(text) or resolve_to_code(text))


def is_comparison_question(objective: str) -> bool:
    """是否明确要求横向比较标的。

    这个判定只负责触发“对称取证”路径，不代表已经识别出了足够的标的；
    后续必须同时拿到至少两只股票才允许形成偏向性结论。
    """
    return bool(_COMPARISON_RE.search((objective or "").strip()))


def _comparison_stocks(text: str, fallback: Optional[StockSnapshot] = None) -> list[StockSnapshot]:
    """从当前问题中按出现顺序提取 2~4 个 A 股标的，避免整句只解析到第一只。

    名称索引来自同一套中文名→代码表，代码和名称混写也能去重；只读取当前问题，
    不把历史上下文中的旧标的带入新的比较题。
    """
    query = str(text or "")
    found: list[tuple[int, int, str, str]] = []
    seen_codes: set[str] = set()
    for match in _A_SHARE_CODE_RE.finditer(query):
        code = match.group(0)
        if code not in seen_codes:
            found.append((match.start(), 0, code, name_of_code(code) or code))
            seen_codes.add(code)

    # 同一公司可能同时命中短名/全名（例如“比亚迪电子”包含“比亚迪”），
    # 以最长名称为准，避免把一个主体拆成两只股票。
    name_hits: list[tuple[int, int, str, str]] = []
    for name, code in all_name_code().items():
        if len(name) < 3 or name not in query:
            continue
        name_hits.append((query.find(name), -len(name), str(code), name))
    name_hits.sort(key=lambda item: (item[0], item[1]))
    for start, _, code, name in name_hits:
        if code in seen_codes:
            continue
        # 如果命中的是更长主体的前缀（如比亚迪→比亚迪电子），跳过短名。
        if any(other_code != code and other_start == start and len(other_name) > len(name)
               for other_start, _, other_code, other_name in name_hits):
            continue
        found.append((start, -len(name), code, name))
        seen_codes.add(code)

    found.sort(key=lambda item: (item[0], item[1]))
    if fallback and fallback.symbol and fallback.symbol not in seen_codes:
        found.insert(0, (0, 0, fallback.symbol, fallback.name or fallback.symbol))
    return [
        StockSnapshot(symbol=code, name=name or code, market="CN")
        for _, _, code, name in found[:4]
    ]


async def _call_with_flags(
    tool: str,
    args: dict[str, Any],
    *,
    ifind_user: bool = False,
    progress: Optional[ResearchProgressCallback] = None,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    if progress:
        await progress("start", {"tool": tool, "args": args})
    try:
        result = await execute_tool(tool, args, ifind_user=ifind_user)
    except Exception as exc:  # execute_tool 已经收敛异常，这里再守一层
        result = {"ok": False, "error": str(exc)[:160]}
    trace = _trace(tool, result, args=args)
    if progress:
        await progress("result", trace)
    return tool, result, trace


async def build_research_packet(
    objective: str,
    context: str = "",
    stock: Optional[StockSnapshot] = None,
    *,
    ifind_user: bool = False,
    progress: Optional[ResearchProgressCallback] = None,
) -> dict[str, Any]:
    """并行准备一份可交给模型/多 Agent/RAG 的统一研究数据包。"""
    if not is_research_question(objective):
        return {"stock": stock, "modules": {}, "traces": [], "sources": [], "gaps": ["非研究类问题"]}

    comparison_stocks = _comparison_stocks(objective, stock) if is_comparison_question(objective) else []
    # 比较题只把单只 stock 当作页面预选标的的兜底；一旦当前问题明确提到多只，
    # 必须以当前问题抽出的完整集合为准，不能让第一只覆盖其余标的。
    is_comparison = len(comparison_stocks) >= 2
    resolved_stock = (comparison_stocks[0] if is_comparison else None) or stock or _stock_from_text(f"{objective}\n{context}")
    stock_args = {
        "symbol": resolved_stock.symbol,
        "market": resolved_stock.market,
    } if resolved_stock else None

    calls: list[tuple[str, dict[str, Any]]] = []
    if is_comparison:
        # 比较题先取一次同口径快照，再为每一边补资金、公告、分红和站内内容。
        # compare_stocks 是“比较成立”的硬门槛：没有两边的同口径结果，后续不得
        # 把单边资料更丰富误写成“更偏好”。
        calls.append(("compare_stocks", {"symbols": ",".join(item.symbol for item in comparison_stocks)}))
        for side in comparison_stocks:
            side_args = {"symbol": side.symbol, "market": side.market}
            side_query = str(side.name or side.symbol).strip()
            calls.extend([
                ("get_fund_flow", side_args),
                ("get_stock_news", {**side_args, "limit": 6}),
                ("get_stock_announcements", {**side_args, "days": 30}),
                ("get_dividend_history", {**side_args, "limit": 6}),
                ("get_financial_statements", side_args),
                ("get_industry_context", {**side_args, "name": side_query}),
                ("get_peer_comparison", {**side_args, "name": side_query, "limit": 5}),
                ("get_supply_chain_context", {**side_args, "name": side_query}),
                ("get_site_fast_news", {"query": side_query, "days": 30, "limit": 12}),
                ("get_site_articles", {"query": side_query, "days": 30, "limit": 12}),
                ("get_stock_research", {**side_args, "limit": 6}),
                ("get_recent_research", {"query": side_query, "limit": 8}),
                ("get_institution_notes", {"query": side_query, "limit": 8}),
                ("get_celebrity_views", {"query": side_query}),
            ])
    elif stock_args:
        # 个股深度研究固定扫描两层资料：
        # 1) 公开数据（行情/估值/财务/现金流/资金/公开新闻）；
        # 2) 稻草财经五类站内内容（快讯、文章、研报、机构纪要、名人观点）。
        # 五类内容使用独立工具名，确保数据包不会因为同一工具多次调用而互相覆盖。
        # 内容流的搜索接口对“代码 + 名称”并不都支持 OR；优先用解析后的公司名，
        # 没有名称时再退回代码，避免站内文章/纪要被整句关键词误过滤。
        content_query = str(resolved_stock.name or resolved_stock.symbol or "").strip()
        calls.extend([
            ("get_market_quote", stock_args),
            ("get_valuation", stock_args),
            ("get_financials", stock_args),
            ("get_financial_statements", stock_args),
            ("get_fund_flow", stock_args),
            ("get_stock_news", {**stock_args, "limit": 6}),
            # 估值问题不能只看 PE/PB：同时取卖方一致预期、近期公告和分红，
            # 让“贵不贵”能回答估值溢价由什么支撑、哪些事件会证伪。
            ("get_analyst_consensus", stock_args),
            ("get_stock_announcements", {**stock_args, "days": 30}),
            ("get_dividend_history", {**stock_args, "limit": 6}),
            ("get_industry_context", {**stock_args, "name": content_query}),
            ("get_peer_comparison", {**stock_args, "name": content_query, "limit": 5}),
            ("get_supply_chain_context", {**stock_args, "name": content_query}),
            ("get_site_fast_news", {"query": content_query, "days": 30, "limit": 12}),
            ("get_site_articles", {"query": content_query, "days": 30, "limit": 12}),
            ("get_stock_research", {**stock_args, "limit": 6}),
            ("get_recent_research", {"query": content_query, "limit": 8}),
            ("get_institution_notes", {"query": content_query, "limit": 8}),
            # 名人观点是白名单专属工具。普通用户会得到“未开放”的结构化提示，
            # 不会泄露内容；白名单用户则由 execute_tool 的 ContextVar 门控后纳入综合。
            ("get_celebrity_views", {"query": content_query}),
            ("get_daily_review", {}),
        ])
    else:
        # 非个股问题用宏观/站内内容，不强行拼一个标的。
        # 市场结构硬数据（指数/成交额/涨跌家数/涨跌停/板块+主力净额/连板梯队）对任何
        # 大盘级问题都是地基——没有官方数字，报告只能靠公众号文章拼凑（曾出现 68%
        # 可信度只引 3 篇微信文的空档）。无论走哪条内容分支都补上。
        calls.append(("get_market_structure", {}))
        recent_days = recent_content_window_days(objective)
        if recent_days is not None:
            calls.append(("get_recent_content_digest", {"query": "", "days": recent_days, "limit": 500}))
        else:
            calls.extend([
                ("get_market_data", {"query": objective[:120]}),
                ("get_daily_review", {}),
                ("search_our_content", {"query": objective[:80], "days": 3, "limit": 20}),
                ("get_recent_research", {"query": objective[:80], "limit": 8}),
            ])

    outcomes = await asyncio.gather(
        *[_call_with_flags(tool, args, ifind_user=ifind_user, progress=progress) for tool, args in calls],
        return_exceptions=True,
    )
    modules: dict[str, Any] = {}
    traces: list[dict[str, Any]] = []
    sources: list[str] = []
    gaps: list[str] = []
    quote_result: Optional[dict[str, Any]] = None

    for item in outcomes:
        if isinstance(item, Exception):
            gaps.append(str(item)[:160])
            continue
        tool, result, trace = item
        # 比较题同一工具会按标的调用多次，不能让后一次覆盖前一次；动态键保留
        # 每一边的原始结果，compare_stocks 则作为统一主表。
        module_key = tool
        if is_comparison and tool != "compare_stocks":
            side_symbol = str((trace.get("args") or {}).get("symbol") or "").strip()
            if not side_symbol:
                side_query = str((trace.get("args") or {}).get("query") or "").strip()
                side_symbol = str(resolve_to_code(side_query) or "").strip()
            if side_symbol:
                module_key = f"{tool}:{side_symbol}"
            elif tool in modules:
                module_key = f"{tool}:{len([key for key in modules if key.startswith(tool + ':')]) + 1}"
        modules[module_key] = result.get("data") if result.get("ok") else None
        traces.append(trace)
        # sources 是“实际命中过的来源”列表；工具虽然注册了某来源，但返回空/未授权时
        # 不把它冒充成已使用的数据源，缺口由 gaps/content_scope 单独展示。
        data_value = result.get("data")
        trace_has_structured_fact = (
            tool == "get_industry_context"
            and isinstance(data_value, dict)
            and bool(data_value.get("industry") or data_value.get("board"))
        )
        if trace["source"] and (_has_data(data_value) or trace_has_structured_fact) and trace["source"] not in sources:
            sources.append(trace["source"])
        if tool == "get_market_quote" and not is_comparison:
            quote_result = result
        if not result.get("ok") or not _has_data(result.get("data")):
            gaps.append(f"{tool}: 暂无可用数据")

    # 把“会查哪些资料”显式写进数据包，而不是只让模型从工具名猜。
    # status=available / no_data / not_authorized 让前端和回答器都能准确解释覆盖范围。
    content_scope: dict[str, dict[str, Any]] = {}
    if is_comparison:
        compare_data = modules.get("compare_stocks") if isinstance(modules.get("compare_stocks"), dict) else {}
        compare_items = compare_data.get("items") if isinstance(compare_data, dict) else []
        compare_items = compare_items if isinstance(compare_items, list) else []
        comparison_basis = compare_data.get("comparison_basis") if isinstance(compare_data, dict) else {}
        comparison_basis = comparison_basis if isinstance(comparison_basis, dict) else {}
        side_scope: list[dict[str, Any]] = []
        for side in comparison_stocks:
            side_item = next((item for item in compare_items if isinstance(item, dict) and str(item.get("symbol")) == side.symbol), {})
            side_scope.append({
                "symbol": side.symbol,
                "name": side.name,
                "status": "available" if side_item and not side_item.get("data_gaps") else "partial" if side_item else "no_data",
                "data_gaps": list(side_item.get("data_gaps") or []) if isinstance(side_item, dict) else ["比较快照暂不可用"],
            })
        compare_status = "available" if len(compare_items) >= len(comparison_stocks) and all(not item.get("data_gaps") for item in side_scope) else "partial" if compare_items else "no_data"
        content_scope["比较口径"] = {
            "module": "compare_stocks",
            "status": compare_status,
            "symbols": [side.symbol for side in comparison_stocks],
            "sides": side_scope,
            "comparison_basis": comparison_basis,
            "rule": (
                "行情/估值按同一抓取时点的快照比较；财务指标可按共同报告期横向比较。"
                if comparison_basis.get("is_strictly_comparable") is True
                else "当前仅比较同一抓取时点的行情/估值快照；各自最新财报分别展示，营收、利润、ROE、毛利率和 EPS 不作横向排名。"
            ),
        }
        scope_specs = (
            ("快讯", "get_site_fast_news"),
            ("文章", "get_site_articles"),
            ("研报", "get_stock_research"),
            ("投行研报", "get_recent_research"),
            ("机构纪要", "get_institution_notes"),
            ("名人观点", "get_celebrity_views"),
            ("行业", "get_industry_context"),
            ("同行", "get_peer_comparison"),
            ("上下游", "get_supply_chain_context"),
            ("资金", "get_fund_flow"),
            ("公告", "get_stock_announcements"),
            ("分红", "get_dividend_history"),
            ("财务三表", "get_financial_statements"),
        )
        for label, key in scope_specs:
            sides: list[dict[str, Any]] = []
            for side in comparison_stocks:
                value = modules.get(f"{key}:{side.symbol}")
                status = "available" if _has_data(value) else "no_data"
                if key == "get_industry_context" and isinstance(value, dict) and (value.get("industry") or value.get("board")):
                    status = "available"
                sides.append({"symbol": side.symbol, "name": side.name, "status": status})
                if status == "no_data":
                    gaps.append(f"{side.name}{label}: 暂无可用数据")
            available_count = sum(item["status"] == "available" for item in sides)
            content_scope[label] = {
                "module": key,
                "status": "available" if available_count == len(sides) else "partial" if available_count else "no_data",
                "sides": sides,
            }
        content_scope["公开数据"] = {
            "module": "compare_stocks / get_fund_flow / get_stock_announcements / get_dividend_history / get_financial_statements",
            "status": content_scope["比较口径"]["status"],
            "available": ["compare_stocks"] if compare_items else [],
            "sides": side_scope,
        }
    elif resolved_stock:
        scope_specs = (
            ("快讯", "get_site_fast_news"),
            ("文章", "get_site_articles"),
            ("研报", "get_stock_research"),
            ("投行研报", "get_recent_research"),
            ("机构纪要", "get_institution_notes"),
            ("名人观点", "get_celebrity_views"),
            ("行业", "get_industry_context"),
            ("同行", "get_peer_comparison"),
            ("上下游", "get_supply_chain_context"),
        )
        for label, key in scope_specs:
            value = modules.get(key)
            status = "available" if _has_data(value) else "no_data"
            if key == "get_industry_context" and isinstance(value, dict) and (value.get("industry") or value.get("board")):
                status = "available"
            note = ""
            if isinstance(value, dict):
                note = str(value.get("note") or "")[:180]
                if "未开放" in note or "白名单" in note:
                    status = "not_authorized"
            content_scope[label] = {"module": key, "status": status}
            if note:
                content_scope[label]["note"] = note
            if status == "not_authorized":
                gaps.append(f"{label}: 当前账号未授权，未纳入结论")
            elif status == "no_data":
                gaps.append(f"{label}: 暂无可用数据")

        public_data_keys = (
            "get_market_quote", "get_valuation", "get_financials", "get_financial_statements",
            "get_fund_flow", "get_stock_news", "get_analyst_consensus",
            "get_stock_announcements", "get_dividend_history",
        )
        public_available = [key for key in public_data_keys if _has_data(modules.get(key))]
        content_scope["公开数据"] = {
            "module": " / ".join(public_data_keys),
            "status": "available" if public_available else "no_data",
            "available": public_available,
            "raw_count": len(public_data_keys),
            "selected_count": len(public_available),
        }
    else:
        # Generic recent-content requests have no stock entity, but still expose
        # the same auditable per-source coverage contract to the model/frontend.
        digest = modules.get("get_recent_content_digest")
        if isinstance(digest, dict):
            for label in ("快讯", "文章", "研报", "投行研报", "机构纪要"):
                source = (digest.get("sources") or {}).get(label)
                source = source if isinstance(source, dict) else {}
                coverage = source.get("coverage") if isinstance(source.get("coverage"), dict) else {}
                count = int(source.get("count") or 0)
                complete = bool(coverage.get("complete"))
                status = "available" if count and complete else "partial" if count else "no_data"
                content_scope[label] = {
                    "module": "get_recent_content_digest",
                    "status": status,
                    "raw_count": int(coverage.get("scanned_count") or 0),
                    "selected_count": count,
                    "window_days": digest.get("window_days"),
                    "complete": complete,
                }
                if status != "available":
                    gaps.append(f"{label}: 最近窗口扫描未完整或暂无命中")

    if resolved_stock and quote_result:
        resolved_stock = _merge_stock_quote(resolved_stock, quote_result)

    # RAG 证据层：保留标题、出处、时间和可信度，给圆桌做可引用来源。
    # 证据 RAG 使用规范标的作为主查询，避免把“现在贵不贵/我没说……”等整句
    # 词片带入站内搜索。主题词只作为第二层补充，不覆盖实体过滤。
    query_parts = []
    if comparison_stocks:
        for item in comparison_stocks:
            query_parts.extend([str(item.name or "").strip(), str(item.symbol or "").strip()])
    elif resolved_stock:
        query_parts.extend([str(resolved_stock.name or "").strip(), str(resolved_stock.symbol or "").strip()])
    query = " ".join(item for item in dict.fromkeys(query_parts) if item) or None
    try:
        evidence: list[tuple[str, Any]] = []
        evidence_symbols = comparison_stocks if is_comparison else ([resolved_stock] if resolved_stock else [])
        if evidence_symbols:
            seen_evidence: set[tuple[str, str]] = set()
            for evidence_stock in evidence_symbols:
                side_query = " ".join(
                    item for item in (str(evidence_stock.name or "").strip(), str(evidence_stock.symbol or "").strip())
                    if item
                ) or query
                rows = list_data_items(symbol=evidence_stock.symbol, query=side_query, limit=8, sort="time_desc")
                if not rows:
                    rows = list_data_items(symbol=evidence_stock.symbol, limit=8, sort="time_desc")
                for row in rows:
                    key = (evidence_stock.symbol, str(getattr(row, "title", "") or ""))
                    if key not in seen_evidence:
                        seen_evidence.add(key)
                        evidence.append((evidence_stock.symbol, row))
        elif query:
            evidence = [("", row) for row in list_data_items(query=query, limit=8, sort="time_desc")]
    except Exception as exc:
        evidence = []
        gaps.append(f"evidence_rag: {str(exc)[:120]}")
    evidence_rows = [
        {
            "symbol": symbol,
            "title": item.title,
            "source": item.source_name,
            "url": item.url,
            "credibility": item.credibility_score,
            "collected_at": item.collected_at or item.created_at,
            "summary": item.text_preview,
        }
        for symbol, item in evidence
    ]
    if evidence_rows:
        modules["evidence_rag"] = evidence_rows
        sources.extend([str(row["source"]) for row in evidence_rows if row.get("source")])
    else:
        gaps.append("evidence_rag: 证据库未命中")

    # 统一内容索引：标签、实体、来源可信度、新鲜度和跨源去重都在模型前完成。
    # 索引层失败只影响摘要，不阻断原始模块和数值事实回退。
    try:
        # The recent-window tool already scanned/indexed all four feeds.  Expose
        # its per-source rows under the normal module names as well so the
        # existing index/coverage machinery can reuse the same evidence packet.
        recent_digest = modules.get("get_recent_content_digest")
        if isinstance(recent_digest, dict) and isinstance(recent_digest.get("sources"), dict):
            source_modules = {
                "快讯": "get_site_fast_news",
                "文章": "get_site_articles",
                # 本地“研报”与海外投行研报是两个独立来源；不要让
                # 统一 digest 已经扫描到的本地研报在二次索引时丢失。
                "研报": "get_stock_research",
                "投行研报": "get_recent_research",
                "机构纪要": "get_institution_notes",
            }
            for label, module_name in source_modules.items():
                source_payload = recent_digest["sources"].get(label)
                if isinstance(source_payload, dict) and module_name not in modules:
                    modules[module_name] = source_payload.get("items") or []
        research_index = build_research_index(
            modules,
            objective=objective,
            stock=resolved_stock,
            persist_annotations=True,
        )
        digest_for_context: list[dict[str, Any]] = []
        digest_counts: dict[str, int] = {}
        for item in research_index.get("digest") or []:
            category = str(item.get("category") or "")
            if digest_counts.get(category, 0) >= 2:
                continue
            digest_for_context.append(item)
            digest_counts[category] = digest_counts.get(category, 0) + 1
        modules["research_index"] = {
            "digest": digest_for_context,
            "coverage": research_index.get("coverage") or {},
            "stats": research_index.get("stats") or {},
        }
        for item in research_index.get("digest") or []:
            source = str(item.get("source") or "").strip()
            if source and source not in sources:
                sources.append(source)
    except Exception as exc:  # noqa: BLE001 - 统一索引可降级
        research_index = {"digest": [], "coverage": {}, "stats": {}}
        gaps.append(f"research_index: {str(exc)[:120]}")

    indexed_labels = {
        "快讯": "site_fast_news",
        "文章": "site_articles",
        "研报": "stock_research",
        "投行研报": "recent_research",
        "机构纪要": "institution_notes",
        "名人观点": "celebrity_views",
    }
    for label, category in indexed_labels.items():
        indexed = (research_index.get("coverage") or {}).get(category) or {}
        if indexed and label in content_scope:
            content_scope[label]["raw_count"] = indexed.get("raw_count", 0)
            content_scope[label]["selected_count"] = indexed.get("selected_count", 0)
            content_scope[label]["top_tags"] = indexed.get("top_tags", [])

    evidence_references = _evidence_references(research_index, traces)
    compact_modules = {key: _json_size(value) for key, value in modules.items()}
    readable_gaps = "; ".join(_friendly_gap(item) for item in dict.fromkeys(gaps))
    comparison_context = ""
    if is_comparison:
        comparison_basis = ((modules.get("compare_stocks") or {}).get("comparison_basis")
                            if isinstance(modules.get("compare_stocks"), dict) else {})
        comparison_basis = comparison_basis if isinstance(comparison_basis, dict) else {}
        strict_basis = comparison_basis.get("is_strictly_comparable") is True
        basis_rule = (
            "财务指标可按共同报告期横向比较；仍需逐项引用实际返回字段。"
            if strict_basis else
            "报告期不一致时，只能横向比较同一抓取时点的行情/估值快照；营收、利润、ROE、毛利率和 EPS 必须分别标注报告期，不得排名。"
        )
        comparison_context = (
            "【比较题硬规则】\n"
            f"已识别标的：{json.dumps([item.model_dump(by_alias=True) for item in comparison_stocks], ensure_ascii=False)}\n"
            f"比较口径：{basis_rule}\n"
            "必须逐一核对每个标的的实际返回字段；任何一边关键字段缺失时，只能给条件化选择或暂不偏向，"
            "绝不能把‘某一边资料更多/命中更多’写成‘更值得/更偏好’。\n"
            f"比较主表：{json.dumps(modules.get('compare_stocks') or {}, ensure_ascii=False)}\n"
        )
    packet_context = (
        "【统一研究 Harness 数据包】\n"
        f"目标：{objective[:800]}\n"
        f"原始上下文：{context[:1800]}\n"
        f"标的：{resolved_stock.model_dump(by_alias=True) if resolved_stock else '未锁定具体标的'}\n"
        f"{comparison_context}"
        "研究范围：站内快讯、文章、研报、机构纪要、名人观点（按权限）+ 公开行情/估值/财务/资金/新闻，"
        "并额外扫描行业归属、公开同行候选和上下游/价格传导线索；"
        "回答时必须区分公司直接事实、行业背景、机构观点和名人观点，按来源交叉核验，未命中或未授权的类别不得补写。\n"
        f"资料覆盖：{json.dumps(content_scope, ensure_ascii=False)}\n"
        f"数据模块：{json.dumps(compact_modules, ensure_ascii=False)}\n"
        f"证据缺口：{readable_gaps[:1200] or '未发现明显缺口'}\n"
    )
    return {
        "stock": resolved_stock,
        "modules": modules,
        "traces": traces,
        "sources": list(dict.fromkeys(sources))[:12],
        "gaps": list(dict.fromkeys(gaps))[:12],
        "content_scope": content_scope,
        "comparison": {
            "is_comparison": is_comparison,
            "stocks": [item.model_dump(by_alias=True) for item in comparison_stocks],
            "items": modules.get("compare_stocks") if is_comparison else {},
            "comparison_basis": (
                (modules.get("compare_stocks") or {}).get("comparison_basis", {})
                if is_comparison and isinstance(modules.get("compare_stocks"), dict) else {}
            ),
        },
        "evidence_references": evidence_references,
        "context": packet_context,
    }
