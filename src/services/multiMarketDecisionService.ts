import { Stock } from '../types';
import { apiPost } from './apiClient';

export type DecisionMarket = 'CN' | 'HK' | 'US' | 'OTHER';
export type DecisionMode = 'research' | 'backtest' | 'paper';
export type DecisionRiskProfile = '保守' | '稳健' | '进取' | '专业';
export type DecisionDataProfile = 'china_stable' | 'offline' | 'mixed';
export type DecisionHorizon = '5日' | '10日' | '20日' | '60日';

export interface MultiMarketDecisionRequest {
  markets: DecisionMarket[];
  horizon: DecisionHorizon;
  mode: DecisionMode;
  risk_profile: DecisionRiskProfile;
  data_profile: DecisionDataProfile;
  objective: string;
  stocks: Stock[];
  notes?: string;
  min_score: number;
}

export interface DecisionDependency {
  name: string;
  role: string;
  markets: DecisionMarket[];
  required: boolean;
  mainland_ready: boolean;
  install_hint: string;
  config_keys: string[];
  notes: string;
}

export interface DecisionModuleStatus {
  key: string;
  name: string;
  market: DecisionMarket;
  purpose: string;
  data_sources: string[];
  research_engine: string;
  backtest_engine: string;
  execution_engine?: string | null;
  status: 'ready' | 'partial' | 'planned';
  readiness: number;
  blocked_by: string[];
  notes: string[];
}

export interface SectorOpinion {
  name: string;
  score: number;
  stance: '强势' | '中性' | '回避';
  rationale: string;
}

export interface MarketStyleSignal {
  market: DecisionMarket;
  label: string;
  trend_score: number;
  risk_regime: '进攻' | '均衡' | '防守';
  dominant_factors: string[];
  sector_opinions: SectorOpinion[];
  avoid_sectors: string[];
  rationale: string;
}

export interface CandidateOpinion {
  symbol: string;
  name: string;
  market: DecisionMarket;
  sector: string;
  action: '重点跟踪' | '观察' | '回避';
  score: number;
  surge_probability: number;
  expected_horizon: string;
  evidence: string[];
  risk_flags: string[];
  invalidation: string[];
}

export interface BacktestPlan {
  market: DecisionMarket;
  engine: string;
  data_requirements: string[];
  trading_rules: string[];
  metrics: string[];
  next_step: string;
}

export interface MultiMarketDecisionResponse {
  provider: string;
  model: string;
  generated_at: string;
  mode: DecisionMode;
  risk_profile: DecisionRiskProfile;
  readiness_score: number;
  summary: string;
  modules: DecisionModuleStatus[];
  dependencies: DecisionDependency[];
  market_styles: MarketStyleSignal[];
  candidates: CandidateOpinion[];
  portfolio_actions: string[];
  backtest_plan: BacktestPlan[];
  warnings: string[];
  disclaimer: string;
}

export async function runMultiMarketDecision(
  payload: MultiMarketDecisionRequest
): Promise<MultiMarketDecisionResponse> {
  return apiPost<MultiMarketDecisionResponse>('/api/decision/multi-market', payload, {
    timeout: 30000
  });
}
