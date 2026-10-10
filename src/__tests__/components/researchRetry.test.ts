import { createResearchRetryQueue, isResearchNetworkDrop } from '../../components/FinancialTerminal';

beforeEach(() => jest.useFakeTimers());
afterEach(() => { jest.clearAllTimers(); jest.useRealTimers(); });

it('waits six seconds and caps one task at two retries', () => {
  const queue = createResearchRetryQueue(() => true);
  const request = jest.fn();
  expect(queue.schedule('report:1', 1, () => true, request)).toBe(1);
  jest.advanceTimersByTime(5999);
  expect(request).not.toHaveBeenCalled();
  jest.advanceTimersByTime(1);
  expect(request).toHaveBeenCalledTimes(1);
  expect(queue.schedule('report:1', 1, () => true, request)).toBe(2);
  jest.advanceTimersByTime(6000);
  expect(request).toHaveBeenCalledTimes(2);
  expect(queue.schedule('report:1', 1, () => true, request)).toBe(0);
  jest.advanceTimersByTime(60000);
  expect(request).toHaveBeenCalledTimes(2);
});

it('does not duplicate a pending retry', () => {
  const queue = createResearchRetryQueue(() => true);
  const request = jest.fn();
  queue.schedule('report:1', 1, () => true, request);
  expect(queue.schedule('report:1', 1, () => true, request)).toBe(1);
  jest.advanceTimersByTime(6000);
  expect(request).toHaveBeenCalledTimes(1);
});

it('rejects an old account epoch or a replaced task instance', () => {
  let revision = 1;
  const queue = createResearchRetryQueue(expected => revision === expected);
  const request = jest.fn();
  queue.schedule('report:1', revision, () => true, request);
  revision = 2;
  jest.advanceTimersByTime(6000);
  expect(request).not.toHaveBeenCalled();
  const oldTask = {};
  let currentTask = oldTask;
  queue.schedule('report:2', revision, () => currentTask === oldTask, request);
  currentTask = {};
  jest.advanceTimersByTime(6000);
  expect(request).not.toHaveBeenCalled();
});

it('clears delayed work on success or manual restart and resets its retry budget', () => {
  const queue = createResearchRetryQueue(() => true);
  const oldRequest = jest.fn(), newRequest = jest.fn();
  queue.schedule('report:1', 1, () => true, oldRequest);
  queue.reset('report:1');
  expect(queue.schedule('report:1', 1, () => true, newRequest)).toBe(1);
  jest.advanceTimersByTime(6000);
  expect(oldRequest).not.toHaveBeenCalled();
  expect(newRequest).toHaveBeenCalledTimes(1);
  queue.schedule('report:1', 1, () => true, newRequest);
  queue.reset('report:1');
  jest.advanceTimersByTime(6000);
  expect(newRequest).toHaveBeenCalledTimes(1);
});

it('clears every timer on account replacement or unmount', () => {
  const queue = createResearchRetryQueue(() => true);
  const request = jest.fn();
  queue.schedule('report:1', 1, () => true, request);
  queue.schedule('report:2', 1, () => true, request);
  queue.clear();
  jest.advanceTimersByTime(6000);
  expect(request).not.toHaveBeenCalled();
  expect(jest.getTimerCount()).toBe(0);
});

it.each(['Failed to fetch', 'NetworkError when attempting to fetch resource.', 'Load failed'])('recognizes a browser transport error: %s', message => {
  expect(isResearchNetworkDrop(new TypeError(message))).toBe(true);
});

it('recognizes only the stream reader EOF error rather than general 502 responses', () => {
  expect(isResearchNetworkDrop(Object.assign(new Error('解读中断，请重试'), { streamInterrupted: true, response: { status: 502 } }))).toBe(true);
  expect(isResearchNetworkDrop(Object.assign(new Error('Provider unavailable'), { response: { status: 502 } }))).toBe(false);
});

it.each([
  { name: 'AbortError', message: 'NetworkError' },
  { code: 'ERR_CANCELED', message: 'NetworkError' },
  { code: 'ECONNABORTED', message: 'NetworkError' },
  { response: { status: 401 }, message: 'NetworkError' },
  { response: { status: 402 }, message: 'NetworkError' },
  { response: { status: 403 }, message: 'NetworkError' },
  { response: { status: 422 }, message: 'NetworkError' },
  { message: 'Unexpected JSON parse error' },
  { message: 'Failed to fetch', streamDeliveredResult: true },
])('does not replay a cancellation, backend error or already delivered preview %#', error => {
  expect(isResearchNetworkDrop(error)).toBe(false);
});
