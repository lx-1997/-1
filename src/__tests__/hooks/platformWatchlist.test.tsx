import { act, renderHook } from '@testing-library/react';
import { useAccountWatchlist } from '../../hooks/useAccountWatchlist';
import { fetchWatchlist, saveWatchlist } from '../../services/watchlistService';
import { authenticateSession, getAuthSnapshot, invalidateAuthSession } from '../../state/authSession';
import { WATCHLIST_UPDATED_EVENT } from '../../utils/platformAssistant';

jest.mock('../../services/watchlistService', () => ({ fetchWatchlist: jest.fn(), saveWatchlist: jest.fn() }));
const fetchList = fetchWatchlist as jest.Mock;
const saveList = saveWatchlist as jest.Mock;
const defaults = ['DEFAULT']; const names = { DEFAULT: 'Default' };
const settle = async () => { await act(async () => { await Promise.resolve(); }); };
beforeEach(() => {
  localStorage.clear(); invalidateAuthSession(); jest.clearAllMocks();
  authenticateSession({ id: 'A', username: 'A', email: null, role: 'viewer', is_active: true, created_at: '' }, 'a', 'https://example.test');
});
afterEach(() => invalidateAuthSession());

it('loads the canonical list after an assistant mutation without saving it again', async () => {
  fetchList.mockResolvedValueOnce({ symbols: ['OLD'], names: {}, empty: false });
  const { result } = renderHook(() => useAccountWatchlist('A', defaults, names));
  await settle();
  fetchList.mockResolvedValueOnce({ symbols: ['NVDA'], names: { NVDA: '英伟达' }, empty: false });
  act(() => window.dispatchEvent(new CustomEvent(WATCHLIST_UPDATED_EVENT, { detail: { revision: getAuthSnapshot().revision, accountId: 'A' } })));
  await settle();
  expect(result.current.watchlist).toEqual(['NVDA']);
  expect(result.current.names.NVDA).toBe('英伟达');
  expect(saveList).not.toHaveBeenCalled();
});

it('does not let an earlier login hydration overwrite a newer assistant mutation', async () => {
  let release!: (data: any) => void;
  fetchList.mockImplementationOnce(() => new Promise(resolve => { release = resolve; }));
  const { result } = renderHook(() => useAccountWatchlist('A', defaults, names));
  fetchList.mockResolvedValueOnce({ symbols: ['NEW'], names: {}, empty: false });
  act(() => window.dispatchEvent(new CustomEvent(WATCHLIST_UPDATED_EVENT, { detail: { revision: getAuthSnapshot().revision, accountId: 'A' } })));
  await settle();
  act(() => release({ symbols: ['OLD'], names: {}, empty: true }));
  await settle();
  expect(result.current.watchlist).toEqual(['NEW']);
  expect(saveList).not.toHaveBeenCalled();
});
