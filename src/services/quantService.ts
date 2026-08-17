import { apiPost } from './apiClient';
import { RiskSummaryResponse } from './riskService';

export type QuantStrategyKey = 'momentum' | 'mean_reversion' | 'trend_following' | 'breakout' | 'defensive';

export interface QuantRiskRules {
  max_position_size_pct: number;
  max_total_exposure_pct: number;
  max_sector_exposure_pct: number;
  max_drawdown_pct: number;
  daily_loss_limit_pct: number;
  stop_loss_pct: number;
  take_profit_pct: number;
  cooldown_days: number;
  allow_reentry: boolean;
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
    risk: Record<string, any>;
    baseline: Record<string, any>;
    benchmark: Record<string, any>;
    improvement: Record<string, number>;
    rule_hits: Record<string, number>;
    summary: string;
  };
  events: Array<{ date: string; type: string; message: string; symbol?: string | null; value?: number | null }>;
  trades_log: Array<Record<string, any>>;
  equity_curve: number[];
  baseline_curve: number[];
  benchmark_curve: number[];
  dates: string[];
  data_sources: Record<string, string>;
  disclaimer: string;
}

export interface QuantLabRequest {
  name: string;
  market?: string;
  strategy_key: QuantStrategyKey;
  symbols: string[];
  start_date: string;
  end_date: string;
  benchmark: string;
  initial_capital: number;
  lookback: number;
  top_n: number;
  allow_short: boolean;
  risk_rules: QuantRiskRules;
  sector_map?: Record<string, string>;
}

export interface QuantSignalRecord {
  symbol: string;
  name: string;
  sector: string;
  market: string;
  latest_close: number;
  score: number;
  action: 'buy' | 'hold' | 'sell';
  confidence: number;
  target_weight: number;
  current_weight: number;
  reasons: string[];
  risk_flags: string[];
  metrics: Record<string, number>;
}

export interface QuantOrderPlan {
  symbol: string;
  side: 'buy' | 'sell' | 'hold';
  current_weight: number;
  target_weight: number;
  delta_weight: number;
  quantity: number;
  notional: number;
  reason: string;
}

export interface QuantAllocationSummary {
  gross_exposure_pct: number;
  cash_buffer_pct: number;
  selected_count: number;
  buy_count: number;
  sell_count: number;
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
  disclaimer: string;
}

export async function runQuantLab(request: QuantLabRequest): Promise<QuantLabResponse> {
  return apiPost<QuantLabResponse>('/api/quant/lab', request, { timeout: 60000 });
}
