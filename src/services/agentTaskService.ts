import { apiGet, apiPost } from './apiClient';

export type AgentTaskStatus = 'pending' | 'running' | 'waiting_approval' | 'failed' | 'completed' | 'cancelled';

export interface AgentRuntimeHealth {
  status: string;
  worker_running: boolean;
  pending: number;
  running: number;
  completed: number;
  failed: number;
}

export interface AgentLogEntry {
  timestamp: string;
  agent: string;
  message: string;
}

export interface InvestmentTaskCreate {
  title: string;
  symbol?: string;
  asset_name?: string;
  task_type: 'investment_research' | 'portfolio_review' | 'risk_review' | 'watchlist_monitor';
  horizon: string;
  investor_profile: '保守' | '稳健' | '进取' | '专业';
  objective: string;
  context: string;
  priority: number;
}

export interface InvestmentTaskRecord {
  id: string;
  title: string;
  symbol?: string | null;
  asset_name?: string | null;
  task_type: string;
  status: AgentTaskStatus;
  priority: number;
  assigned_agent?: string | null;
  progress: number;
  created_at: string;
  updated_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  error?: string | null;
  input: Record<string, any>;
  logs: AgentLogEntry[];
  result?: InvestmentTaskResult | null;
}

export interface InvestmentTaskResult {
  investor_summary: string;
  decision: 'avoid' | 'watch' | 'research_more' | 'candidate';
  confidence: number;
  agent_findings: Record<string, string[]>;
  scenarios: Array<{
    case: string;
    probability: number;
    thesis: string;
    triggers: string[];
  }>;
  risk_controls: string[];
  action_plan: string[];
  watchlist: string[];
  disconfirming_evidence: string[];
  evidence?: Array<{
    title: string;
    source: string;
    source_type: string;
    tags?: string[];
    credibility_score: number;
    url?: string | null;
    takeaway: string;
  }>;
  plain_language_takeaway: string;
  disclaimer: string;
}

export async function getAgentHealth(): Promise<AgentRuntimeHealth> {
  return apiGet<AgentRuntimeHealth>('/api/agents/health');
}

export async function listAgentTasks(): Promise<InvestmentTaskRecord[]> {
  const response = await apiGet<{ tasks: InvestmentTaskRecord[] }>('/api/agents/tasks');
  return response.tasks;
}

export async function createAgentTask(payload: InvestmentTaskCreate): Promise<InvestmentTaskRecord> {
  return apiPost<InvestmentTaskRecord>('/api/agents/tasks', payload);
}

export async function retryAgentTask(taskId: string): Promise<InvestmentTaskRecord> {
  return apiPost<InvestmentTaskRecord>(`/api/agents/tasks/${taskId}/retry`);
}

export async function cancelAgentTask(taskId: string): Promise<InvestmentTaskRecord> {
  return apiPost<InvestmentTaskRecord>(`/api/agents/tasks/${taskId}/cancel`);
}
