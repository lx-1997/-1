import { apiGet } from './apiClient';

export interface CapacityTrendPoint {
  week: string;
  date?: string;
  electronics: number | null;
  semiconductor: number | null;
  aiProxy: number | null;
  observed?: boolean;
  signal?: boolean;
  signal_date?: string;
  signal_source?: string;
}

export interface DeliveryTrendPoint {
  week: string;
  date?: string;
  cowos: number | null;
  hbm: number | null;
  optical: number | null;
  power: number | null;
  wafer?: number | null;
  substrate?: number | null;
  pcb?: number | null;
  ssd?: number | null;
  rack?: number | null;
  observed?: boolean;
  signal?: boolean;
  signal_date?: string;
  signal_source?: string;
}

export interface PricingTrendPoint {
  week: string;
  date?: string;
  hbmDram: number | null;
  enterpriseSsd: number | null;
  cowosPackaging: number | null;
  substratePcb: number | null;
  powerIc: number | null;
  opticalModule: number | null;
  rackBom: number | null;
  observed?: boolean;
  signal?: boolean;
  history_gap?: boolean;
  signal_source?: string;
}

export interface AiSupplyChainTrendSource {
  name: string;
  url: string;
  type: string;
  note: string;
}

export interface AiSupplyChainIndustryUpdate {
  date: string;
  title: string;
  url: string;
  type: string;
}

export interface AiSupplyChainCapacityTrends {
  capacity_trend: CapacityTrendPoint[];
  delivery_trend: DeliveryTrendPoint[];
  pricing_trend: PricingTrendPoint[];
  horizon?: '3m' | '1y';
  week_count?: number;
  official_source: string;
  official_observed_through?: string | null;
  official_release_date?: string | null;
  proxy_observed_through?: string | null;
  pricing_observed_through?: string | null;
  industry_observed_through?: string | null;
  industry_updates: AiSupplyChainIndustryUpdate[];
  stale_from?: string | null;
  fetched_at: string;
  sources: AiSupplyChainTrendSource[];
  warnings: string[];
}

export type AiSupplyChainHorizon = '3m' | '1y';

export async function fetchAiSupplyChainCapacityTrends(horizon: AiSupplyChainHorizon = '3m'): Promise<AiSupplyChainCapacityTrends> {
  return apiGet<AiSupplyChainCapacityTrends>(`/api/ai-supply-chain/capacity-trends?horizon=${horizon}`, {
    timeout: horizon === '1y' ? 40000 : 30000
  });
}
