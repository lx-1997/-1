import { apiGet } from './apiClient';
import type { DataQuality } from '../types';

export interface PersonVoiceItem {
  id: string;
  title: string;
  summary: string;
  url: string;
  source_name: string;
  published_at?: string | null;
  reported_date?: string | null;
  tags: string[];
  importance_score: number;
}

export interface PersonProfile {
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
  items: PersonVoiceItem[];
  item_count: number;
  latest_date?: string | null;
  digest: string;
  digest_provider: string;
  warnings: string[];
  data_quality: DataQuality;
}

export interface PeopleSpotlightResponse {
  provider: string;
  generated_at: string;
  figures: PersonProfile[];
  total_items: number;
  cache_age_seconds: number;
  warnings: string[];
  data_quality: DataQuality;
}

export interface PersonDigestResponse {
  id: string;
  name: string;
  digest: string;
  digest_provider: string;
  item_count: number;
  generated_at: string;
  data_quality: DataQuality;
}

export async function getPeopleSpotlight(options: { refresh?: boolean } = {}): Promise<PeopleSpotlightResponse> {
  return apiGet<PeopleSpotlightResponse>('/api/people/spotlight', {
    params: { refresh: Boolean(options.refresh) },
    timeout: 28000,
  });
}

export async function getPersonDigest(
  figureId: string,
  options: { refresh?: boolean } = {}
): Promise<PersonDigestResponse> {
  return apiGet<PersonDigestResponse>(`/api/people/${encodeURIComponent(figureId)}/digest`, {
    params: { refresh: Boolean(options.refresh) },
    timeout: 24000,
  });
}
