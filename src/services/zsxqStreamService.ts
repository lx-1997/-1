import { apiGet } from './apiClient';

// 星球纪要：知识星球普通帖子流（调研纪要 / 动态点评）。仅白名单账号（后端硬门控 401/403）。

export interface ZsxqComment {
  author: string;
  text: string;
  create_time?: string;
  likes_count?: number;
  sticky?: boolean;
  reply_to?: string;
}

// ⭐后端刻意不返回 author(星球号名)与 url(星球帖子链接):展示面不体现来源、无原文跳转。
export interface ZsxqTopic {
  id: string;
  title: string;
  text: string;
  links: Array<{ label: string; url: string }>;
  images: string[];
  image_fulls: string[];
  create_time: string;
  date: string;
  digested: boolean;
  comments_count: number;
  comments: ZsxqComment[];
  tags: string[];
  ontology?: Record<string, unknown>;
}

export interface ZsxqGroup { id: string; name: string }

export interface ZsxqStreamResponse {
  items: ZsxqTopic[];
  group: string;
  groups: ZsxqGroup[];
  keyword: string;
  next_before: string;
  has_more: boolean;
  total: number;
}

export interface ZsxqCommentsResponse {
  topic_id: string;
  comments: ZsxqComment[];
  count: number;
  has_more: boolean;
  error?: string;
}

export async function getZsxqStream(
  options: { group?: string; q?: string; before?: string; limit?: number; refresh?: boolean } = {}
): Promise<ZsxqStreamResponse> {
  return apiGet<ZsxqStreamResponse>('/api/zsxq/stream', {
    params: {
      ...(options.group ? { group: options.group } : {}),
      ...(options.q ? { q: options.q } : {}),
      ...(options.before ? { before: options.before } : {}),
      ...(options.limit ? { limit: options.limit } : {}),
      ...(options.refresh ? { refresh: true } : {}),
    },
  });
}

export async function getZsxqTopicComments(topicId: string, limit = 150): Promise<ZsxqCommentsResponse> {
  return apiGet<ZsxqCommentsResponse>('/api/zsxq/topic-comments', {
    params: { topic_id: topicId, limit },
  });
}
