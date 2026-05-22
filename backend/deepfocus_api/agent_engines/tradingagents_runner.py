from __future__ import annotations

import json
import os
import queue
import socket
import sys
import threading
import traceback
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from contextvars import copy_context
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable


DEFAULT_TOOL_TIMEOUT_SECONDS = float(os.getenv("DEEPFOCUS_TRADINGAGENTS_TOOL_TIMEOUT", "20"))
DEFAULT_WEB_SEARCH_LIMIT = int(os.getenv("DEEPFOCUS_TRADINGAGENTS_WEB_SEARCH_LIMIT", "6"))
DEFAULT_WEB_SEARCH_TIMEOUT_SECONDS = float(
    os.getenv("DEEPFOCUS_TRADINGAGENTS_WEB_SEARCH_TIMEOUT", "8")
)


def _serialize(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return value
    return str(value)


def main() -> int:
    try:
        request: dict[str, Any] = json.loads(sys.stdin.read() or "{}")
        symbol = str(request.get("symbol") or "").strip().upper()
        analysis_date = str(request.get("analysis_date") or "").strip()
        if not symbol or not analysis_date:
            raise ValueError("symbol and analysis_date are required")

        config_overrides = request.get("config_overrides") or {}
        decision = _run_modern(symbol, analysis_date, config_overrides, dry_run=bool(request.get("dry_run")))
        print(json.dumps({"ok": True, "decision": decision}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": str(exc),
                    "traceback": traceback.format_exc(limit=8),
                },
                ensure_ascii=False,
            )
        )
        return 1


def _run_modern(
    symbol: str,
    analysis_date: str,
    config_overrides: dict[str, Any],
    dry_run: bool = False,
) -> Any:
    tool_timeout_seconds = float(
        config_overrides.pop("_tool_timeout_seconds", DEFAULT_TOOL_TIMEOUT_SECONDS)
    )
    web_search_enabled = bool(config_overrides.pop("_web_search_enabled", True))
    web_search_limit = int(config_overrides.pop("_web_search_limit", DEFAULT_WEB_SEARCH_LIMIT))
    web_search_timeout_seconds = float(
        config_overrides.pop("_web_search_timeout_seconds", DEFAULT_WEB_SEARCH_TIMEOUT_SECONDS)
    )
    socket.setdefaulttimeout(tool_timeout_seconds)
    try:
        from tradingagents.config import TradingAgentsConfig
        from tradingagents.graph.setup import SUPPORTED_ANALYSTS, GraphSetup
        from tradingagents.graph.trading_graph import TradingAgentsGraph
    except ModuleNotFoundError:
        return _run_legacy(symbol, analysis_date, config_overrides, dry_run=dry_run)

    _install_tool_timeouts(tool_timeout_seconds)
    web_tools = []
    if web_search_enabled:
        web_tools = _install_web_search_tools(
            default_limit=web_search_limit,
            timeout_seconds=web_search_timeout_seconds,
        )
    selected_analysts = config_overrides.pop("selected_analysts", None) or list(SUPPORTED_ANALYSTS)
    selected_analysts = GraphSetup.validate_selected_analysts([str(item) for item in selected_analysts])
    if "results_dir" in config_overrides:
        config_overrides["results_dir"] = Path(str(config_overrides["results_dir"]))
    config = TradingAgentsConfig(**config_overrides)
    if dry_run:
        return {
            "runtime": "modern",
            "config": config.model_dump(mode="json"),
            "selected_analysts": selected_analysts,
            "web_search_enabled": bool(web_tools),
        }
    graph = TradingAgentsGraph(debug=False, config=config, selected_analysts=selected_analysts)
    if web_tools:
        _attach_web_search_tool_nodes(graph, web_tools)
    _, recommendation = graph.propagate(symbol, analysis_date)
    return _serialize(recommendation)


def _run_legacy(
    symbol: str,
    analysis_date: str,
    config_overrides: dict[str, Any],
    dry_run: bool = False,
) -> Any:
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    config = DEFAULT_CONFIG.copy()
    config.update(config_overrides)
    if dry_run:
        return {"runtime": "legacy", "config": config}
    graph = TradingAgentsGraph(debug=False, config=config)
    _, decision = graph.propagate(symbol, analysis_date)
    return _serialize(decision)


def _install_tool_timeouts(timeout_seconds: float) -> None:
    """Prevent one slow data source from hanging the full TradingAgents graph."""
    import tradingagents.agents.utils.agent_utils as agent_utils
    import tradingagents.agents.utils.core_stock_tools as core_stock_tools
    import tradingagents.agents.utils.fundamental_data_tools as fundamental_data_tools
    import tradingagents.agents.utils.news_data_tools as news_data_tools
    import tradingagents.agents.utils.technical_indicators_tools as technical_indicators_tools

    tool_names = [
        "get_analyst_ratings",
        "get_balance_sheet",
        "get_cashflow",
        "get_dividends_splits",
        "get_earnings_calendar",
        "get_fundamentals",
        "get_global_news",
        "get_income_statement",
        "get_indicators",
        "get_insider_transactions",
        "get_institutional_holders",
        "get_market_context",
        "get_news",
        "get_short_interest",
        "get_stock_data",
    ]
    for module in [
        agent_utils,
        core_stock_tools,
        fundamental_data_tools,
        news_data_tools,
        technical_indicators_tools,
    ]:
        for name in tool_names:
            tool = getattr(module, name, None)
            if tool is not None and hasattr(tool, "func"):
                _wrap_tool_func(tool, timeout_seconds)


def _wrap_tool_func(tool: Any, timeout_seconds: float) -> None:
    if getattr(tool, "_deepfocus_timeout_wrapped", False):
        return
    original: Callable[..., Any] | None = getattr(tool, "func", None)
    if original is None:
        return
    tool_name = getattr(tool, "name", "tool")

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        result_queue: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)
        context = copy_context()

        def run_original() -> None:
            try:
                result_queue.put((True, context.run(original, *args, **kwargs)))
            except Exception as exc:  # noqa: BLE001 - returned to the agent as tool context
                result_queue.put((False, exc))

        thread = threading.Thread(target=run_original, daemon=True)
        thread.start()
        thread.join(timeout_seconds)
        if thread.is_alive():
            return (
                f"[TOOL_ERROR] {tool_name} timed out after {timeout_seconds:.0f}s. "
                "Continue the analysis with available evidence and explicitly note the data gap."
            )
        ok, payload = result_queue.get() if not result_queue.empty() else (False, "empty tool result")
        if ok:
            return payload
        return f"[TOOL_ERROR] {tool_name} failed: {payload!s}"

    object.__setattr__(tool, "func", wrapped)
    object.__setattr__(tool, "_deepfocus_timeout_wrapped", True)


def _install_web_search_tools(default_limit: int, timeout_seconds: float) -> list[Any]:
    """Inject a Codex-like web search tool into TradingAgents news analysts."""
    from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
    from langchain_core.tools import tool
    from tradingagents.agents.prompts import load_prompt
    from tradingagents.agents.utils.agent_states import AgentState
    import tradingagents.agents.analysts.news_analyst as news_analyst_module
    import tradingagents.agents.analysts.social_media_analyst as social_media_module
    import tradingagents.agents.utils.agent_utils as agent_utils
    import tradingagents.graph.setup as setup_module

    @tool
    def deepfocus_web_search(query: str, limit: int = default_limit) -> str:
        """Search the public web for current investment evidence.

        Use this when Yahoo Finance, Google News RSS, or other structured
        finance tools are empty, rate-limited, or too narrow. Search for
        company news, filings, press releases, regulation, products,
        competitors, and macro catalysts. Return cited titles, sources,
        snippets, and URLs for human verification.
        """

        return _deepfocus_web_search(query, limit=limit, timeout_seconds=timeout_seconds)

    @tool
    def deepfocus_read_url(url: str, max_chars: int = 6000) -> str:
        """Read a public web page and return extractive text for verification.

        Use this after deepfocus_web_search when a result URL looks important.
        Prefer official filings, company press releases, exchange pages,
        regulator pages, and reputable news sources. Do not assume a page is
        readable if this tool returns an error or only boilerplate.
        """

        return _deepfocus_read_url(url, max_chars=max_chars, timeout_seconds=timeout_seconds)

    web_tools = [deepfocus_web_search, deepfocus_read_url]

    _patch_get_news_with_web_fallback(
        agent_utils.get_news,
        default_limit=default_limit,
        timeout_seconds=timeout_seconds,
    )

    def create_news_analyst_with_web(llm: Any) -> Callable[[AgentState], dict[str, Any]]:
        def news_analyst_node(state: AgentState) -> dict[str, Any]:
            tools = [
                agent_utils.get_news,
                agent_utils.get_global_news,
                agent_utils.get_insider_transactions,
                *web_tools,
            ]
            prompt = ChatPromptTemplate.from_messages([
                ("system", load_prompt("news_analyst")),
                MessagesPlaceholder(variable_name="messages"),
            ])
            prompt = prompt.partial(tool_names=", ".join([item.name for item in tools]))
            prompt = prompt.partial(current_date=state.trade_date)
            prompt = prompt.partial(ticker=state.company_of_interest)
            result = (prompt | llm.bind_tools(tools)).invoke(state.messages)
            report = "" if result.tool_calls else result.content
            return {"messages": [result], "news_report": report}

        return news_analyst_node

    def create_social_media_analyst_with_web(llm: Any) -> Callable[[AgentState], dict[str, Any]]:
        def social_media_analyst_node(state: AgentState) -> dict[str, Any]:
            tools = [agent_utils.get_news, *web_tools]
            prompt = ChatPromptTemplate.from_messages([
                ("system", load_prompt("news_sentiment_analyst")),
                MessagesPlaceholder(variable_name="messages"),
            ])
            prompt = prompt.partial(tool_names=", ".join([item.name for item in tools]))
            prompt = prompt.partial(current_date=state.trade_date)
            prompt = prompt.partial(ticker=state.company_of_interest)
            result = (prompt | llm.bind_tools(tools)).invoke(state.messages)
            report = "" if result.tool_calls else result.content
            return {"messages": [result], "sentiment_report": report}

        return social_media_analyst_node

    news_analyst_module.create_news_analyst = create_news_analyst_with_web
    social_media_module.create_social_media_analyst = create_social_media_analyst_with_web
    setup_module.create_news_analyst = create_news_analyst_with_web
    setup_module.create_social_media_analyst = create_social_media_analyst_with_web
    return web_tools


def _attach_web_search_tool_nodes(graph: Any, web_tools: list[Any]) -> None:
    from langgraph.prebuilt import ToolNode
    from tradingagents.graph.trading_graph import _tool_error_handler
    from tradingagents.agents.utils.agent_utils import (
        get_news,
        get_global_news,
        get_insider_transactions,
        get_market_context,
        get_earnings_calendar,
    )

    tool_nodes = dict(graph.tool_nodes)
    tool_nodes["social"] = ToolNode([get_news, *web_tools], handle_tool_errors=_tool_error_handler)
    tool_nodes["news"] = ToolNode(
        [
            get_news,
            get_global_news,
            get_insider_transactions,
            get_market_context,
            get_earnings_calendar,
            *web_tools,
        ],
        handle_tool_errors=_tool_error_handler,
    )
    object.__setattr__(graph, "tool_nodes", tool_nodes)


def _patch_get_news_with_web_fallback(
    get_news_tool: Any,
    default_limit: int,
    timeout_seconds: float,
) -> None:
    if getattr(get_news_tool, "_deepfocus_web_fallback_wrapped", False):
        return
    original = getattr(get_news_tool, "func", None)
    if original is None:
        return

    def get_news_with_web_fallback(ticker: str, start_date: str, end_date: str) -> str:
        result = original(ticker, start_date, end_date)
        text = str(result or "")
        if "[TOOL_ERROR]" not in text and "[NO_DATA]" not in text and text.lstrip().startswith("##"):
            return text
        query = f"{ticker} stock news earnings filings catalysts {start_date} {end_date}"
        fallback = _deepfocus_web_search(
            query,
            limit=default_limit,
            timeout_seconds=timeout_seconds,
        )
        return f"{text}\n\n---\n\n{fallback}"

    object.__setattr__(get_news_tool, "func", get_news_with_web_fallback)
    object.__setattr__(get_news_tool, "_deepfocus_web_fallback_wrapped", True)


def _deepfocus_web_search(query: str, limit: int, timeout_seconds: float) -> str:
    query = str(query or "").strip()
    if not query:
        return "[TOOL_ERROR] deepfocus_web_search requires a non-empty query."
    limit = max(1, min(10, int(limit or DEFAULT_WEB_SEARCH_LIMIT)))
    started = datetime.now(timezone.utc).isoformat()
    providers = [
        _search_tavily,
        _search_serper,
        _search_brave,
        _search_bing_rss,
        _search_duckduckgo_html,
    ]
    errors: list[str] = []
    for provider in providers:
        try:
            results = provider(query, limit, timeout_seconds)
        except Exception as exc:  # noqa: BLE001 - provider failures are returned as tool evidence
            errors.append(f"{provider.__name__}: {exc}")
            continue
        if results:
            provider_name = results[0].get("provider") or provider.__name__.removeprefix("_search_")
            return _format_search_results(query, provider_name, started, results[:limit])
    return (
        "[TOOL_ERROR] DeepFocus web search returned no results.\n"
        f"Query: {query}\n"
        f"Diagnostics: {'; '.join(errors[-4:]) if errors else 'no provider returned data'}"
    )


def _deepfocus_read_url(url: str, max_chars: int, timeout_seconds: float) -> str:
    url = str(url or "").strip()
    if not url.startswith(("http://", "https://")):
        return "[TOOL_ERROR] deepfocus_read_url only supports http(s) URLs."
    max_chars = max(500, min(12000, int(max_chars or 6000)))
    try:
        html = _http_text(url, timeout_seconds)
    except Exception as exc:  # noqa: BLE001
        return f"[TOOL_ERROR] deepfocus_read_url failed for {url}: {exc}"

    parser = _ReadableHTMLParser()
    try:
        parser.feed(html)
    except Exception:
        pass
    text = parser.text()
    if len(text) < 120:
        text = _clean_html_text(_strip_html_tags(html))
    if len(text) < 120:
        return (
            f"[NO_DATA] deepfocus_read_url fetched {url}, but could not extract readable text. "
            "The page may require JavaScript, login, or anti-bot access."
        )
    return (
        "## DeepFocus URL Read\n"
        f"URL: {url}\n"
        f"Fetched at: {datetime.now(timezone.utc).isoformat()}\n\n"
        f"{text[:max_chars]}"
    )


def _format_search_results(
    query: str,
    provider_name: str,
    fetched_at: str,
    results: list[dict[str, str]],
) -> str:
    lines = [
        "## DeepFocus Web Search Results",
        f"Query: {query}",
        f"Provider: {provider_name}",
        f"Fetched at: {fetched_at}",
        "",
    ]
    for index, item in enumerate(results, start=1):
        title = _clean_html_text(item.get("title") or "(untitled)")
        url = str(item.get("url") or "").strip()
        snippet = _clean_html_text(item.get("snippet") or "")
        source = item.get("source") or _domain_from_url(url) or provider_name
        lines.append(f"### {index}. {title}")
        lines.append(f"Source: {source}")
        if url:
            lines.append(f"URL: {url}")
        if snippet:
            lines.append(f"Snippet: {snippet}")
        lines.append("")
    return "\n".join(lines).strip()


def _search_tavily(query: str, limit: int, timeout_seconds: float) -> list[dict[str, str]]:
    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key:
        return []
    body = json.dumps(
        {
            "api_key": api_key,
            "query": query,
            "search_depth": "advanced",
            "max_results": limit,
            "include_answer": False,
            "include_raw_content": False,
        }
    ).encode("utf-8")
    data = _http_json(
        "https://api.tavily.com/search",
        timeout_seconds,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    return [
        {
            "provider": "tavily",
            "title": str(item.get("title") or ""),
            "url": str(item.get("url") or ""),
            "snippet": str(item.get("content") or ""),
        }
        for item in data.get("results", [])[:limit]
    ]


def _search_serper(query: str, limit: int, timeout_seconds: float) -> list[dict[str, str]]:
    api_key = os.getenv("SERPER_API_KEY", "").strip()
    if not api_key:
        return []
    body = json.dumps({"q": query, "num": limit}).encode("utf-8")
    data = _http_json(
        "https://google.serper.dev/search",
        timeout_seconds,
        data=body,
        headers={"Content-Type": "application/json", "X-API-KEY": api_key},
    )
    return [
        {
            "provider": "serper",
            "title": str(item.get("title") or ""),
            "url": str(item.get("link") or ""),
            "snippet": str(item.get("snippet") or ""),
        }
        for item in data.get("organic", [])[:limit]
    ]


def _search_brave(query: str, limit: int, timeout_seconds: float) -> list[dict[str, str]]:
    api_key = os.getenv("BRAVE_SEARCH_API_KEY", "").strip()
    if not api_key:
        return []
    url = (
        "https://api.search.brave.com/res/v1/web/search?"
        + urllib.parse.urlencode({"q": query, "count": limit})
    )
    data = _http_json(
        url,
        timeout_seconds,
        headers={"Accept": "application/json", "X-Subscription-Token": api_key},
    )
    return [
        {
            "provider": "brave",
            "title": str(item.get("title") or ""),
            "url": str(item.get("url") or ""),
            "snippet": str(item.get("description") or ""),
        }
        for item in (data.get("web") or {}).get("results", [])[:limit]
    ]


def _search_bing_rss(query: str, limit: int, timeout_seconds: float) -> list[dict[str, str]]:
    url = "https://www.bing.com/search?" + urllib.parse.urlencode({"q": query, "format": "rss"})
    root = ET.fromstring(_http_text(url, timeout_seconds))
    results = []
    for item in root.findall(".//item")[:limit]:
        title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        description = item.findtext("description") or ""
        results.append(
            {
                "provider": "bing_rss",
                "title": title,
                "url": link,
                "snippet": description,
            }
        )
    return results


def _search_duckduckgo_html(query: str, limit: int, timeout_seconds: float) -> list[dict[str, str]]:
    url = "https://duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    parser = _DuckDuckGoHTMLParser(limit)
    parser.feed(_http_text(url, timeout_seconds))
    return parser.results


def _http_json(
    url: str,
    timeout_seconds: float,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    text = _http_text(url, timeout_seconds, data=data, headers=headers)
    return json.loads(text)


def _http_text(
    url: str,
    timeout_seconds: float,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> str:
    request_headers = {
        "User-Agent": "DeepFocusAgent/1.0 (+https://localhost)",
        **(headers or {}),
    }
    request = urllib.request.Request(url, data=data, headers=request_headers)
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return response.read().decode("utf-8", errors="replace")


def _domain_from_url(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def _clean_html_text(value: str) -> str:
    text = unescape(str(value or ""))
    text = " ".join(text.replace("\n", " ").split())
    return text[:800]


class _DuckDuckGoHTMLParser(HTMLParser):
    def __init__(self, limit: int) -> None:
        super().__init__()
        self.limit = limit
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._capture: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        class_name = attrs_dict.get("class", "")
        if tag == "a" and "result__a" in class_name and len(self.results) < self.limit:
            href = attrs_dict.get("href", "") or ""
            self._current = {"provider": "duckduckgo_html", "url": _decode_ddg_url(href)}
            self._capture = "title"
            self._parts = []
        elif self._current is not None and tag in {"a", "div"} and "result__snippet" in class_name:
            self._capture = "snippet"
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._current is None or self._capture is None:
            return
        if tag not in {"a", "div"}:
            return
        text = _clean_html_text(" ".join(self._parts))
        if self._capture == "title":
            self._current["title"] = text
        elif self._capture == "snippet":
            self._current["snippet"] = text
        if self._current.get("title") and self._current.get("url") and self._current not in self.results:
            if self._capture == "snippet" or not self._current.get("snippet"):
                self.results.append(self._current)
                self._current = None
        self._capture = None
        self._parts = []


def _decode_ddg_url(href: str) -> str:
    parsed = urllib.parse.urlparse(href)
    params = urllib.parse.parse_qs(parsed.query)
    if params.get("uddg"):
        return params["uddg"][0]
    return href


class _ReadableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._current_link: str | None = None
        self._chunks: list[str] = []
        self._title: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg", "canvas"}:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        attrs_dict = dict(attrs)
        if tag == "a":
            href = attrs_dict.get("href")
            if href and href.startswith(("http://", "https://")):
                self._current_link = href
        if tag in {"p", "div", "section", "article", "li", "tr", "h1", "h2", "h3", "br"}:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg", "canvas"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag == "a":
            self._current_link = None
        if tag in {"p", "div", "section", "article", "li", "tr", "h1", "h2", "h3"}:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = _clean_html_text(data)
        if not text:
            return
        if self.lasttag == "title":
            self._title.append(text)
            return
        self._chunks.append(text)
        if self._current_link:
            self._chunks.append(f" ({self._current_link})")

    def text(self) -> str:
        lines = []
        if self._title:
            lines.append("# " + _clean_html_text(" ".join(self._title)))
        raw = " ".join(self._chunks)
        for line in raw.split("\n"):
            line = _clean_html_text(line)
            if len(line) >= 20 and line not in lines:
                lines.append(line)
        return "\n".join(lines)


class _TagStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _strip_html_tags(html: str) -> str:
    parser = _TagStripper()
    parser.feed(html)
    return " ".join(parser.parts)


if __name__ == "__main__":
    raise SystemExit(main())
