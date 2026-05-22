import { apiPost } from './apiClient';

export type MajorEventType =
  | 'control_change'
  | 'restructuring'
  | 'buyback'
  | 'equity_incentive'
  | 'pledge_freeze'
  | 'litigation_arbitration'
  | 'regulatory_penalty'
  | 'st_delisting'
  | 'abnormal_trading'
  | 'dividend'
  | 'financing'
  | 'related_transaction'
  | 'other';
export type MajorEventStatus = 'new' | 'progress' | 'completed' | 'risk' | 'other';
export type MajorEventImpact = 'positive' | 'neutral' | 'negative' | 'mixed' | 'unknown';
export type MajorEventRiskLevel = 'low' | 'medium' | 'high';
export type MajorEventDetailSource = 'title' | 'pdf' | 'unavailable';
export type MajorEventDetailQuality = 'full' | 'partial' | 'title_only';

export interface MajorEventScanRequest {
  market?: 'A';
  days?: number;
  start_date?: string | null;
  end_date?: string | null;
  event_types?: MajorEventType[];
  limit?: number;
  detail_limit?: number;
}

export interface MajorEventRecord {
  symbol: string;
  name: string;
  announcement_date: string;
  event_type: MajorEventType;
  status: MajorEventStatus;
  impact: MajorEventImpact;
  risk_level: MajorEventRiskLevel;
  title: string;
  url: string;
  source: string;
  source_name: string;
  announcement_id: string;
  subject: string;
  amount: string;
  share_ratio: string;
  progress: string;
  deadline: string;
  detail_summary: string;
  key_points: string[];
  risk_flags: string[];
  action_items: string[];
  evidence_excerpt: string;
  tags: string[];
  detail_source: MajorEventDetailSource;
  detail_quality: MajorEventDetailQuality;
  metadata: Record<string, unknown>;
}

export interface MajorEventScanResponse {
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
  records: MajorEventRecord[];
  warnings: string[];
  coverage_note: string;
  skill_invocation: string;
}

export async function scanMajorEvents(
  request: MajorEventScanRequest
): Promise<MajorEventScanResponse> {
  return apiPost<MajorEventScanResponse>(
    '/api/skills/major-events/scan',
    {
      market: 'A',
      ...request
    },
    { timeout: 60000 }
  );
}
