import { apiGet, apiPost } from './apiClient';
import type { DataQuality } from '../types';

// === earningsCalendarService types ===

export type EarningsEventStatus = 'scheduled' | 'reported' | 'watchlist_template';
export type EarningsEventConfidence = 'confirmed' | 'estimated' | 'pending_provider';

export interface EarningsCalendarEvent {
  symbol: string;
  name: string;
  report_date?: string | null;
  fiscal_date_ending?: string | null;
  eps_estimate?: number | null;
  eps_high_estimate?: number | null;
  eps_low_estimate?: number | null;
  eps_actual?: number | null;
  eps_surprise_percent?: number | null;
  revenue_estimate?: number | null;
  revenue_actual?: number | null;
  revenue_surprise_percent?: number | null;
  market_cap?: number | null;
  analyst_count?: number | null;
  revision_up_count?: number | null;
  revision_down_count?: number | null;
  last_year_report_date?: string | null;
  last_year_eps?: number | null;
  currency: string;
  time_of_day?: string | null;
  provider: string;
  source_name: string;
  source_url?: string | null;
  data_as_of?: string | null;
  days_until_report?: number | null;
  is_date_confirmed?: boolean;
  status: EarningsEventStatus;
  confidence: EarningsEventConfidence;
  watch_items: string[];
  focus_metrics: string[];
  risk_flags: string[];
  related_symbols: string[];
}

export interface EarningsCalendarResponse {
  events: EarningsCalendarEvent[];
  provider: string;
  fetched_at: string;
  warnings: string[];
  data_quality?: DataQuality;
}

// === cnEarningsService types ===

export type CnEarningsReportType = 'annual' | 'semiannual' | 'q1' | 'q3' | 'forecast' | 'flash' | 'correction' | 'other';
export type CnEarningsRiskLevel = 'low' | 'medium' | 'high';
export type CnEarningsDetailSource = 'title' | 'pdf' | 'unavailable';
export type CnEarningsDetailQuality = 'full' | 'partial' | 'title_only';
export type CnEarningsDiagnosisSignal = 'positive' | 'neutral' | 'negative' | 'watch';
export type CnEarningsFinancialQuality = 'strong' | 'stable' | 'mixed' | 'weak' | 'unknown';

export interface CnEarningsScanRequest {
  market?: 'A';
  days?: number;
  start_date?: string | null;
  end_date?: string | null;
  report_types?: CnEarningsReportType[];
  limit?: number;
  detail_limit?: number;
}

export interface CnEarningsRecord {
  symbol: string;
  name: string;
  announcement_date: string;
  report_type: CnEarningsReportType;
  fiscal_year: string;
  fiscal_period: string;
  title: string;
  url: string;
  source: string;
  source_name: string;
  announcement_id: string;
  risk_level: CnEarningsRiskLevel;
  revenue: string;
  revenue_yoy: string;
  net_profit: string;
  net_profit_yoy: string;
  deducted_net_profit: string;
  deducted_net_profit_yoy: string;
  eps: string;
  roe: string;
  gross_margin: string;
  operating_cash_flow: string;
  total_assets: string;
  key_takeaways: string[];
  risk_flags: string[];
  evidence_excerpt: string;
  detail_source: CnEarningsDetailSource;
  detail_quality: CnEarningsDetailQuality;
  tags: string[];
  metadata: Record<string, unknown>;
}

export interface CnEarningsScanResponse {
  provider: string;
  model: string;
  generated_at: string;
  skill: string;
  market: 'A';
  start_date: string;
  end_date: string;
  total_found: number;
  returned_count: number;
  detail_attempted_count: number;
  detail_success_count: number;
  summary: string;
  records: CnEarningsRecord[];
  warnings: string[];
  coverage_note: string;
  skill_invocation: string;
}

export interface CnEarningsDiagnosisRequest {
  record: CnEarningsRecord;
  question?: string | null;
  style?: 'brief' | 'full';
  locale?: string;
}

export interface CnEarningsRecordDetailResponse {
  provider: string;
  model: string;
  generated_at: string;
  record: CnEarningsRecord;
  warnings: string[];
}

export interface CnEarningsDiagnosisAgentStep {
  agent: string;
  role: string;
  finding: string;
  status: 'done' | 'watch' | 'risk';
}

export interface CnEarningsDiagnosisResponse {
  provider: string;
  model: string;
  generated_at: string;
  skill: string;
  symbol: string;
  name: string;
  title: string;
  report_type: CnEarningsReportType;
  diagnosis_signal: CnEarningsDiagnosisSignal;
  risk_level: CnEarningsRiskLevel;
  financial_quality: CnEarningsFinancialQuality;
  overall_score: number;
  summary: string;
  verdict: string;
  agent_steps: CnEarningsDiagnosisAgentStep[];
  positives: string[];
  concerns: string[];
  questions: string[];
  actions: string[];
  evidence: string[];
  prompt_version: string;
  confidence: number;
  disclaimer: string;
}

// === earningsCalendarService functions ===

export async function getEarningsCalendar(
  symbols: string[],
  horizon: '3month' | '6month' | '12month' = '3month',
  minMarketCap?: number,
  includeAll = false
): Promise<EarningsCalendarResponse> {
  const cleanedSymbols = symbols
    .map(symbol => symbol.trim().toUpperCase())
    .filter(Boolean);

  if (cleanedSymbols.length === 0 && !minMarketCap && !includeAll) {
    return {
      events: [],
      provider: 'none',
      fetched_at: new Date().toISOString(),
      warnings: ['No symbols supplied']
    };
  }

  return apiGet<EarningsCalendarResponse>('/api/earnings/calendar', {
    params: {
      symbols: cleanedSymbols.join(','),
      horizon,
      ...(minMarketCap ? { min_market_cap: minMarketCap } : {}),
      ...(includeAll ? { include_all: true } : {})
    },
    timeout: 45000
  });
}

// === cnEarningsService functions ===

export async function scanCnEarnings(
  request: CnEarningsScanRequest
): Promise<CnEarningsScanResponse> {
  return apiPost<CnEarningsScanResponse>(
    '/api/skills/cn-earnings/scan',
    {
      market: 'A',
      ...request
    },
    { timeout: 60000 }
  );
}

export async function enrichCnEarningsRecordDetail(
  record: CnEarningsRecord
): Promise<CnEarningsRecordDetailResponse> {
  return apiPost<CnEarningsRecordDetailResponse>(
    '/api/skills/cn-earnings/detail',
    { record },
    { timeout: 30000 }
  );
}

export async function diagnoseCnEarnings(
  request: CnEarningsDiagnosisRequest
): Promise<CnEarningsDiagnosisResponse> {
  return apiPost<CnEarningsDiagnosisResponse>(
    '/api/skills/cn-earnings/diagnose',
    {
      style: 'brief',
      locale: 'zh-CN',
      ...request
    },
    { timeout: 45000 }
  );
}
