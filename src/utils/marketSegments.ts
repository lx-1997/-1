import type { Stock } from '../types';
import type { MarketRegion } from '../services/marketService';

export type MarketSegmentKey = 'all' | 'a-share' | 'global';

export interface MarketSegmentMeta {
  key: MarketSegmentKey;
  label: string;
  shortLabel: string;
  description: string;
  markets: MarketRegion[];
  searchPlaceholder: string;
  chips: string[];
}

export const marketSegments: Record<MarketSegmentKey, MarketSegmentMeta> = {
  all: {
    key: 'all',
    label: '全市场',
    shortLabel: '全市场',
    description: '统一管理 A股、港股、美股自选和研究上下文。',
    markets: ['CN', 'HK', 'US', 'OTHER'],
    searchPlaceholder: '添加自选：AAPL / 腾讯 / 00700 / 贵州茅台 / 600519',
    chips: ['统一搜索', '统一自选', '统一提醒']
  },
  'a-share': {
    key: 'a-share',
    label: 'A股',
    shortLabel: 'A股',
    description: '聚焦本土交易时段、涨跌停、公告、龙虎榜和资金面线索。',
    markets: ['CN'],
    searchPlaceholder: '添加 A股：贵州茅台 / 宁德时代 / 600519 / 300750',
    chips: ['涨跌停', '公告财报', '资金流']
  },
  global: {
    key: 'global',
    label: '港美股',
    shortLabel: '港美',
    description: '聚焦港股与美股的财报、盘前盘后、汇率和全球风险线索。',
    markets: ['HK', 'US'],
    searchPlaceholder: '添加港美股：AAPL / TSLA / 腾讯 / 00700',
    chips: ['盘前盘后', '财报日历', '汇率风险']
  }
};

export const marketOfStock = (stock: Pick<Stock, 'market'>): MarketRegion => stock.market || 'US';

export const stockBelongsToSegment = (
  stock: Pick<Stock, 'market'>,
  segment: MarketSegmentKey
): boolean => (
  segment === 'all' || marketSegments[segment].markets.includes(marketOfStock(stock))
);

export const getMarketSegmentForStock = (stock: Pick<Stock, 'market'>): MarketSegmentKey => (
  marketOfStock(stock) === 'CN' ? 'a-share' : 'global'
);

export const countStocksBySegment = (stocks: Array<Pick<Stock, 'market'>>) => ({
  all: stocks.length,
  aShare: stocks.filter(stock => stockBelongsToSegment(stock, 'a-share')).length,
  global: stocks.filter(stock => stockBelongsToSegment(stock, 'global')).length
});
