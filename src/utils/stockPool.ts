import type { MarketQuote, MarketSymbolCandidate } from '../services/marketService';
import type { Stock } from '../types';

export const STOCK_POOL_STORAGE_KEY = 'deepfocus.stockPool.v1';
export const DEFAULT_STOCK_SUBSCRIPTION_TOPICS: NonNullable<Stock['subscriptionTopics']> = [
  'price',
  'news',
  'earnings',
  'research'
];

const toFiniteNumber = (value?: number | null): number | undefined => {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
};

export const marketCurrency = (market?: string): string => {
  if (market === 'HK') return 'HKD';
  if (market === 'CN') return 'CNY';
  return 'USD';
};

export const enrichDefaultStock = (stock: Stock): Stock => ({
  ...stock,
  market: stock.market || 'US',
  exchange: stock.exchange || 'US',
  currency: stock.currency || marketCurrency(stock.market || 'US'),
  isSubscribed: stock.isSubscribed ?? true,
  subscriptionTopics: stock.subscriptionTopics || DEFAULT_STOCK_SUBSCRIPTION_TOPICS,
  addedAt: stock.addedAt || new Date().toISOString()
});

export const candidateToStock = (candidate: MarketSymbolCandidate): Stock => ({
  symbol: candidate.symbol,
  name: candidate.name,
  market: candidate.market,
  exchange: candidate.exchange || candidate.security_type,
  currency: marketCurrency(candidate.market),
  quoteId: candidate.quote_id || undefined,
  isSubscribed: true,
  subscriptionTopics: DEFAULT_STOCK_SUBSCRIPTION_TOPICS,
  addedAt: new Date().toISOString(),
  sector: candidate.security_type || (candidate.market === 'US' ? '美股' : candidate.market === 'HK' ? '港股' : 'A股'),
  marketCap: 0,
  currentPrice: 0,
  changePercent: 0,
  priceChange: 0,
  previousClose: 0,
  quoteVolume: 0,
  quoteProvider: candidate.provider,
  quoteProviderName: candidate.provider_name,
  quoteFetchedAt: new Date().toISOString(),
  quoteIsRealtime: false,
  quoteDelayNote: '已加入自选，等待行情刷新',
  description: `${candidate.name}（${candidate.symbol}）已加入观察池，可监控价格、新闻、财报和研究提醒。`,
  focusLevel: 'medium',
  totalPosts: 0,
  totalPaidPosts: 0,
  communityScore: 50
});

export const applyMarketQuotesToStocks = (stocks: Stock[], quotes: MarketQuote[]): Stock[] => {
  const quoteBySymbol = new Map(quotes.map(quote => [quote.symbol.toUpperCase(), quote]));

  return stocks.map(stock => {
    const quote = quoteBySymbol.get(stock.symbol.toUpperCase());
    if (!quote) {
      return stock;
    }

    return {
      ...stock,
      currentPrice: quote.price,
      changePercent: toFiniteNumber(quote.change_percent) ?? stock.changePercent,
      priceChange: toFiniteNumber(quote.change) ?? stock.priceChange,
      previousClose: toFiniteNumber(quote.previous_close) ?? stock.previousClose,
      quoteVolume: toFiniteNumber(quote.volume) ?? stock.quoteVolume,
      quoteProvider: quote.provider,
      quoteProviderName: quote.provider_name,
      quoteMarketTime: quote.market_time,
      quoteFetchedAt: quote.fetched_at,
      quoteIsRealtime: quote.is_realtime,
      quoteDelayNote: quote.delay_note
    };
  });
};
