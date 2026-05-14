import { apiDelete, apiGet, apiPost } from './apiClient';

export type McpTransport = 'streamable_http' | 'stdio' | 'hosted';
export type McpServerStatus = 'unknown' | 'connected' | 'error' | 'disabled';
export type McpCapabilityType = 'tool' | 'resource' | 'prompt';
export type TrustLevel = 'internal' | 'official' | 'media' | 'community' | 'unknown';
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
