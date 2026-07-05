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
# 当前提问用户名(微信绑定/网页登录用户)：入口处 set，供 get_my_watchlist 读取"本人自选股"。
# 同样走 ContextVar 而非工具参数 → 模型无法在 args 里伪造别人用户名越权读他人自选。
_BINDING_USER: ContextVar[str] = ContextVar("binding_user", default="")

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


# 白名单专属工具：只对白名单用户出现在工具清单里，其他用户连工具名/描述都看不到（防灰度内容如名人观点被套问泄露）。
_WHITELIST_ONLY_TOOLS = {"get_celebrity_views"}


def _binding_whitelisted() -> bool:
    """当前提问用户(经 _BINDING_USER 注入，如微信绑定用户)是否在白名单(复用 iFinD 白名单，默认 lx199710)。"""
    try:
        from . import ifind_api
        u = (_BINDING_USER.get() or "").strip().lower()
        return bool(u) and u in ifind_api.allowed_usernames()
    except Exception:  # noqa: BLE001
        return False


def openai_tool_specs(extra_tools: dict[str, "AgentTool"] | None = None, whitelist_user: bool = False) -> list[dict[str, Any]]:
    """供 chat.completions.create(tools=...) 使用的工具清单。

    extra_tools：本次运行的动态工具（如发现到的外部 MCP 工具），与静态注册表合并。
    whitelist_user：本次是否白名单用户（终端 ifind_user / 微信 _BINDING_USER 命中）；非白名单则隐藏白名单专属工具。
    """
    wl = whitelist_user or _binding_whitelisted()
    specs = [tool.openai_spec() for tool in TOOL_REGISTRY.values()
             if wl or tool.name not in _WHITELIST_ONLY_TOOLS]
    if extra_tools:
        specs.extend(tool.openai_spec() for tool in extra_tools.values())
    return specs


def _coerce_symbol_arg(arguments: dict[str, Any] | None) -> dict[str, Any] | None:
    """工具入参里若 symbol 是中文名(或裹着名字的短语)→ 自动解析成 A 股代码再下发；
    已是代码/美港股 ticker/解析不出 → 原样不动。根除模型凭记忆猜错 A 股代码导致取数全错。"""
    if not isinstance(arguments, dict):
        return arguments
    sym = arguments.get("symbol")
    if isinstance(sym, str) and sym.strip():
        try:
            from .stock_name_index import resolve_to_code
            code = resolve_to_code(sym)
            if code and code != sym.strip():
                arguments = {**arguments, "symbol": code}
        except Exception:  # noqa: BLE001 —— 解析失败不影响原调用
            pass
    # 复数 symbols(逗号分隔，如 get_stock_comparison)：同样把每个中文名归一成代码，否则横向对比整表降级
    syms = arguments.get("symbols")
    if isinstance(syms, str) and syms.strip():
        try:
            from .stock_name_index import resolve_to_code
            parts = [p.strip() for p in _re.split(r"[,，、\s]+", syms) if p.strip()]
            coerced = [(resolve_to_code(p) or p) for p in parts]
            if parts and coerced != parts:
                arguments = {**arguments, "symbols": ",".join(coerced)}
        except Exception:  # noqa: BLE001
            pass
    return arguments


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
    arguments = _coerce_symbol_arg(arguments)  # 中文名→A股代码自动归一（模型猜错代码的兜底）
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
    mk = (market or "").upper()
    is_us = mk == "US" or (not mk and str(symbol).strip().isalpha())
    if not is_us:  # A股/港股：东财机构评级/平均目标价(补齐"仅美股"的本土缺口；注意目标价币种)
        try:
            from .cn_consensus import fetch_cn_consensus
            res = await fetch_cn_consensus(symbol, market)
            if res:
                return res
        except Exception:  # noqa: BLE001
            pass
    return await fetch_analyst_consensus(symbol, market)


async def _tool_get_stock_news(symbol: str, market: Optional[str] = None, limit: int = 8) -> Any:
    from .stock_news import fetch_stock_news
    return await fetch_stock_news(symbol, market, limit=min(max(int(limit or 8), 1), 30))


async def _tool_get_dividend_history(symbol: str, market: Optional[str] = None, limit: int = 6) -> Any:
    from .dividend_history import fetch_dividend_history
    return await fetch_dividend_history(symbol, market, limit=min(max(int(limit or 6), 1), 40))


async def _tool_get_dragon_tiger(symbol: str, market: Optional[str] = None) -> Any:
    from .dragon_tiger import fetch_dragon_tiger
    return await fetch_dragon_tiger(symbol, market)


async def _tool_get_my_watchlist() -> Any:
    """读取【当前提问用户本人】的自选股(用户名经入口 ContextVar 注入)；未登录/未绑定/为空 → 如实提示。"""
    user = (_BINDING_USER.get() or "").strip()
    if not user:
        return {"note": "需要登录/绑定后才能查看你的自选股"}
    try:
        from . import user_prefs
        # ⭐ContextVar 注入的是【用户名】，而 user_prefs 按【user.id】键控(JWT sub=user.id)——
        # 必须先解析 username→id；此前直接拿用户名查恒为空，该工具对所有人都答"你还没有添加自选股"。
        uid: Optional[str] = None
        try:
            from . import auth as _auth
            uid = _auth.user_id_of_username(user)
        except Exception:  # noqa: BLE001
            uid = None
        wl = user_prefs.get_watchlist(uid or user) or {}
    except Exception:  # noqa: BLE001
        wl = {}
    syms = wl.get("symbols") or []
    names = wl.get("names") or {}
    if not syms:
        return {"watchlist": [], "note": "你还没有添加自选股"}
    return {"watchlist": [{"symbol": s, "name": names.get(s, "")} for s in syms[:50]], "count": len(syms)}


async def _tool_get_stock_announcements(symbol: str, market: Optional[str] = None, days: int = 30) -> Any:
    from .eastmoney_announcements import fetch_stock_announcements
    return await fetch_stock_announcements(symbol, days=min(max(int(days or 30), 1), 90))


async def _tool_get_next_disclosure(symbol: str, market: Optional[str] = None) -> Any:
    from .eastmoney_announcements import fetch_next_disclosure
    return await fetch_next_disclosure(symbol)


async def _tool_get_cn_calendar(days: int = 7) -> Any:
    from .cn_calendar import fetch_cn_calendar
    return await fetch_cn_calendar(days=min(max(int(days or 7), 1), 30))


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
    description="获取市值与估值（总市值/流通市值、市盈率 TTM 即 pe_ratio 与动态即 pe_dynamic 双口径、PB/PS/PEG、美股远期PE/股息率/beta）。"
                "A/港股的市值/PB/动态PE取自行情页实时口径，与用户在东财/同花顺看到的一致；market_cap 单位为元、另附 market_cap_yi(亿元)直接展示。美/A/港股通用。",
    parameters=_SYMBOL_MARKET_SCHEMA,
    handler=_tool_get_valuation,
))
register_tool(AgentTool(
    name="get_analyst_consensus",
    description="获取卖方一致预期：目标价、较现价空间、评级共识(买入/增持家数)。美股+A股+港股均覆盖(A/港股取东财机构评级与平均目标价，注意目标价币种)。",
    parameters=_SYMBOL_MARKET_SCHEMA,
    handler=_tool_get_analyst_consensus,
))
register_tool(AgentTool(
    name="get_stock_news",
    description="获取某只股票最近的新闻/资讯(标题+日期+摘要+来源+链接，时间倒序)。回答『X股最近发生了什么/有什么消息/最新动态』时调用。日期与数字均为真实资讯、须直接引用不得凭记忆杜撰时间或金额；无数据返回空。",
    parameters={"type": "object", "properties": {
        "symbol": {"type": "string", "description": "股票代码(中文名会自动归一)，如 600519/00700"},
        "limit": {"type": "integer", "description": "返回条数，默认 8(上限 30)"},
    }, "required": ["symbol"]},
    handler=_tool_get_stock_news,
))
register_tool(AgentTool(
    name="get_dividend_history",
    description="查询A股个股最近几期分红送转(每10股送X转Y派Z元、股息率、股权登记日/除权除息日、分红进度)。问『XX分红多少/股息率/派息/送转/除权日』时调用；返回东财F10官方方案，防编造分红数字。仅A股。",
    parameters={"type": "object", "properties": {
        "symbol": {"type": "string", "description": "A股代码(中文名会自动归一)，如 600519"},
        "limit": {"type": "integer", "description": "返回最近几期，默认 6(上限 40)"},
    }, "required": ["symbol"]},
    handler=_tool_get_dividend_history,
))
register_tool(AgentTool(
    name="get_dragon_tiger",
    description="查询某只A股最近一次龙虎榜上榜的买卖席位明细(游资/机构席位、买卖top营业部、买卖净额、上榜原因)。问『某股近期龙虎榜/游资席位/谁在买谁在卖』时调用；只取近30天最近一次，无则空。仅A股。",
    parameters=_SYMBOL_MARKET_SCHEMA,
    handler=_tool_get_dragon_tiger,
))
register_tool(AgentTool(
    name="get_my_watchlist",
    description="读取【当前用户本人】的自选股列表(代码+名称)。用户说『我的自选股/我关注的票/我的持仓/帮我看看我自选里的票』时调用，再据此逐只取数分析。未登录/未绑定或为空会如实提示。",
    parameters={"type": "object", "properties": {}},
    handler=_tool_get_my_watchlist,
))
register_tool(AgentTool(
    name="get_stock_announcements",
    description="查询某只A股近30天的交易所公告清单(标题/类型/日期/链接)。问『XX最近有什么公告/有没有回购/增发/减持/重组』时调用。纯交易所公开事实。仅A股。",
    parameters={"type": "object", "properties": {
        "symbol": {"type": "string", "description": "A股6位代码，如 600519"},
        "days": {"type": "integer", "description": "回看天数，默认30，最大90"},
    }, "required": ["symbol"]},
    handler=_tool_get_stock_announcements,
))
register_tool(AgentTool(
    name="get_next_disclosure",
    description="查询某只A股的财报预约披露日期(年报/季报什么时候出，含已披露的实际日期)。问『XX什么时候出财报/年报/中报』时调用。确定性日期事实。仅A股。",
    parameters={"type": "object", "properties": {
        "symbol": {"type": "string", "description": "A股6位代码，如 600519"},
    }, "required": ["symbol"]},
    handler=_tool_get_next_disclosure,
))
register_tool(AgentTool(
    name="get_cn_calendar",
    description="查询A股未来N天的市场日历：限售解禁(哪些股解禁/解禁市值)、新股申购与上市、财报预约披露。"
                "问『今天/明天/本周/近期有什么解禁/新股/打新/哪些公司出财报』这类**全市场日历**问题时调用"
                "(个股单只的披露日期用 get_next_disclosure)。交易所公开确定性日期事实。",
    parameters={"type": "object", "properties": {
        "days": {"type": "integer", "description": "向后看的天数，默认7，最大30"},
    }},
    handler=_tool_get_cn_calendar,
))


async def _tool_resolve_symbol(query: str = "") -> Any:
    """把股票【中文名称/片段】解析成标准【A 股代码】候选（精确→前缀→子串）。"""
    from . import stock_name_index
    cands = stock_name_index.search_names(query, limit=8)
    if not cands:
        return {"matches": [], "note": f"未找到与「{query}」匹配的 A 股标的；若是港股/美股请直接用代码(如 00700/AAPL)"}
    return {"matches": cands}


register_tool(AgentTool(
    name="resolve_symbol",
    description=(
        "把股票【中文名称】解析成标准【代码】(A 股)。当用户用中文名提到某只 A 股(如『长电科技』『亿纬锂能』『概伦电子』)、"
        "或多轮对话里用『他/它/这只/这个票』指代某只股时，**先调本工具拿到准确代码，再去调行情/估值/财报等数据工具**——"
        "绝不要凭记忆猜 A 股代码(猜错=取数全错)。返回候选 {name,code,market}，多个时按相关度排序、挑最贴切的；"
        "为空说明没匹配到。港股/美股可直接用代码(00700/AAPL)，无需本工具。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "股票中文名或片段，如『长电科技』『宁德』『概伦电子』"},
        },
        "required": ["query"],
    },
    handler=_tool_resolve_symbol,
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

    # 季报 ROE 是累计值；喂进生命周期阶段/投资类型阈值前先按报告期年化，否则 Q1/Q3 把成熟蓝筹误判成过渡/初创。展示仍用原始累计 roe。
    _rdate = str(fin.get("report_date") or stmt.get("report_date") or "")
    roe_for_stage = bull_playbook.annualize_ratio(roe, _rdate)
    rs = bull_playbook.roe_stage(roe=roe_for_stage, revenue_yoy=rev, profit_yoy=pf)    # 第4.7 ROE 生命周期(年化ROE判级)
    target = bull_playbook.infer_target_type(revenue_yoy=rev, profit_yoy=pf, roe=roe_for_stage)  # 第5章 投资对象类型
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
        # ⭐符号/增长闸门：负 PEG，或盈利负增长/亏损时算出的低 PEG，都无意义——绝不能贴"偏低估"(会给垃圾股虚假便宜信号)
        if peg <= 0 or (pf is not None and pf <= 0):
            _peg_note = "盈利负增长/亏损，PEG 不适用"
        elif peg < 1:
            _peg_note = "<1 偏低估，成长性消化估值"
        else:
            _peg_note = ">1 估值已含成长"
        vnotes.append(f"PEG {peg:.2f}（{_peg_note}）")

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


async def _tool_get_theme_stocks(theme: str = "", limit: int = 12) -> Any:
    """A股题材/行业「顺藤摸瓜找受益股」：题材名→概念/行业板块成分股；留空→当日概念板块涨幅榜。仅A股，确定性板块归属。"""
    from .theme_navigation import fetch_board_stocks, fetch_concept_boards, find_board_by_name

    n = max(1, min(int(limit or 12), 30))
    q = (theme or "").strip()
    if not q:
        boards = await fetch_concept_boards(limit=n)
        if not boards:
            return None
        return {"kind": "board_ranking", "boards": [
            {"name": b["name"], "pct": b["pct"], "leader": b["leader"], "leader_pct": b["leader_pct"]}
            for b in boards
        ]}
    board = await find_board_by_name(q)
    if not board:
        return {"kind": "not_found", "note": f"未找到「{q}」对应的题材/行业板块"}
    stocks = await fetch_board_stocks(board["code"], limit=n)
    if not stocks:
        return None
    return {"kind": "theme_stocks", "theme": board["name"], "stocks": [
        {"code": s["code"], "name": s["name"], "pct": s["pct"]} for s in stocks
    ]}


register_tool(AgentTool(
    name="get_theme_stocks",
    description=(
        "A股题材/行业「顺藤摸瓜找受益股」：给定题材或行业名（如『半导体』『白酒』『人工智能』『机器人』『算力』『光伏』），"
        "返回该概念/行业板块的成分股（按当日涨跌幅降序，含代码/名称/涨跌幅）。theme 留空=返回当日概念板块涨幅榜（看哪些主线在动）。"
        "仅 A股。用于回答『XX题材有哪些股/龙头』『今天什么板块涨得好』。**这是确定性的板块归属事实，不构成推荐或投资建议**。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "theme": {"type": "string", "description": "题材/行业名，如『半导体』『白酒』『光伏』；留空=返回概念板块涨幅榜"},
            "limit": {"type": "integer", "description": "最多返回条数，默认 12（上限 30）"},
        },
    },
    handler=_tool_get_theme_stocks,
))


async def _tool_get_limit_up_ladder(limit: int = 30) -> Any:
    """A股涨停天梯（连板梯队）：今日/最近交易日涨停股按连板数降序。纯事实，非荐股。"""
    from .theme_navigation import fetch_limit_up_ladder

    n = max(1, min(int(limit or 30), 60))
    d = await fetch_limit_up_ladder(limit=n)
    if not d.get("ladder"):
        return None
    return {"date": d["date"], "count": d["count"], "ladder": [
        {"name": s["name"], "code": s["code"], "boards": s["boards"], "days_ct": s["days_ct"], "industry": s["industry"]}
        for s in d["ladder"]
    ]}


register_tool(AgentTool(
    name="get_limit_up_ladder",
    description=(
        "A股涨停天梯/连板梯队：今日(或最近交易日)涨停股按连板数(几连板)降序，含 N天M板/所属行业/炸板次数。"
        "用于回答『今天有哪些连板股/几连板龙头』『最高几板』『涨停梯队/打板情绪』。仅 A股。**纯事实(谁涨停/几连板)，非荐股或投资建议**。"
    ),
    parameters={"type": "object", "properties": {
        "limit": {"type": "integer", "description": "最多返回条数，默认 30（上限 60）"}}},
    handler=_tool_get_limit_up_ladder,
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
        "核实卖方机构对该股的覆盖与最新观点——『我们网站能查到的研报』主力来源。**仅 A股有券商研报覆盖；港股/美股几乎查不到(东财研报库对港美股基本为空)，对港美股不要反复空跑本工具**。"
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


# ============ 大盘指数 / 商品 / 比特币 / 汇率 / 宏观利率实时行情 ============
# 个股工具够不到这些；过去问"黄金/原油/上证/比特币/美元"只能凭模型记忆答（常张口就来"历史新高"=幻觉）。
# 接入已缓存+预热的市场看板（fetch_market_dashboard 全球宏观 / fetch_ashare_dashboard A股），秒回真实价。
_MARKET_DATA_KEYS = [
    ("gold", "黄金(美元/盎司)"), ("oil", "WTI原油(美元/桶)"), ("bitcoin", "比特币(美元)"),
    ("spx", "标普500指数"), ("vix", "VIX恐慌指数"), ("dxy", "美元指数DXY"),
    ("ten_year", "美国10年期国债收益率(%)"),
    ("sse_composite", "上证指数"), ("csi300", "沪深300指数"), ("chinext", "创业板指"),
    ("usd_cny", "美元兑人民币"),
]


async def _tool_get_market_data(query: str = "") -> Any:
    """大盘指数/商品(黄金·原油)/比特币/汇率/宏观利率的【实时价格快照】。看板缓存+预热，秒回。
    只回 现价+今日涨跌(不带'历史新高'之类的模板论断)，让模型据实陈述、不臆造记录。"""
    from . import market_dashboard as md
    flat: dict[str, dict] = {}
    glob = ash = {}
    try:
        glob = await md.fetch_market_dashboard()
    except Exception:  # noqa: BLE001
        glob = {}
    try:
        ash = await md.fetch_ashare_dashboard()
    except Exception:  # noqa: BLE001
        ash = {}
    for dash in (glob, ash):
        for cat in (dash.get("categories") or []):
            for ind in (cat.get("indicators") or []):
                if ind.get("key"):
                    flat[ind["key"]] = ind
    items: list[dict[str, Any]] = []
    for key, label in _MARKET_DATA_KEYS:
        ind = flat.get(key)
        if not ind or ind.get("value") in (None, 0):
            continue
        items.append({"name": label, "price": ind.get("value"), "change_pct": ind.get("change_pct")})
    # 补充看板里没有但用户常问的品种：纳指/道指/恒生/白银（sina 直取，复用看板解析）
    try:
        from . import market_dashboard as _md
        raw = await _md._fetch_sina_quotes(["int_nasdaq", "int_dji", "rt_hkHSI", "hf_SI"])
        for code, label, kind in (("int_nasdaq", "纳斯达克综合指数", "index"),
                                   ("int_dji", "道琼斯工业指数", "index"),
                                   ("hf_SI", "白银(美元/盎司)", "futures")):
            f = raw.get(code)
            if not f:
                continue
            q = _md._parse_sina_fields(kind, f)
            if q.get("price"):
                items.append({"name": label, "price": q["price"], "change_pct": q.get("change_pct")})
        hsi = raw.get("rt_hkHSI")  # 恒生无现成 kind：现价 f[6]、涨跌幅 f[8]
        if hsi and len(hsi) > 8:
            try:
                p, pct = float(hsi[6]), float(hsi[8])
                if p:
                    items.append({"name": "恒生指数", "price": p, "change_pct": pct})
            except (ValueError, IndexError):
                pass
    except Exception:  # noqa: BLE001  补充品种失败不影响主看板
        pass
    if not items:
        return None
    return {
        "as_of": glob.get("generated_at") or ash.get("generated_at"),
        "quotes": items,
        "note": "实时价格快照（美股/港股/商品在其休市时段为最近一个交易日收盘价，as_of 是抓取时刻非行情时刻，勿据此当盘中实时报）；只据 price 与 change_pct 如实陈述当前价位与今日涨跌，不要凭记忆断言历史新高/新低/创纪录。",
    }


register_tool(AgentTool(
    name="get_market_data",
    description=(
        "获取【大盘指数 / 商品 / 比特币 / 汇率 / 宏观利率】的实时价格：黄金、WTI原油、比特币、标普500、VIX、美元指数、"
        "美国10年期国债收益率、上证指数、沪深300、创业板指、美元兑人民币（含现价 + 今日涨跌幅）。"
        "用户问『黄金/原油/比特币现在多少钱、涨没涨』『大盘/上证/沪深300/创业板今天怎样』『美元人民币汇率』『美债收益率/VIX』等**非个股的指数·商品·宏观行情**时，"
        "必须调本工具取真实价，绝不能凭记忆作答（尤其不要张口就说『历史新高/新低』）。已覆盖 纳指/道指/恒生/白银；返回里确实没有的品种才如实说暂无法实时获取。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "可留空；想看的品种关键词，如『黄金』『上证』『比特币』（当前返回全部常用品种）"},
        },
    },
    handler=_tool_get_market_data,
))


# 名人观点（celebrity_views）：注册为 **白名单专属** agent 工具（_WHITELIST_ONLY_TOOLS）——
# 仅白名单(lx199710)的会话才会在工具清单里看到它(openai_tool_specs 过滤)+handler 双重门控；
# 其他会员/匿名既看不到工具、即使硬调也只得"未开放"，杜绝把灰度名人内容套问泄露。与 iFinD/深度研判同策略。
async def _tool_get_celebrity_views(query: str = "") -> Any:
    # 双重门控(防御纵深)：终端走 _IFIND_GRADE(ifind_user)、微信走 _BINDING_USER 白名单；都不命中 → 不泄露任何内容。
    if not (_IFIND_GRADE.get() or _binding_whitelisted()):
        return {"note": "该功能暂未开放"}
    try:
        from . import celebrity_views
        resp = await celebrity_views.fetch_celebrity_views()
        data = resp.model_dump() if hasattr(resp, "model_dump") else (resp or {})
    except Exception:  # noqa: BLE001
        return None
    q = (query or "").strip()
    out: list[dict[str, Any]] = []
    for fg in (data.get("figures") or []):
        name = fg.get("name") or ""
        if q and q not in name and q not in (fg.get("org") or "") and q not in (fg.get("role") or ""):
            continue
        latest = [{"title": it.get("title", ""), "summary": (it.get("body") or "")[:220],
                   "date": it.get("published_at") or it.get("reported_date")}
                  for it in (fg.get("items") or [])[:4]]
        out.append({"name": name, "role": fg.get("role") or fg.get("org") or "",
                    "digest": fg.get("digest") or "", "latest": latest})
    if not out:
        return None
    return {"celebrities": out[:8], "note": "名人观点（白名单专属）；据此如实回答，不要编造未给出的观点。"}


register_tool(AgentTool(
    name="get_celebrity_views",
    description="获取【名人观点】最新研判要点（白名单专属功能）。当用户问『某名人/大V 最近怎么看后市/某板块、XX 的最新观点』时调用；据返回内容如实回答、不得编造未给出的观点。",
    parameters={"type": "object", "properties": {
        "query": {"type": "string", "description": "可选：某位名人的名字，留空返回全部名人最新观点"},
    }},
    handler=_tool_get_celebrity_views,
))


# ---- 交易纪律/行为偏差方法论(源自真实交割单行为归因复盘·通用化) ----
from . import trading_discipline as _trading_discipline


async def _tool_get_trading_discipline(topic: Optional[str] = None, question: Optional[str] = None) -> Any:
    """交易纪律知识库:开仓/仓位/加仓/止损/止盈/行为偏差/估值陷阱/复盘方法。只讲方法论,不评具体标的。"""
    return _trading_discipline.lookup(topic=topic, question=question)


register_tool(AgentTool(
    name="get_trading_discipline",
    description="交易纪律与资金管理方法论知识库(开仓时机/仓位控制/加仓原则/止损止盈/行为偏差/估值陷阱/复盘方法)。"
                "当用户问『被套了怎么办/要不要补仓/怎么止损止盈/什么时候该卖/仓位怎么控/跌这么多是不是便宜了/"
                "怎么改掉追涨杀跌·频繁交易的毛病/怎么复盘』这类**交易方法与心态**问题时调用;"
                "回答须忠于返回的纪律条目并保留免责声明,不得据此给出具体标的的买卖指令。",
    parameters={"type": "object", "properties": {
        "topic": {"type": "string", "description": "可选主题:entry开仓/sizing仓位/add加仓/stop止损/profit止盈/bias行为偏差/valuation_trap估值陷阱/review复盘;留空按question自动匹配"},
        "question": {"type": "string", "description": "可选:用户原话,用于关键词匹配相关主题"},
    }},
    handler=_tool_get_trading_discipline,
))
