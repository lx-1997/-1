/** A normalized display model: prices are amounts, changePercent is percentage points. */
export type QuoteQuality = 'live' | 'delayed' | 'stale' | 'mock' | 'unavailable';
export type QuoteMarket = 'CN' | 'HK' | 'US' | 'OTHER';
export type QuoteColorConvention = 'cn' | 'international';

export interface QuoteItem {
  /** Stable identity. Use an exchange-qualified symbol when codes can collide. */
  id: string;
  symbol: string;
  name: string;
  market?: QuoteMarket;
  currency?: string;
  /** Missing quotes must use null. Zero is a valid price. */
  price: number | null;
  changePercent: number | null;
  /** Supplied by the data adapter; display components never infer that data is live. */
  quality: QuoteQuality;
  source?: string;
  /** ISO timestamp with an explicit timezone offset or Z. */
  updatedAt?: string | null;
  delayNote?: string;
}
