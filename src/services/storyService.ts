import { apiGet, apiPost, apiPut } from './apiClient';

// 事件簇（故事）API 客户端：今日看板 / 千人千面推荐 / 详情 / 标的下钻 / 埋点 / 显式画像。
// 类型与后端 story_engine._row_to_card、build_mass_brief、user_profile 输出逐字对齐。

export type StoryKind = 'company' | 'theme' | 'macro';

export interface StorySourceRef {
  name: string;
  url: string;
  published_at: string;
}

export interface StoryItem {
  content_id: string;
  role: string;
  content_type: string;
  content_type_label: string;
  title: string;
  source_name: string;
  url: string;
  access_level: string;
  published_at: string;
  item_score: number;
}

export interface StoryTheme {
  code: string;
  label: string;
}

export interface StoryCard {
  story_id: string;
  bucket: string;
  kind: StoryKind | string;
  title: string;
  summary: string;
  symbol: string;
  entity_name: string;
  market: string;
  market_label: string;
  event_code: string;
  event_label: string;
  theme_codes: string[];
  theme_labels: string[];
  signal_code: string;
  signal_label: string;
  horizon_code: string;
  horizon_label: string;
  impact: string;
  verify: string[];
  mass: number;
  top_tier: string;
  recency: number;
  item_count: number;
  source_count: number;
  by_type: Record<string, number>;
  by_role: Record<string, number>;
  by_signal: Record<string, number>;
  symbols: string[];
  themes: StoryTheme[];
  timeline: Array<Record<string, unknown>>;
  first_seen: string;
  last_seen: string;
  generated_at: string;
  actions: Array<{ label: string; kind: string; [key: string]: string }>;
  disclaimer: string;
  why: string[];
  items?: StoryItem[];
  // 个人化排序（仅 /api/stories/me 登录态返回）
  relevance?: number;
  personal_score?: number;
  why_personal?: string[];
  // 详情页分区
  facts?: StoryItem[];
  opinions?: StoryItem[];
  risks?: StoryItem[];
  context?: StoryItem[];
}

export interface BriefItem {
  story_id: string;
  subject: string;
  title: string;
  summary: string;
  kind: string;
  symbol: string;
  entity_name: string;
  event_label: string;
  signal_label: string;
  impact: string;
  verify: string[];
  item_count: number;
  source_count: number;
  mass: number;
  sources: StorySourceRef[];
}

export interface BriefSection {
  key: string;
  title: string;
  note: string;
  items: BriefItem[];
}

export interface MassBrief {
  generated_at: string;
  scope: string;
  window_hours: number;
  headline: string;
  sections: BriefSection[];
  stats: {
    stories: number;
    content_items: number;
    sources: number;
    company_stories: number;
    risk_stories: number;
    window_hours: number;
  };
  disclaimer: string;
}

export interface StoryHealth {
  stories: number;
  story_items: number;
  by_kind: Record<string, number>;
  last_build: string;
  top: Array<{ story_id: string; title: string; mass: number; item_count: number; source_count: number }>;
}

export interface TodayResponse {
  stories: StoryCard[];
  brief: MassBrief;
  health: StoryHealth;
}

export interface StoriesMeResponse {
  personalized: boolean;
  stories: StoryCard[];
}

export interface StoriesBySymbolResponse {
  symbol: string;
  stories: StoryCard[];
}

export type StoryEventAction =
  | 'impression' | 'click' | 'dwell' | 'bookmark' | 'react'
  | 'follow' | 'feedback_up' | 'feedback_down' | 'dismiss';

export interface StoryEventPayload {
  story_id: string;
  action: StoryEventAction;
  content_id?: string;
  surface?: string;
  position?: number;
  dwell_ms?: number;
  score?: number;
  symbol?: string;
  kind?: string;
  themes?: string[];
}

export const MARKETS = ['CN', 'HK', 'US'] as const;
export const HORIZONS = [
  { code: 'short', label: '短线（数日）' },
  { code: 'mid', label: '波段（数周）' },
  { code: 'long', label: '中长线（季度+）' },
] as const;
export const RISKS = [
  { code: 'conservative', label: '稳健' },
  { code: 'balanced', label: '平衡' },
  { code: 'aggressive', label: '进取' },
] as const;

export interface UserProfile {
  markets: string[];
  horizon: string;
  risk: string;
  theme_follows: string[];
}

export function getTodayStories(limit = 20, windowHours = 72): Promise<TodayResponse> {
  return apiGet<TodayResponse>('/api/stories/today', {
    params: { limit, window_hours: windowHours },
    timeout: 30000,
  });
}

export function getMyStories(limit = 20, windowHours = 72): Promise<StoriesMeResponse> {
  return apiGet<StoriesMeResponse>('/api/stories/me', {
    params: { limit, window_hours: windowHours },
    timeout: 30000,
  });
}

export function getMassBrief(windowHours = 72): Promise<MassBrief> {
  return apiGet<MassBrief>('/api/stories/brief', { params: { window_hours: windowHours }, timeout: 30000 });
}

export function getStoryDetail(storyId: string): Promise<StoryCard> {
  return apiGet<StoryCard>(`/api/stories/${encodeURIComponent(storyId)}`, { timeout: 30000 });
}

export function getStoriesBySymbol(symbol: string, days = 30, limit = 30): Promise<StoriesBySymbolResponse> {
  return apiGet<StoriesBySymbolResponse>(`/api/stories/symbol/${encodeURIComponent(symbol)}`, {
    params: { days, limit },
    timeout: 30000,
  });
}

// 埋点是「即发即忘」：失败不应打断用户浏览，调用方无需 await 结果。
export function logStoryEvent(payload: StoryEventPayload): Promise<{ ok: boolean }> {
  return apiPost<{ ok: boolean }>('/api/stories/event', payload, { timeout: 10000 });
}

export function getMyProfile(): Promise<UserProfile> {
  return apiGet<UserProfile>('/api/profile', { timeout: 15000 });
}

export function saveMyProfile(profile: Partial<UserProfile>): Promise<UserProfile> {
  return apiPut<UserProfile>('/api/profile', profile, { timeout: 15000 });
}
