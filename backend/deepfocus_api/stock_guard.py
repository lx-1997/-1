from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Optional

"""AI 问答·标的防幻觉护栏。

实测：用户问「建滔集团近况」，tool-agent 的模型凭记忆把代码猜成 0005（汇丰控股）
或 600545（无关 A 股），数据工具照单全收 → 用户拿到的是另一家公司的数据还被
当作答料。根因是 LLM 猜代码无任何校验。

本模块做两件事（纯函数 + 一次搜索解析，不依赖模型、不碰提示词）：
1. resolve_question_stock(question)：抽问题里的中文串走东财 suggest 搜索，
   只有候选名【原样出现在问题文本里】才采信（包含式高置信，杜绝把
   「建滔集团近况」解成不相干公司）；用户已给代码/多标的/超长文本不介入。
2. guard_symbol_args(tool_name, args, resolved)：工具分发前把单标的数据工具的
   symbol 参数改写为解析出的标准代码。多标的（比较题）不强行改写。
"""

_CJK_RUN = re.compile(r"[\u4e00-\u9fa5]{2,10}")
_BARE_CODE = re.compile(r"\b\d{4,6}\b")
_MAX_PROBES = 10             # 每个问题最多发起的搜索次数（噪声切分后按段、段内前缀降序）
_PROBE_TIMEOUT = 4.0
_CACHE_TTL = 1800.0
_MAX_QUESTION = 80

# 问题里的噪声词/连接词/标点：切分后剩下的段落才像公司名（「建滔集团近况」→「建滔集团」）
_NOISE_RE = re.compile(
    r"(?:近况|最近|现在|今天|今日|最新|怎么样|怎样|如何|哪个|哪些|什么|为什么|怎么看|对比|比较|分析|一下|"
    r"行情|股价|走势|消息|新闻|基本面|估值|值得|适合|入手|买入|还要|可以|应该|目前|和|与|及|跟|还是|吗|呢)|"
    r"[^\u4e00-\u9fa5]+"
)

# 仅限「symbol 语义不是问题里那家公司」的工具：多标的/用户数据/大盘全站类。
# 公司级工具（行情/估值/财报/新闻/公告/预告/研报等）一律改写——实测 get_stock_news
# 被模型配上猜错的代码后，会把别家公司新闻当作问题标的的新闻作答。
_TOOLS_NEVER_REWRITE = frozenset({
    "resolve_symbol", "compare_stocks", "get_my_watchlist", "get_market_structure",
    "get_limit_up_ladder", "get_cn_calendar", "get_site_fast_news", "get_site_articles",
    "get_recent_content_digest", "get_market_data", "get_daily_review",
    "get_dragon_tiger", "get_industry_context", "get_theme_stocks",
})

_CACHE: dict[str, tuple[float, Optional[dict[str, Any]]]] = {}


def _name_runs(text: str) -> list[str]:
    """噪声词/连接词/标点切分后的候选公司名段（≥2 字）。"""
    return [seg for seg in _NOISE_RE.split(text) if len(seg) >= 2]


def _probe_names(text: str) -> list[tuple[str, int]]:
    """候选查询词：(截断前缀, 所属段号)，按段、段内长度降序。"""
    out: list[tuple[str, int]] = []
    for idx, run in enumerate(_name_runs(text)):
        for width in range(min(len(run), 10), 1, -1):
            piece = run[:width]
            if all(piece != p for p, _ in out):
                out.append((piece, idx))
            if len(out) >= 24:
                return out
    return out


async def resolve_question_stock(question: str) -> Optional[dict[str, Any]]:
    """问题文本 → 高置信标的解析。命中多个或无命中/任何异常 → 可用信息随返回值给出，
    单标的确证时 primary 唯一；完全不可判 → None。结果带进程内 30min 缓存。"""
    text = (question or "").strip()
    if not text or len(text) > _MAX_QUESTION or _BARE_CODE.search(text) or not _CJK_RUN.search(text):
        return None
    key = text
    hit = _CACHE.get(key)
    if hit and (time.time() - hit[0]) < _CACHE_TTL:
        return hit[1]
    resolved: Optional[dict[str, Any]] = None
    try:
        from .market_data import search_market_symbols
        hits: dict[str, dict[str, Any]] = {}
        probes = 0
        done_runs: set[int] = set()
        for piece, run_idx in _probe_names(text):
            if probes >= _MAX_PROBES or run_idx in done_runs:
                continue
            probes += 1
            try:
                resp = await asyncio.wait_for(search_market_symbols(piece), timeout=_PROBE_TIMEOUT)
            except Exception:  # noqa: BLE001 — 搜索任何失败都不拦问答
                continue
            for cand in (getattr(resp, "candidates", None) or []):
                name = (getattr(cand, "name", "") or "").strip()
                if len(name) < 2 or name not in text:
                    continue  # 只有候选名原样出现在问题里才采信
                symbol = (getattr(cand, "symbol", "") or "").strip()
                if symbol:
                    hits[name] = {"symbol": symbol, "code": (getattr(cand, "code", "") or "").strip(),
                                  "name": name, "market": getattr(cand, "market", "") or ""}
            if hits:
                done_runs.add(run_idx)  # 命中后同段更短前缀必是其子串，无需再探
        if hits:
            ordered = sorted(hits.values(), key=lambda h: -len(h["name"]))
            resolved = {"primary": ordered[0], "all": ordered}
    except Exception:  # noqa: BLE001
        resolved = None
    _CACHE[key] = (time.time(), resolved)
    if len(_CACHE) > 512:
        for k in [k for k, v in _CACHE.items() if (time.time() - v[0]) >= _CACHE_TTL][:256]:
            _CACHE.pop(k, None)
    return resolved


def guard_symbol_args(tool_name: str, args: Any, resolved: Optional[dict[str, Any]]) -> Any:
    """工具分发前的确定性改写：问题恰解析出唯一标的时，单标的数据工具的 symbol
    参数一律替换为解析出的标准代码（模型猜对格式不同也归一）。其余情况原样放行。"""
    if not resolved or not isinstance(args, dict):
        return args
    all_hits = resolved.get("all") or []
    if len(all_hits) != 1 or tool_name in _TOOLS_NEVER_REWRITE:
        return args
    primary = resolved.get("primary") or {}
    symbol = primary.get("symbol")
    if not symbol:
        return args
    current = args.get("symbol")
    if isinstance(current, str) and current.strip() and current.strip() != symbol:
        return {**args, "symbol": symbol}
    return args
