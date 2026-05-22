import { apiGet, apiPost } from './apiClient';

export type OptionsSourceStatusValue = 'ready' | 'fallback' | 'blocked' | 'unavailable';
export type OptionsSignalStatus = 'delayed' | 'partial' | 'unavailable';
export type OptionsDirection = '偏多' | '中性' | '偏空' | '不可判定';
export type OptionsConviction = '高' | '中' | '低';
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
