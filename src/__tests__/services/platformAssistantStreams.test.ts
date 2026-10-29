import { TextDecoder, TextEncoder } from 'util';
import { runGeneralChatStream, runToolResearchStream, runToolResearch, runGeneralChat, runOrchestratorChat } from '../../services/agentService';
import { apiPost } from '../../services/apiClient';
import { authenticateSession, getAuthSnapshot, invalidateAuthSession } from '../../state/authSession';
import { PLATFORM_RESULT_EVENT } from '../../utils/platformAssistant';

jest.mock('../../services/apiClient', () => ({
  apiPost: jest.fn(), apiGet: jest.fn(), DF_WEB_TOKEN: 'web-token',
  getApiBaseUrls: () => ['https://example.test'], getActiveApiBaseUrl: () => 'https://example.test', sessionOwnsRequest: () => true,
}));
const action = { id: 'operation', title: '读取自选', capability_id: 'watchlist.list', method: 'GET', path: '/api/me/watchlist', arguments: {}, status: 'completed', result: { symbols: [] } };
const result = { ok: true, answer: '操作已完成', tool_trace: [], platform_actions: [action] };
const settle = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
const listener = jest.fn();
const originalFetch = global.fetch;
beforeEach(() => {
  invalidateAuthSession(); authenticateSession({ id: 'A', username: 'A', email: null, role: 'viewer', is_active: true, created_at: '' }, 'token-a', 'https://example.test');
  Object.assign(global, { TextDecoder, TextEncoder });
  listener.mockClear(); (apiPost as jest.Mock).mockReset();
  window.addEventListener(PLATFORM_RESULT_EVENT, listener);
});
afterEach(() => { invalidateAuthSession(); window.removeEventListener(PLATFORM_RESULT_EVENT, listener); global.fetch = originalFetch; });
function stream(frame: string) {
  const read = jest.fn().mockResolvedValueOnce({ done: false, value: new TextEncoder().encode(frame) }).mockResolvedValue({ done: true });
  global.fetch = jest.fn().mockResolvedValue({ ok: true, status: 200, body: { getReader: () => ({ read, releaseLock: jest.fn() }) } });
}

it('delivers cumulative prose before final and does not publish partial text as completed', async () => {
  stream('event: answer_preview\ndata: {"text":"第一段","provisional":true}\n\nevent: answer_preview\ndata: {"text":"第一段第二段","provisional":true}\n\nevent: final\ndata: {"ok":true,"answer":"已校验全文","tool_trace":[]}\n\n');
  const order: string[] = [];
  runToolResearchStream({ message: '测试流式正文' }, { onPreview: text => order.push(text), onFinal: result => order.push(result.answer) });
  await settle();
  expect(order).toEqual(['第一段','第一段第二段','已校验全文']);
  expect(listener).not.toHaveBeenCalled(); // No operation receipt exists in this text-only final.
});

it('publishes general stream receipts once at done and sends current identity/request ID', async () => {
  stream(`data: ${JSON.stringify({ delta: '完成' })}\n\ndata: ${JSON.stringify({ done: true, ...result })}\n\n`);
  const done = jest.fn();
  runGeneralChatStream({ message: '查看自选股' }, { onDelta: jest.fn(), onDone: done });
  await settle();
  expect(listener).toHaveBeenCalledTimes(1); expect(done).toHaveBeenCalledTimes(1);
  const request = (global.fetch as jest.Mock).mock.calls[0][1];
  expect(request.headers.Authorization).toBe('Bearer token-a');
  expect(request.headers['X-DF-Web']).toBe('web-token');
  expect(JSON.parse(request.body).request_id).toBeTruthy();
  expect(request.signal).toBeDefined();
});

it('publishes research receipts without discarding actions at the final projection', async () => {
  stream(`event: final\ndata: ${JSON.stringify(result)}\n\nevent: final\ndata: ${JSON.stringify(result)}\n\n`);
  const final = jest.fn();
  runToolResearchStream({ message: '查看自选股' }, { onFinal: final });
  await settle();
  expect(listener).toHaveBeenCalledTimes(1);
  expect(final).toHaveBeenCalledTimes(1);
  expect(final.mock.calls[0][0].platform_actions).toEqual([action]);
});

it('aborts a general stream when its account changes and suppresses late receipts', async () => {
  let release!: (value: unknown) => void;
  global.fetch = jest.fn().mockImplementation(() => new Promise(resolve => { release = resolve; }));
  runGeneralChatStream({ message: '查看自选股' }, { onDelta: jest.fn() });
  const signal = (global.fetch as jest.Mock).mock.calls[0][1].signal;
  invalidateAuthSession();
  release({ ok: true, body: { getReader: jest.fn() } });
  await settle();
  expect(signal.aborted).toBe(true); expect(listener).not.toHaveBeenCalled();
});

it('publishes receipts for every non-streaming assistant entry point', async () => {
  (apiPost as jest.Mock).mockResolvedValue(result);
  await runToolResearch('查看自选股');
  await runGeneralChat({ message: '查看自选股' });
  await runOrchestratorChat({ message: '查看自选股', engine: 'deepfocus', mode: 'research' });
  expect(listener).toHaveBeenCalledTimes(3);
  (apiPost as jest.Mock).mock.calls.forEach((call: any[]) => expect(call[1].request_id).toBeTruthy());
  expect(listener.mock.calls[0][0].detail.revision).toBe(getAuthSnapshot().revision);
});

it('does not fall back and replay a request after the server reports execution started', async () => {
  jest.useFakeTimers();
  try {
    const read = jest.fn().mockResolvedValueOnce({ done: false, value: new TextEncoder().encode('event: status\ndata: {"message":"正在执行平台操作","platform":true}\n\n') })
      .mockImplementation(() => new Promise(() => {}));
    global.fetch = jest.fn().mockResolvedValue({ ok: true, status: 200, body: { getReader: () => ({ read, releaseLock: jest.fn() }) } });
    const fallback = jest.fn(); const error = jest.fn();
    const cancel = runToolResearchStream({ message: '修改自选股' }, { onFallback: fallback, onError: error });
    await settle(); jest.advanceTimersByTime(30001);
    expect(fallback).not.toHaveBeenCalled(); expect(error).toHaveBeenCalledTimes(1);
    expect(global.fetch).toHaveBeenCalledTimes(1);
    cancel();
  } finally { jest.useRealTimers(); }
});

it('reports an uncertain ordinary research timeout without replaying it', async () => {
  jest.useFakeTimers();
  try {
    const read = jest.fn().mockResolvedValueOnce({ done: false, value: new TextEncoder().encode('event: status\ndata: {"message":"正在检索证据"}\n\n') })
      .mockImplementation(() => new Promise(() => {}));
    global.fetch = jest.fn().mockResolvedValue({ ok: true, status: 200, body: { getReader: () => ({ read, releaseLock: jest.fn() }) } });
    const fallback = jest.fn(); const error = jest.fn();
    const cancel = runToolResearchStream({ message: '分析 NVDA' }, { onFallback: fallback, onError: error });
    await settle(); jest.advanceTimersByTime(30001);
    expect(fallback).not.toHaveBeenCalled(); expect(error).toHaveBeenCalledTimes(1);
    cancel();
  } finally { jest.useRealTimers(); }
});
