import { apiGet } from './apiClient';

export type EarningsEventStatus = 'scheduled' | 'reported' | 'watchlist_template';
export type EarningsEventConfidence = 'confirmed' | 'estimated' | 'pending_provider';

export interface EarningsCalendarEvent {
  symbol: string;
  name: string;
  report_date?: string | null;
  fiscal_date_ending?: string | null;
  eps_estimate?: number | null;
  eps_high_estimate?: number | null;
  eps_low_estimate?: number | null;
  eps_actual?: number | null;
  eps_surprise_percent?: number | null;
  revenue_estimate?: number | null;
  revenue_actual?: number | null;
  revenue_surprise_percent?: number | null;
  market_cap?: number | null;
  analyst_count?: number | null;
  revision_up_count?: number | null;
  revision_down_count?: number | null;
  last_year_report_date?: string | null;
  last_year_eps?: number | null;
  currency: string;
  time_of_day?: string | null;
  provider: string;
  source_name: string;
  source_url?: string | null;
  data_as_of?: string | null;
  days_until_report?: number | null;
  is_date_confirmed?: boolean;
  status: EarningsEventStatus;
  confidence: EarningsEventConfidence;
  watch_items: string[];
  focus_metrics: string[];
  risk_flags: string[];
  related_symbols: string[];
}

export interface EarningsCalendarResponse {
  events: EarningsCalendarEvent[];
  provider: string;
  fetched_at: string;
  warnings: string[];
}

export async function getEarningsCalendar(
  symbols: string[],
  horizon: '3month' | '6month' | '12month' = '3month',
  minMarketCap?: number,
  includeAll = false
): Promise<EarningsCalendarResponse> {
  const cleanedSymbols = symbols
    .map(symbol => symbol.trim().toUpperCase())
    .filter(Boolean);

  if (cleanedSymbols.length === 0 && !minMarketCap && !includeAll) {
    return {
      events: [],
      provider: 'none',
      fetched_at: new Date().toISOString(),
      warnings: ['No symbols supplied']
    };
  }

  return apiGet<EarningsCalendarResponse>('/api/earnings/calendar', {
    params: {
      symbols: cleanedSymbols.join(','),
      horizon,
      ...(minMarketCap ? { min_market_cap: minMarketCap } : {}),
      ...(includeAll ? { include_all: true } : {})
    },
    timeout: 45000
  });
}
