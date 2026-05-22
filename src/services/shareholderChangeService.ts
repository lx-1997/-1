import { apiPost } from './apiClient';

export type ShareholderChangeDirection = 'all' | 'increase' | 'decrease';
export type ShareholderChangeMarket = 'A' | 'HK' | 'US';
export type ShareholderChangeRecordDirection = 'increase' | 'decrease' | 'mixed' | 'unknown';
export type ShareholderChangeStatus = 'plan' | 'progress' | 'completed' | 'other';
export type ShareholderRiskLevel = 'low' | 'medium' | 'high';
export type ShareholderDetailSource = 'title' | 'pdf' | 'html' | 'xml' | 'unavailable';
export type ShareholderDetailQuality = 'full' | 'partial' | 'title_only';

export interface ShareholderChangeScanRequest {
  market?: ShareholderChangeMarket;
  days?: number;
  start_date?: string | null;
  end_date?: string | null;
  direction?: ShareholderChangeDirection;
  limit?: number;
  detail_limit?: number;
}

export interface ShareholderChangeRecord {
  symbol: string;
  name: string;
  announcement_date: string;
  direction: ShareholderChangeRecordDirection;
  status: ShareholderChangeStatus;
  shareholder_type: string;
  shareholder_hint: string;
  title: string;
  url: string;
  source: string;
  source_name: string;
  announcement_id: string;
  risk_level: ShareholderRiskLevel;
  tags: string[];
  shareholder_names: string[];
  change_shares: string;
  change_ratio: string;
  change_amount: string;
  price_range: string;
  change_period: string;
  change_method: string;
  holding_before: string;
  holding_after: string;
  change_reason: string;
  detail_summary: string;
  evidence_excerpt: string;
  detail_source: ShareholderDetailSource;
  detail_quality: ShareholderDetailQuality;
  metadata: Record<string, unknown>;
}

export interface ShareholderChangeScanResponse {
  provider: string;
  model: string;
  generated_at: string;
  skill: string;
  market: ShareholderChangeMarket;
  direction: ShareholderChangeDirection;
  start_date: string;
  end_date: string;
  total_found: number;
  returned_count: number;
  detail_attempted_count: number;
  detail_success_count: number;
  summary: string;
  records: ShareholderChangeRecord[];
  warnings: string[];
  coverage_note: string;
  skill_invocation: string;
}

export type ShareholderInterpretTone = 'positive' | 'watch' | 'risk' | 'neutral';

export interface ShareholderChangeInterpretRequest {
  record: ShareholderChangeRecord;
  question?: string | null;
  style?: 'brief' | 'full';
  locale?: string;
}

export interface ShareholderChangeInterpretResponse {
  provider: string;
  model: string;
  generated_at: string;
  skill: string;
  symbol: string;
  name: string;
  title: string;
  tone: ShareholderInterpretTone;
  verdict: string;
  summary: string;
  points: string[];
  risks: string[];
  questions: string[];
  actions: string[];
  evidence: string[];
  prompt_version: string;
  confidence: number;
  disclaimer: string;
}

export async function scanShareholderChanges(
  request: ShareholderChangeScanRequest
): Promise<ShareholderChangeScanResponse> {
  return apiPost<ShareholderChangeScanResponse>(
    '/api/skills/shareholder-changes/scan',
    {
      market: 'A',
      ...request
    },
    { timeout: 60000 }
  );
}

export async function interpretShareholderChange(
  request: ShareholderChangeInterpretRequest
): Promise<ShareholderChangeInterpretResponse> {
  return apiPost<ShareholderChangeInterpretResponse>(
    '/api/skills/shareholder-changes/interpret',
    {
      style: 'brief',
      locale: 'zh-CN',
      ...request
    },
    { timeout: 45000 }
  );
}
