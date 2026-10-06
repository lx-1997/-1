import { apiDelete, apiGet, apiPost } from './apiClient';
import { RiskSummaryResponse } from './riskService';

export type QuantStrategyKey = 'momentum' | 'mean_reversion' | 'trend_following' | 'breakout' | 'defensive';
export type QuantMarket = 'AUTO' | 'US' | 'CN' | 'HK';
export type RebalanceFrequency = 'daily' | 'weekly' | 'monthly';

export interface QuantRiskRules {
  max_position_size_pct: number;
  max_total_exposure_pct: number;
  max_short_exposure_pct: number;
  max_sector_exposure_pct: number;
  max_drawdown_pct: number;
  daily_loss_limit_pct: number;
  stop_loss_pct: number;
  take_profit_pct: number;
  cooldown_days: number;
  allow_reentry: boolean;
}

export interface QuantDataSourceDetail {
  symbol?: string;
  normalized_symbol?: string;
  market?: string;
  exchange?: string;
  provider_symbol?: string;
  asset_class?: string;
  source?: string;
  source_name?: string;
  quality?: string;
  adjustment?: string;
  is_synthetic?: boolean;
  total_bars?: number;
  start_date?: string;
  end_date?: string;
  fetched_at?: string;
  cached?: boolean;
  warnings?: string[];
}

export interface QuantBacktestResponse {
  generated_at: string;
  name: string;
  market: string;
  symbols: string[];
  benchmark: string;
  start_date: string;
  end_date: string;
  initial_capital: number;
  rules: QuantRiskRules;
  metrics: {
    risk: Record<string, number>;
    baseline: Record<string, number>;
    benchmark: Record<string, number>;
    improvement: Record<string, number>;
    rule_hits: Record<string, number>;
    summary: string;
    execution: {
      commission_bps: number;
      slippage_bps: number;
      short_borrow_bps: number;
      transaction_cost: number;
      borrow_cost: number;
      turnover_notional: number;
      turnover_ratio: number;
      rebalance_count: number;
      signal_timing: string;
      currency_mode: 'constant_currency' | 'local_market_currency';
    };
    attribution: {
      realized_pnl_by_symbol: Record<string, number>;
      latest_strategy_weights: Record<string, number>;
      final_weights: Record<string, number>;
      final_gross_exposure_pct: number;
      final_net_exposure_pct: number;
    };
  };
  events: Array<{ date: string; type: string; message: string; symbol?: string | null; value?: number | null }>;
  trades_log: Array<Record<string, unknown>>;
  equity_curve: number[];
  baseline_curve: number[];
  benchmark_curve: number[];
  dates: string[];
  data_sources: Record<string, string>;
  data_source_details: Record<string, QuantDataSourceDetail>;
  disclaimer: string;
}

export interface QuantLabRequest {
  name: string;
  market: QuantMarket;
  strategy_key: QuantStrategyKey;
  symbols: string[];
  start_date: string;
  end_date: string;
  benchmark: string;
  initial_capital: number;
  lookback: number;
  top_n: number;
  allow_short: boolean;
  rebalance_frequency: RebalanceFrequency;
  commission_bps: number;
  slippage_bps: number;
  short_borrow_bps: number;
  min_trade_notional: number;
  risk_rules: QuantRiskRules;
  sector_map?: Record<string, string>;
  platform_context?: Record<string, Record<string, unknown>>;
}

export interface QuantSignalRecord {
  symbol: string;
  name: string;
  sector: string;
  market: string;
  latest_close: number;
  score: number;
  action: 'buy' | 'hold' | 'sell' | 'short';
  confidence: number;
  target_weight: number;
  current_weight: number;
  reasons: string[];
  risk_flags: string[];
  metrics: Record<string, number>;
  platform_context?: Record<string, unknown>;
}

export interface QuantOrderPlan {
  symbol: string;
  side: 'buy' | 'sell' | 'short' | 'cover' | 'hold';
  current_weight: number;
  target_weight: number;
  delta_weight: number;
  quantity: number;
  notional: number;
  reason: string;
}

export interface QuantAllocationSummary {
  gross_exposure_pct: number;
  net_exposure_pct: number;
  cash_buffer_pct: number;
  selected_count: number;
  buy_count: number;
  sell_count: number;
  short_count: number;
  hold_count: number;
  max_target_weight_pct: number;
  notes: string[];
}

export interface QuantLabResponse {
  generated_at: string;
  name: string;
  market: string;
  strategy_key: QuantStrategyKey;
  strategy_label: string;
  symbols: string[];
  benchmark: string;
  initial_capital: number;
  lookback: number;
  signals: QuantSignalRecord[];
  orders: QuantOrderPlan[];
  allocation: QuantAllocationSummary;
  portfolio_context: Record<string, any>;
  backtest: QuantBacktestResponse;
  risk_summary: RiskSummaryResponse | null;
  notes: string[];
  data_sources: Record<string, string>;
  data_source_details: Record<string, QuantDataSourceDetail>;
  platform_context?: { source?: string; matched_symbols?: number; available_fields?: string[]; score_overlay_cap?: number; historical_backtest_included?: boolean };
  disclaimer: string;
}

export type QuantJobStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';

export interface QuantJobResponse {
  job_id: string;
  status: QuantJobStatus;
  progress: number;
  stage: string;
  created_at: string;
  updated_at: string;
  completed_at?: string | null;
  result?: QuantLabResponse | null;
  error?: string;
}

export async function runQuantLab(request: QuantLabRequest): Promise<QuantLabResponse> {
  return apiPost<QuantLabResponse>('/api/quant/lab', request, { timeout: 300000 });
}

export async function startQuantLabJob(request: QuantLabRequest): Promise<QuantJobResponse> {
  return apiPost<QuantJobResponse>('/api/quant/lab/jobs', request, { timeout: 15000 });
}

export async function getQuantLabJob(jobId: string): Promise<QuantJobResponse> {
  return apiGet<QuantJobResponse>(`/api/quant/lab/jobs/${encodeURIComponent(jobId)}`, { timeout: 15000 });
}

export async function cancelQuantLabJob(jobId: string): Promise<QuantJobResponse> {
  return apiDelete<QuantJobResponse>(`/api/quant/lab/jobs/${encodeURIComponent(jobId)}`, { timeout: 15000 });
}
