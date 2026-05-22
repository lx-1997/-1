import { apiGet, apiPost, getApiBaseUrls } from './apiClient';
import { Stock } from '../types';

export type AgentTaskStatus = 'pending' | 'running' | 'waiting_approval' | 'failed' | 'completed' | 'cancelled';
export type AgentEngine = 'deepfocus' | 'tradingagents' | 'financial_services';

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
  progress?: number | null;
}

export interface InvestmentTaskCreate {
  title: string;
  symbol?: string;
  asset_name?: string;
  task_type: 'investment_research' | 'portfolio_review' | 'risk_review' | 'watchlist_monitor' | 'customs_trade_analysis';
  engine: AgentEngine;
  horizon: string;
  investor_profile: '保守' | '稳健' | '进取' | '专业';
  objective: string;
  context: string;
  engine_config?: Record<string, any>;
  priority: number;
}

export interface InvestmentTaskRecord {
  id: string;
  title: string;
  symbol?: string | null;
  asset_name?: string | null;
  task_type: string;
  engine: AgentEngine;
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

export type AgentRunEventType =
  | 'run_state'
  | 'reasoning_delta'
  | 'tool_start'
  | 'tool_progress'
  | 'tool_result'
  | 'approval_required'
  | 'artifact_update'
  | 'run_complete'
  | 'error';

export type AgentRunEventSurface = 'text' | 'block' | 'timeline' | 'control';

export interface AgentRunEvent {
  id: string;
  task_id: string;
  type: AgentRunEventType;
  surface: AgentRunEventSurface;
  phase: string;
  agent: string;
  title: string;
  message: string;
  progress?: number | null;
  created_at: string;
  payload?: Record<string, any>;
}

export const AGENT_RUN_EVENT_CONTRACT: Record<AgentRunEventType, {
  surface: AgentRunEventSurface;
  uiProjection: 'run_state' | 'reasoning' | 'tool' | 'approval' | 'artifact' | 'control' | 'error';
}> = {
  run_state: { surface: 'timeline', uiProjection: 'run_state' },
  reasoning_delta: { surface: 'block', uiProjection: 'reasoning' },
  tool_start: { surface: 'timeline', uiProjection: 'tool' },
  tool_progress: { surface: 'timeline', uiProjection: 'tool' },
  tool_result: { surface: 'timeline', uiProjection: 'tool' },
  approval_required: { surface: 'block', uiProjection: 'approval' },
  artifact_update: { surface: 'block', uiProjection: 'artifact' },
  run_complete: { surface: 'control', uiProjection: 'control' },
  error: { surface: 'block', uiProjection: 'error' }
};

export interface InvestmentTaskResult {
  engine?: AgentEngine;
  engine_label?: string;
  engine_status?: 'completed' | 'setup_required' | 'installed' | string;
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
  artifacts?: Array<{
    type: string;
    title: string;
    content: string;
  }>;
}

export interface OrchestratorChatRequest {
  message: string;
  history?: Array<{
    role: 'user' | 'assistant';
    content: string;
  }>;
  engine: AgentEngine;
  mode: 'research' | 'risk' | 'portfolio' | 'monitor';
  stock?: Partial<Stock>;
  attached_files?: string[];
  data_source_count?: number;
  mcp_server_count?: number;
  reasoning_mode?: 'fast' | 'thinking';
  locale?: string;
}

export interface GeneralChatRequest {
  message: string;
  history?: Array<{
    role: 'user' | 'assistant';
    content: string;
  }>;
  context?: Record<string, unknown>;
  locale?: string;
}

export interface GeneralChatResponse {
  provider: string;
  model: string;
  generated_at: string;
  title: string;
  content: string;
}

export interface OrchestratorReasoningStep {
  phase: string;
  title: string;
  detail: string;
  status?: 'done' | 'working' | 'wait' | 'error';
}

export interface OrchestratorChatResponse {
  provider: string;
  model: string;
  generated_at: string;
  agent: string;
  engine: AgentEngine;
  title: string;
  content: string;
  chips: string[];
  suggested_actions: string[];
  reasoning_trace?: OrchestratorReasoningStep[];
  should_create_task: boolean;
  handled_inline?: boolean;
  confidence: number;
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

export async function runOrchestratorChat(payload: OrchestratorChatRequest): Promise<OrchestratorChatResponse> {
  return apiPost<OrchestratorChatResponse>('/api/agents/orchestrator-chat', payload);
}

export async function runGeneralChat(payload: GeneralChatRequest): Promise<GeneralChatResponse> {
  return apiPost<GeneralChatResponse>('/api/agents/chat', payload);
}

export async function getAgentTask(taskId: string): Promise<InvestmentTaskRecord> {
  return apiGet<InvestmentTaskRecord>(`/api/agents/tasks/${taskId}`);
}

export async function retryAgentTask(taskId: string): Promise<InvestmentTaskRecord> {
  return apiPost<InvestmentTaskRecord>(`/api/agents/tasks/${taskId}/retry`);
}

export async function cancelAgentTask(taskId: string): Promise<InvestmentTaskRecord> {
  return apiPost<InvestmentTaskRecord>(`/api/agents/tasks/${taskId}/cancel`);
}

const AGENT_RUN_EVENT_TYPES: AgentRunEventType[] = [
  'run_state',
  'reasoning_delta',
  'tool_start',
  'tool_progress',
  'tool_result',
  'approval_required',
  'artifact_update',
  'run_complete',
  'error'
];

export function subscribeAgentTaskEvents(
  taskId: string,
  handlers: {
    onEvent: (event: AgentRunEvent) => void;
    onDone?: () => void;
    onError?: (error: Event) => void;
  }
): () => void {
  const apiBaseUrl = getApiBaseUrls()[0];
  const source = new EventSource(`${apiBaseUrl}/api/agents/tasks/${taskId}/events`);
  let closed = false;
  let reconnectFallbackTimer: number | undefined;

  const clearReconnectFallback = () => {
    if (reconnectFallbackTimer) {
      window.clearTimeout(reconnectFallbackTimer);
      reconnectFallbackTimer = undefined;
    }
  };

  const close = () => {
    if (closed) {
      return;
    }
    closed = true;
    clearReconnectFallback();
    source.close();
  };

  source.addEventListener('connected', clearReconnectFallback);

  AGENT_RUN_EVENT_TYPES.forEach(eventType => {
    source.addEventListener(eventType, event => {
      clearReconnectFallback();
      try {
        handlers.onEvent(JSON.parse((event as MessageEvent).data) as AgentRunEvent);
      } catch {
        // Ignore malformed stream chunks; the polling fallback still protects the UI.
      }
    });
  });

  source.addEventListener('done', () => {
    close();
    handlers.onDone?.();
  });

  source.onerror = event => {
    if (!closed && !reconnectFallbackTimer) {
      reconnectFallbackTimer = window.setTimeout(() => {
        reconnectFallbackTimer = undefined;
        if (closed) {
          return;
        }
        handlers.onError?.(event);
      }, 8000);
    }
  };

  return close;
}
