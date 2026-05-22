import { apiGet } from './apiClient';

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

  return apiGet<MarketQuoteListResponse>('/api/market/quotes', {
    params: {
      symbols: cleanedSymbols.join(',')
    },
    timeout: 12000
  });
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

  return apiGet<MarketSymbolSearchResponse>('/api/market/search', {
    params: {
      q: cleanedQuery,
      market: market && market !== 'all' ? market : undefined
    },
    timeout: 12000
  });
}
