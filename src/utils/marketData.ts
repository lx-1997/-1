import { Stock } from '../types';

export const getQuoteProviderLabel = (stock?: Stock | null): string => {
  if (!stock) {
    return '行情未连接';
  }

  if (stock.quoteProviderName) {
    return stock.quoteProviderName;
  }

  if (stock.quoteProvider === 'mock') {
    return '本地样例';
  }

  return '行情源未知';
};

export const getQuoteFreshnessLabel = (stock?: Stock | null): string => {
  if (!stock) {
    return '未连接';
  }

  if (stock.quoteIsRealtime) {
    return '实时';
  }

  if (stock.quoteProvider === 'mock') {
    return '样例';
  }

  return '延迟';
};

export const formatQuoteTimestamp = (stock?: Stock | null): string => {
  const rawValue = stock?.quoteMarketTime || stock?.quoteFetchedAt;
  if (!rawValue) {
    return '待刷新';
  }

  const parsed = new Date(rawValue);
  if (Number.isNaN(parsed.getTime())) {
    return rawValue;
  }

  return parsed.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  });
};

export const formatQuoteSourceLine = (stock?: Stock | null): string => {
  return `${getQuoteFreshnessLabel(stock)} · ${getQuoteProviderLabel(stock)}`;
};

export const getQuoteDelayNote = (stock?: Stock | null): string => {
  if (!stock) {
    return '';
  }

  if (stock.quoteProvider === 'stooq') {
    return '免费无 key 公共快照，通常为延迟或最新可用行情。';
  }

  if (stock.quoteProvider === 'eastmoney') {
    return '东方财富免费公共行情快照，覆盖 A 股、港股和部分美股；稳定性依赖公开接口可用性。';
  }

  if (stock.quoteProvider === 'sina') {
    return '新浪财经免费公共行情快照，覆盖 A 股、港股和部分美股；稳定性依赖公开接口可用性。';
  }

  if (stock.quoteProvider === 'alpha_vantage') {
    return 'Alpha Vantage 免费额度返回最新可用报价，不保证交易所实时。';
  }

  if (stock.quoteProvider === 'finnhub') {
    return 'Finnhub 报价接口，实时性仍取决于账号和交易所权限。';
  }

  return stock.quoteDelayNote || '';
};
