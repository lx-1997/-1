from __future__ import annotations

import asyncio
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from .agent_runtime import (
    cancel_investment_task,
    create_investment_task,
    get_investment_task,
    init_task_db,
    is_worker_running,
    list_investment_tasks,
    retry_investment_task,
    start_agent_worker,
    stop_agent_worker,
    task_counts,
)
from .data_sources import (
    capture_agent_web_pages,
    create_data_source,
    delete_data_source,
    delete_data_item,
    get_data_item,
    get_data_source,
    init_data_source_db,
    keyword_crawl_data_source,
    list_data_items,
    list_data_sources,
    list_data_tags,
    store_upload_item,
    sync_data_source,
    update_data_item,
)
from .file_tools import extract_upload_file
from .earnings_calendar import fetch_earnings_calendar
from .llm import CloudResearchLLM
from .market_data import fetch_market_quotes
from .model_config import public_model_config, save_model_config
from .schemas import (
    AgentBriefRequest,
    AgentRuntimeHealthResponse,
    CapabilityListResponse,
    CorridorRiskRequest,
    DataSourceCreateRequest,
    DataSourceItemInterpretRequest,
    DataSourceItemInterpretResponse,
    DataSourceItemListResponse,
    DataSourceItemRecord,
    DataSourceItemUpdateRequest,
    DataSourceKeywordCrawlRequest,
    DataSourceKeywordCrawlResponse,
    DataSourceListResponse,
    DataSourceRecord,
    DataSourceSyncRequest,
    DataSourceSyncResponse,
    DataSourceTagListResponse,
    EarningsCalendarResponse,
    FinGptTaskResponse,
    ForecastRequest,
    FileExtractionResponse,
    InvestmentTaskCreateRequest,
    InvestmentTaskListResponse,
    InvestmentTaskRecord,
    MarketQuoteListResponse,
    NewsSummaryRequest,
    ModelConfigRequest,
    ModelConfigResponse,
    RagQueryRequest,
    ReportAnalysisRequest,
    SentimentRequest,
    SentimentResponse,
    StockAnalysisRequest,
    StockAnalysisResponse,
    StockCheckRequest,
    StockCheckResponse,
    StockCheckStep,
)

load_dotenv()


def _allowed_origins() -> list[str]:
    raw = os.getenv("CORS_ORIGINS", "*")
    return [origin.strip() for origin in raw.split(",") if origin.strip()] or ["*"]

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_task_db()
    init_data_source_db()
    await start_agent_worker()
    yield
    await stop_agent_worker()


app = FastAPI(
    title="DeepFocus AI API",
    description="Cloud-model research API for the DeepFocus frontend.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)

llm = CloudResearchLLM()


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "service": "deepfocus-ai-api",
        "provider": llm.provider_name,
        "model": llm.model,
    }


@app.get("/api/fingpt/capabilities", response_model=CapabilityListResponse)
async def capabilities() -> CapabilityListResponse:
    return llm.capabilities()


@app.get("/api/fingpt/model-config", response_model=ModelConfigResponse)
async def get_model_config() -> ModelConfigResponse:
    return public_model_config()


@app.post("/api/fingpt/model-config", response_model=ModelConfigResponse)
async def update_model_config(request: ModelConfigRequest) -> ModelConfigResponse:
    return save_model_config(request)


@app.get("/api/market/quotes", response_model=MarketQuoteListResponse)
async def market_quotes(symbols: str = "") -> MarketQuoteListResponse:
    requested_symbols = [symbol.strip() for symbol in symbols.split(",") if symbol.strip()]
    return await fetch_market_quotes(requested_symbols)


@app.get("/api/earnings/calendar", response_model=EarningsCalendarResponse)
async def earnings_calendar(symbols: str = "", horizon: str = "3month") -> EarningsCalendarResponse:
    requested_symbols = [symbol.strip() for symbol in symbols.split(",") if symbol.strip()]
    return await fetch_earnings_calendar(requested_symbols, horizon=horizon)


@app.post("/api/fingpt/files/extract", response_model=FileExtractionResponse)
async def extract_file(file: UploadFile = File(...)) -> FileExtractionResponse:
    return await extract_upload_file(file)


@app.get("/api/data-sources", response_model=DataSourceListResponse)
async def api_list_data_sources() -> DataSourceListResponse:
    return DataSourceListResponse(sources=list_data_sources())


@app.post("/api/data-sources", response_model=DataSourceRecord)
async def api_create_data_source(request: DataSourceCreateRequest) -> DataSourceRecord:
    return create_data_source(request)


@app.delete("/api/data-sources/{source_id}")
async def api_delete_data_source(source_id: str) -> dict[str, bool]:
    if not delete_data_source(source_id):
        raise HTTPException(status_code=404, detail="Data source not found")
    return {"ok": True}


@app.post("/api/data-sources/{source_id}/sync", response_model=DataSourceSyncResponse)
async def api_sync_data_source(source_id: str, request: DataSourceSyncRequest) -> DataSourceSyncResponse:
    source, items = await sync_data_source(source_id, request)
    return DataSourceSyncResponse(source=source, imported_count=len(items), items=items)


@app.get("/api/data-sources/items", response_model=DataSourceItemListResponse)
async def api_list_data_items(
    symbol: Optional[str] = None,
    query: Optional[str] = None,
    source_type: Optional[str] = None,
    source_id: Optional[str] = None,
    tag: Optional[str] = None,
    limit: int = 50,
    sort: str = "time_desc",
) -> DataSourceItemListResponse:
    return DataSourceItemListResponse(
        items=list_data_items(
            symbol=symbol,
            query=query,
            source_type=source_type,
            source_id=source_id,
            tag=tag,
            limit=max(1, min(limit, 100)),
            sort=sort,
        )
    )


@app.get("/api/data-sources/items/tags", response_model=DataSourceTagListResponse)
async def api_list_data_tags() -> DataSourceTagListResponse:
    return DataSourceTagListResponse(tags=list_data_tags())


@app.get("/api/data-sources/items/{item_id}", response_model=DataSourceItemRecord)
async def api_get_data_item(item_id: str) -> DataSourceItemRecord:
    item = get_data_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Data item not found")
    return item


@app.patch("/api/data-sources/items/{item_id}", response_model=DataSourceItemRecord)
async def api_update_data_item(item_id: str, request: DataSourceItemUpdateRequest) -> DataSourceItemRecord:
    item = update_data_item(item_id, request)
    if not item:
        raise HTTPException(status_code=404, detail="Data item not found")
    return item


@app.post("/api/data-sources/items/{item_id}/interpret", response_model=DataSourceItemInterpretResponse)
async def api_interpret_data_item(
    item_id: str,
    request: DataSourceItemInterpretRequest = DataSourceItemInterpretRequest(),
) -> DataSourceItemInterpretResponse:
    item = get_data_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Data item not found")
    if llm.provider == "mock":
        raise HTTPException(
            status_code=409,
            detail="当前模型仍是 mock 模式，不能执行真实 AI 解读。请先在 FinGPT → 模型配置 中配置 OpenAI、Minimax 或 OpenAI-compatible 的 API Key。",
        )

    try:
        if _is_wechat_public_item(item):
            result = await llm.analyze_wechat_article(_wechat_article_payload(item))
        else:
            result = await llm.analyze_report(
                ReportAnalysisRequest(
                    title=item.title,
                    report_text=item.text,
                    locale="zh-CN",
                )
            )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI 解读失败：{exc}") from exc
    interpretation = _format_interpretation(result)
    updated = item
    if request.persist:
        updated = update_data_item(item.id, DataSourceItemUpdateRequest(ai_interpretation=interpretation)) or item
    return DataSourceItemInterpretResponse(item=updated, interpretation=interpretation, result=result)


@app.delete("/api/data-sources/items/{item_id}")
async def api_delete_data_item(item_id: str) -> dict[str, bool]:
    if not delete_data_item(item_id):
        raise HTTPException(status_code=404, detail="Data item not found")
    return {"ok": True}


@app.post("/api/data-sources/upload", response_model=DataSourceItemRecord)
async def api_upload_data_file(
    file: UploadFile = File(...),
    symbol: Optional[str] = Form(None),
    title: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
) -> DataSourceItemRecord:
    extracted = await extract_upload_file(file)
    parsed_tags = [tag.strip() for tag in (tags or "").split(",") if tag.strip()]
    return store_upload_item(
        filename=extracted.filename,
        text=extracted.text,
        parser=extracted.parser,
        content_type=extracted.content_type,
        symbol=symbol,
        title=title,
        tags=parsed_tags,
    )


@app.post("/api/data-sources/agent-crawl", response_model=DataSourceSyncResponse)
async def api_agent_crawl(request: DataSourceSyncRequest) -> DataSourceSyncResponse:
    source, items = await capture_agent_web_pages(request)
    return DataSourceSyncResponse(source=source, imported_count=len(items), items=items)


@app.post("/api/data-sources/keyword-crawl", response_model=DataSourceKeywordCrawlResponse)
async def api_keyword_crawl(request: DataSourceKeywordCrawlRequest) -> DataSourceKeywordCrawlResponse:
    source, items, warnings, meta = await keyword_crawl_data_source(request)
    return DataSourceKeywordCrawlResponse(
        provider=request.provider,
        effective_provider=meta["effective_provider"],
        attempted_providers=meta["attempted_providers"],
        fallback_used=meta["fallback_used"],
        provider_policy=meta["provider_policy"],
        keyword=request.keyword,
        sort=request.sort,
        freshness=request.freshness,
        source=source,
        imported_count=len(items),
        items=items,
        warnings=warnings,
    )


def _format_interpretation(result: FinGptTaskResponse) -> str:
    def block(title: str, lines: list[str]) -> str:
        clean_lines = [line.strip() for line in lines if line.strip()]
        if not clean_lines:
            return ""
        return f"{title}\n" + "\n".join(f"- {line}" for line in clean_lines)

    capability_label = {
        "wechat_article": "公众号事件快读",
        "report_analysis": "财报/研报解读",
        "news_summary": "新闻蒸馏",
    }.get(result.capability, result.capability)
    parts = [
        f"标题：{result.title}",
        f"能力：{capability_label}",
        f"生成时间：{result.generated_at.isoformat()}",
        f"模型：{result.provider} / {result.model}",
        "",
        f"摘要：{result.summary}",
        "",
        block("核心要点", result.key_points),
        "",
        block("信号", result.signals),
        "",
        block("风险", result.risks),
        "",
        block("后续动作", result.actions),
        "",
        block("证据来源", result.sources),
        "",
        result.disclaimer,
    ]
    return "\n".join(part for part in parts if part != "")


def _is_wechat_public_item(item: DataSourceItemRecord) -> bool:
    tags = {tag.lower() for tag in item.tags}
    provider = str(item.metadata.get("provider") or "").lower()
    return provider == "wechat_public" or "公众号" in item.tags or "搜狗微信" in item.tags or "wechat_public" in tags


def _wechat_article_payload(item: DataSourceItemRecord) -> dict[str, object]:
    return {
        "title": item.title,
        "summary": _extract_labeled_text(item.text, "摘要") or item.text_preview,
        "account": item.metadata.get("account") or _extract_labeled_text(item.text, "公众号") or item.source_name,
        "published": item.metadata.get("published") or _extract_labeled_text(item.text, "时间"),
        "published_at": item.metadata.get("published_at") or item.collected_at,
        "symbol": item.symbol,
        "keyword": _extract_labeled_text(item.text, "搜索关键词"),
        "tags": item.tags,
        "url": item.url,
    }


def _extract_labeled_text(text: str, label: str) -> str:
    match = re.search(rf"{re.escape(label)}[：:]\s*(.*?)(?=\n\S{{1,16}}[：:]|\Z)", text or "", flags=re.S)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()[:600]


async def _run_stock_check_job(key: str, name: str, coro: Any) -> tuple[str, StockCheckStep, Any]:
    try:
        value = await asyncio.wait_for(coro, timeout=18)
        return key, StockCheckStep(key=key, name=name, status="completed", detail="已完成"), value
    except Exception as exc:
        return key, StockCheckStep(key=key, name=name, status="failed", detail=_clean_step_error(exc)), None


def _mark_stock_check_fallback(checks: list[StockCheckStep], key: str) -> None:
    for step in checks:
        if step.key == key:
            step.status = "completed"
            step.detail = "云模型未及时返回，已使用本地规则兜底。"
            return


def _stock_check_context(
    request: StockCheckRequest,
    evidence_items: Optional[list[DataSourceItemRecord]] = None,
) -> str:
    stock = request.stock
    lines = [
        f"标的：{stock.name}（{stock.symbol}）",
        f"行业：{stock.sector or '未知'}",
        f"价格：{stock.current_price if stock.current_price is not None else '未知'}",
        f"涨跌幅：{stock.change_percent if stock.change_percent is not None else '未知'}%",
        f"市值：{stock.market_cap if stock.market_cap is not None else '未知'}",
        f"关注度：{stock.focus_level or '未知'}",
        f"社区热度：{stock.community_score if stock.community_score is not None else '未知'}",
        f"描述：{stock.description or ''}",
    ]
    if request.question:
        lines.append(f"用户关注：{request.question}")
    for index, post in enumerate(request.posts[:10], start=1):
        lines.extend(
            [
                "",
                f"资料 {index}：{post.title}",
                f"摘要：{post.summary or ''}",
                f"内容：{(post.content or '')[:900]}",
                f"标签：{', '.join(post.tags)}",
                f"时间：{post.publish_time or ''}",
            ]
        )
    for index, item in enumerate((evidence_items or [])[:8], start=1):
        lines.extend(
            [
                "",
                f"数据源 {index}：{item.title}",
                f"来源：{item.source_name}",
                f"可信度：{round(item.credibility_score * 100)}%",
                f"摘要：{item.text_preview[:700]}",
                f"标签：{', '.join(item.tags)}",
                f"时间：{item.collected_at}",
            ]
        )
    return "\n".join(lines)[:9000]


def _stock_check_documents(
    request: StockCheckRequest,
    evidence_items: Optional[list[DataSourceItemRecord]] = None,
) -> list[dict[str, str]]:
    documents = [
        {
            "source": "stock_snapshot",
            "text": _stock_check_context(
                StockCheckRequest(
                    stock=request.stock,
                    posts=[],
                    question=request.question,
                    horizon=request.horizon,
                    locale=request.locale,
                )
            ),
        }
    ]
    for post in request.posts[:8]:
        documents.append(
            {
                "source": post.title,
                "text": "\n".join([post.summary or "", (post.content or "")[:1200], ", ".join(post.tags)]).strip(),
            }
        )
    for item in (evidence_items or [])[:8]:
        documents.append(
            {
                "source": item.title,
                "text": "\n".join([item.text_preview, item.text[:1200], ", ".join(item.tags)]).strip(),
            }
        )
    return documents


async def _stock_check_single_pass(
    request: StockCheckRequest,
    context: str,
) -> Optional[StockCheckResponse]:
    if llm.provider == "mock":
        return None
    prompt = (
        "你是专业股票投研系统的 One-Click Stock Check 编排器。"
        "请在一次输出中同时完成这些 FinGPT 镜头：个股投研、金融情绪、新闻蒸馏、资料解读、RAG验证、预测推演、Agent复核。"
        "不要给确定性交易建议；要像投资者体检报告，指出值得跟踪的理由、风险、证据缺口和下一步动作。\n"
        "返回严格 JSON object，字段：verdict, score, confidence, summary, action_items, risk_flags, "
        "sentiment_label, sentiment_score, sentiment_rationale, stock_summary, catalysts, stock_risks, watch_items, "
        "sections。verdict 只能是 重点跟踪/谨慎观察/暂不行动；score 0-100；confidence 0-1。"
        "sections 是对象，必须包含 news_summary, report_analysis, rag_query, forecast, agent_brief；"
        "每个 section 字段为 summary, key_points, signals, risks, actions, sources, confidence。"
        "数组每项不超过 24 个中文字符，每个数组最多 5 项。\n"
        f"输入：{context}"
    )
    try:
        data = await asyncio.wait_for(
            llm.complete_json(prompt, max_tokens=2200, timeout_seconds=14, force_json_first=False),
            timeout=16,
        )
    except Exception as exc:
        return _stock_check_local_response(request, context, _clean_step_error(exc))

    verdict = str(data.get("verdict") or "谨慎观察")
    if verdict not in {"重点跟踪", "谨慎观察", "暂不行动"}:
        verdict = "谨慎观察"
    score = int(round(_clamp(_number_value(data.get("score"), 50), 0, 100)))
    confidence = _clamp(_number_value(data.get("confidence"), 0.6), 0, 1)
    label = str(data.get("sentiment_label") or "neutral").lower()
    if label not in {"positive", "neutral", "negative"}:
        label = "neutral"
    sentiment = SentimentResponse(
        provider=llm.provider_name,
        model=llm.model,
        label=label,
        score=_clamp(_number_value(data.get("sentiment_score"), 0), -1, 1),
        rationale=str(data.get("sentiment_rationale") or "一键检测综合判断。"),
    )
    stock_analysis = StockAnalysisResponse(
        provider=llm.provider_name,
        model=llm.model,
        generated_at=datetime.now(timezone.utc),
        executive_summary=str(data.get("stock_summary") or data.get("summary") or "已完成一键检测。"),
        sentiment_label=label,
        sentiment_score=sentiment.score,
        risk_level="high" if score < 45 else "medium" if score < 68 else "low",
        catalysts=_json_list(data.get("catalysts")),
        risks=_json_list(data.get("stock_risks") or data.get("risk_flags")),
        watch_items=_json_list(data.get("watch_items") or data.get("action_items")),
        suggested_questions=_json_list(data.get("suggested_questions")) or ["关键催化是否可验证？", "风险是否已反映在价格中？"],
    )
    sections = data.get("sections") if isinstance(data.get("sections"), dict) else {}

    def section_task(key: str, title: str) -> FinGptTaskResponse:
        section = sections.get(key) if isinstance(sections.get(key), dict) else {}
        return FinGptTaskResponse(
            provider=llm.provider_name,
            model=llm.model,
            generated_at=datetime.now(timezone.utc),
            capability=key,
            title=f"{request.stock.name} {title}",
            summary=str(section.get("summary") or data.get("summary") or "已完成检测。"),
            key_points=_json_list(section.get("key_points")),
            signals=_json_list(section.get("signals")),
            risks=_json_list(section.get("risks")),
            actions=_json_list(section.get("actions")),
            sources=_json_list(section.get("sources")) or ["一键检测输入"],
            confidence=_clamp(_number_value(section.get("confidence"), confidence), 0, 1),
        )

    checks = [
        StockCheckStep(key="stock_analysis", name="个股投研", status="completed", detail="一键编排完成"),
        StockCheckStep(key="sentiment", name="金融情绪", status="completed", detail="一键编排完成"),
        StockCheckStep(key="news_summary", name="新闻蒸馏", status="completed", detail="一键编排完成"),
        StockCheckStep(key="report_analysis", name="资料解读", status="completed", detail="一键编排完成"),
        StockCheckStep(key="rag_answer", name="RAG问答", status="completed", detail="一键编排完成"),
        StockCheckStep(key="forecast", name="预测推演", status="completed", detail="一键编排完成"),
        StockCheckStep(key="agent_brief", name="Agent复核", status="completed", detail="一键编排完成"),
        StockCheckStep(key="corridor_risk", name="通道风险", status="skipped", detail="稳定币/支付通道风险不适用于普通个股一键检测。"),
    ]
    return StockCheckResponse(
        provider=llm.provider_name,
        model=llm.model,
        generated_at=datetime.now(timezone.utc),
        stock=request.stock,
        verdict=verdict,
        score=score,
        confidence=confidence,
        summary=str(data.get("summary") or stock_analysis.executive_summary),
        action_items=_json_list(data.get("action_items")) or stock_analysis.watch_items,
        risk_flags=_json_list(data.get("risk_flags")) or stock_analysis.risks,
        checks=checks,
        stock_analysis=stock_analysis,
        sentiment=sentiment,
        news_summary=section_task("news_summary", "新闻蒸馏"),
        report_analysis=section_task("report_analysis", "资料解读"),
        rag_answer=section_task("rag_query", "RAG问答"),
        forecast=section_task("forecast", "预测推演"),
        agent_brief=section_task("agent_brief", "Agent复核"),
        warnings=[],
    )


def _stock_check_local_response(
    request: StockCheckRequest,
    context: str,
    reason: str,
) -> StockCheckResponse:
    stock_result = _fallback_stock_analysis(request)
    sentiment_result = _fallback_sentiment(context)
    news_result = _fallback_task("news_summary", "新闻蒸馏", request, context)
    report_result = _fallback_task("report_analysis", "资料解读", request, context)
    rag_result = _fallback_task("rag_query", "RAG问答", request, context)
    forecast_result = _fallback_task("forecast", "预测推演", request, context)
    agent_result = _fallback_task("agent_brief", "Agent复核", request, context)
    score = _stock_check_score(request, stock_result, sentiment_result, forecast_result)
    verdict = "重点跟踪" if score >= 68 else "谨慎观察" if score >= 45 else "暂不行动"
    checks = [
        StockCheckStep(key="stock_analysis", name="个股投研", status="completed", detail=f"本地规则兜底：{reason}"),
        StockCheckStep(key="sentiment", name="金融情绪", status="completed", detail=f"本地规则兜底：{reason}"),
        StockCheckStep(key="news_summary", name="新闻蒸馏", status="completed", detail=f"本地规则兜底：{reason}"),
        StockCheckStep(key="report_analysis", name="资料解读", status="completed", detail=f"本地规则兜底：{reason}"),
        StockCheckStep(key="rag_answer", name="RAG问答", status="completed", detail=f"本地规则兜底：{reason}"),
        StockCheckStep(key="forecast", name="预测推演", status="completed", detail=f"本地规则兜底：{reason}"),
        StockCheckStep(key="agent_brief", name="Agent复核", status="completed", detail=f"本地规则兜底：{reason}"),
        StockCheckStep(key="corridor_risk", name="通道风险", status="skipped", detail="稳定币/支付通道风险不适用于普通个股一键检测。"),
    ]
    return StockCheckResponse(
        provider="local-rule",
        model="stock-check-v1",
        generated_at=datetime.now(timezone.utc),
        stock=request.stock,
        verdict=verdict,
        score=score,
        confidence=0.45,
        summary=_stock_check_summary(
            request,
            verdict,
            score,
            stock_result,
            sentiment_result,
            news_result,
            forecast_result,
            [],
        ),
        action_items=_dedupe_lines(stock_result.watch_items + forecast_result.actions + rag_result.actions + agent_result.actions)[:8],
        risk_flags=_dedupe_lines(stock_result.risks + forecast_result.risks + report_result.risks + news_result.risks)[:8],
        checks=checks,
        stock_analysis=stock_result,
        sentiment=sentiment_result,
        news_summary=news_result,
        report_analysis=report_result,
        rag_answer=rag_result,
        forecast=forecast_result,
        agent_brief=agent_result,
        warnings=[],
    )


def _stock_check_score(
    request: StockCheckRequest,
    stock_result: Optional[StockAnalysisResponse],
    sentiment_result: Optional[SentimentResponse],
    forecast_result: Optional[FinGptTaskResponse],
) -> int:
    score = 50.0
    if sentiment_result:
        score += sentiment_result.score * 18
    if stock_result:
        score += {"low": 8, "medium": 0, "high": -14}.get(stock_result.risk_level, 0)
        score += {"positive": 8, "neutral": 0, "negative": -8}.get(stock_result.sentiment_label, 0)
    if forecast_result:
        score += (forecast_result.confidence - 0.5) * 12
        bearish_words = ("下行", "承压", "回撤", "风险", "走弱")
        bullish_words = ("上行", "改善", "催化", "增长", "突破")
        joined = " ".join(forecast_result.signals + forecast_result.key_points + forecast_result.actions)
        score += 5 if any(word in joined for word in bullish_words) else 0
        score -= 5 if any(word in joined for word in bearish_words) else 0
    if request.stock.change_percent is not None:
        score += max(-6, min(6, request.stock.change_percent))
    return int(round(_clamp(score, 0, 100)))


def _fallback_stock_analysis(request: StockCheckRequest) -> StockAnalysisResponse:
    sentiment_label, sentiment_score = _fallback_sentiment_label(_stock_check_context(request))
    risk_level = "medium"
    if request.stock.change_percent is not None and request.stock.change_percent <= -4:
        risk_level = "high"
    elif request.stock.focus_level == "high" and request.stock.community_score and request.stock.community_score >= 75:
        risk_level = "medium"
    return StockAnalysisResponse(
        provider="local-rule",
        model="stock-check-v1",
        generated_at=datetime.now(timezone.utc),
        executive_summary=(
            f"{request.stock.name} 当前由本地规则完成快速体检：结合价格、关注度、社区资料和用户问题，"
            "先输出观察清单，等待云模型或外部数据补充验证。"
        ),
        sentiment_label=sentiment_label,
        sentiment_score=sentiment_score,
        risk_level=risk_level,
        catalysts=_dedupe_lines([post.title for post in request.posts[:4]] + ["关注公告和财报更新"])[:4],
        risks=["云模型未完成，结论需复核", "资料可能不完整", "短线价格波动可能放大"],
        watch_items=["补充最新公告", "核对财报关键指标", "跟踪成交量和资金流", "复核估值与催化匹配"],
        suggested_questions=["最新催化是否可验证？", "主要风险是否已反映在价格中？"],
    )


def _fallback_sentiment(text: str) -> SentimentResponse:
    label, score = _fallback_sentiment_label(text)
    return SentimentResponse(
        provider="local-rule",
        model="stock-check-v1",
        label=label,
        score=score,
        rationale="云模型未及时返回，使用本地关键词和风险词做快速情绪判断。",
    )


def _fallback_task(
    capability: str,
    title: str,
    request: StockCheckRequest,
    context: str,
) -> FinGptTaskResponse:
    label, _ = _fallback_sentiment_label(context)
    stock_name = f"{request.stock.name}（{request.stock.symbol}）"
    signals = _dedupe_lines(
        [post.title for post in request.posts[:3]]
        + _extract_context_titles(context)
        + [f"{stock_name} 关注度：{request.stock.focus_level or '未知'}"]
    )
    risks = ["云模型未及时返回，需人工复核", "数据源覆盖可能不足", "短线信号不能替代基本面"]
    actions = ["补充最新公告/财报", "交叉验证新闻来源", "跟踪价格和成交量"]
    if label == "positive":
        signals.append("本地情绪偏积极")
    elif label == "negative":
        risks.append("本地情绪偏谨慎")
    return FinGptTaskResponse(
        provider="local-rule",
        model="stock-check-v1",
        generated_at=datetime.now(timezone.utc),
        capability=capability,
        title=f"{stock_name} {title}",
        summary=f"{title}云模型未及时返回，已基于本地资料生成快速复核清单。",
        key_points=signals[:5],
        signals=signals[:5],
        risks=risks[:5],
        actions=actions,
        sources=["本地个股快照", "社区/资料输入"],
        confidence=0.45,
    )


def _fallback_sentiment_label(text: str) -> tuple[str, float]:
    lowered = text.lower()
    positive = sum(1 for word in ["增长", "超预期", "改善", "上调", "催化", "突破", "positive", "beat"] if word in lowered)
    negative = sum(1 for word in ["风险", "下滑", "承压", "监管", "事故", "不确定", "negative", "miss"] if word in lowered)
    if positive > negative:
        return "positive", min(0.8, 0.2 + positive * 0.12)
    if negative > positive:
        return "negative", max(-0.8, -0.2 - negative * 0.12)
    return "neutral", 0.0


def _extract_context_titles(context: str) -> list[str]:
    titles = re.findall(r"(?:资料|数据源)\s*\d+[：:]\s*(.+)", context or "")
    return [title.strip() for title in titles[:6] if title.strip()]


def _stock_check_summary(
    request: StockCheckRequest,
    verdict: str,
    score: int,
    stock_result: Optional[StockAnalysisResponse],
    sentiment_result: Optional[SentimentResponse],
    news_result: Optional[FinGptTaskResponse],
    forecast_result: Optional[FinGptTaskResponse],
    warnings: list[str],
) -> str:
    parts = [
        f"{request.stock.name}（{request.stock.symbol}）一键检测结论为“{verdict}”，综合分 {score}/100。",
    ]
    if stock_result:
        parts.append(stock_result.executive_summary)
    elif news_result:
        parts.append(news_result.summary)
    if sentiment_result:
        parts.append(f"文本情绪为{sentiment_result.label}，情绪分 {sentiment_result.score:.2f}。")
    if forecast_result:
        parts.append(f"预测推演：{forecast_result.summary}")
    if warnings:
        parts.append(f"有 {len(warnings)} 个能力未完成，需人工复核。")
    return " ".join(part.strip() for part in parts if part.strip())[:900]


def _dedupe_lines(lines: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for line in lines:
        clean = re.sub(r"\s+", " ", str(line or "")).strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean[:120])
    return result


def _json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return _dedupe_lines([str(item) for item in value])[:6]
    if isinstance(value, str) and value.strip():
        return _dedupe_lines(re.split(r"[；;\n]", value))[:6]
    return []


def _number_value(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _clean_step_error(exc: Exception) -> str:
    text = re.sub(r"\s+", " ", str(exc)).strip()
    return text[:260] or exc.__class__.__name__


@app.get("/api/data-sources/{source_id}", response_model=DataSourceRecord)
async def api_get_data_source(source_id: str) -> DataSourceRecord:
    source = get_data_source(source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Data source not found")
    return source


@app.get("/api/agents/health", response_model=AgentRuntimeHealthResponse)
async def agent_runtime_health() -> AgentRuntimeHealthResponse:
    counts = task_counts()
    return AgentRuntimeHealthResponse(
        status="ok",
        worker_running=is_worker_running(),
        pending=counts["pending"],
        running=counts["running"],
        completed=counts["completed"],
        failed=counts["failed"],
    )


@app.get("/api/agents/tasks", response_model=InvestmentTaskListResponse)
async def list_agent_tasks(limit: int = 50) -> InvestmentTaskListResponse:
    return InvestmentTaskListResponse(tasks=list_investment_tasks(limit=limit))


@app.post("/api/agents/tasks", response_model=InvestmentTaskRecord)
async def create_agent_task(request: InvestmentTaskCreateRequest) -> InvestmentTaskRecord:
    return create_investment_task(request)


@app.get("/api/agents/tasks/{task_id}", response_model=InvestmentTaskRecord)
async def get_agent_task(task_id: str) -> InvestmentTaskRecord:
    task = get_investment_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.post("/api/agents/tasks/{task_id}/retry", response_model=InvestmentTaskRecord)
async def retry_agent_task(task_id: str) -> InvestmentTaskRecord:
    task = retry_investment_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.post("/api/agents/tasks/{task_id}/cancel", response_model=InvestmentTaskRecord)
async def cancel_agent_task(task_id: str) -> InvestmentTaskRecord:
    task = cancel_investment_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.post("/api/ai/stock-analysis", response_model=StockAnalysisResponse)
async def stock_analysis(request: StockAnalysisRequest) -> StockAnalysisResponse:
    try:
        return await llm.analyze_stock(request)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/ai/sentiment", response_model=SentimentResponse)
async def sentiment(request: SentimentRequest) -> SentimentResponse:
    try:
        return await llm.score_sentiment(request.text)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/fingpt/stock-check", response_model=StockCheckResponse)
async def stock_check(request: StockCheckRequest) -> StockCheckResponse:
    posts = request.posts[:10]
    evidence_items = list_data_items(symbol=request.stock.symbol, limit=8, sort="time_desc")
    stock_context = _stock_check_context(request, evidence_items)
    single_pass = await _stock_check_single_pass(request, stock_context)
    if single_pass:
        return single_pass

    news_items = [
        {
            "title": post.title,
            "summary": post.summary,
            "source": post.category,
            "published_at": post.publish_time,
        }
        for post in posts
    ]
    documents = _stock_check_documents(request, evidence_items)

    jobs = {
        "stock_analysis": (
            "个股投研",
            llm.analyze_stock(
                StockAnalysisRequest(
                    stock=request.stock,
                    posts=posts,
                    question=request.question,
                    locale=request.locale,
                )
            ),
        ),
        "sentiment": (
            "金融情绪",
            llm.score_sentiment(stock_context),
        ),
        "news_summary": (
            "新闻蒸馏",
            llm.summarize_news(
                NewsSummaryRequest(
                    stock=request.stock,
                    items=news_items,
                    focus=request.question or "识别影响股价的事实变化、催化和风险",
                    locale=request.locale,
                )
            ),
        ),
        "report_analysis": (
            "资料解读",
            llm.analyze_report(
                ReportAnalysisRequest(
                    title=f"{request.stock.name} 一键检测资料解读",
                    report_text=stock_context,
                    stock=request.stock,
                    locale=request.locale,
                )
            ),
        ),
        "rag_answer": (
            "RAG问答",
            llm.rag_query(
                RagQueryRequest(
                    question=f"基于资料，{request.stock.name}（{request.stock.symbol}）当前最需要验证的投资问题是什么？",
                    documents=documents,
                    locale=request.locale,
                )
            ),
        ),
        "forecast": (
            "预测推演",
            llm.forecast(
                ForecastRequest(
                    stock=request.stock,
                    horizon=request.horizon,
                    context=request.question,
                    posts=posts,
                    locale=request.locale,
                )
            ),
        ),
        "agent_brief": (
            "Agent复核",
            llm.agent_brief(
                AgentBriefRequest(
                    role="ops_oversight",
                    context=(
                        f"请作为投研流程监督 Agent，复核 {request.stock.name}（{request.stock.symbol}）"
                        f"的一键检测输入，输出需要人工复核的事实、风险和下一步动作。\n{stock_context[:5000]}"
                    ),
                    locale=request.locale,
                )
            ),
        ),
    }
    results = await asyncio.gather(
        *[_run_stock_check_job(key, name, coro) for key, (name, coro) in jobs.items()]
    )
    data = {key: value for key, _, value in results}
    checks = [step for _, step, _ in results]
    checks.append(
        StockCheckStep(
            key="corridor_risk",
            name="通道风险",
            status="skipped",
            detail="稳定币/支付通道风险不适用于普通个股一键检测。",
        )
    )

    stock_result = data.get("stock_analysis") if isinstance(data.get("stock_analysis"), StockAnalysisResponse) else None
    sentiment_result = data.get("sentiment") if isinstance(data.get("sentiment"), SentimentResponse) else None
    news_result = data.get("news_summary") if isinstance(data.get("news_summary"), FinGptTaskResponse) else None
    report_result = data.get("report_analysis") if isinstance(data.get("report_analysis"), FinGptTaskResponse) else None
    rag_result = data.get("rag_answer") if isinstance(data.get("rag_answer"), FinGptTaskResponse) else None
    forecast_result = data.get("forecast") if isinstance(data.get("forecast"), FinGptTaskResponse) else None
    agent_result = data.get("agent_brief") if isinstance(data.get("agent_brief"), FinGptTaskResponse) else None

    if stock_result is None:
        stock_result = _fallback_stock_analysis(request)
        _mark_stock_check_fallback(checks, "stock_analysis")
    if sentiment_result is None:
        sentiment_result = _fallback_sentiment(stock_context)
        _mark_stock_check_fallback(checks, "sentiment")
    if news_result is None:
        news_result = _fallback_task("news_summary", "新闻蒸馏", request, stock_context)
        _mark_stock_check_fallback(checks, "news_summary")
    if report_result is None:
        report_result = _fallback_task("report_analysis", "资料解读", request, stock_context)
        _mark_stock_check_fallback(checks, "report_analysis")
    if rag_result is None:
        rag_result = _fallback_task("rag_query", "RAG问答", request, stock_context)
        _mark_stock_check_fallback(checks, "rag_answer")
    if forecast_result is None:
        forecast_result = _fallback_task("forecast", "预测推演", request, stock_context)
        _mark_stock_check_fallback(checks, "forecast")
    if agent_result is None:
        agent_result = _fallback_task("agent_brief", "Agent复核", request, stock_context)
        _mark_stock_check_fallback(checks, "agent_brief")

    warnings = [step.detail for step in checks if step.status == "failed"]
    score = _stock_check_score(request, stock_result, sentiment_result, forecast_result)
    verdict = "重点跟踪" if score >= 68 else "谨慎观察" if score >= 45 else "暂不行动"
    task_confidences = [
        item.confidence
        for item in [news_result, report_result, rag_result, forecast_result, agent_result]
        if item is not None
    ]
    confidence = _clamp(
        (sum(task_confidences) / len(task_confidences)) if task_confidences else 0.55,
        0,
        1,
    )

    action_items = _dedupe_lines(
        (stock_result.watch_items if stock_result else [])
        + (forecast_result.actions if forecast_result else [])
        + (rag_result.actions if rag_result else [])
        + (agent_result.actions if agent_result else [])
    )[:8]
    risk_flags = _dedupe_lines(
        (stock_result.risks if stock_result else [])
        + (forecast_result.risks if forecast_result else [])
        + (report_result.risks if report_result else [])
        + (news_result.risks if news_result else [])
    )[:8]

    summary = _stock_check_summary(
        request,
        verdict,
        score,
        stock_result,
        sentiment_result,
        news_result,
        forecast_result,
        warnings,
    )
    return StockCheckResponse(
        provider=llm.provider_name,
        model=llm.model,
        generated_at=datetime.now(timezone.utc),
        stock=request.stock,
        verdict=verdict,
        score=score,
        confidence=confidence,
        summary=summary,
        action_items=action_items,
        risk_flags=risk_flags,
        checks=checks,
        stock_analysis=stock_result,
        sentiment=sentiment_result,
        news_summary=news_result,
        report_analysis=report_result,
        rag_answer=rag_result,
        forecast=forecast_result,
        agent_brief=agent_result,
        warnings=warnings,
    )


@app.post("/api/fingpt/news-summary", response_model=FinGptTaskResponse)
async def news_summary(request: NewsSummaryRequest) -> FinGptTaskResponse:
    try:
        return await llm.summarize_news(request)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/fingpt/report-analysis", response_model=FinGptTaskResponse)
async def report_analysis(request: ReportAnalysisRequest) -> FinGptTaskResponse:
    try:
        return await llm.analyze_report(request)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/fingpt/rag-query", response_model=FinGptTaskResponse)
async def rag_query(request: RagQueryRequest) -> FinGptTaskResponse:
    try:
        return await llm.rag_query(request)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/fingpt/forecast", response_model=FinGptTaskResponse)
async def forecast(request: ForecastRequest) -> FinGptTaskResponse:
    try:
        return await llm.forecast(request)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/fingpt/corridor-risk", response_model=FinGptTaskResponse)
async def corridor_risk(request: CorridorRiskRequest) -> FinGptTaskResponse:
    try:
        return await llm.corridor_risk(request)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/fingpt/agent-brief", response_model=FinGptTaskResponse)
async def agent_brief(request: AgentBriefRequest) -> FinGptTaskResponse:
    try:
        return await llm.agent_brief(request)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
