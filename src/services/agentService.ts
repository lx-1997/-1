import { Stock } from '../types';
import { apiGet, apiPost, DF_WEB_TOKEN, getApiBaseUrls } from './apiClient';

// === agentTaskService types ===

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
  /** Context sent separately so backend intent detectors only see the live question. */
  context_hint?: string;
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
  include_macro?: boolean;
  include_risk?: boolean;
  include_evidence?: boolean;
  include_metrics?: boolean;
  include_supply_chain?: boolean;
  include_trade?: boolean;
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

// === dulusAgentService types ===

export type DulusProviderMode = 'openai_compatible' | 'local_mock' | 'webbridge_disabled';
export type DulusProviderStatus = 'ready' | 'needs_config' | 'disabled';
export type DulusToolCategory = 'filesystem' | 'shell' | 'search' | 'browser' | 'memory' | 'mcp' | 'finance' | 'risk' | 'report';
export type DulusToolPermission = 'read_only' | 'approval_required' | 'disabled';
export type DulusRoundtableMode = 'fast' | 'debate' | 'deep_research';
export type DulusTurnStance = 'evidence' | 'research' | 'risk' | 'operator' | 'synthesis';
export type DulusTraceStatus = 'completed' | 'skipped' | 'blocked';
export type DulusDecision = 'candidate' | 'watch' | 'research_more' | 'blocked';

export interface DulusProviderRecord {
  id: string;
  name: string;
  mode: DulusProviderMode;
  model: string;
  status: DulusProviderStatus;
  latency_hint: string;
  risk_level: 'low' | 'medium' | 'high';
  notes: string;
}

export interface DulusToolRecord {
  id: string;
  name: string;
  category: DulusToolCategory;
  description: string;
  permission: DulusToolPermission;
  enabled: boolean;
  risk_level: 'low' | 'medium' | 'high';
  invocation: string;
}

export interface DulusRuntimeStatus {
  generated_at: string;
  compliant_mode: boolean;
  provider: string;
  model: string;
  providers: DulusProviderRecord[];
  tools: DulusToolRecord[];
  webbridge_policy: string;
  memory_scope: string;
  warnings: string[];
}

export interface DulusRoundtableRequest {
  objective: string;
  context?: string;
  stock?: Partial<Stock> | null;
  participants?: string[];
  enabled_tools?: string[];
  authorized_webbridge_url?: string | null;
  mode?: DulusRoundtableMode;
  locale?: string;
}

export interface DulusToolTrace {
  tool: string;
  title: string;
  input: string;
  output: string;
  status: DulusTraceStatus;
}

export interface DulusAgentTurn {
  participant_id: string;
  participant_name: string;
  provider: string;
  model: string;
  stance: DulusTurnStance;
  content: string;
  key_points: string[];
  risks: string[];
  actions: string[];
  confidence: number;
  tool_traces: DulusToolTrace[];
}

export interface DulusContentScopeItem {
  module: string;
  status: 'available' | 'no_data' | 'not_authorized' | string;
  raw_count?: number;
  selected_count?: number;
  top_tags?: string[];
  available?: string[];
  note?: string;
}

export interface DulusRoundtableResponse {
  provider: string;
  model: string;
  generated_at: string;
  mode: DulusRoundtableMode;
  objective: string;
  turns: DulusAgentTurn[];
  synthesis: string;
  decision: DulusDecision;
  memory_notes: string[];
  tool_traces: DulusToolTrace[];
  warnings: string[];
  sources: string[];
  /** 编号可引用证据（带 url+可信度），圆桌结论的可溯源来源列表。 */
  citable_sources?: ChatCitationSource[];
  confidence: number;
  disclaimer: string;
  /** 深度研判模式的非会员当日剩余次数；会员/管理员为 null。 */
  quota_left?: number | null;
  /** 本次研究实际扫描与选入的资料覆盖，按模块独立展示。 */
  content_scope?: Record<string, DulusContentScopeItem>;
  /** 可审计研究路径：只包含已执行的取数/筛选/复核步骤，不是隐藏思维链。 */
  research_steps?: Array<{ id: string; label: string; status: string; detail?: string; count?: number }>;
  /** 来自本次研究数据包的关键事实摘要。 */
  evidence_highlights?: string[];
  /** 面向用户的引用依据，按站内资料/外部公开数据分组。 */
  evidence_references?: Array<{
    id?: string;
    group?: string;
    group_label?: string;
    category?: string;
    title: string;
    detail?: string;
    source?: string;
    url?: string;
    published_at?: string;
    credibility?: number | null;
  }>;
  /** 前后端答案协议版本，便于识别线上是否已切到证据治理后的输出。 */
  answer_protocol_version?: string;
}

export interface DulusMemoryCreateRequest {
  scope: 'session' | 'project' | 'user';
  hall: string;
  title: string;
  content: string;
  tags?: string[];
  source?: string;
}

export interface DulusMemoryRecord {
  id: string;
  scope: string;
  hall: string;
  title: string;
  content: string;
  tags: string[];
  source: string;
  created_at: string;
}

export interface DulusMemoryListResponse {
  memories: DulusMemoryRecord[];
}

export interface DulusWebBridgeInspectRequest {
  url: string;
  mode?: 'text' | 'dom';
}

export interface DulusWebBridgeInspectResponse {
  url: string;
  allowed: boolean;
  policy: string;
  title: string;
  text_preview: string;
  links: string[];
  fetched_at: string;
}

// === agentTaskService functions ===

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

/**
 * Stream a general-chat reply token-by-token over SSE.
 * Returns a cancel function; call it to abort the stream.
 */
export interface ChatCitationSource {
  n: number;
  title: string;
  source: string;
  url?: string;
  /** 来源可信度 0–1（证据条目带，附件=1，无则不显示）。 */
  credibility?: number | null;
}

export function runGeneralChatStream(
  payload: GeneralChatRequest,
  handlers: {
    onDelta: (text: string) => void;
    onSources?: (sources: ChatCitationSource[]) => void;
    onStatus?: (status: string) => void;
    onDone?: () => void;
    onError?: (error: string) => void;
  }
): () => void {
  const apiBaseUrl = getApiBaseUrls()[0];
  let aborted = false;

  const run = async () => {
    try {
      const response = await fetch(`${apiBaseUrl}/api/agents/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (!response.ok || !response.body) {
        handlers.onError?.(`请求失败 (${response.status})`);
        return;
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (!aborted) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (!line.startsWith('data: ')) {
            continue;
          }
          try {
            const parsed = JSON.parse(line.slice(6)) as { delta?: string; done?: boolean; error?: string; status?: string; sources?: ChatCitationSource[] };
            if (parsed.error) {
              handlers.onError?.(parsed.error);
            } else if (parsed.done) {
              handlers.onDone?.();
            } else if (typeof parsed.status === 'string') {
              handlers.onStatus?.(parsed.status);
            } else if (Array.isArray(parsed.sources)) {
              handlers.onSources?.(parsed.sources);
            } else if (typeof parsed.delta === 'string') {
              handlers.onDelta(parsed.delta);
            }
          } catch {
            // skip malformed SSE chunk
          }
        }
      }

      reader.releaseLock();
    } catch (err) {
      if (!aborted) {
        handlers.onError?.(err instanceof Error ? err.message : 'AI 服务连接失败');
      }
    }
  };

  void run();

  return () => {
    aborted = true;
  };
}

// AI 原生 tool-use（非流式 JSON）：一次返回答案 + 用了哪些工具。走 apiClient(axios)——自动带 Authorization
// (iFinD 灰度靠它识别 lx199710)、有同源回退，比 SSE 流式经 nginx 稳。这是终端 AI 问答采用的可靠通道。
export interface ToolReference {
  id?: string;
  category?: string;
  title: string;
  detail?: string;
  source?: string;
  url?: string;
  published_at?: string;
}
export interface ToolTraceItem { tool: string; ok?: boolean; summary?: string; args?: any; references?: ToolReference[]; }
export interface ToolResearchResult {
  ok: boolean; answer: string; tool_trace: ToolTraceItem[]; rounds?: number; reason?: string; error?: string; status?: number;
  suggestions?: string[];       // 基于本次工具轨迹的确定性追问建议（零 token）
  quota_left?: number | null;   // 本次回答后剩余免费次数；null=会员不限
  route_title?: string;         // Orchestrator 实际选择的回答/技能路径
  route_chips?: string[];
  confidence?: number;
  needs_clarification?: boolean; // 只是在确认市场/周期/风险偏好，不消耗免费问答额度
  /** 研究类快速问答使用的真实圆桌元数据：专家观点、证据和来源，供前端做可复核展示。 */
  roundtable?: DulusRoundtableResponse | null;
  answer_protocol_version?: string;
  core_agent_run_id?: string;
  core_agent_protocol_version?: string;
  core_agent_route?: string;
}
export async function runToolResearch(
  message: string, symbol = '', name = '',
  history: Array<[string, string]> = [],   // 最近几轮 [问,答]——web 端多轮记忆（后端只喂 LLM，不进确定性路由）
  attachment?: { filename: string; text: string },
  options?: { roundtable?: boolean; context_hint?: string; timeoutMs?: number },
): Promise<ToolResearchResult> {
  const normalizedMessage = message.trim();
  if (!normalizedMessage) {
    return { ok: false, answer: '', tool_trace: [], error: '请输入问题后再发送', status: 400 };
  }
  try {
    // 正文/附件进 JSON body；message 同时放入短 query 参数，兼容尚未切换到 JSON 解析的旧线上后端。
    // 只带问题/标的元数据，不把 history/附件放进 URL，避免 CDN/nginx 长度限制。
    const legacyQuery = new URLSearchParams({ message: normalizedMessage, symbol, name }).toString();
    return await apiPost<ToolResearchResult>(`/api/agents/tool-research?${legacyQuery}`, {
      message: normalizedMessage,
      symbol,
      name,
      roundtable: Boolean(options?.roundtable),
      ...(options?.context_hint ? { context_hint: options.context_hint.slice(0, 16000) } : {}),
      history: history.slice(-3),
      ...(attachment?.text ? { attachment: { filename: attachment.filename, text: attachment.text } } : {}),
    }, { timeout: Math.max(5000, Math.min(90000, options?.timeoutMs ?? 90000)) });
  } catch (e: any) {
    // 带上 HTTP 状态码：402(非会员额度用完→升级)/403(匿名→登录)，前端据此分流
    return { ok: false, answer: '', tool_trace: [], error: e?.response?.data?.detail || e?.message || '请求失败', status: e?.response?.status };
  }
}

/** AI 答案 👍👎 反馈（fire-and-forget）：落后端 qa_feedback，踩会作废共享答案缓存。 */
export function sendAgentFeedback(question: string, answer: string, verdict: 'up' | 'down', toolTrace: ToolTraceItem[] = []): void {
  apiPost('/api/agents/feedback', {
    question: question.slice(0, 300),
    answer: answer.slice(0, 500),
    verdict,
    tool_trace: toolTrace.slice(0, 10),
  }).catch(() => { /* 反馈失败不打扰用户 */ });
}

// 深度研判（多智能体辩论，灰度 lx199710）：POST 起任务返回 task_id → 每 2s 轮询进度/结果。
// 纯轮询走 apiClient(axios，自动带 Authorization)，不用 SSE（生产 nginx HTTP/2 下流式 444）。
export interface DeepStage {
  key: string;
  label: string;
  status: 'wait' | 'working' | 'done' | 'error';
  detail?: string;
  output?: any;
}
export interface DeepVerdict {
  direction: string;
  confidence?: number | null;
  thesis?: string;
  core_evidence?: { point: string; evidence_ref: string }[];
  key_risks?: { risk: string; severity: string }[];
  watch_levels?: { support?: string; resistance?: string; note?: string };
  debate_synthesis?: string;
  disclaimer?: string;
  data_quality?: { ifind_used: boolean; degraded_sources?: string[]; gaps?: string[] };
}
export interface DeepTask {
  task_id: string;
  status: 'pending' | 'running' | 'done' | 'error';
  symbol: string;
  name?: string;
  market?: string;
  progress?: number;
  current_stage?: string;
  ifind_used?: boolean;
  stages: DeepStage[];
  result?: DeepVerdict | null;
  error?: string | null;
}
export async function startDeepResearch(symbol: string, name = '', market = 'CN', force = false): Promise<{ task_id: string; status: string; reused?: boolean }> {
  const qs = new URLSearchParams({ symbol, name, market, ...(force ? { force: '1' } : {}) }).toString();
  return apiPost(`/api/agents/deep-research?${qs}`, {});
}
export async function pollDeepResearch(taskId: string): Promise<DeepTask> {
  return apiGet(`/api/agents/deep-research/${taskId}`);
}

// AI 原生 tool-use 流式研究：边调工具(行情/估值/iFinD/搜我们的资讯/复盘)边推进度，最后给答案。
// 手动带 Authorization 头——iFinD 灰度按登录用户名识别(lx199710)。返回 cancel 函数。
export interface ToolEvent { tool: string; ok?: boolean; summary?: string; references?: ToolReference[]; }

// fetch 层的连接类异常（Safari 抛 "NetworkError"、Chrome 抛 "Failed to fetch"）
// 不应把浏览器原生文案直出给用户；后端 401 的 detail（重新登录引导）原样保留。
export function friendlyStreamError(message?: string): string {
  const msg = String(message || '');
  if (/networkerror/i.test(msg) || /failed to fetch/i.test(msg) || /load failed/i.test(msg)) {
    return '网络连接不稳定，请稍后重试；多次失败请重新登录后再试';
  }
  return msg || 'AI 服务连接失败';
}

export function runToolResearchStream(
  payload: {
    message: string;
    /** Optional context kept out of deterministic skill routing. */
    context_hint?: string;
    symbol?: string;
    name?: string;
    /** 研究类网页问答走真实的证据/研究/风险专家圆桌。 */
    roundtable?: boolean;
    history?: Array<[string, string]>;
    attachment?: { filename: string; text: string };
  },
  handlers: {
    onTool?: (e: ToolEvent, phase: 'start' | 'result') => void;
    onStatus?: (status: string) => void;
    onFinal?: (result: ToolResearchResult) => void;
    onFallback?: (reason: string) => void;
    onError?: (error: string, status?: number) => void;
    onDone?: () => void;
  }
): () => void {
  const token = (() => { try { return window.localStorage.getItem('auth_token') || ''; } catch { return ''; } })();
  const ctrl = new AbortController();
  let stopped = false;
  let terminal = false;
  // 只负责通知调用方切换通道，不在 service 内部偷偷再发一次请求。
  // 旧实现这里和 FinancialTerminal 的 onFallback 各发一遍兜底请求，竞态时会
  // 重复扣额度、互相覆盖答案，并把一次正常降级放大成 500/403。
  // This is an *idle* guard, not a total request deadline.  Harness can take
  // 15–45s on a cold start; the backend emits heartbeat status frames, so a
  // 30s quiet window catches a genuinely dead stream without racing a healthy
  // run and issuing a duplicate, quota-consuming fallback request.
  // Quick问答要尽快切换兜底；深度圆桌允许更长的安静窗口，后端会持续发送
  // 阶段状态，避免真实取数尚未结束时被前端误判为“卡死”并重复扣额度。
  const STREAM_IDLE_TIMEOUT_MS = payload.roundtable ? 90000 : 30000;
  let timeout: number | undefined;
  const clearTimeoutGuard = () => {
    if (timeout !== undefined) window.clearTimeout(timeout);
    timeout = undefined;
  };
  const armTimeoutGuard = () => {
    clearTimeoutGuard();
    timeout = window.setTimeout(() => {
      if (stopped || terminal) return;
      terminal = true;
      handlers.onFallback?.('实时研究响应超时，正在切换快速通道');
      // 取消当前流；兜底请求由调用方统一发起，保证最多只有一个。
      ctrl.abort();
    }, STREAM_IDLE_TIMEOUT_MS);
  };
  const normalizedMessage = payload.message.trim();
  if (!normalizedMessage) {
    clearTimeoutGuard();
    terminal = true;
    handlers.onError?.('请输入问题后再发送', 400);
    handlers.onDone?.();
    return () => { stopped = true; ctrl.abort(); };
  }
  armTimeoutGuard();
  (async () => {
    let lastError = 'AI 服务连接失败';
    try {
      for (const base of getApiBaseUrls()) {
        let receivedEvent = false;
        try {
          // 同时保留 message query，兼容尚未部署 JSON body 解析的旧线上后端。
          const legacyQuery = new URLSearchParams({
            message: normalizedMessage,
            symbol: payload.symbol || '',
            name: payload.name || '',
          }).toString();
          const resp = await fetch(`${base}/api/agents/tool-research/stream?${legacyQuery}`, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'X-DF-Web': DF_WEB_TOKEN,
              ...(token ? { Authorization: `Bearer ${token}` } : {}),
            },
            body: JSON.stringify({
              message: normalizedMessage,
              ...(payload.context_hint ? { context_hint: payload.context_hint.slice(0, 16000) } : {}),
              symbol: payload.symbol || '',
              name: payload.name || '',
              roundtable: Boolean(payload.roundtable),
              history: (payload.history || []).slice(-3),
              ...(payload.attachment?.text ? { attachment: payload.attachment } : {}),
            }),
            signal: ctrl.signal,
          });
          armTimeoutGuard();
          if (!resp.ok) {
            let detail = `请求失败 (${resp.status})`;
            try {
              const data = await resp.json();
              detail = typeof data?.detail === 'string' ? data.detail : detail;
            } catch { /* 保留 HTTP 状态提示 */ }
            clearTimeoutGuard();
            terminal = true;
            handlers.onError?.(detail, resp.status);
            return;
          }
          if (!resp.body) throw new Error('无法读取实时响应');
          const reader = resp.body.getReader();
          const dec = new TextDecoder();
          let buffer = '';
          while (!stopped) {
            const { done, value } = await reader.read();
            if (done) break;
            // 服务器会定期发 status/keep-alive；按“无活动”而非总耗时计时，
            // 真实圆桌持续有进度时不会被误切到快速通道。
            armTimeoutGuard();
            buffer += dec.decode(value, { stream: true }).replace(/\r\n/g, '\n');
            const frames = buffer.split('\n\n');
            buffer = frames.pop() || '';
            for (const frame of frames) {
              if (!frame.trim() || frame.trimStart().startsWith(':')) continue;
              const eventType = frame.split('\n').find(line => line.startsWith('event: '))?.slice(7).trim() || '';
              const dataText = frame.split('\n').filter(line => line.startsWith('data: ')).map(line => line.slice(6)).join('\n');
              if (!dataText) continue;
              let data: any; try { data = JSON.parse(dataText); } catch { continue; }
              armTimeoutGuard();
              receivedEvent = true;
              if (eventType === 'status') handlers.onStatus?.(String(data.message || ''));
              else if (eventType === 'tool_start') handlers.onTool?.({ tool: data.tool }, 'start');
              else if (eventType === 'tool_result') handlers.onTool?.({
                tool: data.tool,
                ok: data.ok,
                summary: data.summary,
                references: Array.isArray(data.references) ? data.references : [],
              }, 'result');
              else if (eventType === 'final') {
                terminal = true;
                clearTimeoutGuard();
                handlers.onFinal?.({
                ok: true,
                answer: data.answer || '',
                tool_trace: data.tool_trace || [],
                rounds: data.rounds || 0,
                suggestions: data.suggestions || [],
                quota_left: typeof data.quota_left === 'number' ? data.quota_left : null,
                route_title: data.route_title,
                route_chips: data.route_chips || [],
                confidence: data.confidence,
                needs_clarification: Boolean(data.needs_clarification),
                roundtable: data.roundtable || null,
                answer_protocol_version: data.answer_protocol_version,
                core_agent_run_id: data.core_agent_run_id,
                core_agent_protocol_version: data.core_agent_protocol_version,
                core_agent_route: data.core_agent_route,
                });
              }
              else if (eventType === 'fallback') {
                terminal = true;
                clearTimeoutGuard();
                handlers.onFallback?.(data.reason || '');
              }
              else if (eventType === 'error') {
                clearTimeoutGuard();
                terminal = true;
                handlers.onError?.(data.message || '出错了');
              }
            }
          }
          reader.releaseLock();
          clearTimeoutGuard();
          handlers.onDone?.();
          return;
        } catch (error: any) {
          if (error?.name === 'AbortError' || stopped) return;
          lastError = error?.message || lastError;
          // 一旦收到过实时事件，就不换源重放整次请求，避免重复扣额度。
          if (receivedEvent) break;
        }
      }
      clearTimeoutGuard();
      if (!stopped && !terminal) { terminal = true; handlers.onError?.(friendlyStreamError(lastError)); }
    } catch (error: any) {
      if (error?.name !== 'AbortError' && !stopped) handlers.onError?.(friendlyStreamError(error?.message || lastError));
    }
  })();
  return () => { stopped = true; clearTimeoutGuard(); ctrl.abort(); };
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

// === dulusAgentService functions ===

export async function getDulusStatus(): Promise<DulusRuntimeStatus> {
  return apiGet<DulusRuntimeStatus>('/api/dulus/status');
}

export async function getDulusTools(): Promise<DulusToolRecord[]> {
  return apiGet<DulusToolRecord[]>('/api/dulus/tools');
}

export async function runDulusRoundtable(payload: DulusRoundtableRequest): Promise<DulusRoundtableResponse> {
  // 深研是增强路径，不能把对话锁死到后端的 240 秒上限；调用方会在
  // 超时后切换到独立 quick 路由，确保用户始终能先拿到可用结论。
  // Harness 冷启动/工具锁竞争时可能超过 45s；后端会持续记录进度，90s
  // 给真实圆桌足够完成时间，同时仍保留 askDeepAnswer 的明确错误兜底路径。
  return apiPost<DulusRoundtableResponse>('/api/dulus/roundtable', payload, { timeout: 90000 });
}

export async function listDulusMemory(limit = 20, scope?: string): Promise<DulusMemoryRecord[]> {
  const query = new URLSearchParams({ limit: String(limit) });
  if (scope) query.set('scope', scope);
  const response = await apiGet<DulusMemoryListResponse>(`/api/dulus/memory?${query.toString()}`);
  return response.memories;
}

export async function createDulusMemory(payload: DulusMemoryCreateRequest): Promise<DulusMemoryRecord> {
  return apiPost<DulusMemoryRecord>('/api/dulus/memory', payload);
}

export async function inspectDulusWebBridge(payload: DulusWebBridgeInspectRequest): Promise<DulusWebBridgeInspectResponse> {
  return apiPost<DulusWebBridgeInspectResponse>('/api/dulus/webbridge/inspect', payload, { timeout: 20000 });
}

export interface CrossModuleResearchRequest {
  symbol: string;
  include_macro?: boolean;
  include_risk?: boolean;
  include_evidence?: boolean;
  include_metrics?: boolean;
  include_supply_chain?: boolean;
  include_trade?: boolean;
}

export interface CrossModuleResearchResponse {
  symbol: string;
  generated_at: string;
  modules_available: string[];
  quotes: Record<string, unknown>;
  macro: Record<string, unknown>;
  risk: Record<string, unknown>;
  evidence: Record<string, unknown>;
  metrics: Record<string, unknown>;
  supply_chain: Record<string, unknown>;
  trade: Record<string, unknown>;
}

export async function crossModuleResearch(payload: CrossModuleResearchRequest): Promise<CrossModuleResearchResponse> {
  return apiPost<CrossModuleResearchResponse>('/api/agents/cross-module-research', payload, { timeout: 20000 });
}

export interface ResearchThesis {
  bull_case: string;
  bull_probability: number;
  base_case: string;
  base_probability: number;
  bear_case: string;
  bear_probability: number;
}

export interface RiskDimension {
  score: number;
  label: string;
  detail: string;
}

export interface RiskMatrix {
  market_risk: RiskDimension;
  valuation_risk: RiskDimension;
  liquidity_risk: RiskDimension;
  macro_risk: RiskDimension;
  event_risk: RiskDimension;
}

export interface LoopResearchEvent {
  loop_id: string;
  phase?: string;
  phase_label?: string;
  round?: number;
  title?: string;
  detail?: string;
  status?: string;
  module?: string;
  module_name?: string;
  research_framework?: string;
  scoping_rationale?: string;
  key_questions?: string[];
  key_points?: string[];
  data_budget?: number;
  momentum_signal?: string;
  momentum_detail?: string;
  fundamental_quality?: number;
  fundamental_detail?: string;
  valuation_view?: string;
  valuation_detail?: string;
  macro_tailwind?: string;
  macro_detail?: string;
  confidence?: number;
  confidence_contribution?: number;
  cumulative_confidence?: number;
  need_another_round?: boolean;
  next_modules?: string[];
  executive_summary?: string;
  recommendation?: string;
  conviction?: string;
  target_rationale?: string;
  thesis?: ResearchThesis;
  risk_matrix?: RiskMatrix;
  key_catalysts?: string[];
  data_issues?: string[];
  risk_warnings?: string[];
  action_suggestions?: string[];
  next_steps?: string[];
  sources?: string[];
  sources_used?: string[];
  /** 编号可引用证据（带 url+可信度），用于深研结论的可溯源来源列表。 */
  citable_sources?: ChatCitationSource[];
  total_rounds?: number;
  modules_gathered?: string[];
  elapsed_seconds?: number;
  symbol?: string;
  question?: string;
  error?: string;
}

export interface ToolStep {
  tool: string;
  ok?: boolean;
  summary?: string;
  references?: ToolReference[];
}

/**
 * 流式 AI 原生 tool-use：边调工具边把进度推给前端。返回取消函数。
 * onFallback 表示 tool-agent 未返回结果（未启用/不支持），调用方应回退非流式 orchestrator-chat。
 */
export function streamToolResearch(
  params: { message: string; symbol?: string; name?: string; context_hint?: string },
  handlers: {
    onToolStart?: (tool: string) => void;
    onToolResult?: (tool: string, ok: boolean, summary: string, references: ToolReference[]) => void;
    onFinal?: (answer: string, toolTrace: ToolStep[]) => void;
    onFallback?: () => void;
    onError?: (msg: string) => void;
  }
): () => void {
  let terminalEvent = false;
  return runToolResearchStream(params, {
    onTool: (event, phase) => {
      if (phase === 'start') handlers.onToolStart?.(event.tool);
      else handlers.onToolResult?.(event.tool, !!event.ok, event.summary || '', event.references || []);
    },
    onFinal: result => { terminalEvent = true; handlers.onFinal?.(result.answer, result.tool_trace || []); },
    onFallback: () => { terminalEvent = true; handlers.onFallback?.(); },
    onError: message => { terminalEvent = true; handlers.onError?.(message); },
    onDone: () => { if (!terminalEvent) handlers.onFallback?.(); },
  });
}

export function subscribeResearchLoop(
  symbol: string,
  question: string,
  handlers: {
    onEvent: (event: LoopResearchEvent) => void;
    onDone?: (final: LoopResearchEvent) => void;
    onError?: (error: string) => void;
  }
): () => void {
  const apiBaseUrl = getApiBaseUrls()[0];
  let aborted = false;

  const run = async () => {
    try {
      const url = `${apiBaseUrl}/api/agents/research-loop/stream?symbol=${encodeURIComponent(symbol)}&question=${encodeURIComponent(question)}`;
      const response = await fetch(url, { method: 'POST' });

      if (!response.ok) {
        handlers.onError?.(`HTTP ${response.status}`);
        return;
      }

      const reader = response.body?.getReader();
      if (!reader) {
        handlers.onError?.('无法读取流');
        return;
      }

      const decoder = new TextDecoder();
      let buffer = '';

      while (!aborted) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        let currentEventType = '';
        let currentData = '';

        for (const line of lines) {
          if (line.startsWith('event: ')) {
            currentEventType = line.slice(7).trim();
          } else if (line.startsWith('data: ')) {
            currentData = line.slice(6).trim();
          } else if (line.trim() === '' && currentData) {
            try {
              const parsed = JSON.parse(currentData) as LoopResearchEvent;
              if (currentEventType === 'loop_done' || currentEventType === 'research_done') {
                handlers.onDone?.(parsed);
              } else {
                handlers.onEvent(parsed);
              }
            } catch {
              // skip malformed
            }
            currentEventType = '';
            currentData = '';
          }
        }
      }

      reader.releaseLock();
    } catch (err: any) {
      if (!aborted) {
        handlers.onError?.(err.message || 'SSE 连接失败');
      }
    }
  };

  run();

  return () => {
    aborted = true;
  };
}
