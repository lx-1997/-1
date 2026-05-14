import { apiDelete, apiGet, apiPatch, apiPost } from './apiClient';

export type DataSourceType = 'server_api' | 'market_api' | 'upload' | 'web_page' | 'agent_crawl' | 'manual';
export type DataSourceStatus = 'active' | 'paused' | 'error';
export type DataSourceCategory = 'market' | 'earnings' | 'filing' | 'research' | 'sentiment' | 'upload' | 'internal' | 'other';
export type DataSourceModule = 'home' | 'stock_detail' | 'earnings_calendar' | 'ai_research' | 'realtime_messages' | 'agent_center' | 'data_source_center' | 'custom';
export type DataSourceRefRole = 'primary' | 'fallback' | 'evidence' | 'signal' | 'context';
export type TrustLevel = 'internal' | 'official' | 'media' | 'community' | 'unknown';
export type KeywordCrawlProvider = 'xueqiu' | 'wechat_public';
export type DataSourceSort = 'relevance' | 'time_desc';
export type KeywordCrawlFreshness = 'day' | 'week' | 'month' | 'year' | 'any';

export interface DataSourceCreate {
  name: string;
  category: DataSourceCategory;
  source_type: DataSourceType;
  description: string;
  url?: string;
  method: 'GET' | 'POST';
  headers: Record<string, string>;
  params: Record<string, string>;
  body?: Record<string, any> | null;
  symbol_param?: string;
  query_param?: string;
  trust_level: TrustLevel;
  enabled: boolean;
  notes?: string;
  output_schema?: Record<string, any>;
}

export interface DataSourceModuleRefCreate {
  source_id: string;
  module: DataSourceModule;
  role: DataSourceRefRole;
  filters?: Record<string, any>;
  notes?: string;
  enabled?: boolean;
}

export interface DataSourceModuleRefRecord {
  id: string;
  source_id: string;
  source_name: string;
  source_type: DataSourceType;
  source_category: DataSourceCategory;
  module: DataSourceModule;
  role: DataSourceRefRole;
  filters: Record<string, any>;
  notes: string;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface DataSourceRecord {
  id: string;
  name: string;
  category: DataSourceCategory;
  source_type: DataSourceType;
  description: string;
  status: DataSourceStatus;
  trust_level: TrustLevel;
  output_schema: Record<string, any>;
  config: Record<string, any>;
  module_refs: DataSourceModuleRefRecord[];
  items_count: number;
  last_sync_at?: string | null;
  last_error?: string | null;
  created_at: string;
  updated_at: string;
}

export interface DataSourceItemRecord {
  id: string;
  source_id: string;
  source_name: string;
  source_type: DataSourceType;
  source_category: DataSourceCategory;
  title: string;
  symbol?: string | null;
  url?: string | null;
  text: string;
  text_preview: string;
  tags: string[];
  metadata: Record<string, any>;
  credibility_score: number;
  collected_at: string;
  created_at: string;
}

export interface DataSourceItemUpdate {
  title?: string;
  symbol?: string | null;
  tags?: string[];
  credibility_score?: number;
  ai_interpretation?: string;
}

export interface DataSourceTagRecord {
  tag: string;
  count: number;
}

export interface DataSourceSyncPayload {
  symbol?: string;
  query?: string;
  url?: string;
  limit?: number;
}

export interface DataSourceSyncResult {
  source: DataSourceRecord;
  imported_count: number;
  items: DataSourceItemRecord[];
}

export interface DataSourceKeywordCrawlPayload {
  provider: KeywordCrawlProvider;
  keyword: string;
  symbol?: string;
  limit?: number;
  sort?: DataSourceSort;
  freshness?: KeywordCrawlFreshness;
}

export interface KeywordProviderPolicy {
  provider: KeywordCrawlProvider;
  name: string;
  description: string;
  auth_mode: string;
  risk_level: 'low' | 'medium' | 'high';
  fallback_provider?: KeywordCrawlProvider | null;
  rate_limit: string;
  health_score: number;
  notes: string;
  configured?: boolean;
}

export interface DataSourceKeywordCrawlResult {
  provider: KeywordCrawlProvider;
  effective_provider: KeywordCrawlProvider;
  attempted_providers: KeywordCrawlProvider[];
  fallback_used: boolean;
  provider_policy: KeywordProviderPolicy;
  keyword: string;
  sort: DataSourceSort;
  freshness: KeywordCrawlFreshness;
  source: DataSourceRecord;
  imported_count: number;
  items: DataSourceItemRecord[];
  warnings: string[];
}

export interface DataSourceInterpretResult {
  item: DataSourceItemRecord;
  interpretation: string;
  result: Record<string, any>;
}

export async function listDataSources(): Promise<DataSourceRecord[]> {
  const response = await apiGet<{ sources: DataSourceRecord[] }>('/api/data-sources');
  return response.sources;
}

export async function createDataSource(payload: DataSourceCreate): Promise<DataSourceRecord> {
  return apiPost<DataSourceRecord>('/api/data-sources', payload);
}

export async function listDataSourceModuleRefs(filters: {
  source_id?: string;
  module?: DataSourceModule;
} = {}): Promise<DataSourceModuleRefRecord[]> {
  const response = await apiGet<{ refs: DataSourceModuleRefRecord[] }>('/api/data-sources/module-refs', {
    params: filters
  });
  return response.refs;
}

export async function saveDataSourceModuleRef(payload: DataSourceModuleRefCreate): Promise<DataSourceModuleRefRecord> {
  return apiPost<DataSourceModuleRefRecord>('/api/data-sources/module-refs', payload);
}

export async function deleteDataSourceModuleRef(refId: string): Promise<void> {
  await apiDelete(`/api/data-sources/module-refs/${refId}`);
}

export async function deleteDataSource(sourceId: string): Promise<void> {
  await apiDelete(`/api/data-sources/${sourceId}`);
}

export async function syncDataSource(sourceId: string, payload: DataSourceSyncPayload): Promise<DataSourceSyncResult> {
  return apiPost<DataSourceSyncResult>(`/api/data-sources/${sourceId}/sync`, payload);
}

export async function listDataItems(filters: {
  symbol?: string;
  query?: string;
  source_type?: DataSourceType;
  source_id?: string;
  tag?: string;
  limit?: number;
  sort?: DataSourceSort;
} = {}): Promise<DataSourceItemRecord[]> {
  const response = await apiGet<{ items: DataSourceItemRecord[] }>('/api/data-sources/items', {
    params: filters
  });
  return response.items;
}

export async function listDataTags(): Promise<DataSourceTagRecord[]> {
  const response = await apiGet<{ tags: DataSourceTagRecord[] }>('/api/data-sources/items/tags');
  return response.tags;
}

export async function updateDataItem(itemId: string, payload: DataSourceItemUpdate): Promise<DataSourceItemRecord> {
  return apiPatch<DataSourceItemRecord>(`/api/data-sources/items/${itemId}`, payload);
}

export async function interpretDataItem(itemId: string, persist = true): Promise<DataSourceInterpretResult> {
  return apiPost<DataSourceInterpretResult>(
    `/api/data-sources/items/${itemId}/interpret`,
    { persist }
  );
}

export async function deleteDataItem(itemId: string): Promise<void> {
  await apiDelete(`/api/data-sources/items/${itemId}`);
}

export async function uploadDataFile(file: File, metadata: {
  symbol?: string;
  title?: string;
  tags?: string;
} = {}): Promise<DataSourceItemRecord> {
  const formData = new FormData();
  formData.append('file', file);
  if (metadata.symbol) formData.append('symbol', metadata.symbol);
  if (metadata.title) formData.append('title', metadata.title);
  if (metadata.tags) formData.append('tags', metadata.tags);
  return apiPost<DataSourceItemRecord>('/api/data-sources/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' }
  });
}

export async function agentCrawl(payload: DataSourceSyncPayload): Promise<DataSourceSyncResult> {
  return apiPost<DataSourceSyncResult>('/api/data-sources/agent-crawl', payload);
}

export async function keywordCrawl(payload: DataSourceKeywordCrawlPayload): Promise<DataSourceKeywordCrawlResult> {
  return apiPost<DataSourceKeywordCrawlResult>('/api/data-sources/keyword-crawl', payload);
}
