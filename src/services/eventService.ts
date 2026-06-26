import { apiGet, apiPost, apiDelete, getApiBaseUrls, DF_WEB_TOKEN } from './apiClient';

// === majorEventService types ===

export type MajorEventType =
  | 'control_change'
  | 'restructuring'
  | 'buyback'
  | 'equity_incentive'
  | 'pledge_freeze'
  | 'litigation_arbitration'
  | 'regulatory_penalty'
  | 'st_delisting'
  | 'abnormal_trading'
  | 'dividend'
  | 'financing'
  | 'related_transaction'
  | 'other';
export type MajorEventStatus = 'new' | 'progress' | 'completed' | 'risk' | 'other';
export type MajorEventImpact = 'positive' | 'neutral' | 'negative' | 'mixed' | 'unknown';
export type MajorEventRiskLevel = 'low' | 'medium' | 'high';
export type MajorEventDetailSource = 'title' | 'pdf' | 'unavailable';
export type MajorEventDetailQuality = 'full' | 'partial' | 'title_only';

export interface MajorEventScanRequest {
  market?: 'A';
  days?: number;
  start_date?: string | null;
  end_date?: string | null;
  event_types?: MajorEventType[];
  limit?: number;
  detail_limit?: number;
}

export interface MajorEventRecord {
  symbol: string;
  name: string;
  announcement_date: string;
  event_type: MajorEventType;
  status: MajorEventStatus;
  impact: MajorEventImpact;
  risk_level: MajorEventRiskLevel;
  title: string;
  url: string;
  source: string;
  source_name: string;
  announcement_id: string;
  subject: string;
  amount: string;
  share_ratio: string;
  progress: string;
  deadline: string;
  detail_summary: string;
  key_points: string[];
  risk_flags: string[];
  action_items: string[];
  evidence_excerpt: string;
  tags: string[];
  detail_source: MajorEventDetailSource;
  detail_quality: MajorEventDetailQuality;
  metadata: Record<string, unknown>;
}

export interface MajorEventScanResponse {
  provider: string;
  model: string;
  generated_at: string;
  skill: string;
  market: 'A';
  start_date: string;
  end_date: string;
  total_found: number;
  returned_count: number;
  detail_attempted_count: number;
  detail_success_count: number;
  summary: string;
  records: MajorEventRecord[];
  warnings: string[];
  coverage_note: string;
  skill_invocation: string;
}

// === shareholderChangeService types ===

export type ShareholderChangeDirection = 'all' | 'increase' | 'decrease';
export type ShareholderChangeMarket = 'A' | 'HK' | 'US';
export type ShareholderChangeRecordDirection = 'increase' | 'decrease' | 'mixed' | 'unknown';
export type ShareholderChangeStatus = 'plan' | 'progress' | 'completed' | 'other';
export type ShareholderRiskLevel = 'low' | 'medium' | 'high';
export type ShareholderDetailSource = 'title' | 'pdf' | 'html' | 'xml' | 'unavailable';
export type ShareholderDetailQuality = 'full' | 'partial' | 'title_only';

export interface ShareholderChangeScanRequest {
  market?: ShareholderChangeMarket;
  days?: number;
  start_date?: string | null;
  end_date?: string | null;
  direction?: ShareholderChangeDirection;
  limit?: number;
  detail_limit?: number;
}

export interface ShareholderChangeRecord {
  symbol: string;
  name: string;
  announcement_date: string;
  direction: ShareholderChangeRecordDirection;
  status: ShareholderChangeStatus;
  shareholder_type: string;
  shareholder_hint: string;
  title: string;
  url: string;
  source: string;
  source_name: string;
  announcement_id: string;
  risk_level: ShareholderRiskLevel;
  tags: string[];
  shareholder_names: string[];
  change_shares: string;
  change_ratio: string;
  change_amount: string;
  price_range: string;
  change_period: string;
  change_method: string;
  holding_before: string;
  holding_after: string;
  change_reason: string;
  detail_summary: string;
  evidence_excerpt: string;
  detail_source: ShareholderDetailSource;
  detail_quality: ShareholderDetailQuality;
  metadata: Record<string, unknown>;
}

export interface ShareholderChangeScanResponse {
  provider: string;
  model: string;
  generated_at: string;
  skill: string;
  market: ShareholderChangeMarket;
  direction: ShareholderChangeDirection;
  start_date: string;
  end_date: string;
  total_found: number;
  returned_count: number;
  detail_attempted_count: number;
  detail_success_count: number;
  summary: string;
  records: ShareholderChangeRecord[];
  warnings: string[];
  coverage_note: string;
  skill_invocation: string;
}

export type ShareholderInterpretTone = 'positive' | 'watch' | 'risk' | 'neutral';

export interface ShareholderChangeInterpretRequest {
  record: ShareholderChangeRecord;
  question?: string | null;
  style?: 'brief' | 'full';
  locale?: string;
}

export interface ShareholderChangeInterpretResponse {
  provider: string;
  model: string;
  generated_at: string;
  skill: string;
  symbol: string;
  name: string;
  title: string;
  tone: ShareholderInterpretTone;
  verdict: string;
  summary: string;
  points: string[];
  risks: string[];
  questions: string[];
  actions: string[];
  evidence: string[];
  prompt_version: string;
  confidence: number;
  disclaimer: string;
}

// === realtimeMessageService types ===

export type RealtimeMessageSeverity = 'info' | 'success' | 'warning' | 'critical';
export type StreamConnectionStatus = 'connecting' | 'live' | 'reconnecting' | 'closed' | 'error';

export interface RealtimeMessageRecord {
  id: string;
  title: string;
  content: string;
  source_id?: string | null;
  source_name?: string | null;
  source_type?: string | null;
  symbol?: string | null;
  topic: string;
  severity: RealtimeMessageSeverity;
  url?: string | null;
  tags: string[];
  metadata: Record<string, any>;
  created_at: string;
}

export interface RealtimeMessageCreate {
  title: string;
  content?: string;
  source_id?: string;
  source_name?: string;
  source_type?: string;
  symbol?: string;
  topic?: string;
  severity?: RealtimeMessageSeverity;
  url?: string;
  tags?: string[];
  metadata?: Record<string, any>;
}

export interface RealtimeMessageFilters {
  symbol?: string;
  topic?: string;
  severity?: RealtimeMessageSeverity;
  since?: string;
  before?: string;   // 翻页游标：取比该时间更旧的历史
  q?: string;        // 关键词检索全量历史（标题/正文，空格分词 AND）
  anyq?: string;     // 多别名检索（逗号分隔，任一命中 OR）——选股用简称/全名/代码一起搜
  limit?: number;
}

// === majorEventService functions ===

export async function scanMajorEvents(
  request: MajorEventScanRequest
): Promise<MajorEventScanResponse> {
  return apiPost<MajorEventScanResponse>(
    '/api/skills/major-events/scan',
    {
      market: 'A',
      ...request
    },
    { timeout: 60000 }
  );
}

// === shareholderChangeService functions ===

export async function scanShareholderChanges(
  request: ShareholderChangeScanRequest
): Promise<ShareholderChangeScanResponse> {
  return apiPost<ShareholderChangeScanResponse>(
    '/api/skills/shareholder-changes/scan',
    {
      market: 'A',
      ...request
    },
    { timeout: 60000 }
  );
}

export async function interpretShareholderChange(
  request: ShareholderChangeInterpretRequest
): Promise<ShareholderChangeInterpretResponse> {
  return apiPost<ShareholderChangeInterpretResponse>(
    '/api/skills/shareholder-changes/interpret',
    {
      style: 'brief',
      locale: 'zh-CN',
      ...request
    },
    { timeout: 45000 }
  );
}

// === realtimeMessageService functions ===

export async function listRealtimeMessages(
  filters: RealtimeMessageFilters = {}
): Promise<RealtimeMessageRecord[]> {
  const response = await apiGet<{ messages: RealtimeMessageRecord[] }>('/api/realtime/messages', {
    params: filters
  });
  return response.messages;
}

export async function pushRealtimeMessage(payload: RealtimeMessageCreate): Promise<RealtimeMessageRecord> {
  return apiPost<RealtimeMessageRecord>('/api/realtime/messages', payload);
}

/** 按 id 取单条消息（文章分享深链 ?article={id} 用）；不存在返回 null。 */
export async function getRealtimeMessageById(id: string): Promise<RealtimeMessageRecord | null> {
  try {
    return await apiGet<RealtimeMessageRecord>(`/api/realtime/messages/${encodeURIComponent(id)}`);
  } catch {
    return null;
  }
}

export function getRealtimeStreamUrl(): string {
  return `${getApiBaseUrls()[0]}/api/realtime/messages/stream`;
}

export function getRealtimePushUrl(): string {
  return `${getApiBaseUrls()[0]}/api/realtime/messages`;
}

export function createRealtimeMessageStream(options: {
  url?: string;
  onMessage: (message: RealtimeMessageRecord) => void;
  onStatus: (status: StreamConnectionStatus) => void;
  onError?: (error: unknown) => void;
}): { close: () => void } {
  // EventSource 不能带自定义头 → 用 ?w= 查询参数携带前端标识（nginx 校验）
  const urls = options.url?.trim() ? [options.url.trim()] : getApiBaseUrls().map(baseUrl => `${baseUrl}/api/realtime/messages/stream?w=${DF_WEB_TOKEN}`);
  let closed = false;
  let source: EventSource | null = null;
  let retryTimer: number | undefined;
  let urlIndex = 0;

  const open = () => {
    if (closed) {
      return;
    }

    source?.close();
    options.onStatus(urlIndex === 0 ? 'connecting' : 'reconnecting');
    source = new EventSource(urls[urlIndex]);

    source.addEventListener('open', () => {
      options.onStatus('live');
    });

    source.addEventListener('connected', () => {
      options.onStatus('live');
    });

    source.addEventListener('realtime-message', event => {
      try {
        const message = JSON.parse((event as MessageEvent).data) as RealtimeMessageRecord;
        options.onMessage(message);
      } catch (error) {
        options.onError?.(error);
      }
    });

    source.onerror = error => {
      source?.close();
      if (closed) {
        return;
      }
      options.onError?.(error);
      options.onStatus('reconnecting');
      urlIndex = (urlIndex + 1) % urls.length;
      retryTimer = window.setTimeout(open, urlIndex === 0 ? 3000 : 800);
    };
  };

  open();

  return {
    close: () => {
      closed = true;
      if (retryTimer) {
        window.clearTimeout(retryTimer);
      }
      source?.close();
      options.onStatus('closed');
    }
  };
}

// === 信号召回 · 离线通道订阅（邮件 / Web Push）===

export type RecallChannel = 'email' | 'webpush';
export type RecallSubscriptionScope = 'watchlist' | 'all';

export interface RecallSubscriptionCreate {
  channel: RecallChannel;
  address: string;
  symbols?: string[];
  severities?: RealtimeMessageSeverity[];
  scope?: RecallSubscriptionScope;
  label?: string;
}

export interface RecallSubscriptionRecord {
  id: string;
  channel: RecallChannel;
  address: string;
  symbols: string[];
  severities: RealtimeMessageSeverity[];
  scope: RecallSubscriptionScope;
  label?: string | null;
  active: boolean;
  created_at: string;
}

export async function createRecallSubscription(payload: RecallSubscriptionCreate): Promise<RecallSubscriptionRecord> {
  return apiPost<RecallSubscriptionRecord>('/api/realtime/recall/subscriptions', payload);
}

export async function listRecallSubscriptions(): Promise<RecallSubscriptionRecord[]> {
  const response = await apiGet<{ subscriptions: RecallSubscriptionRecord[] }>('/api/realtime/recall/subscriptions');
  return response.subscriptions;
}

export async function deleteRecallSubscription(id: string): Promise<boolean> {
  const response = await apiDelete<{ deleted: boolean }>(`/api/realtime/recall/subscriptions/${id}`);
  return Boolean(response?.deleted);
}

// 召回闭环验真：送达 / 点击回流 / 回流率
export interface RecallMetrics {
  delivered: number;
  clicked: number;
  click_through_rate: number;
  skipped: number;
  error: number;
  by_channel: Record<string, number>;
}

export async function getRecallMetrics(): Promise<RecallMetrics> {
  return apiGet<RecallMetrics>('/api/realtime/recall/metrics');
}
