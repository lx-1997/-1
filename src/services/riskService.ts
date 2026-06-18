import axios from 'axios';
import { getApiBaseUrls, formatErrorMessage } from './apiClient';

const API_BASE = () => getApiBaseUrls()[0];

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
  try {
    const resp = await axios.post(`${API_BASE()}/api/risk/greeks`, request);
    return resp.data;
  } catch (error) {
    throw new Error(formatErrorMessage(error));
  }
}

export async function getPositions(status?: string): Promise<PositionRecord[]> {
  try {
    const params = status ? { status } : {};
    const resp = await axios.get(`${API_BASE()}/api/risk/positions`, { params });
    return resp.data.positions;
  } catch (error) {
    throw new Error(formatErrorMessage(error));
  }
}

export async function getPosition(positionId: string): Promise<PositionRecord> {
  try {
    const resp = await axios.get(`${API_BASE()}/api/risk/positions/${positionId}`);
    return resp.data;
  } catch (error) {
    throw new Error(formatErrorMessage(error));
  }
}

export async function createPosition(request: PositionCreateRequest): Promise<PositionRecord> {
  try {
    const resp = await axios.post(`${API_BASE()}/api/risk/positions`, request);
    return resp.data;
  } catch (error) {
    throw new Error(formatErrorMessage(error));
  }
}

export async function updatePosition(positionId: string, updates: Partial<PositionRecord>): Promise<PositionRecord> {
  try {
    const resp = await axios.put(`${API_BASE()}/api/risk/positions/${positionId}`, updates);
    return resp.data;
  } catch (error) {
    throw new Error(formatErrorMessage(error));
  }
}

export async function deletePosition(positionId: string): Promise<void> {
  try {
    await axios.delete(`${API_BASE()}/api/risk/positions/${positionId}`);
  } catch (error) {
    throw new Error(formatErrorMessage(error));
  }
}

export async function closePosition(positionId: string, exitPrice: number, exitReason?: string): Promise<PositionRecord> {
  try {
    const resp = await axios.post(`${API_BASE()}/api/risk/positions/${positionId}/close`, {
      exit_price: exitPrice,
      exit_reason: exitReason || ''
    });
    return resp.data;
  } catch (error) {
    throw new Error(formatErrorMessage(error));
  }
}

export async function refreshPrices(): Promise<{ updated_count: number }> {
  try {
    const resp = await axios.post(`${API_BASE()}/api/risk/positions/refresh`);
    return resp.data;
  } catch (error) {
    throw new Error(formatErrorMessage(error));
  }
}

export async function getRiskSummary(): Promise<RiskSummaryResponse> {
  try {
    const resp = await axios.get(`${API_BASE()}/api/risk/summary`);
    return resp.data;
  } catch (error) {
    throw new Error(formatErrorMessage(error));
  }
}

export async function getRiskLimits(): Promise<RiskLimitRecord[]> {
  try {
    const resp = await axios.get(`${API_BASE()}/api/risk/limits`);
    return resp.data;
  } catch (error) {
    throw new Error(formatErrorMessage(error));
  }
}

export async function updateRiskLimit(key: string, value: number, enabled?: boolean): Promise<RiskLimitRecord> {
  try {
    const resp = await axios.put(`${API_BASE()}/api/risk/limits/${key}`, { value, enabled });
    return resp.data;
  } catch (error) {
    throw new Error(formatErrorMessage(error));
  }
}

export async function getPnlSummary(): Promise<PnlSummaryResponse> {
  try {
    const resp = await axios.get(`${API_BASE()}/api/risk/pnl`);
    return resp.data;
  } catch (error) {
    throw new Error(formatErrorMessage(error));
  }
}

export async function getPnlRecords(positionId?: string, limit?: number): Promise<PnlRecord[]> {
  try {
    const params: Record<string, any> = {};
    if (positionId) params.position_id = positionId;
    if (limit) params.limit = limit;
    const resp = await axios.get(`${API_BASE()}/api/risk/pnl/records`, { params });
    return resp.data;
  } catch (error) {
    throw new Error(formatErrorMessage(error));
  }
}