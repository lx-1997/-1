import { apiPost } from './apiClient';

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
