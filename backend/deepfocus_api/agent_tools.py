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

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Optional

from .consensus_source import fetch_analyst_consensus
from .eastmoney_data import fetch_eastmoney_earnings, fetch_fund_flow
from .market_data import fetch_market_quotes
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
) -> dict[str, Any]:
    """执行一个工具调用，永不抛出：成功 {"ok":True,"data":...}，失败 {"ok":False,"error":...}。

    extra_tools 优先于静态注册表（同名时动态覆盖），便于注入本次运行的 MCP 工具。
    """
    tool = (extra_tools or {}).get(name) or TOOL_REGISTRY.get(name)
    if tool is None:
        return {"ok": False, "error": f"未知工具：{name}"}
    try:
        data = await tool.handler(**(arguments or {}))
    except TypeError as exc:
        return {"ok": False, "error": f"参数不合法：{exc}"}
    except Exception as exc:  # noqa: BLE001 - 工具失败不能拖垮 agent 循环
        return {"ok": False, "error": f"取数失败：{exc}"}
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
    resp = await fetch_market_quotes([symbol])
    quotes = [q.model_dump() for q in resp.quotes]
    return {"quotes": quotes, "provider": resp.provider, "warnings": resp.warnings[:3]}


async def _tool_get_financials(symbol: str, market: Optional[str] = None) -> Any:
    return await fetch_eastmoney_earnings(symbol, market)


async def _tool_get_fund_flow(symbol: str, market: Optional[str] = None) -> Any:
    return await fetch_fund_flow(symbol, market)


async def _tool_get_valuation(symbol: str, market: Optional[str] = None) -> Any:
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
