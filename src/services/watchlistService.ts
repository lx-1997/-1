import { apiGet, apiPost } from './apiClient';

/**
 * 账号自选股同步服务。
 * 仅在登录态调用（apiClient 自动注入 JWT）；未登录时前端走 localStorage，不触发这些请求。
 */

export interface WatchlistData {
  symbols: string[];
  names: Record<string, string>;
  empty?: boolean;
}

/** 读取当前账号的自选股；empty=true 表示该账号尚无记录（首次，前端用当前/默认列表做种子）。 */
export async function fetchWatchlist(): Promise<WatchlistData> {
  return apiGet<WatchlistData>('/api/me/watchlist');
}

/** 整表保存当前账号的自选股（覆盖式）。 */
export async function saveWatchlist(
  symbols: string[],
  names: Record<string, string>
): Promise<WatchlistData> {
  return apiPost<WatchlistData>('/api/me/watchlist', { symbols, names });
}
