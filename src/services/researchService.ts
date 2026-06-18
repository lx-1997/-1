import { DataQuality, Post, Stock } from '../types';
import { apiGet, apiPost } from './apiClient';

// === aiResearchService types ===

export interface AiResearchReport {
  provider: string;
  model: string;
  generated_at: string;
  executive_summary: string;
  sentiment_label: 'positive' | 'neutral' | 'negative';
  sentiment_score: number;
  risk_level: 'low' | 'medium' | 'high';
  catalysts: string[];
  risks: string[];
  watch_items: string[];
  suggested_questions: string[];
  disclaimer: string;
  data_quality?: DataQuality;
}

export interface FinGptCapability {
  key: string;
  name: string;
  description: string;
  endpoint: string;
  mode: 'cloud' | 'mock' | 'local-optional';
}

export interface CapabilityListResponse {
  provider: string;
  model: string;
  capabilities: FinGptCapability[];
}

export interface FinGptTaskResponse {
  provider: string;
  model: string;
  generated_at: string;
  capability: string;
  title: string;
  summary: string;
  key_points: string[];
  signals: string[];
  risks: string[];
  actions: string[];
  sources: string[];
  confidence: number;
  disclaimer: string;
  data_quality?: DataQuality;
}

export interface SentimentResponse {
  provider: string;
  model: string;
  label: 'positive' | 'neutral' | 'negative';
  score: number;
  rationale: string;
  data_quality?: DataQuality;
}

export interface StockCheckStep {
  key: string;
  name: string;
  status: 'completed' | 'failed' | 'skipped';
  detail: string;
}

export interface StockCheckResponse {
  provider: string;
  model: string;
  generated_at: string;
  stock: Stock;
  verdict: '重点跟踪' | '谨慎观察' | '暂不行动';
  score: number;
  confidence: number;
  summary: string;
  action_items: string[];
  risk_flags: string[];
  checks: StockCheckStep[];
  stock_analysis?: AiResearchReport | null;
  sentiment?: SentimentResponse | null;
  news_summary?: FinGptTaskResponse | null;
  report_analysis?: FinGptTaskResponse | null;
  rag_answer?: FinGptTaskResponse | null;
  forecast?: FinGptTaskResponse | null;
  agent_brief?: FinGptTaskResponse | null;
  warnings: string[];
  disclaimer: string;
}

export interface ModelConfig {
  provider: 'mock' | 'openai' | 'minimax' | 'openai-compatible' | 'cloud';
  model: string;
  base_url?: string | null;
  temperature: number;
  api_key_configured: boolean;
  api_key_preview?: string | null;
  config_source: string;
}

export interface ModelConfigUpdate {
  provider: ModelConfig['provider'];
  model?: string;
  base_url?: string;
  api_key?: string;
  temperature: number;
  persist?: boolean;
}

export interface FileExtractionResult {
  filename: string;
  content_type?: string | null;
  text: string;
  char_count: number;
  truncated: boolean;
  parser: string;
}

export interface StockAnalysisRequest {
  stock: Stock;
  posts: Pick<Post, 'title' | 'summary' | 'content' | 'category' | 'tags' | 'qualityScore' | 'publishTime'>[];
  question?: string;
  locale?: string;
}

// === tearSheetService types（个股速判卡，对齐后端 schemas.TearSheetResponse）===

export type TearSheetSignal = 'bullish' | 'neutral' | 'bearish' | 'insufficient';
export type TearSheetVerdict = '重点跟踪' | '中性观察' | '谨慎回避' | '数据不足';

export interface CitedSource {
  label: string;
  provider?: string;
  url?: string;
  quality?: 'live' | 'degraded' | 'mock';
}

export interface TearSheetDimension {
  key: string;
  label: string;
  signal: TearSheetSignal;
  score: number;
  headline: string;
  evidence: string[];
  confidence: number;
  data_quality?: DataQuality;
  sources?: CitedSource[];
}

export interface TearSheetResponse {
  symbol: string;
  name: string;
  generated_at: string;
  provider: string;
  price?: number | null;
  change_percent?: number | null;
  currency: string;
  overall_verdict: TearSheetVerdict;
  overall_score: number;
  confidence: number;
  narrative: string;
  narrative_provider: string;
  dimensions: TearSheetDimension[];
  price_series?: { date: string; value: number }[];
  sp500_series?: { date: string; value: number }[];
  us10y_series?: { date: string; value: number }[];
  disclaimer: string;
  data_quality?: DataQuality;
}

// === proResearchService types ===

export type ProfessionalReportType = 'annual' | 'semiannual' | 'quarterly' | 'research' | 'transcript' | 'other';
export type ProfessionalCitationKind = 'chunk' | 'metric';

export interface ProfessionalReportRecord {
  id: string;
  source_item_id?: string | null;
  title: string;
  symbol?: string | null;
  report_type: string;
  period?: string | null;
  parser: string;
  char_count: number;
  metadata: Record<string, any>;
  metrics_count: number;
  chunks_count: number;
  created_at: string;
  updated_at: string;
}

export interface ProfessionalMetricRecord {
  id: string;
  report_id: string;
  symbol?: string | null;
  period?: string | null;
  metric_key: string;
  metric_label: string;
  value?: number | null;
  normalized_value?: number | null;
  unit?: string | null;
  raw_value: string;
  source_page?: number | null;
  source_excerpt: string;
  confidence: number;
  metadata: Record<string, any>;
  created_at: string;
}

export interface ProfessionalCitation {
  citation_id: string;
  kind: ProfessionalCitationKind;
  source_id: string;
  report_id: string;
  report_title: string;
  title: string;
  page?: number | null;
  text: string;
  score: number;
  metadata: Record<string, any>;
}

export interface ProfessionalRagQuery {
  question: string;
  symbol?: string;
  report_id?: string;
  period?: string;
  top_k?: number;
  use_cloud_model?: boolean;
}

export interface ProfessionalRagQueryResult {
  answer: string;
  citations: ProfessionalCitation[];
  metrics: ProfessionalMetricRecord[];
  confidence: number;
  missing: string[];
  disclaimer: string;
}

export interface ProfessionalReportAnalysis {
  report: ProfessionalReportRecord;
  summary: string;
  key_metrics: ProfessionalMetricRecord[];
  quality_flags: string[];
  risks: string[];
  follow_up_questions: string[];
  citations: ProfessionalCitation[];
  confidence: number;
  disclaimer: string;
}

export interface ProfessionalEvalCase {
  question: string;
  expected_text?: string | null;
  expected_metric_key?: string | null;
  expected_refusal?: boolean;
  must_cite?: boolean;
}

export interface ProfessionalEvalRunResult {
  generated_at: string;
  total: number;
  passed: number;
  pass_rate: number;
  citation_rate: number;
  answer_match_rate: number;
  refusal_guard_rate: number;
  cases: Array<{
    question: string;
    answer: string;
    passed: boolean;
    notes: string[];
    citations_present: boolean;
    answer_match: boolean;
    refusal_ok: boolean;
  }>;
}

export interface WorkbenchDownloadFile {
  name: string;
  size: number;
  mtime: string;
}

export interface WorkbenchDownloadsResponse {
  dir: string;
  exists: boolean;
  files: WorkbenchDownloadFile[];
  summary: {
    total: number;
    downloaded: number;
    failed: number;
    listed: number;
    sizeBytes: number;
  };
}

// === researchWorkbenchService types ===

const DEFAULT_GROUP_ID = '88888142214212';
const DEFAULT_TAG = '海外投行报告';
const DEFAULT_OUT = 'downloads/海外投行报告';
const DEFAULT_SEARCH_PAGES = 100;

export interface ResearchWorkbenchSearchItem {
  fileId?: string;
  topicId?: string;
  name: string;
  size?: number;
  hashtag?: string;
  createTime?: string;
  topicCreateTime?: string;
  downloadCount?: number;
  score?: number;
}

export interface ResearchWorkbenchSearchResponse {
  items: ResearchWorkbenchSearchItem[];
  keyword?: string;
  count: number;
  scannedTopics?: number;
  hashtags?: Array<{ title: string; hashtagId?: string }>;
}

export interface ResearchWorkbenchJob {
  id: string;
  status: string;
  progress?: number;
}

export interface ResearchWorkbenchChatResponse {
  reply: string;
  skill?: string;
  usage?: unknown;
}

export interface ResearchWorkbenchDownloadFile {
  name: string;
  size: number;
  mtime: string;
}

export interface ResearchWorkbenchDownloadsResponse {
  files: ResearchWorkbenchDownloadFile[];
  summary?: {
    total: number;
    downloaded: number;
    failed: number;
    listed: number;
    sizeBytes: number;
  };
}

export interface ResearchWorkbenchSearchPayload {
  keyword: string;
  tag?: string;
  tags?: string[];
  group?: string;
  ext?: string;
  out?: string;
  searchPages?: number;
  resultLimit?: number;
}

// === aiResearchService functions ===

export async function analyzeStock(request: StockAnalysisRequest): Promise<AiResearchReport> {
  return apiPost<AiResearchReport>('/api/ai/stock-analysis', request);
}

export async function getTearSheet(
  symbol: string,
  name?: string,
  marketCap?: number,
  market?: string,
): Promise<TearSheetResponse> {
  return apiGet<TearSheetResponse>('/api/stock/tear-sheet', {
    params: { symbol, name: name || undefined, market_cap: marketCap, market: market || undefined },
  });
}

export type PortfolioVerdict = '稳健' | '需关注' | '高风险' | '空仓';

export interface PortfolioReviewResponse {
  portfolio_name: string;
  generated_at: string;
  position_count: number;
  total_value?: number | null;
  total_pnl_pct?: number | null;
  overall_verdict: PortfolioVerdict;
  risk_score: number;
  confidence: number;
  narrative: string;
  narrative_provider?: string;
  dimensions: TearSheetDimension[];
  sp500_series?: { date: string; value: number }[];
  us10y_series?: { date: string; value: number }[];
  alerts: string[];
  disclaimer: string;
  data_quality?: DataQuality;
}

export async function getPortfolioReview(): Promise<PortfolioReviewResponse> {
  return apiGet<PortfolioReviewResponse>('/api/portfolio/review');
}

export type MacroVerdict = '风险偏好' | '中性' | '避险' | '数据不足';
export type RegimeAxis = '扩张' | '放缓' | '中性' | '升温' | '回落';

export interface MacroRegime {
  name: string;
  growth_axis: RegimeAxis;
  inflation_axis: RegimeAxis;
  growth_score: number;
  inflation_score: number;
  playbook: string;
  favored: string[];
  avoided: string[];
  confidence: number;
  evidence: string[];
}

export interface MacroReviewResponse {
  generated_at: string;
  overall_verdict: MacroVerdict;
  overall_score: number;
  confidence: number;
  narrative: string;
  narrative_provider?: string;
  regime?: MacroRegime | null;
  dimensions: TearSheetDimension[];
  china_dimensions?: TearSheetDimension[];
  china_read?: string;
  sp500_series?: { date: string; value: number }[];
  us10y_series?: { date: string; value: number }[];
  disclaimer: string;
  data_quality?: DataQuality;
}

export async function getMacroReview(): Promise<MacroReviewResponse> {
  return apiGet<MacroReviewResponse>('/api/macro/review');
}

export interface WatchlistSectorBucket {
  sector: string;
  count: number;
  pct: number;
}

export interface WatchlistSummary {
  total: number;
  covered: number;
  sectors: WatchlistSectorBucket[];
  note: string;
  data_quality?: DataQuality;
}

export interface BriefingResponse {
  generated_at: string;
  headline: string;
  headline_provider?: string;
  macro_verdict: MacroVerdict;
  portfolio_verdict: string;
  macro: MacroReviewResponse;
  portfolio: PortfolioReviewResponse;
  watchlist?: WatchlistSummary;
  disclaimer: string;
  data_quality?: DataQuality;
}

export async function getBriefing(symbols?: string[]): Promise<BriefingResponse> {
  const q = symbols && symbols.length ? `?symbols=${encodeURIComponent(symbols.join(','))}` : '';
  return apiGet<BriefingResponse>(`/api/briefing/today${q}`);
}

export interface StockCompareItem {
  symbol: string;
  name: string;
  overall_verdict: TearSheetVerdict;
  overall_score: number;
  sector?: string | null;
  market_cap?: number | null;
  dimensions: TearSheetDimension[];
  data_quality?: DataQuality;
}

export interface StockCompareResponse {
  generated_at: string;
  items: StockCompareItem[];
  disclaimer: string;
  data_quality?: DataQuality;
}

export async function getStockCompare(
  symbols: string[],
  caps: (string | number | undefined)[] = [],
): Promise<StockCompareResponse> {
  const p = new URLSearchParams();
  if (symbols.length) p.set('symbols', symbols.join(','));
  if (caps.length) p.set('caps', caps.map((c) => String(c ?? '')).join(','));
  return apiGet<StockCompareResponse>(`/api/stock/compare?${p.toString()}`);
}

export async function getFinGptCapabilities(): Promise<CapabilityListResponse> {
  return apiGet<CapabilityListResponse>('/api/fingpt/capabilities');
}

export async function getModelConfig(): Promise<ModelConfig> {
  return apiGet<ModelConfig>('/api/fingpt/model-config');
}

export async function updateModelConfig(payload: ModelConfigUpdate): Promise<ModelConfig> {
  return apiPost<ModelConfig>('/api/fingpt/model-config', payload);
}

export async function extractFileText(file: File): Promise<FileExtractionResult> {
  const formData = new FormData();
  formData.append('file', file);
  return apiPost<FileExtractionResult>('/api/fingpt/files/extract', formData);
}

export async function scoreSentiment(text: string): Promise<SentimentResponse> {
  return apiPost<SentimentResponse>('/api/ai/sentiment', { text });
}

export async function runStockCheck(payload: StockAnalysisRequest & { horizon?: string }): Promise<StockCheckResponse> {
  return apiPost<StockCheckResponse>('/api/fingpt/stock-check', payload);
}

export async function summarizeNews(payload: any): Promise<FinGptTaskResponse> {
  return apiPost<FinGptTaskResponse>('/api/fingpt/news-summary', payload);
}

export async function analyzeReport(payload: any): Promise<FinGptTaskResponse> {
  return apiPost<FinGptTaskResponse>('/api/fingpt/report-analysis', payload);
}

export async function ragQuery(payload: any): Promise<FinGptTaskResponse> {
  return apiPost<FinGptTaskResponse>('/api/fingpt/rag-query', payload);
}

export async function forecastStock(payload: any): Promise<FinGptTaskResponse> {
  return apiPost<FinGptTaskResponse>('/api/fingpt/forecast', payload);
}

export async function assessCorridorRisk(payload: any): Promise<FinGptTaskResponse> {
  return apiPost<FinGptTaskResponse>('/api/fingpt/corridor-risk', payload);
}

export async function createAgentBrief(payload: any): Promise<FinGptTaskResponse> {
  return apiPost<FinGptTaskResponse>('/api/fingpt/agent-brief', payload);
}

export async function checkAiApiHealth(): Promise<{ status: string; provider: string; model: string }> {
  return apiGet<{ status: string; provider: string; model: string }>('/health');
}

// === proResearchService functions ===

export async function uploadProfessionalReport(payload: {
  file: File;
  symbol?: string;
  title?: string;
  report_type?: ProfessionalReportType;
  period?: string;
  tags?: string[];
}): Promise<ProfessionalReportRecord> {
  const form = new FormData();
  form.append('file', payload.file);
  if (payload.symbol) form.append('symbol', payload.symbol);
  if (payload.title) form.append('title', payload.title);
  if (payload.report_type) form.append('report_type', payload.report_type);
  if (payload.period) form.append('period', payload.period);
  if (payload.tags?.length) form.append('tags', payload.tags.join(','));
  return apiPost<ProfessionalReportRecord>('/api/pro-research/reports/upload', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 60000
  });
}

export async function ingestWorkbenchReportFile(payload: {
  filename: string;
  out?: string;
  symbol?: string;
  title?: string;
  report_type?: ProfessionalReportType;
  period?: string;
  tags?: string[];
}): Promise<ProfessionalReportRecord> {
  return apiPost<ProfessionalReportRecord>('/api/pro-research/reports/ingest-workbench-file', {
    report_type: 'research',
    tags: [],
    ...payload
  }, {
    timeout: 60000
  });
}

export async function ingestProfessionalReportItem(payload: {
  data_item_id: string;
  symbol?: string;
  title?: string;
  report_type?: ProfessionalReportType;
  period?: string;
  tags?: string[];
}): Promise<ProfessionalReportRecord> {
  return apiPost<ProfessionalReportRecord>('/api/pro-research/reports/ingest-item', {
    report_type: 'other',
    tags: [],
    ...payload
  });
}

export async function ingestProfessionalReportUrl(payload: {
  url: string;
  symbol?: string;
  title?: string;
  report_type?: ProfessionalReportType;
  period?: string;
  tags?: string[];
}): Promise<ProfessionalReportRecord> {
  return apiPost<ProfessionalReportRecord>('/api/pro-research/reports/ingest-url', {
    report_type: 'research',
    tags: [],
    ...payload
  }, {
    timeout: 60000
  });
}

export async function listProfessionalReports(filters: {
  symbol?: string;
  limit?: number;
} = {}): Promise<ProfessionalReportRecord[]> {
  const response = await apiGet<{ reports: ProfessionalReportRecord[] }>('/api/pro-research/reports', {
    params: filters
  });
  return response.reports;
}

export async function listWorkbenchDownloads(out = 'downloads/海外投行报告'): Promise<WorkbenchDownloadsResponse> {
  return apiGet<WorkbenchDownloadsResponse>('/research-workbench/api/downloads', {
    params: { out }
  });
}

export async function listProfessionalMetrics(filters: {
  report_id?: string;
  symbol?: string;
  metric_key?: string;
  limit?: number;
} = {}): Promise<ProfessionalMetricRecord[]> {
  const response = await apiGet<{ metrics: ProfessionalMetricRecord[] }>('/api/pro-research/metrics', {
    params: filters
  });
  return response.metrics;
}

export async function queryProfessionalRag(payload: ProfessionalRagQuery): Promise<ProfessionalRagQueryResult> {
  return apiPost<ProfessionalRagQueryResult>('/api/pro-research/rag/query', payload);
}

export async function analyzeProfessionalReport(
  reportId: string,
  payload: { focus?: string; use_cloud_model?: boolean } = {}
): Promise<ProfessionalReportAnalysis> {
  return apiPost<ProfessionalReportAnalysis>(`/api/pro-research/reports/${reportId}/analyze`, payload);
}

export async function runProfessionalEval(payload: {
  report_id?: string;
  symbol?: string;
  top_k?: number;
  cases?: ProfessionalEvalCase[];
}): Promise<ProfessionalEvalRunResult> {
  return apiPost<ProfessionalEvalRunResult>('/api/pro-research/evals/run', payload);
}

// === researchWorkbenchService functions ===

export function searchResearchWorkbenchReports(
  payload: ResearchWorkbenchSearchPayload
): Promise<ResearchWorkbenchSearchResponse> {
  const tags = payload.tags?.length
    ? payload.tags
    : String(payload.tag || DEFAULT_TAG)
        .split(/[,，;；、\n\r]+/)
        .map(item => item.trim())
        .filter(Boolean);

  return apiPost<ResearchWorkbenchSearchResponse>('/research-workbench/api/search', {
    authMode: 'env',
    group: payload.group || DEFAULT_GROUP_ID,
    keyword: payload.keyword,
    tag: tags.join(','),
    tags,
    out: payload.out || DEFAULT_OUT,
    ext: payload.ext || 'pdf',
    searchPages: payload.searchPages ?? DEFAULT_SEARCH_PAGES,
    resultLimit: payload.resultLimit ?? 200
  }, { timeout: 600000 });
}

export function summarizeResearchWorkbenchHits(prompt: string): Promise<ResearchWorkbenchChatResponse> {
  return apiPost<ResearchWorkbenchChatResponse>('/research-workbench/api/chat', {
    messages: [{ role: 'user', content: prompt }],
    skill: 'chat',
    includeFile: false,
    prompt
  }, { timeout: 180000 });
}

export function startResearchWorkbenchDownload(
  selectedFiles: ResearchWorkbenchSearchItem[],
  options: { tag?: string; group?: string; out?: string } = {}
): Promise<ResearchWorkbenchJob> {
  return apiPost<ResearchWorkbenchJob>('/research-workbench/api/jobs', {
    authMode: 'env',
    group: options.group || DEFAULT_GROUP_ID,
    tag: options.tag || DEFAULT_TAG,
    out: options.out || DEFAULT_OUT,
    ext: 'pdf',
    selectedFiles,
    limit: 0,
    listOnly: false
  }, { timeout: 60000 });
}

export function listResearchWorkbenchDownloads(out = DEFAULT_OUT): Promise<ResearchWorkbenchDownloadsResponse> {
  return apiGet<ResearchWorkbenchDownloadsResponse>(
    `/research-workbench/api/downloads?out=${encodeURIComponent(out)}`,
    { timeout: 20000 }
  );
}

// === 研报快速研读（搜索 · 在线预览 · 一键AI分析）===

export type ResearchMarket = 'US' | 'HK' | 'CN' | 'OTHER';

export interface ResearchReportItem {
  id: string;
  source: 'eastmoney';
  title: string;
  org: string;
  date: string;
  symbol?: string | null;
  market: ResearchMarket;
  rating?: string | null;
  stock_name?: string | null;
  pdf_url: string;
  preview_url: string;
}

export interface ResearchReportSearchResponse {
  keyword: string;
  resolved_symbol?: string | null;
  resolved_market?: ResearchMarket | null;
  items: ResearchReportItem[];
  provider: string;
  fetched_at: string;
  warnings: string[];
  data_quality?: DataQuality;
}

export interface WorkbenchPreviewLink {
  id: string;
  name: string;
  previewUrl: string;
  expiresAt: string;
}

/** 东方财富研报融合检索（关键词→标的→A股/港股研报，直链 PDF）。美股返回空+警告。 */
export async function searchResearchReports(
  keyword: string,
  market?: ResearchMarket | 'auto',
  pageSize = 20,
): Promise<ResearchReportSearchResponse> {
  return apiGet<ResearchReportSearchResponse>('/api/research/search', {
    params: {
      keyword,
      market: market && market !== 'auto' ? market : undefined,
      page_size: pageSize,
    },
    timeout: 20000,
  });
}

/** 知识星球「海外投行报告」单文件服务端预览链接（无需手动下载）。需登录凭证。 */
export async function createWorkbenchPreview(payload: {
  fileId: string;
  name?: string;
  group?: string;
}): Promise<WorkbenchPreviewLink> {
  return apiPost<WorkbenchPreviewLink>('/research-workbench/api/preview', {
    authMode: 'env',
    group: payload.group || DEFAULT_GROUP_ID,
    fileId: payload.fileId,
    name: payload.name,
  }, { timeout: 60000 });
}

export interface ResearchVisionAnalysis {
  title: string;
  symbol?: string | null;
  summary: string;
  key_points: string[];
  risks: string[];
  rating?: string | null;
  target_price?: string | null;
  confidence: number;
  pages_analyzed: number;
  provider: string;
  mode: 'vision';
  disclaimer: string;
  data_quality?: DataQuality;
}

/** 图片型研报（无文字层）的多模态视觉解读——渲染页面图像交给视觉模型读图出观点。 */
export async function visionAnalyzeReport(payload: {
  pdf_url?: string;
  workbench_filename?: string;
  workbench_out?: string;
  title?: string;
  symbol?: string;
  max_pages?: number;
}): Promise<ResearchVisionAnalysis> {
  return apiPost<ResearchVisionAnalysis>('/api/research/vision-analyze', payload, { timeout: 240000 });
}
