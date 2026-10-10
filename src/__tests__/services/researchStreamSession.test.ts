import { TextDecoder } from 'util';
import { generateResearchDeepDraftSmart } from '../../services/researchService';
import { authenticateSession, getAuthSnapshot, invalidateAuthSession } from '../../state/authSession';
import type { AuthUser } from '../../services/authService';
import { apiPost } from '../../services/apiClient';

jest.mock('../../services/apiClient', () => ({
  apiGet: jest.fn(), apiPost: jest.fn(), DF_WEB_TOKEN: 'test-web',
  getActiveApiBaseUrl: () => 'https://example.test', sessionOwnsRequest: () => true,
}));
const account: AuthUser = { id: 'A', username: 'A', role: 'viewer', email: null, is_active: true, created_at: '' };
const originalFetch = global.fetch;
const originalDecoder = global.TextDecoder;
beforeEach(() => { jest.clearAllMocks(); invalidateAuthSession(); authenticateSession(account, 'test-a', 'https://example.test'); global.TextDecoder = TextDecoder as any; });
afterEach(() => { invalidateAuthSession(); global.fetch = originalFetch; global.TextDecoder = originalDecoder; });

it('uses the API origin for a native research stream and reads the final result', async () => {
  const result = { title: 'Research result', summary: 'Done' };
  const read = jest.fn().mockResolvedValueOnce({ done: false, value: new Uint8Array(Buffer.from(`data: ${JSON.stringify({ type: 'done', data: result })}\n\n`)) }).mockResolvedValueOnce({ done: true });
  global.fetch = jest.fn().mockResolvedValue({ ok: true, body: { getReader: () => ({ read }) } });
  expect(await generateResearchDeepDraftSmart({ title: 'Report', file_id: 'test-file' })).toEqual(result);
  expect(global.fetch).toHaveBeenCalledWith('https://example.test/api/research/deep-draft/stream', expect.objectContaining({ headers: expect.objectContaining({ Authorization: 'Bearer test-a' }), signal: expect.any(AbortSignal) }));
});

it('clears a research stream session on an authenticated 401', async () => {
  global.fetch = jest.fn().mockResolvedValue({ ok: false, status: 401, json: async () => ({ detail: 'Expired' }) });
  await expect(generateResearchDeepDraftSmart({ title: 'Report', file_id: 'test-file' })).rejects.toMatchObject({ response: { status: 401 } });
  expect(getAuthSnapshot().account).toBeNull();
});

it('aborts an old account research stream instead of starting a new fallback request', async () => {
  let signal: AbortSignal | undefined;
  global.fetch = jest.fn().mockImplementation((_url, options) => new Promise((_resolve, reject) => {
    signal = options.signal;
    signal!.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
  }));
  const research = generateResearchDeepDraftSmart({ title: 'Report', file_id: 'test-file' });
  authenticateSession({ ...account, id: 'B' }, 'test-b', 'https://example.test');
  expect(signal!.aborted).toBe(true);
  await expect(research).rejects.toMatchObject({ name: 'AbortError' });
  expect(getAuthSnapshot().account?.id).toBe('B');
});

const streamFrames = (events: unknown[]) => {
  const read = jest.fn()
    .mockResolvedValueOnce({ done: false, value: new Uint8Array(Buffer.from(events.map(event => `data: ${JSON.stringify(event)}\n\n`).join(''))) })
    .mockResolvedValueOnce({ done: true });
  global.fetch = jest.fn().mockResolvedValue({ ok: true, body: { getReader: () => ({ read }) } });
};

it('delivers the quick preview before the deep result', async () => {
  const quick = { one_liner: 'Preview', quick: true };
  const result = { title: 'Research result', summary: 'Deep result' };
  streamFrames([{ type: 'quick', data: quick }, { type: 'done', data: result }]);
  const onQuick = jest.fn();
  expect(await generateResearchDeepDraftSmart({ title: 'Report' }, undefined, onQuick)).toEqual(result);
  expect(onQuick).toHaveBeenCalledWith(quick);
  expect(apiPost).not.toHaveBeenCalled();
});

it('marks a quick-only interrupted stream so the caller cannot replay a paid request', async () => {
  streamFrames([{ type: 'quick', data: { one_liner: 'Preview', quick: true } }]);
  await expect(generateResearchDeepDraftSmart({ title: 'Report' })).rejects.toMatchObject({
    response: { status: 502 }, streamDeliveredResult: true,
  });
  expect(apiPost).not.toHaveBeenCalled();
});

it('stops dispatching frames from a chunk when its quick callback changes the account', async () => {
  streamFrames([{ type: 'quick', data: { one_liner: 'Preview' } }, { type: 'stage', detail: 'Old account progress' }, { type: 'done', data: { title: 'Old account result' } }]);
  const onStage = jest.fn();
  const onQuick = jest.fn(() => authenticateSession({ ...account, id: 'B' }, 'test-b', 'https://example.test'));
  await expect(generateResearchDeepDraftSmart({ title: 'Report' }, onStage, onQuick)).rejects.toMatchObject({ code: 'ERR_CANCELED' });
  expect(onQuick).toHaveBeenCalledTimes(1);
  expect(onStage).not.toHaveBeenCalled();
  expect(apiPost).not.toHaveBeenCalled();
});

it('marks clean EOF before a final result as an interrupted transport', async () => {
  streamFrames([{ type: 'stage', detail: 'Generating' }]);
  await expect(generateResearchDeepDraftSmart({ title: 'Report' })).rejects.toMatchObject({
    streamInterrupted: true, response: { status: 502 },
  });
  expect(apiPost).not.toHaveBeenCalled();
});

it('keeps a backend error distinct from transport EOF', async () => {
  streamFrames([{ type: 'error', status: 502, detail: 'Provider unavailable' }]);
  let error: any;
  try { await generateResearchDeepDraftSmart({ title: 'Report' }); } catch (caught) { error = caught; }
  expect(error).toMatchObject({ response: { status: 502 } });
  expect(error.streamInterrupted).toBeUndefined();
});

it('aborts the stream when its caller unmounts or cancels without starting a fallback', async () => {
  const controller = new AbortController();
  let signal: AbortSignal | undefined;
  global.fetch = jest.fn().mockImplementation((_url, options) => new Promise((_resolve, reject) => {
    signal = options.signal;
    signal!.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
  }));
  const research = generateResearchDeepDraftSmart({ title: 'Report' }, undefined, undefined, controller.signal);
  controller.abort();
  await expect(research).rejects.toMatchObject({ code: 'ERR_CANCELED' });
  expect(signal!.aborted).toBe(true);
  expect(apiPost).not.toHaveBeenCalled();
});

it('never starts a request for an already cancelled caller', async () => {
  const controller = new AbortController();
  controller.abort();
  global.fetch = jest.fn();
  await expect(generateResearchDeepDraftSmart({ title: 'Report' }, undefined, undefined, controller.signal)).rejects.toMatchObject({ code: 'ERR_CANCELED' });
  expect(global.fetch).not.toHaveBeenCalled();
  expect(apiPost).not.toHaveBeenCalled();
});

it('forwards caller cancellation to the older non-stream fallback', async () => {
  const controller = new AbortController();
  streamFrames([]);
  (apiPost as jest.Mock).mockResolvedValueOnce({ title: 'Fallback' });
  expect(await generateResearchDeepDraftSmart({ title: 'Report' }, undefined, undefined, controller.signal)).toEqual({ title: 'Fallback' });
  expect(apiPost).toHaveBeenCalledWith('/api/research/deep-draft', { title: 'Report' }, expect.objectContaining({ signal: controller.signal }));
});
