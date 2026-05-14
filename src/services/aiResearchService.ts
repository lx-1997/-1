import { Post, Stock } from '../types';
import { apiGet, apiPost } from './apiClient';

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
}

export interface SentimentResponse {
  provider: string;
  model: string;
  label: 'positive' | 'neutral' | 'negative';
  score: number;
  rationale: string;
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

export async function analyzeStock(request: StockAnalysisRequest): Promise<AiResearchReport> {
  return apiPost<AiResearchReport>('/api/ai/stock-analysis', request);
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
