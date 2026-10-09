"""DeepFocus 远程 MCP 服务（Streamable HTTP，挂载于 POST /api/mcp）。

把平台自有内容与 AI 问答以 MCP 工具形式开放给外部客户端（Claude Desktop、Cursor、
Claude Code、Cherry Studio 等）。与内部端点（/mcp，mcp_local）的区别：
- 鉴权：个人接入令牌（dfm_，mcp_tokens 签发），优先 Authorization: Bearer，
  兼容 ?token=（部分客户端自定义连接器无法携带 Header；令牌会进访问日志，文档中已提示风险）。
- 会员墙：AI 问答配额与网页端同一闸（_check_agent_quota：会员/管理员无限，非会员每天 10 次）。
- 版权红线与合作方 API（/api/v1）一致：只开放自有内容——复盘/速判卡/资讯流/研报元数据/题材；
  第三方版权内容（投行研报原文、聚合文章全文）绝不在此暴露，速判卡硬编码 use_ifind=False。

main 的符号一律在调用时懒导入：本模块在 main 启动期被导入，顶层 import 会成环。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from . import mcp_oauth, mcp_tokens
from .mcp_local import PROTOCOL_VERSION

SERVER_NAME = "稻草财经 MCP"
SERVER_VERSION = "1.0.0"

_ASK_MAX_CHARS = 4000

TOOLS: list[dict[str, Any]] = [
    {
        "name": "ping",
        "title": "连接探针",
        "description": "验证稻草财经 MCP 在线，并返回当前令牌绑定的账号与会员状态。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "search_market_symbols",
        "title": "搜索标的",
        "description": "按名称、代码或关键词搜索股票标的（A股/港股/美股）。只读。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "股票名称、代码或关键词"},
                "market": {"type": "string", "description": "可选：US、CN 或 HK"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_market_quotes",
        "title": "获取行情",
        "description": "获取一个或多个标的的公开行情快照（价格/涨跌幅/成交量）。只读。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbols": {"type": "array", "items": {"type": "string"},
                            "description": "标的代码列表，例如 [\"AAPL\", \"600519.SH\"]"}
            },
            "required": ["symbols"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_review_today",
        "title": "今日复盘",
        "description": "最新一期稻草财经 A股复盘（盘中=午盘版，收盘后=收盘版；一句话盘面/板块/明日展望）。自有内容。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_review",
        "title": "指定日期复盘",
        "description": "某一天（YYYY-MM-DD）的完整 A股复盘。",
        "inputSchema": {
            "type": "object",
            "properties": {"date": {"type": "string", "description": "日期，格式 YYYY-MM-DD"}},
            "required": ["date"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_reviews",
        "title": "历史复盘列表",
        "description": "历史 A股复盘列表（轻量摘要，新→旧）。",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "description": "条数，默认 30，上限 120"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "get_stock_verdict",
        "title": "个股速判卡",
        "description": "稻草财经确定性引擎生成的个股证据速判卡（多维证据+信号灯+可信度），非 LLM 叙述。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "标的代码，如 AAPL / 00700 / 600519"},
                "name": {"type": "string", "description": "可选：标的名称，辅助消歧"},
                "market": {"type": "string", "description": "可选：市场 US/CN/HK"},
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
    {
        "name": "search_news",
        "title": "搜资讯",
        "description": "稻草财经资讯流检索：快讯/文章，可按标的、关键词过滤。只含自有条目。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "可选：快讯 或 文章，留空=全部"},
                "symbol": {"type": "string", "description": "可选：按标的过滤"},
                "q": {"type": "string", "description": "可选：关键词"},
                "limit": {"type": "integer", "description": "条数，默认 30，上限 200"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "search_research",
        "title": "搜研报",
        "description": "海外投行研报检索（标题/机构/日期/预览链接等元数据）。原文受版权保护不在此提供。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "q": {"type": "string", "description": "检索关键词，留空=最新"},
                "limit": {"type": "integer", "description": "条数，默认 30，上限 200"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_theme_stocks",
        "title": "题材→受益股",
        "description": "A股题材/行业板块→成分股（按涨跌幅降序，确定性板块归属，非荐股）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "theme": {"type": "string", "description": "题材/行业名，如 白酒、半导体、人工智能"},
                "code": {"type": "string", "description": "可选：东财板块代码 BKxxxx"},
                "limit": {"type": "integer", "description": "条数，默认 40，上限 80"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_stock_themes",
        "title": "个股→题材反查",
        "description": "查询某标的所属行业/概念板块。",
        "inputSchema": {
            "type": "object",
            "properties": {"symbol": {"type": "string", "description": "标的代码"}},
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
    {
        "name": "search_stock_reports",
        "title": "个股券商研报",
        "description": "查询某标的近两年的券商研报列表（标题/机构/日期/评级等元数据）。研报原文受版权保护不在此提供。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "标的代码，如 600519 / 00700 / AAPL"},
                "market": {"type": "string", "description": "可选：市场 CN/HK/US，辅助消歧"},
                "limit": {"type": "integer", "description": "条数，默认 20，上限 50"},
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
    {
        "name": "search_minutes",
        "title": "机构纪要检索",
        "description": "检索机构调研纪要/动态点评（稻草财经自有信息流，含正文、日期、标签）。keyword 留空返回最新。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "关键词，如 公司名/题材；留空=最新"},
                "group": {"type": "string", "description": "可选：星球分组 id（search 一次后可从返回的 groups 里取）"},
                "limit": {"type": "integer", "description": "条数，默认 10，上限 20"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_minutes_sentiment",
        "title": "纪要多空统计",
        "description": "当日机构纪要的多空倾向统计（AI 判定：看多/看空/中性比例 + 板块分布，样本量如实披露）。",
        "inputSchema": {
            "type": "object",
            "properties": {"force": {"type": "boolean", "description": "可选：跳过缓存强刷（默认 20 分钟缓存）"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "get_kline",
        "title": "K线/分时",
        "description": "个股 K 线（默认日线 OHLC，A股为主）或当日分时（period=1m）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "标的代码，如 600519 / AAPL"},
                "market": {"type": "string", "description": "可选：市场 CN/HK/US"},
                "points": {"type": "integer", "description": "K线根数，默认 120，上限 320"},
                "period": {"type": "string", "description": "留空=日线；1m=当日分时"},
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_market_dashboard",
        "title": "大盘指标盘",
        "description": "大盘核心指标盘（指数/宽度/情绪等汇总面板）。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_risk_radar",
        "title": "市场风险雷达",
        "description": "A/H/美股市值前 N 风险预警（只读公开数据，确定性规则）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "markets": {"type": "string", "description": "可选：逗号分隔，默认 CN,HK,US"},
                "limit": {"type": "integer", "description": "每市场条数，默认 10，上限 20"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_theme_boards",
        "title": "概念板块涨幅榜",
        "description": "A股概念板块当日涨幅榜（看哪些题材主线在动，确定性归属，非荐股）。",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "description": "条数，默认 40，上限 80"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "get_limit_up_ladder",
        "title": "涨停天梯",
        "description": "A股涨停天梯/连板梯队（N天M板/所属行业/炸板，东财涨停池，纯事实非荐股）。",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "description": "条数，默认 60，上限 120"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "get_dragon_tiger",
        "title": "龙虎榜",
        "description": "某交易日 A股龙虎榜全榜单（date 留空=最近交易日，交易所公开事实）。",
        "inputSchema": {
            "type": "object",
            "properties": {"date": {"type": "string", "description": "可选：YYYY-MM-DD"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "get_market_calendar",
        "title": "A股日历",
        "description": "A股日历：解禁/新股/财报预约披露等确定性事件（默认未来 7 天，上限 30 天）。",
        "inputSchema": {
            "type": "object",
            "properties": {"days": {"type": "integer", "description": "未来天数，默认 7，上限 30"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "universal_search",
        "title": "统一搜索",
        "description": "跨库聚合搜索：股票/快讯文章/研报/术语/板块 一次返回（各路独立容错）。",
        "inputSchema": {
            "type": "object",
            "properties": {"q": {"type": "string", "description": "关键词，如 白酒 / 茅台 / semiconductor"}},
            "required": ["q"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_headlines",
        "title": "AI 今日头条",
        "description": "AI 评选的今日头条（快讯/文章/研报各至多 3 条，附「为什么重要」）。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_track_record",
        "title": "平台战绩",
        "description": "「我们提前发现的」平台战绩：近 30 天经 AI 判定验证的命中汇总与近期明细。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_ai_fund_snapshot",
        "title": "AI 模拟盘快照",
        "description": "AI 模拟盘全貌（净值/收益/持仓盯市/近期交易理由/净值曲线）；strategy 可选竞技场选手 id。",
        "inputSchema": {
            "type": "object",
            "properties": {"strategy": {"type": "string", "description": "可选：选手 fund_id，留空=主账户"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "ask_ai",
        "title": "AI 投研问答",
        "description": "向稻草财经投研 AI 提问（会调工具取真实数据：行情/复盘/研报/财报等）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "投研问题，如『贵州茅台当前值得关注吗』"},
                "deep": {"type": "boolean", "description": "可选：深度模式（更慢更全），默认快速"},
            },
            "required": ["question"],
            "additionalProperties": False,
        },
    },
]


def _jsonable(value: Any) -> Any:
    """递归转 JSON 安全类型。比 mcp_local 版多处理 datetime/Decimal——速判卡/资讯条目
    的 model_dump()（python mode）会带 datetime，json.dumps 直接炸 500（已在线上踩过）。"""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


# 浏览器侧 MCP 客户端（claude.ai web 等）跨域访问需要 CORS；MCP 规范要求授权相关头可暴露。
_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
    "Access-Control-Allow-Headers": "Authorization, Content-Type, Mcp-Session-Id, Mcp-Protocol-Version, Last-Event-ID",
    "Access-Control-Expose-Headers": "Mcp-Session-Id, WWW-Authenticate",
}


def _www_authenticate() -> str:
    """RFC 9728：401 时指引客户端去做 OAuth 发现（MCP 客户端据此自动弹出浏览器授权）。"""
    return f'Bearer resource_metadata="{mcp_oauth.ISSUER}{mcp_oauth.RS_METADATA_PATH}"'


def _rpc_error(request_id: Any, code: int, message: str, session_id: str = "") -> JSONResponse:
    headers = dict(_CORS_HEADERS)
    headers["Mcp-Session-Id"] = session_id or str(uuid.uuid4())
    return JSONResponse(
        {"jsonrpc": "2.0", "id": request_id, "error": {"code": int(code), "message": message}},
        status_code=200,
        headers=headers,
    )


def _result(request_id: Any, result: dict[str, Any], session_id: str) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": request_id, "result": result},
        headers={**_CORS_HEADERS, "Mcp-Session-Id": session_id},
    )


def _text_result(value: Any) -> dict[str, Any]:
    payload = _jsonable(value)
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        "structuredContent": {"data": payload},
        "isError": False,
    }


def _http_error(status: int, detail: str, extra: Optional[dict] = None) -> JSONResponse:
    headers = dict(_CORS_HEADERS)
    if status == 401:
        headers["WWW-Authenticate"] = _www_authenticate()
    if extra:
        headers.update(extra)
    return JSONResponse({"detail": detail}, status_code=status, headers=headers)


def _bearer_token(request: Request) -> str:
    header = request.headers.get("authorization") or ""
    parts = header.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    # 兜底：部分客户端的自定义连接器无法配置 Header，允许 ?token=（会进访问日志，控制台已提示）。
    return (request.query_params.get("token") or "").strip()


def _membership_tier(user_out) -> str:
    return ((getattr(user_out, "membership", None) or {}).get("tier")) or "trial"


def _daily_quota_for(user_out) -> int:
    if str(getattr(user_out, "role", "") or "").lower() == "admin":
        return 0
    return 0 if _membership_tier(user_out) in ("premium", "lifetime") else mcp_tokens.TRIAL_DAILY_QUOTA


# --------------------------------------------------------------------------- #
# 工具实现（全部只读；main 符号懒导入防循环）
# --------------------------------------------------------------------------- #
async def _tool_ping(user_out) -> dict:
    return {
        "ok": True,
        "service": SERVER_NAME,
        "protocolVersion": PROTOCOL_VERSION,
        "account": getattr(user_out, "username", ""),
        "membership": getattr(user_out, "membership", None),
    }


async def _tool_search_symbols(args: dict) -> Any:
    from .market_data import search_market_symbols

    query = str(args.get("query") or "").strip()
    if not query:
        raise ValueError("query 不能为空")
    return await search_market_symbols(query, market=str(args.get("market") or "") or None)


async def _tool_quotes(args: dict) -> Any:
    from .market_data import fetch_market_quotes

    symbols = args.get("symbols")
    if isinstance(symbols, str):
        symbols = [s.strip() for s in symbols.split(",") if s.strip()]
    if not isinstance(symbols, list) or not symbols:
        raise ValueError("symbols 必须为非空数组")
    return await fetch_market_quotes([str(s) for s in symbols[:30]])


async def _tool_review_today() -> Any:
    from . import ashare_review

    rv = ashare_review.latest_review()
    return {"exists": bool(rv), "review": rv} if rv else {"exists": False}


async def _tool_review(args: dict) -> Any:
    import re

    from . import ashare_review

    date = str(args.get("date") or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise ValueError("date 须为 YYYY-MM-DD")
    rv = ashare_review.review_for_date(date)
    if not rv:
        return {"exists": False, "detail": "该日期暂无复盘"}
    return {"exists": True, "review": rv}


async def _tool_reviews(args: dict) -> Any:
    from . import ashare_review

    try:
        limit = max(1, min(int(args.get("limit") or 30), 120))
    except (TypeError, ValueError):
        limit = 30
    return {"items": ashare_review.list_reviews(limit=limit)}


async def _tool_verdict(args: dict) -> Any:
    from .main import _build_stock_tear_sheet_core

    sym = str(args.get("symbol") or "").strip().upper()[:16]
    if not sym:
        raise ValueError("symbol 不能为空")
    sheet = await _build_stock_tear_sheet_core(
        sym, name=str(args.get("name") or "").strip(), market=str(args.get("market") or "").strip(),
        use_ifind=False,  # iFinD 数据禁止裸转分发，绝不进对外通道
    )
    return sheet.model_dump() if hasattr(sheet, "model_dump") else dict(sheet)


async def _tool_news(args: dict) -> Any:
    from .realtime_messages import list_realtime_messages

    try:
        limit = max(1, min(int(args.get("limit") or 30), 200))
    except (TypeError, ValueError):
        limit = 30
    msgs = list_realtime_messages(
        topic=(str(args.get("topic") or "").strip() or None),
        symbol=(str(args.get("symbol") or "").strip() or None),
        q=(str(args.get("q") or "").strip() or None),
        limit=limit,
    )
    items = [m.model_dump() if hasattr(m, "model_dump") else dict(m) for m in msgs]
    return {"count": len(items), "messages": items}


async def _tool_research(args: dict) -> Any:
    from .research_wire import fetch_research_wire_online

    try:
        limit = max(1, min(int(args.get("limit") or 30), 200))
    except (TypeError, ValueError):
        limit = 30
    data = await fetch_research_wire_online(limit=limit, query=str(args.get("q") or "").strip())
    items = data.get("items", []) if isinstance(data, dict) else []
    return {"count": len(items), "items": items}


async def _tool_theme_stocks(args: dict) -> Any:
    from .theme_navigation import _board_name_pairs, fetch_board_stocks, find_board_by_name

    theme = str(args.get("theme") or "").strip()
    code = str(args.get("code") or "").strip().upper()
    try:
        limit = max(1, min(int(args.get("limit") or 40), 80))
    except (TypeError, ValueError):
        limit = 40
    if code:
        if not code.startswith("BK") or not code[2:].isdigit():
            raise ValueError("code 须形如 BK1175")
        pairs = await _board_name_pairs()
        board: Optional[dict] = {"code": code, "name": next((p["name"] for p in pairs if p["code"] == code), code)}
    elif theme:
        board = await find_board_by_name(theme)
    else:
        raise ValueError("需提供 theme(题材名) 或 code(BKxxxx)")
    if not board:
        return {"board": None, "stocks": [], "count": 0, "detail": "未找到对应题材/行业板块"}
    stocks = await fetch_board_stocks(board["code"], limit=limit)
    return {"board": board, "stocks": stocks, "count": len(stocks)}


async def _tool_stock_themes(args: dict) -> Any:
    from .theme_navigation import fetch_stock_themes

    sym = str(args.get("symbol") or "").strip()
    if not sym:
        raise ValueError("symbol 不能为空")
    return await fetch_stock_themes(sym) or {"symbol": sym, "name": "", "industry": "", "board": ""}


async def _tool_stock_reports(args: dict) -> Any:
    from .eastmoney_reports import query_eastmoney_reports

    sym = str(args.get("symbol") or "").strip()
    if not sym:
        raise ValueError("symbol 不能为空")
    try:
        limit = max(1, min(int(args.get("limit") or 20), 50))
    except (TypeError, ValueError):
        limit = 20
    rows, warnings = await query_eastmoney_reports(
        code=sym, market=(str(args.get("market") or "").strip() or None), page_size=limit,
    )
    items = [
        {"title": r.get("title"), "org": r.get("org"), "date": r.get("date"),
         "rating": r.get("rating"), "stock_name": r.get("stock_name"),
         "stock_code": r.get("stock_code"), "pdf_url": r.get("pdf_url")}
        for r in rows if isinstance(r, dict)
    ]
    return {"count": len(items), "items": items, "warnings": warnings}


async def _tool_search_minutes(args: dict) -> Any:
    from .zsxq_stream import fetch_stream

    keyword = str(args.get("keyword") or "").strip()[:40]
    try:
        limit = max(1, min(int(args.get("limit") or 10), 20))
    except (TypeError, ValueError):
        limit = 10
    data = await fetch_stream(
        group=str(args.get("group") or "").strip(), keyword=keyword, limit=limit, use_cache=True,
    )
    items = []
    for it in data.get("items") or []:
        if not isinstance(it, dict):
            continue
        text = str(it.get("text") or "")
        items.append({
            "id": it.get("id"),
            "title": it.get("title"),
            "text": text[:3000] + ("…（正文截断）" if len(text) > 3000 else ""),
            "date": it.get("date"),
            "create_time": it.get("create_time"),
            "tags": it.get("tags") or [],
            "comments_count": it.get("comments_count"),
        })
    return {"count": len(items), "items": items, "groups": data.get("groups") or [],
            "has_more": bool(data.get("has_more")), "next_before": data.get("next_before")}


async def _tool_minutes_sentiment(args: dict) -> Any:
    from .note_sentiment import get_daily_sentiment

    return await get_daily_sentiment(force=bool(args.get("force")))


async def _tool_kline(args: dict) -> Any:
    from .main import market_kline

    sym = str(args.get("symbol") or "").strip()
    if not sym:
        raise ValueError("symbol 不能为空")
    try:
        points = max(20, min(int(args.get("points") or 120), 320))
    except (TypeError, ValueError):
        points = 120
    return await market_kline(
        sym, market=str(args.get("market") or "").strip(),
        points=points, period=str(args.get("period") or "").strip(),
    )


async def _tool_dashboard() -> Any:
    from .main import fetch_market_dashboard

    return await fetch_market_dashboard()


async def _tool_risk_radar(args: dict) -> Any:
    from .market_risk_radar import build_market_risk_radar

    markets = [m.strip().upper() for m in str(args.get("markets") or "CN,HK,US").split(",") if m.strip()]
    try:
        limit = max(1, min(int(args.get("limit") or 10), 20))
    except (TypeError, ValueError):
        limit = 10
    return await build_market_risk_radar(markets, limit=limit)


async def _tool_theme_boards(args: dict) -> Any:
    from .theme_navigation import fetch_concept_boards

    try:
        limit = max(1, min(int(args.get("limit") or 40), 80))
    except (TypeError, ValueError):
        limit = 40
    boards = await fetch_concept_boards(limit=limit)
    return {"boards": boards or [], "count": len(boards or [])}


async def _tool_limit_up(args: dict) -> Any:
    from .theme_navigation import fetch_limit_up_ladder

    try:
        limit = max(1, min(int(args.get("limit") or 60), 120))
    except (TypeError, ValueError):
        limit = 60
    return await fetch_limit_up_ladder(limit=limit)


async def _tool_dragon_tiger(args: dict) -> Any:
    import re

    from .dragon_tiger import fetch_daily_billboard

    date = str(args.get("date") or "").strip()
    if date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise ValueError("date 须为 YYYY-MM-DD 或留空")
    data = await fetch_daily_billboard(date)
    return {"data": data, "note": "交易所公开龙虎榜数据，仅为事实呈现，不构成投资建议"}


async def _tool_calendar(args: dict) -> Any:
    from .cn_calendar import fetch_cn_calendar

    try:
        days = max(1, min(int(args.get("days") or 7), 30))
    except (TypeError, ValueError):
        days = 7
    return await fetch_cn_calendar(days=days)


async def _tool_usearch(args: dict) -> Any:
    import asyncio

    from .main import (
        _usearch_boards,
        _usearch_news,
        _usearch_reports,
        _usearch_stocks,
        _usearch_terms,
    )

    q = str(args.get("q") or "").strip()[:60]
    if not q:
        raise ValueError("q 不能为空")

    async def _safe(coro):
        try:
            return await coro
        except Exception:  # noqa: BLE001 - 单路失败不拖垮聚合
            return []

    stocks, news, reports, terms, boards = await asyncio.wait_for(
        asyncio.gather(
            _safe(_usearch_stocks(q)),
            _safe(_usearch_news(q)),
            _safe(_usearch_reports(q)),
            _safe(_usearch_terms(q)),
            _safe(_usearch_boards(q)),
        ),
        timeout=10,
    )
    return {"q": q, "stocks": stocks, "news": news, "reports": reports, "terms": terms, "boards": boards}


async def _tool_headlines() -> Any:
    from . import main as _main_mod

    hl = getattr(_main_mod, "_HEADLINES", None) or {}
    cap = lambda seq: (seq or [])[-5:]  # noqa: E731 - 每类至多 5 条，控载荷
    return {"kx": cap(hl.get("kx")), "wz": cap(hl.get("wz")), "yb": cap(hl.get("yb")),
            "generated_at": hl.get("generated_at") or ""}


async def _tool_track_record() -> Any:
    from . import track_record

    hits = track_record._collect_hits(30)
    return {**track_record._summarize(hits), "days": 30, "recent": hits[:12]}


async def _tool_aifund(args: dict) -> Any:
    import asyncio

    from .main import _aifund_snapshot

    return await asyncio.to_thread(_aifund_snapshot, str(args.get("strategy") or "").strip())


async def _tool_ask_ai(args: dict, user_out) -> dict:
    question = str(args.get("question") or "").strip()[:_ASK_MAX_CHARS]
    if not question:
        raise ValueError("question 不能为空")
    deep = bool(args.get("deep"))
    from .main import (
        OrchestratorChatRequest,
        _check_agent_quota,
        _record_agent_chain,
        _route_orchestrator_chat,
        metrics_incr,
    )
    from . import agent_tools as _agent_tools

    # 会员墙：与网页端同一闸（会员/管理员无限；非会员每天 10 次，超额 402）。
    claims = {"sub": getattr(user_out, "id", ""), "role": getattr(user_out, "role", "")}
    quota_key = _check_agent_quota(claims, None)
    started = time.perf_counter()
    uname = str(getattr(user_out, "username", "") or "")
    tok = _agent_tools._BINDING_USER.set(uname)
    try:
        # Mac/生产 main.py 有分叉：生产版 _route_orchestrator_chat 无 tool_max_rounds 参数。
        # 按运行时签名过滤 kwargs，两边都能跑。
        import inspect as _inspect

        kwargs: dict[str, Any] = {
            "tool_timeout": 55.0 if deep else 45.0,
            "force_research": deep,
            "skip_professional": True,
        }
        if "tool_max_rounds" in _inspect.signature(_route_orchestrator_chat).parameters:
            kwargs["tool_max_rounds"] = 6 if deep else 4
        routed = await _route_orchestrator_chat(
            OrchestratorChatRequest(
                message=question,
                stock=None,
                attached_files=[],
                reasoning_mode="thinking",
            ),
            _ifind=False,  # iFinD 数据不进对外通道
            **kwargs,
        )
    except Exception as exc:  # noqa: BLE001
        _record_agent_chain(mode="mcp", route="error", message=question, trace=[],
                            elapsed_ms=int((time.perf_counter() - started) * 1000),
                            answer="", ok=False, error=str(exc))
        return {"ok": False, "answer": "", "error": str(exc)[:200]}
    finally:
        _agent_tools._BINDING_USER.reset(tok)
    answer = (getattr(routed, "content", "") or "").strip()
    if quota_key:
        metrics_incr(quota_key)  # 与网页端一致：成功回答才扣次
    from .compliance import ai_label
    from .privacy_guard import scrub_internal_text

    _record_agent_chain(mode="mcp", route="orchestrator", message=question, trace=[],
                        elapsed_ms=int((time.perf_counter() - started) * 1000),
                        answer=answer, ok=bool(answer))
    return {
        "ok": bool(answer),
        "answer": ai_label(scrub_internal_text(answer), brief=True),
        "title": str(getattr(routed, "title", "") or ""),
        "chips": [str(c) for c in (getattr(routed, "chips", None) or [])][:6],
        "confidence": float(getattr(routed, "confidence", 0.0) or 0.0),
        "disclaimer": "内容仅供研究参考，不构成投资建议。",
    }


# --------------------------------------------------------------------------- #
# JSON-RPC 入口
# --------------------------------------------------------------------------- #
async def handle_remote_mcp_request(request: Request) -> JSONResponse:
    ip = request.client.host if request.client else "?"
    if mcp_tokens.auth_fail_blocked(ip):
        return _http_error(429, "无效请求过多，请稍后再试")

    token = _bearer_token(request)
    rec = mcp_tokens.verify_token(token)
    oauth_user: Optional[dict] = None
    if rec is None:
        # 非 dfm_ 令牌 → 尝试 OAuth access token（JWT typ=mcp，浏览器授权流签发）
        oauth_user = mcp_oauth.verify_access_token(token)
    if rec is None and oauth_user is None:
        mcp_tokens.register_auth_fail(ip)
        return _http_error(401, "无效或已过期的接入凭证。首次使用请在客户端里直接添加本地址，"
                               "将自动跳转浏览器完成登录授权；或到 daocaijing.com/api/mcp/setup 创建个人令牌")
    if rec is not None:
        token_hash, token_prefix, user_id = rec["_token_hash"], rec.get("token_prefix", ""), str(rec["user_id"])
    else:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()  # OAuth JWT 无库内摘要，哈希原文作限流/计量键
        token_prefix = f"oauth:{str(oauth_user.get('username') or '')[:8]}"
        user_id = str(oauth_user["user_id"])

    # 令牌绑定的用户须仍然有效（停用/注销立即失效）。
    from .auth import get_user_out_by_id

    user_out = get_user_out_by_id(user_id)
    if user_out is None or not getattr(user_out, "is_active", False):
        return _http_error(401, "令牌绑定的账号不可用")

    if not mcp_tokens.check_rate(token_hash):
        return _http_error(429, f"超出速率上限（{mcp_tokens.RATE_PER_MIN} 次/分钟），请稍后再试")
    quota = _daily_quota_for(user_out)
    if quota > 0 and mcp_tokens.today_count(token_hash) >= quota:
        return _http_error(429, f"已达今日调用上限（{quota} 次/天，体验期用户）。开通尊享会员畅享不限次")

    try:
        payload = await request.json()
    except Exception:
        return _rpc_error(None, -32700, "Invalid JSON")
    if not isinstance(payload, dict):
        return _rpc_error(None, -32600, "Request must be a JSON object")

    request_id = payload.get("id")
    method = str(payload.get("method") or "")
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        return _rpc_error(request_id, -32602, "Params must be an object")

    # 通知（无 id）不回 JSON-RPC 结果；202 让 streamable-http 客户端满意。
    if request_id is None:
        return JSONResponse({}, status_code=202)

    session_id = request.headers.get("Mcp-Session-Id") or str(uuid.uuid4())

    if method == "initialize":
        return _result(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": (
                    "稻草财经（daocaijing.com）投研 MCP：A股复盘/个股速判卡/资讯/研报元数据/题材/行情等只读工具，"
                    "以及 ask_ai 投研问答（会员配额与网页端一致）。内容仅供研究参考，不构成投资建议。"
                ),
            },
            session_id,
        )
    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS}, session_id)
    if method in ("resources/list", "prompts/list"):
        return _result(request_id, {method.split("/")[0]: []}, session_id)
    if method != "tools/call":
        return _rpc_error(request_id, -32601, f"Method not found: {method}", session_id)

    tool_name = str(params.get("name") or "")
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        return _rpc_error(request_id, -32602, "Tool arguments must be an object", session_id)

    try:
        if tool_name == "ping":
            value = await _tool_ping(user_out)
        elif tool_name == "search_market_symbols":
            value = await _tool_search_symbols(arguments)
        elif tool_name == "get_market_quotes":
            value = await _tool_quotes(arguments)
        elif tool_name == "get_review_today":
            value = await _tool_review_today()
        elif tool_name == "get_review":
            value = await _tool_review(arguments)
        elif tool_name == "list_reviews":
            value = await _tool_reviews(arguments)
        elif tool_name == "get_stock_verdict":
            value = await _tool_verdict(arguments)
        elif tool_name == "search_news":
            value = await _tool_news(arguments)
        elif tool_name == "search_research":
            value = await _tool_research(arguments)
        elif tool_name == "get_theme_stocks":
            value = await _tool_theme_stocks(arguments)
        elif tool_name == "get_stock_themes":
            value = await _tool_stock_themes(arguments)
        elif tool_name == "get_kline":
            value = await _tool_kline(arguments)
        elif tool_name == "get_market_dashboard":
            value = await _tool_dashboard()
        elif tool_name == "get_risk_radar":
            value = await _tool_risk_radar(arguments)
        elif tool_name == "get_theme_boards":
            value = await _tool_theme_boards(arguments)
        elif tool_name == "get_limit_up_ladder":
            value = await _tool_limit_up(arguments)
        elif tool_name == "get_dragon_tiger":
            value = await _tool_dragon_tiger(arguments)
        elif tool_name == "get_market_calendar":
            value = await _tool_calendar(arguments)
        elif tool_name == "universal_search":
            value = await _tool_usearch(arguments)
        elif tool_name == "get_headlines":
            value = await _tool_headlines()
        elif tool_name == "get_track_record":
            value = await _tool_track_record()
        elif tool_name == "get_ai_fund_snapshot":
            value = await _tool_aifund(arguments)
        elif tool_name == "search_stock_reports":
            value = await _tool_stock_reports(arguments)
        elif tool_name == "search_minutes":
            value = await _tool_search_minutes(arguments)
        elif tool_name == "get_minutes_sentiment":
            value = await _tool_minutes_sentiment(arguments)
        elif tool_name == "ask_ai":
            value = await _tool_ask_ai(arguments, user_out)
        else:
            return _rpc_error(request_id, -32602, f"Unknown tool: {tool_name}", session_id)
    except ValueError as exc:
        return _result(request_id, {**_text_result({"error": str(exc)}), "isError": True}, session_id)
    except HTTPException as exc:  # 会员墙 402/403 等：把引导语原样给到客户端模型
        return _result(request_id, {**_text_result({"error": str(exc.detail or exc)}), "isError": True}, session_id)
    except Exception as exc:  # noqa: BLE001 - MCP 错误保持 JSON-RPC 形态返回给客户端模型
        return _result(request_id, {**_text_result({"error": f"服务暂时不可用：{exc}"}), "isError": True}, session_id)

    mcp_tokens.record_success(token_hash, token_prefix)
    try:
        return _result(request_id, _text_result(value), session_id)
    except Exception as exc:  # noqa: BLE001 - 序列化失败降级为文本错误，绝不 500
        return _result(request_id, {**_text_result({"error": f"结果序列化失败: {exc}"})}, session_id)


# --------------------------------------------------------------------------- #
# 自助接入控制台（GET /api/mcp/setup，公开静态页）
# 登录态由同源 localStorage['auth_token'] 提供（与站点 SPA 同一令牌），无需前端发版。
# --------------------------------------------------------------------------- #
_SETUP_PAGE_TMPL = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>稻草财经 MCP 接入控制台</title>
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
.ok{background:rgba(43,217,106,.08);border:1px solid rgba(43,217,106,.35);border-radius:8px;padding:10px 14px;margin:12px 0;font-size:13.5px}
table{border-collapse:collapse;width:100%;margin:8px 0;font-size:13px}th,td{border:1px solid var(--line);padding:6px 9px;text-align:left}th{color:var(--mute);font-weight:600}
button{background:var(--blue);border:none;color:#08111c;font-weight:700;border-radius:7px;padding:8px 14px;cursor:pointer;font-size:13.5px}
button.ghost{background:transparent;border:1px solid var(--line);color:var(--text)}
button.danger{background:transparent;border:1px solid rgba(255,107,107,.5);color:var(--red)}
input{background:#0c1018;border:1px solid var(--line);border-radius:7px;color:var(--text);padding:8px 10px;font-size:13.5px;width:220px}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.tok{font-family:ui-monospace,Menlo,monospace;background:rgba(43,217,106,.1);border:1px dashed rgba(43,217,106,.5);border-radius:7px;padding:8px 10px;word-break:break-all;color:var(--green);font-size:13px}
.mut{color:var(--mute);font-size:12.5px}.ft{margin-top:40px;color:var(--mute);font-size:12px;border-top:1px solid var(--line);padding-top:14px}
.tag{display:inline-block;border:1px solid var(--line);border-radius:5px;padding:0 8px;font-size:12px;color:var(--mute);margin-left:6px}
</style></head><body><div class="wrap">
<h1>稻草财经 MCP 接入控制台</h1>
<p class="sub">把稻草财经（daocaijing.com）的 A股复盘、个股速判卡、资讯、研报与 AI 投研问答，接入你自己的 AI 客户端（Claude Desktop、Claude Code、Cursor、Cherry Studio 等任何支持 MCP 的软件）。</p>
<div class="note">接入地址（MCP URL）：<code>__MCP_URL__</code>，传输方式 Streamable HTTP。需要你的<b>个人接入令牌</b>（下方创建），内容权限与会员权益同你的网页端账号。</div>

<h2>第一步 · 创建接入令牌</h2>
<div id="loginHint" class="note" style="display:none">尚未检测到登录态：请先 <a href="/" style="color:var(--blue)">打开稻草财经首页登录</a>，然后回到本页刷新。</div>
<div class="panel">
  <div class="row"><input id="tokName" placeholder="令牌备注（如：我的 Cursor）" maxlength="40"><button onclick="createTok()">创建令牌</button><button class="ghost" onclick="loadToks()">刷新列表</button></div>
  <div id="newTok" style="display:none" class="ok"><b>请立即保存（仅此一次完整显示）：</b><div class="tok" id="newTokVal"></div><button class="ghost" onclick="copyTok()">复制令牌</button> <span class="mut">令牌等同账号凭证，泄露请立即在下方撤销重发。</span></div>
  <div id="tokList"></div>
</div>

<h2>第二步 · 在你的 AI 客户端里添加</h2>
<p class="sub">把下面的令牌填进配置。推荐优先用 Header 方式（令牌不进 URL）。</p>
<div class="panel"><h3>Claude Code（命令行一把梭）</h3>
<pre id="cfgClaudeCode">claude mcp add --transport http daocaijing __MCP_URL__ --header "Authorization: Bearer &lt;你的令牌&gt;"</pre></div>
<div class="panel"><h3>Claude Desktop（设置 → 连接器 → 添加自定义连接器，直接粘贴 URL）</h3>
<pre id="cfgClaudeDesktop">__MCP_URL__?token=&lt;你的令牌&gt;</pre>
<span class="mut">自定义连接器无法携带 Header 时用 query 方式；令牌会进入服务端访问日志，介意请改用支持 Header 的客户端。</span></div>
<div class="panel"><h3>Cursor / 通用 MCP 客户端（JSON 配置，Header 方式）</h3>
<pre id="cfgJson">{
  "mcpServers": {
    "daocaijing": {
      "url": "__MCP_URL__",
      "headers": { "Authorization": "Bearer &lt;你的令牌&gt;" }
    }
  }
}</pre></div>
<div class="panel"><h3>curl 直连验证</h3>
<pre id="cfgCurl">curl -s __MCP_URL__ -H "Authorization: Bearer &lt;你的令牌&gt;" -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'</pre></div>

<h2>可用工具（26 个）</h2>
<table><tr><th>工具</th><th>说明</th></tr>
<tr><td><code>ping</code></td><td>连通探针，返回账号与会员状态</td></tr>
<tr><td><code>search_market_symbols</code> / <code>get_market_quotes</code></td><td>标的搜索 / 行情快照（A/H/美）</td></tr>
<tr><td><code>get_review_today</code> / <code>get_review</code> / <code>list_reviews</code></td><td>A股复盘（今日 / 指定日期 / 历史列表）</td></tr>
<tr><td><code>get_stock_verdict</code></td><td>个股证据速判卡（确定性引擎）</td></tr>
<tr><td><code>search_news</code> / <code>search_research</code> / <code>search_stock_reports</code></td><td>资讯流 / 投行研报元数据 / 个股券商研报（近两年，标题机构评级）</td></tr>
<tr><td><code>search_minutes</code> / <code>get_minutes_sentiment</code></td><td>机构纪要检索（含正文）/ 当日纪要多空统计</td></tr>
<tr><td><code>get_theme_stocks</code> / <code>get_stock_themes</code> / <code>get_theme_boards</code> / <code>get_limit_up_ladder</code></td><td>题材→受益股 / 题材反查 / 板块涨幅榜 / 涨停天梯</td></tr>
<tr><td><code>get_kline</code> / <code>get_market_dashboard</code> / <code>get_risk_radar</code> / <code>get_dragon_tiger</code> / <code>get_market_calendar</code></td><td>K线分时 / 大盘指标盘 / 风险雷达 / 龙虎榜 / A股日历</td></tr>
<tr><td><code>universal_search</code> / <code>get_headlines</code> / <code>get_track_record</code> / <code>get_ai_fund_snapshot</code></td><td>统一搜索 / AI头条 / 平台战绩 / AI模拟盘</td></tr>
<tr><td><code>ask_ai</code></td><td>AI 投研问答（取真数再回答；会员/管理员不限次，非会员每天 10 次，与网页端同一配额）</td></tr>
</table>

<div class="note">速率：每令牌 60 次/分钟；体验期账号每天 300 次工具调用，尊享/永久会员不限次。第三方版权内容（投行研报原文、聚合文章全文）不通过 MCP 提供，仅元数据与稻草财经自有内容。</div>
<p class="ft">内容仅供研究参考，不构成投资建议。© 稻草财经 · daocaijing.com</p>
</div>
<script>
const API='/api/account/mcp-tokens';
let jwt=''; try{ jwt=localStorage.getItem('auth_token')||''; }catch(e){}
function hdrs(){ return {'Content-Type':'application/json','Authorization':'Bearer '+jwt}; }
let lastToken='';
async function loadToks(){
  if(!jwt){ document.getElementById('loginHint').style.display='block'; return; }
  try{
    const r=await fetch(API,{headers:hdrs()});
    if(r.status===401){ document.getElementById('loginHint').style.display='block'; return; }
    const d=await r.json();
    const rows=(d.tokens||[]).map(t=>{
      const st=t.active? '<span style="color:var(--green)">有效</span>':'<span class="mut">已撤销</span>';
      return `<tr><td><code>${t.token_prefix}…</code></td><td>${t.name}</td><td>${st}</td><td>${t.today||0} 今 / ${t.call_count||0} 总</td><td>${(t.last_used_at||'—').slice(0,10)}</td><td>${t.active?`<button class="danger" onclick="revoke('${t.token_prefix}')">撤销</button>`:'—'}</td></tr>`;
    }).join('');
    document.getElementById('tokList').innerHTML = rows? `<table><tr><th>令牌</th><th>备注</th><th>状态</th><th>调用</th><th>最近使用</th><th></th></tr>${rows}</table>` : '<span class="mut">还没有令牌，上面创建一把。</span>';
  }catch(e){ document.getElementById('tokList').innerHTML='<span class="mut">加载失败：'+e+'</span>'; }
}
async function createTok(){
  if(!jwt){ document.getElementById('loginHint').style.display='block'; return; }
  const name=document.getElementById('tokName').value.trim();
  const r=await fetch(API,{method:'POST',headers:hdrs(),body:JSON.stringify({name})});
  const d=await r.json().catch(()=>({}));
  if(!r.ok){ alert(d.detail||('创建失败 '+r.status)); return; }
  lastToken=d.token;
  document.getElementById('newTok').style.display='block';
  document.getElementById('newTokVal').textContent=d.token;
  fillConfigs(d.token);
  loadToks();
}
function fillConfigs(tok){
  const url='__MCP_URL__';
  document.getElementById('cfgClaudeCode').textContent=`claude mcp add --transport http daocaijing ${url} --header "Authorization: Bearer ${tok}"`;
  document.getElementById('cfgClaudeDesktop').textContent=`${url}?token=${tok}`;
  document.getElementById('cfgJson').textContent=JSON.stringify({mcpServers:{daocaijing:{url,headers:{Authorization:'Bearer '+tok}}}},null,2);
  document.getElementById('cfgCurl').textContent=`curl -s ${url} -H "Authorization: Bearer ${tok}" -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'`;
}
function copyTok(){ try{ navigator.clipboard.writeText(lastToken); }catch(e){} }
async function revoke(pfx){
  if(!confirm('撤销 '+pfx+'…？使用该令牌的客户端会立即断开。')) return;
  await fetch(API+'/'+pfx,{method:'DELETE',headers:hdrs()});
  loadToks();
}
/* 手动粘贴令牌也能生成配置（换设备/找回场景） */
(function(){
  const url='__MCP_URL__';
  document.getElementById('tokName').addEventListener('input',()=>{});
  window.__pasteToken=function(t){ if(t&&t.startsWith('dfm_')) fillConfigs(t); };
})();
if(jwt) loadToks(); else document.getElementById('loginHint').style.display='block';
</script></body></html>"""


def setup_page() -> HTMLResponse:
    """控制台页（公开静态 HTML；登录态由前端同源 localStorage 提供）。"""
    mcp_url = (os.getenv("DEEPFOCUS_MCP_PUBLIC_URL") or "").strip() or "https://daocaijing.com/api/mcp"
    return HTMLResponse(_SETUP_PAGE_TMPL.replace("__MCP_URL__", mcp_url))
