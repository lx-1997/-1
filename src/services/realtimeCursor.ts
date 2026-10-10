import axios from 'axios';
import { apiGet } from './apiClient';
import type { RealtimeMessageFilters, RealtimeMessageRecord } from './eventService';

export interface RealtimeMessageCursor { created_at: string; id: string }
export interface RealtimeMessagePage {
  messages: RealtimeMessageRecord[];
  next_cursor?: RealtimeMessageCursor | null;
  has_more?: boolean;
}
export function newestMessageCursor(messages: RealtimeMessageRecord[]): RealtimeMessageCursor | null {
  let newest: RealtimeMessageCursor | null = null;
  for (const message of messages) {
    if (message.created_at && (!newest || message.created_at > newest.created_at
      || (message.created_at === newest.created_at && message.id > newest.id))) newest = { created_at: message.created_at, id: message.id };
  }
  return newest;
}
export async function fetchRealtimeMessagePage(filters: RealtimeMessageFilters = {}, signal?: AbortSignal): Promise<RealtimeMessagePage> {
  return apiGet<RealtimeMessagePage>('/api/realtime/messages', { params: filters, signal });
}

/** Advance only after a complete page is applied. Push events must never advance this cursor. */
export async function drainRealtimeMessages(cursor: RealtimeMessageCursor | null,
  applyPage: (messages: RealtimeMessageRecord[], cursor: RealtimeMessageCursor | null) => void,
  signal?: AbortSignal): Promise<RealtimeMessageCursor | null> {
  let next = cursor;
  do {
    if (signal?.aborted) throw new axios.CanceledError('Feed closed');
    const page = await fetchRealtimeMessagePage(next
      ? { after_created_at: next.created_at, after_id: next.id, order: 'asc', limit: 200 }
      : { limit: 150 }, signal);
    if (signal?.aborted) throw new axios.CanceledError('Feed closed');
    const candidate = page.next_cursor || newestMessageCursor(page.messages) || next;
    const advanced = candidate && (!next || candidate.created_at > next.created_at
      || (candidate.created_at === next.created_at && candidate.id > next.id));
    if (page.has_more && !advanced) throw new Error('Realtime cursor did not advance');
    applyPage(page.messages, candidate);
    next = candidate;
    if (!page.has_more) return next;
  } while (true);
}
