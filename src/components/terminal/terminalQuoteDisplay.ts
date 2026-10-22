import { finiteQuoteValue, formatQuoteTime } from '../market-ui/formatQuote';
import type { QuoteStatusProps } from '../market-ui/QuoteStatus';

/** Optional metadata also tolerates quotes returned by older API deployments. */
export interface TerminalQuoteSnapshot {
  price?: number | null;
  change_percent?: number | null;
  provider?: string | null;
  provider_name?: string | null;
  market_time?: string | null;
  fetched_at?: string | null;
  is_realtime?: boolean;
  delay_note?: string | null;
}

export interface TerminalQuoteDisplayContext {
  lastSuccessAt?: number;
  /** This symbol was omitted by the latest response and uses its previous quote. */
  retained?: boolean;
  failed?: boolean;
  now?: number;
}

export interface TerminalQuoteDisplay extends QuoteStatusProps {
  price: number | null;
  changePercent: number | null;
}

const MAX_FETCH_AGE_MS = 90_000;
const MOCK_SOURCE = /(?:^|[\s_.-])(?:mock|demo|sample)(?=$|[\s_.-])|演示|模拟|示例/i;

/** Fetch freshness and explicit provider entitlement are separate evidence. */
export function toTerminalQuoteDisplay(quote: TerminalQuoteSnapshot | undefined,
  { lastSuccessAt, retained = false, failed = false, now = Date.now() }: TerminalQuoteDisplayContext = {},
): TerminalQuoteDisplay {
  const price = finiteQuoteValue(quote?.price);
  const changePercent = finiteQuoteValue(quote?.change_percent);
  const source = quote?.provider_name?.trim() || quote?.provider?.trim() || undefined;
  const marketTime = formatQuoteTime(quote?.market_time);
  const fetchTime = formatQuoteTime(quote?.fetched_at);
  const updatedAt = marketTime?.iso || quote?.fetched_at?.trim() || quote?.market_time?.trim() || null;
  const fetchedTooLongAgo = Boolean(fetchTime && now - Date.parse(fetchTime.iso) > MAX_FETCH_AGE_MS);
  const lastSuccessTooLongAgo = typeof lastSuccessAt === 'number'
    && Number.isFinite(lastSuccessAt) && now - lastSuccessAt > MAX_FETCH_AGE_MS;
  const mock = MOCK_SOURCE.test(`${quote?.provider || ''} ${quote?.provider_name || ''}`);
  const quality = price === null ? 'unavailable' : mock ? 'mock'
    : failed || retained || fetchedTooLongAgo || lastSuccessTooLongAgo ? 'stale'
    : quote?.is_realtime === true ? 'live' : 'delayed';
  const notes: string[] = [];
  if (quote?.delay_note?.trim()) notes.push(quote.delay_note.trim());
  else if (quality === 'delayed') notes.push('行情源未提供实时性保证');
  if (!marketTime && quote?.fetched_at?.trim()) notes.push('时间为接口获取时间，非交易所报价时间');
  else if (quote?.market_time && !marketTime) notes.push('行情时间格式待核验');
  if (quality === 'stale') notes.push('当前保留上次报价，等待行情恢复');
  return { price, changePercent, quality, source, updatedAt, delayNote: notes.join(' · ') || undefined };
}
