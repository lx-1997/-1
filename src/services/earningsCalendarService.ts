import axios from 'axios';

const configuredApiBaseUrl = process.env.REACT_APP_API_BASE_URL?.replace(/\/$/, '');

export type EarningsEventStatus = 'scheduled' | 'reported' | 'watchlist_template';
export type EarningsEventConfidence = 'confirmed' | 'estimated' | 'pending_provider';

export interface EarningsCalendarEvent {
  symbol: string;
  name: string;
  report_date?: string | null;
  fiscal_date_ending?: string | null;
  eps_estimate?: number | null;
  eps_actual?: number | null;
  eps_surprise_percent?: number | null;
  revenue_estimate?: number | null;
  revenue_actual?: number | null;
  revenue_surprise_percent?: number | null;
  currency: string;
  time_of_day?: string | null;
  provider: string;
  source_name: string;
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

function uniqueValues(values: string[]): string[] {
  return values.filter((value, index, array) => value && array.indexOf(value) === index);
}

function getApiBaseUrls(): string[] {
  const candidates: string[] = [];

  if (configuredApiBaseUrl) {
    candidates.push(configuredApiBaseUrl);
  }

  if (typeof window !== 'undefined') {
    const { protocol, hostname } = window.location;
    if (hostname && hostname !== 'localhost' && hostname !== '127.0.0.1') {
      candidates.push(`${protocol}//${hostname}:8300`);
    }
  }

  candidates.push('http://127.0.0.1:8300');
  candidates.push('http://localhost:8300');

  return uniqueValues(candidates);
}

function formatRequestError(error: unknown): string {
  if (axios.isAxiosError(error)) {
    if (error.response) {
      return `HTTP ${error.response.status}`;
    }
    return error.message || 'Network Error';
  }

  return error instanceof Error ? error.message : 'Unknown Error';
}

export async function getEarningsCalendar(
  symbols: string[],
  horizon: '3month' | '6month' | '12month' = '3month'
): Promise<EarningsCalendarResponse> {
  const cleanedSymbols = symbols
    .map(symbol => symbol.trim().toUpperCase())
    .filter(Boolean);

  if (cleanedSymbols.length === 0) {
    return {
      events: [],
      provider: 'none',
      fetched_at: new Date().toISOString(),
      warnings: ['No symbols supplied']
    };
  }

  const errors: string[] = [];

  for (const apiBaseUrl of getApiBaseUrls()) {
    try {
      const response = await axios.get<EarningsCalendarResponse>(`${apiBaseUrl}/api/earnings/calendar`, {
        params: {
          symbols: cleanedSymbols.join(','),
          horizon
        },
        timeout: 30000
      });
      return response.data;
    } catch (error) {
      errors.push(`${apiBaseUrl}: ${formatRequestError(error)}`);
    }
  }

  throw new Error(`Earnings calendar API unavailable. Tried ${errors.join('; ')}`);
}
