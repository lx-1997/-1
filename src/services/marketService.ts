import { apiGet, apiPost } from './apiClient';
import type { DataQuality } from '../types';

// === marketDataService types ===

export interface MarketQuote {
  symbol: string;
  price: number;
  change?: number | null;
  change_percent?: number | null;
  previous_close?: number | null;
  open_price?: number | null;
  high?: number | null;
  low?: number | null;
  volume?: number | null;
  currency: string;
  provider: string;
  provider_name: string;
  market_time?: string | null;
  fetched_at: string;
  is_realtime: boolean;
  delay_note: string;
  wk52_high?: number | null;
  wk52_low?: number | null;
  // 基本面（目前仅 iFinD 增强的 A股 quote 会带；其他 provider 恒 null）
  pe_ttm?: number | null;
  pb?: number | null;
  total_capital?: number | null;   // 总市值（元）
  turnover_ratio?: number | null;  // 换手率（%）
}

export type MarketRegion = 'US' | 'HK' | 'CN' | 'OTHER';

export interface MarketSymbolCandidate {
  symbol: string;
  code: string;
  name: string;
  market: MarketRegion;
  exchange: string;
  security_type: string;
  quote_id?: string | null;
  provider: string;
  provider_name: string;
}

export interface MarketQuoteListResponse {
  quotes: MarketQuote[];
  provider: string;
  fetched_at: string;
  warnings: string[];
}

export interface MarketSymbolSearchResponse {
  query: string;
  market?: MarketRegion | null;
  candidates: MarketSymbolCandidate[];
  provider: string;
  fetched_at: string;
  warnings: string[];
}

export type MarketDataLayerKey = 'free_quote' | 'structured_ashare' | 'sentiment';
export type MarketDataLayerState = 'ready' | 'partial' | 'fallback' | 'unconfigured' | 'error';

export interface MarketDataLayerStatus {
  key: MarketDataLayerKey;
  name: string;
  status: MarketDataLayerState;
  providers: string[];
  primary_provider?: string | null;
  configured: boolean;
  trust_level: string;
  coverage: string;
  detail: string;
  remediation?: string | null;
  latency_ms?: number | null;
  warnings: string[];
}

export interface MarketDataLayerStatusResponse {
  generated_at: string;
  symbol?: string | null;
  keyword?: string | null;
  layers: MarketDataLayerStatus[];
}

export interface AShareStructuredDataResponse {
  symbol: string;
  ts_code: string;
  provider: string;
  provider_name: string;
  configured: boolean;
  status: 'ready' | 'unconfigured' | 'empty' | 'error';
  fetched_at: string;
  stock_basic: Record<string, any>;
  daily: Record<string, any>[];
  daily_basic: Record<string, any>[];
  warnings: string[];
}

// === optionsSignalService types ===

export type OptionsSourceStatusValue = 'ready' | 'fallback' | 'blocked' | 'unavailable';
export type OptionsSignalStatus = 'delayed' | 'partial' | 'unavailable';
export type OptionsDirection = '偏多' | '中性' | '偏空' | '不可判定';
export type OptionsConviction = '高' | '中' | '低';
export type OptionsTailEventRiskLevel = '绿灯' | '黄灯' | '橙灯' | '红灯';
export type OptionsForecastLabel = '强看涨' | '看涨' | '震荡偏强' | '震荡' | '震荡偏弱' | '看跌' | '高风险回避' | '不可判定';
export type OptionsTrendLabel = '看涨' | '震荡偏强' | '震荡' | '震荡偏弱' | '看跌' | '不可判定';

export interface OptionsSourceStatus {
  provider: string;
  name: string;
  status: OptionsSourceStatusValue;
  cost: string;
  delay: string;
  coverage: string;
  notes: string;
}

export interface OptionsKeyStrike {
  side: 'call' | 'put' | 'mixed';
  strike: number;
  metric: string;
  value: number;
  distance_pct?: number | null;
  interpretation: string;
}

export interface OptionsGammaStrike {
  strike: number;
  net_gamma_exposure: number;
  call_gamma_exposure: number;
  put_gamma_exposure: number;
  total_open_interest: number;
  distance_pct?: number | null;
}

export interface OptionsExpirationSignal {
  expiration: string;
  dte?: number | null;
  contract_count: number;
  call_volume: number;
  put_volume: number;
  call_open_interest: number;
  put_open_interest: number;
  pcr_volume?: number | null;
  pcr_open_interest?: number | null;
  atm_straddle_mid?: number | null;
  expected_move_pct?: number | null;
  atm_iv?: number | null;
}

export interface OptionsUnusualFlow {
  option_symbol: string;
  side: 'call' | 'put';
  expiration: string;
  dte?: number | null;
  strike: number;
  volume: number;
  open_interest?: number | null;
  volume_open_interest_ratio?: number | null;
  mark_price?: number | null;
  premium_notional?: number | null;
  distance_pct?: number | null;
  score: number;
  severity: OptionsConviction;
  reason: string;
  interpretation: string;
  updated_at?: string | null;
}

export interface OptionsSignal {
  symbol: string;
  provider: string;
  provider_name: string;
  source_status: OptionsSignalStatus;
  underlying_price?: number | null;
  fetched_at: string;
  expiration_count: number;
  contract_count: number;
  data_quality: number;
  direction: OptionsDirection;
  score: number;
  conviction: OptionsConviction;
  summary: string;
  call_volume: number;
  put_volume: number;
  call_open_interest: number;
  put_open_interest: number;
  put_call_volume_ratio?: number | null;
  put_call_open_interest_ratio?: number | null;
  avg_iv?: number | null;
  iv_skew?: number | null;
  term_structure: string;
  max_pain?: number | null;
  call_wall?: number | null;
  put_wall?: number | null;
  expected_move_abs?: number | null;
  expected_move_pct?: number | null;
  pin_risk_score: number;
  gamma_exposure_status: 'available' | 'estimated' | 'unavailable';
  net_gamma_exposure: number;
  call_gamma_exposure: number;
  put_gamma_exposure: number;
  gamma_wall?: number | null;
  negative_gamma_wall?: number | null;
  zero_gamma_estimate?: number | null;
  gamma_strikes: OptionsGammaStrike[];
  tail_event_risk_score: number;
  tail_event_risk_level: OptionsTailEventRiskLevel;
  tail_event_risk_summary: string;
  tail_event_risk_reasons: string[];
  tail_event_risk_actions: string[];
  forecast_score: number;
  forecast_label: OptionsForecastLabel;
  forecast_confidence: OptionsConviction;
  forecast_horizon: string;
  forecast_summary: string;
  forecast_reasons: string[];
  forecast_actions: string[];
  forecast_invalidations: string[];
  unusual_flow_count: number;
  unusual_premium_notional: number;
  unusual_flows: OptionsUnusualFlow[];
  key_strikes: OptionsKeyStrike[];
  expirations: OptionsExpirationSignal[];
  signals: string[];
  risk_flags: string[];
  delay_note: string;
}

export interface OptionsSignalResponse {
  generated_at: string;
  horizon_days: number;
  provider: string;
  signals: OptionsSignal[];
  sources: OptionsSourceStatus[];
  warnings: string[];
  disclaimer: string;
  data_quality?: DataQuality;
}

export interface OptionsAiAnalysisResponse {
  provider: string;
  model: string;
  generated_at: string;
  symbol: string;
  trend_label: OptionsTrendLabel;
  trend_score: number;
  confidence: number;
  time_horizon: string;
  thesis: string;
  key_drivers: string[];
  upside_triggers: string[];
  downside_triggers: string[];
  watch_levels: string[];
  risk_notes: string[];
  suggested_action: string;
  disclaimer: string;
}

// === premarketOpportunityService types ===

export type PremarketThemeStance = '重点机会' | '观察机会' | '中性观察' | '风险回避';
export type PremarketThemeDirection = '上行' | '下行' | '震荡';

export interface PremarketMappedAsset {
  symbol: string;
  name: string;
  market: MarketRegion;
  role: string;
  reason: string;
}

export interface PremarketQuoteSignal {
  symbol: string;
  name: string;
  market: MarketRegion;
  price?: number | null;
  change_percent?: number | null;
  provider: string;
  role: string;
}

export interface PremarketOptionSignal {
  symbol: string;
  forecast_label: string;
  forecast_score: number;
  tail_event_risk_level: OptionsTailEventRiskLevel;
  tail_event_risk_score: number;
  summary: string;
}

export interface PremarketThemeSignal {
  theme_key: string;
  theme_name: string;
  stance: PremarketThemeStance;
  direction: PremarketThemeDirection;
  opportunity_score: number;
  risk_score: number;
  confidence: OptionsConviction;
  us_impulse_score: number;
  option_confirmation_score: number;
  china_confirmation_score: number;
  leader_quotes: PremarketQuoteSignal[];
  option_signals: PremarketOptionSignal[];
  mapped_assets: PremarketMappedAsset[];
  evidence: string[];
  triggers: string[];
  invalidations: string[];
  suggested_action: string;
}

export interface PremarketOpportunityResponse {
  provider: string;
  model: string;
  generated_at: string;
  market_phase: string;
  regime_score: number;
  summary: string;
  themes: PremarketThemeSignal[];
  risk_watchlist: PremarketThemeSignal[];
  confirmation_checklist: string[];
  warnings: string[];
  disclaimer: string;
}

// === marketDataService functions ===

export async function getMarketQuotes(symbols: string[]): Promise<MarketQuoteListResponse> {
  const cleanedSymbols = symbols
    .map(symbol => symbol.trim().toUpperCase())
    .filter(Boolean);

  if (cleanedSymbols.length === 0) {
    return {
      quotes: [],
      provider: 'none',
      fetched_at: new Date().toISOString(),
      warnings: ['No symbols supplied']
    };
  }

  return apiGet<MarketQuoteListResponse>('/api/market/quotes', {
    params: {
      symbols: cleanedSymbols.join(',')
    },
    timeout: 12000
  });
}

export async function searchMarketSymbols(query: string, market?: MarketRegion | 'all'): Promise<MarketSymbolSearchResponse> {
  const cleanedQuery = query.trim();
  if (!cleanedQuery) {
    return {
      query,
      market: market && market !== 'all' ? market : null,
      candidates: [],
      provider: 'none',
      fetched_at: new Date().toISOString(),
      warnings: ['No query supplied']
    };
  }

  return apiGet<MarketSymbolSearchResponse>('/api/market/search', {
    params: {
      q: cleanedQuery,
      market: market && market !== 'all' ? market : undefined
    },
    timeout: 12000
  });
}

export async function getMarketDataLayers(symbol?: string, keyword?: string): Promise<MarketDataLayerStatusResponse> {
  return apiGet<MarketDataLayerStatusResponse>('/api/market/data-layers', {
    params: {
      symbol: symbol || undefined,
      keyword: keyword || undefined
    },
    timeout: 20000
  });
}

export async function getAshareStructuredData(
  symbol: string,
  options: { start_date?: string; end_date?: string; limit?: number } = {}
): Promise<AShareStructuredDataResponse> {
  return apiGet<AShareStructuredDataResponse>('/api/market/ashare/structured', {
    params: {
      symbol,
      ...options
    },
    timeout: 30000
  });
}

// === optionsSignalService functions ===

export async function getOptionsSignals(
  symbols: string[],
  horizonDays = 45,
  maxExpirations = 3
): Promise<OptionsSignalResponse> {
  const cleanedSymbols = symbols
    .map(symbol => symbol.trim().toUpperCase())
    .filter(Boolean);

  if (cleanedSymbols.length === 0) {
    return {
      generated_at: new Date().toISOString(),
      horizon_days: horizonDays,
      provider: 'none',
      signals: [],
      sources: [],
      warnings: ['No symbols supplied'],
      disclaimer: '期权链信号仅供投研和风控参考，不构成投资建议。'
    };
  }

  return apiGet<OptionsSignalResponse>('/api/options/signals', {
    params: {
      symbols: cleanedSymbols.join(','),
      horizon_days: horizonDays,
      max_expirations: maxExpirations
    },
    timeout: 45000
  });
}

export async function analyzeOptionsTrend(
  signal: OptionsSignal,
  horizonDays = 45,
  question?: string
): Promise<OptionsAiAnalysisResponse> {
  return apiPost<OptionsAiAnalysisResponse>('/api/options/ai-analysis', {
    signal,
    horizon_days: horizonDays,
    question,
    locale: 'zh-CN'
  }, {
    timeout: 45000
  });
}

// === premarketOpportunityService functions ===

export async function getPremarketOpportunities(): Promise<PremarketOpportunityResponse> {
  return apiGet<PremarketOpportunityResponse>('/api/agents/premarket-opportunities', {
    timeout: 60000
  });
}
