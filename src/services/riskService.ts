import { apiGet, apiPost, apiPut, apiDelete } from './apiClient';


export interface PositionRiskMetrics {
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
  notional_value: number;
  cost_basis: number;
  risk_reward_ratio: number;
  distance_to_stop_pct: number;
  distance_to_target_pct: number;
  risk_per_share: number;
  reward_per_share: number;
}

export interface PositionRecord {
  id: string;
  symbol: string;
  name: string;
  market: string;
  asset_class: string;
  direction: string;
  entry_price: number;
  entry_date: string;
  quantity: number;
  current_price: number | null;
  stop_loss: number | null;
  take_profit: number | null;
  position_size_pct: number;
  sector: string;
  strategy: string;
  notes: string;
  tags: string[];
  greeks: Record<string, number>;
  created_at: string;
  updated_at: string;
  closed_at: string | null;
  status: string;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
  notional_value: number;
  cost_basis: number;
  risk_reward_ratio: number;
  distance_to_stop_pct: number;
  distance_to_target_pct: number;
}

export interface PortfolioDirectionExposure {
  long: number;
  short: number;
  net: number;
  gross: number;
}

export interface PortfolioMetrics {
  total_value: number;
  total_cost: number;
  total_pnl: number;
  total_pnl_pct: number;
  position_count: number;
  open_count: number;
  win_rate: number;
  sector_exposure: Record<string, number>;
  asset_class_exposure: Record<string, number>;
  direction_exposure: PortfolioDirectionExposure;
  concentration_risk: string;
  risk_flags: string[];
}

export interface VaRResult {
  historical_var: number;
  parametric_var: number;
  var_amount: number;
  var_pct: number;
  confidence: number;
  observations: number;
  mean_return: number;
  std_dev: number;
  sharpe_ratio: number;
  method: string;
}

export interface RiskAlert {
  level: 'info' | 'warning' | 'danger';
  type: string;
  message: string;
  metric: string;
  value: number;
  limit: number;
  symbol?: string;
}

export interface RiskLimitRecord {
  id: string;
  key: string;
  label: string;
  value: number;
  unit: string;
  enabled: boolean;
  description: string;
  created_at: string;
  updated_at: string;
}

export interface RiskSummaryResponse {
  portfolio: PortfolioMetrics;
  var: VaRResult;
  open_positions: PositionRecord[];
  closed_positions_count: number;
  alerts: RiskAlert[];
  risk_limits: Record<string, RiskLimitRecord>;
  generated_at: string;
}

export interface GreeksRequest {
  underlying_price: number;
  strike: number;
  days_to_expiry: number;
  risk_free_rate?: number;
  implied_vol?: number;
  option_type?: 'call' | 'put';
}

export interface GreeksResponse {
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
  rho: number;
  iv: number;
  theoretical_price: number;
}

export interface PnlRecord {
  id: string;
  position_id: string;
  symbol: string;
  date: string;
  entry_price: number;
  exit_price: number | null;
  quantity: number;
  realized_pnl: number;
  unrealized_pnl: number;
  total_pnl: number;
  return_pct: number;
  holding_days: number;
  exit_reason: string;
  created_at: string;
}

export interface PnlSummaryResponse {
  total_realized_pnl: number;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate: number;
  avg_win: number;
  avg_loss: number;
  profit_factor: number;
  total_return_pct: number;
  best_trade: PnlRecord | null;
  worst_trade: PnlRecord | null;
  avg_holding_days: number;
}

export interface RiskBacktestRules {
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

export interface RiskBacktestRequest {
  name: string;
  market?: string;
  symbols: string[];
  start_date: string;
  end_date: string;
  initial_capital: number;
  benchmark: string;
  rules: RiskBacktestRules;
  sector_map?: Record<string, string>;
}

export interface RiskBacktestEvent {
  date: string;
  type: string;
  message: string;
  symbol?: string | null;
  value?: number | null;
}

export interface RiskBacktestResponse {
  generated_at: string;
  name: string;
  market: string;
  symbols: string[];
  benchmark: string;
  start_date: string;
  end_date: string;
  initial_capital: number;
  rules: RiskBacktestRules;
  metrics: {
    risk: Record<string, any>;
    baseline: Record<string, any>;
    benchmark: Record<string, any>;
    improvement: Record<string, number>;
    rule_hits: Record<string, number>;
    summary: string;
  };
  events: RiskBacktestEvent[];
  trades_log: Array<Record<string, any>>;
  equity_curve: number[];
  baseline_curve: number[];
  benchmark_curve: number[];
  dates: string[];
  data_sources: Record<string, string>;
  disclaimer: string;
}

export interface PositionCreateRequest {
  symbol: string;
  name?: string;
  market?: string;
  asset_class?: string;
  direction?: string;
  entry_price: number;
  quantity: number;
  stop_loss?: number;
  take_profit?: number;
  position_size_pct?: number;
  sector?: string;
  strategy?: string;
  notes?: string;
  tags?: string[];
  greeks?: Record<string, number>;
}

export async function calculateGreeks(request: GreeksRequest): Promise<GreeksResponse> {
  return apiPost('/api/risk/greeks', request);
}
export async function getPositions(status?: string): Promise<PositionRecord[]> {
  const response = await apiGet<{ positions: PositionRecord[] }>('/api/risk/positions', { params: status ? { status } : {} });
  return response.positions;
}
export async function getPosition(positionId: string): Promise<PositionRecord> {
  return apiGet(`/api/risk/positions/${positionId}`);
}
export async function createPosition(request: PositionCreateRequest): Promise<PositionRecord> {
  return apiPost('/api/risk/positions', request);
}
export async function updatePosition(positionId: string, updates: Partial<PositionRecord>): Promise<PositionRecord> {
  return apiPut(`/api/risk/positions/${positionId}`, updates);
}
export async function deletePosition(positionId: string): Promise<void> {
  return apiDelete(`/api/risk/positions/${positionId}`);
}
export async function closePosition(positionId: string, exitPrice: number, exitReason?: string): Promise<PositionRecord> {
  return apiPost(`/api/risk/positions/${positionId}/close`, { exit_price: exitPrice, exit_reason: exitReason || '' });
}
export async function refreshPrices(): Promise<{ updated_count: number }> {
  return apiPost('/api/risk/positions/refresh');
}
export async function getRiskSummary(): Promise<RiskSummaryResponse> {
  return apiGet('/api/risk/summary');
}
export async function getRiskLimits(): Promise<RiskLimitRecord[]> {
  return apiGet('/api/risk/limits');
}
export async function updateRiskLimit(key: string, value: number, enabled?: boolean): Promise<RiskLimitRecord> {
  return apiPut(`/api/risk/limits/${key}`, { value, enabled });
}
export async function getPnlSummary(): Promise<PnlSummaryResponse> {
  return apiGet('/api/risk/pnl');
}
export async function getPnlRecords(positionId?: string, limit?: number): Promise<PnlRecord[]> {
  return apiGet('/api/risk/pnl/records', { params: { ...(positionId ? { position_id: positionId } : {}), ...(limit ? { limit } : {}) } });
}
export async function runRiskBacktest(request: RiskBacktestRequest): Promise<RiskBacktestResponse> {
  return apiPost('/api/risk/backtest', request);
}
