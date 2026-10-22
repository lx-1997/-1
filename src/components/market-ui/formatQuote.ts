export type QuoteDirection = 'up' | 'down' | 'flat' | 'missing';

const PRICE_FORMAT = new Intl.NumberFormat('zh-CN', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 4,
});
const PERCENT_FORMAT = new Intl.NumberFormat('zh-CN', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const QUOTE_TIME_FORMAT = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
});

/** Normalize non-finite input without turning a missing quote into a zero. */
export function finiteQuoteValue(value: number | null | undefined): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  return Object.is(value, -0) ? 0 : value;
}

export function quoteDirection(value: number | null | undefined): QuoteDirection {
  const number = finiteQuoteValue(value);
  return number === null ? 'missing' : number > 0 ? 'up' : number < 0 ? 'down' : 'flat';
}

/** Keep ISO currency codes visible, so CNY/HKD/USD cannot share an ambiguous $/¥. */
export function formatQuotePrice(value: number | null | undefined, currency?: string): string {
  const number = finiteQuoteValue(value);
  if (number === null) return '—';
  const amount = PRICE_FORMAT.format(number);
  const code = currency?.trim().toUpperCase();
  // A provider may supply a non-ISO label. Plain text safely tolerates it without Intl errors.
  return code ? `${code} ${amount}` : amount;
}

/** Input is percentage points: 1.25 means 1.25%, never 125%. */
export function formatQuotePercent(value: number | null | undefined): string {
  const number = finiteQuoteValue(value);
  if (number === null) return '—';
  const magnitude = PERCENT_FORMAT.format(Math.abs(number));
  return `${number > 0 ? '+' : number < 0 ? '−' : ''}${magnitude}%`;
}

export interface FormattedQuoteTime {
  /** Canonical machine-readable timestamp for <time dateTime>. */
  iso: string;
  /** Precise time in the product's timezone, independent of browser locale. */
  label: string;
}

/** Do not silently interpret timezone-less provider strings as the viewer's local time. */
export function formatQuoteTime(value?: string | null): FormattedQuoteTime | null {
  if (!value) return null;
  const input = value.trim();
  const parts = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(Z|[+-]\d{2}:\d{2})$/i.exec(input);
  if (!parts) return null;
  const [, year, month, day, hour, minute, second, offset] = parts;
  const daysInMonth = new Date(Date.UTC(Number(year), Number(month), 0)).getUTCDate();
  if (Number(month) < 1 || Number(month) > 12 || Number(day) < 1 || Number(day) > daysInMonth
      || Number(hour) > 23 || Number(minute) > 59 || Number(second) > 59) return null;
  if (offset !== 'Z' && offset !== 'z') {
    const offsetHours = Number(offset.slice(1, 3));
    const offsetMinutes = Number(offset.slice(4, 6));
    if (offsetHours > 23 || offsetMinutes > 59) return null;
  }
  const date = new Date(input);
  if (!Number.isFinite(date.getTime())) return null;
  return { iso: date.toISOString(), label: `${QUOTE_TIME_FORMAT.format(date)} 北京时间` };
}
