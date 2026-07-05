import { apiGet, getApiBaseUrls } from './apiClient';
import type { DataQuality } from '../types';

// 帖子下的一条评论（作者 + 文字 + 时间 + 点赞/置顶；reply_to=楼中楼被回复人）。
export interface CelebrityViewComment {
  author: string;
  text: string;
  create_time?: string;
  likes_count?: number;
  sticky?: boolean;
  reply_to?: string;
}

// 名人观点：富媒体观点条目（图片 / 文字 / 可播放语音 / 出处），数据源可插拔。
export interface CelebrityViewItem {
  id: string;
  title: string;
  body: string;
  image_urls: string[];
  image_fulls?: string[];
  audio_url: string;
  source_name: string;
  source_url: string;
  source_type: string;
  published_at?: string | null;
  reported_date?: string | null;
  tags: string[];
  importance_score: number;
  topic_id?: string;                     // 上游帖子 ID（拉全部评论用；非 zsxq 源为空）
  comments?: CelebrityViewComment[];     // 随帖预览评论(上游最多带前几条)
  comments_count?: number;               // 评论总数；> comments.length 时可「加载全部」
}

export interface CelebrityProfile {
  id: string;
  name: string;
  en_name: string;
  role: string;
  org: string;
  image: string;
  image_credit: string;
  avatar: string;
  monogram: string;
  accent: string;
  bio: string;
  topics: string[];
  why_it_matters: string;
  items: CelebrityViewItem[];
  item_count: number;
  latest_date?: string | null;
  digest: string;
  digest_provider: string;
  warnings: string[];
  data_quality: DataQuality;
}

export interface CelebrityViewsResponse {
  provider: string;
  generated_at: string;
  figures: CelebrityProfile[];
  total_items: number;
  sources: string[];
  cache_age_seconds: number;
  warnings: string[];
  data_quality: DataQuality;
}

export interface CelebrityDigestResponse {
  id: string;
  name: string;
  digest: string;
  digest_provider: string;
  item_count: number;
  generated_at: string;
  data_quality: DataQuality;
}

const PUBLIC_URL = process.env.PUBLIC_URL || '';

/**
 * 把条目里的相对媒体路径解析成可直接喂给 <img>/<audio> 的 URL：
 * - http(s) 绝对地址 → 原样
 * - /api/* (后端服务的图片/语音) → 用 apiClient 解析出的后端基址前缀
 *   （正式域名=同源经 nginx 反代；本地开发=127.0.0.1:8300 直连后端，避免打到前端 dev server）
 * - 其它(/people/* 等前端静态资源) → PUBLIC_URL 前缀
 */
export const resolveMediaUrl = (url: string): string => {
  const u = (url || '').trim();
  if (!u) return '';
  if (/^https?:\/\//i.test(u)) return u;
  if (u.startsWith('/api/')) {
    const base = getApiBaseUrls()[0] || (typeof window !== 'undefined' ? window.location.origin : '');
    return base + u;
  }
  return PUBLIC_URL + u;
};

export async function getCelebrityViews(
  options: { refresh?: boolean } = {}
): Promise<CelebrityViewsResponse> {
  return apiGet<CelebrityViewsResponse>('/api/celebrity/views', {
    params: { refresh: Boolean(options.refresh) },
    timeout: 28000,
  });
}

export async function getCelebrityDigest(
  celebId: string,
  options: { refresh?: boolean } = {}
): Promise<CelebrityDigestResponse> {
  return apiGet<CelebrityDigestResponse>(`/api/celebrity/${encodeURIComponent(celebId)}/digest`, {
    params: { refresh: Boolean(options.refresh) },
    timeout: 24000,
  });
}

export interface CelebrityMoreResponse {
  items: CelebrityViewItem[];
  next_before: string;
  has_more: boolean;
}

// 加载该名人 before 时间点之前的更早观点（知识星球分页）。
export async function getCelebrityMore(
  celebId: string,
  before: string,
  limit = 20
): Promise<CelebrityMoreResponse> {
  return apiGet<CelebrityMoreResponse>(`/api/celebrity/${encodeURIComponent(celebId)}/more`, {
    params: { before, limit },
    timeout: 30000,
  });
}

export interface CelebrityCommentsResponse {
  comments: CelebrityViewComment[];
  count: number;
  has_more: boolean;
  error?: string;
}

// 拉某条帖子的全部评论（条目随帖只带前几条预览）。
export async function getCelebrityComments(
  celebId: string,
  topicId: string,
  limit = 100
): Promise<CelebrityCommentsResponse> {
  return apiGet<CelebrityCommentsResponse>(`/api/celebrity/${encodeURIComponent(celebId)}/comments`, {
    params: { topic: topicId, limit },
    timeout: 45000,
  });
}
