import { apiGet, apiPost, getApiBaseUrls } from './apiClient';

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
  limit?: number;
}

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
  const urls = options.url?.trim() ? [options.url.trim()] : getApiBaseUrls().map(baseUrl => `${baseUrl}/api/realtime/messages/stream`);
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
