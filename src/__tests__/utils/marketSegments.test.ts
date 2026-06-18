import {
  getMarketSegmentForStock,
  countStocksBySegment,
  stockBelongsToSegment,
  marketOfStock
} from '../../utils/marketSegments';
import type { Stock } from '../../types';

function makeStock(market: Stock['market']): Pick<Stock, 'market'> {
  return { market };
}

describe('getMarketSegmentForStock', () => {
  it('should return "a-share" for CN stocks', () => {
    expect(getMarketSegmentForStock(makeStock('CN'))).toBe('a-share');
  });

  it('should return "global" for HK stocks', () => {
    expect(getMarketSegmentForStock(makeStock('HK'))).toBe('global');
  });

  it('should return "global" for US stocks', () => {
    expect(getMarketSegmentForStock(makeStock('US'))).toBe('global');
  });

  it('should return "global" for OTHER stocks', () => {
    expect(getMarketSegmentForStock(makeStock('OTHER'))).toBe('global');
  });

  it('should return "global" for undefined market', () => {
    expect(getMarketSegmentForStock({ market: undefined })).toBe('global');
  });
});

describe('marketOfStock', () => {
  it('should return the stock market', () => {
    expect(marketOfStock(makeStock('US'))).toBe('US');
    expect(marketOfStock(makeStock('HK'))).toBe('HK');
    expect(marketOfStock(makeStock('CN'))).toBe('CN');
  });

  it('should default to "US" when market is undefined', () => {
    expect(marketOfStock({ market: undefined })).toBe('US');
  });
});

describe('stockBelongsToSegment', () => {
  it('should return true for "all" segment regardless of market', () => {
    expect(stockBelongsToSegment(makeStock('CN'), 'all')).toBe(true);
    expect(stockBelongsToSegment(makeStock('US'), 'all')).toBe(true);
    expect(stockBelongsToSegment(makeStock('HK'), 'all')).toBe(true);
  });

  it('should return true for CN stock in "a-share" segment', () => {
    expect(stockBelongsToSegment(makeStock('CN'), 'a-share')).toBe(true);
  });

  it('should return false for US stock in "a-share" segment', () => {
    expect(stockBelongsToSegment(makeStock('US'), 'a-share')).toBe(false);
  });

  it('should return true for HK/US stock in "global" segment', () => {
    expect(stockBelongsToSegment(makeStock('HK'), 'global')).toBe(true);
    expect(stockBelongsToSegment(makeStock('US'), 'global')).toBe(true);
  });

  it('should return false for CN stock in "global" segment', () => {
    expect(stockBelongsToSegment(makeStock('CN'), 'global')).toBe(false);
  });
});

describe('countStocksBySegment', () => {
  it('should count an empty array correctly', () => {
    const result = countStocksBySegment([]);
    expect(result.all).toBe(0);
    expect(result.aShare).toBe(0);
    expect(result.global).toBe(0);
  });

  it('should count CN stocks as a-share', () => {
    const stocks = [makeStock('CN'), makeStock('CN'), makeStock('CN')];
    const result = countStocksBySegment(stocks);
    expect(result.all).toBe(3);
    expect(result.aShare).toBe(3);
    expect(result.global).toBe(0);
  });

  it('should count US and HK stocks as global', () => {
    const stocks = [makeStock('US'), makeStock('HK'), makeStock('US')];
    const result = countStocksBySegment(stocks);
    expect(result.all).toBe(3);
    expect(result.aShare).toBe(0);
    expect(result.global).toBe(3);
  });

  it('should count mixed markets correctly', () => {
    const stocks = [
      makeStock('CN'),
      makeStock('US'),
      makeStock('HK'),
      makeStock('CN'),
      makeStock('OTHER')
    ];
    const result = countStocksBySegment(stocks);
    expect(result.all).toBe(5);
    expect(result.aShare).toBe(2);
    expect(result.global).toBe(2);
  });

  it('should handle undefined market as US (global)', () => {
    const stocks = [makeStock(undefined), makeStock('CN')];
    const result = countStocksBySegment(stocks);
    expect(result.all).toBe(2);
    expect(result.aShare).toBe(1);
    expect(result.global).toBe(1);
  });
});