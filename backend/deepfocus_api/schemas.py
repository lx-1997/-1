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

DataQualityLevel = Literal["live", "degraded", "mock"]


class DataQuality(BaseModel):
    """统一的数据可信度标注，供前端显式提示数据真伪。

    - live：真实数据 + 云端模型，可正常参考。
    - degraded：真实数据但已降级（云端模型不可用，使用本地规则/模板兜底）。
    - mock：本地演示/合成数据，仅用于界面演示，不能作为投资依据。
    """

    level: DataQualityLevel = "live"
    label: str = ""
    detail: str = ""
    reasons: list[str] = Field(default_factory=list)


# provider 取值 → 可信度等级：mock=本地演示；local-* / 模板 / 兜底=降级；其余=真实云端。
_DEGRADED_PROVIDERS = {"none", "template", "watchlist_template", "synthetic", "fallback"}


def classify_data_quality(provider: str | None, *, reasons: list[str] | None = None) -> DataQuality:
    """根据响应的 provider 推断数据可信度。

    provider 在各产出路径已被可靠标记（mock 路径为 'mock'，云端失败兜底为 'local-rule' 等），
    因此统一在出口按 provider 判定，避免把"这是不是真数据"的判断散落到各处。
    """
    value = (provider or "").strip().lower()
    extra = [r for r in (reasons or []) if r]
    if value == "mock":
        return DataQuality(
            level="mock",
            label="演示数据",
            detail="当前为本地演示模型（mock），以下内容为示例，不能作为投资依据。请在「设置 → 模型配置」接入云端模型以获取真实分析。",
            reasons=extra,
        )
    if value.startswith("local") or value in _DEGRADED_PROVIDERS:
        return DataQuality(
            level="degraded",
            label="降级兜底",
            detail="云端模型或实时数据暂不可用，以下为本地规则/兜底结果，仅供参考，请谨慎使用。",
            reasons=extra,
        )
    return DataQuality(level="live", reasons=extra)


def attach_data_quality(response: Any, *, reasons: list[str] | None = None) -> Any:
    """读 response.provider 推断可信度并写入 response.data_quality，统一在接口出口调用一次即可。"""
    if response is None or not hasattr(response, "data_quality"):
        return response
    response.data_quality = classify_data_quality(getattr(response, "provider", None), reasons=reasons)
    return response


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
    data_quality: DataQuality = Field(default_factory=DataQuality)


class SentimentRequest(BaseModel):
    text: str
    locale: str = "zh-CN"


class SentimentResponse(BaseModel):
    provider: str
    model: str
    label: SentimentLabel
    score: float = Field(ge=-1, le=1)
    rationale: str
    data_quality: DataQuality = Field(default_factory=DataQuality)


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


OfficialNewsSource = Literal["xinwenlianbo", "cctv-news", "cctv-economy"]


class OfficialNewsItem(BaseModel):
    id: str
    title: str
    summary: str = ""
    url: str
    source: OfficialNewsSource
    source_name: str
    section: str = ""
    published_at: Optional[str] = None
    reported_date: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    importance_score: int = Field(default=50, ge=0, le=100)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OfficialNewsResponse(BaseModel):
    provider: str = "cctv.com"
    generated_at: str
    source: OfficialNewsSource
    source_name: str
    source_url: str
    total_found: int
    returned_count: int
    cache_age_seconds: int = 0
    warnings: list[str] = Field(default_factory=list)
    items: list[OfficialNewsItem]


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
    data_quality: DataQuality = Field(default_factory=DataQuality)


class MarketSymbolSearchResponse(BaseModel):
    query: str
    market: Optional[MarketRegion] = None
    candidates: list[MarketSymbolCandidate]
    provider: str
    fetched_at: str
    warnings: list[str] = Field(default_factory=list)


MarketDataLayerKind = Literal["free_quote", "structured_ashare", "sentiment"]
MarketDataLayerState = Literal["ready", "partial", "fallback", "unconfigured", "error"]


class MarketDataLayerStatus(BaseModel):
    key: MarketDataLayerKind
    name: str
    status: MarketDataLayerState
    providers: list[str] = Field(default_factory=list)
    primary_provider: Optional[str] = None
    configured: bool = True
    trust_level: TrustLevel = "unknown"
    coverage: str = ""
    detail: str = ""
    remediation: Optional[str] = None
    latency_ms: Optional[float] = None
    warnings: list[str] = Field(default_factory=list)


class MarketDataLayerStatusResponse(BaseModel):
    generated_at: str
    symbol: Optional[str] = None
    keyword: Optional[str] = None
    layers: list[MarketDataLayerStatus]


class AShareStructuredDataResponse(BaseModel):
    symbol: str
    ts_code: str
    provider: str = "tushare"
    provider_name: str = "Tushare Pro"
    configured: bool
    status: Literal["ready", "unconfigured", "empty", "error"]
    fetched_at: str
    stock_basic: dict[str, Any] = Field(default_factory=dict)
    daily: list[dict[str, Any]] = Field(default_factory=list)
    daily_basic: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PremarketMappedAsset(BaseModel):
    symbol: str
    name: str
    market: MarketRegion
    role: str = "映射标的"
    reason: str = ""


class PremarketQuoteSignal(BaseModel):
    symbol: str
    name: str = ""
    market: MarketRegion = "US"
    price: Optional[float] = None
    change_percent: Optional[float] = None
    provider: str = ""
    role: str = ""


class PremarketOptionSignal(BaseModel):
    symbol: str
    forecast_label: str
    forecast_score: int = Field(ge=0, le=100)
    tail_event_risk_level: Literal["绿灯", "黄灯", "橙灯", "红灯"]
    tail_event_risk_score: int = Field(ge=0, le=100)
    summary: str = ""


class PremarketThemeSignal(BaseModel):
    theme_key: str
    theme_name: str
    stance: Literal["重点机会", "观察机会", "中性观察", "风险回避"]
    direction: Literal["上行", "下行", "震荡"]
    opportunity_score: int = Field(ge=0, le=100)
    risk_score: int = Field(ge=0, le=100)
    confidence: Literal["高", "中", "低"]
    us_impulse_score: int = Field(ge=0, le=100)
    option_confirmation_score: int = Field(ge=0, le=100)
    china_confirmation_score: int = Field(ge=0, le=100)
    leader_quotes: list[PremarketQuoteSignal] = Field(default_factory=list)
    option_signals: list[PremarketOptionSignal] = Field(default_factory=list)
    mapped_assets: list[PremarketMappedAsset] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    triggers: list[str] = Field(default_factory=list)
    invalidations: list[str] = Field(default_factory=list)
    suggested_action: str = ""


class PremarketOpportunityResponse(BaseModel):
    provider: str = "local-rule"
    model: str = "cross-market-opportunity-v1"
    generated_at: datetime
    market_phase: str
    regime_score: int = Field(ge=0, le=100)
    summary: str
    themes: list[PremarketThemeSignal]
    risk_watchlist: list[PremarketThemeSignal] = Field(default_factory=list)
    confirmation_checklist: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    disclaimer: str = "跨市场机会雷达仅供盘前研究和风控参考，不构成投资建议；必须等待 A 股竞价和开盘确认。"
    data_quality: DataQuality = Field(default_factory=DataQuality)


OptionsSourceStatusValue = Literal["ready", "fallback", "blocked", "unavailable"]
OptionsSignalStatus = Literal["delayed", "partial", "unavailable"]
OptionsDirection = Literal["偏多", "中性", "偏空", "不可判定"]
OptionsConviction = Literal["高", "中", "低"]
OptionsTailEventRiskLevel = Literal["绿灯", "黄灯", "橙灯", "红灯"]
OptionsForecastLabel = Literal["强看涨", "看涨", "震荡偏强", "震荡", "震荡偏弱", "看跌", "高风险回避", "不可判定"]
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


class OptionsGammaStrike(BaseModel):
    strike: float
    net_gamma_exposure: float = 0
    call_gamma_exposure: float = 0
    put_gamma_exposure: float = 0
    total_open_interest: float = 0
    distance_pct: Optional[float] = None


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
    gamma_exposure_status: Literal["available", "estimated", "unavailable"] = "unavailable"
    net_gamma_exposure: float = 0
    call_gamma_exposure: float = 0
    put_gamma_exposure: float = 0
    gamma_wall: Optional[float] = None
    negative_gamma_wall: Optional[float] = None
    zero_gamma_estimate: Optional[float] = None
    gamma_strikes: list[OptionsGammaStrike] = Field(default_factory=list)
    tail_event_risk_score: int = Field(default=0, ge=0, le=100)
    tail_event_risk_level: OptionsTailEventRiskLevel = "绿灯"
    tail_event_risk_summary: str = "暂无明显左尾事件预警。"
    tail_event_risk_reasons: list[str] = Field(default_factory=list)
    tail_event_risk_actions: list[str] = Field(default_factory=list)
    forecast_score: int = Field(default=50, ge=0, le=100)
    forecast_label: OptionsForecastLabel = "不可判定"
    forecast_confidence: OptionsConviction = "低"
    forecast_horizon: str = "1-5个交易日"
    forecast_summary: str = "走势预判待补充。"
    forecast_reasons: list[str] = Field(default_factory=list)
    forecast_actions: list[str] = Field(default_factory=list)
    forecast_invalidations: list[str] = Field(default_factory=list)
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
    data_quality: DataQuality = Field(default_factory=DataQuality)


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
    data_quality: DataQuality = Field(default_factory=DataQuality)


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
    data_quality: DataQuality = Field(default_factory=DataQuality)


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
    data_quality: DataQuality = Field(default_factory=DataQuality)


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
    agent: str = "Orchestrator"
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
    data_quality: DataQuality = Field(default_factory=DataQuality)


class ModuleContextChatRequest(BaseModel):
    message: str
    history: list[dict[str, str]] = Field(default_factory=list)
    module_context: dict[str, Any] = Field(default_factory=dict)
    locale: str = "zh-CN"


class OrchestratorChatRequest(BaseModel):
    message: str
    history: list[dict[str, str]] = Field(default_factory=list)
    engine: AgentEngine = "deepfocus"
    mode: Literal["research", "risk", "portfolio", "monitor"] = "research"
    stock: Optional[StockSnapshot] = None
    attached_files: list[str] = Field(default_factory=list)
    data_source_count: int = 0
    mcp_server_count: int = 0
    include_macro: bool = True
    include_risk: bool = True
    include_evidence: bool = True
    include_metrics: bool = True
    include_supply_chain: bool = False
    include_trade: bool = False
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
    agent: str = "Orchestrator"
    engine: AgentEngine = "deepfocus"
    title: str
    content: str
    chips: list[str] = Field(default_factory=list)
    suggested_actions: list[str] = Field(default_factory=list)
    reasoning_trace: list[OrchestratorReasoningStep] = Field(default_factory=list)
    should_create_task: bool = False
    handled_inline: bool = False
    confidence: float = Field(default=0.6, ge=0, le=1)
    data_quality: DataQuality = Field(default_factory=DataQuality)


DulusProviderMode = Literal["openai_compatible", "local_mock", "webbridge_disabled"]
DulusProviderStatus = Literal["ready", "needs_config", "disabled"]
DulusToolCategory = Literal["filesystem", "shell", "search", "browser", "memory", "mcp", "finance", "risk", "report"]
DulusToolPermission = Literal["read_only", "approval_required", "disabled"]
DulusRoundtableMode = Literal["fast", "debate", "deep_research"]
DulusTurnStance = Literal["evidence", "research", "risk", "operator", "synthesis"]
DulusTraceStatus = Literal["completed", "skipped", "blocked"]


class DulusProviderRecord(BaseModel):
    id: str
    name: str
    mode: DulusProviderMode
    model: str
    status: DulusProviderStatus
    latency_hint: str
    risk_level: Literal["low", "medium", "high"] = "low"
    notes: str = ""


class DulusToolRecord(BaseModel):
    id: str
    name: str
    category: DulusToolCategory
    description: str
    permission: DulusToolPermission
    enabled: bool = True
    risk_level: Literal["low", "medium", "high"] = "low"
    invocation: str


class DulusRuntimeStatusResponse(BaseModel):
    generated_at: datetime
    compliant_mode: bool = True
    provider: str
    model: str
    providers: list[DulusProviderRecord]
    tools: list[DulusToolRecord]
    webbridge_policy: str
    memory_scope: str
    warnings: list[str] = Field(default_factory=list)


class DulusRoundtableRequest(BaseModel):
    objective: str
    context: str = ""
    stock: Optional[StockSnapshot] = None
    participants: list[str] = Field(default_factory=list)
    enabled_tools: list[str] = Field(default_factory=list)
    authorized_webbridge_url: Optional[str] = None
    mode: DulusRoundtableMode = "debate"
    locale: str = "zh-CN"


class DulusToolTrace(BaseModel):
    tool: str
    title: str
    input: str = ""
    output: str
    status: DulusTraceStatus = "completed"


class DulusAgentTurn(BaseModel):
    participant_id: str
    participant_name: str
    provider: str
    model: str
    stance: DulusTurnStance
    content: str
    key_points: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)
    tool_traces: list[DulusToolTrace] = Field(default_factory=list)


class DulusRoundtableResponse(BaseModel):
    provider: str
    model: str
    generated_at: datetime
    mode: DulusRoundtableMode
    objective: str
    turns: list[DulusAgentTurn]
    synthesis: str
    decision: Literal["candidate", "watch", "research_more", "blocked"]
    memory_notes: list[str] = Field(default_factory=list)
    tool_traces: list[DulusToolTrace] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)
    disclaimer: str = "合规版 Dulus Runtime 不捕获第三方网页会话；输出仅供投研和工作流参考。"


class DulusMemoryCreateRequest(BaseModel):
    scope: Literal["session", "project", "user"] = "session"
    hall: str = "events"
    title: str
    content: str
    tags: list[str] = Field(default_factory=list)
    source: str = "manual"


class DulusMemoryRecord(BaseModel):
    id: str
    scope: str
    hall: str
    title: str
    content: str
    tags: list[str] = Field(default_factory=list)
    source: str
    created_at: str


class DulusMemoryListResponse(BaseModel):
    memories: list[DulusMemoryRecord]


class DulusWebBridgeInspectRequest(BaseModel):
    url: str
    mode: Literal["text", "dom"] = "text"


class DulusWebBridgeInspectResponse(BaseModel):
    url: str
    allowed: bool
    policy: str
    title: str = ""
    text_preview: str = ""
    links: list[str] = Field(default_factory=list)
    fetched_at: datetime


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
    data_quality: DataQuality = Field(default_factory=DataQuality)


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


class ProfessionalReportUrlIngestRequest(BaseModel):
    url: str
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


PositionDirection = Literal["long", "short"]
PositionAssetClass = Literal["equity", "option", "future", "forex", "bond", "crypto", "other"]
PositionStatus = Literal["open", "closed"]
RiskAlertLevel = Literal["info", "warning", "danger"]
GreeksType = Literal["call", "put"]


class GreeksRequest(BaseModel):
    underlying_price: float = Field(gt=0)
    strike: float = Field(gt=0)
    days_to_expiry: float = Field(gt=0)
    risk_free_rate: float = 0.05
    implied_vol: float = 0.3
    option_type: GreeksType = "call"


class GreeksResponse(BaseModel):
    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float
    iv: float
    theoretical_price: float


class PositionCreateRequest(BaseModel):
    symbol: str
    name: str = ""
    market: MarketRegion = "US"
    asset_class: PositionAssetClass = "equity"
    direction: PositionDirection = "long"
    entry_price: float = Field(ge=0)
    quantity: float = Field(ge=0)
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    position_size_pct: float = Field(default=0, ge=0, le=100)
    sector: str = ""
    strategy: str = ""
    notes: str = ""
    tags: list[str] = Field(default_factory=list)
    greeks: Optional[dict[str, float]] = None


class PositionUpdateRequest(BaseModel):
    name: Optional[str] = None
    market: Optional[MarketRegion] = None
    asset_class: Optional[PositionAssetClass] = None
    direction: Optional[PositionDirection] = None
    entry_price: Optional[float] = Field(default=None, ge=0)
    quantity: Optional[float] = Field(default=None, ge=0)
    current_price: Optional[float] = Field(default=None, ge=0)
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    position_size_pct: Optional[float] = Field(default=None, ge=0, le=100)
    sector: Optional[str] = None
    strategy: Optional[str] = None
    notes: Optional[str] = None
    tags: Optional[list[str]] = None
    greeks: Optional[dict[str, float]] = None


class PositionCloseRequest(BaseModel):
    exit_price: float = Field(ge=0)
    exit_reason: str = ""


class PositionRecord(BaseModel):
    id: str
    symbol: str
    name: str
    market: str
    asset_class: str
    direction: str
    entry_price: float
    entry_date: str
    quantity: float
    current_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    position_size_pct: float = 0
    sector: str = ""
    strategy: str = ""
    notes: str = ""
    tags: list[str] = Field(default_factory=list)
    greeks: dict[str, float] = Field(default_factory=dict)
    created_at: str
    updated_at: str
    closed_at: Optional[str] = None
    status: str
    # 逐仓风险指标（由 calculate_position_risk 计算后并入；可选，缺省 None）
    unrealized_pnl: Optional[float] = None
    unrealized_pnl_pct: Optional[float] = None
    notional_value: Optional[float] = None
    cost_basis: Optional[float] = None
    risk_reward_ratio: Optional[float] = None
    distance_to_stop_pct: Optional[float] = None
    distance_to_target_pct: Optional[float] = None


class PositionRiskMetrics(BaseModel):
    unrealized_pnl: float
    unrealized_pnl_pct: float
    notional_value: float
    cost_basis: float
    risk_reward_ratio: float
    distance_to_stop_pct: float
    distance_to_target_pct: float
    risk_per_share: float
    reward_per_share: float


class PositionListResponse(BaseModel):
    positions: list[PositionRecord]


class PortfolioDirectionExposure(BaseModel):
    long: float
    short: float
    net: float
    gross: float


class PortfolioMetrics(BaseModel):
    total_value: float
    total_cost: float
    total_pnl: float
    total_pnl_pct: float
    position_count: int
    open_count: int
    win_rate: float
    sector_exposure: dict[str, float] = Field(default_factory=dict)
    asset_class_exposure: dict[str, float] = Field(default_factory=dict)
    direction_exposure: PortfolioDirectionExposure
    concentration_risk: str
    risk_flags: list[str] = Field(default_factory=list)


class VaRResult(BaseModel):
    historical_var: float
    parametric_var: float
    var_amount: float = 0
    var_pct: float
    confidence: float
    observations: int
    mean_return: float = 0
    std_dev: float = 0
    sharpe_ratio: float = 0
    method: str


class RiskAlert(BaseModel):
    level: RiskAlertLevel
    type: str
    message: str
    metric: str
    value: float = 0
    limit: float = 0
    symbol: Optional[str] = None


class RiskLimitRecord(BaseModel):
    id: str
    key: str
    label: str
    value: float
    unit: str
    enabled: bool
    description: str
    created_at: str
    updated_at: str


class RiskLimitUpdateRequest(BaseModel):
    value: float = Field(ge=0)  # 风险限额均为非负阈值，拒绝负值（如 max_leverage=-999）
    enabled: Optional[bool] = None


class RiskSummaryResponse(BaseModel):
    portfolio: PortfolioMetrics
    var: VaRResult
    open_positions: list[PositionRecord]
    closed_positions_count: int
    alerts: list[RiskAlert]
    risk_limits: dict[str, RiskLimitRecord]
    generated_at: str


class PnlRecord(BaseModel):
    id: str
    position_id: str
    symbol: str
    date: str
    entry_price: float
    exit_price: Optional[float] = None
    quantity: float
    realized_pnl: float = 0
    unrealized_pnl: float = 0
    total_pnl: float = 0
    return_pct: float = 0
    holding_days: int = 0
    exit_reason: str = ""
    created_at: str


class PnlSummaryResponse(BaseModel):
    total_realized_pnl: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    total_return_pct: float
    best_trade: Optional[PnlRecord] = None
    worst_trade: Optional[PnlRecord] = None
    avg_holding_days: float


BacktestStrategyType = Literal[
    "momentum", "mean_reversion", "trend_following", "breakout",
    "grid", "martingale", "arbitrage", "pairs_trading", "custom",
]
BacktestStatus = Literal["pending", "running", "completed", "failed"]


class BacktestCreateRequest(BaseModel):
    name: str
    market: MarketRegion = "US"
    strategy_type: BacktestStrategyType = "momentum"
    symbols: list[str] = Field(default_factory=list)
    start_date: str = ""
    end_date: str = ""
    initial_capital: float = Field(default=100000, ge=1000)
    benchmark: str = "SPY"
    parameters: dict[str, Any] = Field(default_factory=dict)


class BacktestRecord(BaseModel):
    id: str
    name: str
    market: str
    strategy_type: str
    symbols: list[str] = Field(default_factory=list)
    start_date: str
    end_date: str
    initial_capital: float
    benchmark: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    status: str
    progress: int = Field(ge=0, le=100)
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    created_at: str
    updated_at: str
    completed_at: Optional[str] = None


class BacktestListResponse(BaseModel):
    backtests: list[BacktestRecord]


class BacktestMetricsRequest(BaseModel):
    equity_curve: list[float] = Field(min_length=2)  # 至少 2 个净值点，否则 422
    benchmark_curve: Optional[list[float]] = None
    initial_capital: float = Field(default=100000, gt=0)


class BacktestMetricsResponse(BaseModel):
    total_return: float
    total_return_pct: float
    annualized_return: float
    annualized_volatility: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: float
    max_drawdown_pct: float
    calmar_ratio: float
    win_rate: float
    profit_factor: float
    total_trades: int
    avg_trade_return: float
    benchmark_return: float
    alpha: float
    beta: float
    information_ratio: float


IndicatorSignal = Literal["strong_bullish", "bullish", "neutral", "bearish", "strong_bearish"]


class MarketIndicator(BaseModel):
    key: str
    name: str
    category: str
    value: float
    unit: str = ""
    change: Optional[float] = None
    change_pct: Optional[float] = None
    signal: IndicatorSignal = "neutral"
    status: str = ""
    description: str = ""
    historical_context: str = ""
    current_reading: str = ""
    source: str = ""


class DashboardCategory(BaseModel):
    key: str
    name: str
    indicators: list[MarketIndicator]


class MarketDashboardResponse(BaseModel):
    generated_at: str
    overall_signal: str
    overall_score: int = Field(ge=0, le=100)
    overall_summary: str
    total_indicators: int
    available_indicators: int
    categories: list[DashboardCategory]


class DashboardAnalysisResponse(BaseModel):
    title: str = ""
    summary: str = ""
    key_points: list[str] = Field(default_factory=list)
    signals: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0, ge=0, le=1)


class CrossModuleResearchRequest(BaseModel):
    symbol: str = ""
    include_macro: bool = True
    include_risk: bool = True
    include_evidence: bool = True
    include_metrics: bool = True
    include_supply_chain: bool = False
    include_trade: bool = False


class CrossModuleResearchResponse(BaseModel):
    symbol: str
    generated_at: str
    modules_available: list[str] = Field(default_factory=list)
    quotes: dict[str, Any] = Field(default_factory=dict)
    macro: dict[str, Any] = Field(default_factory=dict)
    risk: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    supply_chain: dict[str, Any] = Field(default_factory=dict)
    trade: dict[str, Any] = Field(default_factory=dict)
