from __future__ import annotations

import asyncio
import html
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .schemas import (
    DulusAgentTurn,
    DulusCitableSource,
    DulusMemoryCreateRequest,
    DulusMemoryListResponse,
    DulusMemoryRecord,
    DulusProviderRecord,
    DulusRoundtableRequest,
    DulusRoundtableResponse,
    DulusRuntimeStatusResponse,
    DulusToolRecord,
    DulusToolTrace,
    DulusWebBridgeInspectRequest,
    DulusWebBridgeInspectResponse,
    StockSnapshot,
)
from .data_sources import list_data_items, query_tokens_2gram, crawl_evidence_if_thin
from .llm import _cred_tier_label
from .research_harness import (
    build_research_packet,
    ResearchProgressCallback,
    is_comparison_question,
    is_research_question,
    is_stock_selection_request,
    normalize_stock_selection_request,
    sanitize_stock_selection_context,
    stock_selection_clarification_text,
    stock_selection_needs_clarification,
)

DB_PATH = Path(os.getenv("DULUS_MEMORY_DB_PATH") or Path(__file__).resolve().parents[1] / ".dulus_memory.sqlite3")
WEBBRIDGE_DEFAULT_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "::1"}
WEBBRIDGE_POLICY = (
    "Authorized WebBridge 只允许本机或 DULUS_WEBBRIDGE_ALLOWED_HOSTS 白名单域名；"
    "不发送 Cookie，不读取浏览器 profile，不复用第三方 AI 会话。"
)
DULUS_ANSWER_PROTOCOL_VERSION = "research-v4-buy-side"


def _is_method_question(text: str) -> bool:
    """教学/方法题不应偷偷退化成一轮实时个股研判。"""
    query = str(text or "").strip()
    return bool(re.search(
        r"只讲方法|方法论|只解释|解释(?:一下)?(?:PE|PB|PEG|估值)|不引用(?:实时|当前)|"
        r"不要引用(?:实时|当前)|不查(?:实时|行情)|假设(?:例子|案例)",
        query,
        re.I,
    ))


PARTICIPANT_DEFINITIONS: dict[str, dict[str, str]] = {
    "evidence": {
        "name": "Evidence Scout",
        "stance": "evidence",
        "brief": "只看事实、证据覆盖和缺口，优先标注来源不足。",
    },
    "research": {
        "name": "Research Analyst",
        "stance": "research",
        "brief": "形成投研假设、催化路径和可验证指标。",
    },
    "risk": {
        "name": "Risk Sentinel",
        "stance": "risk",
        "brief": "先找反证、合规边界、资金保护和失效条件。",
    },
    "operator": {
        "name": "Tool Operator",
        "stance": "operator",
        "brief": "把目标拆成工具调用、文件和资料处理步骤。",
    },
}


def init_dulus_runtime_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dulus_memory (
                id TEXT PRIMARY KEY,
                scope TEXT NOT NULL,
                hall TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_dulus_memory_created ON dulus_memory(created_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_dulus_memory_scope ON dulus_memory(scope, hall)")
        conn.commit()
    _ensure_default_memory_buckets()


def list_dulus_memories(limit: int = 20, scope: str | None = None) -> DulusMemoryListResponse:
    init_dulus_runtime_db()
    safe_limit = max(1, min(limit, 100))
    query = "SELECT * FROM dulus_memory"
    params: list[Any] = []
    user_scope = str(scope or "").strip()
    if user_scope.startswith("user:"):
        owner = user_scope[5:].strip()[:80]
        query += " WHERE scope = 'user' AND hall = 'ai_chat' AND tags_json LIKE ?"
        params.append(f'%"owner:{owner}"%')
    elif user_scope == "__shared__":
        query += " WHERE scope != 'user'"
    elif scope:
        query += " WHERE scope = ?"
        params.append(scope)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(safe_limit)
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return DulusMemoryListResponse(memories=[_memory_from_row(row) for row in rows])


def create_dulus_memory(request: DulusMemoryCreateRequest) -> DulusMemoryRecord:
    init_dulus_runtime_db()
    return _save_memory(
        scope=request.scope,
        hall=request.hall,
        title=request.title,
        content=request.content,
        tags=request.tags,
        source=request.source,
    )


def inspect_authorized_webbridge(request: DulusWebBridgeInspectRequest) -> DulusWebBridgeInspectResponse:
    allowed, reason = _is_authorized_url(request.url)
    if not allowed:
        return DulusWebBridgeInspectResponse(
            url=request.url,
            allowed=False,
            policy=f"{WEBBRIDGE_POLICY} 拒绝原因：{reason}",
            fetched_at=datetime.now(timezone.utc),
        )

    parsed = urlparse(request.url)
    req = Request(
        request.url,
        headers={
            "User-Agent": "DeepFocus-Dulus-AuthorizedWebBridge/1.0",
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
        },
        method="GET",
    )
    try:
        with urlopen(req, timeout=10) as response:
            content_type = response.headers.get("content-type", "")
            raw = response.read(400_000)
    except Exception as exc:
        return DulusWebBridgeInspectResponse(
            url=request.url,
            allowed=True,
            policy=f"{WEBBRIDGE_POLICY} 请求失败：{_clean_text(exc, 'unknown error')}",
            fetched_at=datetime.now(timezone.utc),
        )

    charset = _charset_from_content_type(content_type) or "utf-8"
    text = raw.decode(charset, errors="replace")
    title = _extract_title(text) or parsed.netloc
    links = _extract_links(text, base=f"{parsed.scheme}://{parsed.netloc}") if request.mode == "dom" else []
    preview = _html_to_text(text)[:1800]
    return DulusWebBridgeInspectResponse(
        url=request.url,
        allowed=True,
        policy=WEBBRIDGE_POLICY,
        title=title[:180],
        text_preview=preview,
        links=links[:20],
        fetched_at=datetime.now(timezone.utc),
    )


def list_dulus_tools() -> list[DulusToolRecord]:
    return [
        DulusToolRecord(
            id="repo_search",
            name="仓库搜索",
            category="search",
            description="检索本地仓库文件名、符号和文本片段。",
            permission="read_only",
            enabled=True,
            risk_level="low",
            invocation='repo.search({ query })',
        ),
        DulusToolRecord(
            id="evidence_lookup",
            name="证据库检索",
            category="memory",
            description="从 DeepFocus 数据源中心读取上下文、来源和可信度摘要。",
            permission="read_only",
            enabled=True,
            risk_level="low",
            invocation='evidence.lookup({ objective, symbol })',
        ),
        DulusToolRecord(
            id="market_snapshot",
            name="行情快照",
            category="finance",
            description="读取当前页面传入的标的价格、涨跌幅和市场标签。",
            permission="read_only",
            enabled=True,
            risk_level="low",
            invocation='market.snapshot({ symbol })',
        ),
        DulusToolRecord(
            id="research_harness",
            name="统一研究取数",
            category="finance",
            description="并行调用公开行情/估值/财报/资金/新闻，以及稻草财经快讯、文章、研报、机构纪要、名人观点（按权限），并扫描行业归属、同行候选和上下游线索；统一打标签、按可信度/新鲜度排序并去重后生成可引用研究数据包。",
            permission="read_only",
            enabled=True,
            risk_level="low",
            invocation='research.harness({ objective, symbol })',
        ),
        DulusToolRecord(
            id="risk_review",
            name="风险纪律",
            category="risk",
            description="检查过度自信、来源不足、交易建议越界和反证缺失。",
            permission="read_only",
            enabled=True,
            risk_level="medium",
            invocation='risk.review({ answer })',
        ),
        DulusToolRecord(
            id="report_outline",
            name="报告骨架",
            category="report",
            description="生成证据、共识、分歧、动作和反证结构。",
            permission="read_only",
            enabled=True,
            risk_level="low",
            invocation='report.outline({ objective })',
        ),
        DulusToolRecord(
            id="authorized_webbridge_inspect",
            name="授权 WebBridge",
            category="browser",
            description="无 Cookie 抓取本机/白名单网页文本和 DOM 摘要，用于自有页面调试。",
            permission="read_only",
            enabled=True,
            risk_level="medium",
            invocation='webbridge.inspect({ url, mode })',
        ),
        DulusToolRecord(
            id="mem_palace_save",
            name="MemPalace 写入",
            category="memory",
            description="把圆桌摘要写入本地 SQLite 记忆宫殿，供后续任务检索。",
            permission="read_only",
            enabled=True,
            risk_level="low",
            invocation='memory.save({ scope, hall, title, content })',
        ),
        DulusToolRecord(
            id="mcp_gateway",
            name="MCP 网关",
            category="mcp",
            description="通过已登记 MCP server 调用工具，默认需要人工确认。",
            permission="approval_required",
            enabled=True,
            risk_level="high",
            invocation='mcp.call({ server, tool, arguments })',
        ),
        DulusToolRecord(
            id="shell_executor",
            name="Bash 执行",
            category="shell",
            description="执行本地命令，合规版默认作为受控能力展示。",
            permission="approval_required",
            enabled=False,
            risk_level="high",
            invocation='shell.run({ command })',
        ),
        DulusToolRecord(
            id="webbridge_browser_capture",
            name="WebBridge 会话捕获",
            category="browser",
            description="第三方网页 AI 会话捕获在 DeepFocus 合规版中禁用。",
            permission="disabled",
            enabled=False,
            risk_level="high",
            invocation='webbridge.capture({ provider })',
        ),
    ]


def build_dulus_runtime_status(llm: Any) -> DulusRuntimeStatusResponse:
    provider = _provider_name(llm)
    model = _model_name(llm)
    is_mock = getattr(llm, "provider", "mock") == "mock"
    current_mode = "local_mock" if is_mock else "openai_compatible"

    providers = [
        DulusProviderRecord(
            id="deepfocus-current",
            name="DeepFocus 当前模型",
            mode=current_mode,
            model=model,
            status="ready",
            latency_hint="10-45 秒",
            risk_level="low",
            notes="沿用系统设置里的 OpenAI-compatible / MiniMax / OpenAI 配置。",
        ),
        DulusProviderRecord(
            id="litellm-gateway",
            name="LiteLLM 网关",
            mode="openai_compatible",
            model="100+ backends",
            status="needs_config",
            latency_hint="取决于后端",
            risk_level="medium",
            notes="将模型配置 Base URL 指向自建 LiteLLM 后可接入。",
        ),
        DulusProviderRecord(
            id="ollama-local",
            name="Ollama 本地模型",
            mode="openai_compatible",
            model="qwen / llama / deepseek",
            status="needs_config",
            latency_hint="本机性能决定",
            risk_level="low",
            notes="使用 OpenAI-compatible Ollama endpoint 接入。",
        ),
        DulusProviderRecord(
            id="webbridge-gemini-guest",
            name="WebBridge 网页访客会话",
            mode="webbridge_disabled",
            model="browser session",
            status="disabled",
            latency_hint="禁用",
            risk_level="high",
            notes="不捕获、不复用、不代理第三方网页 AI 会话。",
        ),
    ]

    warnings = [
        "WebBridge 的第三方网页会话捕获已禁用，请使用官方 API、OpenAI-compatible 网关或本地模型。",
    ]
    if is_mock:
        warnings.append("当前模型是 mock，本页面会返回本地可复现的圆桌结果。")

    return DulusRuntimeStatusResponse(
        generated_at=datetime.now(timezone.utc),
        compliant_mode=True,
        provider=provider,
        model=model,
        providers=providers,
        tools=list_dulus_tools(),
        webbridge_policy=WEBBRIDGE_POLICY,
        memory_scope="本地 SQLite MemPalace 和 DeepFocus 证据库上下文，不写入第三方账号会话。",
        warnings=warnings,
    )


def _citable_sources_for_request(request: DulusRoundtableRequest) -> list[DulusCitableSource]:
    """从证据库按聚焦标的 + 议题关键词检索真实证据条目，去重→编号可引用来源（带 url+可信度）。
    让圆桌结论像分析师快答/深研一样可溯源，而非只给「工具轨迹」字符串标签。"""
    symbol = request.stock.symbol if request.stock else None
    keywords = _extract_keywords(f"{request.objective} {request.context}")[:4]
    query = " ".join(keywords) if keywords else None
    try:
        items: list[Any] = []
        if symbol:
            items = list_data_items(symbol=symbol, query=query, limit=10, sort="time_desc")
            if not items:
                items = list_data_items(symbol=symbol, limit=10, sort="time_desc")
        elif query:
            items = list_data_items(query=query, limit=10, sort="time_desc")
    except Exception:
        return []
    # 去重
    seen: set[str] = set()
    deduped: list[Any] = []
    for item in items:
        title = (item.title or "").strip()
        if not title or title in seen:
            continue
        seen.add(title)
        deduped.append(item)
    # 相关性重排：按议题 2-gram 词元命中标题+正文次数排序（credibility 作次序），与分析师快答一致。
    q_tokens = query_tokens_2gram(f"{request.objective} {request.context}")
    if q_tokens and len(deduped) > 1:
        def _rel(it: Any) -> int:
            hay = f"{it.title or ''} {it.text_preview or ''}".lower()
            return sum(1 for tok in q_tokens if tok in hay)
        deduped.sort(key=lambda it: (_rel(it), it.credibility_score), reverse=True)
    out: list[DulusCitableSource] = []
    for item in deduped:
        title = (item.title or "").strip()
        cred = item.credibility_score
        out.append(DulusCitableSource(
            n=len(out) + 1,
            title=title,
            source=item.source_name or "证据库",
            url=item.url or "",
            credibility=float(cred) if isinstance(cred, (int, float)) else None,
        ))
        if len(out) >= 6:
            break
    return out


async def run_dulus_roundtable(
    llm: Any,
    request: DulusRoundtableRequest,
    *,
    ifind_user: bool = False,
    progress: ResearchProgressCallback | None = None,
) -> DulusRoundtableResponse:
    objective = request.objective.strip()
    if not objective:
        raise ValueError("objective is required")

    # 全市场选股不是“当前页面这只股”的深度研判。旧客户端即使仍携带 stock，
    # 后端也要清掉；独立问题还要隔离上一轮主题，避免国金证券/AI 算力式串题。
    if is_stock_selection_request(objective):
        clean_context = sanitize_stock_selection_context(objective, request.context)
        request = request.model_copy(update={"stock": None, "context": clean_context})
        if stock_selection_needs_clarification(objective, clean_context):
            return DulusRoundtableResponse(
                provider=_provider_name(llm),
                model=_model_name(llm),
                generated_at=datetime.now(timezone.utc),
                mode=request.mode,
                objective=objective,
                turns=[],
                synthesis=stock_selection_clarification_text(),
                decision="research_more",
                memory_notes=[],
                tool_traces=[],
                warnings=[],
                sources=[],
                citable_sources=[],
                confidence=0.95,
                disclaimer="补充筛选范围后再开始查证；本次未执行研究取数。",
                content_scope={},
                research_steps=[],
                evidence_highlights=[],
                evidence_references=[],
            )
        normalized_selection = normalize_stock_selection_request(objective)
        if normalized_selection != objective:
            objective = normalized_selection
            request = request.model_copy(update={"objective": objective})

    # 先补证据库，再构建 harness，确保圆桌上下文和可引用来源看到的是同一批资料。
    crawl_symbol = request.stock.symbol if request.stock else None
    crawl_keyword = (
        (request.stock.name if request.stock and (request.stock.name or "").strip() else crawl_symbol)
        or (" ".join(_extract_keywords(objective)[:2]) or None)
    )
    if is_research_question(objective) and not _is_method_question(objective) and getattr(llm, "provider", "mock") != "mock":
        await crawl_evidence_if_thin(crawl_symbol, crawl_keyword)

    # 统一研究前置层：圆桌不再只消费页面快照和规则占位轨迹，而是先拿到真实的
    # 行情/估值/财报/资金/新闻/研报/站内证据，再交给多角色讨论。
    packet: dict[str, Any] = {}
    # mock provider 供离线测试/演示使用，不触发外部数据源；真实模型才走完整 harness。
    if is_research_question(objective) and not _is_method_question(objective) and getattr(llm, "provider", "mock") != "mock":
        packet = await build_research_packet(
            objective,
            request.context,
            request.stock,
            ifind_user=ifind_user,
            progress=progress,
        )
        packet_stock = packet.get("stock")
        packet_context = str(packet.get("context") or "").strip()
        if packet_stock or packet_context:
            request = request.model_copy(update={
                "stock": packet_stock or request.stock,
                "context": "\n".join(item for item in (request.context.strip(), packet_context) if item),
            })
        # 微信 AI 问答与网页深度研判共用同一份短期答案缓存时，把它作为“待交叉核验”的
        # 参考意见带入圆桌；绝不把微信回答当作事实来源，也不跨用户读取会话上下文。
        wechat_reference = _wechat_reference_for_question(objective)
        if wechat_reference:
            request = request.model_copy(update={
                "context": request.context + "\n微信侧历史 AI 参考（仅供交叉核验，不得替代公开数据）：\n" + wechat_reference,
            })

    # 编号可引用来源前置：参与者 + 主席都拿到，正文才能用 [n] 内联引用（与分析师/深研对称）。
    citable_sources = _citable_sources_for_request(request)

    participant_ids = _select_participants(request.participants)
    tool_records = {tool.id: tool for tool in list_dulus_tools()}
    selected_tool_ids = _select_tools(request.enabled_tools, tool_records)
    tool_traces = _run_safe_tools(request, selected_tool_ids, tool_records)
    if packet:
        modules = list((packet.get("modules") or {}).keys())
        gaps = list(packet.get("gaps") or [])
        scope = packet.get("content_scope") or {}
        scope_labels = [
            label for label, item in scope.items()
            if isinstance(item, dict) and item.get("status") == "available"
        ]
        unavailable_labels = [
            label for label, item in scope.items()
            if isinstance(item, dict) and item.get("status") != "available"
        ]
        index_stats = ((packet.get("modules") or {}).get("research_index") or {}).get("stats") or {}
        tool_traces.insert(0, DulusToolTrace(
            tool="research_harness",
            title="统一取数 · 站内资料 + 行业/同行/上下游 + 公开数据",
            input=request.stock.symbol if request.stock else request.objective[:120],
            output=(
                f"已并行完成 {len(modules)} 个数据模块；"
                f"可用类别：{', '.join(scope_labels) or '暂无'}。"
                f"未覆盖/未授权：{', '.join(unavailable_labels) or '无'}；"
                f"索引去重 {index_stats.get('raw_items', 0)}→{index_stats.get('deduped_items', 0)} 条；"
                f"证据源 {len(packet.get('sources') or [])} 个，缺口 {len(gaps)} 个。"
            ),
            status="completed" if modules else "skipped",
        ))
        # 保留每个真实模块的可读摘要，供最终答案卡、历史记录和 SSE 之外的
        # “查看核对记录”复核；不写入参数、不暴露模型内部提示词。
        trace_title_map = {
            "get_market_quote": "实时行情",
            "get_valuation": "估值快照",
            "get_financials": "财报摘要",
            "get_financial_statements": "财务三表",
            "get_fund_flow": "资金流向",
            "get_stock_news": "公司动态",
            "get_analyst_consensus": "卖方一致预期",
            "get_stock_announcements": "公司公告",
            "get_dividend_history": "分红历史",
            "get_site_fast_news": "稻草财经快讯",
            "get_site_articles": "稻草财经文章",
            "get_stock_research": "券商研报",
            "get_recent_research": "投行研报",
            "get_institution_notes": "机构纪要",
            "get_recent_content_digest": "最近窗口四类资料全量扫描",
            "get_celebrity_views": "名人观点",
            "get_industry_context": "行业扫描",
            "get_peer_comparison": "同行候选",
            "get_supply_chain_context": "上下游线索",
            "compare_stocks": "多股同口径对比",
        }
        for trace in packet.get("traces") or []:
            if not isinstance(trace, dict):
                continue
            tool_name = str(trace.get("tool") or "研究数据")
            tool_traces.append(DulusToolTrace(
                tool=tool_name,
                title=trace_title_map.get(tool_name, str(trace.get("source") or "研究数据")),
                output=str(trace.get("summary") or "已完成核对")[:220],
                status="completed" if trace.get("ok") else "skipped",
            ))
        comparison_meta = packet.get("comparison") or {}
        if comparison_meta.get("is_comparison"):
            comparison_stocks = comparison_meta.get("stocks") or []
            basis = comparison_meta.get("comparison_basis") if isinstance(comparison_meta, dict) else {}
            basis = basis if isinstance(basis, dict) else {}
            strict = basis.get("is_strictly_comparable") is True
            tool_traces.insert(1, DulusToolTrace(
                tool="comparison_symmetry",
                title="比较核对 · 报告期与快照口径",
                input="、".join(str(item.get("symbol") or item.get("name") or "") for item in comparison_stocks if isinstance(item, dict)),
                output=(
                    f"已锁定 {len(comparison_stocks)} 个比较标的；"
                    + ("财务字段为共同报告期，可在有证据时横向比较；"
                       if strict else
                       "财报期不一致，仅比较同一抓取时点的行情/估值快照，财务字段分别展示；")
                    + "单边缺口不会被解释成偏好。"
                ),
                status="completed" if len(comparison_stocks) >= 2 else "skipped",
            ))

    turns = await asyncio.gather(*[
        _run_participant(llm, request, participant_id, tool_traces, citable_sources)
        for participant_id in participant_ids
    ])
    synthesis = await _build_synthesis(llm, request, turns, citable_sources)
    confidence = _average([turn.confidence for turn in turns] + [synthesis.confidence])
    decision = _derive_decision(turns, synthesis, tool_traces)
    warnings = _warnings_for_request(request, selected_tool_ids, tool_records)
    if getattr(llm, "provider", "mock") == "mock":
        warnings.insert(0, "当前为本地模板模式，未调用云模型；结论仅基于规则和已取到的数据。")
    if any(turn.provider == "local-fallback" for turn in [*turns, synthesis]):
        warnings.insert(0, "云模型当前不可用或额度受限，已使用统一取数生成多角色事实结论。")
    for gap in list(packet.get("gaps") or [])[:4]:
        warnings.append(f"研究数据缺口：{_format_research_gap(str(gap))}")
    # 把真实供应商放在工具轨迹前，用户先看到“数据从哪里来”，而不是一串内部模块名。
    sources = list(packet.get("sources") or [])
    if packet and _wechat_reference_for_question(objective):
        sources.append("微信 AI 历史回答（仅作交叉核验）")
    sources.extend(_sources_for_request(request, tool_traces))
    sources = list(dict.fromkeys(sources))[:12]
    research_steps = _research_steps(request, packet, tool_traces, turns)
    evidence_highlights = _evidence_highlights(request, packet)
    memory_notes = _build_memory_notes(request, turns)
    _save_roundtable_memory(request, synthesis.content, memory_notes, decision, confidence)

    return DulusRoundtableResponse(
        provider=_provider_name(llm),
        model=_model_name(llm),
        generated_at=datetime.now(timezone.utc),
        mode=request.mode,
        objective=objective,
        turns=turns,
        synthesis=synthesis.content,
        decision=decision,
        memory_notes=memory_notes,
        tool_traces=tool_traces,
        warnings=warnings,
        sources=sources,
        citable_sources=citable_sources,
        confidence=confidence,
        content_scope=packet.get("content_scope") or {},
        research_steps=research_steps,
        evidence_highlights=evidence_highlights,
        evidence_references=packet.get("evidence_references") or [],
        answer_protocol_version=DULUS_ANSWER_PROTOCOL_VERSION,
    )


def _sources_prompt_block(citable_sources: "list[DulusCitableSource] | None") -> str:
    """编号可引用来源 + [n] 内联标注指令（参与者/主席共用，与分析师快答/深研对称）。"""
    if not citable_sources:
        return ""
    lines = [
        f"[{s.n}] 《{(s.title or '')[:50]}》— {s.source}〔可信度：{_cred_tier_label(s.credibility)}〕"
        for s in citable_sources
    ]
    return (
        "\n【可引用来源（按编号 + 可信度档）】\n" + "\n".join(lines) +
        "\n在 content/key_points 里引用证据支撑的结论时用 [n] 内联标注（如 [1][3]），"
        "只引已列编号；优先高可信、直接相关来源，存疑来源仅能交叉印证时用。\n"
    )


async def _run_participant(
    llm: Any,
    request: DulusRoundtableRequest,
    participant_id: str,
    tool_traces: list[DulusToolTrace],
    citable_sources: "list[DulusCitableSource] | None" = None,
) -> DulusAgentTurn:
    definition = PARTICIPANT_DEFINITIONS[participant_id]
    if getattr(llm, "provider", "mock") == "mock":
        return _mock_turn(llm, request, participant_id, tool_traces)

    from .buy_side_qa import build_buy_side_mandate

    buy_side_prompt = build_buy_side_mandate(request.objective, request.context).roundtable_prompt()
    payload = _roundtable_payload(request, tool_traces)
    prompt = (
        f"你是 Dulus Runtime 圆桌里的 {definition['name']}。"
        f"你的立场：{definition['brief']}\n"
        "请基于输入生成严格 JSON：content, key_points, risks, actions, confidence。"
        "content 不超过 180 个中文字符；数组最多 4 项，每项不超过 28 个中文字符；"
        "源纪律：对输入里的证据先判断与议题的相关性和可信度——优先用高可信、直接相关的；"
        "论坛传闻/营销号/单一来源需谨慎，不当作确定结论；证据不足就明说缺口、不要编造实时数据。"
        "不要给确定性交易指令。"
        + ("本题是方法教学题：只讲抽象计算、口径和假设例子；不得把任意公司、资产、收购、行业事件或历史数字当作事实，"
           "也不要引用上下文中的实时数据。\n" if _is_method_question(request.objective) else "\n")
        + buy_side_prompt
        + f"{_sources_prompt_block(citable_sources)}"
        + f"输入：{json.dumps(payload, ensure_ascii=False)}"
    )
    try:
        data = await llm.complete_json(
            prompt,
            max_tokens=900,
            timeout_seconds=24,
            force_json_first=True,
            retry_schema_hint="必须返回 content, key_points, risks, actions, confidence。",
        )
        return DulusAgentTurn(
            participant_id=participant_id,
            participant_name=definition["name"],
            provider=_provider_name(llm),
            model=_model_name(llm),
            stance=definition["stance"],
            content=_clean_text(data.get("content"), fallback=_fallback_content(request, participant_id)),
            key_points=_safe_list(data.get("key_points"))[:4],
            risks=_safe_list(data.get("risks"))[:4],
            actions=_safe_list(data.get("actions"))[:4],
            confidence=_safe_confidence(data.get("confidence"), 0.62),
            tool_traces=_traces_for_participant(participant_id, tool_traces),
        )
    except Exception:
        return _mock_turn(llm, request, participant_id, tool_traces, local_fallback=True)


async def _build_synthesis(
    llm: Any,
    request: DulusRoundtableRequest,
    turns: list[DulusAgentTurn],
    citable_sources: "list[DulusCitableSource] | None" = None,
) -> DulusAgentTurn:
    if getattr(llm, "provider", "mock") == "mock":
        return _mock_synthesis(llm, request, turns)

    from .buy_side_qa import build_buy_side_mandate

    buy_side_mandate = build_buy_side_mandate(request.objective, request.context)
    buy_side_prompt = buy_side_mandate.roundtable_prompt()
    synthesis_length = "900-1400" if buy_side_mandate.key_variable_target >= 15 else "450-700"
    synthesis_token_budget = 2400 if buy_side_mandate.key_variable_target >= 15 else 1500
    prompt = (
        "你是 DeepFocus 的投研主编。请综合多个 Agent 的观点，输出严格 JSON："
        "content, key_points, risks, actions, confidence。"
        "content 必须按以下顺序输出四个 Markdown 粗体小标题，标题文字不可改："
        "**结论**、**核心依据**、**风险与反证**、**下一步核验**。"
        f"每个标题后写 1-3 句短段落；全文 {synthesis_length} 个中文字符，先回答用户问题，再解释依据，"
        "不要把 Agent 名称、内部流程、工具函数名、模型名或供应商名写进 content。"
        "源纪律：综合时优先采纳有高可信、直接相关证据支撑的观点；对仅凭传闻/单一来源的判断要点明「待核验」；"
        "Agent 之间结论冲突时点明分歧、不要简单取一。不要编造实时数据，不要输出确定性买卖建议。"
        "如果任务是比较题，必须在结论和核心依据中同时写出每个标的的名称或代码，"
        "只比较双方都有且被 comparison_basis 允许的字段；绝不能因为某一方资料命中更多，就把资料覆盖差异写成投资偏好。"
        "若 comparison_basis.is_strictly_comparable=false，只能比较同一抓取时点的行情/估值快照；"
        "财务指标必须分别写明 report_date/period_label，禁止写‘同口径’、‘同一报告期’或做 ROE/增速/EPS 横向排名。"
        "若关键字段不对称，结论必须写成条件化选择或‘暂不偏向’，并明确缺口。"
        + ("本题是方法教学题：只讲抽象方法和假设数字；不得借宁德时代、比亚迪或其他公司编造资产、收购、商誉、行业事件等事实。\n"
           if _is_method_question(request.objective) else "\n")
        + buy_side_prompt
        + f"{_sources_prompt_block(citable_sources)}"
        + f"任务：{request.objective[:800]}\n"
        + f"上下文：{request.context[:12000]}\n"
        + f"圆桌发言：{json.dumps([turn.model_dump() for turn in turns], ensure_ascii=False)}"
    )
    try:
        data = await llm.complete_json(
            prompt,
            max_tokens=synthesis_token_budget,
            timeout_seconds=28,
            force_json_first=True,
            retry_schema_hint="必须返回包含四段式 content 的圆桌 synthesis JSON。",
        )
        key_points = _safe_list(data.get("key_points"))[:5]
        risks = _safe_list(data.get("risks"))[:5]
        actions = _safe_list(data.get("actions"))[:5]
        content = _professional_synthesis_content(
            data.get("content"), key_points, risks, actions,
            fallback=_fallback_synthesis_content(request, turns),
        )
        if _is_method_question(request.objective):
            content = _method_safety_content(content)
        comparison_items = _comparison_items_from_context(request.context) if is_comparison_question(request.objective) else []
        if len(comparison_items) >= 2:
            required_sides = [
                [str(item.get("name") or ""), str(item.get("symbol") or "")]
                for item in comparison_items[:4]
            ]
            # 发布前硬拦截：模型漏掉任一边或复述“仅一边有数据”时，改用保守的逐边事实兜底。
            all_sides_present = all(any(label and label in content for label in labels) for labels in required_sides)
            if not all_sides_present or re.search(r"仅.*(?:一方|单边)|只.*(?:宁德时代|比亚迪).*(?:数据|资料)", content):
                content = _comparison_fallback_content(request, turns)
            content = _enforce_comparison_contract(request, content)
        return DulusAgentTurn(
            participant_id="synthesis",
            participant_name="Mesa Redonda",
            provider=_provider_name(llm),
            model=_model_name(llm),
            stance="synthesis",
            content=content,
            key_points=key_points,
            risks=risks,
            actions=actions,
            confidence=_safe_confidence(data.get("confidence"), _average([turn.confidence for turn in turns])),
        )
    except Exception:
        return _mock_synthesis(llm, request, turns, local_fallback=True)


def _run_safe_tools(
    request: DulusRoundtableRequest,
    selected_tool_ids: list[str],
    tool_records: dict[str, DulusToolRecord],
) -> list[DulusToolTrace]:
    traces: list[DulusToolTrace] = []
    for tool_id in selected_tool_ids:
        tool = tool_records[tool_id]
        if tool.permission == "disabled":
            traces.append(DulusToolTrace(
                tool=tool_id,
                title=tool.name,
                input=tool.invocation,
                output="该能力在合规版中禁用。",
                status="blocked",
            ))
            continue
        if tool.permission == "approval_required":
            traces.append(DulusToolTrace(
                tool=tool_id,
                title=tool.name,
                input=tool.invocation,
                output="需要人工确认后才能执行，本次只生成计划。",
                status="skipped",
            ))
            continue
        traces.append(_run_read_only_tool(request, tool))
    return traces


def _run_read_only_tool(request: DulusRoundtableRequest, tool: DulusToolRecord) -> DulusToolTrace:
    stock = request.stock
    if tool.id == "market_snapshot":
        if not stock:
            output = "未选择标的，跳过行情快照。"
            status = "skipped"
        else:
            output = (
                f"{stock.symbol} {stock.name}，市场 {stock.market or '未知'}，"
                f"价格 {_fmt_number(stock.current_price)}，涨跌幅 {_fmt_percent(stock.change_percent)}。"
            )
            status = "completed"
        return DulusToolTrace(tool=tool.id, title=tool.name, input=stock.symbol if stock else "", output=output, status=status)

    if tool.id == "evidence_lookup":
        keywords = _extract_keywords(f"{request.objective} {request.context}")[:6]
        # 真实检索证据库（按聚焦标的 + 关键词），让工具轨迹反映实际命中数。
        hit_count = 0
        try:
            symbol = request.stock.symbol if request.stock else None
            query = " ".join(keywords[:4]) if keywords else None
            if symbol:
                hits = list_data_items(symbol=symbol, query=query, limit=10, sort="time_desc") \
                    or list_data_items(symbol=symbol, limit=10, sort="time_desc")
            elif query:
                hits = list_data_items(query=query, limit=10, sort="time_desc")
            else:
                hits = []
            hit_count = len({(h.title or "").strip() for h in hits if (h.title or "").strip()})
        except Exception:
            hit_count = 0
        evidence_note = (
            f"证据库命中 {hit_count} 条相关条目，已编号供引用。" if hit_count
            else "证据库未命中相关条目，建议在数据源中心补充后复核。"
        )
        output = (
            f"上下文 {len(request.context)} 字符；关键词：{', '.join(keywords) if keywords else '未识别'}。{evidence_note}"
        )
        return DulusToolTrace(tool=tool.id, title=tool.name, input=request.objective[:120], output=output)

    if tool.id == "risk_review":
        flags = []
        text = f"{request.objective}\n{request.context}"
        if re.search(r"必涨|稳赚|满仓|all in|梭哈|翻倍", text, re.I):
            flags.append("存在高确定性或仓位越界措辞")
        if len(request.context.strip()) < 80:
            flags.append("上下文偏少")
        output = "；".join(flags) if flags else "未发现明显越界措辞，仍需补充来源和反证。"
        return DulusToolTrace(tool=tool.id, title=tool.name, input="risk lint", output=output)

    if tool.id == "report_outline":
        output = "报告结构：事实证据、圆桌共识、关键分歧、风险纪律、下一步核验。"
        return DulusToolTrace(tool=tool.id, title=tool.name, input=request.mode, output=output)

    if tool.id == "authorized_webbridge_inspect":
        if not request.authorized_webbridge_url:
            return DulusToolTrace(
                tool=tool.id,
                title=tool.name,
                input="",
                output="未提供授权 URL，跳过网页检查。",
                status="skipped",
            )
        result = inspect_authorized_webbridge(
            DulusWebBridgeInspectRequest(url=request.authorized_webbridge_url, mode="text")
        )
        if not result.allowed:
            return DulusToolTrace(
                tool=tool.id,
                title=tool.name,
                input=request.authorized_webbridge_url,
                output=result.policy,
                status="blocked",
            )
        return DulusToolTrace(
            tool=tool.id,
            title=tool.name,
            input=request.authorized_webbridge_url,
            output=f"{result.title}：{result.text_preview[:220]}",
        )

    if tool.id == "mem_palace_save":
        return DulusToolTrace(
            tool=tool.id,
            title=tool.name,
            input="roundtable summary",
            output="圆桌完成后会自动写入 MemPalace。",
        )

    if tool.id == "repo_search":
        output = "已规划仓库检索：schemas、services、components、agent runtime。"
        return DulusToolTrace(tool=tool.id, title=tool.name, input=request.objective[:120], output=output)

    return DulusToolTrace(tool=tool.id, title=tool.name, input=tool.invocation, output="工具已登记，本次未触发。", status="skipped")


def _mock_turn(
    llm: Any,
    request: DulusRoundtableRequest,
    participant_id: str,
    tool_traces: list[DulusToolTrace],
    *,
    local_fallback: bool = False,
) -> DulusAgentTurn:
    definition = PARTICIPANT_DEFINITIONS[participant_id]
    stock_label = _stock_label(request.stock)
    fact_content = _fallback_content(request, participant_id)
    content_map = {
        "evidence": fact_content if _research_facts(request) else f"{stock_label} 的任务需要先核验行情、公告、研报和社区材料；当前上下文可形成初筛，但证据链还不能当作结论。",
        "research": fact_content if _research_facts(request) else "核心假设应拆成催化、基本面验证和时间窗口。先把可证伪指标列清楚，再决定是否进入长任务。",
        "risk": fact_content if _research_facts(request) else "最重要的是防止把单一叙事当成确定性机会；需要设置反证、仓位边界和失效触发器。",
        "operator": f"建议按证据检索、行情快照、风险纪律、报告骨架四步执行，涉及写操作或命令执行时保留人工确认。",
    }
    point_map = {
        "evidence": ["补足来源覆盖", "区分事实与观点", "标注证据可信度"],
        "research": ["形成可验证假设", "拆催化和反证", "对齐投资周期"],
        "risk": ["先列亏损路径", "避免确定性措辞", "设置退出条件"],
        "operator": ["先读后写", "高风险工具审批", "保留工具轨迹"],
    }
    return DulusAgentTurn(
        participant_id=participant_id,
        participant_name=definition["name"],
        provider="local-fallback" if local_fallback else _provider_name(llm),
        model="dulus-local-template" if local_fallback else _model_name(llm),
        stance=definition["stance"],
        content=content_map[participant_id],
        key_points=point_map[participant_id],
        risks=["资料不足会误判", "外部数据需复核"] if participant_id != "risk" else ["单一叙事过热", "仓位纪律缺失", "高风险工具越权"],
        actions=["补充证据", "运行长任务", "保留反证清单"],
        confidence=max(0.5, _research_confidence(request) - (0.03 if participant_id == "evidence" else 0.01 if participant_id == "risk" else 0)),
        tool_traces=_traces_for_participant(participant_id, tool_traces),
    )


def _mock_synthesis(
    llm: Any,
    request: DulusRoundtableRequest,
    turns: list[DulusAgentTurn],
    *,
    local_fallback: bool = False,
) -> DulusAgentTurn:
    content = _fallback_synthesis_content(request, turns)
    return DulusAgentTurn(
        participant_id="synthesis",
        participant_name="Mesa Redonda",
        provider="local-fallback" if local_fallback else _provider_name(llm),
        model="dulus-local-template" if local_fallback else _model_name(llm),
        stance="synthesis",
        content=content,
        key_points=["先补证据", "再跑长任务", "最后做风险复核"],
        risks=["WebBridge 捕获禁用", "mock 模型仅供演示"],
        actions=["接入真实模型", "配置证据库", "将结论转 Agent 任务"],
        confidence=_research_confidence(request) if _research_facts(request) else _average([turn.confidence for turn in turns]),
    )


def _professional_synthesis_content(
    content: Any,
    key_points: list[str],
    risks: list[str],
    actions: list[str],
    *,
    fallback: str,
) -> str:
    """把云模型短答收敛为用户可读的投研四段式。

    模型偶尔会返回一段没有标题的摘要。这里用已返回的结构化数组补齐层级，
    既不添加事实，也让云模型和本地兜底在前端呈现出同一种专业结构。
    """
    text = str(content or "").strip()
    required = ("**结论**", "**核心依据**", "**风险与反证**", "**下一步核验**")
    if text and all(marker in text for marker in required):
        return _normalize_sectioned_content(text, fallback=fallback, limit=1800)

    basis = [item.strip() for item in key_points if item.strip()][:4]
    risk_items = [item.strip() for item in risks if item.strip()][:4]
    action_items = [item.strip() for item in actions if item.strip()][:4]
    if not basis:
        basis = ["已取到的行情、估值与财报事实需结合口径一起解读。"]
    if not risk_items:
        risk_items = ["关键数据存在时点或来源缺口，结论需要后续复核。"]
    if not action_items:
        action_items = ["复核最新财报、估值口径与关键催化，确认结论是否仍成立。"]
    conclusion = text or "当前证据可以形成初步判断，但不足以支持确定性交易结论。"
    sections = [
        f"**结论**：{conclusion}",
        "**核心依据**：\n" + "\n".join(f"- {item}" for item in basis),
        "**风险与反证**：\n" + "\n".join(f"- {item}" for item in risk_items),
        "**下一步核验**：\n" + "\n".join(f"- {item}" for item in action_items),
    ]
    return _normalize_sectioned_content("\n\n".join(sections), fallback=fallback, limit=1800)


_SYNTHESIS_HEADINGS = ("结论", "核心依据", "风险与反证", "下一步核验")
_SYNTHESIS_SECTION_LIMITS = {
    "结论": 360,
    "核心依据": 500,
    "风险与反证": 350,
    "下一步核验": 300,
}


def _trim_sentence_boundary(text: str, limit: int) -> str:
    """在句末/列表项边界截断，避免 UI 出现半个句子。"""
    value = re.sub(r"[ \t\r\f\v]+", " ", str(text or "")).strip()
    value = re.sub(r"\n{3,}", "\n\n", value)
    if len(value) <= limit:
        return value
    head = value[:max(80, limit)]
    cut = max(head.rfind(mark) for mark in "。！？；\n")
    if cut < max(60, int(limit * 0.52)):
        cut = head.rfind("，")
    return (head[:cut + 1] if cut >= 60 else head).rstrip(" ，、") + "…"


def _normalize_sectioned_content(text: str, *, fallback: str, limit: int = 1800) -> str:
    """统一四段式输出，只保留每个标题第一次出现，并按段落边界限长。"""
    marker_re = re.compile(r"\*\*(结论|核心依据|风险与反证|下一步核验)\*\*\s*[:：]?")
    matches = list(marker_re.finditer(str(text or "")))
    if not matches:
        return _clean_text(fallback, fallback=fallback, limit=limit)
    bodies: dict[str, str] = {}
    for index, match in enumerate(matches):
        heading = match.group(1)
        if heading in bodies:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        bodies[heading] = str(text[match.end():end]).strip()
    if any(heading not in bodies for heading in _SYNTHESIS_HEADINGS):
        return _clean_text(fallback, fallback=fallback, limit=limit)
    sections = []
    for heading in _SYNTHESIS_HEADINGS:
        body = bodies.get(heading, "")
        body = re.sub(r"\*\*(?:结论|核心依据|风险与反证|下一步核验)\**", "", body)
        body = _trim_sentence_boundary(body, _SYNTHESIS_SECTION_LIMITS[heading])
        separator = "\n" if body.startswith(("-", "•", "1.")) else ""
        sections.append(f"**{heading}**：{separator}{body}")
    result = "\n\n".join(sections)
    # 分段预算应已足够；最后一道保护仍在句末截断，而不是硬切半句。
    return _trim_sentence_boundary(result, limit)


def _comparison_basis_from_context(context: str) -> dict[str, Any]:
    modules = _research_modules_from_context(context)
    bundle = modules.get("compare_stocks")
    if not isinstance(bundle, dict):
        return {}
    basis = bundle.get("comparison_basis")
    return basis if isinstance(basis, dict) else {}


def _comparison_period_line(items: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in items[:4]:
        name = str(item.get("name") or item.get("symbol") or "该标的")
        fin = item.get("financials") if isinstance(item.get("financials"), dict) else {}
        period = str(fin.get("period_label") or fin.get("report_date") or "报告期未返回")
        parts.append(f"{name}：{period}")
    return "；".join(parts)


def _enforce_comparison_contract(request: DulusRoundtableRequest, content: str) -> str:
    """发布前把“各自最新财报”误写成“同口径比较”的模型文本拉回安全口径。"""
    items = _comparison_items_from_context(request.context)
    basis = _comparison_basis_from_context(request.context)
    if len(items) < 2 or basis.get("is_strictly_comparable") is True:
        return content
    normalized = re.sub(r"同口径(?:快照|数据|字段|比较)?", "当前估值快照", content)
    normalized = re.sub(r"同一报告期", "各自报告期", normalized)
    normalized = re.sub(r"可比(?:的)?财务字段", "分别展示的财务字段", normalized)
    # 这类短语通常是模型把最新各期数据直接排了名；保留事实，但撤销横向结论。
    normalized = re.sub(
        r"[^。\n]{0,36}(?:ROE|营收同比|净利同比|利润增速|毛利率|EPS)[^。\n]{0,24}(?:明显|显著|更高|更强|领先|占优)[^。\n]{0,36}。?",
        "财务指标因报告期不同仅分别展示，不作横向排名。",
        normalized,
        flags=re.I,
    )
    period_line = _comparison_period_line(items)
    guard = (
        f"口径提示：财报期不同（{period_line}）；当前只比较同一抓取时点的行情/估值快照，"
        "营收、利润、ROE、毛利率和 EPS 仅分别展示。"
    )
    if "口径提示：" not in normalized:
        normalized = normalized.replace("**结论**：", f"**结论**：{guard}\n", 1)
    return _normalize_sectioned_content(normalized, fallback=normalized, limit=1800)


def _method_safety_content(content: str) -> str:
    """方法题若模型偷偷带入公司事实，改为不依赖任何公司的抽象答案。"""
    if re.search(r"宁德时代|比亚迪|贵州茅台|公司公告|收购|商誉|资产负债表显示|行业事件", content):
        return (
            "**结论**：PE 看盈利倍数，PB 看净资产倍数，PEG 用于把 PE 与盈利增速放在一起观察；三者只能作为框架，不能单独下结论。\n\n"
            "**核心依据**：假设某公司 PE=20 倍、未来盈利增速 25%，则 PEG≈0.8；若 PB=4 倍，还要结合 ROE、现金流和资产质量判断估值是否有支撑。\n\n"
            "**风险与反证**：PEG 对周期、负增长和盈利预测极敏感；负利润、一次性收益或不同会计口径会让比较失真。\n\n"
            "**下一步核验**：先统一 TTM/预测期、币种和报告期，再核对盈利增速来源、ROE 质量与现金流，最后才做同业比较。"
        )
    return _normalize_sectioned_content(content, fallback=content, limit=1800)


def _research_steps(
    request: DulusRoundtableRequest,
    packet: dict[str, Any],
    tool_traces: list[DulusToolTrace],
    turns: list[DulusAgentTurn],
) -> list[dict[str, Any]]:
    """生成面向用户的可审计路径，不暴露模型隐藏推理链。"""
    modules = packet.get("modules") or {}
    scope = packet.get("content_scope") or {}
    stats = ((modules.get("research_index") or {}).get("stats") or {}) if isinstance(modules, dict) else {}
    source_count = len(packet.get("sources") or []) if packet else len({trace.tool for trace in tool_traces})
    available_count = sum(
        1 for item in scope.values()
        if isinstance(item, dict) and item.get("status") == "available"
    )
    available_labels = [
        str(label) for label, item in scope.items()
        if isinstance(item, dict) and item.get("status") == "available"
    ]
    gap_labels = [
        str(label) for label, item in scope.items()
        if isinstance(item, dict) and item.get("status") not in {"available"}
    ]
    data_count = len(modules) if isinstance(modules, dict) else 0
    digest = modules.get("get_recent_content_digest") if isinstance(modules, dict) else None
    digest_coverage = (digest.get("coverage") if isinstance(digest, dict) else None) or {}
    digest_detail = ""
    if digest_coverage:
        parts = []
        for label, item in digest_coverage.items():
            if isinstance(item, dict):
                parts.append(f"{label}{item.get('matched_count', 0)}/{item.get('scanned_count', 0)}")
        if parts:
            digest_detail = f"；窗口全量扫描：{'、'.join(parts)}"
    return [
        {
            "id": "collect",
            "label": "数据接入",
            "status": "completed" if data_count or tool_traces else "partial",
            "detail": (
                f"并行读取 {data_count or len(tool_traces)} 个数据模块，已覆盖 "
                f"{available_count or '0'} 类资料"
                f"（{'、'.join(available_labels[:10]) if available_labels else '暂无命中'}）"
                f"；缺口：{'、'.join(gap_labels[:6]) if gap_labels else '无'}{digest_detail}。"
            ),
            "count": data_count or len(tool_traces),
        },
        {
            "id": "screen",
            "label": "证据筛选",
            "status": "completed" if stats else ("partial" if packet else "completed"),
            "detail": (
                f"去重 {stats.get('raw_items', 0)}→{stats.get('deduped_items', 0)} 条，"
                f"选入 {stats.get('selected_items', stats.get('deduped_items', 0))} 条。"
                if stats else "按相关性、新鲜度和可信度筛选可用证据。"
            ),
            "count": int(stats.get("selected_items", stats.get("deduped_items", 0)) or 0),
        },
        {
            "id": "crosscheck",
            "label": "交叉验证",
            "status": "completed" if source_count >= 2 else "partial",
            "detail": f"已关联 {source_count} 个证据来源，标记数据缺口与待核验项。",
            "count": source_count,
        },
        {
            "id": "debate",
            "label": "多空评估",
            "status": "completed" if turns else "partial",
            "detail": f"{len(turns)} 个分析视角分别复核事实、假设与风险。",
            "count": len(turns),
        },
        {
            "id": "synthesis",
            "label": "结论输出",
            "status": "completed",
            "detail": "将事实、反证和后续核验动作合并为最终回答。",
            "count": 1,
        },
    ]


def _evidence_highlights(request: DulusRoundtableRequest, packet: dict[str, Any]) -> list[str]:
    """从研究数据包提取可复核的事实摘要，避免 UI 只展示“调用了工具”。"""
    facts = _research_facts(request)
    if not facts:
        return []
    highlights: list[str] = []
    for label, key in (
        ("估值快照", "valuation_line"),
        ("业绩快照", "earnings_line"),
        ("市场预期", "consensus_line"),
        ("资金与催化", "flow_line"),
        ("资料检索", "content_line"),
    ):
        value = str(facts.get(key) or "").strip().rstrip("。")
        if value:
            highlights.append(f"{label}：{value}。")
    if facts.get("gap_line"):
        highlights.append(f"待核验：{facts['gap_line']}。")
    return highlights[:6]


def _select_participants(raw: list[str]) -> list[str]:
    selected = [item for item in raw if item in PARTICIPANT_DEFINITIONS]
    if not selected:
        selected = ["evidence", "research", "risk"]
    unique = list(dict.fromkeys(selected))
    return unique[:4]


def _select_tools(raw: list[str], records: dict[str, DulusToolRecord]) -> list[str]:
    if not raw:
        raw = ["market_snapshot", "evidence_lookup", "risk_review", "report_outline"]
    return [item for item in dict.fromkeys(raw) if item in records][:8]


def _warnings_for_request(
    request: DulusRoundtableRequest,
    selected_tool_ids: list[str],
    records: dict[str, DulusToolRecord],
) -> list[str]:
    warnings: list[str] = []
    if "webbridge_browser_capture" in selected_tool_ids:
        warnings.append("WebBridge 会话捕获已阻断。")
    if "authorized_webbridge_inspect" in selected_tool_ids and request.authorized_webbridge_url:
        allowed, reason = _is_authorized_url(request.authorized_webbridge_url)
        if not allowed:
            warnings.append(f"授权 WebBridge 已拒绝该 URL：{reason}")
    if any(records[tool_id].permission == "approval_required" for tool_id in selected_tool_ids):
        warnings.append("部分高风险工具需要人工确认，本次只输出计划。")
    if not request.context.strip():
        warnings.append("上下文为空，圆桌只会给出框架性建议。")
    return warnings


def _sources_for_request(request: DulusRoundtableRequest, traces: list[DulusToolTrace]) -> list[str]:
    sources = ["DeepFocus Dulus Runtime"]
    if request.stock:
        sources.append(f"当前页面标的：{request.stock.symbol}")
    sources.extend([f"工具轨迹：{trace.title}" for trace in traces if trace.status == "completed"])
    return sources[:8]


def _wechat_reference_for_question(question: str, max_age_seconds: float = 7 * 86400) -> str:
    """读取同一标准化问题的微信 AI 短期缓存，作为第二意见而不是事实源。

    微信答案本身已经经过出口合规过滤；这里仍只取同问题的共享缓存，不读取任何用户会话，
    并在 Harness 上下文中明确要求再次用公开数据核验，避免把微信侧旧行情直接当成当前事实。
    """
    try:
        from . import data_store
        from .weixin_channel import _qa_fingerprint
        fingerprint = _qa_fingerprint(question)
        if not fingerprint:
            return ""
        cached = data_store.latest("wx_qa", fingerprint, max_age_seconds=max_age_seconds)
        answer = cached.get("answer") if isinstance(cached, dict) else ""
        return str(answer or "").strip()[:2400]
    except Exception:
        return ""


def _build_memory_notes(request: DulusRoundtableRequest, turns: list[DulusAgentTurn]) -> list[str]:
    notes = [
        f"目标：{request.objective[:80]}",
        f"模式：{request.mode}",
    ]
    if request.stock:
        notes.append(f"标的：{request.stock.symbol} {request.stock.name}")
    strong_actions = [action for turn in turns for action in turn.actions[:1]]
    if strong_actions:
        notes.append(f"下一步：{strong_actions[0]}")
    return notes[:4]


def _derive_decision(
    turns: list[DulusAgentTurn],
    synthesis: DulusAgentTurn,
    tool_traces: list[DulusToolTrace],
) -> str:
    if any(trace.status == "blocked" for trace in tool_traces):
        return "blocked"
    combined = "\n".join([synthesis.content, *[item for turn in turns for item in turn.risks]])
    confidence = _average([turn.confidence for turn in turns] + [synthesis.confidence])
    if re.search(r"资料不足|证据不足|上下文偏少|补充", combined):
        return "research_more"
    if re.search(r"暂避|回避|越界|高风险", combined) and confidence < 0.7:
        return "watch"
    return "candidate" if confidence >= 0.62 else "watch"


def _roundtable_payload(request: DulusRoundtableRequest, traces: list[DulusToolTrace]) -> dict[str, Any]:
    stock_payload = request.stock.model_dump(by_alias=True) if request.stock else None
    return {
        "objective": request.objective[:1200],
        # Harness 已把五类站内内容和公开数据压成紧凑包；只截到 3k 会把文章/纪要/观点
        # 截掉，圆桌就退化成“只看行情”。保留完整包（极端超长时取前 14k）。
        "context": request.context[:14000],
        "mode": request.mode,
        "stock": stock_payload,
        "authorized_webbridge_url": request.authorized_webbridge_url,
        "tool_traces": [trace.model_dump() for trace in traces],
    }


def _traces_for_participant(participant_id: str, traces: list[DulusToolTrace]) -> list[DulusToolTrace]:
    if participant_id == "evidence":
        keys = {"evidence_lookup", "market_snapshot", "research_harness", "repo_search"}
    elif participant_id == "risk":
        keys = {"risk_review", "research_harness", "webbridge_browser_capture", "shell_executor", "mcp_gateway", "authorized_webbridge_inspect"}
    elif participant_id == "operator":
        keys = {trace.tool for trace in traces}
    else:
        keys = {"report_outline", "market_snapshot", "research_harness", "authorized_webbridge_inspect"}
    return [trace for trace in traces if trace.tool in keys]


def _fallback_content(request: DulusRoundtableRequest, participant_id: str) -> str:
    stock = _stock_label(request.stock)
    facts = _research_facts(request)
    if facts:
        valuation = facts.get("valuation_line") or "估值数据已返回"
        earnings = facts.get("earnings_line") or "盈利数据已返回"
        flow = facts.get("flow_line")
        if participant_id == "evidence":
            return _clean_text(
                f"{stock} 已拿到{valuation}；{earnings}。"
                f"{('另有' + flow + '。') if flow else '行情源存在时延，需以交易所/东财页面复核。'}",
                f"{stock} 需要补齐可追溯来源后再提升结论置信度。",
            )
        if participant_id == "research":
            return _clean_text(
                f"从估值看，{facts.get('valuation_assessment', '暂不能判断')}；{earnings}。"
                "下一步看盈利增速能否修复，而不是只看单一 PE。",
                f"{stock} 可以先形成假设，再用工具和证据逐项验证。",
            )
        if participant_id == "risk":
            return _clean_text(
                f"主要风险是{facts.get('risk_line', '盈利和估值数据的时点变化')}。"
                "需设置盈利下修、渠道价格走弱和估值回落的复核触发器。",
                f"{stock} 需要先检查反证、仓位纪律和资料缺口，避免直接进入执行。",
            )
    if participant_id == "risk":
        return f"{stock} 需要先检查反证、仓位纪律和资料缺口，避免直接进入执行。"
    if participant_id == "evidence":
        return f"{stock} 需要补齐可追溯来源后再提升结论置信度。"
    return f"{stock} 可以先形成假设，再用工具和证据逐项验证。"


def _fallback_synthesis_content(request: DulusRoundtableRequest, turns: list[DulusAgentTurn]) -> str:
    comparison_items = _comparison_items_from_context(request.context)
    if is_comparison_question(request.objective):
        if len(comparison_items) >= 2:
            return _comparison_fallback_content(request, turns)
        # 比较题连主表都没解析出来时（LLM 失败 + compare_stocks 异常叠加），
        # 仍要围绕“两边都没取到数”作答，而不是落回单股流程话术。
        sides = "、".join(
            str(item.get("name") or item.get("symbol") or "").strip()
            for item in comparison_items if isinstance(item, dict)
        )
        if not sides:
            sides = "、".join(_comparison_labels_from_objective(request.objective))
        sides = sides or "问题中提到的标的"
        return _normalize_sectioned_content(
            f"**结论**：{sides} 的比较本轮未取得可比数据，暂不偏向任何一方。\n\n"
            "**核心依据**：\n- 双方行情、估值与最新财报均未成功返回，没有可比较的字段。\n\n"
            "**风险与反证**：单边资料更完整不代表投资吸引力更高；数据缺口不构成偏好理由。\n\n"
            "**下一步核验**：稍后重试，或分别查询两边的估值与最新财报后再比较。",
            fallback=f"{sides} 的比较本轮未取得可比数据，暂不偏向任何一方。",
            limit=800,
        )
    stock = _stock_label(request.stock)
    if not turns:
        return f"{stock} 的任务已进入圆桌，但还没有有效发言；建议补充上下文后重试。"
    facts = _research_facts(request)
    if facts:
        stance = facts.get("valuation_assessment", "暂不能判断")
        conclusion = f"{stock} 当前估值{stance}"
        if "数据不足" not in stance and "偏低" in stance:
            conclusion += "，相对估值有一定安全边际，但仍需结合盈利持续性确认。"
        elif "数据不足" not in stance:
            conclusion += "，当前不属于明显低估，后续关键看盈利能否消化估值。"
        else:
            conclusion += "。"
        evidence_lines = [
            f"估值与质量：{facts.get('valuation_line') or '估值快照不完整'}。{facts.get('earnings_line') or '财报数据不完整'}。",
        ]
        if facts.get("consensus_line"):
            evidence_lines.append(f"市场预期：{facts['consensus_line']}（一致预期只代表机构观点，不等于内在价值）。")
        if facts.get("flow_line"):
            evidence_lines.append(f"资金与催化：{facts['flow_line']}。{facts.get('catalyst_line') or ''}".rstrip("。") + "。")
        elif facts.get("catalyst_line"):
            evidence_lines.append(f"公告与催化：{facts['catalyst_line']}。")
        if facts.get("dividend_line"):
            evidence_lines.append(f"股东回报：{facts['dividend_line']}。")
        bear_basis = []
        if facts.get("profit_yoy") is not None and facts.get("profit_yoy") < 0:
            bear_basis.append(f"净利同比 {facts['profit_yoy']:+.1f}%")
        if facts.get("pb") is not None and facts.get("pb") >= 5:
            bear_basis.append(f"PB 约 {facts['pb']:.1f} 倍、绝对值不低")
        if facts.get("flow_line") and "净流出" in facts.get("flow_line", ""):
            bear_basis.append(facts["flow_line"])
        if not bear_basis:
            bear_basis.append("当前 PE/PB 快照不完整，估值结论需要下一次行情核验")
        bull_basis = ["ROE、毛利率和经营现金流质量较强"]
        if facts.get("dividend_line"):
            bull_basis.append("分红记录提供一定回报支撑")
        evidence_lines.append(
            f"多空权衡：多头依据是{'、'.join(bull_basis)}；空头依据是{'、'.join(bear_basis)}。"
            f"估值能否继续消化，取决于盈利增速是否修复。{facts.get('research_line') or ''}".rstrip("。") + "。"
        )
        watch = facts.get("watch_line") or "下一期营收与净利增速、批价/库存、现金流和机构预期是否继续下修。"
        lines = [
            f"**结论**：{conclusion}",
            "**核心依据**：\n" + "\n".join(f"- {item}" for item in evidence_lines),
            "**风险与反证**：\n" + "\n".join(f"- {item}" for item in [
                f"需要警惕：{facts.get('risk_line') or '盈利增速和估值容错率变化'}。",
                f"反证条件：{watch}。",
            ]),
            "**下一步核验**：\n" + "\n".join(f"- {item}" for item in [
                f"跟踪：{watch}。",
                "将最新行情、财报和一致预期放在同一时点复核，再更新估值判断。",
            ]),
        ]
        if facts.get("content_line"):
            lines.append(f"**资料覆盖**：{facts['content_line']}。")
        if facts.get("gap_line"):
            lines.append(f"**数据缺口**：{facts['gap_line']}。")
        return _clean_text("\n".join(lines), f"圆桌共识：{stock} 先走证据补全和风险复核，再转长任务。", limit=1800)
    return (
        f"圆桌共识：{stock} 先走证据补全和风险复核，再转长任务。"
        "当前适合形成研究计划，不适合直接给确定性交易结论。"
    )


def _research_modules_from_context(context: str) -> dict[str, Any]:
    """从统一 Harness 的上下文中恢复紧凑数据模块，供本地兜底回答使用。

    云模型超时/返回坏 JSON 时，不能丢掉已经取到的事实。Harness 为了控制
    token 会把每个模块压成 JSON 字符串，这里逐模块解包；某一个模块被截断
    只会被跳过，不影响其他模块继续生成结论。
    """
    marker = "数据模块："
    start = (context or "").rfind(marker)
    if start < 0:
        return {}
    payload = (context or "")[start + len(marker):]
    end = payload.find("\n证据缺口：")
    if end >= 0:
        payload = payload[:end]
    try:
        compact = json.loads(payload.strip())
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(compact, dict):
        return {}
    modules: dict[str, Any] = {}
    for key, value in compact.items():
        if isinstance(value, str):
            try:
                modules[str(key)] = json.loads(value)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        elif isinstance(value, (dict, list)):
            modules[str(key)] = value
    return modules


def _comparison_items_from_context(context: str) -> list[dict[str, Any]]:
    """从 Harness 紧凑数据包取出比较主表，供云模型失败时的安全兜底使用。"""
    modules = _research_modules_from_context(context)
    bundle = modules.get("compare_stocks")
    if not isinstance(bundle, dict) or not isinstance(bundle.get("items"), list):
        return []
    return [item for item in bundle["items"] if isinstance(item, dict)]


def _comparison_labels_from_objective(objective: str) -> list[str]:
    """从原问题恢复比较标的，避免取数全失败时回答变成「问题中提到的标的」。

    这条路径只用于错误/降级文案，不参与取数和投资判断；因此宁可少识别，也不
    凭空猜股票。中文名称和 A 股代码走现有名称索引，港美 ticker 只保留常见的
    字母/数字 token，并过滤 PE/PB/ROE 等指标缩写。
    """
    text = str(objective or "")
    found: list[tuple[int, str]] = []
    seen: set[str] = set()
    name_of_code = lambda _code: ""
    try:
        from .stock_name_index import all_name_code, name_of_code

        name_code = all_name_code()
        # 长名称优先，避免「比亚迪电子」同时命中「比亚迪」时拆成两只股票。
        for name, code in sorted(name_code.items(), key=lambda item: (-len(str(item[0])), str(item[0]))):
            label = str(name or "").strip()
            if len(label) < 3 or label not in text:
                continue
            symbol = str(code or "").strip()
            key = symbol or label
            if key in seen:
                continue
            found.append((text.find(label), f"{label}（{symbol}）" if symbol else label))
            seen.add(key)
    except Exception:  # noqa: BLE001 - 降级文案不能阻断主回答
        pass

    # 代码/海外 ticker 仅作为名称索引没有命中时的补充；不把自然语言缩写显示成标的。
    for match in re.finditer(r"(?<![A-Za-z0-9])(?:\d{6}|[A-Z]{1,5})(?![A-Za-z0-9])", text, re.I):
        token = match.group(0).upper()
        if token in {"AI", "PE", "PB", "ROE", "EPS", "PEG", "VIX"} or token in seen:
            continue
        label = name_of_code(token) if token.isdigit() else ""
        value = f"{label}（{token}）" if label else token
        found.append((match.start(), value))
        seen.add(token)

    found.sort(key=lambda item: item[0])
    return [label for _, label in found[:4]]


def _comparison_side_lines(
    items: list[dict[str, Any]],
    modules: dict[str, Any],
    *,
    strict: bool,
) -> list[str]:
    """比较题兜底的事实行：逐边合并主表与“工具:代码”独立模块里的已取数据。

    主表 compare_stocks 失败/被截断时，资金流、三表、公告等带 symbol 后缀的
    模块往往仍然取到了；只读主表会把它们全部丢掉，兜底就退化成流程话术。
    """
    lines: list[str] = []
    for item in items[:4]:
        name = str(item.get("name") or item.get("symbol") or "该标的")
        symbol = str(item.get("symbol") or "").strip()
        val = item.get("valuation") if isinstance(item.get("valuation"), dict) else {}
        fin = item.get("financials") if isinstance(item.get("financials"), dict) else {}
        quote = item.get("quote") if isinstance(item.get("quote"), dict) else {}

        side_facts: list[str] = []
        pe = _first_number(val, "pe_ratio")
        pb = _first_number(val, "pb_ratio")
        price = _first_number(quote, "price", "current_price")
        if pe is not None or pb is not None:
            side_facts.append(
                "PE " + (f"{pe:.1f}" if pe is not None else "—") +
                ("、PB " + f"{pb:.1f}" if pb is not None else "")
            )
        if price is not None:
            side_facts.append(f"现价 {price:.2f}")
        period = str(fin.get("period_label") or fin.get("report_date") or "").strip()
        for label, key in (
            ("营收同比", "revenue_yoy"), ("净利同比", "profit_yoy"),
            ("ROE", "roe"), ("毛利率", "gross_margin"),
        ):
            value = _first_number(fin, key)
            if value is not None:
                side_facts.append(f"{label} {value:+.1f}%" if key.endswith("_yoy") else f"{label} {value:.1f}%")

        if symbol:
            flow = modules.get(f"get_fund_flow:{symbol}")
            flow_yi = _first_number(flow if isinstance(flow, dict) else {}, "main_flow_5d_yi")
            if flow_yi is not None:
                side_facts.append(
                    f"近5日主力净流入 {flow_yi:.1f} 亿" if flow_yi >= 0 else f"近5日主力净流出 {abs(flow_yi):.1f} 亿"
                )
            stmt = modules.get(f"get_financial_statements:{symbol}")
            if isinstance(stmt, dict):
                stmt_date = str(stmt.get("report_date") or "").strip()[:10]
                if stmt_date and not period:
                    period = stmt_date
                stmt_roe = _first_number(stmt, "roe")
                if stmt_roe is not None and "ROE" not in "、".join(side_facts):
                    side_facts.append(f"ROE {stmt_roe:.1f}%")
                fcf = _first_number(stmt, "free_cash_flow")
                if fcf is not None:
                    side_facts.append(f"自由现金流 {fcf / 1e8:.1f} 亿")
            announcements = modules.get(f"get_stock_announcements:{symbol}")
            ann_items = announcements.get("items") if isinstance(announcements, dict) else None
            titles = [
                str(entry.get("title") or "").strip()[:40]
                for entry in (ann_items or [])[:2]
                if isinstance(entry, dict) and str(entry.get("title") or "").strip()
            ]
            if titles:
                side_facts.append("公告：" + "；".join(titles))

        if period:
            side_facts.insert(0, f"报告期 {period[:10]}")
        line = f"{name}（{symbol or '—'}）" + ("：" + "；".join(side_facts) if side_facts else "：本轮未取到可用字段")
        if not strict:
            line += "（财务指标仅作单边记录，不作横向排名）"
        lines.append(line)
    return lines


def _comparison_fallback_content(request: DulusRoundtableRequest, turns: list[DulusAgentTurn]) -> str:
    """比较题的保守兜底：逐边展示允许比较的字段，绝不把数据覆盖差异当成偏好。"""
    items = _comparison_items_from_context(request.context)
    basis = _comparison_basis_from_context(request.context)
    strict = basis.get("is_strictly_comparable") is True
    modules = _research_modules_from_context(request.context)
    if len(items) < 2:
        identified = [str(item.get("name") or item.get("symbol")) for item in items[:4]]
        sides_note = f"本轮仅识别到 {identified[0]}，另一边未取到数据" if identified else "尚未同时拿到两边的可比数据"
        return _normalize_sectioned_content(
            "**结论**：比较题尚未同时拿到两边的可比数据，暂不偏向任何一方。\n\n"
            f"**核心依据**：\n- {sides_note}；需要先补齐双方行情、估值和最新财报。\n\n"
            "**风险与反证**：单边资料更完整不代表投资吸引力更高。\n\n"
            "**下一步核验**：补齐缺失标的后再按统一抓取时点和报告期复核。",
            fallback="比较题尚未同时拿到两边的可比数据，暂不偏向任何一方。",
            limit=1800,
        )

    def _fmt(value: Any, suffix: str = "") -> str:
        try:
            return f"{float(value):.1f}{suffix}"
        except (TypeError, ValueError):
            return "—"

    rows: list[str] = []
    for item in items[:4]:
        name = str(item.get("name") or item.get("symbol") or "该标的")
        val = item.get("valuation") if isinstance(item.get("valuation"), dict) else {}
        fin = item.get("financials") if isinstance(item.get("financials"), dict) else {}
        quote = item.get("quote") if isinstance(item.get("quote"), dict) else {}
        period = str(fin.get("period_label") or fin.get("report_date") or "报告期未返回")
        if strict:
            rows.append(
                f"{name}（{item.get('symbol') or '—'}）：PE {_fmt(val.get('pe_ratio'))}、PB {_fmt(val.get('pb_ratio'))}，"
                f"营收同比 {_fmt(fin.get('revenue_yoy'), '%')}、净利同比 {_fmt(fin.get('profit_yoy'), '%')}、"
                f"ROE {_fmt(fin.get('roe'), '%')}，现价 {_fmt(quote.get('price'))}。"
            )
        else:
            rows.append(
                f"{name}（{item.get('symbol') or '—'}，{period}）：PE {_fmt(val.get('pe_ratio'))}、PB {_fmt(val.get('pb_ratio'))}，"
                f"现价 {_fmt(quote.get('price'))}；财务增速与 ROE 仅作该公司的单边记录。"
            )
    # 主表字段太薄时（常见于 compare_stocks 部分失败），用每边独立模块补齐事实行。
    main_table_thin = all(
        _first_number(item.get("valuation") if isinstance(item.get("valuation"), dict) else {}, "pe_ratio") is None
        for item in items[:4]
    )
    if main_table_thin or len(rows[0]) < 60:
        side_lines = _comparison_side_lines(items, modules, strict=strict)
        if side_lines and any("未取到可用字段" not in line for line in side_lines):
            rows = side_lines
    if strict:
        conclusion = "基于当前统一抓取时点和共同报告期的可比字段，两家公司各有优劣；在未明确更看重估值、成长或盈利质量前，暂不做单边偏向。"
        risk = "双方币种和数据新鲜度仍需核对；缺失字段不参与排序，资料覆盖多少也不代表公司更好。"
        next_step = "按用户目标（估值容忍度、盈利增速或现金流质量）给出条件化选择，并补查双方近期公告与站内内容。"
    else:
        pe_rows = [
            (str(item.get("name") or item.get("symbol") or "该标的"), _first_number(item.get("valuation"), "pe_ratio"))
            for item in items[:4]
        ]
        pe_rows = [(name, value) for name, value in pe_rows if value is not None and value > 0]
        valuation_tilt = ""
        if len(pe_rows) >= 2:
            pe_rows.sort(key=lambda pair: pair[1])
            valuation_tilt = f"按当前 PE 快照，{pe_rows[0][0]} 的倍数更低；若优先盈利倍数，可暂偏向该侧。"
        conclusion = (
            "报告期不同，当前只按同一抓取时点的行情/估值快照做初步倾向；财务指标分别展示，不作横向排名。"
            + (" " + valuation_tilt if valuation_tilt else "")
        )
        risk = "各自最新财报不能直接排出高下；币种、数据新鲜度和资料覆盖都需核对，资料覆盖多少也不代表公司更好。"
        next_step = "统一双方最新报告期后，再复核营收、利润、ROE 与 EPS；同时按用户更看重的估值、成长或现金流给出条件化选择。"
    return _normalize_sectioned_content(
        "**结论**：" + conclusion + "\n\n"
        "**核心依据**：\n- " + "\n- ".join(rows) + "\n\n"
        "**风险与反证**：" + risk + "\n\n"
        "**下一步核验**：" + next_step,
        fallback="比较题已取得双方数据，但暂不形成单边结论。",
        limit=1800,
    )


def _first_number(value: Any, *keys: str) -> float | None:
    if not isinstance(value, dict):
        return None
    for key in keys:
        raw = value.get(key)
        try:
            if raw is not None and raw != "":
                return float(raw)
        except (TypeError, ValueError):
            continue
    return None


def _research_facts(request: DulusRoundtableRequest) -> dict[str, Any]:
    """提炼回答“贵不贵”真正需要的事实，避免兜底只输出流程话术。"""
    modules = _research_modules_from_context(request.context)
    valuation = modules.get("get_valuation") if isinstance(modules.get("get_valuation"), dict) else {}
    financials = modules.get("get_financials") if isinstance(modules.get("get_financials"), dict) else {}
    statements = modules.get("get_financial_statements") if isinstance(modules.get("get_financial_statements"), dict) else {}
    quote = modules.get("get_market_quote") if isinstance(modules.get("get_market_quote"), dict) else {}
    quote_rows = quote.get("quotes") if isinstance(quote, dict) else None
    quote_row = quote_rows[0] if isinstance(quote_rows, list) and quote_rows and isinstance(quote_rows[0], dict) else {}
    flow = modules.get("get_fund_flow") if isinstance(modules.get("get_fund_flow"), dict) else {}
    consensus = modules.get("get_analyst_consensus") if isinstance(modules.get("get_analyst_consensus"), dict) else {}
    announcements = modules.get("get_stock_announcements") if isinstance(modules.get("get_stock_announcements"), dict) else {}
    dividend = modules.get("get_dividend_history")

    pe = _first_number(valuation, "pe_ratio", "pe_ttm", "pe_dynamic")
    forecast_pe = _first_number(consensus, "forecast_pe_this_year")
    pb = _first_number(valuation, "pb_ratio", "pb_mrq", "pb")
    price = _first_number(quote_row, "price", "current_price")
    revenue_yoy = _first_number(financials, "revenue_yoy")
    profit_yoy = _first_number(financials, "profit_yoy")
    roe = _first_number(financials, "roe",) or _first_number(statements, "roe")
    gross_margin = _first_number(financials, "gross_margin") or _first_number(statements, "gross_margin")
    flow_yi = _first_number(flow, "main_flow_5d_yi")
    report_date = financials.get("report_date") or statements.get("report_date")
    earnings_quality = statements.get("earnings_quality") if isinstance(statements, dict) else {}
    quality_detail = earnings_quality.get("detail") if isinstance(earnings_quality, dict) else {}
    cfo_ratio = _first_number(quality_detail, "cfo_ratio")

    avg_target = _first_number(consensus, "avg_target_price")
    target_gap = ((avg_target / price) - 1) * 100 if avg_target is not None and price else None
    rating = str(consensus.get("consensus_rating") or "").strip()
    report_count = consensus.get("report_count")
    consensus_parts = []
    if rating:
        consensus_parts.append(f"近180天机构评级偏{rating}")
    if isinstance(report_count, (int, float)) and report_count:
        consensus_parts.append(f"覆盖约 {int(report_count)} 份报告")
    if avg_target is not None:
        target_text = f"平均目标价约 {avg_target:.2f} 元"
        if target_gap is not None:
            target_text += f"，较现价{target_gap:+.1f}%"
        consensus_parts.append(target_text)
    if forecast_pe is not None:
        consensus_parts.append(f"一致预期今年 PE 约 {forecast_pe:.1f} 倍")
    consensus_line = "，".join(consensus_parts)

    dividend_items = dividend if isinstance(dividend, list) else []
    dividend_parts = []
    for item in dividend_items[:2]:
        if not isinstance(item, dict):
            continue
        plan = str(item.get("plan") or "").strip()
        yield_pct = _first_number(item, "dividend_yield_pct")
        if plan:
            dividend_parts.append(plan + (f"，股息率约 {yield_pct:.2f}%" if yield_pct is not None else ""))
    dividend_line = "；".join(dividend_parts)

    announcement_items = announcements.get("items") if isinstance(announcements, dict) else []
    announcement_titles = [
        str(item.get("title") or "").strip()[:60]
        for item in (announcement_items or [])[:2]
        if isinstance(item, dict) and str(item.get("title") or "").strip()
    ]
    catalyst_line = "近期公告关注：" + "；".join(announcement_titles) if announcement_titles else ""

    index = modules.get("research_index") if isinstance(modules.get("research_index"), dict) else {}
    digest = index.get("digest") if isinstance(index, dict) else []
    digest_titles = []
    for item in (digest or [])[:4]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        category = str(item.get("category") or "").strip()
        if title:
            digest_titles.append(f"{category}:{title[:42]}" if category else title[:50])
    research_line = "站内重点：" + "；".join(digest_titles) if digest_titles else ""

    if pe is None and pb is None and forecast_pe is None and not financials and not statements and price is None:
        return {}

    if pe is None and forecast_pe is not None:
        valuation_assessment = f"按一致预期今年 PE 约 {forecast_pe:.1f} 倍看不算便宜（当前 PE 快照缺失）"
    elif pe is None:
        valuation_assessment = "估值数据不足"
    elif pe < 15:
        valuation_assessment = "偏低"
    elif pe < 20:
        valuation_assessment = "合理但不便宜"
    elif pe < 28:
        valuation_assessment = "合理偏贵"
    else:
        valuation_assessment = "偏贵"
    if pb is not None and pb >= 5 and "不足" not in valuation_assessment:
        valuation_assessment += "，PB 绝对值不低"

    valuation_line = "、".join(item for item in (
        f"PE 约 {pe:.1f} 倍" if pe is not None else None,
        f"PB 约 {pb:.1f} 倍" if pb is not None else None,
        f"一致预期今年 PE 约 {forecast_pe:.1f} 倍（当前 PE 快照缺失）" if pe is None and forecast_pe is not None else None,
        f"股价约 {price:.2f} 元" if price is not None else None,
    ) if item)
    earnings_parts = []
    if report_date:
        earnings_parts.append(f"截至 {str(report_date)[:10]} 财报")
    if revenue_yoy is not None:
        earnings_parts.append(f"营收同比 {revenue_yoy:+.1f}%")
    if profit_yoy is not None:
        earnings_parts.append(f"净利同比 {profit_yoy:+.1f}%")
    if roe is not None:
        earnings_parts.append(f"ROE {roe:.1f}%")
    if gross_margin is not None:
        earnings_parts.append(f"毛利率 {gross_margin:.1f}%")
    if cfo_ratio is not None:
        earnings_parts.append(f"经营现金流/净利约 {cfo_ratio:.2f}")
    earnings_line = "，".join(earnings_parts)

    risk_parts = []
    if profit_yoy is not None and profit_yoy < 0:
        risk_parts.append(f"最新净利同比 {profit_yoy:+.1f}%")
    if flow_yi is not None and flow_yi < 0:
        risk_parts.append(f"近 5 日主力净流出约 {abs(flow_yi):.1f} 亿元")
    if not risk_parts:
        risk_parts.append("盈利增速和估值容错率仍需后续财报验证")
    if flow_yi is not None:
        flow_line = (
            f"近 5 日主力净流出约 {abs(flow_yi):.1f} 亿元"
            if flow_yi < 0 else f"近 5 日主力净流入约 {flow_yi:.1f} 亿元"
        )
    else:
        flow_line = None

    watch_parts = []
    if profit_yoy is not None:
        watch_parts.append("下一期净利增速能否转正")
    watch_parts.append("批价/库存是否稳定")
    if flow_yi is not None:
        watch_parts.append("主力资金是否持续改善")
    watch_line = "、".join(watch_parts)

    raw_gaps = str(request.context or "").split("证据缺口：", 1)[-1] if "证据缺口：" in request.context else ""
    gap_items = [item.strip(" ;；") for item in raw_gaps.split(";") if item.strip(" ;；")]
    gap_labels = []
    for item in gap_items:
        if item.startswith("get_daily_review"):
            continue
        label = _format_research_gap(item)
        if "本次未引用" in label or "不影响估值初筛" in label:
            continue
        gap_labels.append(label)
    gap_line = "、".join(gap_labels[:2]) if gap_labels else ""
    content_counts: list[str] = []
    for label, key, unit in (
        ("站内快讯", "get_site_fast_news", "条"),
        ("站内文章", "get_site_articles", "篇"),
        ("研报", "get_stock_research", "篇"),
        ("投行研报", "get_recent_research", "篇"),
        ("机构纪要", "get_institution_notes", "条"),
        ("名人观点", "get_celebrity_views", "位"),
    ):
        value = modules.get(key)
        if isinstance(value, list):
            count = len(value)
        elif isinstance(value, dict) and isinstance(value.get("items"), list):
            count = len(value["items"])
        elif isinstance(value, dict) and isinstance(value.get("celebrities"), list):
            count = len(value["celebrities"])
        else:
            count = 0
        if count:
            content_counts.append(f"{label}{count}{unit}")
    content_line = "、".join(content_counts) if content_counts else "站内内容暂未命中"
    return {
        "valuation_assessment": valuation_assessment,
        "valuation_line": valuation_line,
        "pe": pe,
        "pb": pb,
        "profit_yoy": profit_yoy,
        "earnings_line": earnings_line,
        "flow_line": flow_line,
        "consensus_line": consensus_line,
        "dividend_line": dividend_line,
        "catalyst_line": catalyst_line,
        "research_line": research_line,
        "watch_line": watch_line,
        "risk_line": "；".join(risk_parts),
        "gap_line": gap_line,
        "content_line": content_line,
    }


def _research_confidence(request: DulusRoundtableRequest) -> float:
    modules = _research_modules_from_context(request.context)
    score = 0.50
    for key, bonus in (
        ("get_market_quote", 0.03),
        ("get_valuation", 0.08),
        ("get_financials", 0.08),
        ("get_financial_statements", 0.06),
        ("get_fund_flow", 0.04),
        ("get_stock_research", 0.03),
        ("get_site_fast_news", 0.02),
        ("get_site_articles", 0.02),
        ("get_recent_research", 0.02),
        ("get_institution_notes", 0.02),
        ("get_celebrity_views", 0.01),
        ("evidence_rag", 0.04),
    ):
        if modules.get(key):
            score += bonus
    return round(min(0.86, score), 2)


def _format_research_gap(gap: str) -> str:
    """把内部工具名转换成用户可理解的、带影响范围的缺口说明。"""
    text = (gap or "").strip()
    if text.startswith("get_site_fast_news") or text.startswith("快讯:"):
        return "稻草财经快讯暂无命中，本次未引用该模块"
    if text.startswith("get_site_articles") or text.startswith("文章:"):
        return "稻草财经文章暂无命中，本次未引用该模块"
    if text.startswith("get_recent_research") or text.startswith("投行研报:"):
        return "稻草财经投行研报暂无命中，本次未引用该模块"
    if text.startswith("get_institution_notes") or text.startswith("机构纪要:"):
        return "稻草财经机构纪要暂无命中，本次未引用该模块"
    if text.startswith("get_celebrity_views") or text.startswith("名人观点:"):
        return "名人观点暂无命中，本次未引用该模块"
    if text.startswith("get_fund_flow"):
        return "资金流暂不可用，不影响估值初筛"
    if text.startswith("get_daily_review"):
        return "今日复盘暂不可用，本次未引用该模块"
    if text.startswith("get_stock_news"):
        return "个股新闻暂不可用，本次未引用该模块"
    if text.startswith("get_analyst_consensus"):
        return "卖方一致预期暂不可用，本次不引用目标价/评级"
    if text.startswith("get_stock_announcements"):
        return "近期公告暂不可用，本次不引用公告催化"
    if text.startswith("get_dividend_history"):
        return "分红历史暂不可用，本次不评价股东回报"
    if text.startswith("get_stock_research"):
        return "个股研报暂不可用，本次未引用该模块"
    if text.startswith("evidence_rag"):
        return "站内证据库未命中，本次未引用站内材料"
    if text.startswith("get_market_quote"):
        return "行情快照暂不可用，价格需要手动复核"
    if text.startswith("get_valuation"):
        return "估值快照暂不可用，已用一致预期 PE 做辅助判断；PE/PB 需下一次行情核验"
    if text.startswith("get_financials") or text.startswith("get_financial_statements"):
        return "财报模块暂不可用，基本面判断需后续复核"
    return text[:180]


def _extract_keywords(text: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_.-]{1,}|[\u4e00-\u9fff]{2,6}", text)
    stop = {"这个", "项目", "实现", "需要", "当前", "分析", "风险", "一个", "如何"}
    unique: list[str] = []
    for token in tokens:
        normalized = token.strip()
        if not normalized or normalized.lower() in stop or normalized in stop:
            continue
        if normalized not in unique:
            unique.append(normalized)
    return unique


def _safe_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip()[:120] for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [line.strip()[:120] for line in re.split(r"[\n；;]", value) if line.strip()]
    return []


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _memory_from_row(row: sqlite3.Row) -> DulusMemoryRecord:
    try:
        tags = json.loads(row["tags_json"])
    except Exception:
        tags = []
    return DulusMemoryRecord(
        id=str(row["id"]),
        scope=str(row["scope"]),
        hall=str(row["hall"]),
        title=str(row["title"]),
        content=str(row["content"]),
        tags=[str(tag) for tag in tags if str(tag).strip()],
        source=str(row["source"]),
        created_at=str(row["created_at"]),
    )


def _save_memory(
    *,
    scope: str,
    hall: str,
    title: str,
    content: str,
    tags: list[str],
    source: str,
) -> DulusMemoryRecord:
    now = datetime.now(timezone.utc).isoformat()
    memory_id = f"mem_{int(datetime.now(timezone.utc).timestamp() * 1000)}_{abs(hash((title, content, now))) % 100000}"
    clean_tags = [tag.strip()[:40] for tag in tags if tag.strip()][:12]
    record = DulusMemoryRecord(
        id=memory_id,
        scope=scope[:24] or "session",
        hall=hall[:40] or "events",
        title=title.strip()[:180] or "Untitled memory",
        content=content.strip()[:4000],
        tags=clean_tags,
        source=source.strip()[:80] or "manual",
        created_at=now,
    )
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        candidates = conn.execute(
            "SELECT * FROM dulus_memory WHERE scope=? AND hall=? AND title=? ORDER BY created_at DESC",
            (record.scope, record.hall, record.title),
        ).fetchall()
        record_owner = next((tag for tag in record.tags if tag.startswith("owner:")), "")
        record_attachment = next((tag for tag in record.tags if tag.startswith("attachment:")), "")
        if record_attachment == "attachment:附件":
            record_attachment = ""
        normalized_content = re.sub(r"\s+", " ", record.content).strip().casefold()
        for existing in candidates:
            try:
                existing_tags = [str(tag) for tag in json.loads(existing["tags_json"] or "[]")]
            except Exception:
                existing_tags = []
            existing_owner = next((tag for tag in existing_tags if tag.startswith("owner:")), "")
            existing_attachment = next((tag for tag in existing_tags if tag.startswith("attachment:")), "")
            if existing_attachment == "attachment:附件":
                existing_attachment = ""
            same_identity = (
                record.scope != "user" or (record_owner and record_owner == existing_owner)
            ) and (record.source != "ai_chat" or record_attachment == existing_attachment)
            same_content = same_identity and re.sub(r"\s+", " ", str(existing["content"])).strip().casefold() == normalized_content
            same_ai_key = (
                same_identity
                and record.source in {"ai_chat", "roundtable"}
                and str(existing["source"]) == record.source
            )
            if not (same_content or same_ai_key):
                continue
            if same_ai_key and (
                str(existing["content"]) != record.content
                or str(existing["tags_json"]) != json.dumps(record.tags, ensure_ascii=False)
            ):
                conn.execute(
                    "UPDATE dulus_memory SET content=?, tags_json=?, created_at=? WHERE id=?",
                    (record.content, json.dumps(record.tags, ensure_ascii=False), record.created_at, str(existing["id"])),
                )
                conn.commit()
                return DulusMemoryRecord(
                    id=str(existing["id"]), scope=str(existing["scope"]), hall=str(existing["hall"]),
                    title=str(existing["title"]), content=record.content, tags=record.tags,
                    source=str(existing["source"]), created_at=record.created_at,
                )
            conn.commit()
            return _memory_from_row(existing)
        conn.execute(
            """
            INSERT INTO dulus_memory (id, scope, hall, title, content, tags_json, source, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.scope,
                record.hall,
                record.title,
                record.content,
                json.dumps(record.tags, ensure_ascii=False),
                record.source,
                record.created_at,
            ),
        )
        conn.commit()
    return record


def _ensure_default_memory_buckets() -> None:
    with _connect() as conn:
        existing = conn.execute("SELECT COUNT(*) AS count FROM dulus_memory WHERE source = 'palace_init'").fetchone()["count"]
    if existing:
        return
    defaults = [
        ("project", "soul", "DeepFocus Dulus Runtime", "合规版 Dulus Runtime：圆桌、多工具、MemPalace、授权 WebBridge。"),
        ("project", "preferences", "Runtime Preferences", "默认中文投研语境；保留风险纪律；不复用第三方网页 AI 会话。"),
        ("project", "rules", "Hardened Rules", "高风险工具需要确认；WebBridge 只允许本机/白名单；结论需带证据和反证。"),
    ]
    for scope, hall, title, content in defaults:
        _save_memory(scope=scope, hall=hall, title=title, content=content, tags=["dulus", hall], source="palace_init")


def _save_roundtable_memory(
    request: DulusRoundtableRequest,
    synthesis: str,
    memory_notes: list[str],
    decision: str,
    confidence: float,
) -> None:
    try:
        title = f"圆桌：{request.objective[:56]}"
        content = "\n".join([
            f"目标：{request.objective}",
            f"结论：{synthesis}",
            f"决策：{decision} / confidence={confidence:.2f}",
            *memory_notes,
        ])
        tags = ["roundtable", request.mode]
        if request.stock:
            tags.append(request.stock.symbol)
        _save_memory(scope="session", hall="events", title=title, content=content, tags=tags, source="roundtable")
    except Exception:
        pass


def _allowed_webbridge_hosts() -> set[str]:
    env_hosts = {
        host.strip().lower()
        for host in os.getenv("DULUS_WEBBRIDGE_ALLOWED_HOSTS", "").split(",")
        if host.strip()
    }
    return WEBBRIDGE_DEFAULT_ALLOWED_HOSTS | env_hosts


def _is_authorized_url(url: str) -> tuple[bool, str]:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        return False, "只允许 http/https URL"
    if parsed.username or parsed.password:
        return False, "URL 中不能携带用户名或密码"
    host = (parsed.hostname or "").lower()
    if not host:
        return False, "URL 缺少 host"
    allowed_hosts = _allowed_webbridge_hosts()
    if host in allowed_hosts:
        return True, "allowed"
    return False, f"host {host} 不在白名单；设置 DULUS_WEBBRIDGE_ALLOWED_HOSTS 可放行自有域名"


def _charset_from_content_type(content_type: str) -> str | None:
    match = re.search(r"charset=([\w.-]+)", content_type, re.I)
    return match.group(1) if match else None


def _extract_title(raw_html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", raw_html, re.I | re.S)
    if not match:
        return ""
    return html.unescape(re.sub(r"\s+", " ", match.group(1))).strip()


def _html_to_text(raw_html: str) -> str:
    text = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\1>", " ", raw_html)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h[1-6]>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _extract_links(raw_html: str, base: str) -> list[str]:
    links: list[str] = []
    for match in re.finditer(r"<a[^>]+href=[\"']([^\"']+)[\"']", raw_html, re.I):
        href = html.unescape(match.group(1)).strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:")):
            continue
        if href.startswith("/"):
            href = f"{base}{href}"
        if href not in links:
            links.append(href[:500])
    return links


def _safe_confidence(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))


def _average(values: list[float]) -> float:
    clean = [max(0.0, min(1.0, float(value))) for value in values if value is not None]
    if not clean:
        return 0.5
    return round(sum(clean) / len(clean), 2)


def _clean_text(value: Any, fallback: str, limit: int = 600) -> str:
    text = str(value or "").strip()
    return text[:max(80, int(limit))] if text else fallback


def _fmt_number(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "--"
    return f"{number:.2f}"


def _fmt_percent(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "--"
    return f"{number:+.2f}%"


def _stock_label(stock: StockSnapshot | None) -> str:
    if not stock:
        return "当前目标"
    return f"{stock.symbol} {stock.name}"


def _provider_name(llm: Any) -> str:
    return str(getattr(llm, "provider_name", getattr(llm, "provider", "mock")))


def _model_name(llm: Any) -> str:
    return str(getattr(llm, "model", "deepfocus-mock"))
