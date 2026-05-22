import { apiGet, apiPost } from './apiClient';
import type { FinGptTaskResponse } from './aiResearchService';

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
