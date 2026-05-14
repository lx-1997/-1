import axios from 'axios';

const configuredApiBaseUrl = process.env.REACT_APP_API_BASE_URL?.replace(/\/$/, '');

export interface MarketQuote {
  symbol: string;
  price: number;
  change?: number | null;
  change_percent?: number | null;
  previous_close?: number | null;
  open_price?: number | null;
  high?: number | null;
  low?: number | null;
  volume?: number | null;
  currency: string;
  provider: string;
  provider_name: string;
  market_time?: string | null;
  fetched_at: string;
  is_realtime: boolean;
  delay_note: string;
}

export type MarketRegion = 'US' | 'HK' | 'CN' | 'OTHER';

export interface MarketSymbolCandidate {
  symbol: string;
  code: string;
  name: string;
  market: MarketRegion;
  exchange: string;
  security_type: string;
  quote_id?: string | null;
  provider: string;
  provider_name: string;
}

export interface MarketQuoteListResponse {
  quotes: MarketQuote[];
  provider: string;
  fetched_at: string;
  warnings: string[];
}

export interface MarketSymbolSearchResponse {
  query: string;
  market?: MarketRegion | null;
  candidates: MarketSymbolCandidate[];
  provider: string;
  fetched_at: string;
  warnings: string[];
}

function uniqueValues(values: string[]): string[] {
  return values.filter((value, index, array) => value && array.indexOf(value) === index);
}

function getMarketApiBaseUrls(): string[] {
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

export async function getMarketQuotes(symbols: string[]): Promise<MarketQuoteListResponse> {
  const cleanedSymbols = symbols
    .map(symbol => symbol.trim().toUpperCase())
    .filter(Boolean);

  if (cleanedSymbols.length === 0) {
    return {
      quotes: [],
      provider: 'none',
      fetched_at: new Date().toISOString(),
      warnings: ['No symbols supplied']
    };
  }

  const errors: string[] = [];

  for (const apiBaseUrl of getMarketApiBaseUrls()) {
    try {
      const response = await axios.get<MarketQuoteListResponse>(`${apiBaseUrl}/api/market/quotes`, {
        params: {
          symbols: cleanedSymbols.join(',')
        },
        timeout: 12000
      });
      return response.data;
    } catch (error) {
      errors.push(`${apiBaseUrl}: ${formatRequestError(error)}`);
    }
  }

  throw new Error(`Market quote API unavailable. Tried ${errors.join('; ')}`);
}

export async function searchMarketSymbols(query: string, market?: MarketRegion | 'all'): Promise<MarketSymbolSearchResponse> {
  const cleanedQuery = query.trim();
  if (!cleanedQuery) {
    return {
      query,
      market: market && market !== 'all' ? market : null,
      candidates: [],
      provider: 'none',
      fetched_at: new Date().toISOString(),
      warnings: ['No query supplied']
    };
  }

  const errors: string[] = [];

  for (const apiBaseUrl of getMarketApiBaseUrls()) {
    try {
      const response = await axios.get<MarketSymbolSearchResponse>(`${apiBaseUrl}/api/market/search`, {
        params: {
          q: cleanedQuery,
          market: market && market !== 'all' ? market : undefined
        },
        timeout: 12000
      });
      return response.data;
    } catch (error) {
      errors.push(`${apiBaseUrl}: ${formatRequestError(error)}`);
    }
  }

  throw new Error(`Market symbol search API unavailable. Tried ${errors.join('; ')}`);
}
