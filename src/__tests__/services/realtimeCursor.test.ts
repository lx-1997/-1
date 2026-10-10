import { apiGet } from '../../services/apiClient';
import { drainRealtimeMessages, type RealtimeMessageCursor } from '../../services/realtimeCursor';
import type { RealtimeMessageRecord } from '../../services/eventService';

jest.mock('../../services/apiClient', () => ({ apiGet: jest.fn() }));
const get = apiGet as jest.MockedFunction<typeof apiGet>;
const message = (id: number): RealtimeMessageRecord => ({ id: String(id).padStart(4, '0'), title: `Item ${id}`, content: '', topic: '快讯', severity: 'info', tags: [], metadata: {}, created_at: '2026-10-10T10:00:00' });
const cursor = (id: number): RealtimeMessageCursor => ({ created_at: '2026-10-10T10:00:00', id: String(id).padStart(4, '0') });
beforeEach(() => jest.clearAllMocks());

it('drains a backlog larger than a page including records with the same timestamp', async () => {
  const backlog = Array.from({ length: 421 }, (_, i) => message(i + 1));
  get.mockResolvedValueOnce({ messages: backlog.slice(0, 200), next_cursor: cursor(200), has_more: true })
    .mockResolvedValueOnce({ messages: backlog.slice(200, 400), next_cursor: cursor(400), has_more: true })
    .mockResolvedValueOnce({ messages: backlog.slice(400), next_cursor: cursor(421), has_more: false });
  const collected: RealtimeMessageRecord[] = [];
  const end = await drainRealtimeMessages(cursor(0), page => collected.push(...page));
  expect(collected).toEqual(backlog); expect(end).toEqual(cursor(421));
  expect(get.mock.calls[1][1]?.params).toEqual({ after_created_at: cursor(200).created_at, after_id: '0200', order: 'asc', limit: 200 });
});

it('continues scanning a permission-filtered empty page using its server cursor', async () => {
  get.mockResolvedValueOnce({ messages: [], next_cursor: cursor(200), has_more: true })
    .mockResolvedValueOnce({ messages: [message(201)], next_cursor: cursor(201), has_more: false });
  const applied: RealtimeMessageRecord[] = [];
  expect(await drainRealtimeMessages(cursor(0), page => applied.push(...page))).toEqual(cursor(201));
  expect(applied.map(record => record.id)).toEqual(['0201']);
});

it('does not apply or advance a cancelled response', async () => {
  const controller = new AbortController();
  get.mockImplementationOnce(async () => { controller.abort(); return { messages: [message(1)], next_cursor: cursor(1), has_more: false }; });
  const apply = jest.fn();
  await expect(drainRealtimeMessages(cursor(0), apply, controller.signal)).rejects.toMatchObject({ code: 'ERR_CANCELED' });
  expect(apply).not.toHaveBeenCalled();
});

it('rejects a nonadvancing page instead of polling forever', async () => {
  get.mockResolvedValueOnce({ messages: [], next_cursor: cursor(0), has_more: true });
  await expect(drainRealtimeMessages(cursor(0), jest.fn())).rejects.toThrow('did not advance');
  expect(get).toHaveBeenCalledTimes(1);
});
