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

DB_PATH = Path(os.getenv("DULUS_MEMORY_DB_PATH") or Path(__file__).resolve().parents[1] / ".dulus_memory.sqlite3")
WEBBRIDGE_DEFAULT_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "::1"}
WEBBRIDGE_POLICY = (
    "Authorized WebBridge 只允许本机或 DULUS_WEBBRIDGE_ALLOWED_HOSTS 白名单域名；"
    "不发送 Cookie，不读取浏览器 profile，不复用第三方 AI 会话。"
)


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
    if scope:
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


async def run_dulus_roundtable(llm: Any, request: DulusRoundtableRequest) -> DulusRoundtableResponse:
    objective = request.objective.strip()
    if not objective:
        raise ValueError("objective is required")

    # 主动取数（agentic）：议题相关证据稀薄时，先爬一轮补进证据库（供参与者 + 工具轨迹 + 可引用来源）。
    crawl_symbol = request.stock.symbol if request.stock else None
    crawl_keyword = (
        (request.stock.name if request.stock and (request.stock.name or "").strip() else crawl_symbol)
        or (" ".join(_extract_keywords(objective)[:2]) or None)
    )
    await crawl_evidence_if_thin(crawl_symbol, crawl_keyword)

    # 编号可引用来源前置：参与者 + 主席都拿到，正文才能用 [n] 内联引用（与分析师/深研对称）。
    citable_sources = _citable_sources_for_request(request)

    participant_ids = _select_participants(request.participants)
    tool_records = {tool.id: tool for tool in list_dulus_tools()}
    selected_tool_ids = _select_tools(request.enabled_tools, tool_records)
    tool_traces = _run_safe_tools(request, selected_tool_ids, tool_records)

    turns = await asyncio.gather(*[
        _run_participant(llm, request, participant_id, tool_traces, citable_sources)
        for participant_id in participant_ids
    ])
    synthesis = await _build_synthesis(llm, request, turns, citable_sources)
    confidence = _average([turn.confidence for turn in turns] + [synthesis.confidence])
    decision = _derive_decision(turns, synthesis, tool_traces)
    warnings = _warnings_for_request(request, selected_tool_ids, tool_records)
    if any(turn.provider == "local-fallback" for turn in [*turns, synthesis]):
        warnings.append("云模型圆桌调用不可用，已使用本地模板兜底。")
    sources = _sources_for_request(request, tool_traces)
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

    payload = _roundtable_payload(request, tool_traces)
    prompt = (
        f"你是 Dulus Runtime 圆桌里的 {definition['name']}。"
        f"你的立场：{definition['brief']}\n"
        "请基于输入生成严格 JSON：content, key_points, risks, actions, confidence。"
        "content 不超过 180 个中文字符；数组最多 4 项，每项不超过 28 个中文字符；"
        "源纪律：对输入里的证据先判断与议题的相关性和可信度——优先用高可信、直接相关的；"
        "论坛传闻/营销号/单一来源需谨慎，不当作确定结论；证据不足就明说缺口、不要编造实时数据。"
        "不要给确定性交易指令。\n"
        f"{_sources_prompt_block(citable_sources)}"
        f"输入：{json.dumps(payload, ensure_ascii=False)}"
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

    prompt = (
        "你是 Dulus Runtime 的圆桌主席。请综合多个 Agent 的观点，输出严格 JSON："
        "content, key_points, risks, actions, confidence。"
        "content 不超过 220 个中文字符；重点写共识、分歧、下一步核验动作。"
        "源纪律：综合时优先采纳有高可信、直接相关证据支撑的观点；对仅凭传闻/单一来源的判断要点明「待核验」；"
        "Agent 之间结论冲突时点明分歧、不要简单取一。不要编造实时数据，不要输出确定性买卖建议。\n"
        f"{_sources_prompt_block(citable_sources)}"
        f"任务：{request.objective[:800]}\n"
        f"上下文：{request.context[:2000]}\n"
        f"圆桌发言：{json.dumps([turn.model_dump() for turn in turns], ensure_ascii=False)}"
    )
    try:
        data = await llm.complete_json(
            prompt,
            max_tokens=1000,
            timeout_seconds=28,
            force_json_first=True,
            retry_schema_hint="必须返回圆桌 synthesis JSON。",
        )
        return DulusAgentTurn(
            participant_id="synthesis",
            participant_name="Mesa Redonda",
            provider=_provider_name(llm),
            model=_model_name(llm),
            stance="synthesis",
            content=_clean_text(data.get("content"), fallback=_fallback_synthesis_content(request, turns)),
            key_points=_safe_list(data.get("key_points"))[:5],
            risks=_safe_list(data.get("risks"))[:5],
            actions=_safe_list(data.get("actions"))[:5],
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
    content_map = {
        "evidence": f"{stock_label} 的任务需要先核验行情、公告、研报和社区材料；当前上下文可形成初筛，但证据链还不能当作结论。",
        "research": f"核心假设应拆成催化、基本面验证和时间窗口。先把可证伪指标列清楚，再决定是否进入长任务。",
        "risk": f"最重要的是防止把单一叙事当成确定性机会；需要设置反证、仓位边界和失效触发器。",
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
        confidence=0.58 if participant_id == "evidence" else 0.62,
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
        confidence=_average([turn.confidence for turn in turns]),
    )


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
        "context": request.context[:3000],
        "mode": request.mode,
        "stock": stock_payload,
        "authorized_webbridge_url": request.authorized_webbridge_url,
        "tool_traces": [trace.model_dump() for trace in traces],
    }


def _traces_for_participant(participant_id: str, traces: list[DulusToolTrace]) -> list[DulusToolTrace]:
    if participant_id == "evidence":
        keys = {"evidence_lookup", "market_snapshot", "repo_search"}
    elif participant_id == "risk":
        keys = {"risk_review", "webbridge_browser_capture", "shell_executor", "mcp_gateway", "authorized_webbridge_inspect"}
    elif participant_id == "operator":
        keys = {trace.tool for trace in traces}
    else:
        keys = {"report_outline", "market_snapshot", "authorized_webbridge_inspect"}
    return [trace for trace in traces if trace.tool in keys]


def _fallback_content(request: DulusRoundtableRequest, participant_id: str) -> str:
    stock = _stock_label(request.stock)
    if participant_id == "risk":
        return f"{stock} 需要先检查反证、仓位纪律和资料缺口，避免直接进入执行。"
    if participant_id == "evidence":
        return f"{stock} 需要补齐可追溯来源后再提升结论置信度。"
    return f"{stock} 可以先形成假设，再用工具和证据逐项验证。"


def _fallback_synthesis_content(request: DulusRoundtableRequest, turns: list[DulusAgentTurn]) -> str:
    stock = _stock_label(request.stock)
    if not turns:
        return f"{stock} 的任务已进入圆桌，但还没有有效发言；建议补充上下文后重试。"
    return (
        f"圆桌共识：{stock} 先走证据补全和风险复核，再转长任务。"
        "当前适合形成研究计划，不适合直接给确定性交易结论。"
    )


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


def _clean_text(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    return text[:600] if text else fallback


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
