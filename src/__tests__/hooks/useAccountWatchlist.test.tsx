import { act, renderHook } from '@testing-library/react';
import { authenticateSession, invalidateAuthSession } from '../../state/authSession';
import { useAccountWatchlist } from '../../hooks/useAccountWatchlist';
import { fetchWatchlist, saveWatchlist } from '../../services/watchlistService';
import type { AuthUser } from '../../services/authService';

jest.mock('../../services/watchlistService', () => ({ fetchWatchlist: jest.fn(), saveWatchlist: jest.fn() }));
const fetchList = fetchWatchlist as jest.MockedFunction<typeof fetchWatchlist>;
const saveList = saveWatchlist as jest.MockedFunction<typeof saveWatchlist>;
const defaults = ['DEFAULT'];
const defaultNames = { DEFAULT: 'Default' };
const user = (id: string): AuthUser => ({ id, username: id, email: null, role: 'viewer', is_active: true, created_at: '' });
const settle = async () => { await act(async () => { await Promise.resolve(); }); };
beforeEach(() => {
  jest.useFakeTimers(); invalidateAuthSession(); localStorage.clear(); jest.clearAllMocks();
  saveList.mockResolvedValue({ symbols: [], names: {} });
});
afterEach(() => { invalidateAuthSession(); jest.useRealTimers(); });

it('keeps an intentionally empty server list empty and does not write a guest seed', async () => {
  authenticateSession(user('A'), 'test-a', 'https://example.test');
  fetchList.mockResolvedValueOnce({ symbols: [], names: {}, empty: false });
  const { result } = renderHook(() => useAccountWatchlist('A', defaults, defaultNames));
  await settle();
  expect(result.current.watchlist).toEqual([]);
  act(() => jest.advanceTimersByTime(1000));
  expect(saveList).not.toHaveBeenCalled();
});

it('seeds only a first login using the guest list', async () => {
  localStorage.setItem('bbt.watchlist', JSON.stringify(['GUEST']));
  authenticateSession(user('A'), 'test-a', 'https://example.test');
  fetchList.mockResolvedValueOnce({ symbols: [], names: {}, empty: true });
  const { result } = renderHook(() => useAccountWatchlist('A', defaults, defaultNames));
  await settle();
  expect(result.current.watchlist).toEqual(['GUEST']);
  await act(async () => { jest.advanceTimersByTime(800); });
  expect(saveList).toHaveBeenCalledWith(['GUEST'], defaultNames, expect.any(AbortSignal));
});

it('cancels a previous account hydration and prevents its late response or setter overwriting the next account', async () => {
  let finishA!: (data: any) => void;
  fetchList.mockImplementationOnce(() => new Promise(resolve => { finishA = resolve; }));
  fetchList.mockResolvedValueOnce({ symbols: ['B-STOCK'], names: {}, empty: false });
  authenticateSession(user('A'), 'test-a', 'https://example.test');
  const { result, rerender } = renderHook(({ id }) => useAccountWatchlist(id, defaults, defaultNames), { initialProps: { id: 'A' } });
  const oldSet = result.current.setWatchlist;
  const oldSignal = fetchList.mock.calls[0][0];
  act(() => authenticateSession(user('B'), 'test-b', 'https://example.test'));
  rerender({ id: 'B' }); await settle();
  expect(oldSignal?.aborted).toBe(true);
  act(() => { oldSet(['A-LATE']); finishA({ symbols: ['A-STOCK'], names: {}, empty: false }); });
  await settle();
  expect(result.current.watchlist).toEqual(['B-STOCK']);
});

it('cancels a debounced save before switching account and never seeds B with the A list', async () => {
  authenticateSession(user('A'), 'test-a', 'https://example.test');
  fetchList.mockResolvedValueOnce({ symbols: ['A-STOCK'], names: {}, empty: false });
  fetchList.mockResolvedValueOnce({ symbols: [], names: {}, empty: false });
  const { result, rerender } = renderHook(({ id }) => useAccountWatchlist(id, defaults, defaultNames), { initialProps: { id: 'A' } });
  await settle(); act(() => result.current.setWatchlist(['A-EDIT']));
  act(() => authenticateSession(user('B'), 'test-b', 'https://example.test'));
  rerender({ id: 'B' }); await settle();
  act(() => jest.advanceTimersByTime(1000));
  expect(result.current.watchlist).toEqual([]);
  expect(saveList).not.toHaveBeenCalled();
});

it('keeps the pending list identity stable until server hydration finishes', () => {
  authenticateSession(user('A'), 'test-a', 'https://example.test');
  fetchList.mockImplementationOnce(() => new Promise(() => {}));
  const { result, rerender } = renderHook(() => useAccountWatchlist('A', defaults, defaultNames));
  const pendingSymbols = result.current.watchlist;
  const pendingNames = result.current.names;
  rerender(); rerender();
  expect(result.current.watchlist).toBe(pendingSymbols);
  expect(result.current.names).toBe(pendingNames);
  expect(fetchList).toHaveBeenCalledTimes(1);
});

it('keeps an anonymous empty list stable across unrelated renders', () => {
  localStorage.setItem('bbt.watchlist', '[]');
  const { result, rerender } = renderHook(() => useAccountWatchlist(null, defaults, defaultNames));
  const symbols = result.current.watchlist;
  rerender(); rerender();
  expect(result.current.watchlist).toBe(symbols);
  expect(symbols).toEqual([]);
  expect(fetchList).not.toHaveBeenCalled();
});

it('keeps a deliberately empty server list stable after hydration', async () => {
  authenticateSession(user('A'), 'test-a', 'https://example.test');
  fetchList.mockResolvedValueOnce({ symbols: [], names: {}, empty: false });
  const { result, rerender } = renderHook(() => useAccountWatchlist('A', defaults, defaultNames));
  await settle();
  const symbols = result.current.watchlist;
  rerender(); rerender();
  expect(result.current.watchlist).toBe(symbols);
  expect(fetchList).toHaveBeenCalledTimes(1);
  expect(saveList).not.toHaveBeenCalled();
});

it('exposes failed hydration and retries without writing an empty list', async () => {
  authenticateSession(user('A'), 'test-a', 'https://example.test');
  fetchList.mockRejectedValueOnce(new Error('offline'));
  fetchList.mockResolvedValueOnce({ symbols: ['SERVER'], names: {}, empty: false });
  const { result } = renderHook(() => useAccountWatchlist('A', defaults, defaultNames));
  await settle();
  expect(result.current.ready).toBe(false);
  expect(result.current.loadError).toContain('加载失败');
  act(() => result.current.setWatchlist(['IGNORED']));
  expect(saveList).not.toHaveBeenCalled();
  act(() => result.current.retrySync()); await settle();
  expect(result.current.ready).toBe(true);
  expect(result.current.loadError).toBe('');
  expect(result.current.watchlist).toEqual(['SERVER']);
  expect(saveList).not.toHaveBeenCalled();
});

it('retains failed edits, exposes the sync error and retries the latest list', async () => {
  authenticateSession(user('A'), 'test-a', 'https://example.test');
  fetchList.mockResolvedValueOnce({ symbols: ['SERVER'], names: {}, empty: false });
  saveList.mockRejectedValueOnce(new Error('offline'));
  const { result } = renderHook(() => useAccountWatchlist('A', defaults, defaultNames));
  await settle();
  act(() => result.current.setWatchlist(['SERVER', 'NEW']));
  await act(async () => { jest.advanceTimersByTime(800); });
  expect(result.current.saveError).toContain('尚未同步');
  expect(result.current.watchlist).toEqual(['SERVER', 'NEW']);
  act(() => result.current.setWatchlist(['SERVER', 'LATEST']));
  act(() => result.current.retrySync());
  await act(async () => { jest.advanceTimersByTime(800); });
  expect(saveList).toHaveBeenLastCalledWith(['SERVER', 'LATEST'], expect.any(Object), expect.any(AbortSignal));
  expect(result.current.saveError).toBe('');
  expect(result.current.syncing).toBe(false);
});

it('serializes in-flight saves and follows with the newest snapshot without aborting the old write', async () => {
  authenticateSession(user('A'), 'test-a', 'https://example.test');
  fetchList.mockResolvedValueOnce({ symbols: ['SERVER'], names: {}, empty: false });
  let finish!: (data: any) => void;
  saveList.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  const { result } = renderHook(() => useAccountWatchlist('A', defaults, defaultNames));
  await settle();
  act(() => result.current.setWatchlist(['FIRST']));
  await act(async () => { jest.advanceTimersByTime(800); });
  const signal = saveList.mock.calls[0][2];
  act(() => result.current.setWatchlist(['LATEST']));
  await act(async () => { jest.advanceTimersByTime(2000); });
  expect(saveList).toHaveBeenCalledTimes(1);
  expect(signal?.aborted).toBe(false);
  await act(async () => { finish({ symbols: ['FIRST'], names: {} }); });
  await act(async () => { jest.advanceTimersByTime(800); });
  expect(saveList).toHaveBeenCalledTimes(2);
  expect(saveList.mock.calls[1][0]).toEqual(['LATEST']);
  expect(result.current.syncing).toBe(false);
});
