from __future__ import annotations

import asyncio
import json
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Optional

import httpx
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from dotenv import load_dotenv

from .agent_engines import DEFAULT_ENGINE_KEY, list_engines
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
from .agent_events import agent_task_event_stream
from .auth import (
    AuthMiddleware,
    AuthUserOut,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserExistsError,
    UserListResponse,
    authenticate,
    auth_required,
    count_users,
    create_access_token,
    create_user,
    get_user_out_by_id,
    init_auth,
    is_valid_email,
    list_users,
    require_admin,
    require_current_user,
    self_register_enabled,
)
from .ai_supply_chain import fetch_ai_supply_chain_capacity_trends
from .data_sources import (
    capture_agent_web_pages,
    create_data_source,
    delete_data_source,
    delete_data_item,
    delete_data_source_module_ref,
    get_data_item,
    get_data_source,
    init_data_source_db,
    keyword_crawl_data_source,
    list_data_source_module_refs,
    corpus_stats,
    crawl_evidence_if_thin,
    list_data_items,
    list_data_sources,
    list_data_tags,
    query_tokens_2gram,
    save_data_source_module_ref,
    store_upload_item,
    sync_data_source,
    update_data_item,
)
from .dulus_runtime import (
    build_dulus_runtime_status,
    create_dulus_memory,
    init_dulus_runtime_db,
    inspect_authorized_webbridge,
    list_dulus_memories,
    list_dulus_tools,
    run_dulus_roundtable,
)
from .file_tools import extract_local_file, extract_upload_file
from .cn_earnings_skill import (
    detect_cn_earnings_request,
    diagnose_cn_earnings,
    enrich_cn_earnings_record_detail,
    format_cn_earnings_skill_response,
    scan_cn_earnings,
)
from .customs_hs_detail import fetch_customs_hs_detail_snapshot, search_customs_hs_detail_products
from .customs_trade import build_customs_trade_analysis_text, fetch_customs_trade_snapshot
from .earnings_calendar import fetch_earnings_calendar
from .agent_tools import AgentTool, list_tools as list_agent_tool_specs, register_tool
from .mcp_tools import discover_mcp_agent_tools
from .llm import CloudResearchLLM, extract_citable_sources, tool_agent_to_orchestrator_response
from .market_data import fetch_market_quotes, search_market_symbols
from .market_layers import build_market_data_layer_status
from .major_event_skill import (
    detect_major_event_request,
    format_major_event_skill_response,
    scan_major_events,
)
from .mcp_hub import (
    call_mcp_tool,
    create_mcp_server,
    delete_mcp_server,
    discover_mcp_server,
    init_mcp_db,
    list_mcp_capabilities,
    list_mcp_servers,
)
from .model_config import public_model_config, save_model_config, configure_data_source_egress
from .multi_market_decision import build_multi_market_decision
from .tear_sheet import (
    build_briefing,
    build_macro_review,
    build_portfolio_review,
    build_tear_sheet,
    build_watchlist_summary,
)
from .options_signal import fetch_options_signals
from .official_news import fetch_official_news
from .people_voices import (
    FIGURES_BY_ID,
    fetch_people_spotlight,
    fetch_person_voices,
)
from .premarket_opportunity import build_premarket_opportunity_radar
from .professional_research import (
    analyze_professional_report,
    get_professional_report,
    ingest_professional_report_from_item,
    ingest_professional_report_text,
    init_professional_research_db,
    list_professional_chunks,
    list_professional_metrics,
    list_professional_reports,
    query_professional_rag,
    run_professional_eval,
)
from .shared_utils import clamp
from .risk_management import (
    calculate_greeks,
    calculate_position_risk,
    close_position,
    create_position,
    delete_position,
    get_pnl_summary,
    get_position,
    get_risk_limits,
    get_risk_summary,
    init_risk_db,
    list_pnl_records,
    list_positions,
    PositionAlreadyClosedError,
    refresh_position_prices,
    update_position,
    update_risk_limit,
)
from .backtest_engine import (
    calculate_backtest_metrics,
    compute_equity_curve_from_trades,
    create_backtest,
    delete_backtest,
    get_backtest,
    init_backtest_db,
    list_backtests,
    update_backtest,
)
from .backtest_executor import run_backtest, list_backtest_results
from .agent_loop import run_agent_research_loop
from .market_dashboard import (
    fetch_market_dashboard,
    fetch_ashare_dashboard,
)
from .tushare_data import fetch_ashare_structured_data
from .cross_module_aggregator import (
    gather_all_for_stock,
    build_injection_block,
)
from .realtime_messages import (
    create_realtime_message,
    init_realtime_message_db,
    list_realtime_messages,
    publish_data_source_items,
    realtime_message_event_stream,
    register_post_message_hook,
)
from .recall_subscriptions import (
    create_recall_subscription,
    delete_recall_subscription,
    dispatch_recall,
    init_recall_subscription_db,
    list_deliveries,
    list_recall_subscriptions,
    mark_recall_click,
    recall_metrics,
    recent_deliveries,
)
from .share_snapshots import (
    create_share_snapshot,
    get_share_snapshot,
    increment_share_views,
    init_share_snapshot_db,
    render_not_found_html,
    render_share_page_html,
)
from .report_url_ingest import extract_report_url
from .eastmoney_reports import eastmoney_report_pdf_url, query_eastmoney_reports
from .research_vision import analyze_pdf_vision
from urllib.parse import urlparse
from .research_workbench import (
    WORKBENCH_DIR,
    proxy_research_workbench,
    stop_research_workbench,
    warm_research_workbench,
)
from .shareholder_change_skill import (
    detect_shareholder_change_request,
    format_shareholder_change_skill_response,
    interpret_shareholder_change,
    scan_shareholder_changes,
)
from .schemas import (
    AgentBriefRequest,
    AgentEngineInfo,
    AgentEngineListResponse,
    AgentToolInfo,
    AgentToolListResponse,
    AgentRuntimeHealthResponse,
    DataQuality,
    ResearchReportItem,
    ResearchReportSearchResponse,
    ResearchVisionAnalyzeRequest,
    ResearchVisionAnalysisResponse,
    CapabilityListResponse,
    CnEarningsDiagnosisRequest,
    CnEarningsDiagnosisResponse,
    CnEarningsRecordDetailRequest,
    CnEarningsRecordDetailResponse,
    CnEarningsScanRequest,
    CnEarningsScanResponse,
    CorridorRiskRequest,
    CustomsTradeAnalysisRequest,
    DataSourceCreateRequest,
    DataSourceItemInterpretRequest,
    DataSourceItemInterpretResponse,
    DataSourceItemListResponse,
    DataSourceItemRecord,
    DataSourceItemUpdateRequest,
    DataSourceKeywordCrawlRequest,
    DataSourceKeywordCrawlResponse,
    DataSourceListResponse,
    DataSourceModuleRefCreateRequest,
    DataSourceModuleRefListResponse,
    DataSourceModuleRefRecord,
    DataSourceRecord,
    DataSourceSyncRequest,
    DataSourceSyncResponse,
    DataSourceCorpusStats,
    DataSourceTagListResponse,
    DulusMemoryCreateRequest,
    DulusMemoryListResponse,
    DulusMemoryRecord,
    DulusRoundtableRequest,
    DulusRoundtableResponse,
    DulusRuntimeStatusResponse,
    DulusToolRecord,
    DulusWebBridgeInspectRequest,
    DulusWebBridgeInspectResponse,
    EarningsCalendarResponse,
    FinGptTaskResponse,
    ForecastRequest,
    FileExtractionResponse,
    GeneralChatRequest,
    GeneralChatResponse,
    InvestmentTaskCreateRequest,
    InvestmentTaskListResponse,
    InvestmentTaskRecord,
    MarketQuoteListResponse,
    MarketDataLayerStatusResponse,
    MarketSymbolSearchResponse,
    AShareStructuredDataResponse,
    MajorEventScanRequest,
    MajorEventScanResponse,
    McpCapabilityListResponse,
    McpDiscoverResponse,
    McpServerCreateRequest,
    McpServerListResponse,
    McpServerRecord,
    McpToolCallRequest,
    McpToolCallResponse,
    NewsSummaryRequest,
    OfficialNewsResponse,
    PeopleSpotlightResponse,
    PersonDigestResponse,
    PersonProfile,
    ModelConfigRequest,
    ModelConfigResponse,
    MultiMarketDecisionRequest,
    MultiMarketDecisionResponse,
    OptionsAiAnalysisRequest,
    OptionsAiAnalysisResponse,
    OptionsSignalResponse,
    OrchestratorChatRequest,
    OrchestratorChatResponse,
    PremarketOpportunityResponse,
    ProfessionalEvalRunRequest,
    ProfessionalEvalRunResponse,
    ProfessionalMetricListResponse,
    ProfessionalRagQueryRequest,
    ProfessionalRagQueryResponse,
    ProfessionalReportAnalysisRequest,
    ProfessionalReportAnalysisResponse,
    ProfessionalReportChunkListResponse,
    ProfessionalReportIngestRequest,
    ProfessionalReportListResponse,
    ProfessionalReportRecord,
    ProfessionalReportUrlIngestRequest,
    ProfessionalWorkbenchFileIngestRequest,
    RagQueryRequest,
    RealtimeMessageCreateRequest,
    RealtimeMessageListResponse,
    RecallDeliveryLogResponse,
    RecallDeliveryResult,
    RecallMetricsResponse,
    RecallSubscriptionCreateRequest,
    RecallSubscriptionListResponse,
    RecallSubscriptionRecord,
    ShareSnapshotCreateRequest,
    ShareSnapshotRecord,
    RealtimeMessageRecord,
    ReportAnalysisRequest,
    SentimentRequest,
    SentimentResponse,
    attach_data_quality,
    classify_data_quality,
    ShareholderChangeInterpretRequest,
    ShareholderChangeInterpretResponse,
    ShareholderChangeScanRequest,
    ShareholderChangeScanResponse,
    StockAnalysisRequest,
    StockAnalysisResponse,
    StockCheckRequest,
    StockCheckResponse,
    StockCheckStep,
    BriefingResponse,
    MarketQuote,
    StockCompareItem,
    StockCompareResponse,
    StockScreenCriterion,
    StockScreenMatch,
    StockScreenResponse,
    MacroReviewResponse,
    PortfolioReviewResponse,
    SystemReadinessCheck,
    TearSheetResponse,
    SystemReadinessResponse,
    GreeksRequest,
    GreeksResponse,
    PnlRecord,
    PnlSummaryResponse,
    PositionCloseRequest,
    PositionCreateRequest,
    PositionListResponse,
    PositionRecord,
    PositionRiskMetrics,
    PositionUpdateRequest,
    RiskAlert,
    RiskLimitRecord,
    RiskLimitUpdateRequest,
    RiskSummaryResponse,
    BacktestCreateRequest,
    BacktestListResponse,
    BacktestMetricsRequest,
    BacktestMetricsResponse,
    BacktestRecord,
    MarketDashboardResponse,
    DashboardAnalysisResponse,
    ModuleContextChatRequest,
    CrossModuleResearchRequest,
    CrossModuleResearchResponse,
)

load_dotenv()


def _safe_workbench_file_path(out: str, filename: str) -> Path:
    root = WORKBENCH_DIR.resolve()
    base = (root / (out or "downloads/海外投行报告")).resolve()
    try:
        base.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="抓取目录不在研报工作台内。") from exc

    file_path = (base / filename).resolve()
    try:
        file_path.relative_to(base)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="文件路径越界。") from exc

    if file_path.name in {"manifest.json", "files.csv"} or file_path.name.endswith(".part"):
        raise HTTPException(status_code=400, detail="该文件不是可入库研报。")
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="抓取文件不存在。")
    return file_path


def _allowed_origins() -> list[str]:
    raw = os.getenv("CORS_ORIGINS", "*")
    origins = [origin.strip() for origin in raw.split(",") if origin.strip()] or ["*"]
    if "*" in origins:
        return origins
    local_origins = [
        f"http://{host}:{port}"
        for host in ("localhost", "127.0.0.1")
        for port in range(3000, 3016)
    ]
    return list(dict.fromkeys([*origins, *local_origins]))

@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_data_source_egress()  # 数据源域名绕过出网代理（封锁环境下仍能直连取数）
    init_auth()  # 建认证表（统一存储层）+ 按 env 预置管理员
    init_task_db()
    init_data_source_db()
    init_professional_research_db()
    init_realtime_message_db()
    init_recall_subscription_db()
    init_share_snapshot_db()
    # 新信号落库广播后，扇出到离线召回订阅（邮件 / Web Push）。
    register_post_message_hook(lambda message: dispatch_recall(message))
    init_mcp_db()
    init_risk_db()
    init_backtest_db()
    init_dulus_runtime_db()
    await warm_research_workbench()
    await start_agent_worker()
    yield
    await stop_agent_worker()
    await stop_research_workbench()


app = FastAPI(
    title="DeepFocus AI API",
    description="Cloud-model research API for the DeepFocus frontend.",
    version="0.1.0",
    lifespan=lifespan,
)

# 先加鉴权中间件、后加 CORS，使 CORS 处于最外层——
# 这样鉴权返回的 401/403 也会带上 CORS 头，浏览器能正确读到状态码而非报跨域。
app.add_middleware(AuthMiddleware)

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


@app.post("/api/auth/register", response_model=TokenResponse)
async def auth_register(payload: RegisterRequest) -> TokenResponse:
    # 已有用户后是否允许自助注册由 env 控制；机构部署可关掉只让管理员开账号。
    if not self_register_enabled() and count_users() > 0:
        raise HTTPException(status_code=403, detail="自助注册已关闭，请联系管理员开通账号")
    if not is_valid_email(payload.email):
        raise HTTPException(status_code=422, detail="邮箱格式不正确")
    try:
        user = create_user(payload.email, payload.username, payload.password)
    except UserExistsError:
        raise HTTPException(status_code=409, detail="邮箱或用户名已存在")
    return TokenResponse(access_token=create_access_token(user), user=user)


@app.post("/api/auth/login", response_model=TokenResponse)
async def auth_login(payload: LoginRequest) -> TokenResponse:
    user = authenticate(payload.username, payload.password)
    if user is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return TokenResponse(access_token=create_access_token(user), user=user)


@app.get("/api/auth/me", response_model=AuthUserOut)
async def auth_me(request: Request) -> AuthUserOut:
    claims = require_current_user(request)
    user = get_user_out_by_id(str(claims.get("sub", "")))
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在或已停用")
    return user


@app.get("/api/auth/users", response_model=UserListResponse)
async def auth_list_users(request: Request) -> UserListResponse:
    require_admin(request)  # handler 级守卫：即便旁路态也只放行管理员
    return UserListResponse(users=list_users())


@app.get("/api/system/readiness", response_model=SystemReadinessResponse)
async def system_readiness() -> SystemReadinessResponse:
    checks = _build_system_readiness_checks()
    weights = {
        "model": 18,
        "auth": 18,
        "agent_worker": 12,
        "data_sources": 14,
        "market_data": 10,
        "mcp_governance": 10,
        "cors": 10,
        "storage": 8,
    }
    score = 0.0
    for check in checks:
        weight = weights.get(check.key, 0)
        if check.status == "pass":
            score += weight
        elif check.status == "warn":
            score += weight * 0.5

    rounded_score = int(round(max(0, min(100, score))))
    blockers = [check.name for check in checks if check.status == "fail"]
    warnings = [check.name for check in checks if check.status == "warn"]
    status_label = "ready" if rounded_score >= 85 and not blockers else "degraded" if rounded_score >= 55 else "not_ready"
    return SystemReadinessResponse(
        status=status_label,
        score=rounded_score,
        generated_at=datetime.now(timezone.utc),
        checks=checks,
        blockers=blockers,
        warnings=warnings,
    )


def _build_system_readiness_checks() -> list[SystemReadinessCheck]:
    model_config = public_model_config()
    sources = list_data_sources()
    mcp_servers = list_mcp_servers()
    origins = _allowed_origins()
    quote_key_configured = bool(
        os.getenv("FINNHUB_API_KEY")
        or os.getenv("FINNHUB_TOKEN")
        or os.getenv("ALPHAVANTAGE_API_KEY")
        or os.getenv("ALPHA_VANTAGE_API_KEY")
    )
    auth_enforced = auth_required()
    try:
        registered_users = count_users()
    except Exception:
        registered_users = 0
    from . import storage as core_storage

    managed_database = core_storage.is_managed_database()
    persistent_db_paths = [
        os.getenv("DEEPFOCUS_AGENT_DB_PATH"),
        os.getenv("DEEPFOCUS_DATA_SOURCE_DB_PATH"),
        os.getenv("DEEPFOCUS_MCP_DB_PATH"),
    ]
    using_explicit_storage = managed_database or any(path for path in persistent_db_paths)
    high_risk_mcp_without_approval = [
        server.name for server in mcp_servers
        if server.risk_level == "high" and not server.approval_required
    ]

    return [
        SystemReadinessCheck(
            key="model",
            name="真实模型通道",
            status="pass" if model_config.provider != "mock" and model_config.api_key_configured else "fail",
            detail=(
                f"当前模型：{model_config.provider}/{model_config.model}。"
                if model_config.provider != "mock" and model_config.api_key_configured
                else "当前仍是 mock 或缺少 API Key，AI 投研不能作为真实结论链路。"
            ),
            remediation="在 FinGPT → 模型配置中保存真实模型、Base URL 和 API Key。",
        ),
        SystemReadinessCheck(
            key="auth",
            name="认证与权限",
            status=(
                "pass" if auth_enforced and registered_users > 0
                else "warn" if auth_enforced
                else "fail"
            ),
            detail=(
                f"已启用 JWT 认证 + RBAC，已注册 {registered_users} 个账号。"
                if auth_enforced and registered_users > 0
                else "已启用强制认证，但尚无账号；请注册或预置管理员。"
                if auth_enforced
                else "当前 API 未启用强制认证（演示态）；JWT/RBAC 通道已就绪，置 DEEPFOCUS_AUTH_REQUIRED=true 即生效。"
            ),
            remediation="设置 DEEPFOCUS_AUTH_REQUIRED=true、DEEPFOCUS_JWT_SECRET，并预置管理员（DEEPFOCUS_ADMIN_EMAIL/PASSWORD）。",
        ),
        SystemReadinessCheck(
            key="agent_worker",
            name="Agent Worker",
            status="pass" if is_worker_running() else "fail",
            detail="投研任务 worker 正在运行。" if is_worker_running() else "投研任务 worker 未运行。",
            remediation="启动 FastAPI 后端并确认 lifespan 能正常启动 worker。",
        ),
        SystemReadinessCheck(
            key="data_sources",
            name="证据数据源",
            status="pass" if len(sources) >= 2 else "warn" if sources else "fail",
            detail=f"已注册 {len(sources)} 个数据源。",
            remediation="至少配置行情、公告/财报、新闻/社区、研报文件等可追溯数据源。",
        ),
        SystemReadinessCheck(
            key="market_data",
            name="行情主数据",
            status="pass" if quote_key_configured else "warn",
            detail=(
                "已配置 Finnhub/Alpha Vantage 等主行情 key。"
                if quote_key_configured
                else "未配置主行情 key，会依赖免费公共 fallback，时效和稳定性不足。"
            ),
            remediation="配置 FINNHUB_API_KEY、ALPHAVANTAGE_API_KEY 或接入本地授权行情源。",
        ),
        SystemReadinessCheck(
            key="mcp_governance",
            name="MCP 工具治理",
            status="fail" if high_risk_mcp_without_approval else "pass" if mcp_servers else "warn",
            detail=(
                f"高风险 MCP 未开启审批：{', '.join(high_risk_mcp_without_approval[:3])}。"
                if high_risk_mcp_without_approval
                else f"已登记 {len(mcp_servers)} 个 MCP server。"
            ),
            remediation="高风险 MCP 必须开启人工确认、allow-list 和调用审计。",
        ),
        SystemReadinessCheck(
            key="cors",
            name="CORS 边界",
            status="fail" if "*" in origins else "pass",
            detail="CORS 当前允许任意来源。" if "*" in origins else f"CORS 限定在 {len(origins)} 个来源。",
            remediation="生产环境设置 CORS_ORIGINS 为明确域名，避免使用 *。",
        ),
        SystemReadinessCheck(
            key="storage",
            name="持久化配置",
            status="pass" if using_explicit_storage else "warn",
            detail=(
                "已接入托管数据库（DEEPFOCUS_DATABASE_URL），认证子系统已运行其上。"
                if managed_database
                else "已显式配置核心 SQLite/存储路径。"
                if using_explicit_storage
                else "当前使用默认本地 SQLite 文件，适合单机演示，不适合多用户机构部署。"
            ),
            remediation="生产环境迁移到托管数据库/对象存储，并配置备份、迁移和权限。",
        ),
    ]


@app.api_route(
    "/research-workbench",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
async def research_workbench_root(request: Request):
    return await proxy_research_workbench(request, "")


@app.api_route(
    "/research-workbench/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
async def research_workbench_proxy(path: str, request: Request):
    return await proxy_research_workbench(request, path)


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
    return attach_data_quality(await fetch_market_quotes(requested_symbols))


@app.get("/api/market/search", response_model=MarketSymbolSearchResponse)
async def market_symbol_search(q: str = "", market: Optional[str] = None) -> MarketSymbolSearchResponse:
    return await search_market_symbols(q, market=market)


@app.get("/api/market/data-layers", response_model=MarketDataLayerStatusResponse)
async def market_data_layers(symbol: Optional[str] = None, keyword: Optional[str] = None) -> MarketDataLayerStatusResponse:
    return await build_market_data_layer_status(symbol=symbol, keyword=keyword)


@app.get("/api/market/ashare/structured", response_model=AShareStructuredDataResponse)
async def ashare_structured_data(
    symbol: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 120,
) -> AShareStructuredDataResponse:
    return await fetch_ashare_structured_data(
        symbol,
        start_date=start_date,
        end_date=end_date,
        limit=max(1, min(limit, 500)),
    )


@app.get("/api/options/signals", response_model=OptionsSignalResponse)
async def options_signals(
    symbols: str = "",
    horizon_days: int = 45,
    max_expirations: int = 3,
) -> OptionsSignalResponse:
    requested_symbols = [symbol.strip() for symbol in symbols.split(",") if symbol.strip()]
    return attach_data_quality(await fetch_options_signals(
        requested_symbols,
        horizon_days=horizon_days,
        max_expirations=max_expirations,
    ))


@app.post("/api/options/ai-analysis", response_model=OptionsAiAnalysisResponse)
async def options_ai_analysis(request: OptionsAiAnalysisRequest) -> OptionsAiAnalysisResponse:
    return attach_data_quality(await llm.analyze_options_trend(request))


@app.get("/api/earnings/calendar", response_model=EarningsCalendarResponse)
async def earnings_calendar(
    symbols: str = "",
    horizon: str = "3month",
    min_market_cap: Optional[float] = None,
    include_all: bool = False,
) -> EarningsCalendarResponse:
    requested_symbols = [symbol.strip() for symbol in symbols.split(",") if symbol.strip()]
    return attach_data_quality(await fetch_earnings_calendar(
        requested_symbols,
        horizon=horizon,
        min_market_cap=min_market_cap,
        include_all=include_all,
    ))


@app.post("/api/decision/multi-market", response_model=MultiMarketDecisionResponse)
async def multi_market_decision(request: MultiMarketDecisionRequest) -> MultiMarketDecisionResponse:
    # 用真实准实时行情刷新候选的现价/涨跌幅，让规则评分跑在真数据上，
    # 而非信任前端可能过期的快照或硬编码示例。A股/港股经东财/新浪可达，美股 best-effort。
    enriched_count = 0
    symbols = [s.symbol for s in (request.stocks or []) if s.symbol]
    if symbols:
        try:
            quote_resp = await fetch_market_quotes(symbols)
            qmap: dict[str, Any] = {}
            for q in quote_resp.quotes:
                sym = (q.symbol or "").upper()
                qmap[sym] = q
                qmap.setdefault(sym.split(".")[0], q)  # 容错：按去后缀的 base 也建一个键
            updated = []
            for s in request.stocks:
                key = (s.symbol or "").upper()
                q = qmap.get(key) or qmap.get(key.split(".")[0])
                if q and q.price:
                    updated.append(s.model_copy(update={
                        "current_price": q.price,
                        "change_percent": q.change_percent if q.change_percent is not None else s.change_percent,
                    }))
                    enriched_count += 1
                else:
                    updated.append(s)
            request = request.model_copy(update={"stocks": updated})
        except Exception:
            pass

    response = build_multi_market_decision(request)
    reasons: list[str] = []
    if enriched_count > 0:
        response.provider = "multi-market-rule"
        reasons.append(f"已用真实准实时行情刷新 {enriched_count}/{len(symbols)} 只候选的现价与涨跌幅，规则评分基于真数据。")
    else:
        reasons.append("未获取到候选的实时行情，评分基于传入快照或示例数据，请谨慎参考。")
    reasons.append("模块就绪度、依赖与回测计划为能力规划（规划中），非已落地的真实回测结果。")
    return attach_data_quality(response, reasons=reasons)


@app.get("/api/agents/premarket-opportunities", response_model=PremarketOpportunityResponse)
async def premarket_opportunities() -> PremarketOpportunityResponse:
    return attach_data_quality(await build_premarket_opportunity_radar())


def _market_quote_from_google(g: dict) -> MarketQuote:
    """把 Google Finance 抓取的真实行情 dict 转成 MarketQuote（provider=google-finance → degraded）。"""
    return MarketQuote(
        symbol=g["symbol"],
        price=g["price"],
        change=g.get("change"),
        change_percent=g.get("change_percent"),
        previous_close=g.get("previous_close"),
        open_price=g.get("open"),
        high=g.get("high"),
        low=g.get("low"),
        volume=g.get("volume"),
        currency=g.get("currency", "USD"),
        provider="google-finance",
        provider_name="Google Finance",
        fetched_at=datetime.now(timezone.utc).isoformat(),
        is_realtime=False,
        delay_note="Google Finance 网页行情，可能延迟约 15 分钟",
    )


def _market_quote_from_yahoo(g: dict) -> MarketQuote:
    """把 Yahoo Finance 官方行情 dict 转成 MarketQuote（provider=yahoo-finance → live、官方实时）。"""
    prev = g.get("previous_close")
    price = g.get("price")
    change = round(price - prev, 4) if (price and prev) else None
    pct = round(change / prev * 100, 4) if (change is not None and prev) else None
    return MarketQuote(
        symbol=g["symbol"],
        price=price,
        change=change,
        change_percent=pct,
        previous_close=prev,
        high=g.get("high"),
        low=g.get("low"),
        volume=g.get("volume"),
        currency=g.get("currency", "USD"),
        provider="yahoo-finance",
        provider_name="Yahoo Finance",
        fetched_at=datetime.now(timezone.utc).isoformat(),
        is_realtime=True,
        wk52_high=g.get("wk52_high"),
        wk52_low=g.get("wk52_low"),
    )


async def _enhance_tear_sheet_narrative(ts):
    """用 LLM 把确定性 7 维证据合成 2-3 句买方观点；mock/失败/超时回退模板（verdict/score 不变）。"""
    try:
        from .llm import CloudResearchLLM

        llm = CloudResearchLLM()
        if llm.provider == "mock":
            return ts
        narrative = await llm.synthesize_tear_sheet_narrative(ts)
        if narrative:
            ts.narrative = narrative
            ts.narrative_provider = llm.provider
    except Exception:
        pass  # LLM 不可用 → 保留确定性模板叙述
    return ts


async def _enhance_review_narrative(obj, *, view, subject):
    """组合/宏观速判：用 LLM 合成 narrative；mock/失败回退模板（verdict/score 不变）。"""
    try:
        from .llm import CloudResearchLLM

        llm = CloudResearchLLM()
        if llm.provider == "mock":
            return obj
        narrative = await llm.synthesize_review_narrative(
            subject=subject,
            verdict=obj.overall_verdict,
            score=getattr(obj, "overall_score", getattr(obj, "risk_score", 0)),
            confidence=obj.confidence,
            dimensions=obj.dimensions,
            view=view,
        )
        if narrative:
            obj.narrative = narrative
            obj.narrative_provider = llm.provider
    except Exception:
        pass
    return obj


async def _enhance_briefing_headline(briefing):
    """投研晨报：用 LLM 把宏观×组合×自选股合成晨会纪要 headline；mock/失败回退模板。"""
    try:
        from .llm import CloudResearchLLM

        llm = CloudResearchLLM()
        if llm.provider == "mock":
            return briefing
        headline = await llm.synthesize_briefing_headline(
            briefing.macro, briefing.portfolio, getattr(briefing, "watchlist", None)
        )
        if headline:
            briefing.headline = headline
            briefing.headline_provider = llm.provider
    except Exception:
        pass
    return briefing


async def _build_stock_tear_sheet_core(
    symbol: str,
    name: str = "",
    market_cap: Optional[float] = None,
    market: str = "",
) -> TearSheetResponse:
    """聚合行情/财报/期权多源证据，由确定性引擎逐维度判定，返回未经 LLM 叙述增强的速判卡。

    每块数据缺失时诚实标 insufficient，整体可信度取各维度最差档（引擎已算，不经 attach 覆盖）。
    端点会叠加 LLM 叙述；tool-use agent 的 get_stock_verdict 直接调用本函数（不触发 LLM，避免递归）。
    """
    sym = symbol.strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol 不能为空")

    import asyncio

    from .eastmoney_data import fetch_eastmoney_earnings, fetch_eastmoney_index, fetch_fund_flow
    from .github_data import (
        fetch_sp500_constituent,
        fetch_sp500_index_history,
        fetch_us10y_history,
    )
    from .google_finance import fetch_google_finance_quote
    from .nasdaq_data import fetch_nasdaq_earnings, fetch_nasdaq_options
    from .consensus_source import fetch_analyst_consensus
    from .valuation_source import fetch_valuation
    from .yahoo_finance import fetch_yahoo_history, fetch_yahoo_quote

    _mkt = (market or "").upper()
    if not _mkt and sym.isdigit():
        _mkt = "CN" if len(sym) == 6 else "HK"  # 6位数字→A股，其余纯数字→港股
    _cn_secid = "1.000300" if _mkt == "CN" else ("100.HSI" if _mkt == "HK" else None)

    async def _safe(coro, default):
        try:
            return await coro
        except Exception:
            return default

    # 所有真实数据源并行抓取（串行约 10s → 并行约 2s），各源独立容错、互不阻塞。
    (
        gquote,
        price_history,
        nasdaq_eps,
        nasdaq_opts,
        eastmoney_fin,
        sp500_idx,
        rates_history,
        constituent,
        cn_idx,
        valuation_data,
        yquote,
        consensus_data,
        fund_flow_data,
    ) = await asyncio.gather(
        _safe(fetch_google_finance_quote(sym, market or None), None),
        _safe(fetch_yahoo_history(sym, market or None), []),
        _safe(fetch_nasdaq_earnings(sym), None),
        _safe(fetch_nasdaq_options(sym), None),
        _safe(fetch_eastmoney_earnings(sym, market), None),
        _safe(fetch_sp500_index_history(), []),
        _safe(fetch_us10y_history(), []),
        _safe(fetch_sp500_constituent(sym), None),
        _safe(fetch_eastmoney_index(_cn_secid), []) if _cn_secid else _safe(asyncio.sleep(0, result=[]), []),
        _safe(fetch_valuation(sym, market), None),
        _safe(fetch_yahoo_quote(sym, market or None), None),
        _safe(fetch_analyst_consensus(sym, market or None), None),
        _safe(fetch_fund_flow(sym, market or None), None),
    )

    # 行情优先 Yahoo 官方（live），限流/失败回退 Google Finance（degraded）。
    quote = _market_quote_from_yahoo(yquote) if yquote else (_market_quote_from_google(gquote) if gquote else None)
    earnings_events: list = []
    options_signal = None

    # CN/HK：Yahoo 历史在本环境 403 → price_history 空，用东财个股日线兜底（价格趋势图转 live），
    # 并用日线 min/max 补 52周区间（quote 缺失 52周时）。
    if _mkt in ("CN", "HK") and not price_history:
        from .eastmoney_data import _em_stock_secid, fetch_eastmoney_kline

        _stk_secid = _em_stock_secid(sym, _mkt)
        if _stk_secid:
            price_history = await _safe(fetch_eastmoney_kline(_stk_secid), [])
            if price_history and quote is not None and not quote.wk52_high:
                _closes = [v for _, v in price_history]
                if _closes:
                    quote.wk52_high = max(_closes)
                    quote.wk52_low = min(_closes)

    # 市场环境维度按 market 选本土大盘：A股→沪深300、港股→恒生（东财），其余→标普500。
    market_index_name = "标普500"
    market_provider_tag = "github-sp500"
    market_source = None
    market_index_history = sp500_idx
    if _mkt in ("CN", "HK") and cn_idx:
        from .tear_sheet import _CN_INDEX_SRC

        market_index_name = "沪深300" if _mkt == "CN" else "恒生指数"
        market_provider_tag = "eastmoney"
        market_source = _CN_INDEX_SRC
        market_index_history = cn_idx

    return build_tear_sheet(
        symbol=sym,
        name=name or (constituent or {}).get("name") or sym,
        market_cap=market_cap or (valuation_data or {}).get("market_cap") or (gquote.get("market_cap") if gquote else None),
        currency=(quote.currency if quote else "USD"),
        quote=quote,
        earnings_events=earnings_events,
        options_signal=options_signal,
        market_index_history=market_index_history,
        rates_history=rates_history,
        constituent=constituent,
        valuation=gquote,
        valuation_data=valuation_data,
        consensus_data=consensus_data,
        fund_flow_data=fund_flow_data,
        price_history=price_history,
        nasdaq_eps=nasdaq_eps,
        nasdaq_opts=nasdaq_opts,
        eastmoney_fin=eastmoney_fin,
        market_index_name=market_index_name,
        market_source=market_source,
        market_provider_tag=market_provider_tag,
    )


@app.get("/api/stock/tear-sheet", response_model=TearSheetResponse)
async def stock_tear_sheet(
    symbol: str,
    name: str = "",
    market_cap: Optional[float] = None,
    market: str = "",
) -> TearSheetResponse:
    """个股速判卡：聚合多源证据由确定性引擎逐维度判定，再叠加 LLM 买方叙述。"""
    ts = await _build_stock_tear_sheet_core(symbol, name=name, market_cap=market_cap, market=market)
    return await _enhance_tear_sheet_narrative(ts)


async def _tool_get_stock_verdict(symbol: str, market: Optional[str] = None) -> Any:
    """工具：返回确定性引擎的速判卡结论（不触发 LLM 叙述）。verdict/score/各维度信号均为 ground truth。"""
    ts = await _build_stock_tear_sheet_core(symbol, market=market or "")
    return {
        "symbol": ts.symbol,
        "name": ts.name,
        "price": ts.price,
        "change_percent": ts.change_percent,
        "currency": ts.currency,
        "overall_verdict": ts.overall_verdict,
        "overall_score": ts.overall_score,
        "confidence": ts.confidence,
        "data_quality": ts.data_quality.model_dump() if ts.data_quality else None,
        "dimensions": [
            {
                "label": dim.label,
                "signal": dim.signal,
                "score": dim.score,
                "headline": dim.headline,
                "confidence": dim.confidence,
            }
            for dim in ts.dimensions
        ],
    }


register_tool(AgentTool(
    name="get_stock_verdict",
    description=(
        "获取个股速判卡：确定性引擎逐维度判定的综合结论（看多/看空/中性）、评分、置信度与各维度证据信号。"
        "这是平台核心结论（ground truth，非模型臆测），回答个股研判/是否值得买/综合看法类问题时应优先调用。美/A/港股通用。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "股票代码，如 AAPL、600519、00700"},
            "market": {"type": "string", "enum": ["US", "CN", "HK"], "description": "市场；缺省按代码推断"},
        },
        "required": ["symbol"],
    },
    handler=_tool_get_stock_verdict,
))


@app.get("/api/portfolio/review", response_model=PortfolioReviewResponse)
async def portfolio_review() -> PortfolioReviewResponse:
    """组合风险速判：基于本地持仓与风控摘要的买方视角一页纸（集中度/行业敞口/回撤/止损纪律）。"""
    from .github_data import fetch_sp500_index_history, fetch_us10y_history
    from .risk_management import get_risk_summary

    sp500: list = []
    rates: list = []
    try:
        sp500 = await fetch_sp500_index_history()
        rates = await fetch_us10y_history()
    except Exception:
        sp500, rates = [], []

    summary = get_risk_summary()
    # 有持仓才拉实时价（空仓零开销）；用 Google Finance 准实时价刷新 current_price 后重算盈亏/回撤。
    if summary.get("open_positions"):
        try:
            from .risk_management import apply_live_prices, fetch_live_prices_for_positions

            price_map = await fetch_live_prices_for_positions(summary["open_positions"])
            summary = apply_live_prices(summary, price_map)
        except Exception:
            pass
    review = build_portfolio_review(summary, sp500_history=sp500, rates_history=rates)
    return await _enhance_review_narrative(review, view="portfolio", subject="组合")


@app.get("/api/macro/review", response_model=MacroReviewResponse)
async def macro_review() -> MacroReviewResponse:
    """宏观环境速判：市场/利率/通胀/避险，全部真实公开数据（github datasets）。"""
    from .github_data import (
        fetch_gold_history,
        fetch_oil_history,
        fetch_sp500_index_history,
        fetch_us10y_history,
    )

    sp500: list = []
    rates: list = []
    oil: list = []
    gold: list = []
    try:
        sp500 = await fetch_sp500_index_history()
    except Exception:
        sp500 = []
    try:
        rates = await fetch_us10y_history()
    except Exception:
        rates = []
    try:
        oil = await fetch_oil_history()
    except Exception:
        oil = []
    try:
        gold = await fetch_gold_history()
    except Exception:
        gold = []
    review = build_macro_review(sp500_history=sp500, rates_history=rates, oil_history=oil, gold_history=gold)
    return await _enhance_review_narrative(review, view="macro", subject="宏观环境")


@app.get("/api/briefing/today", response_model=BriefingResponse)
async def briefing_today(symbols: str = "") -> BriefingResponse:
    """投研晨报：聚合宏观环境速判 + 组合风险速判，给买方晨会一页纸。

    复用同一份 github 行情（sp500/rates 供宏观与组合背景共用），整轮只拉一次。
    """
    from .github_data import (
        fetch_gold_history,
        fetch_oil_history,
        fetch_sp500_constituent,
        fetch_sp500_index_history,
        fetch_us10y_history,
    )
    from .risk_management import get_risk_summary

    sp500: list = []
    rates: list = []
    oil: list = []
    gold: list = []
    try:
        sp500 = await fetch_sp500_index_history()
    except Exception:
        sp500 = []
    try:
        rates = await fetch_us10y_history()
    except Exception:
        rates = []
    try:
        oil = await fetch_oil_history()
    except Exception:
        oil = []
    try:
        gold = await fetch_gold_history()
    except Exception:
        gold = []
    macro = build_macro_review(sp500_history=sp500, rates_history=rates, oil_history=oil, gold_history=gold)
    portfolio = build_portfolio_review(get_risk_summary(), sp500_history=sp500, rates_history=rates)
    briefing = build_briefing(macro, portfolio)

    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()][:8]
    if syms:
        symbol_sectors: dict = {}
        for sym in syms:
            try:
                c = await fetch_sp500_constituent(sym)
                symbol_sectors[sym] = (c or {}).get("sector")
            except Exception:
                symbol_sectors[sym] = None
        briefing.watchlist = build_watchlist_summary(symbol_sectors, macro.overall_verdict)
    return await _enhance_briefing_headline(briefing)


@app.get("/api/stock/compare", response_model=StockCompareResponse)
async def stock_compare(symbols: str = "", caps: str = "") -> StockCompareResponse:
    """多标的横向对比：复用速判引擎做逐维度信号灯矩阵，共享同一份 github 市场/利率数据。

    个股实时行情受限时（动量/期权/催化）相关维度诚实显示数据不足；
    规模（market_cap）/行业（github 成分）/市场背景维度正常区分。
    """
    from .github_data import (
        fetch_sp500_constituent,
        fetch_sp500_index_history,
        fetch_us10y_history,
    )
    from .google_finance import fetch_google_finance_quote

    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()][:6]
    cap_list = [c.strip() for c in caps.split(",")] if caps else []
    if not syms:
        return StockCompareResponse(generated_at=datetime.now(timezone.utc), items=[])

    sp500: list = []
    rates: list = []
    try:
        sp500 = await fetch_sp500_index_history()
    except Exception:
        sp500 = []
    try:
        rates = await fetch_us10y_history()
    except Exception:
        rates = []

    from .eastmoney_data import fetch_eastmoney_earnings
    from .nasdaq_data import fetch_nasdaq_earnings, fetch_nasdaq_options
    from .valuation_source import fetch_valuation

    async def _safe2(coro, default):
        try:
            return await coro
        except Exception:
            return default

    async def _compare_one(i: int, sym: str) -> StockCompareItem:
        cap = None
        if i < len(cap_list) and cap_list[i]:
            try:
                cap = float(cap_list[i])
            except ValueError:
                cap = None
        # 每标的多源并行抓取（含 valuation，让 scale/valuation 与单卡一致 live）；标的之间也并行。
        constituent, g, valuation_data, neps, nopts, efin = await asyncio.gather(
            _safe2(fetch_sp500_constituent(sym), None),
            _safe2(fetch_google_finance_quote(sym), None),
            _safe2(fetch_valuation(sym), None),
            _safe2(fetch_nasdaq_earnings(sym), None),
            _safe2(fetch_nasdaq_options(sym), None),
            _safe2(fetch_eastmoney_earnings(sym), None),
        )
        quote = _market_quote_from_google(g) if g else None
        gname = g.get("name") if g else None
        eff_cap = cap or (valuation_data or {}).get("market_cap") or (g.get("market_cap") if g else None)
        ts = build_tear_sheet(
            symbol=sym,
            name=gname or sym,
            market_cap=eff_cap,
            quote=quote,
            market_index_history=sp500,
            rates_history=rates,
            constituent=constituent,
            valuation=g,
            valuation_data=valuation_data,
            nasdaq_eps=neps,
            nasdaq_opts=nopts,
            eastmoney_fin=efin,
        )
        # 对比矩阵聚焦核心维度，个股深度维度（一致预期/资金面）不进 PK 列。
        dims = [d for d in ts.dimensions if d.key not in ("consensus", "fund_flow")]
        return StockCompareItem(
            symbol=sym,
            name=gname or sym,
            overall_verdict=ts.overall_verdict,
            overall_score=ts.overall_score,
            sector=(constituent or {}).get("sector"),
            market_cap=eff_cap,
            dimensions=dims,
            data_quality=ts.data_quality,
        )

    items = list(await asyncio.gather(*[_compare_one(i, sym) for i, sym in enumerate(syms)]))

    levels = [it.data_quality.level for it in items]
    worst = "mock" if "mock" in levels else ("degraded" if "degraded" in levels else "live")
    overall_dq = next((it.data_quality for it in items if it.data_quality.level == worst), items[0].data_quality)
    return StockCompareResponse(generated_at=datetime.now(timezone.utc), items=items, data_quality=overall_dq)


_SCREEN_DEFAULT = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "TSLA", "META", "AVGO"]
_SCREEN_DIM_LABELS = {
    "momentum": "价格动量", "catalyst": "盈利质量", "options": "期权情绪",
    "scale": "规模", "valuation": "估值", "consensus": "一致预期",
    "fund_flow": "资金面", "market": "市场环境", "macro": "宏观利率",
}


@app.get("/api/stock/screen", response_model=StockScreenResponse)
async def stock_screen(query: str, symbols: str = "") -> StockScreenResponse:
    """自然语言选股：LLM 解析需求 → 对候选逐一跑速判引擎 → 按维度信号筛选并按命中数排序。

    候选标的间 + 每标的多源 双层并行；verdict/维度信号仍由确定性引擎判定，LLM 只负责把
    自然语言意图翻译成筛选条件（不参与个股判定）。
    """
    q = (query or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="query 不能为空")
    import asyncio

    from .consensus_source import fetch_analyst_consensus
    from .eastmoney_data import fetch_eastmoney_earnings, fetch_fund_flow
    from .github_data import (
        fetch_sp500_constituent,
        fetch_sp500_index_history,
        fetch_us10y_history,
    )
    from .google_finance import fetch_google_finance_quote
    from .llm import CloudResearchLLM
    from .nasdaq_data import fetch_nasdaq_earnings, fetch_nasdaq_options
    from .valuation_source import fetch_valuation

    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()][:10] or _SCREEN_DEFAULT

    llm = CloudResearchLLM()
    parsed = None
    if llm.provider != "mock":
        try:
            parsed = await llm.parse_screen_query(q)
        except Exception:
            parsed = None
    if not parsed or not parsed.get("criteria"):
        return StockScreenResponse(
            generated_at=datetime.now(timezone.utc),
            query=q,
            criteria_summary="未能解析筛选条件（请在设置配置 AI 模型，或换更明确的表述）",
            provider="rule-template",
        )
    criteria = [
        StockScreenCriterion(dim=c["dim"], want=c.get("want", "bullish"), label=_SCREEN_DIM_LABELS[c["dim"]])
        for c in parsed["criteria"]
        if isinstance(c, dict) and c.get("dim") in _SCREEN_DIM_LABELS and c.get("want") in ("bullish", "bearish", "neutral")
    ]
    if not criteria:
        return StockScreenResponse(
            generated_at=datetime.now(timezone.utc),
            query=q,
            criteria_summary=parsed.get("summary", ""),
            provider=llm.provider,
        )

    async def _safe(coro, default):
        try:
            return await coro
        except Exception:
            return default

    sp500 = await _safe(fetch_sp500_index_history(), [])
    rates = await _safe(fetch_us10y_history(), [])

    async def _screen_one(sym: str) -> StockScreenMatch:
        constituent, g, valuation_data, neps, nopts, efin, cons, flow = await asyncio.gather(
            _safe(fetch_sp500_constituent(sym), None),
            _safe(fetch_google_finance_quote(sym), None),
            _safe(fetch_valuation(sym), None),
            _safe(fetch_nasdaq_earnings(sym), None),
            _safe(fetch_nasdaq_options(sym), None),
            _safe(fetch_eastmoney_earnings(sym), None),
            _safe(fetch_analyst_consensus(sym), None),
            _safe(fetch_fund_flow(sym), None),
        )
        quote = _market_quote_from_google(g) if g else None
        gname = g.get("name") if g else None
        eff_cap = (valuation_data or {}).get("market_cap") or (g.get("market_cap") if g else None)
        ts = build_tear_sheet(
            symbol=sym,
            name=gname or sym,
            market_cap=eff_cap,
            quote=quote,
            market_index_history=sp500,
            rates_history=rates,
            constituent=constituent,
            valuation=g,
            valuation_data=valuation_data,
            consensus_data=cons,
            fund_flow_data=flow,
            nasdaq_eps=neps,
            nasdaq_opts=nopts,
            eastmoney_fin=efin,
        )
        dim_by_key = {d.key: d for d in ts.dimensions}
        hit_labels: list = []
        miss_labels: list = []
        for c in criteria:
            dim = dim_by_key.get(c.dim)
            if c.dim == "scale":
                # 规模维度 signal 恒中性，"大盘"按 headline 判定（超大盘/大盘）
                ok = bool(dim and c.want == "bullish" and "大盘" in dim.headline)
            else:
                ok = bool(dim and dim.signal == c.want)
            (hit_labels if ok else miss_labels).append(c.label)
        return StockScreenMatch(
            symbol=sym,
            name=gname or sym,
            overall_verdict=ts.overall_verdict,
            overall_score=ts.overall_score,
            matched_all=(len(hit_labels) == len(criteria)),
            hit_count=len(hit_labels),
            hit_labels=hit_labels,
            miss_labels=miss_labels,
            data_quality=ts.data_quality,
        )

    results = list(await asyncio.gather(*[_screen_one(s) for s in syms]))
    results.sort(key=lambda m: (m.matched_all, m.hit_count, m.overall_score), reverse=True)
    levels = [r.data_quality.level for r in results]
    worst = "mock" if "mock" in levels else ("degraded" if "degraded" in levels else "live")
    overall_dq = next(r.data_quality for r in results if r.data_quality.level == worst)

    return StockScreenResponse(
        generated_at=datetime.now(timezone.utc),
        query=q,
        criteria_summary=parsed.get("summary", ""),
        criteria=criteria,
        matches=results,
        scanned=len(results),
        provider=llm.provider,
        data_quality=overall_dq,
    )


@app.get("/api/official-news/cctv", response_model=OfficialNewsResponse)
async def official_cctv_news(
    source: str = "xinwenlianbo",
    limit: int = 30,
    refresh: bool = False,
) -> OfficialNewsResponse:
    return await fetch_official_news(source=source, limit=limit, refresh=refresh)


@app.get("/api/people/spotlight", response_model=PeopleSpotlightResponse)
async def people_spotlight(refresh: bool = False) -> PeopleSpotlightResponse:
    """人物专题：焦点人物头像墙 + 各自近期发言 / 观点 / 来源。"""
    return await fetch_people_spotlight(refresh=refresh)


@app.get("/api/people/{figure_id}/digest", response_model=PersonDigestResponse)
async def people_digest(figure_id: str, refresh: bool = False) -> PersonDigestResponse:
    """单人物 AI 近期观点综述：把近期发言合成 2-3 句，方向不可编造，失败回退确定性模板。"""
    if figure_id not in FIGURES_BY_ID:
        allowed = " / ".join(FIGURES_BY_ID)
        raise HTTPException(
            status_code=404,
            detail=f"未知焦点人物：{figure_id}，可选 {allowed}",
        )
    profile = await fetch_person_voices(figure_id, refresh=refresh)
    digest, provider = await _synthesize_person_digest(profile)
    quality = profile.data_quality
    if profile.item_count and provider in {"template", "fallback"}:
        # 有真实条目但只能用确定性模板综述时，标降级而非 live。
        quality = classify_data_quality("template")
    return PersonDigestResponse(
        id=profile.id,
        name=profile.name,
        digest=digest,
        digest_provider=provider,
        item_count=profile.item_count,
        generated_at=datetime.now(timezone.utc).isoformat(),
        data_quality=quality,
    )


async def _synthesize_person_digest(profile: PersonProfile) -> tuple[str, str]:
    """把人物近期发言合成一段中性观点综述：LLM 优先，mock/失败回退确定性模板。"""
    headlines = [item.title for item in profile.items[:8] if item.title]
    if not headlines:
        return ("近期暂无可聚合的公开报道，请稍后刷新或查看下方原始条目。", "template")

    fallback = _template_person_digest(profile, headlines)
    llm = CloudResearchLLM()
    if llm.provider == "mock":
        return (fallback, "template")

    bullets = "\n".join(f"- {title}" for title in headlines)
    prompt = (
        f"你是财经媒体编辑。下面是关于「{profile.name}（{profile.role}，{profile.org}）」"
        f"近期的公开报道标题，请据此客观归纳其近期关注焦点与公开观点，供投研参考。\n\n"
        f"近期报道：\n{bullets}\n\n"
        "要求：用中文写 2-3 句综述，只能基于上面出现的事实归纳、不得编造数字或未提及的表态，"
        "点出 1-2 个对市场可能的影响方向，不写免责声明、不超过 120 个中文字。"
        '仅返回 JSON：{"digest": "..."}'
    )
    try:
        data = await llm.complete_json(
            prompt,
            max_tokens=600,
            timeout_seconds=14,
            force_json_first=True,
            retry_schema_hint="只需填充 digest 一个字段，2-3 句、不超过 120 字。",
        )
    except Exception:
        return (fallback, "template")
    digest = (data or {}).get("digest")
    if isinstance(digest, str) and digest.strip():
        return (digest.strip(), llm.provider_name)
    return (fallback, "template")


def _template_person_digest(profile: PersonProfile, headlines: list[str]) -> str:
    """确定性兜底综述：从近期条目的主题标签 + 最新一条提炼，不依赖云端模型。"""
    tag_counts: dict[str, int] = {}
    for item in profile.items:
        for tag in item.tags:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    top_tags = [tag for tag, _ in sorted(tag_counts.items(), key=lambda kv: kv[1], reverse=True)[:3]]
    focus = "、".join(top_tags) if top_tags else "多条公开动态"
    latest = headlines[0] if headlines else ""
    return (
        f"近{profile.item_count}条公开报道显示，{profile.name}近期焦点集中在{focus}。"
        f"最新一条：{latest}。{profile.why_it_matters}"
    )


@app.get("/api/ai-supply-chain/capacity-trends")
async def ai_supply_chain_capacity_trends(horizon: str = "3m") -> dict[str, Any]:
    return await fetch_ai_supply_chain_capacity_trends(horizon=horizon)


@app.get("/api/customs-trade/snapshot")
async def customs_trade_snapshot() -> dict[str, Any]:
    return await fetch_customs_trade_snapshot()


@app.get("/api/customs-trade/hs-detail/search")
async def customs_trade_hs_detail_search(q: str = "", limit: int = 20) -> dict[str, Any]:
    return await search_customs_hs_detail_products(q, limit=limit)


@app.get("/api/customs-trade/hs-detail")
async def customs_trade_hs_detail(
    query: Optional[str] = None,
    code: Optional[str] = None,
    months: int = 12,
) -> dict[str, Any]:
    return await fetch_customs_hs_detail_snapshot(query=query, code=code, months=months)


async def _wait_for_customs_agent_task(task_id: str, *, timeout_seconds: float) -> InvestmentTaskRecord:
    deadline = datetime.now(timezone.utc).timestamp() + timeout_seconds
    while datetime.now(timezone.utc).timestamp() < deadline:
        task = get_investment_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Agent task not found")
        if task.status == "completed":
            return task
        if task.status in {"failed", "cancelled"}:
            detail = task.error or (task.logs[-1].message if task.logs else "Agent task failed")
            raise HTTPException(status_code=502, detail=detail)
        await asyncio.sleep(1.0)
    raise HTTPException(status_code=504, detail=f"海关投研任务 {task_id} 尚未在限定时间内完成，请到投研任务中心查看进度。")


def _customs_agent_task_to_fingpt(task: InvestmentTaskRecord) -> FinGptTaskResponse:
    result = task.result or {}
    findings = result.get("agent_findings") if isinstance(result.get("agent_findings"), dict) else {}
    evidence_sources = [
        str(item.get("title") or item.get("source"))
        for item in result.get("evidence", [])
        if isinstance(item, dict) and (item.get("title") or item.get("source"))
    ]
    sources = [
        f"DeepFocus Agent Task {task.id}",
        *evidence_sources,
    ]
    return FinGptTaskResponse(
        provider=llm.provider_name,
        model=llm.model,
        generated_at=datetime.now(timezone.utc),
        capability="customs_trade_agent_runtime",
        title="中国海关进出口投研Agent",
        summary=str(result.get("investor_summary") or result.get("plain_language_takeaway") or "海关投研 Agent 已完成。"),
        key_points=_json_list(findings.get("research")) or _json_list(result.get("watchlist"))[:6],
        signals=_json_list(findings.get("report")) or _json_list(result.get("action_plan"))[:6],
        risks=_json_list(result.get("risk_controls")) or _json_list(findings.get("risk")),
        actions=_json_list(result.get("action_plan")) or _json_list(findings.get("report")),
        sources=sources[:8],
        confidence=clamp(float(result.get("confidence") or 0.6), 0, 1),
        disclaimer=str(result.get("disclaimer") or "仅供投研和运营参考，不构成投资建议、支付建议或合规结论。"),
    )


@app.post("/api/customs-trade/ai-analysis", response_model=FinGptTaskResponse)
async def customs_trade_ai_analysis(request: CustomsTradeAnalysisRequest) -> FinGptTaskResponse:
    try:
        tab_labels = {
            "chapters": "HS2商品结构",
            "exports": "重点进出口商品",
            "partners": "贸易伙伴结构",
            "hs": "HS大类结构",
            "fine": "HS明细商品",
        }
        focus = request.focus or tab_labels.get(request.selected_tab or "", request.selected_tab) or "全局海关进出口快照"
        if not is_worker_running():
            await start_agent_worker()
        task = create_investment_task(
            InvestmentTaskCreateRequest(
                title="中国海关进出口投研Agent分析",
                symbol="CUSTOMS_CN",
                asset_name="中国海关进出口",
                task_type="customs_trade_analysis",
                engine="deepfocus",
                horizon="最近12个月",
                investor_profile="专业",
                objective=f"基于中国海关进出口官方数据生成投资建议、代表股票、风险和触发条件。当前焦点：{focus}",
                context="海关总署进出口月度快照、HS2商品、重点进出口商品、贸易伙伴和近12个月曲线。",
                analysis_domain="customs_trade",
                customs_focus=focus,
                customs_focus_key=request.focus_key,
                customs_focus_type=request.focus_type,
                customs_selected_tab=request.selected_tab,
                engine_config={
                    "source": "customs_trade_module",
                    "focus_type": request.focus_type,
                },
                priority=1,
            )
        )
        completed = await _wait_for_customs_agent_task(task.id, timeout_seconds=95)
        return _customs_agent_task_to_fingpt(completed)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/skills/shareholder-changes/scan", response_model=ShareholderChangeScanResponse)
async def shareholder_change_scan(request: ShareholderChangeScanRequest) -> ShareholderChangeScanResponse:
    return await scan_shareholder_changes(request)


@app.post("/api/skills/shareholder-changes/interpret", response_model=ShareholderChangeInterpretResponse)
async def shareholder_change_interpret(request: ShareholderChangeInterpretRequest) -> ShareholderChangeInterpretResponse:
    return await interpret_shareholder_change(request, llm)


@app.post("/api/skills/cn-earnings/scan", response_model=CnEarningsScanResponse)
async def cn_earnings_scan(request: CnEarningsScanRequest) -> CnEarningsScanResponse:
    return await scan_cn_earnings(request)


@app.post("/api/skills/cn-earnings/diagnose", response_model=CnEarningsDiagnosisResponse)
async def cn_earnings_diagnose(request: CnEarningsDiagnosisRequest) -> CnEarningsDiagnosisResponse:
    return await diagnose_cn_earnings(request, llm)


@app.post("/api/skills/cn-earnings/detail", response_model=CnEarningsRecordDetailResponse)
async def cn_earnings_record_detail(request: CnEarningsRecordDetailRequest) -> CnEarningsRecordDetailResponse:
    return await enrich_cn_earnings_record_detail(request)


@app.post("/api/skills/major-events/scan", response_model=MajorEventScanResponse)
async def major_event_scan(request: MajorEventScanRequest) -> MajorEventScanResponse:
    return await scan_major_events(request)


@app.post("/api/fingpt/files/extract", response_model=FileExtractionResponse)
async def extract_file(file: UploadFile = File(...)) -> FileExtractionResponse:
    return await extract_upload_file(file)


@app.get("/api/data-sources", response_model=DataSourceListResponse)
async def api_list_data_sources() -> DataSourceListResponse:
    return DataSourceListResponse(sources=list_data_sources())


@app.post("/api/data-sources", response_model=DataSourceRecord)
async def api_create_data_source(request: DataSourceCreateRequest) -> DataSourceRecord:
    return create_data_source(request)


@app.get("/api/data-sources/module-refs", response_model=DataSourceModuleRefListResponse)
async def api_list_data_source_module_refs(
    source_id: Optional[str] = None,
    module: Optional[str] = None,
) -> DataSourceModuleRefListResponse:
    return DataSourceModuleRefListResponse(refs=list_data_source_module_refs(source_id=source_id, module=module))


@app.post("/api/data-sources/module-refs", response_model=DataSourceModuleRefRecord)
async def api_save_data_source_module_ref(request: DataSourceModuleRefCreateRequest) -> DataSourceModuleRefRecord:
    return save_data_source_module_ref(request)


@app.delete("/api/data-sources/module-refs/{ref_id}")
async def api_delete_data_source_module_ref(ref_id: str) -> dict[str, bool]:
    if not delete_data_source_module_ref(ref_id):
        raise HTTPException(status_code=404, detail="Data source reference not found")
    return {"ok": True}


@app.delete("/api/data-sources/{source_id}")
async def api_delete_data_source(source_id: str) -> dict[str, bool]:
    if not delete_data_source(source_id):
        raise HTTPException(status_code=404, detail="Data source not found")
    return {"ok": True}


@app.post("/api/data-sources/{source_id}/sync", response_model=DataSourceSyncResponse)
async def api_sync_data_source(source_id: str, request: DataSourceSyncRequest) -> DataSourceSyncResponse:
    source, items = await sync_data_source(source_id, request)
    publish_data_source_items(items, topic="data-source-sync")
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


@app.get("/api/data-sources/corpus-stats", response_model=DataSourceCorpusStats)
async def api_corpus_stats() -> DataSourceCorpusStats:
    """证据语料的质量与覆盖聚合（全量扫描）——证据库「语料质量与覆盖」概览。"""
    return corpus_stats()


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
    _reject_non_ingestible_file(file.filename or "")
    extracted = await extract_upload_file(file)
    parsed_tags = [tag.strip() for tag in (tags or "").split(",") if tag.strip()]
    item = store_upload_item(
        filename=extracted.filename,
        text=extracted.text,
        parser=extracted.parser,
        content_type=extracted.content_type,
        symbol=symbol,
        title=title,
        tags=parsed_tags,
    )
    publish_data_source_items([item], topic="data-source-upload", severity="success")
    return item


@app.post("/api/pro-research/reports/upload", response_model=ProfessionalReportRecord)
async def api_upload_professional_report(
    file: UploadFile = File(...),
    symbol: Optional[str] = Form(None),
    title: Optional[str] = Form(None),
    report_type: str = Form("other"),
    period: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
) -> ProfessionalReportRecord:
    _reject_non_ingestible_file(file.filename or "")
    extracted = await extract_upload_file(file)
    parsed_tags = [tag.strip() for tag in (tags or "").split(",") if tag.strip()]
    item = store_upload_item(
        filename=extracted.filename,
        text=extracted.text,
        parser=extracted.parser,
        content_type=extracted.content_type,
        symbol=symbol,
        title=title,
        tags=[*parsed_tags, "专业财报库"],
    )
    publish_data_source_items([item], topic="pro-research-upload", severity="success")
    return ingest_professional_report_text(
        text=extracted.text,
        title=title or extracted.filename,
        symbol=symbol,
        report_type=report_type,
        period=period,
        source_item_id=item.id,
        parser=extracted.parser,
        metadata={
            "filename": extracted.filename,
            "content_type": extracted.content_type,
            "data_item_id": item.id,
            "tags": [*parsed_tags, "专业财报库"],
        },
    )


@app.post("/api/pro-research/reports/ingest-item", response_model=ProfessionalReportRecord)
async def api_ingest_professional_report_item(
    request: ProfessionalReportIngestRequest,
) -> ProfessionalReportRecord:
    return ingest_professional_report_from_item(request)


@app.post("/api/pro-research/reports/ingest-url", response_model=ProfessionalReportRecord)
async def api_ingest_professional_report_url(
    request: ProfessionalReportUrlIngestRequest,
) -> ProfessionalReportRecord:
    extracted = await extract_report_url(request.url)
    tags = list(dict.fromkeys([*request.tags, "URL入库", "专业财报库"]))
    title = request.title or extracted.title
    item = store_upload_item(
        filename=extracted.filename,
        text=extracted.text,
        parser=extracted.parser,
        content_type=extracted.content_type,
        symbol=request.symbol,
        title=title,
        tags=tags,
        url=extracted.final_url,
        metadata={
            "source_url": extracted.url,
            "final_url": extracted.final_url,
            "parser": extracted.parser,
            "truncated": extracted.truncated,
            "tags": tags,
        },
    )
    publish_data_source_items([item], topic="pro-research-url-ingest", severity="success")
    return ingest_professional_report_text(
        text=extracted.text,
        title=title,
        symbol=request.symbol,
        report_type=request.report_type,
        period=request.period,
        source_item_id=item.id,
        parser=extracted.parser,
        metadata={
            "filename": extracted.filename,
            "content_type": extracted.content_type,
            "source_url": extracted.url,
            "final_url": extracted.final_url,
            "data_item_id": item.id,
            "tags": tags,
            "truncated": extracted.truncated,
        },
    )


_JUNK_FILENAMES = {".ds_store", "thumbs.db", "desktop.ini", ".localized"}
_INGESTIBLE_SUFFIXES = {
    ".pdf", ".docx", ".doc", ".txt", ".md", ".markdown",
    ".html", ".htm", ".csv", ".xlsx", ".xls", ".pptx", ".rtf", ".json",
}


def _reject_non_ingestible_file(filename: str) -> None:
    """拒绝系统/隐藏/垃圾文件与不支持的类型，避免 .DS_Store 之类被切块入 RAG。"""
    base = Path((filename or "").strip()).name
    if not base or base.lower() in _JUNK_FILENAMES or base.startswith("."):
        raise HTTPException(status_code=422, detail=f"该文件疑似系统/隐藏文件（{base or '空文件名'}），不是可入库的研报。")
    suffix = Path(base).suffix.lower()
    if suffix and suffix not in _INGESTIBLE_SUFFIXES:
        raise HTTPException(status_code=422, detail=f"暂不支持的研报文件类型：{suffix}。支持 PDF/Word/Excel/PPT/TXT/Markdown/HTML/CSV。")


@app.post("/api/pro-research/reports/ingest-workbench-file", response_model=ProfessionalReportRecord)
async def api_ingest_professional_workbench_file(
    request: ProfessionalWorkbenchFileIngestRequest,
) -> ProfessionalReportRecord:
    _reject_non_ingestible_file(request.filename)
    file_path = _safe_workbench_file_path(request.out, request.filename)
    extracted = extract_local_file(file_path)
    parsed_tags = [tag.strip() for tag in request.tags if tag.strip()]
    tags = [*parsed_tags, "抓取舱", "专业财报库"]
    item = store_upload_item(
        filename=extracted.filename,
        text=extracted.text,
        parser=extracted.parser,
        content_type=extracted.content_type,
        symbol=request.symbol,
        title=request.title or file_path.stem,
        tags=tags,
    )
    publish_data_source_items([item], topic="pro-research-workbench-ingest", severity="success")
    return ingest_professional_report_text(
        text=extracted.text,
        title=request.title or file_path.stem,
        symbol=request.symbol,
        report_type=request.report_type,
        period=request.period,
        source_item_id=item.id,
        parser=extracted.parser,
        metadata={
            "filename": extracted.filename,
            "content_type": extracted.content_type,
            "workbench_out": request.out,
            "workbench_path": str(file_path),
            "data_item_id": item.id,
            "tags": tags,
        },
    )


@app.get("/api/pro-research/reports", response_model=ProfessionalReportListResponse)
async def api_list_professional_reports(
    symbol: Optional[str] = None,
    limit: int = 50,
) -> ProfessionalReportListResponse:
    return ProfessionalReportListResponse(reports=list_professional_reports(symbol=symbol, limit=limit))


@app.get("/api/pro-research/reports/{report_id}", response_model=ProfessionalReportRecord)
async def api_get_professional_report(report_id: str) -> ProfessionalReportRecord:
    report = get_professional_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Professional report not found")
    return report


@app.get("/api/pro-research/reports/{report_id}/chunks", response_model=ProfessionalReportChunkListResponse)
async def api_list_professional_report_chunks(
    report_id: str,
    limit: int = 100,
) -> ProfessionalReportChunkListResponse:
    if not get_professional_report(report_id):
        raise HTTPException(status_code=404, detail="Professional report not found")
    return ProfessionalReportChunkListResponse(chunks=list_professional_chunks(report_id, limit=limit))


@app.get("/api/pro-research/metrics", response_model=ProfessionalMetricListResponse)
async def api_list_professional_metrics(
    report_id: Optional[str] = None,
    symbol: Optional[str] = None,
    metric_key: Optional[str] = None,
    limit: int = 100,
) -> ProfessionalMetricListResponse:
    return ProfessionalMetricListResponse(
        metrics=list_professional_metrics(
            report_id=report_id,
            symbol=symbol,
            metric_key=metric_key,
            limit=limit,
        )
    )


@app.post("/api/pro-research/rag/query", response_model=ProfessionalRagQueryResponse)
async def api_professional_rag_query(
    request: ProfessionalRagQueryRequest,
) -> ProfessionalRagQueryResponse:
    return await query_professional_rag(request)


@app.post("/api/pro-research/reports/{report_id}/analyze", response_model=ProfessionalReportAnalysisResponse)
async def api_analyze_professional_report(
    report_id: str,
    request: ProfessionalReportAnalysisRequest = ProfessionalReportAnalysisRequest(),
) -> ProfessionalReportAnalysisResponse:
    return await analyze_professional_report(report_id, request)


@app.post("/api/pro-research/evals/run", response_model=ProfessionalEvalRunResponse)
async def api_run_professional_eval(
    request: ProfessionalEvalRunRequest,
) -> ProfessionalEvalRunResponse:
    return await run_professional_eval(request)


_RESEARCH_INFO_CODE_RE = re.compile(r"[A-Za-z0-9]{1,64}")
_RESEARCH_PDF_TIMEOUT = httpx.Timeout(20.0, connect=6.0)


@app.get("/api/research/search", response_model=ResearchReportSearchResponse)
async def api_research_search(
    keyword: str,
    market: Optional[str] = None,
    page_size: int = 20,
) -> ResearchReportSearchResponse:
    """研报融合检索（东方财富直连侧）：关键词→标的→A股/港股研报列表，附直链 PDF。

    美股无东财研报，返回空 + 警告，前端据此切到海外投行（知识星球）源。"""
    kw = (keyword or "").strip()
    fetched_at = datetime.now(timezone.utc).isoformat()
    if not kw:
        return ResearchReportSearchResponse(
            keyword=keyword, items=[], provider="none", fetched_at=fetched_at,
            warnings=["请输入公司、代码或主题"],
            data_quality=DataQuality(level="degraded", label="未检索", detail="缺少关键词"),
        )

    resolved = await search_market_symbols(kw, market=market)
    candidate = resolved.candidates[0] if resolved.candidates else None
    if not candidate:
        # 东财仅覆盖 A股/港股；美股及海外标的（如特斯拉）由海外投行源承接。
        return ResearchReportSearchResponse(
            keyword=keyword, items=[], provider="none", fetched_at=fetched_at,
            warnings=["东方财富(A股/港股)未匹配到该标的；海外/美股研报见下方「海外投行报告」结果"],
            data_quality=DataQuality(
                level="degraded", label="东财未命中",
                detail="未在 A股/港股 匹配到该标的，可改用海外投行源或更精确的代码",
                reasons=["eastmoney-no-match"],
            ),
        )

    resolved_market = candidate.market
    if resolved_market == "US":
        return ResearchReportSearchResponse(
            keyword=keyword, resolved_symbol=candidate.symbol, resolved_market=resolved_market,
            items=[], provider="eastmoney", fetched_at=fetched_at,
            warnings=[f"东方财富无{candidate.name}（美股）研报，请在海外投行报告源检索"],
            data_quality=DataQuality(
                level="degraded", label="东财无美股研报",
                detail="美股研报请使用海外投行（知识星球）源", reasons=["eastmoney-no-us"],
            ),
        )

    rows, query_warnings = await query_eastmoney_reports(
        code=candidate.code, market=resolved_market, page_size=page_size,
    )
    items = [
        ResearchReportItem(
            id=row["info_code"],
            title=row["title"],
            org=row["org"],
            date=row["date"],
            symbol=candidate.symbol,
            market=resolved_market,
            rating=row.get("rating") or None,
            stock_name=row.get("stock_name") or candidate.name,
            pdf_url=row["pdf_url"],
            preview_url=f"/api/research/pdf/{row['info_code']}",
        )
        for row in rows
    ]

    if items:
        data_quality = DataQuality(
            level="live", label="东方财富研报直连", detail=f"{candidate.name} · {len(items)} 篇",
        )
    else:
        data_quality = DataQuality(
            level="degraded", label="暂无研报", detail="该标的暂无东财研报", reasons=query_warnings,
        )

    return ResearchReportSearchResponse(
        keyword=keyword,
        resolved_symbol=candidate.symbol,
        resolved_market=resolved_market,
        items=items,
        provider="eastmoney" if items else "none",
        fetched_at=fetched_at,
        warnings=query_warnings,
        data_quality=data_quality,
    )


@app.get("/api/research/pdf/{info_code}")
async def api_research_pdf(info_code: str) -> StreamingResponse:
    """研报 PDF 在线预览代理（SSRF 安全：host 硬编码，仅放行 [A-Za-z0-9] 编号）。"""
    code = (info_code or "").strip()
    if not _RESEARCH_INFO_CODE_RE.fullmatch(code):
        raise HTTPException(status_code=422, detail="非法的研报编号")

    pdf_url = eastmoney_report_pdf_url(code)
    client = httpx.AsyncClient(trust_env=False, timeout=_RESEARCH_PDF_TIMEOUT, follow_redirects=True)
    upstream_request = client.build_request(
        "GET", pdf_url,
        headers={"Referer": "https://data.eastmoney.com/", "User-Agent": "Mozilla/5.0"},
    )
    try:
        upstream = await client.send(upstream_request, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(status_code=502, detail=f"研报 PDF 拉取失败：{exc}") from exc

    if upstream.status_code != 200:
        status = upstream.status_code
        await upstream.aclose()
        await client.aclose()
        raise HTTPException(status_code=404 if status == 404 else 502, detail=f"研报 PDF 不可用（HTTP {status}）")

    async def body_iter() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        body_iter(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{code}.pdf"',
            "Cache-Control": "public, max-age=3600",
        },
    )


async def _resolve_research_pdf_bytes(request: ResearchVisionAnalyzeRequest) -> bytes:
    """取研报 PDF 字节：优先本地抓取舱文件（海外投行），否则东财 PDF 直链。"""
    if request.workbench_filename:
        path = _safe_workbench_file_path(request.workbench_out, request.workbench_filename)
        return path.read_bytes()

    url = (request.pdf_url or "").strip()
    if not url:
        raise HTTPException(status_code=422, detail="缺少研报来源（workbench_filename 或 pdf_url）")
    host = (urlparse(url).hostname or "").lower()
    if host != "pdf.dfcfw.com":
        raise HTTPException(status_code=400, detail="仅支持东方财富研报 PDF 直链或抓取舱文件的视觉解读")
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=_RESEARCH_PDF_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(
                url, headers={"Referer": "https://data.eastmoney.com/", "User-Agent": "Mozilla/5.0"},
            )
            resp.raise_for_status()
            return resp.content
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"研报 PDF 拉取失败：{exc}") from exc


@app.post("/api/research/vision-analyze", response_model=ResearchVisionAnalysisResponse)
async def api_research_vision_analyze(
    request: ResearchVisionAnalyzeRequest,
) -> ResearchVisionAnalysisResponse:
    """图片型研报视觉解读：渲染前若干页→多模态模型读图出买方观点（无逐句溯源）。"""
    pdf_bytes = await _resolve_research_pdf_bytes(request)
    if not pdf_bytes:
        raise HTTPException(status_code=422, detail="未能获取研报 PDF 内容")

    title = (request.title or "研报").strip()
    try:
        result = await analyze_pdf_vision(
            pdf_bytes, title=title, symbol=request.symbol, max_pages=request.max_pages,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - 统一转成 502，前端给友好提示
        raise HTTPException(status_code=502, detail=f"视觉解读失败：{exc}") from exc

    return ResearchVisionAnalysisResponse(
        title=title,
        symbol=request.symbol,
        summary=result["summary"],
        key_points=result["key_points"],
        risks=result["risks"],
        rating=result["rating"],
        target_price=result["target_price"],
        confidence=result["confidence"],
        pages_analyzed=result["pages_analyzed"],
        provider=result["provider"],
        disclaimer=result["disclaimer"],
        data_quality=DataQuality(
            level="degraded", label="AI 视觉解读",
            detail="基于研报页面图像的解读，非逐句溯源", reasons=["vision-no-citation"],
        ),
    )


@app.post("/api/data-sources/agent-crawl", response_model=DataSourceSyncResponse)
async def api_agent_crawl(request: DataSourceSyncRequest) -> DataSourceSyncResponse:
    source, items = await capture_agent_web_pages(request)
    publish_data_source_items(items, topic="data-source-crawl")
    return DataSourceSyncResponse(source=source, imported_count=len(items), items=items)


@app.post("/api/data-sources/keyword-crawl", response_model=DataSourceKeywordCrawlResponse)
async def api_keyword_crawl(request: DataSourceKeywordCrawlRequest) -> DataSourceKeywordCrawlResponse:
    source, items, warnings, meta = await keyword_crawl_data_source(request)
    publish_data_source_items(items, topic="data-source-keyword")
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


@app.get("/api/realtime/messages", response_model=RealtimeMessageListResponse)
async def api_list_realtime_messages(
    symbol: Optional[str] = None,
    topic: Optional[str] = None,
    severity: Optional[str] = None,
    limit: int = 80,
) -> RealtimeMessageListResponse:
    return RealtimeMessageListResponse(
        messages=list_realtime_messages(
            symbol=symbol,
            topic=topic,
            severity=severity,
            limit=max(1, min(limit, 200)),
        )
    )


@app.post("/api/realtime/messages", response_model=RealtimeMessageRecord)
async def api_push_realtime_message(request: RealtimeMessageCreateRequest) -> RealtimeMessageRecord:
    return create_realtime_message(request)


@app.get("/api/realtime/messages/stream")
async def api_realtime_message_stream(request: Request) -> StreamingResponse:
    return StreamingResponse(
        realtime_message_event_stream(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/realtime/recall/subscriptions", response_model=RecallSubscriptionRecord)
async def api_create_recall_subscription(request: RecallSubscriptionCreateRequest) -> RecallSubscriptionRecord:
    return create_recall_subscription(request)


@app.get("/api/realtime/recall/subscriptions", response_model=RecallSubscriptionListResponse)
async def api_list_recall_subscriptions() -> RecallSubscriptionListResponse:
    return RecallSubscriptionListResponse(subscriptions=list_recall_subscriptions(active_only=False))


@app.delete("/api/realtime/recall/subscriptions/{subscription_id}")
async def api_delete_recall_subscription(subscription_id: str) -> dict[str, bool]:
    return {"deleted": delete_recall_subscription(subscription_id)}


@app.get("/api/realtime/recall/deliveries", response_model=list[RecallDeliveryResult])
async def api_recent_recall_deliveries() -> list[RecallDeliveryResult]:
    return recent_deliveries()


@app.get("/api/realtime/recall/delivery-log", response_model=RecallDeliveryLogResponse)
async def api_recall_delivery_log(limit: int = 50) -> RecallDeliveryLogResponse:
    return RecallDeliveryLogResponse(deliveries=list_deliveries(limit))


@app.get("/api/realtime/recall/metrics", response_model=RecallMetricsResponse)
async def api_recall_metrics() -> RecallMetricsResponse:
    """召回闭环验真：送达 / 点击回流 / 回流率。"""
    return recall_metrics()


@app.get("/api/realtime/recall/click/{delivery_id}", include_in_schema=False)
async def api_recall_click(delivery_id: str) -> RedirectResponse:
    """召回点击回流追踪：记录点击并 302 跳回 App 深链（公开端点，用户从邮件/推送点回）。"""
    target = mark_recall_click(delivery_id)
    # 未知 delivery_id 也不报错——回流体验优先，兜底跳回 App 首页。
    fallback = os.getenv("DEEPFOCUS_APP_BASE_URL", "http://localhost:3000").strip().rstrip("/") or "http://localhost:3000"
    return RedirectResponse(url=target or fallback, status_code=302)


@app.post("/api/share/snapshots", response_model=ShareSnapshotRecord)
async def api_create_share_snapshot(request: ShareSnapshotCreateRequest) -> ShareSnapshotRecord:
    return create_share_snapshot(request)


@app.get("/api/share/snapshots/{snapshot_id}", response_model=ShareSnapshotRecord)
async def api_get_share_snapshot(snapshot_id: str) -> ShareSnapshotRecord:
    record = get_share_snapshot(snapshot_id)
    if record is None:
        raise HTTPException(status_code=404, detail="分享快照不存在")
    return record


@app.get("/s/{snapshot_id}", response_class=HTMLResponse)
async def public_share_page(snapshot_id: str, request: Request) -> HTMLResponse:
    """免登录、可被搜索引擎收录的只读结论页（服务端直出 HTML）。"""
    record = get_share_snapshot(snapshot_id)
    if record is None:
        return HTMLResponse(render_not_found_html(), status_code=404)
    increment_share_views(snapshot_id)
    return HTMLResponse(render_share_page_html(record, page_url=str(request.url)))


@app.get("/api/mcp/servers", response_model=McpServerListResponse)
async def api_list_mcp_servers() -> McpServerListResponse:
    return McpServerListResponse(servers=list_mcp_servers())


@app.post("/api/mcp/servers", response_model=McpServerRecord)
async def api_create_mcp_server(request: McpServerCreateRequest) -> McpServerRecord:
    return create_mcp_server(request)


@app.delete("/api/mcp/servers/{server_id}")
async def api_delete_mcp_server(server_id: str) -> dict[str, bool]:
    if not delete_mcp_server(server_id):
        raise HTTPException(status_code=404, detail="MCP server not found")
    return {"ok": True}


@app.post("/api/mcp/servers/{server_id}/discover", response_model=McpDiscoverResponse)
async def api_discover_mcp_server(server_id: str) -> McpDiscoverResponse:
    server, capabilities, warnings = await discover_mcp_server(server_id)
    return McpDiscoverResponse(server=server, capabilities=capabilities, warnings=warnings)


@app.get("/api/mcp/capabilities", response_model=McpCapabilityListResponse)
async def api_list_mcp_capabilities(
    server_id: Optional[str] = None,
    capability_type: Optional[str] = None,
) -> McpCapabilityListResponse:
    return McpCapabilityListResponse(
        capabilities=list_mcp_capabilities(server_id=server_id, capability_type=capability_type)
    )


@app.post("/api/mcp/servers/{server_id}/tools/call", response_model=McpToolCallResponse)
async def api_call_mcp_tool(server_id: str, request: McpToolCallRequest) -> McpToolCallResponse:
    return await call_mcp_tool(server_id, request)


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
    score = int(round(clamp(_number_value(data.get("score"), 50), 0, 100)))
    confidence = clamp(_number_value(data.get("confidence"), 0.6), 0, 1)
    label = str(data.get("sentiment_label") or "neutral").lower()
    if label not in {"positive", "neutral", "negative"}:
        label = "neutral"
    sentiment = SentimentResponse(
        provider=llm.provider_name,
        model=llm.model,
        label=label,
        score=clamp(_number_value(data.get("sentiment_score"), 0), -1, 1),
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
            confidence=clamp(_number_value(section.get("confidence"), confidence), 0, 1),
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
    return int(round(clamp(score, 0, 100)))


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


@app.get("/api/agents/engines", response_model=AgentEngineListResponse)
async def list_agent_engines() -> AgentEngineListResponse:
    """自描述：注册表里有什么引擎，API 就返回什么。新增引擎自动出现，前端无需硬编码。"""
    return AgentEngineListResponse(
        default=DEFAULT_ENGINE_KEY,
        engines=[
            AgentEngineInfo(
                key=spec.key,
                label=spec.label,
                description=spec.description,
                stage_count=len(spec.stages),
            )
            for spec in list_engines()
        ],
    )


@app.get("/api/agents/tools", response_model=AgentToolListResponse)
async def list_agent_tools_endpoint() -> AgentToolListResponse:
    """自描述：tool-use agent 能调用的工具清单（内部数据工具 + 已接入的外部 MCP 工具）+ 该路径是否启用。"""
    tools = list(list_agent_tool_specs())
    try:
        tools = tools + await discover_mcp_agent_tools()  # best-effort：MCP 不可用不影响内部工具列示
    except Exception:
        pass
    return AgentToolListResponse(
        enabled=_tool_agent_enabled(),
        tools=[
            AgentToolInfo(
                name=tool.name,
                description=tool.description,
                parameters=list((tool.parameters.get("properties") or {}).keys()),
            )
            for tool in tools
        ],
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


@app.get("/api/agents/tasks/{task_id}/events")
async def stream_agent_task_events(task_id: str, request: Request) -> StreamingResponse:
    return StreamingResponse(
        agent_task_event_stream(task_id, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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


@app.get("/api/dulus/status", response_model=DulusRuntimeStatusResponse)
async def dulus_status() -> DulusRuntimeStatusResponse:
    return build_dulus_runtime_status(llm)


@app.get("/api/dulus/tools", response_model=list[DulusToolRecord])
async def dulus_tools() -> list[DulusToolRecord]:
    return list_dulus_tools()


@app.get("/api/dulus/memory", response_model=DulusMemoryListResponse)
async def dulus_memory(limit: int = 20, scope: Optional[str] = None) -> DulusMemoryListResponse:
    return list_dulus_memories(limit=limit, scope=scope)


@app.post("/api/dulus/memory", response_model=DulusMemoryRecord)
async def dulus_memory_create(request: DulusMemoryCreateRequest) -> DulusMemoryRecord:
    return create_dulus_memory(request)


@app.post("/api/dulus/webbridge/inspect", response_model=DulusWebBridgeInspectResponse)
async def dulus_webbridge_inspect(request: DulusWebBridgeInspectRequest) -> DulusWebBridgeInspectResponse:
    return inspect_authorized_webbridge(request)


@app.post("/api/dulus/roundtable", response_model=DulusRoundtableResponse)
async def dulus_roundtable(request: DulusRoundtableRequest) -> DulusRoundtableResponse:
    try:
        return await run_dulus_roundtable(llm, request)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/ai/stock-analysis", response_model=StockAnalysisResponse)
async def stock_analysis(request: StockAnalysisRequest) -> StockAnalysisResponse:
    try:
        return attach_data_quality(await llm.analyze_stock(request))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/ai/sentiment", response_model=SentimentResponse)
async def sentiment(request: SentimentRequest) -> SentimentResponse:
    try:
        return attach_data_quality(await llm.score_sentiment(request.text))
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
    confidence = clamp(
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
        return attach_data_quality(await llm.summarize_news(request))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/fingpt/report-analysis", response_model=FinGptTaskResponse)
async def report_analysis(request: ReportAnalysisRequest) -> FinGptTaskResponse:
    try:
        return attach_data_quality(await llm.analyze_report(request))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/fingpt/rag-query", response_model=FinGptTaskResponse)
async def rag_query(request: RagQueryRequest) -> FinGptTaskResponse:
    try:
        return attach_data_quality(await llm.rag_query(request))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/fingpt/forecast", response_model=FinGptTaskResponse)
async def forecast(request: ForecastRequest) -> FinGptTaskResponse:
    try:
        return attach_data_quality(await llm.forecast(request))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/fingpt/corridor-risk", response_model=FinGptTaskResponse)
async def corridor_risk(request: CorridorRiskRequest) -> FinGptTaskResponse:
    try:
        return attach_data_quality(await llm.corridor_risk(request))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/fingpt/agent-brief", response_model=FinGptTaskResponse)
async def agent_brief(request: AgentBriefRequest) -> FinGptTaskResponse:
    try:
        return attach_data_quality(await llm.agent_brief(request))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _crawl_keyword_for_context(ctx: dict) -> Optional[str]:
    """取一个适合搜索的关键词：优先聚焦标的的中文名（自选股里），否则用代码。"""
    symbol = str(ctx.get("focused_symbol") or "").strip()
    watchlist = ctx.get("watchlist")
    if symbol and isinstance(watchlist, list):
        for s in watchlist:
            if isinstance(s, dict) and str(s.get("symbol", "")).upper() == symbol.upper():
                name = str(s.get("name", "") or "").strip()
                if name:
                    return name
    return symbol or None


async def _acquire_fresh_evidence_if_thin(request: GeneralChatRequest) -> int:
    """主动取数（agentic）：挂了证据库且本地强相关证据稀薄时，自动爬一轮补进证据库，
    让 agent「自己去找数据」而非只报"缺"。委托共享 crawl_evidence_if_thin（三路径统一）。"""
    ctx = request.context
    if not isinstance(ctx, dict):
        return 0
    evidence = ctx.get("evidence_sources")
    if not isinstance(evidence, dict) or not evidence.get("retrieve"):
        return 0  # 仅在用户挂了证据库要求检索时才主动取数
    symbol = str(ctx.get("focused_symbol") or "").strip() or None
    keyword = _crawl_keyword_for_context(ctx) or ((request.message or "").strip()[:40] or None)
    return await crawl_evidence_if_thin(symbol, keyword)


def _augment_context_with_retrieval(request: GeneralChatRequest) -> None:
    """检索增强（RAG）：挂载证据库且要求检索时，按本次提问 + 聚焦标的从证据库
    主动拉取相关条目，覆盖前端兜底的 recent_items，让 [n] 内联引用与问题真正相关。

    - 有聚焦标的：symbol + query 词元做相关性检索；为空则回退 symbol-only（最近+高可信）。
    - 无聚焦标的：用提问词元尽力检索。
    - 检索为空时不覆盖（保留前端兜底 recent_items），保证至少有来源可引用。
    list_data_items 已按 credibility_score 优先排序，引用来源天然偏高可信。
    """
    ctx = request.context
    if not isinstance(ctx, dict):
        return
    evidence = ctx.get("evidence_sources")
    if not isinstance(evidence, dict) or not evidence.get("retrieve"):
        return
    symbol = str(ctx.get("focused_symbol") or "").strip() or None
    message = (request.message or "").strip()
    query = message[:80] or None
    try:
        # 取较大候选池（12）供相关性重排，再裁到 top5。
        items: list[Any] = []
        if symbol:
            items = list_data_items(symbol=symbol, query=query, limit=12, sort="time_desc")
            if not items:
                items = list_data_items(symbol=symbol, limit=12, sort="time_desc")
        elif query:
            items = list_data_items(query=query, limit=12, sort="time_desc")
    except Exception:
        return
    if not items:
        return
    # 去重：同题条目（同一文章被多次入库）只保留可信度最高的一条，避免 [2][3][4] 重复引用。
    deduped: list[Any] = []
    seen_titles: set[str] = set()
    for item in items:
        key = (item.title or "").strip()
        if key and key in seen_titles:
            continue
        seen_titles.add(key)
        deduped.append(item)
    # 相关性重排：按提问 2-gram 词元命中标题+正文的次数排序（credibility 作次序），
    # 让最贴合本次提问的证据排前被引用；提问无重合时退化为原 credibility 序（list 已排好）。
    q_tokens = query_tokens_2gram(message)
    if q_tokens and len(deduped) > 1:
        def _relevance(it: Any) -> int:
            hay = f"{it.title or ''} {it.text_preview or ''}".lower()
            return sum(1 for tok in q_tokens if tok in hay)
        deduped.sort(key=lambda it: (_relevance(it), it.credibility_score), reverse=True)
    evidence["recent_items"] = [
        {
            "title": item.title,
            "symbol": item.symbol,
            "source": item.source_name,
            "url": item.url or "",
            "credibility": item.credibility_score,
            # 用完整正文（截断）而非短 preview，让模型有足够内容做综合+准确引用，而不只是贴标题。
            "preview": (item.text or item.text_preview or "")[:360].strip(),
        }
        for item in deduped[:5]
    ]
    evidence["retrieved"] = True


@app.post("/api/agents/chat", response_model=GeneralChatResponse)
async def general_chat(request: GeneralChatRequest) -> GeneralChatResponse:
    try:
        await _acquire_fresh_evidence_if_thin(request)  # 主动取数：证据不足时自动爬一轮
        _augment_context_with_retrieval(request)
        return attach_data_quality(await llm.general_chat(request))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/agents/chat/stream")
async def general_chat_stream(request: GeneralChatRequest) -> StreamingResponse:
    """SSE stream of assistant text deltas for the home chat (token-by-token)."""

    async def event_generator() -> AsyncIterator[str]:
        # 主动取数（agentic）：证据不足时先去爬一轮最新资料；期间给前端一个状态提示。
        ctx = request.context if isinstance(request.context, dict) else {}
        ev = ctx.get("evidence_sources")
        if isinstance(ev, dict) and ev.get("retrieve"):
            yield f"data: {json.dumps({'status': '正在检索最新资料…'}, ensure_ascii=False)}\n\n"
            await _acquire_fresh_evidence_if_thin(request)
        # 检索增强：按本次提问主动从证据库拉相关条目（RAG），再抽编号来源。
        _augment_context_with_retrieval(request)
        # 先回传本次可引用的编号来源（前端据此把 [n] 渲染成可点引用 + 来源列表）。
        sources = extract_citable_sources(request.context)
        if sources:
            yield f"data: {json.dumps({'sources': sources}, ensure_ascii=False)}\n\n"
        try:
            async for delta in llm.general_chat_stream(request):
                yield f"data: {json.dumps({'delta': delta}, ensure_ascii=False)}\n\n"
        except Exception as exc:  # noqa: BLE001 — surface the error into the client stream
            yield f"data: {json.dumps({'error': str(exc)}, ensure_ascii=False)}\n\n"
        else:
            yield 'data: {"done": true}\n\n'

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _professional_chat_intent(text: str) -> bool:
    return bool(
        re.search(
            r"专业财报|财报库|财报|年报|半年报|季报|研报|电话会|营收|营业收入|净利润|扣非|毛利率|ROE|现金流|资本开支|引用|溯源|评测|回归测试|幻觉",
            text,
            re.I,
        )
    )


async def _maybe_shareholder_change_skill_chat(
    request: OrchestratorChatRequest,
) -> Optional[OrchestratorChatResponse]:
    skill_request = detect_shareholder_change_request(request.message)
    if not skill_request:
        return None

    result = await scan_shareholder_changes(skill_request)
    market_label = {"A": "A股", "HK": "港股", "US": "美股"}.get(result.market, result.market)
    source_label = {
        "A": "巨潮资讯网",
        "HK": "HKEX DI",
        "US": "SEC EDGAR",
    }.get(result.market, result.provider)
    return OrchestratorChatResponse(
        provider=result.provider,
        model=result.model,
        generated_at=result.generated_at,
        agent="Orchestrator",
        engine=request.engine,
        title="股东增减持扫描",
        content=format_shareholder_change_skill_response(result),
        chips=["shareholder.change.scan", source_label, f"{market_label}披露"],
        suggested_actions=["查看高风险减持", "扩大到30天", "导出公告列表"],
        reasoning_trace=[
            {
                "phase": "orchestrator",
                "title": "Orchestrator",
                "detail": f"识别到全市场 {market_label} 股东增减持扫描意图，路由到可执行 skill。",
                "status": "done",
            },
            {
                "phase": "skill",
                "title": "shareholder.change.scan",
                "detail": f"调用{source_label}披露检索，窗口 {result.start_date} 至 {result.end_date}，命中 {result.total_found} 条。",
                "status": "done",
            },
            {
                "phase": "evidence",
                "title": "Evidence",
                "detail": "结果保留公告标题、股票代码、公告日和 PDF 原文链接，避免无来源总结。",
                "status": "done",
            },
        ],
        should_create_task=False,
        handled_inline=True,
        confidence=0.88 if result.records else 0.62,
    )


async def _maybe_cn_earnings_skill_chat(
    request: OrchestratorChatRequest,
) -> Optional[OrchestratorChatResponse]:
    skill_request = detect_cn_earnings_request(request.message)
    if not skill_request:
        return None

    result = await scan_cn_earnings(skill_request)
    return OrchestratorChatResponse(
        provider=result.provider,
        model=result.model,
        generated_at=result.generated_at,
        agent="Orchestrator",
        engine=request.engine,
        title="A股财报扫描",
        content=format_cn_earnings_skill_response(result),
        chips=["cn.earnings.scan", "巨潮资讯网", "A股财报"],
        suggested_actions=["查看高风险财报", "扩大到90天", "进入A股财报板块"],
        reasoning_trace=[
            {
                "phase": "orchestrator",
                "title": "Orchestrator",
                "detail": "识别到全市场 A 股财报公告扫描意图，路由到可执行 skill。",
                "status": "done",
            },
            {
                "phase": "skill",
                "title": "cn.earnings.scan",
                "detail": f"调用巨潮公告检索，窗口 {result.start_date} 至 {result.end_date}，命中 {result.total_found} 条。",
                "status": "done",
            },
            {
                "phase": "evidence",
                "title": "Evidence",
                "detail": "结果保留财报公告标题、核心财务字段、PDF 摘录和原文链接，避免无来源总结。",
                "status": "done",
            },
        ],
        should_create_task=False,
        handled_inline=True,
        confidence=0.86 if result.records else 0.6,
    )


async def _maybe_major_event_skill_chat(
    request: OrchestratorChatRequest,
) -> Optional[OrchestratorChatResponse]:
    skill_request = detect_major_event_request(request.message)
    if not skill_request:
        return None

    result = await scan_major_events(skill_request)
    return OrchestratorChatResponse(
        provider=result.provider,
        model=result.model,
        generated_at=result.generated_at,
        agent="Orchestrator",
        engine=request.engine,
        title="A股重大事项扫描",
        content=format_major_event_skill_response(result),
        chips=["cn.major_event.scan", "巨潮资讯网", "A股重大事项"],
        suggested_actions=["查看高风险事件", "扩大到90天", "进入事件中心"],
        reasoning_trace=[
            {
                "phase": "orchestrator",
                "title": "Orchestrator",
                "detail": "识别到全市场 A 股重大事项/事件预警意图，路由到可执行 skill。",
                "status": "done",
            },
            {
                "phase": "skill",
                "title": "cn.major_event.scan",
                "detail": f"调用巨潮公告检索，窗口 {result.start_date} 至 {result.end_date}，命中 {result.total_found} 条。",
                "status": "done",
            },
            {
                "phase": "evidence",
                "title": "Evidence",
                "detail": "结果保留公告标题、事件标签、PDF 摘录和原文链接，避免无来源总结。",
                "status": "done",
            },
        ],
        should_create_task=False,
        handled_inline=True,
        confidence=0.87 if result.records else 0.62,
    )


def _select_professional_chat_report(request: OrchestratorChatRequest) -> Optional[ProfessionalReportRecord]:
    symbol = request.stock.symbol if request.stock else None
    reports = list_professional_reports(symbol=symbol, limit=1) if symbol else []
    if not reports:
        reports = list_professional_reports(limit=1)
    return reports[0] if reports else None


def _professional_chat_trace(
    request: OrchestratorChatRequest,
    *,
    report: Optional[ProfessionalReportRecord],
    capability: str,
) -> list[dict[str, str]]:
    stock_label = f"{request.stock.name}（{request.stock.symbol}）" if request.stock else "未选择标的"
    report_label = report.title if report else "未命中专业财报库"
    return [
        {
            "phase": "orchestrator",
            "title": "Orchestrator",
            "detail": f"识别为专业财报能力调用，当前标的 {stock_label}。",
            "status": "done",
        },
        {
            "phase": "evidence",
            "title": "Evidence",
            "detail": f"检索专业财报库：{report_label}。",
            "status": "done" if report else "wait",
        },
        {
            "phase": "research",
            "title": "Analyst",
            "detail": f"调用{capability}，使用结构化指标、原文 chunk 和引用约束即时回答。",
            "status": "done" if report else "wait",
        },
        {
            "phase": "risk",
            "title": "RefusalGuard",
            "detail": "证据不足时拒答，不进入自由编造。",
            "status": "done",
        },
    ]


def _format_professional_eval(run: ProfessionalEvalRunResponse) -> str:
    rows = [
        f"评测完成：{run.passed}/{run.total} 通过，Pass Rate {round(run.pass_rate * 100)}%。",
        f"引用覆盖 {round(run.citation_rate * 100)}%，答案命中 {round(run.answer_match_rate * 100)}%，拒答保护 {round(run.refusal_guard_rate * 100)}%。",
    ]
    for case in run.cases[:4]:
        status_label = "通过" if case.passed else "待修正"
        rows.append(f"- {status_label}：{case.question}")
    return "\n".join(rows)


def _format_professional_analysis(result: ProfessionalReportAnalysisResponse) -> str:
    metric_lines = [
        f"- {metric.metric_label}：{metric.raw_value}"
        for metric in result.key_metrics[:6]
    ]
    sections = [
        result.summary,
        "",
        "核心指标：",
        *(metric_lines or ["- 暂未抽取到核心指标"]),
        "",
        "质量/风险：",
        *[f"- {item}" for item in [*result.quality_flags[:3], *result.risks[:3]]],
        "",
        "下一步追问：",
        *[f"- {item}" for item in result.follow_up_questions[:4]],
    ]
    return "\n".join(section for section in sections if section != "")


async def _maybe_professional_research_chat(
    request: OrchestratorChatRequest,
) -> Optional[OrchestratorChatResponse]:
    text = request.message.strip()
    if not _professional_chat_intent(text):
        return None

    report = _select_professional_chat_report(request)
    if not report:
        return OrchestratorChatResponse(
            provider=llm.provider_name,
            model=llm.model,
            generated_at=datetime.now(timezone.utc),
            agent="Orchestrator",
            engine=request.engine,
            title="专业财报库",
            content=(
                "专业财报能力已经接入决策台，但当前还没有可用的入库报告。"
                "你可以在这条对话上传财报/研报 PDF，或先到数据源中心入库；入库后我可以直接查指标、给引用、做财报分析和跑评测。"
            ),
            chips=["专业财报库", "待入库", "引用型RAG"],
            suggested_actions=["上传财报", "入库资料", "查看数据源"],
            reasoning_trace=_professional_chat_trace(request, report=None, capability="专业财报库"),
            should_create_task=False,
            handled_inline=True,
            confidence=0.72,
        )

    if re.search(r"评测|回归测试|幻觉|准确率|eval|test", text, re.I):
        eval_run = await run_professional_eval(ProfessionalEvalRunRequest(report_id=report.id))
        return OrchestratorChatResponse(
            provider=llm.provider_name,
            model=llm.model,
            generated_at=datetime.now(timezone.utc),
            agent="Orchestrator",
            engine=request.engine,
            title="专业财报评测",
            content=_format_professional_eval(eval_run),
            chips=["专业财报库", "评测集", f"通过率{round(eval_run.pass_rate * 100)}%"],
            suggested_actions=["查看失败用例", "补充黄金集", "重新入库"],
            reasoning_trace=_professional_chat_trace(request, report=report, capability="专业财报评测"),
            should_create_task=False,
            handled_inline=True,
            confidence=0.84,
        )

    if re.search(r"分析|解读|体检|质量|红旗|风险|追问|总结|报告|agent", text, re.I):
        analysis = await analyze_professional_report(
            report.id,
            ProfessionalReportAnalysisRequest(focus=text, use_cloud_model=True),
        )
        return OrchestratorChatResponse(
            provider=llm.provider_name,
            model=llm.model,
            generated_at=datetime.now(timezone.utc),
            agent="Orchestrator",
            engine=request.engine,
            title="专业财报分析",
            content=_format_professional_analysis(analysis),
            chips=["专业财报库", "财报分析技能", f"{len(analysis.key_metrics)}个指标"],
            suggested_actions=["追问指标", "跑评测", "查看引用"],
            reasoning_trace=_professional_chat_trace(request, report=report, capability="专业财报分析"),
            should_create_task=False,
            handled_inline=True,
            confidence=analysis.confidence,
        )

    rag = await query_professional_rag(
        ProfessionalRagQueryRequest(
            question=text,
            report_id=report.id,
            symbol=report.symbol,
            top_k=6,
            use_cloud_model=True,
        )
    )
    citation_labels = [citation.citation_id for citation in rag.citations[:4]]
    return OrchestratorChatResponse(
        provider=llm.provider_name,
        model=llm.model,
        generated_at=datetime.now(timezone.utc),
        agent="Orchestrator",
        engine=request.engine,
        title="引用型财报问答",
        content=rag.answer,
        chips=["专业财报库", "引用型RAG", *(citation_labels or ["无证据拒答"])],
        suggested_actions=["继续追问", "分析整份财报", "跑评测"],
        reasoning_trace=_professional_chat_trace(request, report=report, capability="引用型财报问答"),
        should_create_task=False,
        handled_inline=True,
        confidence=rag.confidence,
    )


def _is_research_intent(message: str) -> bool:
    msg = message.lower()
    research_keywords = [
        "分析", "调研", "研究", "评估", "诊断",
        "analyze", "research", "evaluate", "assess", "review",
        "怎么样", "怎么看", "如何", "建议", "推荐",
        "财报", "基本面", "估值", "营收", "利润",
        "风险", "仓位", "持仓", "前景", "未来",
        "earning", "financial", "report", "outlook", "risk",
        "季报", "年报", "中报", "业绩",
    ]
    return any(kw in msg for kw in research_keywords)


def _tool_agent_enabled() -> bool:
    """AI 原生 tool-use agent 路径开关。

    默认关：tool-agent 会改变研究类问答的取数方式（模型自主调工具），需先在目标 live 模型上
    验证其 OpenAI 兼容端点支持 function-calling，再置 DEEPFOCUS_TOOL_AGENT=1 灰度开启。
    关闭时端点行为与既有完全一致（红线：不破坏现有体验）。
    """
    return os.getenv("DEEPFOCUS_TOOL_AGENT", "").strip().lower() in {"1", "true", "yes", "on"}


@app.post("/api/agents/cross-module-research", response_model=CrossModuleResearchResponse)
async def cross_module_research(request: CrossModuleResearchRequest) -> CrossModuleResearchResponse:
    if not request.symbol:
        raise HTTPException(status_code=400, detail="symbol is required")
    data = await gather_all_for_stock(
        symbol=request.symbol,
        include_macro=request.include_macro,
        include_risk=request.include_risk,
        include_evidence=request.include_evidence,
        include_metrics=request.include_metrics,
        include_supply_chain=request.include_supply_chain,
        include_trade=request.include_trade,
    )
    return CrossModuleResearchResponse(**data)


@app.post("/api/agents/research-loop/stream")
async def research_loop_stream(
    request: Request,
    symbol: str = "",
    question: str = "",
):
    if not symbol or not question:
        raise HTTPException(status_code=400, detail="symbol and question are required")

    async def event_generator() -> AsyncIterator[str]:
        async for event in run_agent_research_loop(llm, symbol, question, request):
            if await request.is_disconnected():
                break
            yield event

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
        },
    )


@app.post("/api/agents/orchestrator-chat", response_model=OrchestratorChatResponse)
async def orchestrator_chat(request: OrchestratorChatRequest) -> OrchestratorChatResponse:
    try:
        shareholder_change_reply = await _maybe_shareholder_change_skill_chat(request)
        if shareholder_change_reply:
            return attach_data_quality(shareholder_change_reply)
        cn_earnings_reply = await _maybe_cn_earnings_skill_chat(request)
        if cn_earnings_reply:
            return attach_data_quality(cn_earnings_reply)
        major_event_reply = await _maybe_major_event_skill_chat(request)
        if major_event_reply:
            return attach_data_quality(major_event_reply)
        professional_reply = await _maybe_professional_research_chat(request)
        if professional_reply:
            return attach_data_quality(professional_reply)

        stock_symbol = (request.stock.symbol or "").strip() if request.stock else ""
        if stock_symbol and _is_research_intent(request.message):
            # ① AI 原生 tool-use agent：模型自主调工具按需取数（开关控制，默认关）。
            #    返回 None（mock / 工具不被支持 / 任何失败）即落到 ② 既有预聚合路径。
            if _tool_agent_enabled():
                try:
                    agent_result = await llm.run_tool_agent(
                        question=request.message,
                        context_hint=f"当前标的：{(request.stock.name or '') if request.stock else ''}（{stock_symbol}）",
                    )
                    if agent_result:
                        mapped = tool_agent_to_orchestrator_response(
                            agent_result, request, llm.provider_name, llm.model
                        )
                        if mapped:
                            return attach_data_quality(mapped)
                except Exception:
                    pass
            # ② 既有路径：服务端预聚合跨模块数据 → 注入 → 合成。
            try:
                aggregated = await gather_all_for_stock(
                    stock_symbol,
                    include_macro=request.include_macro,
                    include_risk=request.include_risk,
                    include_evidence=request.include_evidence,
                    include_metrics=request.include_metrics,
                    include_supply_chain=request.include_supply_chain,
                    include_trade=request.include_trade,
                )
                injection = build_injection_block(aggregated)
                return attach_data_quality(await llm.orchestrator_chat_with_context(request, injection))
            except Exception:
                pass

        return attach_data_quality(await llm.orchestrator_chat(request))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/risk/greeks", response_model=GreeksResponse)
async def risk_greeks(request: GreeksRequest) -> GreeksResponse:
    result = calculate_greeks(
        underlying_price=request.underlying_price,
        strike=request.strike,
        days_to_expiry=request.days_to_expiry,
        risk_free_rate=request.risk_free_rate,
        implied_vol=request.implied_vol,
        option_type=request.option_type,
    )
    return GreeksResponse(**result)


@app.get("/api/risk/positions", response_model=PositionListResponse)
async def risk_positions(status: Optional[str] = None) -> PositionListResponse:
    positions = list_positions(status=status if status else None)
    enriched = []
    for pos in positions:
        risk = calculate_position_risk(pos)
        enriched.append({**pos, **risk})
    return PositionListResponse(positions=[PositionRecord(**p) for p in enriched])


@app.post("/api/risk/positions", response_model=PositionRecord)
async def risk_create_position(request: PositionCreateRequest) -> PositionRecord:
    pos = create_position(
        symbol=request.symbol,
        name=request.name,
        market=request.market,
        asset_class=request.asset_class,
        direction=request.direction,
        entry_price=request.entry_price,
        quantity=request.quantity,
        stop_loss=request.stop_loss,
        take_profit=request.take_profit,
        position_size_pct=request.position_size_pct,
        sector=request.sector,
        strategy=request.strategy,
        notes=request.notes,
        tags=request.tags,
        greeks=request.greeks,
    )
    risk = calculate_position_risk(pos)
    return PositionRecord(**{**pos, **risk})


@app.get("/api/risk/positions/{position_id}", response_model=PositionRecord)
async def risk_get_position(position_id: str) -> PositionRecord:
    pos = get_position(position_id)
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found")
    risk = calculate_position_risk(pos)
    return PositionRecord(**{**pos, **risk})


@app.put("/api/risk/positions/{position_id}", response_model=PositionRecord)
async def risk_update_position(position_id: str, request: PositionUpdateRequest) -> PositionRecord:
    updates = {k: v for k, v in request.model_dump().items() if v is not None}
    pos = update_position(position_id, **updates)
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found")
    risk = calculate_position_risk(pos)
    return PositionRecord(**{**pos, **risk})


@app.delete("/api/risk/positions/{position_id}")
async def risk_delete_position(position_id: str) -> dict:
    if not delete_position(position_id):
        raise HTTPException(status_code=404, detail="Position not found")
    return {"status": "deleted", "id": position_id}


@app.post("/api/risk/positions/{position_id}/close", response_model=PositionRecord)
async def risk_close_position(position_id: str, request: PositionCloseRequest) -> PositionRecord:
    try:
        pos = close_position(position_id, request.exit_price, request.exit_reason)
    except PositionAlreadyClosedError:
        raise HTTPException(status_code=409, detail="该持仓已平仓，请勿重复平仓。")
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found")
    risk = calculate_position_risk(pos)
    return PositionRecord(**{**pos, **risk})


@app.post("/api/risk/positions/refresh")
async def risk_refresh_prices() -> dict:
    updated = refresh_position_prices()
    return {"status": "ok", "updated_count": len(updated), "positions": updated}


@app.get("/api/risk/summary", response_model=RiskSummaryResponse)
async def risk_summary() -> RiskSummaryResponse:
    data = get_risk_summary()
    from .risk_management import calculate_position_risk
    enriched = []
    for pos in data.get("open_positions", []):
        risk = calculate_position_risk(pos)
        enriched.append({**pos, **risk})
    data["open_positions"] = enriched
    return RiskSummaryResponse(**data)


@app.get("/api/risk/limits")
async def risk_limits() -> list[RiskLimitRecord]:
    limits = get_risk_limits()
    return [RiskLimitRecord(**lim) for lim in limits]


@app.put("/api/risk/limits/{key}", response_model=RiskLimitRecord)
async def risk_update_limit(key: str, request: RiskLimitUpdateRequest) -> RiskLimitRecord:
    lim = update_risk_limit(key, request.value, request.enabled)
    if not lim:
        raise HTTPException(status_code=404, detail="Risk limit not found")
    return RiskLimitRecord(**lim)


@app.get("/api/risk/pnl", response_model=PnlSummaryResponse)
async def risk_pnl_summary() -> PnlSummaryResponse:
    return PnlSummaryResponse(**get_pnl_summary())


@app.get("/api/risk/pnl/records")
async def risk_pnl_records(position_id: Optional[str] = None, limit: int = 100) -> list[PnlRecord]:
    records = list_pnl_records(position_id=position_id, limit=limit)
    return [PnlRecord(**r) for r in records]


@app.post("/api/backtest/{backtest_id}/run")
async def backtest_run(backtest_id: str, request: Request) -> StreamingResponse:
    bt = get_backtest(backtest_id)
    if not bt:
        raise HTTPException(status_code=404, detail="Backtest not found")
    if bt.get("status") == "running":
        raise HTTPException(status_code=409, detail="Backtest is already running")

    async def event_gen():
        async for event in run_backtest(backtest_id, request):
            if await request.is_disconnected():
                break
            yield event

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache", "Connection": "keep-alive",
            "X-Accel-Buffering": "no", "Access-Control-Allow-Origin": "*",
        },
    )


@app.get("/api/backtest/aggregate")
async def backtest_aggregate_for_research(symbol: str = "") -> dict:
    if not symbol:
        return {"backtests": [], "symbol": "", "total": 0}
    return await list_backtest_results(symbol)


@app.get("/api/backtest", response_model=BacktestListResponse)
async def backtest_list(limit: int = 50) -> BacktestListResponse:
    backtests = list_backtests(limit=limit)
    return BacktestListResponse(backtests=[BacktestRecord(**bt) for bt in backtests])


@app.post("/api/backtest", response_model=BacktestRecord)
async def backtest_create(request: BacktestCreateRequest) -> BacktestRecord:
    bt = create_backtest(
        name=request.name,
        market=request.market,
        strategy_type=request.strategy_type,
        symbols=request.symbols,
        start_date=request.start_date,
        end_date=request.end_date,
        initial_capital=request.initial_capital,
        benchmark=request.benchmark,
        parameters=request.parameters,
    )
    return BacktestRecord(**bt)


@app.get("/api/backtest/{backtest_id}", response_model=BacktestRecord)
async def backtest_get(backtest_id: str) -> BacktestRecord:
    bt = get_backtest(backtest_id)
    if not bt:
        raise HTTPException(status_code=404, detail="Backtest not found")
    return BacktestRecord(**bt)


@app.delete("/api/backtest/{backtest_id}")
async def backtest_delete(backtest_id: str) -> dict:
    if not delete_backtest(backtest_id):
        raise HTTPException(status_code=404, detail="Backtest not found")
    return {"status": "deleted", "id": backtest_id}


@app.post("/api/backtest/metrics", response_model=BacktestMetricsResponse)
async def backtest_metrics(request: BacktestMetricsRequest) -> BacktestMetricsResponse:
    metrics = calculate_backtest_metrics(
        equity_curve=request.equity_curve,
        benchmark_curve=request.benchmark_curve,
        initial_capital=request.initial_capital,
    )
    return BacktestMetricsResponse(**metrics)


@app.get("/api/market-dashboard", response_model=MarketDashboardResponse)
async def market_dashboard() -> MarketDashboardResponse:
    data = await fetch_market_dashboard()
    return MarketDashboardResponse(**data)


@app.get("/api/market-dashboard/ashare", response_model=MarketDashboardResponse)
async def ashare_dashboard() -> MarketDashboardResponse:
    data = await fetch_ashare_dashboard()
    return MarketDashboardResponse(**data)


@app.post("/api/market-dashboard/analyze", response_model=DashboardAnalysisResponse)
async def market_dashboard_analyze() -> DashboardAnalysisResponse:
    dashboard = await fetch_market_dashboard()
    indicators_data = json.dumps(
        [
            {
                "name": ind["name"],
                "value": ind["value"],
                "unit": ind["unit"],
                "signal": ind["signal"],
                "status": ind["status"],
            }
            for cat in dashboard.get("categories", [])
            for ind in cat.get("indicators", [])
        ],
        ensure_ascii=False,
    )
    result = await llm.analyze_market_dashboard(
        title=f"整体信号：{dashboard['overall_signal']} (评分{dashboard['overall_score']})",
        indicators_json=indicators_data,
        market_type="global",
    )
    return DashboardAnalysisResponse(**result)


@app.post("/api/market-dashboard/ashare/analyze", response_model=DashboardAnalysisResponse)
async def ashare_dashboard_analyze() -> DashboardAnalysisResponse:
    dashboard = await fetch_ashare_dashboard()
    indicators_data = json.dumps(
        [
            {
                "name": ind["name"],
                "value": ind["value"],
                "unit": ind["unit"],
                "signal": ind["signal"],
                "status": ind["status"],
            }
            for cat in dashboard.get("categories", [])
            for ind in cat.get("indicators", [])
        ],
        ensure_ascii=False,
    )
    result = await llm.analyze_market_dashboard(
        title=f"A股整体信号：{dashboard['overall_signal']} (评分{dashboard['overall_score']})",
        indicators_json=indicators_data,
        market_type="ashare",
    )
    return DashboardAnalysisResponse(**result)
