import { apiDelete, apiGet, apiPatch, apiPost } from './apiClient';

// === systemHealthService types ===

export type ReadinessStatus = 'pass' | 'warn' | 'fail';
export type SystemReadinessState = 'ready' | 'degraded' | 'not_ready';

export interface SystemReadinessCheck {
  key: string;
  name: string;
  status: ReadinessStatus;
  detail: string;
  remediation?: string | null;
}

export interface SystemReadiness {
  status: SystemReadinessState;
  score: number;
  generated_at: string;
  checks: SystemReadinessCheck[];
  blockers: string[];
  warnings: string[];
}

// === dataSourceService types ===

export type DataSourceType = 'server_api' | 'market_api' | 'upload' | 'web_page' | 'agent_crawl' | 'manual';
export type DataSourceStatus = 'active' | 'paused' | 'error';
export type DataSourceCategory = 'market' | 'earnings' | 'filing' | 'research' | 'sentiment' | 'upload' | 'internal' | 'other';
export type DataSourceModule = 'home' | 'stock_detail' | 'earnings_calendar' | 'ai_research' | 'ai_supply_chain' | 'realtime_messages' | 'agent_center' | 'data_source_center' | 'custom';
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

// === mcpService types ===
// Note: TrustLevel is shared with dataSourceService (identical definition)

export type McpTransport = 'streamable_http' | 'stdio' | 'hosted';
export type McpServerStatus = 'unknown' | 'connected' | 'error' | 'disabled';
export type McpCapabilityType = 'tool' | 'resource' | 'prompt';
export type McpRiskLevel = 'low' | 'medium' | 'high';

export interface McpServerCreate {
  name: string;
  transport: McpTransport;
  description?: string;
  url?: string;
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  headers?: Record<string, string>;
  trust_level?: TrustLevel;
  risk_level?: McpRiskLevel;
  approval_required?: boolean;
  enabled?: boolean;
  allowed_tools?: string[];
  blocked_tools?: string[];
  notes?: string;
}

export interface McpServerRecord {
  id: string;
  name: string;
  transport: McpTransport;
  description: string;
  status: McpServerStatus;
  trust_level: TrustLevel;
  risk_level: McpRiskLevel;
  approval_required: boolean;
  enabled: boolean;
  config: Record<string, any>;
  tool_count: number;
  resource_count: number;
  prompt_count: number;
  last_connected_at?: string | null;
  last_error?: string | null;
  created_at: string;
  updated_at: string;
}

export interface McpCapabilityRecord {
  id: string;
  server_id: string;
  capability_type: McpCapabilityType;
  name: string;
  title?: string | null;
  description: string;
  schema: Record<string, any>;
  uri?: string | null;
  mime_type?: string | null;
  metadata: Record<string, any>;
  discovered_at: string;
}

export interface McpDiscoverResult {
  server: McpServerRecord;
  capabilities: McpCapabilityRecord[];
  warnings: string[];
}

export interface McpToolCallResult {
  server: McpServerRecord;
  tool_name: string;
  arguments: Record<string, any>;
  result: Record<string, any>;
  content_preview: string;
  called_at: string;
}

// === systemHealthService functions ===

export async function getSystemReadiness(): Promise<SystemReadiness> {
  return apiGet<SystemReadiness>('/api/system/readiness', { timeout: 8000 });
}

// === dataSourceService functions ===

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

export interface CorpusStats {
  total: number;
  avg_credibility: number;
  credibility_bands: { high: number; mid: number; low: number };
  symbol_coverage: Array<{ symbol: string; count: number; avg_credibility: number }>;
  source_quality: Array<{ source_name: string; count: number; avg_credibility: number }>;
  freshness: { last_24h: number; last_7d: number; last_30d: number; older: number };
}

/** 证据语料的质量与覆盖聚合（全量扫描）。 */
export async function getCorpusStats(): Promise<CorpusStats> {
  return apiGet<CorpusStats>('/api/data-sources/corpus-stats');
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

// === mcpService functions ===

export async function listMcpServers(): Promise<McpServerRecord[]> {
  const response = await apiGet<{ servers: McpServerRecord[] }>('/api/mcp/servers');
  return response.servers;
}

export async function createMcpServer(payload: McpServerCreate): Promise<McpServerRecord> {
  return apiPost<McpServerRecord>('/api/mcp/servers', payload);
}

export async function deleteMcpServer(serverId: string): Promise<void> {
  await apiDelete(`/api/mcp/servers/${serverId}`);
}

export async function discoverMcpServer(serverId: string): Promise<McpDiscoverResult> {
  return apiPost<McpDiscoverResult>(`/api/mcp/servers/${serverId}/discover`);
}

export async function listMcpCapabilities(filters: {
  server_id?: string;
  capability_type?: McpCapabilityType;
} = {}): Promise<McpCapabilityRecord[]> {
  const response = await apiGet<{ capabilities: McpCapabilityRecord[] }>('/api/mcp/capabilities', {
    params: filters
  });
  return response.capabilities;
}

export async function callMcpTool(
  serverId: string,
  payload: {
    tool_name: string;
    arguments?: Record<string, any>;
    approved?: boolean;
  }
): Promise<McpToolCallResult> {
  return apiPost<McpToolCallResult>(`/api/mcp/servers/${serverId}/tools/call`, payload);
}
