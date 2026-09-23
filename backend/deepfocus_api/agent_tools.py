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
from .realtime_messages import is_futoucaixin_restricted_user, list_realtime_messages
from .shared_utils import safe_float
from .valuation_source import fetch_valuation


def _exclude_restricted_news_source() -> bool:
    """AI 工具也必须遵守账号级资讯隔离。

    账号由服务端入口注入 ContextVar，不放进 tool JSON 参数，因此模型和
    客户端都不能通过伪造工具参数绕过。
    """
    username = (_BINDING_USER.get() or "").strip()
    return not username or is_futoucaixin_restricted_user(username)


def _only_futoucaixin_news_source() -> bool:
    """兼容旧调用参数：当前策略不再把 AI 资料收窄为单一来源。

    资讯列表统一只屏蔽 TradeAlpha；富途财经和其他来源都保留。匿名/受限账号
    对富途财经的隔离仍由 ``_exclude_restricted_news_source`` 负责。
    """
    return False


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

# 中文用户默认指向主要上市地。A 股大盘由 stock_name_index 覆盖；
# 这里只补高频港/美股，避免模型凭记忆把「阿里」一会儿解析成 BABA、
# 一会儿又变 09988，导致横向对比币种与报表口径不一致。
_KNOWN_CROSS_MARKET_SYMBOLS = {
    "建滔集团": "00148", "建滔": "00148",
    "快手": "01024", "快手-w": "01024", "快手－w": "01024",
    "腾讯": "00700", "腾讯控股": "00700",
    "阿里": "09988", "阿里巴巴": "09988", "阿里巴巴-w": "09988",
    "美团": "03690", "美团-w": "03690",
    "英伟达": "NVDA", "英达": "NVDA",
    "苹果": "AAPL", "微软": "MSFT", "特斯拉": "TSLA",
}


def _known_symbol(value: str) -> str | None:
    key = (value or "").strip().lower().replace(" ", "")
    return _KNOWN_CROSS_MARKET_SYMBOLS.get(key)


def resolve_known_symbol(value: str) -> str | None:
    """从整句问题中解析内置的港/美股名称；用于研究前置层锁定标的。"""
    text = (value or "").strip()
    exact = _known_symbol(text)
    if exact:
        return exact
    normalized = text.lower().replace(" ", "")
    for name, code in sorted(_KNOWN_CROSS_MARKET_SYMBOLS.items(), key=lambda item: len(item[0]), reverse=True):
        if name.lower().replace(" ", "") in normalized:
            return code
    return None


def known_symbol_name(symbol: str) -> str:
    """按内置跨市场映射反查公司名。"""
    code = str(symbol or "").strip().upper()
    return next((name for name, value in _KNOWN_CROSS_MARKET_SYMBOLS.items() if str(value).upper() == code), "")


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
        known = _known_symbol(sym)
        if known:
            arguments = {**arguments, "symbol": known}
            sym = known
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
            coerced = [(_known_symbol(p) or resolve_to_code(p) or p) for p in parts]
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
                # 东财港股聚合表可能对覆盖稀疏的标的返回多年以前的记录。
                # 目标价/评级一旦超过 18 个月就不能与当前行情拼接，更不能当作
                # “半年内目标价”回答；保留明确的缺口说明，让上层去查本站研报/纪要。
                dates = _re.findall(r"20\d{2}-\d{2}-\d{2}", str(res.get("period") or ""))
                if dates:
                    try:
                        age_days = (_dt.now(_tz.utc).date() - _dt.fromisoformat(dates[-1]).date()).days
                    except (TypeError, ValueError):
                        age_days = 0
                    if age_days > 540:
                        return {
                            "symbol": res.get("symbol") or str(symbol).strip(),
                            "name": res.get("name"),
                            "market": res.get("market") or market,
                            "currency": res.get("currency"),
                            "period": res.get("period"),
                            "data_quality": "stale_excluded",
                            "note": "卖方共识时点超过18个月，目标价与评级已排除",
                        }
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
                "A/港股为公开行情页口径；market_cap 单位为元、另附 market_cap_yi(亿元)直接展示。美/A/港股通用。",
    parameters=_SYMBOL_MARKET_SCHEMA,
    handler=_tool_get_valuation,
))
register_tool(AgentTool(
    name="get_analyst_consensus",
    description="获取卖方一致预期：目标价、较现价空间、评级共识(买入/增持家数)。美股+A股+港股均覆盖，必须注意目标价币种与数据时点。",
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
    description="查询A股个股最近几期分红送转(每10股送X转Y派Z元、股息率、股权登记日/除权除息日、分红进度)。问『XX分红多少/股息率/派息/送转/除权日』时调用；返回公开披露方案，防止编造分红数字。仅A股。",
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
    """把股票【中文名称/片段】解析成标准代码候选。"""
    known = _known_symbol(query)
    if known:
        market = "港股" if known.isdigit() and len(known) == 5 else "美股"
        return {"matches": [{"name": query.strip(), "code": known, "market": market}]}
    from . import stock_name_index
    cands = stock_name_index.search_names(query, limit=8)
    if not cands:
        return {"matches": [], "note": f"未找到与「{query}」匹配的唯一上市标的，请补充完整名称或代码"}
    return {"matches": cands}


register_tool(AgentTool(
    name="resolve_symbol",
    description=(
        "把股票【中文名称】解析成标准【代码】，覆盖 A/港/美股高频标的。当用户用中文名提到某只股票、"
        "或多轮对话里用『他/它/这只/这个票』指代某只股时，**先调本工具拿到准确代码，再去调行情/估值/财报等数据工具**——"
        "绝不要凭记忆猜代码(猜错=取数全错)。返回候选 {name,code,market}，多个时按相关度排序；为空才请用户补代码。"
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


def _compact_fields(value: Any, keys: tuple[str, ...]) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    out = {key: value.get(key) for key in keys if value.get(key) is not None}
    return out or None


def _financial_period_key(report_date: Any) -> str | None:
    """把财报截止日归一成可比较的报告期键。

    compare_stocks 取的是每家公司各自最新财报；如果不把 03-31/06-30
    明确标成 Q1/H1，模型很容易把“都有营收同比”误写成“同口径”。
    财报源目前只保证 report_date，因此这里不猜财报发布日，只按截止日归类。
    """
    text = str(report_date or "").strip()[:10]
    try:
        date = _dt.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    period = {3: "Q1", 6: "H1", 9: "Q3", 12: "FY"}.get(date.month)
    return f"{date.year}-{period}" if period else f"{date.year}-{date.month:02d}"


async def _tool_compare_stocks(symbols: str = "") -> Any:
    """2~4 股比较数据包；明确区分共同报告期和各自最新报告期。"""
    parts = [p.strip() for p in _re.split(r"[,，、\s]+", symbols or "") if p.strip()][:4]
    if not parts:
        return {"items": [], "note": "请提供股票名称或代码"}

    async def one(symbol: str) -> dict[str, Any]:
        quote, valuation, financials, consensus = await asyncio.gather(
            _tool_get_market_quote(symbol),
            _tool_get_valuation(symbol),
            _tool_get_financials(symbol),
            _tool_get_analyst_consensus(symbol),
            return_exceptions=True,
        )
        qrow = None
        if isinstance(quote, dict) and isinstance(quote.get("quotes"), list) and quote["quotes"]:
            qrow = quote["quotes"][0]
        vrow = valuation if isinstance(valuation, dict) else None
        frow = financials if isinstance(financials, dict) else None
        crow = consensus if isinstance(consensus, dict) else None
        gaps: list[str] = []
        # 历史覆盖很少的标的可能返回多年前的目标价。这种数字
        # 不只是「置信度低」，而是不应再进入当前比较表，否则模型会
        # 拿 2017 年目标价与 2026 年市值混用。
        if crow:
            period = str(crow.get("period") or "")
            dates = _re.findall(r"20\d{2}-\d{2}-\d{2}", period)
            try:
                stale = bool(dates) and (_dt.now(_tz.utc).date() - _dt.fromisoformat(dates[-1]).date()).days > 540
            except (ValueError, TypeError):
                stale = False
            if stale:
                crow = None
                gaps.append("卖方共识时点过旧，已排除")
        name = (frow or {}).get("name") or (crow or {}).get("name") or (qrow or {}).get("name") or symbol
        if qrow is None:
            gaps.append("实时行情暂不可用")
        if vrow is None:
            gaps.append("估值暂不可用")
        if frow is None:
            gaps.append("最新财报暂不可用")
        financial_projection = _compact_fields(frow, ("report_date", "revenue_yoy", "profit_yoy", "roe", "gross_margin", "eps"))
        if financial_projection:
            period_key = _financial_period_key(financial_projection.get("report_date"))
            if period_key:
                financial_projection["period_key"] = period_key
                financial_projection["period_label"] = period_key.replace("-", " ")
        return {
            "symbol": symbol,
            "name": name,
            "quote": _compact_fields(qrow, (
                "price", "change_percent", "currency", "market_time", "fetched_at", "is_realtime",
                "pe_ttm", "wk52_high", "wk52_low", "market_cap",
            )),
            "valuation": _compact_fields(vrow, ("market_cap_yi", "pe_ratio", "pe_dynamic", "pb_ratio", "ps_ratio", "peg", "currency")),
            "financials": financial_projection,
            "consensus": _compact_fields(crow, ("consensus_rating", "rating_summary", "report_count", "period", "avg_target_price", "target_price_low", "target_price_high", "currency")),
            "data_gaps": gaps,
        }

    items = await asyncio.gather(*(one(symbol) for symbol in parts))
    periods_by_symbol = {
        str(item.get("symbol") or ""): ((item.get("financials") or {}).get("period_key"))
        for item in items
    }
    periods = {period for period in periods_by_symbol.values() if period}
    all_have_period = len(items) < 2 or all(periods_by_symbol.values())
    strictly_comparable = len(items) < 2 or (all_have_period and len(periods) == 1)
    comparison_basis = {
        "is_strictly_comparable": strictly_comparable,
        "financials": "common_period" if strictly_comparable and len(items) >= 2 else (
            "not_applicable_single" if len(items) < 2 else "latest_each_company"
        ),
        "snapshot_fields": ["price", "market_cap", "pe_ratio", "pb_ratio", "ps_ratio", "currency"],
        "blocked_financial_fields": [] if strictly_comparable else [
            "revenue_yoy", "profit_yoy", "roe", "gross_margin", "eps", "peg",
        ],
        "periods_by_symbol": periods_by_symbol,
        "warning": "财报截止期不同；营收/利润增速、ROE、毛利率和 EPS 只能分别展示，不得横向排名。"
        if not strictly_comparable and len(items) >= 2 else "",
    }
    for item in items:
        financials = item.get("financials")
        if isinstance(financials, dict):
            financials["same_period_as_peers"] = strictly_comparable

    return {
        "items": items,
        "comparison_basis": comparison_basis,
        "comparison_rule": (
            "行情/估值按当前快照比较；财务字段只有 comparison_basis.is_strictly_comparable=true "
            "时才可横向排名。若为 false，必须分别标注每家公司 report_date/period_label，"
            "不得写‘同口径’、‘同一报告期’或据此比较 ROE/增速。某项为 null 就标注数据缺口，不得自行补数。"
        ),
    }


register_tool(AgentTool(
    name="compare_stocks",
    description=(
        "对 2~4 只股票一次性并行获取行情、估值、各自最新财报与卖方共识；"
        "工具会明确返回财报是否为共同报告期，不能把‘各自最新’默认称为同口径。"
        "用户问『A和B谁更值得关注』『三家排序』等比较题时必须优先只调本工具，"
        "不要再为每只股散调四组工具。支持逗号分隔的中文名或代码。"
    ),
    parameters={
        "type": "object",
        "properties": {"symbols": {"type": "string", "description": "2~4 只股，逗号分隔，如：宁德时代,比亚迪"}},
        "required": ["symbols"],
    },
    handler=_tool_compare_stocks,
))


async def _tool_get_stock_snapshot(symbol: str, market: Optional[str] = None) -> Any:
    """单股常用数据并行快照；market 保留为通用 schema 兼容参数。"""
    bundle = await _tool_compare_stocks(symbol)
    items = bundle.get("items") if isinstance(bundle, dict) else None
    return items[0] if isinstance(items, list) and items else None


register_tool(AgentTool(
    name="get_stock_snapshot",
    description=(
        "一次并行获取单只股票的行情、当前估值、最新财报与卖方共识精简包。"
        "问『估值贵不贵/值不值得关注/财报后还有空间吗』时优先只调本工具，"
        "不要再分别散调行情、估值、财报、共识四个工具。支持常见中文名和代码。"
    ),
    parameters=_SYMBOL_MARKET_SCHEMA,
    handler=_tool_get_stock_snapshot,
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
        for m in list_realtime_messages(
            anyq=symbol,
            exclude_futoucaixin=_exclude_restricted_news_source(),
            only_futoucaixin=_only_futoucaixin_news_source(),
            limit=20,
        ):
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
                "利润含金量、扣非、毛利率、ROE。需要现金流结构/盈利质量/是否现金奶牛时用。公开数据多源回退，仅 A股。",
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


async def _tool_search_our_content(
    query: str = "", days: int = 7, limit: int = 12, topic: str = ""
) -> Any:
    """检索/汇总「我们网站」近期发布的快讯/文章。

    ``topic`` 是可选的内容类型过滤器。保留一个通用工具兼容旧的 AI 对话，深度
    Harness 则通过下面两个专用别名分别取快讯和文章，避免两个模块在数据包里互相覆盖。
    """
    import hashlib as _hashlib
    from .metrics_store import get_ai_cache
    since = (_dt.now(_tz.utc) - _td(days=max(1, min(int(days or 7), 30)))).strftime("%Y-%m-%dT%H:%M:%S")
    # 多词 OR 召回（anyq 按逗号分词→任一词命中标题或正文）；**留空→不加关键词，按 since 取近期最新**。
    terms = [t for t in _re.split(r"[\s,，、/|]+", str(query or "")) if len(t) >= 2][:8]
    anyq = ",".join(terms) if terms else ""
    try:
        # limit 上限放宽到 60，便于「近 24 小时全覆盖」式总结取全近期快讯。
        msgs = list_realtime_messages(
            anyq=anyq,
            since=since,
            exclude_futoucaixin=_exclude_restricted_news_source(),
            only_futoucaixin=_only_futoucaixin_news_source(),
            limit=max(1, min(int(limit or 20), 60)),
        )
    except Exception:
        return []
    _TONE = {"warning": "风险/利空", "success": "利好", "info": "中性"}
    out: list[dict[str, Any]] = []
    seen_content: set[str] = set()
    topic_filter = str(topic or "").strip()
    if topic_filter not in {"", "快讯", "文章"}:
        topic_filter = ""
    for m in msgs:
        topic = getattr(m, "topic", "") or ""
        if topic not in ("快讯", "文章", "研报"):
            continue
        if topic_filter and topic != topic_filter:
            continue
        title = (getattr(m, "title", "") or "")[:80]
        content = _re.sub(r"https?://\S+", "", getattr(m, "content", "") or "").strip()
        #采集源可能重复推送同一篇文章；按规范化标题+正文指纹去重。
        norm_title = _re.sub(r"\s+", "", title.casefold())
        norm_content = _re.sub(r"\s+", "", content)
        dedup_key = _hashlib.sha1(f"{norm_title}\n{norm_content}".encode("utf-8")).hexdigest()
        if dedup_key in seen_content:
            continue
        seen_content.add(dedup_key)
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
            "limit": {"type": "integer", "description": "最多返回条数，默认 12（上限 60）"},
            "topic": {"type": "string", "enum": ["快讯", "文章"], "description": "可选：只返回快讯或文章"},
        },
    },
    handler=_tool_search_our_content,
))


async def _tool_get_site_fast_news(query: str = "", days: int = 7, limit: int = 12) -> Any:
    """深度研究专用：只取稻草财经站内快讯。"""
    return await _tool_search_our_content(query=query, days=days, limit=limit, topic="快讯")


register_tool(AgentTool(
    name="get_site_fast_news",
    description=(
        "只检索稻草财经站内的【快讯】模块，返回标题、摘要、发布时间和方向标签。"
        "不会混入文章或研报；用于深度研究时与公开行情、公告和财报交叉核验。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "股票代码/名称或主题关键词；留空取近期快讯"},
            "days": {"type": "integer", "description": "回溯天数，默认 7"},
            "limit": {"type": "integer", "description": "最多返回条数，默认 12（上限 60）"},
        },
    },
    handler=_tool_get_site_fast_news,
))


async def _tool_get_site_articles(query: str = "", days: int = 30, limit: int = 12) -> Any:
    """深度研究专用：只取稻草财经站内文章。"""
    return await _tool_search_our_content(query=query, days=days, limit=limit, topic="文章")


register_tool(AgentTool(
    name="get_site_articles",
    description=(
        "只检索稻草财经站内的【文章】模块，返回标题、摘要、发布时间、方向标签和已有 AI 解读。"
        "不会混入快讯或研报；用于深度研究时与其他站内内容和公开数据综合。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "股票代码/名称或主题关键词；留空取近期文章"},
            "days": {"type": "integer", "description": "回溯天数，默认 30"},
            "limit": {"type": "integer", "description": "最多返回条数，默认 12（上限 60）"},
        },
    },
    handler=_tool_get_site_articles,
))


def _recent_cutoff(days: int) -> tuple[_dt, str]:
    """Return a UTC cutoff and ISO string for bounded recent-content scans."""
    window_days = max(1, min(int(days or 2), 30))
    cutoff = _dt.now(_tz.utc) - _td(days=window_days)
    return cutoff, cutoff.isoformat()


def _parse_source_datetime(value: Any) -> Optional[_dt]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = _dt.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
            try:
                parsed = _dt.strptime(raw[:19], fmt)
                break
            except ValueError:
                parsed = None
        if parsed is None:
            return None
    return parsed.replace(tzinfo=parsed.tzinfo or _tz.utc)


async def _recent_site_content(
    *, query: str = "", days: int = 2, topic: str, limit: int = 200,
) -> dict[str, Any]:
    """Read all locally indexed realtime items in a time window, page by page.

    The normal site-content tool intentionally caps a single answer at 60 rows.  A
    cross-source digest must first establish coverage, so it pages the SQLite
    index (which is cheap) until the cutoff or the safety cap and returns explicit
    scan metadata alongside the compact rows.
    """
    from .metrics_store import get_ai_cache
    import hashlib as _hashlib

    cutoff, since_iso = _recent_cutoff(days)
    cap = max(1, min(int(limit or 200), 500))
    terms = [t for t in _re.split(r"[\s,，、/|]+", str(query or "")) if len(t) >= 2][:8]
    anyq = ",".join(terms) if terms else ""
    rows: list[dict[str, Any]] = []
    before = ""
    pages = 0
    scanned = 0
    unknown_dates = 0
    complete = False
    visibility = "visible"
    raw_available = False
    while pages < 20 and len(rows) < cap:
        page = list_realtime_messages(
            topic=topic,
            anyq=anyq,
            since=since_iso,
            before=before,
            exclude_futoucaixin=_exclude_restricted_news_source(),
            only_futoucaixin=_only_futoucaixin_news_source(),
            limit=200,
        )
        pages += 1
        if not page:
            # 匿名/受限账号可能看到空页，但库里实际有同窗口的富途快讯。
            # 只做数量探针，不返回受限标题/正文，避免诊断信息绕过内容隔离。
            if _exclude_restricted_news_source():
                try:
                    raw_probe = list_realtime_messages(
                        topic=topic,
                        anyq=anyq,
                        since=since_iso,
                        before=before,
                        exclude_futoucaixin=False,
                        only_futoucaixin=False,
                        limit=1,
                    )
                    raw_available = bool(raw_probe)
                except Exception:  # noqa: BLE001
                    raw_available = False
            visibility = "access_filtered" if raw_available else "empty"
            complete = True
            break
        scanned += len(page)
        for msg in page:
            published = getattr(msg, "created_at", "") or ""
            parsed = _parse_source_datetime(published)
            if parsed is None:
                unknown_dates += 1
                continue
            if parsed < cutoff:
                complete = True
                continue
            title = (getattr(msg, "title", "") or "")[:80]
            content = _re.sub(r"https?://\S+", "", getattr(msg, "content", "") or "").strip()
            item: dict[str, Any] = {
                "id": str(getattr(msg, "id", "") or ""),
                "title": title,
                "topic": topic,
                "snippet": content[:420] if content and content != title else "",
                "published_at": published,
                "tone": {"warning": "风险/利空", "success": "利好", "info": "中性"}.get(
                    getattr(msg, "severity", "") or "", "中性"
                ),
            }
            if topic == "文章":
                raw_title = (getattr(msg, "title", "") or "").strip()
                raw_content = (getattr(msg, "content", "") or "").strip()
                if len(raw_title + raw_content) >= 60:
                    cached = get_ai_cache(
                        "news:" + _hashlib.sha1(f"{raw_title}\n{raw_content}".encode("utf-8")).hexdigest()[:20]
                    )
                    if cached:
                        item["ai_summary"] = (cached.get("summary") or cached.get("one_liner") or "")[:300]
            rows.append(item)
            if len(rows) >= cap:
                break
        oldest = getattr(page[-1], "created_at", "") or ""
        oldest_dt = _parse_source_datetime(oldest)
        if complete or len(rows) >= cap or len(page) < 200 or not oldest_dt or oldest_dt < cutoff:
            complete = complete or bool(oldest_dt and oldest_dt < cutoff) or len(page) < 200
            break
        before = str(oldest)
    return {
        "items": rows,
        "count": len(rows),
        "coverage": {
            "window_days": max(1, min(int(days or 2), 30)),
            "since": since_iso,
            "scanned_count": scanned,
            "matched_count": len(rows),
            "pages": pages,
            "complete": bool(complete),
            "unknown_dates": unknown_dates,
            "visibility": visibility,
            "raw_available": raw_available,
            "ingestion_status": "ok" if rows else ("access_filtered" if raw_available else "empty"),
        },
        "source": "稻草财经" if topic in {"快讯", "文章"} else topic,
    }


async def _recent_wire_content(*, query: str = "", days: int = 2, limit: int = 200) -> dict[str, Any]:
    """Read and date-filter the complete currently indexed overseas research pool."""
    from .research_wire import fetch_research_wire_online
    from .metrics_store import get_ai_cache_many

    cutoff, since_iso = _recent_cutoff(days)
    cap = max(1, min(int(limit or 200), 500))
    try:
        online = await fetch_research_wire_online(limit=500, query=str(query or ""))
    except Exception as exc:  # noqa: BLE001
        return {"items": [], "count": 0, "coverage": {"window_days": days, "since": since_iso, "complete": False, "error": type(exc).__name__}, "source": "稻草财经投行研报"}
    terms = [t.casefold() for t in _re.split(r"[\s,，、/|]+", str(query or "")) if len(t) >= 2][:8]
    source_items = list((online or {}).get("items") or [])
    cache_map = get_ai_cache_many([str(r.get("file_id") or "").strip() for r in source_items if r.get("file_id")])
    out: list[dict[str, Any]] = []
    unknown_dates = 0
    for row in source_items:
        title = str(row.get("title") or "").strip()
        hay = f"{title} {row.get('org') or ''}".casefold()
        if terms and not any(term in hay for term in terms):
            continue
        published = str(row.get("created_at") or row.get("date") or "")[:40]
        parsed = _parse_source_datetime(published)
        if parsed is None:
            unknown_dates += 1
            continue
        if parsed < cutoff:
            continue
        fid = str(row.get("file_id") or "").strip()
        cached = cache_map.get(fid) or {}
        out.append({
            "id": fid or str(row.get("id") or ""),
            "title": title[:120],
            "org": row.get("org") or "",
            "date": row.get("date") or published,
            "published_at": published,
            "file_id": fid,
            "ai_summary": ((cached.get("summary") or cached.get("one_liner") or "")[:300]) or None,
        })
        if len(out) >= cap:
            break
    return {
        "items": out,
        "count": len(out),
        "coverage": {
            "window_days": max(1, min(int(days or 2), 30)),
            "since": since_iso,
            "scanned_count": len(source_items),
            "matched_count": len(out),
            "complete": len(source_items) < 500,
            "unknown_dates": unknown_dates,
        },
        "source": "稻草财经投行研报",
    }


async def _tool_get_recent_content_digest(query: str = "", days: int = 2, limit: int = 200) -> Any:
    """统一扫描最近窗口的快讯、文章、本地研报、投行研报和机构纪要。

    This is the auditable path for questions such as “总结最近两天所有机构纪要”:
    all five retrieval channels use the same cutoff, every source returns scan coverage, and
    the full set is fed through the persistent ontology index before compacting
    the model-facing evidence.
    """
    cap = max(20, min(int(limit or 200), 500))
    news, articles, local_reports, reports, notes = await asyncio.gather(
        _recent_site_content(query=query, days=days, topic="快讯", limit=cap),
        _recent_site_content(query=query, days=days, topic="文章", limit=cap),
        _recent_site_content(query=query, days=days, topic="研报", limit=cap),
        _recent_wire_content(query=query, days=days, limit=cap),
        _tool_get_institution_notes(query=query, days=days, limit=cap, scan_limit=500),
        return_exceptions=True,
    )
    payloads = {"快讯": news, "文章": articles, "研报": local_reports, "投行研报": reports, "机构纪要": notes}
    result: dict[str, Any] = {"window_days": max(1, min(int(days or 2), 30)), "query": query or "", "sources": {}}
    for label, value in payloads.items():
        if isinstance(value, Exception):
            result["sources"][label] = {"items": [], "count": 0, "coverage": {"complete": False, "error": type(value).__name__}}
        else:
            result["sources"][label] = value
    # Persist and rank every row before reducing the model-facing view.  This is
    # the same deterministic index used by stock research, with no LLM call.
    try:
        from .research_index import build_research_index
        modules = {
            "get_site_fast_news": result["sources"]["快讯"].get("items", []),
            "get_site_articles": result["sources"]["文章"].get("items", []),
            "get_stock_research": result["sources"]["研报"].get("items", []),
            "get_recent_research": result["sources"]["投行研报"].get("items", []),
            "get_institution_notes": result["sources"]["机构纪要"].get("items", []),
        }
        indexed = build_research_index(modules, objective=query, stock=None, persist_annotations=True)
        result["index"] = {
            "raw_items": indexed.get("stats", {}).get("raw_items", 0),
            "deduped_items": indexed.get("stats", {}).get("deduped_items", 0),
            "selected_items": indexed.get("stats", {}).get("selected_items", 0),
            "annotation_version": indexed.get("stats", {}).get("annotation_version", ""),
            "coverage": indexed.get("coverage", {}),
        }
        result["analysis"] = indexed.get("analysis", {})
        result["evidence"] = indexed.get("digest", [])[:24]
    except Exception as exc:  # noqa: BLE001
        result["index"] = {"error": type(exc).__name__}
        result["analysis"] = {}
        result["evidence"] = []
    result["coverage"] = {
        label: value.get("coverage", {}) if isinstance(value, dict) else {}
        for label, value in result["sources"].items()
    }
    result["coverage_complete"] = all(bool(item.get("complete")) for item in result["coverage"].values())
    return result


register_tool(AgentTool(
    name="get_recent_content_digest",
    description=(
        "严格按同一时间窗口全量扫描并索引稻草财经【快讯、文章、研报（含投行研报）、机构纪要】。"
        "回答‘最近两天/最近N天全部内容、机构纪要总结、四类资料汇总’时必须优先使用；"
        "返回每类 scanned_count/matched_count/pages/complete 和去重后的证据索引。"
        "它只做真实资料读取与索引，不调用 AI；之后再基于返回证据总结。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "可选主题/公司关键词；留空=窗口内全部"},
            "days": {"type": "integer", "description": "回溯天数，默认 2，最大 30"},
            "limit": {"type": "integer", "description": "每类最多返回条数，默认 200，最大 500"},
        },
    },
    handler=_tool_get_recent_content_digest,
))


def _cross_market_name(symbol: str) -> str:
    code = str(symbol or "").strip().upper()
    for name, value in _KNOWN_CROSS_MARKET_SYMBOLS.items():
        if str(value).upper() == code:
            return name
    return ""


def _entity_content_rows(rows: Any, *, name: str, symbol: str) -> list[dict[str, Any]]:
    """从关键词 OR 召回结果中保留明确提及该主体的条目，防止行业词带回无关快讯。"""
    if not isinstance(rows, list):
        return []
    anchors = [str(value or "").strip().casefold() for value in (name, symbol) if str(value or "").strip()]
    if not anchors:
        return []
    out: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        hay = f"{item.get('title') or ''} {item.get('snippet') or ''}".casefold()
        if any(anchor in hay for anchor in anchors):
            out.append(item)
    return out


async def _tool_get_industry_context(
    symbol: str,
    market: Optional[str] = None,
    name: str = "",
) -> Any:
    """获取个股行业归属，并检索站内行业/竞争格局资料。"""
    display = (name or _cross_market_name(symbol) or symbol or "").strip()
    mk = (market or "").upper()
    themes: dict[str, Any] = {}
    if mk in {"", "CN"} and _re.fullmatch(r"\d{6}", str(symbol or "").strip()):
        try:
            from .theme_navigation import fetch_stock_themes
            themes = await fetch_stock_themes(str(symbol).strip()) or {}
        except Exception:  # noqa: BLE001 - 行业归属是增强层，失败不阻断主研究
            themes = {}
    query = f"{display} 行业 竞争格局".strip()
    try:
        rows = await _tool_search_our_content(query=query, days=180, limit=8)
        rows = _entity_content_rows(rows, name=display, symbol=str(symbol or ""))
    except Exception:  # noqa: BLE001
        rows = []
    industry = str(themes.get("industry") or "").strip()
    board = str(themes.get("board") or "").strip()
    return {
        "symbol": str(symbol or "").strip(),
        "name": display,
        "market": mk,
        "industry": industry,
        "board": board,
        "items": rows if isinstance(rows, list) else [],
        "source": "东方财富行业归属 + 稻草财经行业资料",
        "note": "行业归属为公开板块事实；行业文章只作背景资料，不等同于公司直接证据。"
        if industry or board or rows else "暂无行业归属或行业资料命中",
    }


register_tool(AgentTool(
    name="get_industry_context",
    description=(
        "获取个股公开行业/板块归属，并检索站内行业与竞争格局资料。行业归属是事实，"
        "行业文章只作为背景，不把行业消息冒充公司公告或财报。A股行业归属覆盖最完整；港股若无结构化归属会如实标注缺口。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "股票代码，如 600519、00700、00148"},
            "market": {"type": "string", "enum": ["US", "CN", "HK"]},
            "name": {"type": "string", "description": "可选，公司名称"},
        },
        "required": ["symbol"],
    },
    handler=_tool_get_industry_context,
))


async def _tool_get_peer_comparison(
    symbol: str,
    market: Optional[str] = None,
    name: str = "",
    limit: int = 5,
) -> Any:
    """从确定性行业板块成员中列出可比同行候选；不把候选名单当成估值结论。"""
    code = str(symbol or "").strip()
    display = (name or _cross_market_name(code) or code).strip()
    mk = (market or "").upper()
    industry = ""
    board = ""
    peers: list[dict[str, Any]] = []
    if mk in {"", "CN"} and _re.fullmatch(r"\d{6}", code):
        try:
            from .theme_navigation import fetch_board_stocks, fetch_stock_themes, find_board_by_name
            themes = await fetch_stock_themes(code) or {}
            industry = str(themes.get("industry") or "").strip()
            board = str(themes.get("board") or "").strip()
            board_ref = await find_board_by_name(industry or board)
            if board_ref:
                members = await fetch_board_stocks(board_ref["code"], limit=max(8, min(int(limit or 5) + 2, 30)))
                for item in members:
                    peer_code = str(item.get("code") or "").strip()
                    if not peer_code or peer_code == code:
                        continue
                    peer_name = str(item.get("name") or peer_code).strip()
                    peers.append({
                        "symbol": peer_code,
                        "name": peer_name,
                        "title": f"{peer_name}（{board_ref.get('name') or industry or '同行板块'}成员）",
                        "summary": f"公开板块成员；最新涨跌幅 {item.get('pct') if item.get('pct') is not None else '暂无'}。",
                        "source_name": "东方财富行业板块",
                        "published_at": "",
                        "url": "",
                    })
                    if len(peers) >= max(1, min(int(limit or 5), 8)):
                        break
        except Exception:  # noqa: BLE001
            peers = []
    return {
        "symbol": code,
        "name": display,
        "market": mk,
        "industry": industry,
        "board": board,
        "items": peers,
        "selection_basis": "公开行业板块成员，仅作为可比候选；估值/财务需另行按同一口径取数。",
        "note": (
            "已找到公开行业板块成员，后续应逐只核对估值、报告期与盈利质量。"
            if peers else
            "当前没有可验证的结构化同行名单；不会把文章中被提及的公司直接当作可比同行。"
        ),
    }


register_tool(AgentTool(
    name="get_peer_comparison",
    description=(
        "获取公开行业板块中的同行候选名单，用于后续估值与盈利质量横向核对。"
        "候选名单不是推荐；若没有结构化板块成员，必须标注暂无可比同行，不得凭文章提及关系猜测。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "股票代码"},
            "market": {"type": "string", "enum": ["US", "CN", "HK"]},
            "name": {"type": "string", "description": "可选，公司名称"},
            "limit": {"type": "integer", "description": "同行候选数，默认 5"},
        },
        "required": ["symbol"],
    },
    handler=_tool_get_peer_comparison,
))


async def _tool_get_supply_chain_context(
    symbol: str,
    market: Optional[str] = None,
    name: str = "",
) -> Any:
    """检索公司上下游、原材料、客户与价格传导相关资料，返回线索而非臆测关系。"""
    display = (name or _cross_market_name(symbol) or symbol or "").strip()
    query = f"{display} 供应链 上游 下游 原材料 客户 价格".strip()
    try:
        rows = await _tool_search_our_content(query=query, days=180, limit=12)
        rows = _entity_content_rows(rows, name=display, symbol=str(symbol or ""))
    except Exception:  # noqa: BLE001
        rows = []
    items = rows if isinstance(rows, list) else []
    upstream_terms = _re.compile(r"上游|原材料|成本|供应商|供给|价格", _re.I)
    downstream_terms = _re.compile(r"下游|客户|需求|订单|终端|售价|传导", _re.I)
    upstream = [item for item in items if upstream_terms.search(f"{item.get('title', '')} {item.get('snippet', '')}")]
    downstream = [item for item in items if downstream_terms.search(f"{item.get('title', '')} {item.get('snippet', '')}")]
    return {
        "symbol": str(symbol or "").strip(),
        "name": display,
        "market": (market or "").upper(),
        "items": items,
        "upstream_count": len(upstream),
        "downstream_count": len(downstream),
        "queries": ["上游/原材料/供应商", "下游/客户/需求/价格传导"],
        "source": "稻草财经产业链资料",
        "note": (
            "已检索上下游关键词；关系仍需以公司公告、合同/客户披露或行业数据复核。"
            if items else "暂无上下游相关资料命中，不对供应链关系作推断。"
        ),
    }


register_tool(AgentTool(
    name="get_supply_chain_context",
    description=(
        "检索站内与公司上游原材料、供应商、下游客户、需求和价格传导相关的资料。"
        "返回的是可核验线索与命中数，不会凭常识臆测供应链关系；最终判断必须回到公告/财报/行业数据。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "股票代码"},
            "market": {"type": "string", "enum": ["US", "CN", "HK"]},
            "name": {"type": "string", "description": "可选，公司名称"},
        },
        "required": ["symbol"],
    },
    handler=_tool_get_supply_chain_context,
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
        "核实卖方机构对该股的覆盖与最新观点。**仅 A股有较完整的券商研报覆盖；港股/美股基本为空，对港美股不要反复空跑本工具**。"
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


async def _tool_get_market_structure() -> Any:
    """A股市场结构硬数据：指数涨跌/两市成交额vs昨日/涨跌家数/涨停跌停/板块分布+主力净额/连板梯队。
    盘中问也能拿到当时的真实快照。回答『今天股市为什么这样走/量能如何/赚钱效应』必须先取本工具。"""
    from .ashare_review import gather_market_structure
    d = await gather_market_structure()
    # 有任一硬数据块就算可用（各部分独立降级）
    if not (d.get("indices") or d.get("breadth", {}).get("total") or d.get("turnover") or d.get("sectors_top")):
        return None
    d["source"] = d.get("source") or "公开 A 股行情快照"
    d["retrieved_at_beijing"] = d.get("retrieved_at_beijing") or d.get("generated_at")
    return d


register_tool(AgentTool(
    name="get_market_structure",
    description=(
        "获取 A股市场结构【硬数据快照】：①主要指数收盘点位与涨跌幅（上证/深成/创业板/沪深300/科创50/北证50）"
        "②两市成交额（亿元）及对比昨日增减 ③全市场涨跌家数（涨/跌/平）④涨停/跌停家数 ⑤行业板块领涨领跌及"
        "主力资金净额（亿元）⑥涨停连板梯队（最高几板/龙头股）。盘中查询为当时快照。"
        "回答『今天股市为什么这样走』『今天量能/成交如何』『市场赚钱效应』『涨跌家数/涨停数』"
        "『板块怎么轮动』等大盘盘面问题时，**必须先调本工具拿官方行情数字**，与资讯/复盘叙述交叉印证后作答。"
    ),
    parameters={"type": "object", "properties": {}},
    handler=_tool_get_market_structure,
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


async def _tool_get_institution_notes(
    query: str = "", limit: int = 8, days: int = 30, scan_limit: int = 500,
) -> Any:
    """检索稻草财经【机构纪要】信息流。

    机构纪要由独立的知识星球流模块提供，和研报 PDF、站内文章是不同内容类型。
    这里只投影标题、正文摘要、日期和标签，避免把第三方帖子链接/图片直接塞进模型上下文。
    上游不可用时返回空结果，由 Harness 在研究缺口中明确标注，不阻断其余公开数据。
    """
    from .zsxq_stream import fetch_stream, recent_share_topics

    q = str(query or "").strip()[:80]
    n = max(1, min(int(limit or 8), 500))
    scan_cap = max(n, min(int(scan_limit or 500), 1000))
    cutoff, since_iso = _recent_cutoff(days)
    raw_items: list[dict[str, Any]] = []
    before = ""
    pages = 0
    scanned = 0
    complete = False
    unknown_dates = 0
    fallback_used = False
    # Explicitly page the ZSXQ cursor until the requested cutoff.  The old
    # one-page call was fast but could silently omit older notes on busy days.
    while pages < 30 and len(raw_items) < scan_cap:
        payload: dict[str, Any] | None = None
        try:
            payload = await fetch_stream(
                keyword=q,
                limit=min(40, max(n, 20)),
                end_time=before,
                use_cache=(pages == 0),
            )
        except Exception:  # noqa: BLE001 - 机构纪要是可选资料源，不拖垮深度研判
            payload = None
        page_items = (payload or {}).get("items") if isinstance(payload, dict) else None
        if not isinstance(page_items, list):
            # 服务器重启或工作台暂不可用时，仍尽量使用已经公开落库的最近摘要。
            if pages == 0:
                fallback_used = True
                page_items = recent_share_topics(limit=min(scan_cap, 500))
            else:
                page_items = []
        if not page_items:
            complete = True
            break
        pages += 1
        scanned += len(page_items)
        raw_items.extend(item for item in page_items if isinstance(item, dict))
        oldest_raw = page_items[-1] if isinstance(page_items[-1], dict) else {}
        oldest = str(oldest_raw.get("create_time") or oldest_raw.get("date") or "")
        oldest_dt = _parse_source_datetime(oldest)
        next_before = str((payload or {}).get("next_before") or "").strip() if isinstance(payload, dict) else ""
        has_more = bool((payload or {}).get("has_more")) if isinstance(payload, dict) else False
        if fallback_used:
            # Persisted share summaries are a recovery snapshot, not proof that
            # the upstream two-day window was fully scanned.
            complete = False
            break
        if (oldest_dt and oldest_dt < cutoff) or not has_more or not next_before:
            complete = bool(oldest_dt and oldest_dt < cutoff) or not has_more
            break
        before = next_before

    out: list[dict[str, Any]] = []
    # 代码查询必须按数字边界匹配，避免 00148 误命中 300148（天舟文化）。
    # 多个关键词采用 OR 语义，兼容“代码 + 公司名”的自然提问。
    query_tokens = [t for t in _re.split(r"[\s,，、/|]+", q.casefold()) if t]

    def _matches_query(hay: str) -> bool:
        if not query_tokens:
            return True
        for token in query_tokens:
            if token.isdigit():
                variants = {token}
                if len(token) < 5:
                    variants.add(token.zfill(5))
                if len(token) < 6:
                    variants.add(token.zfill(6))
                if any(_re.search(rf"(?<!\d){_re.escape(v)}(?!\d)", hay) for v in variants):
                    return True
            elif token in hay:
                return True
        return False

    seen: set[tuple[str, str]] = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        text = str(item.get("text") or item.get("lead") or "").strip()
        hay = f"{title} {text}".casefold()
        if not _matches_query(hay):
            continue
        date = str(item.get("date") or item.get("create_time") or "")[:32]
        parsed_date = _parse_source_datetime(date)
        if parsed_date is None:
            unknown_dates += 1
            continue
        if parsed_date < cutoff:
            continue
        dedupe_key = (title.casefold(), date)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        out.append({
            "title": title[:100],
            "summary": text[:420],
            "date": date,
            "tags": [str(t)[:30] for t in (item.get("tags") or [])[:8]],
        })
        if len(out) >= n:
            break
    return {
        "items": out,
        "count": len(out),
        "source": "稻草财经机构纪要",
        "coverage": {
            "window_days": max(1, min(int(days or 7), 30)),
            "since": since_iso,
            "scanned_count": scanned,
            "matched_count": len(out),
            "pages": pages,
            "complete": bool(complete),
            "unknown_dates": unknown_dates,
            "fallback_used": fallback_used,
        },
    }


register_tool(AgentTool(
    name="get_institution_notes",
    description=(
        "检索稻草财经独立的【机构纪要】模块（调研纪要/电话会纪要/动态点评）。"
        "它与快讯、文章、研报分开取数；返回标题、正文摘要、日期和标签。"
        "没有命中时必须如实标注，不得把研报或快讯冒充机构纪要。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "股票代码/名称或关键词；留空取最近纪要"},
            "limit": {"type": "integer", "description": "最多返回条数，默认 8（上限 500）"},
            "days": {"type": "integer", "description": "严格回溯天数，默认 30；回答最近两天时传 2"},
            "scan_limit": {"type": "integer", "description": "最多扫描条数，默认 500，用于防止上游异常放大"},
        },
    },
    handler=_tool_get_institution_notes,
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
    raw_as_of = glob.get("generated_at") or ash.get("generated_at")
    as_of_beijing = raw_as_of
    if raw_as_of:
        try:
            parsed = _dt.fromisoformat(str(raw_as_of).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=_tz.utc)
            as_of_beijing = parsed.astimezone(_tz(_td(hours=8))).isoformat()
        except (ValueError, TypeError):
            pass
    return {
        "snapshot_generated_at_beijing": as_of_beijing,
        "quotes": items,
        "note": "价格快照（美股/港股/商品在其休市时段为最近一个交易日收盘价；snapshot_generated_at_beijing 是北京时间的抓取时刻，不是行情成交时刻）；只据 price 与 change_pct 陈述当前价位与今日涨跌，不得凭记忆断言历史新高/新低/创纪录。",
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
    terms = [t.casefold() for t in _re.split(r"[\s,，、/|]+", q) if len(t.strip()) >= 2]
    out: list[dict[str, Any]] = []
    for fg in (data.get("figures") or []):
        name = fg.get("name") or ""
        latest = [{"title": it.get("title", ""), "summary": (it.get("body") or "")[:220],
                   "date": it.get("published_at") or it.get("reported_date")}
                  for it in (fg.get("items") or [])[:4]]
        if q:
            # 深度研判传入“代码 + 公司名”。名人资料的相关标的通常出现在帖子标题/正文，
            # 不一定出现在人物姓名里，所以按任一有效词过滤，而不是要求整句精确命中。
            identity = f"{name} {fg.get('org') or ''} {fg.get('role') or ''}".casefold()
            matched = [item for item in latest if any(
                term in f"{item.get('title') or ''} {item.get('summary') or ''}".casefold()
                for term in terms
            )]
            if not matched and not any(term in identity for term in terms):
                continue
            latest = matched or latest
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
