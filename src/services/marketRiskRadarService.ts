import { apiGet } from './apiClient';
import type { DataQuality } from '../types';

export type RiskMarket = 'CN' | 'HK' | 'US';
export type RiskLevel = 'green' | 'yellow' | 'orange' | 'red';
export type RiskConfidence = 'high' | 'medium' | 'low';
export type RiskDimensionKey = 'macro' | 'industry' | 'stock' | 'flow' | 'information';

export interface RiskEvidence {
  dimension: RiskDimensionKey;
  title: string;
  detail: string;
  severity: 'warning' | 'critical';
  source: string;
  url?: string | null;
  published_at?: string | null;
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
  dimensions: Record<RiskDimensionKey, number>;
  drivers: string[];
  evidence: RiskEvidence[];
  site_signal_count: number;
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
