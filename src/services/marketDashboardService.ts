import { getApiBaseUrls, formatErrorMessage } from './apiClient';

const API_BASE = () => getApiBaseUrls()[0];

export interface MarketIndicator {
  key: string;
  name: string;
  category: string;
  value: number;
  unit: string;
  change: number | null;
  change_pct: number | null;
  signal: 'strong_bullish' | 'bullish' | 'neutral' | 'bearish' | 'strong_bearish';
  status: string;
  description: string;
  historical_context: string;
  current_reading: string;
  source: string;
}

export interface DashboardCategory {
  key: string;
  name: string;
  indicators: MarketIndicator[];
}

export interface MarketDashboardResponse {
  generated_at: string;
  overall_signal: string;
  overall_score: number;
  overall_summary: string;
  total_indicators: number;
  available_indicators: number;
  categories: DashboardCategory[];
}

export async function fetchMarketDashboard(): Promise<MarketDashboardResponse> {
  try {
    const resp = await fetch(`${API_BASE()}/api/market-dashboard`);
    if (!resp.ok) {
      throw new Error(`HTTP ${resp.status}`);
    }
    return resp.json();
  } catch (error) {
    throw new Error(formatErrorMessage(error));
  }
}

export async function fetchAshareDashboard(): Promise<MarketDashboardResponse> {
  try {
    const resp = await fetch(`${API_BASE()}/api/market-dashboard/ashare`);
    if (!resp.ok) {
      throw new Error(`HTTP ${resp.status}`);
    }
    return resp.json();
  } catch (error) {
    throw new Error(formatErrorMessage(error));
  }
}

export interface DashboardAnalysisResponse {
  title: string;
  summary: string;
  key_points: string[];
  signals: string[];
  risks: string[];
  actions: string[];
  sources: string[];
  confidence: number;
}

export async function analyzeGlobalDashboard(): Promise<DashboardAnalysisResponse> {
  try {
    const resp = await fetch(`${API_BASE()}/api/market-dashboard/analyze`, {
      method: 'POST',
    });
    if (!resp.ok) {
      throw new Error(`HTTP ${resp.status}`);
    }
    return resp.json();
  } catch (error) {
    throw new Error(formatErrorMessage(error));
  }
}

export async function analyzeAshareDashboard(): Promise<DashboardAnalysisResponse> {
  try {
    const resp = await fetch(`${API_BASE()}/api/market-dashboard/ashare/analyze`, {
      method: 'POST',
    });
    if (!resp.ok) {
      throw new Error(`HTTP ${resp.status}`);
    }
    return resp.json();
  } catch (error) {
    throw new Error(formatErrorMessage(error));
  }
}