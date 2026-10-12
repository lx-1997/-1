import { TextDecoder, TextEncoder } from 'util';
import { runGeneralChatStream, runToolResearchStream } from '../../services/agentService';
import { authenticateSession, invalidateAuthSession } from '../../state/authSession';
import { PLATFORM_RESULT_EVENT } from '../../utils/platformAssistant';

jest.mock('../../services/apiClient', () => ({
  apiPost: jest.fn(), apiGet: jest.fn(), DF_WEB_TOKEN: 'web-token',
  getActiveApiBaseUrl: () => 'https://example.test',
  getApiBaseUrls: () => ['https://example.test'], sessionOwnsRequest: () => true,
}));

const account = (id: string) => ({ id, username: id, email: null, role: 'viewer' as const, is_active: true, created_at: '' });
const action = { id: 'operation', title: '读取自选', capability_id: 'get:/api/me/watchlist', method: 'GET', path: '/api/me/watchlist', arguments: {}, status: 'completed', result: { symbols: ['NVDA'] } };
const result = { ok: true, answer: '已读取自选股', tool_trace: [], platform_actions: [action] };
const settle = async (rounds = 30) => { for (let i = 0; i < rounds; i++) await Promise.resolve(); };
const originalFetch = global.fetch;
const listener = jest.fn();

beforeEach(() => {
  invalidateAuthSession(); authenticateSession(account('A'), 'token-a', 'https://example.test');
  Object.assign(global, { TextDecoder, TextEncoder });
  listener.mockClear(); window.addEventListener(PLATFORM_RESULT_EVENT, listener);
});
afterEach(() => {
  invalidateAuthSession(); window.removeEventListener(PLATFORM_RESULT_EVENT, listener);
  global.fetch = originalFetch; jest.useRealTimers();
});

function response(chunks: Uint8Array[], pending?: () => Promise<any>) {
  const read = jest.fn();
  chunks.forEach(value => read.mockResolvedValueOnce({ done: false, value }));
  if (pending) read.mockImplementation(pending);
  else read.mockResolvedValue({ done: true });
  const reader = { read, releaseLock: jest.fn(), cancel: jest.fn().mockResolvedValue(undefined) };
  global.fetch = jest.fn().mockResolvedValue({ ok: true, status: 200, body: { getReader: () => reader } });
  return reader;
}
const encode = (text: string) => new TextEncoder().encode(text);
const byteChunks = (text: string) => Array.from(encode(text), byte => Uint8Array.of(byte));

it('decodes Chinese general-chat deltas split at every UTF-8 byte and publishes one receipt', async () => {
  const text = `data: ${JSON.stringify({ delta: '英伟达已加入自选' })}\n\ndata: ${JSON.stringify({ done: true, ...result })}\n\n`;
  const reader = response(byteChunks(text));
  const delta = jest.fn(); const done = jest.fn(); const error = jest.fn();
  runGeneralChatStream({ message: '查看自选股' }, { onDelta: delta, onDone: done, onError: error });
  await settle(encode(text).length * 4 + 30);
  expect(delta).toHaveBeenCalledWith('英伟达已加入自选');
  expect(done).toHaveBeenCalledTimes(1); expect(error).not.toHaveBeenCalled();
  expect(listener).toHaveBeenCalledTimes(1); expect(reader.releaseLock).toHaveBeenCalledTimes(1);
});

it('accepts CRLF research frames when carriage return and newline land in different chunks', async () => {
  const text = `event: status\r\ndata: {"message":"正在读取自选股","platform":true}\r\n\r\nevent: final\r\ndata: ${JSON.stringify(result)}\r\n\r\n`;
  response(byteChunks(text));
  const final = jest.fn(); const status = jest.fn(); const error = jest.fn();
  runToolResearchStream({ message: '查看自选股' }, { onFinal: final, onStatus: status, onError: error });
  await settle(encode(text).length * 4 + 30);
  expect(status).toHaveBeenCalledWith('正在读取自选股');
  expect(final).toHaveBeenCalledTimes(1); expect(listener).toHaveBeenCalledTimes(1); expect(error).not.toHaveBeenCalled();
});

it('reports an interrupted general-chat platform operation if EOF arrives before its receipt', async () => {
  response([encode('data: {"status":"正在执行平台操作","platform":true}\n\n')]);
  const done = jest.fn(); const error = jest.fn();
  runGeneralChatStream({ message: '添加自选股 NVDA' }, { onDelta: jest.fn(), onDone: done, onError: error });
  await settle();
  expect(error).toHaveBeenCalledTimes(1); expect(done).not.toHaveBeenCalled(); expect(listener).not.toHaveBeenCalled();
});

it('reports interrupted research execution on EOF without requesting a replay', async () => {
  response([encode('event: status\ndata: {"message":"正在执行平台操作","platform":true}\n\n')]);
  const final = jest.fn(); const fallback = jest.fn(); const error = jest.fn();
  runToolResearchStream({ message: '添加自选股 NVDA' }, { onFinal: final, onFallback: fallback, onError: error });
  await settle();
  expect(error).toHaveBeenCalledTimes(1); expect(final).not.toHaveBeenCalled(); expect(fallback).not.toHaveBeenCalled();
  expect(global.fetch).toHaveBeenCalledTimes(1);
});

it('ignores a queued research final delivered after its platform idle timeout', async () => {
  jest.useFakeTimers();
  let release!: (value: any) => void;
  response([encode('event: status\ndata: {"message":"正在执行平台操作","platform":true}\n\n')], () => new Promise(resolve => { release = resolve; }));
  const final = jest.fn(); const error = jest.fn(); const fallback = jest.fn();
  runToolResearchStream({ message: '添加自选股 NVDA' }, { onFinal: final, onError: error, onFallback: fallback });
  await settle(); jest.advanceTimersByTime(30001);
  release({ done: false, value: encode(`event: final\ndata: ${JSON.stringify(result)}\n\n`) });
  await settle();
  expect(error).toHaveBeenCalledTimes(1); expect(final).not.toHaveBeenCalled();
  expect(listener).not.toHaveBeenCalled(); expect(fallback).not.toHaveBeenCalled();
});

it('checks account ownership between general-chat frames in the same network chunk', async () => {
  response([encode(`data: {"delta":"A 的第一段"}\n\ndata: {"delta":"A 的后续私有数据"}\n\ndata: ${JSON.stringify({ done: true, ...result })}\n\n`)]);
  const delta = jest.fn(() => authenticateSession(account('B'), 'token-b', 'https://example.test'));
  const done = jest.fn();
  runGeneralChatStream({ message: '查看自选股' }, { onDelta: delta, onDone: done });
  await settle();
  expect(delta).toHaveBeenCalledTimes(1); expect(done).not.toHaveBeenCalled(); expect(listener).not.toHaveBeenCalled();
});

it('checks account ownership between research frames in the same network chunk', async () => {
  response([encode(`event: status\ndata: {"message":"正在读取 A 自选","platform":true}\n\nevent: final\ndata: ${JSON.stringify(result)}\n\n`)]);
  const final = jest.fn();
  runToolResearchStream({ message: '查看自选股' }, {
    onStatus: () => authenticateSession(account('B'), 'token-b', 'https://example.test'), onFinal: final,
  });
  await settle();
  expect(final).not.toHaveBeenCalled(); expect(listener).not.toHaveBeenCalled();
});

it('stops dispatching general-chat frames when the consumer cancels inside a callback', async () => {
  response([encode(`data: {"delta":"第一段"}\n\ndata: {"delta":"取消后的后续内容"}\n\ndata: ${JSON.stringify({ done: true, ...result })}\n\n`)]);
  let cancel!: () => void; const delta = jest.fn(() => cancel()); const done = jest.fn();
  cancel = runGeneralChatStream({ message: '查看自选股' }, { onDelta: delta, onDone: done });
  await settle();
  expect(delta).toHaveBeenCalledTimes(1); expect(done).not.toHaveBeenCalled(); expect(listener).not.toHaveBeenCalled();
});

it('stops dispatching research frames when the consumer cancels inside a callback', async () => {
  response([encode(`event: status\ndata: {"message":"正在读取自选","platform":true}\n\nevent: final\ndata: ${JSON.stringify(result)}\n\n`)]);
  let cancel!: () => void; const final = jest.fn();
  cancel = runToolResearchStream({ message: '查看自选股' }, { onStatus: () => cancel(), onFinal: final });
  await settle();
  expect(final).not.toHaveBeenCalled(); expect(listener).not.toHaveBeenCalled();
});

it('releases the general-chat reader after a transport read failure', async () => {
  const reader = response([encode('data: {"status":"正在执行平台操作","platform":true}\n\n')], () => Promise.reject(new Error('socket closed')));
  const error = jest.fn(); const done = jest.fn();
  runGeneralChatStream({ message: '查看自选股' }, { onDelta: jest.fn(), onError: error, onDone: done });
  await settle();
  expect(error).toHaveBeenCalledTimes(1); expect(done).not.toHaveBeenCalled(); expect(reader.releaseLock).toHaveBeenCalledTimes(1);
});

it('releases the research reader after a transport read failure without replay', async () => {
  const reader = response([encode('event: status\ndata: {"message":"正在执行平台操作","platform":true}\n\n')], () => Promise.reject(new Error('socket closed')));
  const error = jest.fn(); const fallback = jest.fn();
  runToolResearchStream({ message: '查看自选股' }, { onError: error, onFallback: fallback });
  await settle();
  expect(error).toHaveBeenCalledTimes(1); expect(fallback).not.toHaveBeenCalled();
  expect(global.fetch).toHaveBeenCalledTimes(1); expect(reader.releaseLock).toHaveBeenCalledTimes(1);
});

it('accepts one terminal general-chat frame and ignores later deltas and duplicate receipts', async () => {
  response([encode(`data: ${JSON.stringify({ done: true, ...result })}\n\ndata: {"delta":"迟到内容"}\n\ndata: ${JSON.stringify({ done: true, ...result })}\n\n`)]);
  const delta = jest.fn(); const done = jest.fn();
  runGeneralChatStream({ message: '查看自选股' }, { onDelta: delta, onDone: done });
  await settle();
  expect(done).toHaveBeenCalledTimes(1); expect(delta).not.toHaveBeenCalled(); expect(listener).toHaveBeenCalledTimes(1);
});

it('preserves a failed operation status in the research final projection', async () => {
  const failed = { ...result, ok: false, answer: '操作未完成', platform_actions: [{ ...action, status: 'failed', error: '原接口拒绝请求' }] };
  response([encode(`event: final\ndata: ${JSON.stringify(failed)}\n\n`)]);
  const final = jest.fn();
  runToolResearchStream({ request_id: 'stable-operation-id', message: '查看自选股' }, { onFinal: final });
  await settle();
  expect(final).toHaveBeenCalledWith(expect.objectContaining({ ok: false, platform_actions: failed.platform_actions }));
  expect(JSON.parse((global.fetch as jest.Mock).mock.calls[0][1].body).request_id).toBe('stable-operation-id');
});
