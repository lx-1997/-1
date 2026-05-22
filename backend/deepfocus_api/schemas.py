from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class StockSnapshot(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    symbol: str
    name: str
    market: Optional[str] = None
    sector: Optional[str] = None
    market_cap: Optional[float] = Field(default=None, alias="marketCap")
    current_price: Optional[float] = Field(default=None, alias="currentPrice")
    change_percent: Optional[float] = Field(default=None, alias="changePercent")
    description: Optional[str] = None
    focus_level: Optional[str] = Field(default=None, alias="focusLevel")
    community_score: Optional[float] = Field(default=None, alias="communityScore")


class PostSnapshot(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str
    summary: Optional[str] = None
    content: Optional[str] = None
    category: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    quality_score: Optional[float] = Field(default=None, alias="qualityScore")
    publish_time: Optional[str] = Field(default=None, alias="publishTime")


class StockAnalysisRequest(BaseModel):
    stock: StockSnapshot
    posts: list[PostSnapshot] = Field(default_factory=list)
    question: Optional[str] = None
    locale: str = "zh-CN"


SentimentLabel = Literal["positive", "neutral", "negative"]
RiskLevel = Literal["low", "medium", "high"]


class StockAnalysisResponse(BaseModel):
    provider: str
    model: str
    generated_at: datetime
    executive_summary: str
    sentiment_label: SentimentLabel
    sentiment_score: float = Field(ge=-1, le=1)
    risk_level: RiskLevel
    catalysts: list[str]
    risks: list[str]
    watch_items: list[str]
    suggested_questions: list[str]
    disclaimer: str = "仅供投研参考，不构成投资建议。"


class SentimentRequest(BaseModel):
    text: str
    locale: str = "zh-CN"


class SentimentResponse(BaseModel):
    provider: str
    model: str
    label: SentimentLabel
    score: float = Field(ge=-1, le=1)
    rationale: str


class Capability(BaseModel):
    key: str
    name: str
    description: str
    endpoint: str
    mode: Literal["cloud", "mock", "local-optional"]


class CapabilityListResponse(BaseModel):
    provider: str
    model: str
    capabilities: list[Capability]


class ModelConfigRequest(BaseModel):
    provider: Literal["mock", "openai", "minimax", "openai-compatible", "cloud"] = "mock"
    model: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    temperature: float = Field(default=0.2, ge=0, le=1)
    persist: bool = True


class ModelConfigResponse(BaseModel):
    provider: str
    model: str
    base_url: Optional[str] = None
    temperature: float
    api_key_configured: bool
    api_key_preview: Optional[str] = None
    config_source: str


class FileExtractionResponse(BaseModel):
    filename: str
    content_type: Optional[str] = None
    text: str
    char_count: int
    truncated: bool
    parser: str


DataSourceType = Literal["server_api", "market_api", "upload", "web_page", "agent_crawl", "manual"]
DataSourceStatus = Literal["active", "paused", "error"]
DataSourceCategory = Literal["market", "earnings", "filing", "research", "sentiment", "upload", "internal", "other"]
DataSourceModule = Literal[
    "home",
    "stock_detail",
    "earnings_calendar",
    "ai_research",
    "ai_supply_chain",
    "realtime_messages",
    "agent_center",
    "data_source_center",
    "custom",
]
DataSourceRefRole = Literal["primary", "fallback", "evidence", "signal", "context"]
TrustLevel = Literal["internal", "official", "media", "community", "unknown"]
KeywordCrawlProvider = Literal["xueqiu", "wechat_public"]
KeywordCrawlSort = Literal["relevance", "time_desc"]
KeywordCrawlFreshness = Literal["day", "week", "month", "year", "any"]


class DataSourceCreateRequest(BaseModel):
    name: str
    category: DataSourceCategory = "research"
    source_type: DataSourceType = "server_api"
    description: str = ""
    url: Optional[str] = None
    method: Literal["GET", "POST"] = "GET"
    headers: dict[str, str] = Field(default_factory=dict)
    params: dict[str, str] = Field(default_factory=dict)
    body: Optional[dict[str, Any]] = None
    symbol_param: Optional[str] = "symbol"
    query_param: Optional[str] = "q"
    trust_level: TrustLevel = "unknown"
    enabled: bool = True
    notes: Optional[str] = None
    output_schema: dict[str, Any] = Field(default_factory=dict)


class DataSourceModuleRefCreateRequest(BaseModel):
    source_id: str
    module: DataSourceModule
    role: DataSourceRefRole = "evidence"
    filters: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""
    enabled: bool = True


class DataSourceModuleRefRecord(BaseModel):
    id: str
    source_id: str
    source_name: str
    source_type: DataSourceType
    source_category: DataSourceCategory
    module: DataSourceModule
    role: DataSourceRefRole
    filters: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""
    enabled: bool = True
    created_at: str
    updated_at: str


class DataSourceModuleRefListResponse(BaseModel):
    refs: list[DataSourceModuleRefRecord]


class DataSourceRecord(BaseModel):
    id: str
    name: str
    category: DataSourceCategory = "research"
    source_type: DataSourceType
    description: str = ""
    status: DataSourceStatus
    trust_level: TrustLevel
    output_schema: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    module_refs: list[DataSourceModuleRefRecord] = Field(default_factory=list)
    items_count: int = 0
    last_sync_at: Optional[str] = None
    last_error: Optional[str] = None
    created_at: str
    updated_at: str


class DataSourceListResponse(BaseModel):
    sources: list[DataSourceRecord]


class DataSourceSyncRequest(BaseModel):
    symbol: Optional[str] = None
    query: Optional[str] = None
    url: Optional[str] = None
    limit: int = Field(default=20, ge=1, le=100)


class DataSourceKeywordCrawlRequest(BaseModel):
    provider: KeywordCrawlProvider = "wechat_public"
    keyword: str
    symbol: Optional[str] = None
    limit: int = Field(default=10, ge=1, le=30)
    sort: KeywordCrawlSort = "time_desc"
    freshness: KeywordCrawlFreshness = "week"


class DataSourceItemRecord(BaseModel):
    id: str
    source_id: str
    source_name: str
    source_type: DataSourceType
    source_category: DataSourceCategory = "other"
    title: str
    symbol: Optional[str] = None
    url: Optional[str] = None
    text: str
    text_preview: str
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    credibility_score: float = Field(ge=0, le=1)
    collected_at: str
    created_at: str


class DataSourceItemUpdateRequest(BaseModel):
    title: Optional[str] = None
    symbol: Optional[str] = None
    tags: Optional[list[str]] = None
    credibility_score: Optional[float] = Field(default=None, ge=0, le=1)
    ai_interpretation: Optional[str] = None


class DataSourceItemListResponse(BaseModel):
    items: list[DataSourceItemRecord]


class DataSourceTagRecord(BaseModel):
    tag: str
    count: int


class DataSourceTagListResponse(BaseModel):
    tags: list[DataSourceTagRecord]


class DataSourceSyncResponse(BaseModel):
    source: DataSourceRecord
    imported_count: int
    items: list[DataSourceItemRecord]


class DataSourceKeywordCrawlResponse(BaseModel):
    provider: KeywordCrawlProvider
    effective_provider: KeywordCrawlProvider
    attempted_providers: list[KeywordCrawlProvider] = Field(default_factory=list)
    fallback_used: bool = False
    provider_policy: dict[str, Any] = Field(default_factory=dict)
    keyword: str
    sort: KeywordCrawlSort
    freshness: KeywordCrawlFreshness
    source: DataSourceRecord
    imported_count: int
    items: list[DataSourceItemRecord]
    warnings: list[str] = Field(default_factory=list)


RealtimeMessageSeverity = Literal["info", "success", "warning", "critical"]


class RealtimeMessageCreateRequest(BaseModel):
    title: str
    content: str = ""
    source_id: Optional[str] = None
    source_name: Optional[str] = None
    source_type: Optional[str] = None
    symbol: Optional[str] = None
    topic: str = "data-source"
    severity: RealtimeMessageSeverity = "info"
    url: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RealtimeMessageRecord(BaseModel):
    id: str
    title: str
    content: str
    source_id: Optional[str] = None
    source_name: Optional[str] = None
    source_type: Optional[str] = None
    symbol: Optional[str] = None
    topic: str
    severity: RealtimeMessageSeverity
    url: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class RealtimeMessageListResponse(BaseModel):
    messages: list[RealtimeMessageRecord]


McpTransport = Literal["streamable_http", "stdio", "hosted"]
McpServerStatus = Literal["unknown", "connected", "error", "disabled"]
McpCapabilityType = Literal["tool", "resource", "prompt"]
McpRiskLevel = Literal["low", "medium", "high"]


class McpServerCreateRequest(BaseModel):
    name: str
    transport: McpTransport = "streamable_http"
    description: str = ""
    url: Optional[str] = None
    command: Optional[str] = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    trust_level: TrustLevel = "unknown"
    risk_level: McpRiskLevel = "medium"
    approval_required: bool = True
    enabled: bool = True
    allowed_tools: list[str] = Field(default_factory=list)
    blocked_tools: list[str] = Field(default_factory=list)
    notes: Optional[str] = None


class McpServerRecord(BaseModel):
    id: str
    name: str
    transport: McpTransport
    description: str = ""
    status: McpServerStatus
    trust_level: TrustLevel
    risk_level: McpRiskLevel
    approval_required: bool = True
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)
    tool_count: int = 0
    resource_count: int = 0
    prompt_count: int = 0
    last_connected_at: Optional[str] = None
    last_error: Optional[str] = None
    created_at: str
    updated_at: str


class McpServerListResponse(BaseModel):
    servers: list[McpServerRecord]


class McpCapabilityRecord(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    server_id: str
    capability_type: McpCapabilityType
    name: str
    title: Optional[str] = None
    description: str = ""
    schema_: dict[str, Any] = Field(default_factory=dict, alias="schema")
    uri: Optional[str] = None
    mime_type: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    discovered_at: str


class McpCapabilityListResponse(BaseModel):
    capabilities: list[McpCapabilityRecord]


class McpDiscoverResponse(BaseModel):
    server: McpServerRecord
    capabilities: list[McpCapabilityRecord]
    warnings: list[str] = Field(default_factory=list)


class McpToolCallRequest(BaseModel):
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    approved: bool = False


class McpToolCallResponse(BaseModel):
    server: McpServerRecord
    tool_name: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    content_preview: str = ""
    called_at: str


class MarketQuote(BaseModel):
    symbol: str
    price: float
    change: Optional[float] = None
    change_percent: Optional[float] = None
    previous_close: Optional[float] = None
    open_price: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    volume: Optional[float] = None
    currency: str = "USD"
    provider: str
    provider_name: str
    market_time: Optional[str] = None
    fetched_at: str
    is_realtime: bool = False
    delay_note: str = ""


MarketRegion = Literal["US", "HK", "CN", "OTHER"]
DecisionMode = Literal["research", "backtest", "paper"]
DecisionRiskProfile = Literal["保守", "稳健", "进取", "专业"]


class MarketSymbolCandidate(BaseModel):
    symbol: str
    code: str
    name: str
    market: MarketRegion
    exchange: str = ""
    security_type: str = ""
    quote_id: Optional[str] = None
    provider: str
    provider_name: str


class MarketQuoteListResponse(BaseModel):
    quotes: list[MarketQuote]
    provider: str
    fetched_at: str
    warnings: list[str] = Field(default_factory=list)


class MarketSymbolSearchResponse(BaseModel):
    query: str
    market: Optional[MarketRegion] = None
    candidates: list[MarketSymbolCandidate]
    provider: str
    fetched_at: str
    warnings: list[str] = Field(default_factory=list)


OptionsSourceStatusValue = Literal["ready", "fallback", "blocked", "unavailable"]
OptionsSignalStatus = Literal["delayed", "partial", "unavailable"]
OptionsDirection = Literal["偏多", "中性", "偏空", "不可判定"]
OptionsConviction = Literal["高", "中", "低"]
OptionsTrendLabel = Literal["看涨", "震荡偏强", "震荡", "震荡偏弱", "看跌", "不可判定"]


class OptionsSourceStatus(BaseModel):
    provider: str
    name: str
    status: OptionsSourceStatusValue
    cost: str
    delay: str
    coverage: str
    notes: str = ""


class OptionsKeyStrike(BaseModel):
    side: Literal["call", "put", "mixed"]
    strike: float
    metric: str
    value: float
    distance_pct: Optional[float] = None
    interpretation: str = ""


class OptionsExpirationSignal(BaseModel):
    expiration: str
    dte: Optional[int] = None
    contract_count: int = 0
    call_volume: float = 0
    put_volume: float = 0
    call_open_interest: float = 0
    put_open_interest: float = 0
    pcr_volume: Optional[float] = None
    pcr_open_interest: Optional[float] = None
    atm_straddle_mid: Optional[float] = None
    expected_move_pct: Optional[float] = None
    atm_iv: Optional[float] = None


class OptionsUnusualFlow(BaseModel):
    option_symbol: str
    side: Literal["call", "put"]
    expiration: str
    dte: Optional[int] = None
    strike: float
    volume: float = 0
    open_interest: Optional[float] = None
    volume_open_interest_ratio: Optional[float] = None
    mark_price: Optional[float] = None
    premium_notional: Optional[float] = None
    distance_pct: Optional[float] = None
    score: int = Field(ge=0, le=100)
    severity: OptionsConviction
    reason: str = ""
    interpretation: str = ""
    updated_at: Optional[str] = None


class OptionsSignal(BaseModel):
    symbol: str
    provider: str
    provider_name: str
    source_status: OptionsSignalStatus
    underlying_price: Optional[float] = None
    fetched_at: datetime
    expiration_count: int = 0
    contract_count: int = 0
    data_quality: int = Field(ge=0, le=100)
    direction: OptionsDirection
    score: int = Field(ge=0, le=100)
    conviction: OptionsConviction
    summary: str = ""
    call_volume: float = 0
    put_volume: float = 0
    call_open_interest: float = 0
    put_open_interest: float = 0
    put_call_volume_ratio: Optional[float] = None
    put_call_open_interest_ratio: Optional[float] = None
    avg_iv: Optional[float] = None
    iv_skew: Optional[float] = None
    term_structure: str = ""
    max_pain: Optional[float] = None
    call_wall: Optional[float] = None
    put_wall: Optional[float] = None
    expected_move_abs: Optional[float] = None
    expected_move_pct: Optional[float] = None
    pin_risk_score: int = Field(default=0, ge=0, le=100)
    unusual_flow_count: int = 0
    unusual_premium_notional: float = 0
    unusual_flows: list[OptionsUnusualFlow] = Field(default_factory=list)
    key_strikes: list[OptionsKeyStrike] = Field(default_factory=list)
    expirations: list[OptionsExpirationSignal] = Field(default_factory=list)
    signals: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    delay_note: str = ""


class OptionsSignalResponse(BaseModel):
    generated_at: datetime
    horizon_days: int = 45
    provider: str
    signals: list[OptionsSignal]
    sources: list[OptionsSourceStatus] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    disclaimer: str = "期权链信号仅供投研和风控参考，不构成投资建议；免费源多为延迟快照，不能替代实时订单流、券商成交回报或合规行情。"


class OptionsAiAnalysisRequest(BaseModel):
    signal: OptionsSignal
    horizon_days: int = Field(default=45, ge=7, le=180)
    question: Optional[str] = None
    locale: str = "zh-CN"


class OptionsAiAnalysisResponse(BaseModel):
    provider: str
    model: str
    generated_at: datetime
    symbol: str
    trend_label: OptionsTrendLabel
    trend_score: int = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    time_horizon: str
    thesis: str
    key_drivers: list[str] = Field(default_factory=list)
    upside_triggers: list[str] = Field(default_factory=list)
    downside_triggers: list[str] = Field(default_factory=list)
    watch_levels: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)
    suggested_action: str = ""
    disclaimer: str = "AI 走势研判仅供投研和风控参考，不构成投资建议或收益承诺；请结合实时行情、订单流、基本面和自身风险承受能力独立判断。"


class MultiMarketDecisionRequest(BaseModel):
    markets: list[MarketRegion] = Field(default_factory=lambda: ["CN", "HK", "US"])
    horizon: Literal["5日", "10日", "20日", "60日"] = "10日"
    mode: DecisionMode = "research"
    risk_profile: DecisionRiskProfile = "稳健"
    data_profile: Literal["china_stable", "offline", "mixed"] = "china_stable"
    objective: str = "综合分析市场风格、板块强弱、个股候选和回测验证优先级。"
    stocks: list[StockSnapshot] = Field(default_factory=list)
    notes: Optional[str] = None
    min_score: int = Field(default=60, ge=0, le=100)


class DecisionDependency(BaseModel):
    name: str
    role: str
    markets: list[MarketRegion]
    required: bool = True
    mainland_ready: bool = True
    install_hint: str = ""
    config_keys: list[str] = Field(default_factory=list)
    notes: str = ""


class DecisionModuleStatus(BaseModel):
    key: str
    name: str
    market: MarketRegion
    purpose: str
    data_sources: list[str] = Field(default_factory=list)
    research_engine: str
    backtest_engine: str
    execution_engine: Optional[str] = None
    status: Literal["ready", "partial", "planned"] = "partial"
    readiness: int = Field(ge=0, le=100)
    blocked_by: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class SectorOpinion(BaseModel):
    name: str
    score: int = Field(ge=0, le=100)
    stance: Literal["强势", "中性", "回避"]
    rationale: str = ""


class MarketStyleSignal(BaseModel):
    market: MarketRegion
    label: str
    trend_score: int = Field(ge=0, le=100)
    risk_regime: Literal["进攻", "均衡", "防守"]
    dominant_factors: list[str] = Field(default_factory=list)
    sector_opinions: list[SectorOpinion] = Field(default_factory=list)
    avoid_sectors: list[str] = Field(default_factory=list)
    rationale: str = ""


class CandidateOpinion(BaseModel):
    symbol: str
    name: str
    market: MarketRegion
    sector: str
    action: Literal["重点跟踪", "观察", "回避"]
    score: int = Field(ge=0, le=100)
    surge_probability: float = Field(ge=0, le=1)
    expected_horizon: str
    evidence: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    invalidation: list[str] = Field(default_factory=list)


class BacktestPlan(BaseModel):
    market: MarketRegion
    engine: str
    data_requirements: list[str] = Field(default_factory=list)
    trading_rules: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    next_step: str = ""


class MultiMarketDecisionResponse(BaseModel):
    provider: str = "local-rule"
    model: str = "multi-market-decision-v1"
    generated_at: datetime
    mode: DecisionMode
    risk_profile: DecisionRiskProfile
    readiness_score: int = Field(ge=0, le=100)
    summary: str
    modules: list[DecisionModuleStatus]
    dependencies: list[DecisionDependency]
    market_styles: list[MarketStyleSignal]
    candidates: list[CandidateOpinion]
    portfolio_actions: list[str] = Field(default_factory=list)
    backtest_plan: list[BacktestPlan]
    warnings: list[str] = Field(default_factory=list)
    disclaimer: str = "仅供投研、回测和风控流程参考，不构成投资建议或收益承诺。"


class EarningsCalendarEvent(BaseModel):
    symbol: str
    name: str
    report_date: Optional[str] = None
    fiscal_date_ending: Optional[str] = None
    eps_estimate: Optional[float] = None
    eps_high_estimate: Optional[float] = None
    eps_low_estimate: Optional[float] = None
    eps_actual: Optional[float] = None
    eps_surprise_percent: Optional[float] = None
    revenue_estimate: Optional[float] = None
    revenue_actual: Optional[float] = None
    revenue_surprise_percent: Optional[float] = None
    market_cap: Optional[float] = None
    analyst_count: Optional[int] = None
    revision_up_count: Optional[int] = None
    revision_down_count: Optional[int] = None
    last_year_report_date: Optional[str] = None
    last_year_eps: Optional[float] = None
    currency: str = "USD"
    time_of_day: Optional[str] = None
    provider: str
    source_name: str
    source_url: Optional[str] = None
    data_as_of: Optional[str] = None
    days_until_report: Optional[int] = None
    is_date_confirmed: bool = False
    status: Literal["scheduled", "reported", "watchlist_template"] = "scheduled"
    confidence: Literal["confirmed", "estimated", "pending_provider"] = "estimated"
    watch_items: list[str] = Field(default_factory=list)
    focus_metrics: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    related_symbols: list[str] = Field(default_factory=list)


class EarningsCalendarResponse(BaseModel):
    events: list[EarningsCalendarEvent]
    provider: str
    fetched_at: str
    warnings: list[str] = Field(default_factory=list)


ShareholderChangeDirection = Literal["all", "increase", "decrease"]
ShareholderChangeMarket = Literal["A", "HK", "US"]
ShareholderChangeRecordDirection = Literal["increase", "decrease", "mixed", "unknown"]
ShareholderChangeStatus = Literal["plan", "progress", "completed", "other"]
ShareholderRiskLevel = Literal["low", "medium", "high"]
CnEarningsReportType = Literal[
    "annual",
    "semiannual",
    "q1",
    "q3",
    "forecast",
    "flash",
    "correction",
    "other",
]
CnEarningsRiskLevel = Literal["low", "medium", "high"]
CnEarningsDetailSource = Literal["title", "pdf", "unavailable"]
CnEarningsDetailQuality = Literal["full", "partial", "title_only"]
CnEarningsDiagnosisSignal = Literal["positive", "neutral", "negative", "watch"]
CnEarningsFinancialQuality = Literal["strong", "stable", "mixed", "weak", "unknown"]
MajorEventType = Literal[
    "control_change",
    "restructuring",
    "buyback",
    "equity_incentive",
    "pledge_freeze",
    "litigation_arbitration",
    "regulatory_penalty",
    "st_delisting",
    "abnormal_trading",
    "dividend",
    "financing",
    "related_transaction",
    "other",
]
MajorEventStatus = Literal["new", "progress", "completed", "risk", "other"]
MajorEventImpact = Literal["positive", "neutral", "negative", "mixed", "unknown"]
MajorEventRiskLevel = Literal["low", "medium", "high"]
MajorEventDetailSource = Literal["title", "pdf", "unavailable"]
MajorEventDetailQuality = Literal["full", "partial", "title_only"]


class ShareholderChangeScanRequest(BaseModel):
    market: ShareholderChangeMarket = "A"
    days: int = Field(default=30, ge=1, le=180)
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    direction: ShareholderChangeDirection = "all"
    limit: int = Field(default=80, ge=1, le=200)
    detail_limit: int = Field(default=12, ge=0, le=200)


class ShareholderChangeRecord(BaseModel):
    symbol: str
    name: str
    announcement_date: str
    direction: ShareholderChangeRecordDirection
    status: ShareholderChangeStatus
    shareholder_type: str = ""
    shareholder_hint: str = ""
    title: str
    url: str
    source: str = "cninfo"
    source_name: str = "巨潮资讯网"
    announcement_id: str = ""
    risk_level: ShareholderRiskLevel = "medium"
    tags: list[str] = Field(default_factory=list)
    shareholder_names: list[str] = Field(default_factory=list)
    change_shares: str = ""
    change_ratio: str = ""
    change_amount: str = ""
    price_range: str = ""
    change_period: str = ""
    change_method: str = ""
    holding_before: str = ""
    holding_after: str = ""
    change_reason: str = ""
    detail_summary: str = ""
    evidence_excerpt: str = ""
    detail_source: Literal["title", "pdf", "html", "xml", "unavailable"] = "title"
    detail_quality: Literal["full", "partial", "title_only"] = "title_only"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ShareholderChangeScanResponse(BaseModel):
    provider: str = "local-skill"
    model: str = "cn-shareholder-change-scan-v1"
    generated_at: datetime
    skill: str = "shareholder.change.scan"
    market: ShareholderChangeMarket = "A"
    direction: ShareholderChangeDirection
    start_date: str
    end_date: str
    total_found: int
    returned_count: int
    detail_attempted_count: int = 0
    detail_success_count: int = 0
    summary: str
    records: list[ShareholderChangeRecord]
    warnings: list[str] = Field(default_factory=list)
    coverage_note: str = "来源为巨潮资讯网公告检索；已对返回结果前排公告抓取 PDF 正文抽取股东、数量、比例、期间、方式、价格和金额字段；空字段代表公告未披露或版式未识别，原文链接保留用于核验。"
    skill_invocation: str = "shareholder.change.scan({ market: 'A', window: '30d', direction: 'all' })"


ShareholderInterpretTone = Literal["positive", "watch", "risk", "neutral"]


class ShareholderChangeInterpretRequest(BaseModel):
    record: ShareholderChangeRecord
    question: Optional[str] = None
    style: Literal["brief", "full"] = "brief"
    locale: str = "zh-CN"


class ShareholderChangeInterpretResponse(BaseModel):
    provider: str
    model: str
    generated_at: datetime
    skill: str = "shareholder.change.interpret"
    symbol: str
    name: str
    title: str
    tone: ShareholderInterpretTone = "neutral"
    verdict: str
    summary: str
    points: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    prompt_version: str = "shareholder-change-interpret-v1"
    confidence: float = Field(default=0.62, ge=0, le=1)
    disclaimer: str = "AI 解读仅供投研参考，不构成投资建议。"


class CnEarningsScanRequest(BaseModel):
    market: Literal["A"] = "A"
    days: int = Field(default=30, ge=1, le=180)
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    report_types: list[CnEarningsReportType] = Field(default_factory=list)
    limit: int = Field(default=120, ge=1, le=300)
    detail_limit: int = Field(default=12, ge=0, le=50)


class CnEarningsRecord(BaseModel):
    symbol: str
    name: str
    announcement_date: str
    report_type: CnEarningsReportType
    fiscal_year: str = ""
    fiscal_period: str = ""
    title: str
    url: str
    source: str = "cninfo"
    source_name: str = "巨潮资讯网"
    announcement_id: str = ""
    risk_level: CnEarningsRiskLevel = "medium"
    revenue: str = ""
    revenue_yoy: str = ""
    net_profit: str = ""
    net_profit_yoy: str = ""
    deducted_net_profit: str = ""
    deducted_net_profit_yoy: str = ""
    eps: str = ""
    roe: str = ""
    gross_margin: str = ""
    operating_cash_flow: str = ""
    total_assets: str = ""
    key_takeaways: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    evidence_excerpt: str = ""
    detail_source: CnEarningsDetailSource = "title"
    detail_quality: CnEarningsDetailQuality = "title_only"
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CnEarningsRecordDetailRequest(BaseModel):
    record: CnEarningsRecord


class CnEarningsRecordDetailResponse(BaseModel):
    provider: str = "local-skill"
    model: str = "cn-earnings-scan-v1"
    generated_at: datetime
    record: CnEarningsRecord
    warnings: list[str] = Field(default_factory=list)


class CnEarningsScanResponse(BaseModel):
    provider: str = "local-skill"
    model: str = "cn-earnings-scan-v1"
    generated_at: datetime
    skill: str = "cn.earnings.scan"
    market: Literal["A"] = "A"
    start_date: str
    end_date: str
    total_found: int
    returned_count: int
    detail_attempted_count: int = 0
    detail_success_count: int = 0
    summary: str
    records: list[CnEarningsRecord]
    warnings: list[str] = Field(default_factory=list)
    coverage_note: str = "来源为巨潮资讯网公告检索；已对返回结果前排财报 PDF 正文抽取营业收入、净利润、EPS、ROE、现金流等字段。字段为空代表公告未披露或版式未识别，原文链接保留用于核验。"
    skill_invocation: str = "cn.earnings.scan({ market: 'A', window: '30d', detail: true })"


class CnEarningsDiagnosisRequest(BaseModel):
    record: CnEarningsRecord
    question: Optional[str] = None
    style: Literal["brief", "full"] = "brief"
    locale: str = "zh-CN"


class CnEarningsDiagnosisAgentStep(BaseModel):
    agent: str
    role: str
    finding: str
    status: Literal["done", "watch", "risk"] = "done"


class CnEarningsDiagnosisResponse(BaseModel):
    provider: str
    model: str
    generated_at: datetime
    skill: str = "cn.earnings.diagnose"
    symbol: str
    name: str
    title: str
    report_type: CnEarningsReportType
    diagnosis_signal: CnEarningsDiagnosisSignal = "neutral"
    risk_level: CnEarningsRiskLevel = "medium"
    financial_quality: CnEarningsFinancialQuality = "unknown"
    overall_score: int = Field(default=50, ge=0, le=100)
    summary: str
    verdict: str
    agent_steps: list[CnEarningsDiagnosisAgentStep] = Field(default_factory=list)
    positives: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    prompt_version: str = "cn-earnings-diagnosis-v1"
    confidence: float = Field(default=0.62, ge=0, le=1)
    disclaimer: str = "AI 诊断仅供投研参考，不构成投资建议。"


class MajorEventScanRequest(BaseModel):
    market: Literal["A"] = "A"
    days: int = Field(default=30, ge=1, le=180)
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    event_types: list[MajorEventType] = Field(default_factory=list)
    limit: int = Field(default=120, ge=1, le=300)
    detail_limit: int = Field(default=12, ge=0, le=50)


class MajorEventRecord(BaseModel):
    symbol: str
    name: str
    announcement_date: str
    event_type: MajorEventType
    status: MajorEventStatus
    impact: MajorEventImpact = "unknown"
    risk_level: MajorEventRiskLevel = "medium"
    title: str
    url: str
    source: str = "cninfo"
    source_name: str = "巨潮资讯网"
    announcement_id: str = ""
    subject: str = ""
    amount: str = ""
    share_ratio: str = ""
    progress: str = ""
    deadline: str = ""
    detail_summary: str = ""
    key_points: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)
    evidence_excerpt: str = ""
    tags: list[str] = Field(default_factory=list)
    detail_source: MajorEventDetailSource = "title"
    detail_quality: MajorEventDetailQuality = "title_only"
    metadata: dict[str, Any] = Field(default_factory=dict)


class MajorEventScanResponse(BaseModel):
    provider: str = "local-skill"
    model: str = "cn-major-event-scan-v1"
    generated_at: datetime
    skill: str = "cn.major_event.scan"
    market: Literal["A"] = "A"
    start_date: str
    end_date: str
    total_found: int
    returned_count: int
    detail_attempted_count: int = 0
    detail_success_count: int = 0
    summary: str
    records: list[MajorEventRecord]
    warnings: list[str] = Field(default_factory=list)
    coverage_note: str = "来源为巨潮资讯网公告检索；已对返回结果前排公告抓取 PDF 正文，抽取事件主体、金额/比例、进展、期限、关键看点和风险提示。空字段代表公告未披露或版式未识别，原文链接保留用于核验。"
    skill_invocation: str = "cn.major_event.scan({ market: 'A', window: '30d', detail: true })"


TaskStatus = Literal["pending", "running", "waiting_approval", "failed", "completed", "cancelled"]
AgentEngine = Literal["deepfocus", "tradingagents", "financial_services"]


class InvestmentTaskCreateRequest(BaseModel):
    title: str
    symbol: Optional[str] = None
    asset_name: Optional[str] = None
    task_type: Literal[
        "investment_research",
        "portfolio_review",
        "risk_review",
        "watchlist_monitor",
        "customs_trade_analysis",
    ] = "investment_research"
    engine: AgentEngine = "deepfocus"
    horizon: str = "1-4周"
    investor_profile: Literal["保守", "稳健", "进取", "专业"] = "稳健"
    objective: str = "判断是否值得进一步研究，并给出风险和观察清单。"
    context: str = ""
    analysis_domain: Optional[str] = None
    customs_focus: Optional[str] = None
    customs_focus_key: Optional[str] = None
    customs_focus_type: Optional[str] = None
    customs_selected_tab: Optional[str] = None
    engine_config: dict[str, Any] = Field(default_factory=dict)
    priority: int = Field(default=3, ge=1, le=5)


class AgentLogEntry(BaseModel):
    timestamp: str
    agent: str
    message: str
    progress: Optional[int] = Field(default=None, ge=0, le=100)


class InvestmentTaskRecord(BaseModel):
    id: str
    title: str
    symbol: Optional[str] = None
    asset_name: Optional[str] = None
    task_type: str
    engine: AgentEngine = "deepfocus"
    status: TaskStatus
    priority: int
    assigned_agent: Optional[str] = None
    progress: int = Field(ge=0, le=100)
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None
    input: dict
    logs: list[AgentLogEntry] = Field(default_factory=list)
    result: Optional[dict] = None


AgentRunEventType = Literal[
    "run_state",
    "reasoning_delta",
    "tool_start",
    "tool_progress",
    "tool_result",
    "approval_required",
    "artifact_update",
    "run_complete",
    "error",
]
AgentRunEventSurface = Literal["text", "block", "timeline", "control"]


class AgentRunEvent(BaseModel):
    id: str
    task_id: str
    type: AgentRunEventType
    surface: AgentRunEventSurface
    phase: str = "orchestrator"
    agent: str = "OrchestratorAgent"
    title: str
    message: str = ""
    progress: Optional[int] = Field(default=None, ge=0, le=100)
    created_at: str
    payload: dict[str, Any] = Field(default_factory=dict)


class InvestmentTaskListResponse(BaseModel):
    tasks: list[InvestmentTaskRecord]


class AgentRuntimeHealthResponse(BaseModel):
    status: str
    worker_running: bool
    pending: int
    running: int
    completed: int
    failed: int


ReadinessStatus = Literal["pass", "warn", "fail"]


class SystemReadinessCheck(BaseModel):
    key: str
    name: str
    status: ReadinessStatus
    detail: str
    remediation: Optional[str] = None


class SystemReadinessResponse(BaseModel):
    status: Literal["ready", "degraded", "not_ready"]
    score: int = Field(ge=0, le=100)
    generated_at: datetime
    checks: list[SystemReadinessCheck]
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class NewsItem(BaseModel):
    title: str
    summary: Optional[str] = None
    source: Optional[str] = None
    published_at: Optional[str] = None


class NewsSummaryRequest(BaseModel):
    stock: Optional[StockSnapshot] = None
    items: list[NewsItem] = Field(default_factory=list)
    focus: Optional[str] = None
    locale: str = "zh-CN"


class ReportAnalysisRequest(BaseModel):
    title: Optional[str] = None
    report_text: str
    stock: Optional[StockSnapshot] = None
    locale: str = "zh-CN"


class CustomsTradeAnalysisRequest(BaseModel):
    focus: Optional[str] = None
    focus_key: Optional[str] = None
    focus_type: Optional[str] = None
    selected_tab: Optional[str] = None
    locale: str = "zh-CN"


class RagDocument(BaseModel):
    source: str
    text: str


class RagQueryRequest(BaseModel):
    question: str
    documents: list[RagDocument] = Field(default_factory=list)
    locale: str = "zh-CN"


class ForecastRequest(BaseModel):
    stock: StockSnapshot
    horizon: str = "1周"
    context: Optional[str] = None
    posts: list[PostSnapshot] = Field(default_factory=list)
    locale: str = "zh-CN"


class CorridorRiskRequest(BaseModel):
    corridor_code: str = "US"
    asset: str = "USDC"
    news_items: list[NewsItem] = Field(default_factory=list)
    locale: str = "zh-CN"


class AgentBriefRequest(BaseModel):
    role: Literal[
        "ops_oversight",
        "audit_governance",
        "process_improvement",
        "internal_support",
        "treasury_strategy",
    ]
    context: str
    locale: str = "zh-CN"


class GeneralChatRequest(BaseModel):
    message: str
    history: list[dict[str, str]] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    locale: str = "zh-CN"


class GeneralChatResponse(BaseModel):
    provider: str
    model: str
    generated_at: datetime
    title: str = "DeepFocus"
    content: str


class OrchestratorChatRequest(BaseModel):
    message: str
    history: list[dict[str, str]] = Field(default_factory=list)
    engine: AgentEngine = "deepfocus"
    mode: Literal["research", "risk", "portfolio", "monitor"] = "research"
    stock: Optional[StockSnapshot] = None
    attached_files: list[str] = Field(default_factory=list)
    data_source_count: int = 0
    mcp_server_count: int = 0
    reasoning_mode: Literal["fast", "thinking"] = "thinking"
    locale: str = "zh-CN"


class OrchestratorReasoningStep(BaseModel):
    phase: str
    title: str
    detail: str
    status: Literal["done", "working", "wait", "error"] = "done"


class OrchestratorChatResponse(BaseModel):
    provider: str
    model: str
    generated_at: datetime
    agent: str = "OrchestratorAgent"
    engine: AgentEngine = "deepfocus"
    title: str
    content: str
    chips: list[str] = Field(default_factory=list)
    suggested_actions: list[str] = Field(default_factory=list)
    reasoning_trace: list[OrchestratorReasoningStep] = Field(default_factory=list)
    should_create_task: bool = False
    handled_inline: bool = False
    confidence: float = Field(default=0.6, ge=0, le=1)


class FinGptTaskResponse(BaseModel):
    provider: str
    model: str
    generated_at: datetime
    capability: str
    title: str
    summary: str
    key_points: list[str] = Field(default_factory=list)
    signals: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)
    disclaimer: str = "仅供投研和运营参考，不构成投资建议、支付建议或合规结论。"


class StockCheckRequest(BaseModel):
    stock: StockSnapshot
    posts: list[PostSnapshot] = Field(default_factory=list)
    question: Optional[str] = None
    horizon: str = "1周"
    locale: str = "zh-CN"


class StockCheckStep(BaseModel):
    key: str
    name: str
    status: Literal["completed", "failed", "skipped"]
    detail: str = ""


class StockCheckResponse(BaseModel):
    provider: str
    model: str
    generated_at: datetime
    stock: StockSnapshot
    verdict: Literal["重点跟踪", "谨慎观察", "暂不行动"]
    score: int = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    summary: str
    action_items: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    checks: list[StockCheckStep] = Field(default_factory=list)
    stock_analysis: Optional[StockAnalysisResponse] = None
    sentiment: Optional[SentimentResponse] = None
    news_summary: Optional[FinGptTaskResponse] = None
    report_analysis: Optional[FinGptTaskResponse] = None
    rag_answer: Optional[FinGptTaskResponse] = None
    forecast: Optional[FinGptTaskResponse] = None
    agent_brief: Optional[FinGptTaskResponse] = None
    warnings: list[str] = Field(default_factory=list)
    disclaimer: str = "一键检测为多能力自动汇总，只能作为投研线索和复核清单，不构成投资建议。"


class DataSourceItemInterpretRequest(BaseModel):
    persist: bool = True


class DataSourceItemInterpretResponse(BaseModel):
    item: DataSourceItemRecord
    interpretation: str
    result: FinGptTaskResponse


ProfessionalReportType = Literal["annual", "semiannual", "quarterly", "research", "transcript", "other"]
ProfessionalCitationKind = Literal["chunk", "metric"]


class ProfessionalReportIngestRequest(BaseModel):
    data_item_id: str
    symbol: Optional[str] = None
    title: Optional[str] = None
    report_type: ProfessionalReportType = "other"
    period: Optional[str] = None
    tags: list[str] = Field(default_factory=list)


class ProfessionalWorkbenchFileIngestRequest(BaseModel):
    filename: str
    out: str = "downloads/海外投行报告"
    symbol: Optional[str] = None
    title: Optional[str] = None
    report_type: ProfessionalReportType = "research"
    period: Optional[str] = None
    tags: list[str] = Field(default_factory=list)


class ProfessionalReportRecord(BaseModel):
    id: str
    source_item_id: Optional[str] = None
    title: str
    symbol: Optional[str] = None
    report_type: str = "other"
    period: Optional[str] = None
    parser: str
    char_count: int
    metadata: dict[str, Any] = Field(default_factory=dict)
    metrics_count: int = 0
    chunks_count: int = 0
    created_at: str
    updated_at: str


class ProfessionalReportListResponse(BaseModel):
    reports: list[ProfessionalReportRecord]


class ProfessionalReportChunkRecord(BaseModel):
    id: str
    report_id: str
    chunk_index: int
    page: Optional[int] = None
    title: str
    text: str
    token_count: int
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class ProfessionalReportChunkListResponse(BaseModel):
    chunks: list[ProfessionalReportChunkRecord]


class ProfessionalMetricRecord(BaseModel):
    id: str
    report_id: str
    symbol: Optional[str] = None
    period: Optional[str] = None
    metric_key: str
    metric_label: str
    value: Optional[float] = None
    normalized_value: Optional[float] = None
    unit: Optional[str] = None
    raw_value: str
    source_page: Optional[int] = None
    source_excerpt: str
    confidence: float = Field(ge=0, le=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class ProfessionalMetricListResponse(BaseModel):
    metrics: list[ProfessionalMetricRecord]


class ProfessionalCitation(BaseModel):
    citation_id: str
    kind: ProfessionalCitationKind
    source_id: str
    report_id: str
    report_title: str = ""
    title: str
    page: Optional[int] = None
    text: str
    score: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProfessionalRagQueryRequest(BaseModel):
    question: str
    symbol: Optional[str] = None
    report_id: Optional[str] = None
    period: Optional[str] = None
    top_k: int = Field(default=5, ge=1, le=12)
    use_cloud_model: bool = True


class ProfessionalRagQueryResponse(BaseModel):
    answer: str
    citations: list[ProfessionalCitation] = Field(default_factory=list)
    metrics: list[ProfessionalMetricRecord] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0, le=1)
    missing: list[str] = Field(default_factory=list)
    disclaimer: str = "仅供投研参考，不构成投资建议。"


class ProfessionalReportAnalysisRequest(BaseModel):
    focus: Optional[str] = None
    use_cloud_model: bool = True


class ProfessionalReportAnalysisResponse(BaseModel):
    report: ProfessionalReportRecord
    summary: str
    key_metrics: list[ProfessionalMetricRecord] = Field(default_factory=list)
    quality_flags: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    follow_up_questions: list[str] = Field(default_factory=list)
    citations: list[ProfessionalCitation] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0, le=1)
    disclaimer: str = "仅供投研参考，不构成投资建议。"


class ProfessionalEvalCase(BaseModel):
    question: str
    expected_text: Optional[str] = None
    expected_metric_key: Optional[str] = None
    expected_refusal: bool = False
    must_cite: bool = True


class ProfessionalEvalRunRequest(BaseModel):
    report_id: Optional[str] = None
    symbol: Optional[str] = None
    top_k: int = Field(default=5, ge=1, le=12)
    cases: list[ProfessionalEvalCase] = Field(default_factory=list)


class ProfessionalEvalCaseResult(BaseModel):
    question: str
    expected_text: Optional[str] = None
    expected_metric_key: Optional[str] = None
    expected_refusal: bool = False
    answer: str
    citations: list[dict[str, Any]] = Field(default_factory=list)
    citations_present: bool
    answer_match: bool
    refusal_ok: bool
    passed: bool
    score: float = Field(ge=0, le=1)
    notes: list[str] = Field(default_factory=list)


class ProfessionalEvalRunResponse(BaseModel):
    generated_at: str
    total: int
    passed: int
    pass_rate: float = Field(ge=0, le=1)
    citation_rate: float = Field(ge=0, le=1)
    answer_match_rate: float = Field(ge=0, le=1)
    refusal_guard_rate: float = Field(ge=0, le=1)
    cases: list[ProfessionalEvalCaseResult] = Field(default_factory=list)
