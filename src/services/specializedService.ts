import { DataQuality, Stock } from '../types';
import { apiGet, apiPost } from './apiClient';
import type { FinGptTaskResponse } from './researchService';

// === aiSupplyChainService types ===

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

// === customsTradeService types ===

export interface CustomsTotalRow {
  key: 'total' | 'export' | 'import' | 'balance';
  item: string;
  current_usd_mn: number | null;
  ytd_usd_mn: number | null;
  mom_pct: number | null;
  yoy_current_pct: number | null;
  yoy_ytd_pct: number | null;
}

export interface CustomsTotalTable {
  title: string;
  source_url: string;
  download_url?: string | null;
  rows: CustomsTotalRow[];
  items: Record<string, CustomsTotalRow>;
}

export interface CustomsMonthlyPoint {
  month: string;
  total_usd_mn: number | null;
  export_usd_mn: number | null;
  import_usd_mn: number | null;
  balance_usd_mn: number | null;
  total_mom_pct?: number | null;
  export_mom_pct?: number | null;
  import_mom_pct?: number | null;
  balance_mom_pct?: number | null;
  ytd_total_usd_mn: number | null;
  ytd_export_usd_mn: number | null;
  ytd_import_usd_mn: number | null;
  ytd_balance_usd_mn: number | null;
}

export interface CustomsPartnerRow {
  name: string;
  name_zh?: string | null;
  is_region_header?: boolean;
  current_total_usd_mn: number | null;
  ytd_total_usd_mn: number | null;
  current_export_usd_mn: number | null;
  ytd_export_usd_mn: number | null;
  current_import_usd_mn: number | null;
  ytd_import_usd_mn: number | null;
  ytd_balance_usd_mn: number | null;
  mom_total_pct?: number | null;
  mom_export_pct?: number | null;
  mom_import_pct?: number | null;
  yoy_total_pct: number | null;
  yoy_export_pct: number | null;
  yoy_import_pct: number | null;
}

export interface CustomsHsSectionRow {
  name: string;
  name_zh?: string | null;
  trend_key?: string;
  code?: string | null;
  description?: string;
  description_zh?: string | null;
  is_section: boolean;
  current_export_usd_mn: number | null;
  current_trade_usd_mn?: number | null;
  current_balance_usd_mn?: number | null;
  ytd_export_usd_mn: number | null;
  current_import_usd_mn: number | null;
  ytd_import_usd_mn: number | null;
  ytd_trade_usd_mn: number | null;
  ytd_balance_usd_mn: number | null;
  mom_trade_pct?: number | null;
  mom_export_pct?: number | null;
  mom_import_pct?: number | null;
  yoy_export_pct: number | null;
  yoy_import_pct: number | null;
}

export interface CustomsMajorExportRow {
  direction?: 'export' | 'import';
  trend_key?: string;
  commodity: string;
  commodity_zh?: string | null;
  quantity_unit: string;
  current_quantity: number | null;
  current_value_usd_mn: number | null;
  ytd_quantity: number | null;
  ytd_value_usd_mn: number | null;
  previous_ytd_quantity: number | null;
  previous_ytd_value_usd_mn: number | null;
  quantity_mom_pct?: number | null;
  value_mom_pct?: number | null;
  quantity_yoy_pct: number | null;
  value_yoy_pct: number | null;
}

export interface CustomsHsTrendPoint {
  month: string;
  trade_usd_mn: number | null;
  export_usd_mn: number | null;
  import_usd_mn: number | null;
  balance_usd_mn: number | null;
}

export interface CustomsCommodityTrendPoint {
  month: string;
  direction: 'export' | 'import';
  value_usd_mn: number | null;
  quantity: number | null;
}

export interface CustomsTradeSource {
  name: string;
  url: string;
  type: string;
  note: string;
}

export interface CustomsHsDetailCandidate {
  code: string;
  name?: string | null;
  name_zh?: string | null;
  aliases?: string[];
  industry?: string | null;
  aggr_level?: number | null;
  is_leaf?: boolean;
  quantity_unit?: string | null;
  source?: string;
  matched_by?: string;
}

export interface CustomsHsDetailPoint {
  period: string;
  month: string;
  export_value_usd: number | null;
  import_value_usd: number | null;
  trade_value_usd: number | null;
  balance_value_usd: number | null;
  export_quantity: number | null;
  export_quantity_unit?: string | null;
  import_quantity: number | null;
  import_quantity_unit?: string | null;
  export_unit_value_usd?: number | null;
  import_unit_value_usd?: number | null;
  export_mom_pct?: number | null;
  import_mom_pct?: number | null;
  trade_mom_pct?: number | null;
  cmd_desc?: string | null;
}

export interface CustomsHsDetailPartner {
  direction: 'export' | 'import';
  partner: string;
  partner_zh?: string | null;
  partner_iso?: string | null;
  value_usd: number | null;
  quantity: number | null;
  quantity_unit?: string | null;
  unit_value_usd?: number | null;
}

export interface CustomsHsDetailSearchResponse {
  query: string;
  source_status: 'live' | 'empty' | 'partial';
  candidates: CustomsHsDetailCandidate[];
  sources: CustomsTradeSource[];
  warnings: string[];
}

export interface CustomsHsDetailSnapshot {
  generated_at: string;
  source_status: 'live' | 'empty' | 'partial';
  query?: string | null;
  code?: string | null;
  currency: string;
  unit: string;
  coverage: string;
  product: CustomsHsDetailCandidate | null;
  latest_period?: string | null;
  month_label: string;
  monthly_points: CustomsHsDetailPoint[];
  top_export_partners: CustomsHsDetailPartner[];
  top_import_partners: CustomsHsDetailPartner[];
  candidates: CustomsHsDetailCandidate[];
  sources: CustomsTradeSource[];
  warnings: string[];
}

export interface CustomsTradeSnapshot {
  generated_at: string;
  source_status: 'live' | 'partial';
  observed_month?: string | null;
  month_label: string;
  currency: string;
  unit: string;
  total: CustomsTotalTable;
  monthly_trend: CustomsMonthlyPoint[];
  partners: CustomsPartnerRow[];
  hs_sections: CustomsHsSectionRow[];
  hs_chapters: CustomsHsSectionRow[];
  major_exports: CustomsMajorExportRow[];
  major_imports: CustomsMajorExportRow[];
  hs_trends: Record<string, CustomsHsTrendPoint[]>;
  commodity_trends: Record<string, CustomsCommodityTrendPoint[]>;
  history_months: string[];
  sources: CustomsTradeSource[];
  warnings: string[];
}

// === multiMarketDecisionService types ===

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
  data_quality?: DataQuality;
}

// === paymentService types ===

export interface PaymentRequest {
  orderId: string;
  paymentMethod: 'wechat' | 'alipay';
  amount: number;
  description?: string;
}

export interface PaymentResponse {
  success: boolean;
  qrCode?: string;
  paymentUrl?: string;
  transactionId?: string;
  message?: string;
}

export interface PaymentStatus {
  status: 'pending' | 'paid' | 'failed' | 'cancelled';
  transactionId?: string;
  paidAt?: string;
}

// === aiSupplyChainService functions ===

export async function fetchAiSupplyChainCapacityTrends(horizon: AiSupplyChainHorizon = '3m'): Promise<AiSupplyChainCapacityTrends> {
  return apiGet<AiSupplyChainCapacityTrends>(`/api/ai-supply-chain/capacity-trends?horizon=${horizon}`, {
    timeout: horizon === '1y' ? 40000 : 30000
  });
}

// === customsTradeService functions ===

export async function fetchCustomsTradeSnapshot(): Promise<CustomsTradeSnapshot> {
  return apiGet<CustomsTradeSnapshot>('/api/customs-trade/snapshot', {
    timeout: 40000
  });
}

export async function searchCustomsHsDetails(query: string): Promise<CustomsHsDetailSearchResponse> {
  return apiGet<CustomsHsDetailSearchResponse>('/api/customs-trade/hs-detail/search', {
    params: { q: query, limit: 20 },
    timeout: 30000
  });
}

export async function fetchCustomsHsDetail(payload: {
  query?: string | null;
  code?: string | null;
  months?: number;
}): Promise<CustomsHsDetailSnapshot> {
  return apiGet<CustomsHsDetailSnapshot>('/api/customs-trade/hs-detail', {
    params: {
      query: payload.query || undefined,
      code: payload.code || undefined,
      months: payload.months || 12
    },
    timeout: 60000
  });
}

export async function analyzeCustomsTrade(payload: {
  focus?: string | null;
  focus_key?: string | null;
  focus_type?: string | null;
  selected_tab?: string | null;
} = {}): Promise<FinGptTaskResponse> {
  return apiPost<FinGptTaskResponse>('/api/customs-trade/ai-analysis', {
    ...payload,
    locale: 'zh-CN'
  }, {
    timeout: 110000
  });
}

// === multiMarketDecisionService functions ===

export async function runMultiMarketDecision(
  payload: MultiMarketDecisionRequest
): Promise<MultiMarketDecisionResponse> {
  return apiPost<MultiMarketDecisionResponse>('/api/decision/multi-market', payload, {
    timeout: 30000
  });
}

// === paymentService functions ===

export async function createPayment(request: PaymentRequest): Promise<PaymentResponse> {
  return new Promise((resolve) => {
    setTimeout(() => {
      const qrCode = request.paymentMethod === 'wechat'
        ? `weixin://wxpay/bizpayurl?pr=${request.orderId}`
        : `https://qr.alipay.com/${request.orderId}`;

      resolve({
        success: true,
        qrCode,
        transactionId: `${request.paymentMethod.toUpperCase()}${Date.now()}`,
        message: '支付订单创建成功'
      });
    }, 1000);
  });
}

export async function checkPaymentStatus(orderId: string): Promise<PaymentStatus> {
  return new Promise((resolve) => {
    setTimeout(() => {
      resolve({
        status: 'paid',
        transactionId: `TXN${Date.now()}`,
        paidAt: new Date().toISOString()
      });
    }, 500);
  });
}

export async function cancelPayment(orderId: string): Promise<boolean> {
  return new Promise((resolve) => {
    setTimeout(() => {
      resolve(true);
    }, 500);
  });
}

export async function requestRefund(orderId: string, reason?: string): Promise<boolean> {
  return new Promise((resolve) => {
    setTimeout(() => {
      resolve(true);
    }, 1000);
  });
}

export function pollPaymentStatus(
  orderId: string,
  onStatusChange: (status: PaymentStatus) => void,
  maxAttempts: number = 60,
  interval: number = 2000
): () => void {
  let attempts = 0;
  let cancelled = false;

  const poll = async () => {
    if (cancelled || attempts >= maxAttempts) {
      return;
    }

    attempts++;
    try {
      const status = await checkPaymentStatus(orderId);
      onStatusChange(status);

      if (status.status === 'paid' || status.status === 'failed' || status.status === 'cancelled') {
        return;
      }
    } catch (error) {
      console.error('查询支付状态失败:', error);
    }

    if (!cancelled && attempts < maxAttempts) {
      setTimeout(poll, interval);
    }
  };

  poll();

  return () => {
    cancelled = true;
  };
}
