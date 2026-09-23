import type { RealtimeMessageRecord } from '../services/eventService';

export type ForegroundPopupMode = 'important' | 'all' | 'off';

export const FOREGROUND_POPUP_TOPICS = ['快讯', '文章', '研报', '机构纪要'] as const;
export type ForegroundPopupTopic = typeof FOREGROUND_POPUP_TOPICS[number];

// 新版设置默认覆盖四类内容；旧版未保存类型偏好时仍按“仅快讯”解释，避免老用户突然被大量内容打扰。
export const DEFAULT_FOREGROUND_POPUP_TOPICS: ForegroundPopupTopic[] = [...FOREGROUND_POPUP_TOPICS];
const LEGACY_FOREGROUND_POPUP_TOPICS: ForegroundPopupTopic[] = ['快讯'];
const POPUP_MODES: ForegroundPopupMode[] = ['important', 'all', 'off'];

export const foregroundPopupModeLabel: Record<ForegroundPopupMode, string> = {
  important: '仅重要',
  all: '全部',
  off: '关闭',
};

export const foregroundPopupTopicLabel: Record<ForegroundPopupTopic, string> = {
  快讯: '快讯',
  文章: '文章',
  研报: '研报',
  机构纪要: '机构纪要',
};

export function normalizeForegroundPopupTopic(value: unknown): ForegroundPopupTopic | null {
  const raw = String(value || '').trim();
  if (raw === '快讯') return '快讯';
  if (raw === '文章' || raw === '深度文章') return '文章';
  if (raw === '研报' || raw === '投行研报') return '研报';
  if (raw === '机构纪要' || raw === '机构调研纪要' || raw === '纪要') return '机构纪要';
  return null;
}

export function normalizeForegroundPopupKeywords(values: readonly unknown[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  values.forEach(value => {
    const keyword = String(value || '').trim().replace(/\s+/g, ' ');
    const key = keyword.toLocaleLowerCase();
    if (!keyword || seen.has(key)) return;
    seen.add(key);
    out.push(keyword.slice(0, 40));
  });
  return out.slice(0, 20);
}

function messageSearchText(message: RealtimeMessageRecord): string {
  const metadata = message.metadata || {};
  const metadataText = Object.values(metadata)
    .flatMap(value => Array.isArray(value) ? value : [value])
    .filter(value => ['string', 'number', 'boolean'].includes(typeof value))
    .join(' ');
  return [
    message.title,
    message.content,
    message.topic,
    message.source_name,
    message.symbol,
    ...(message.tags || []),
    metadataText,
  ].filter(Boolean).join(' ').toLocaleLowerCase();
}

/** 关键词为 OR 关系：命中任意一个已选关键词即可弹窗；不选关键词代表不过滤。 */
export function matchesForegroundPopupKeywords(
  message: RealtimeMessageRecord,
  keywords: readonly string[] = [],
): boolean {
  const normalized = normalizeForegroundPopupKeywords(keywords).map(keyword => keyword.toLocaleLowerCase());
  if (!normalized.length) return true;
  const haystack = messageSearchText(message);
  return normalized.some(keyword => haystack.includes(keyword));
}

const normalizeSymbol = (value: unknown): string => String(value || '')
  .trim()
  .toUpperCase()
  .replace(/^(SH|SZ|HK)[.:_-]/, '')
  .replace(/[.:_-](SH|SZ|SS|HK|US)$/, '');

const messageSymbols = (message: RealtimeMessageRecord): string[] => {
  const metadataSymbols = message.metadata?.symbols;
  const raw = [
    message.symbol,
    ...(Array.isArray(metadataSymbols) ? metadataSymbols : []),
  ];
  return raw.map(normalizeSymbol).filter(Boolean);
};

export function shouldShowForegroundPopup(
  message: RealtimeMessageRecord,
  mode: ForegroundPopupMode,
  watchlist: string[],
  topics: readonly ForegroundPopupTopic[] = LEGACY_FOREGROUND_POPUP_TOPICS,
  keywords: readonly string[] = [],
): boolean {
  const topic = normalizeForegroundPopupTopic(message.topic);
  if (mode === 'off' || !topic || !topics.includes(topic)) return false;
  if (!matchesForegroundPopupKeywords(message, keywords)) return false;
  if (mode === 'all') return true;

  if (message.severity === 'critical' || message.severity === 'warning') return true;
  if (message.severity === 'info') return false;

  const watched = new Set(watchlist.map(normalizeSymbol).filter(Boolean));
  return messageSymbols(message).some(symbol => watched.has(symbol));
}

export function nextForegroundPopupMode(mode: ForegroundPopupMode): ForegroundPopupMode {
  const index = POPUP_MODES.indexOf(mode);
  return POPUP_MODES[(index + 1) % POPUP_MODES.length];
}
