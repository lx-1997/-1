import { apiGet } from './apiClient';
import type { DataQuality } from '../types';

export type RiskMarket = 'CN' | 'HK' | 'US';
export type RiskLevel = 'green' | 'yellow' | 'orange' | 'red';
export type RiskConfidence = 'high' | 'medium' | 'low';
export type RiskDimensionKey = 'macro' | 'industry' | 'stock' | 'flow' | 'information' | 'options';

export interface RiskEvidence {
  dimension: RiskDimensionKey;
  title: string;
  detail: string;
  severity: 'info' | 'warning' | 'critical';
  source: string;
  content_type?: string;
  url?: string | null;
  published_at?: string | null;
}

export interface MarketRiskOptionsSignal {
  status: 'available' | 'unavailable' | 'not_applicable';
  risk_score?: number | null;
  provider?: string;
  source_status?: string;
  data_quality?: number;
  contract_count?: number;
  expiration_count?: number;
  direction?: string;
  conviction?: string;
  tail_event_risk_level?: string;
  put_call_volume_ratio?: number | null;
  put_call_open_interest_ratio?: number | null;
  avg_iv?: number | null;
  iv_skew?: number | null;
  expected_move_pct?: number | null;
  pin_risk_score?: number;
  gamma_exposure_status?: string;
  net_gamma_exposure?: number | null;
  unusual_flow_count?: number;
  unusual_premium_notional?: number | null;
  summary: string;
  reasons: string[];
  fetched_at?: string;
}

export interface MarketRiskCompany {
  rank: number;
  symbol: string;
  name: string;
  market: RiskMarket;
  sector: string;
  currency: string;
  market_cap?: number | null;
  price?: number | null;
  change_pct?: number | null;
  amplitude_pct?: number | null;
  turnover_pct?: number | null;
  pe?: number | null;
  pb?: number | null;
  change_60d_pct?: number | null;
  change_ytd_pct?: number | null;
  main_net_inflow?: number | null;
  main_net_inflow_pct?: number | null;
  risk_score: number;
  risk_level: RiskLevel;
  confidence: RiskConfidence;
  dimensions: Partial<Record<RiskDimensionKey, number>>;
  drivers: string[];
  evidence: RiskEvidence[];
  risk_evidence_count: number;
  site_signal_count: number;
  site_source_types: string[];
  options_signal: MarketRiskOptionsSignal;
  data_status: 'live' | 'stale' | 'fallback';
}

export interface MarketRiskMarket {
  market: RiskMarket;
  label: string;
  currency: string;
  ranking_source: string;
  source_status: 'live' | 'stale' | 'fallback';
  macro_label: string;
  macro_risk: number;
  companies: MarketRiskCompany[];
}

export interface MarketRiskCounts {
  green: number;
  yellow: number;
  orange: number;
  red: number;
}

export interface MarketRiskSummary {
  average_risk: number;
  risk_level: RiskLevel;
  counts: MarketRiskCounts;
  site_signal_companies: number;
  site_content_items: number;
  site_source_counts: Record<string, number>;
  options_available_companies: number;
  options_risk_companies: number;
  market_summaries: Array<{
    market: RiskMarket;
    label: string;
    company_count: number;
    average_risk: number;
    counts: MarketRiskCounts;
    source_status: 'live' | 'stale' | 'fallback';
  }>;
}

export interface MarketRiskRadarResponse {
  generated_at: string;
  coverage: {
    markets: RiskMarket[];
    companies: number;
    per_market_limit: number;
    basis: string;
  };
  summary: MarketRiskSummary;
  markets: MarketRiskMarket[];
  methodology: {
    weights: Record<RiskDimensionKey, number>;
    thresholds: Record<RiskLevel, string>;
    explanation: string;
  };
  sources: Array<{ name: string; role: string; status: string }>;
  warnings: string[];
  data_quality: DataQuality;
  disclaimer: string;
}

export function fetchMarketRiskRadar(force = false): Promise<MarketRiskRadarResponse> {
  return apiGet<MarketRiskRadarResponse>('/api/market-risk-radar', {
    params: {
      markets: 'CN,HK,US',
      limit: 20,
      force
    },
    timeout: 60000
  });
}
