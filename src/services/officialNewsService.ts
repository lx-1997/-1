import { apiGet } from './apiClient';

export type OfficialNewsSource = 'xinwenlianbo' | 'cctv-news' | 'cctv-economy';

export interface OfficialNewsItem {
  id: string;
  title: string;
  summary: string;
  url: string;
  source: OfficialNewsSource;
  source_name: string;
  section: string;
  published_at?: string | null;
  reported_date?: string | null;
  tags: string[];
  importance_score: number;
  metadata: Record<string, any>;
}

export interface OfficialNewsResponse {
  provider: string;
  generated_at: string;
  source: OfficialNewsSource;
  source_name: string;
  source_url: string;
  total_found: number;
  returned_count: number;
  cache_age_seconds: number;
  warnings: string[];
  items: OfficialNewsItem[];
}

export async function getOfficialCctvNews(options: {
  source?: OfficialNewsSource;
  limit?: number;
  refresh?: boolean;
} = {}): Promise<OfficialNewsResponse> {
  const response = await apiGet<OfficialNewsResponse>('/api/official-news/cctv', {
    params: {
      source: options.source || 'xinwenlianbo',
      limit: options.limit || 30,
      refresh: Boolean(options.refresh)
    },
    timeout: 22000
  });
  return response;
}
