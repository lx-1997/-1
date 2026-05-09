from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class StockSnapshot(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    symbol: str
    name: str
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
TrustLevel = Literal["internal", "official", "media", "community", "unknown"]
KeywordCrawlProvider = Literal["xueqiu", "wechat_public"]
KeywordCrawlSort = Literal["relevance", "time_desc"]
KeywordCrawlFreshness = Literal["day", "week", "month", "year", "any"]


class DataSourceCreateRequest(BaseModel):
    name: str
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


class DataSourceRecord(BaseModel):
    id: str
    name: str
    source_type: DataSourceType
    description: str = ""
    status: DataSourceStatus
    trust_level: TrustLevel
    config: dict[str, Any] = Field(default_factory=dict)
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


class MarketQuoteListResponse(BaseModel):
    quotes: list[MarketQuote]
    provider: str
    fetched_at: str
    warnings: list[str] = Field(default_factory=list)


class EarningsCalendarEvent(BaseModel):
    symbol: str
    name: str
    report_date: Optional[str] = None
    fiscal_date_ending: Optional[str] = None
    eps_estimate: Optional[float] = None
    eps_actual: Optional[float] = None
    eps_surprise_percent: Optional[float] = None
    revenue_estimate: Optional[float] = None
    revenue_actual: Optional[float] = None
    revenue_surprise_percent: Optional[float] = None
    currency: str = "USD"
    time_of_day: Optional[str] = None
    provider: str
    source_name: str
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


TaskStatus = Literal["pending", "running", "waiting_approval", "failed", "completed", "cancelled"]


class InvestmentTaskCreateRequest(BaseModel):
    title: str
    symbol: Optional[str] = None
    asset_name: Optional[str] = None
    task_type: Literal["investment_research", "portfolio_review", "risk_review", "watchlist_monitor"] = "investment_research"
    horizon: str = "1-4周"
    investor_profile: Literal["保守", "稳健", "进取", "专业"] = "稳健"
    objective: str = "判断是否值得进一步研究，并给出风险和观察清单。"
    context: str = ""
    priority: int = Field(default=3, ge=1, le=5)


class AgentLogEntry(BaseModel):
    timestamp: str
    agent: str
    message: str


class InvestmentTaskRecord(BaseModel):
    id: str
    title: str
    symbol: Optional[str] = None
    asset_name: Optional[str] = None
    task_type: str
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


class InvestmentTaskListResponse(BaseModel):
    tasks: list[InvestmentTaskRecord]


class AgentRuntimeHealthResponse(BaseModel):
    status: str
    worker_running: bool
    pending: int
    running: int
    completed: int
    failed: int


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
