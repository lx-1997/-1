"""LLM 工具注册表（AI 原生 tool-use 的工具来源）。

把平台已有的真实数据函数（行情/财报/资金流/估值/一致预期）包装成「工具」，
通过 OpenAI 兼容的 function-calling 暴露给模型，由 `CloudResearchLLM.run_tool_agent`
驱动「模型自己决定调哪个工具 → 服务端执行真实取数 → 结果回灌 → 再推理」的闭环。

与 agent 引擎注册表（agent_engines.ENGINE_REGISTRY）、行情源注册表
（market_data.QUOTE_PROVIDERS）同构：新增一个工具 = 写 handler + register_tool 一行。

红线：工具只返回 ground-truth 数据，verdict/信号仍由确定性引擎给出；模型负责挑数据、
做解释，不负责编造结论。所有 handler 失败都收敛成 {"ok": False, "error": ...}，
不抛断整个 agent 循环（优雅降级）。
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Optional

# iFinD 灰度位：仅在 execute_tool 作用域内 set/reset，供工具 handler 读取（handler 经 **arguments 调用、无法直接传参）。
# 用 ContextVar 而非 tool JSON Schema 参数 → 模型看不到、无法在 tool args 里伪造 _ifind=true 越权。
_IFIND_GRADE: ContextVar[bool] = ContextVar("ifind_grade", default=False)

import re as _re
from datetime import datetime as _dt, timedelta as _td, timezone as _tz

from . import bull_playbook, financial_statements
from .consensus_source import fetch_analyst_consensus
from .eastmoney_data import fetch_eastmoney_earnings, fetch_fund_flow
from .market_data import fetch_market_quotes
from .realtime_messages import list_realtime_messages
from .shared_utils import safe_float
from .valuation_source import fetch_valuation

ToolHandler = Callable[..., Awaitable[Any]]


@dataclass
class AgentTool:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema（function 参数）
    handler: ToolHandler

    def openai_spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


TOOL_REGISTRY: dict[str, AgentTool] = {}


def register_tool(tool: AgentTool) -> AgentTool:
    TOOL_REGISTRY[tool.name] = tool
    return tool


def list_tools() -> list[AgentTool]:
    return list(TOOL_REGISTRY.values())


def openai_tool_specs(extra_tools: dict[str, "AgentTool"] | None = None) -> list[dict[str, Any]]:
    """供 chat.completions.create(tools=...) 使用的工具清单。

    extra_tools：本次运行的动态工具（如发现到的外部 MCP 工具），与静态注册表合并。
    """
    specs = [tool.openai_spec() for tool in TOOL_REGISTRY.values()]
    if extra_tools:
        specs.extend(tool.openai_spec() for tool in extra_tools.values())
    return specs


async def execute_tool(
    name: str,
    arguments: dict[str, Any],
    extra_tools: dict[str, "AgentTool"] | None = None,
    ifind_user: bool = False,
) -> dict[str, Any]:
    """执行一个工具调用，永不抛出：成功 {"ok":True,"data":...}，失败 {"ok":False,"error":...}。

    extra_tools 优先于静态注册表（同名时动态覆盖），便于注入本次运行的 MCP 工具。
    ifind_user（默认 False）= 本次是否对白名单用户启用 iFinD 增强；仅在本调用作用域内经 ContextVar 暴露给
    工具 handler，**try/finally 严格 reset**，杜绝跨请求/跨调用串味。默认 False 保证现网及其他调用方零变化。
    """
    tool = (extra_tools or {}).get(name) or TOOL_REGISTRY.get(name)
    if tool is None:
        return {"ok": False, "error": f"未知工具：{name}"}
    _tok = _IFIND_GRADE.set(bool(ifind_user))
    try:
        data = await tool.handler(**(arguments or {}))
    except TypeError as exc:
        return {"ok": False, "error": f"参数不合法：{exc}"}
    except Exception as exc:  # noqa: BLE001 - 工具失败不能拖垮 agent 循环
        return {"ok": False, "error": f"取数失败：{exc}"}
    finally:
        _IFIND_GRADE.reset(_tok)  # 任何 return/异常都复位，灰度位绝不残留
    if data is None or (isinstance(data, (list, dict)) and not data):
        return {"ok": True, "data": None, "note": "该数据源对此标的暂无可用数据（已优雅降级）。"}
    return {"ok": True, "data": data}


# --- 参数 schema 复用 -------------------------------------------------------
_SYMBOL_MARKET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "symbol": {"type": "string", "description": "股票代码，如 AAPL、600519、00700"},
        "market": {
            "type": "string",
            "enum": ["US", "CN", "HK"],
            "description": "市场；缺省时按代码自动推断",
        },
    },
    "required": ["symbol"],
}


# --- 工具 handler（包装真实数据函数，归一化成 JSON-able dict）---------------
async def _tool_get_market_quote(symbol: str, market: Optional[str] = None) -> Any:
    # 灰度态(白名单)下复用 Surface B 的 iFinD 增强：A股走同花顺实时，其它/非灰度走原链。
    resp = await fetch_market_quotes([symbol], ifind_user=_IFIND_GRADE.get())
    quotes = [q.model_dump() for q in resp.quotes]
    return {"quotes": quotes, "provider": resp.provider, "warnings": resp.warnings[:3]}


async def _tool_get_financials(symbol: str, market: Optional[str] = None) -> Any:
    return await fetch_eastmoney_earnings(symbol, market)


async def _tool_get_fund_flow(symbol: str, market: Optional[str] = None) -> Any:
    return await fetch_fund_flow(symbol, market)


async def _tool_get_valuation(symbol: str, market: Optional[str] = None) -> Any:
    # 灰度态 + A股：优先用 iFinD 实时 PE_TTM/PB/总市值（本土权威），失败/非A股回退原估值源。
    if _IFIND_GRADE.get():
        try:
            import asyncio
            from . import ifind_api
            code = ifind_api.normalize_a_code(symbol)
            if code and ifind_api.enabled():
                res = await asyncio.wait_for(asyncio.to_thread(ifind_api.real_time_quote, code), timeout=6.0)
                row = (res.get("rows") or [None])[0] if (res and res.get("ok")) else None
                if row and any(row.get(k) is not None for k in ("pe_ttm", "pb", "totalCapital")):
                    return {"pe_ratio": row.get("pe_ttm"), "pb_ratio": row.get("pb"),
                            "market_cap": row.get("totalCapital"), "provider": "ifind",
                            "source": "同花顺 iFinD 实时"}
        except Exception:  # noqa: BLE001 —— iFinD 任何异常 → 回退原估值源
            pass
    return await fetch_valuation(symbol, market)


async def _tool_get_analyst_consensus(symbol: str, market: Optional[str] = None) -> Any:
    return await fetch_analyst_consensus(symbol, market)


register_tool(AgentTool(
    name="get_market_quote",
    description="获取个股最新行情（现价/涨跌幅/成交量/52周高低，多源回退）。美/A/港股通用。",
    parameters=_SYMBOL_MARKET_SCHEMA,
    handler=_tool_get_market_quote,
))
register_tool(AgentTool(
    name="get_financials",
    description="获取最新季报盈利质量：净利/营收同比、EPS、ROE。仅 A股/港股有覆盖。",
    parameters=_SYMBOL_MARKET_SCHEMA,
    handler=_tool_get_financials,
))
register_tool(AgentTool(
    name="get_fund_flow",
    description="获取 A股主力资金净流入（当日/多日）。仅 A股有覆盖。",
    parameters=_SYMBOL_MARKET_SCHEMA,
    handler=_tool_get_fund_flow,
))
register_tool(AgentTool(
    name="get_valuation",
    description="获取市值与估值（市值/PE/PB/PS、远期PE、股息率、beta）。美/A/港股通用。",
    parameters=_SYMBOL_MARKET_SCHEMA,
    handler=_tool_get_valuation,
))
register_tool(AgentTool(
    name="get_analyst_consensus",
    description="获取卖方一致预期：目标价、较现价空间、评级共识。仅美股有覆盖。",
    parameters=_SYMBOL_MARKET_SCHEMA,
    handler=_tool_get_analyst_consensus,
))


_BULL_STAGE_CN = {"startup": "初创期", "growth": "成长期", "mature": "成熟期",
                  "decline": "衰退期", "transition": "过渡期", "unknown": "—"}


async def _tool_get_financial_statements(symbol: str, market: Optional[str] = None) -> Any:
    """三张报表关键项 + 现金流八类型：经营/投资/筹资活动现金流量净额、capex、自由现金流、含金量、扣非、毛利率。
    多源回退(iFinD 优先、东财兜底)。仅 A股覆盖。"""
    s = await financial_statements.fetch_statements(symbol, market)
    if not s:
        return None
    cf = s.get("cashflow_type") or {}
    return {
        "symbol": symbol, "report_date": s.get("report_date"), "source": s.get("provider"),
        "cashflow": {"经营": s.get("ocf"), "投资": s.get("icf"), "筹资": s.get("fcf_financing"),
                     "pattern": cf.get("pattern"), "type": cf.get("type"), "type_desc": cf.get("desc"),
                     "life_stage": cf.get("life")},
        "capex": s.get("capex"), "free_cash_flow": s.get("fcf"),
        "revenue": s.get("revenue"), "net_income": s.get("net_income"),
        "gross_margin": s.get("gross_margin"), "roe": s.get("roe"),
        "accounts_receivable": s.get("accounts_receivable"), "advance_receipts": s.get("advance_receipts"),
        "earnings_quality": s.get("earnings_quality"), "good_business": s.get("good_business"),
    }


async def _tool_assess_long_term_bull(symbol: str, market: Optional[str] = None) -> Any:
    """《价值投资之长线牛股》方法论体检：ROE生命周期 + 投资对象五型 + 真实估值 + 现金流八类型/含金量 + 催化剂 → 牛股基因。
    只用平台 ground-truth(三表/估值/本站内容，多源回退)，缺项诚实标注，绝不臆造数字。"""
    fin = await fetch_eastmoney_earnings(symbol, market) or {}
    val = await _tool_get_valuation(symbol, market) or {}
    stmt = await financial_statements.fetch_statements(symbol, market) or {}
    roe = safe_float(fin.get("roe")) if fin.get("roe") is not None else stmt.get("roe")
    rev = safe_float(fin.get("revenue_yoy")) if fin.get("revenue_yoy") is not None else stmt.get("revenue_yoy")
    pf = safe_float(fin.get("profit_yoy")) if fin.get("profit_yoy") is not None else stmt.get("profit_yoy")
    pe = safe_float(val.get("pe_ratio")); pb = safe_float(val.get("pb_ratio")); peg = safe_float(val.get("peg"))

    rs = bull_playbook.roe_stage(roe=roe, revenue_yoy=rev, profit_yoy=pf)              # 第4.7 ROE 生命周期
    target = bull_playbook.infer_target_type(revenue_yoy=rev, profit_yoy=pf, roe=roe)  # 第5章 投资对象类型
    # 现金流八类型 + 自由现金流 + 盈利质量含金量 + 好生意(全维，来自三表)——之前缺数据的'半成品'现已跑通
    cft = stmt.get("cashflow_type") or {}
    eq = stmt.get("earnings_quality") or {}
    gb = stmt.get("good_business") or bull_playbook.good_business(roe=roe)             # 第1.6 一门好生意
    # 催化剂：扫本站近期与该标的相关内容，按第2章业绩增长关键字分类
    cat_titles: list[str] = []
    try:
        for m in list_realtime_messages(anyq=symbol, limit=20):
            t = getattr(m, "title", "") or ""
            if t:
                cat_titles.append(t)
    except Exception:  # noqa: BLE001
        pass
    cat = bull_playbook.catalyst_profile(cat_titles)
    cat_strength = cat["top"]["strength"] if (cat.get("top") and cat["top"].get("dir", 0) > 0) else None
    gene = bull_playbook.bull_gene_score(good_biz=gb.get("score"), catalyst_strength=cat_strength,
                                         earnings_quality_score=eq.get("score"),
                                         cashflow_score=cft.get("score"))             # 第3.7 牛股基因

    # 真实估值评注(第4章：PE 高低看未来业绩，PB 受盈利干扰更小；PEG<1 偏低估)
    vnotes = []
    if pe is not None:
        vnotes.append(f"PE {pe:.0f}（{'偏高·需未来高增长消化' if pe >= 45 else '中性' if pe >= 20 else '不贵'}）")
    if pb is not None:
        vnotes.append(f"PB {pb:.2f}")
    if peg is not None:
        vnotes.append(f"PEG {peg:.2f}（{'<1 成长性消化估值' if peg < 1 else '>1 估值已含成长'}）")

    gaps = []
    if roe is None and pf is None:
        gaps.append("无财报数据（盈利质量/ROE 阶段无法判定）")
    if pe is None and pb is None:
        gaps.append("无估值数据")
    if not stmt:
        gaps.append("无三表数据（现金流八类型/含金量无法判定）")
    if not cat_titles:
        gaps.append("本站近期无相关催化剂")
    gaps.append("护城河/进化力/净资产真实性等仍需人工研判，未量化")

    return {
        "symbol": symbol,
        "roe_stage": {"stage": _BULL_STAGE_CN.get(rs.get("stage"), "—"),
                      "fair_multiple": rs.get("fair_multiple"), "note": rs.get("note"),
                      "roe": roe, "revenue_yoy": rev, "profit_yoy": pf},
        "target_type": {"label": target.get("label"), "confidence": target.get("confidence"),
                        "focus": target.get("focus"), "payoff": target.get("payoff"),
                        "sizing": target.get("sizing"), "old_stage": target.get("old_stage"),
                        "new_stage": target.get("new_stage")},
        "cashflow": ({"type": cft.get("type"), "pattern": cft.get("pattern"), "risk": cft.get("risk"),
                      "desc": cft.get("desc"), "life": cft.get("life"),
                      "free_cash_flow": stmt.get("fcf"), "source": stmt.get("provider"),
                      "report_date": stmt.get("report_date")} if stmt else None),
        "earnings_quality": eq or None,
        "good_business": gb,
        "valuation": {"pe": pe, "pb": pb, "peg": peg, "notes": vnotes},
        "catalysts": {"net_dir": cat.get("net_dir"), "bull_types": cat.get("bull_types"),
                      "top": (cat["top"]["label"] if cat.get("top") else None), "count": cat.get("n")},
        "bull_gene": gene,
        "data_gaps": gaps,
        "methodology": "《价值投资之长线牛股》：好生意三标准 + 业绩增长关键字 + 护城河/进化力 + ROE生命周期 + "
                       "投资对象五型 + 现金流八类。本工具覆盖 ROE阶段/投资类型/估值/现金流八类型·含金量/催化剂。",
        "disclaimer": "方法论演示，使用公开数据，不构成投资建议；历史/财务不代表未来收益。",
    }


register_tool(AgentTool(
    name="get_financial_statements",
    description="获取个股三张报表关键项 + 现金流八类型：经营/投资/筹资活动现金流量净额、capex、自由现金流、"
                "利润含金量、扣非、毛利率、ROE。需要现金流结构/盈利质量/是否现金奶牛时用。多源回退(iFinD优先东财兜底)，仅 A股。",
    parameters=_SYMBOL_MARKET_SCHEMA,
    handler=_tool_get_financial_statements,
))
register_tool(AgentTool(
    name="assess_long_term_bull",
    description="用《价值投资之长线牛股》方法论给个股做长线体检：ROE 生命周期阶段 + 投资对象五型分类 + "
                "真实估值(PE/PB/PEG) + 现金流八类型/利润含金量 + 本站催化剂分类 + 牛股基因综合分。"
                "判断'是不是一只值得长持的牛股'时用；A股覆盖最全，缺数据会诚实标注 data_gaps。",
    parameters=_SYMBOL_MARKET_SCHEMA,
    handler=_tool_assess_long_term_bull,
))


async def _tool_search_our_content(query: str = "", days: int = 7, limit: int = 12) -> Any:
    """检索/汇总「我们网站」近期发布的快讯/文章（关键词匹配标题或正文；**query 留空=按时间取近期最新全部**，适合「近期快讯总结」）。
    文章附带我们已缓存的 AI 解读（命中预解读缓存即返回 ai_summary，不重算）。用于判断我们是否提前覆盖某主线/驱动，可多次换词深挖。"""
    import hashlib as _hashlib
    from .metrics_store import get_ai_cache
    since = (_dt.now(_tz.utc) - _td(days=max(1, min(int(days or 7), 30)))).strftime("%Y-%m-%dT%H:%M:%S")
    # 多词 OR 召回（anyq 按逗号分词→任一词命中标题或正文）；**留空→不加关键词，按 since 取近期最新**。
    terms = [t for t in _re.split(r"[\s,，、/|]+", str(query or "")) if len(t) >= 2][:8]
    anyq = ",".join(terms) if terms else ""
    try:
        # limit 上限放宽到 60，便于「近 24 小时全覆盖」式总结取全近期快讯。
        msgs = list_realtime_messages(anyq=anyq, since=since, limit=max(1, min(int(limit or 20), 60)))
    except Exception:
        return []
    _TONE = {"warning": "风险/利空", "success": "利好", "info": "中性"}
    out: list[dict[str, Any]] = []
    for m in msgs:
        topic = getattr(m, "topic", "") or ""
        if topic not in ("快讯", "文章", "研报"):
            continue
        title = (getattr(m, "title", "") or "")[:80]
        content = _re.sub(r"https?://\S+", "", getattr(m, "content", "") or "").strip()
        sev = getattr(m, "severity", "") or ""
        item: dict[str, Any] = {
            "title": title,
            "topic": topic,
            "snippet": content[:110] if content and content != title else "",
            "published_at": getattr(m, "created_at", ""),
            "tone": _TONE.get(sev, "中性"),  # 情绪/方向信号(利好/利空/中性)，供模型挑重点+归类，非重要性硬筛
        }
        if topic == "文章":  # 复用已缓存的文章 AI 解读（键与 news-prewarm 一致：news:sha1(title\ncontent)[:20]）
            _t = (getattr(m, "title", "") or "").strip()
            _c = (getattr(m, "content", "") or "").strip()
            if len(_t + _c) >= 60:
                cached = get_ai_cache("news:" + _hashlib.sha1(f"{_t}\n{_c}".encode("utf-8")).hexdigest()[:20])
                if cached:
                    item["ai_summary"] = (cached.get("summary") or cached.get("one_liner") or "")[:300]
        out.append(item)
    return out


register_tool(AgentTool(
    name="search_our_content",
    description=(
        "检索/汇总本平台近期发布的快讯/文章（返回 标题/类型/摘要/发布时间/tone；tone=利好/利空/中性，供你挑重点和归类；文章附带已缓存 AI 解读 ai_summary）。"
        "**query 留空 = 按时间取近期最新全部**，适合『近期快讯总结』；做『近24小时总结』建议 days=1、limit=40~60 取全近期再提炼；带关键词则核实『我们是否提前覆盖过某主线/催化』。"
        "研报请用 get_recent_research。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "检索关键词，如『黄金 央行 增持』『固态电池』；**留空=取近期最新全部**"},
            "days": {"type": "integer", "description": "回溯天数，默认 7"},
            "limit": {"type": "integer", "description": "最多返回条数，默认 12（上限 20）"},
        },
    },
    handler=_tool_search_our_content,
))


async def _tool_get_stock_research(symbol: str, market: Optional[str] = None, limit: int = 10) -> Any:
    """检索本平台「研报」面板可见的券商研报（东方财富研报库，按个股代码，覆盖近 2 年；仅 A股/港股，美股无）。
    返回最近若干篇的标题/机构/评级/日期，用于核实卖方机构对该股的覆盖与观点——这是『我们网站能查到的研报』的主力
    来源，比 search_our_content（只搜快讯/文章库）覆盖得更全。"""
    from .eastmoney_reports import query_eastmoney_reports
    code = (symbol or "").strip()
    if not code:
        return {"ok": False, "data": None, "error": "缺少标的代码"}
    n = max(1, min(int(limit or 10), 20))
    try:
        rows, warnings = await query_eastmoney_reports(code=code, market=market, page_size=n)
    except Exception as exc:  # noqa: BLE001 —— 数据源失败优雅降级
        return {"ok": False, "data": None, "error": str(exc)[:120]}
    reports = [
        {"title": r.get("title"), "org": r.get("org"), "rating": r.get("rating"), "date": r.get("date")}
        for r in rows[:n]
    ]
    if not reports:
        return {"ok": False, "data": None, "error": (warnings[0] if warnings else "东财暂无该标的研报")}
    return {"ok": True, "data": reports}


register_tool(AgentTool(
    name="get_stock_research",
    description=(
        "检索券商研报（东方财富研报库，按个股代码，覆盖近 2 年，返回标题/机构/评级/日期）。"
        "核实卖方机构对该股的覆盖与最新观点——『我们网站能查到的研报』主力来源。仅 A股/港股有覆盖，美股无。"
    ),
    parameters=_SYMBOL_MARKET_SCHEMA,
    handler=_tool_get_stock_research,
))


async def _tool_get_daily_review(date: Optional[str] = None) -> Any:
    """本平台最新一期 A股收盘复盘（大盘/板块/资金主线 + 我们提前发现的资讯对照）。这是平台自有的差异化内容。"""
    from .ashare_review import latest_review, review_for_date
    rv = review_for_date(date) if (date or "").strip() else latest_review()
    if not rv:
        return None
    nar = rv.get("narrative") or {}
    return {
        "date": rv.get("date"), "session": rv.get("session_label") or rv.get("session"),
        "one_liner": nar.get("one_liner", ""),
        "market": nar.get("market", ""), "sectors": nar.get("sectors", ""),
        "funds": nar.get("funds", ""), "tomorrow": nar.get("tomorrow", ""),
        "our_edge": [e.get("title") or e.get("name") for e in (rv.get("our_edge") or [])[:8] if isinstance(e, dict)],
    }


register_tool(AgentTool(
    name="get_daily_review",
    description=(
        "获取本平台最新（或指定日期）的 A股收盘复盘：大盘/板块/资金主线的买方综述，以及"
        "『我们提前发现的资讯』与盘面的对照。回答大盘行情、板块轮动、今日盘面时优先引用本工具（平台自有内容）。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "YYYY-MM-DD，缺省取最新一期"},
        },
    },
    handler=_tool_get_daily_review,
))


async def _tool_get_recent_research(query: str = "", limit: int = 8) -> Any:
    """汇总本平台「海外投行研报」近期条目 + 我们已缓存的 AI 解读（命中预解读缓存即返回 ai_summary，绝不重算/不下载）。
    query 留空=最新一批，带关键词=检索某主题。用于『近期研报总结』『某主题研报怎么看』。"""
    from .research_wire import fetch_research_wire_online
    from .metrics_store import get_ai_cache_many
    cap = max(1, min(int(limit or 8), 20))
    try:
        online = await fetch_research_wire_online(limit=min(cap * 2, 40), query=str(query or ""))
    except Exception:
        return []
    items = (online or {}).get("items") or []
    fids = [str(r.get("file_id") or "").strip() for r in items if r.get("file_id")]
    cache_map = get_ai_cache_many(fids) if fids else {}
    out: list[dict[str, Any]] = []
    for r in items[:cap]:
        cached = cache_map.get(str(r.get("file_id") or "").strip()) or {}
        out.append({
            "title": (r.get("title") or "")[:90],
            "org": r.get("org") or "",
            "date": r.get("date") or r.get("created_at") or "",
            "ai_summary": ((cached.get("summary") or cached.get("one_liner") or "")[:300]) or None,
        })
    return out


register_tool(AgentTool(
    name="get_recent_research",
    description=(
        "汇总本平台『海外投行研报』近期条目及我们已缓存的 AI 解读（ai_summary，命中预解读缓存即返回，不重算/不下载）。"
        "query 留空=最新一批，带关键词=检索某主题。回答『近期研报』『某主题研报观点』时用本工具。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "研报检索关键词；留空=最新一批"},
            "limit": {"type": "integer", "description": "返回条数，默认 8（上限 20）"},
        },
    },
    handler=_tool_get_recent_research,
))


# ============ 平台自有数据打通：AI 模拟盘 / 热度榜 / 焦点人物 ============
# 都是平台已上线、成熟的差异化数据，此前 agent 够不到。包成工具让模型自主调用。
# 纪律：handler 层做精简投影（只回关键字段、控 token），失败收敛成 None/优雅降级。

async def _tool_get_ai_fund_snapshot(strategy: str = "") -> Any:
    """DeepFocus『AI 模拟盘』某流派智能体的战绩卡（持仓/收益/超额/操盘观点）。读本地 SQLite，确定性数据，非建议。"""
    from . import ai_fund
    fid = (strategy or "").strip() or ai_fund.FUND_ID
    snap = await asyncio.to_thread(ai_fund.get_snapshot, fid)
    if not snap:
        return None
    persona = snap.get("persona") or {}
    strat = snap.get("strategy") or {}
    stats = snap.get("stats") or {}
    positions = [
        {"name": p.get("name"), "symbol": p.get("symbol"), "pnl_pct": p.get("pnl_pct"),
         "weight": p.get("weight"), "float_pnl": p.get("float_pnl")}
        for p in (snap.get("positions") or [])[:10]
    ]
    recent = [
        {"side": t.get("side"), "name": t.get("name"), "price": t.get("price"),
         "pnl_pct": t.get("pnl_pct"), "catalyst": t.get("catalyst"), "ts": t.get("ts")}
        for t in (snap.get("trades") or [])[:6]
    ]
    return {
        "fund": persona.get("name"), "style": persona.get("style"), "tag": persona.get("tag"),
        "strategy_model": strat.get("model_label"), "market_stance": strat.get("market_stance"),
        "nav_pct": snap.get("nav_pct"), "alpha_pct": snap.get("alpha_pct"),
        "benchmark_name": snap.get("benchmark_name"), "win_rate": stats.get("win_rate"),
        "cash": snap.get("cash"), "position_count": snap.get("position_count"),
        "max_positions": snap.get("max_positions"), "days_running": snap.get("days_running"),
        "mood": snap.get("mood"), "commentary": snap.get("commentary"),
        "positions": positions, "recent_trades": recent,
        "data_quality": (snap.get("data_quality") or {}).get("label"),
        "disclaimer": "AI 模拟盘为投研演示，虚拟资金、不接券商、不构成投资建议。",
    }


async def _tool_get_ai_fund_arena() -> Any:
    """DeepFocus『AI 模拟盘』五流派竞技场排行（均衡/激进/价值/事件/逆向 × 沪深300基准）。读本地 SQLite，非建议。"""
    from . import ai_fund
    arena = await asyncio.to_thread(ai_fund.get_arena)
    if not arena:
        return None
    strategies = [
        {"rank": s.get("rank"), "name": s.get("name"), "style": s.get("style"),
         "nav_pct": s.get("nav_pct"), "alpha_pct": s.get("alpha_pct"), "win_rate": s.get("win_rate"),
         "max_drawdown_pct": s.get("max_drawdown_pct"), "position_count": s.get("position_count"),
         "model": s.get("model_label"), "last_action": s.get("last_action")}
        for s in (arena.get("strategies") or [])
    ]
    return {
        "champion": arena.get("champion"), "spread": arena.get("spread"),
        "benchmark": arena.get("benchmark"),
        "consensus": arena.get("consensus"), "divergence": arena.get("divergence"),
        "strategies": strategies,
        "disclaimer": arena.get("disclaimer"),
    }


async def _tool_get_hot_stocks(kind: str = "verdict", days: int = 14, limit: int = 10) -> Any:
    """近期站内被研判/关注最多的标的（『大家也在看』热度榜，按站内研判次数排序，非涨幅榜、非建议）。"""
    from . import data_store
    rows = await asyncio.to_thread(
        data_store.hot_symbols, (kind or "verdict"),
        days=float(days or 14), limit=max(1, min(int(limit or 10), 20)),
    )
    # 只回排名,不回 count(站内研判次数属运营数据,不外泄)
    return {"window_days": int(days or 14),
            "hot": [{"symbol": r.get("symbol"), "market": r.get("market"), "rank": i + 1}
                    for i, r in enumerate(rows)]}


register_tool(AgentTool(
    name="get_ai_fund_snapshot",
    description=(
        "查看 DeepFocus『AI 模拟盘』某流派智能体的当前持仓、累计收益、超额(alpha)、操盘风格与最新动作。"
        "用户问『AI 模拟盘现在持什么仓/赚不赚钱/阿尔法怎么操作/某流派表现』时用。"
        "strategy 可选：main(阿尔法·均衡)/mammoth(猛犸·激进)/rock(磐石·价值)/falcon(游隼·事件)/contra(磁极·逆向)，缺省=主账户 main。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "strategy": {"type": "string", "enum": ["main", "mammoth", "rock", "falcon", "contra"],
                         "description": "流派 fund_id，缺省 main(阿尔法)"},
        },
    },
    handler=_tool_get_ai_fund_snapshot,
))
register_tool(AgentTool(
    name="get_ai_fund_arena",
    description=(
        "查看 DeepFocus『AI 模拟盘』五流派竞技场排行榜（均衡/激进/价值/事件/逆向同场赛马，含冠军、收益差、"
        "各自超额沪深300、胜率、最大回撤、最新动作）。用户问『哪个流派最强/AI 模拟盘排行/竞技场战况』时用。"
    ),
    parameters={"type": "object", "properties": {}},
    handler=_tool_get_ai_fund_arena,
))
register_tool(AgentTool(
    name="get_hot_stocks",
    description=(
        "查看近期站内被研判/关注最多的标的（『大家也在看』热度榜，按站内研判次数排序，非涨幅榜）。"
        "用户问『最近大家都在看哪些票/近期热门标的』时用。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "kind": {"type": "string", "description": "热度口径，缺省 verdict(研判)"},
            "days": {"type": "integer", "description": "回溯天数，缺省 14"},
            "limit": {"type": "integer", "description": "返回条数，缺省 10（上限 20）"},
        },
    },
    handler=_tool_get_hot_stocks,
))
