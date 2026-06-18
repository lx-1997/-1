import {
  DEFAULT_STOCK_SUBSCRIPTION_TOPICS,
  applyMarketQuotesToStocks,
  candidateToStock,
  enrichDefaultStock,
  marketCurrency
} from '../../utils/stockPool';
import type { MarketQuote, MarketSymbolCandidate } from '../../services/marketService';
import type { Stock } from '../../types';

const makeStock = (overrides: Partial<Stock> = {}): Stock => ({
  symbol: 'NVDA',
  name: 'NVIDIA',
  market: 'US',
  exchange: 'NASDAQ',
  currency: 'USD',
  isSubscribed: true,
  subscriptionTopics: DEFAULT_STOCK_SUBSCRIPTION_TOPICS,
  addedAt: '2026-01-01T00:00:00.000Z',
  sector: 'Semiconductors',
  marketCap: 100,
  currentPrice: 900,
  changePercent: 1,
  priceChange: 9,
  previousClose: 891,
  quoteVolume: 1000,
  description: 'Demo stock',
  focusLevel: 'medium',
  totalPosts: 0,
  totalPaidPosts: 0,
  communityScore: 50,
  ...overrides
});

describe('stockPool utils', () => {
  it('maps market currency consistently', () => {
    expect(marketCurrency('HK')).toBe('HKD');
    expect(marketCurrency('CN')).toBe('CNY');
    expect(marketCurrency('US')).toBe('USD');
    expect(marketCurrency(undefined)).toBe('USD');
  });

  it('enriches saved stocks with defaults used by the app', () => {
    const stock = enrichDefaultStock(makeStock({
      market: undefined,
      exchange: undefined,
      currency: undefined,
      isSubscribed: undefined,
      subscriptionTopics: undefined,
      addedAt: undefined
    }));

    expect(stock.market).toBe('US');
    expect(stock.exchange).toBe('US');
    expect(stock.currency).toBe('USD');
    expect(stock.isSubscribed).toBe(true);
    expect(stock.subscriptionTopics).toEqual(DEFAULT_STOCK_SUBSCRIPTION_TOPICS);
    expect(stock.addedAt).toEqual(expect.any(String));
  });

  it('converts a symbol-search candidate into a watchlist stock', () => {
    const candidate: MarketSymbolCandidate = {
      symbol: '00700',
      code: '00700',
      name: 'Tencent',
      market: 'HK',
      exchange: '',
      security_type: '港股',
      quote_id: 'HK.00700',
      provider: 'eastmoney',
      provider_name: 'Eastmoney'
    };

    const stock = candidateToStock(candidate);

    expect(stock.symbol).toBe('00700');
    expect(stock.currency).toBe('HKD');
    expect(stock.exchange).toBe('港股');
    expect(stock.quoteId).toBe('HK.00700');
    expect(stock.subscriptionTopics).toEqual(DEFAULT_STOCK_SUBSCRIPTION_TOPICS);
  });

  it('applies fetched quotes without losing existing fallback fields', () => {
    const quote: MarketQuote = {
      symbol: 'NVDA',
      price: 950,
      change: 12,
      change_percent: null,
      previous_close: 938,
      volume: 2000,
      currency: 'USD',
      provider: 'finnhub',
      provider_name: 'Finnhub',
      market_time: '2026-06-02T10:00:00.000Z',
      fetched_at: '2026-06-02T10:01:00.000Z',
      is_realtime: true,
      delay_note: '实时'
    };

    const [updated, untouched] = applyMarketQuotesToStocks(
      [
        makeStock({ symbol: 'NVDA', changePercent: 1.5 }),
        makeStock({ symbol: 'TSLA', name: 'Tesla' })
      ],
      [quote]
    );

    expect(updated.currentPrice).toBe(950);
    expect(updated.priceChange).toBe(12);
    expect(updated.changePercent).toBe(1.5);
    expect(updated.previousClose).toBe(938);
    expect(updated.quoteVolume).toBe(2000);
    expect(updated.quoteProvider).toBe('finnhub');
    expect(updated.quoteIsRealtime).toBe(true);
    expect(untouched.symbol).toBe('TSLA');
    expect(untouched.currentPrice).toBe(900);
  });
});
