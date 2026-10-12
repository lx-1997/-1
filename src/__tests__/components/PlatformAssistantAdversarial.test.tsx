import React from 'react';
import { act, fireEvent, render, renderHook, screen } from '@testing-library/react';
import PlatformAssistantActions from '../../components/PlatformAssistantActions';
import { useAccountWatchlist } from '../../hooks/useAccountWatchlist';
import { apiPost } from '../../services/apiClient';
import { fetchWatchlist, saveWatchlist } from '../../services/watchlistService';
import { authenticateSession, getAuthSnapshot, invalidateAuthSession } from '../../state/authSession';
import { publishPlatformResult, WATCHLIST_UPDATED_EVENT } from '../../utils/platformAssistant';
import type { PlatformAction } from '../../utils/platformAssistant';

jest.mock('../../services/apiClient', () => ({ apiPost: jest.fn() }));
jest.mock('../../services/watchlistService', () => ({ fetchWatchlist: jest.fn(), saveWatchlist: jest.fn() }));
const post = apiPost as jest.Mock;
const fetchList = fetchWatchlist as jest.Mock;
const saveList = saveWatchlist as jest.Mock;
const account = (id: string) => ({ id, username: id, email: null, role: 'viewer' as const, is_active: true, created_at: '' });
const defaults = ['DEFAULT']; const names = { DEFAULT: 'Default' };
const proposal = (id: string): PlatformAction => ({ id, title: `确认 ${id}`, capability_id: 'post:/api/me/watchlist', method: 'POST', path: '/api/me/watchlist', arguments: { symbols: [id] }, status: 'needs_confirmation', confirmation_token: `token-${id}` });
const completed = (id: string): PlatformAction => ({ ...proposal(id), status: 'completed', result: { symbols: [id] } });
const settle = async () => { await act(async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); }); };
const publish = (actions: PlatformAction[], requestId: string) => act(() => publishPlatformResult({ platform_actions: actions }, getAuthSnapshot().revision, requestId));

beforeEach(() => {
  localStorage.clear(); invalidateAuthSession(); authenticateSession(account('A'), 'token-a', 'https://example.test');
  post.mockReset(); fetchList.mockReset(); saveList.mockReset();
});
afterEach(() => { act(() => invalidateAuthSession()); jest.useRealTimers(); });

it('keeps the other reviewed operation available after confirming one action in a multi-action turn', async () => {
  render(<PlatformAssistantActions />);
  publish([proposal('NVDA'), proposal('AAPL')], 'multiple-proposals');
  post.mockResolvedValue({ platform_actions: [completed('NVDA')] });
  await act(async () => fireEvent.click(screen.getAllByRole('button', { name: '确认执行' })[0]));
  expect(screen.getByText('确认 NVDA')).toBeInTheDocument();
  expect(screen.getByText('确认 AAPL')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '确认执行' })).toBeEnabled();
  expect(post).toHaveBeenCalledTimes(1);
  post.mockResolvedValue({ platform_actions: [completed('AAPL')] });
  await act(async () => fireEvent.click(screen.getByRole('button', { name: '确认执行' })));
  expect(screen.getAllByText('已完成')).toHaveLength(2);
  expect(screen.queryByRole('button', { name: '确认执行' })).not.toBeInTheDocument();
  expect(post).toHaveBeenCalledTimes(2);
});

it('replaces the previous turn when a new assistant request returns its own proposal', () => {
  render(<PlatformAssistantActions />);
  publish([proposal('NVDA')], 'previous-turn');
  publish([proposal('AAPL')], 'new-turn');
  expect(screen.queryByText('确认 NVDA')).not.toBeInTheDocument();
  expect(screen.getByText('确认 AAPL')).toBeInTheDocument();
  expect(post).not.toHaveBeenCalled();
});

it('keeps the newer turn visible when an older confirmation finishes after it arrives', async () => {
  render(<PlatformAssistantActions />);
  publish([proposal('NVDA'), proposal('OLD')], 'old-confirming-turn');
  let release!: (result: any) => void; post.mockImplementation(() => new Promise(resolve => { release = resolve; }));
  act(() => fireEvent.click(screen.getAllByRole('button', { name: '确认执行' })[0]));
  publish([proposal('AAPL')], 'new-during-confirmation');
  await act(async () => release({ platform_actions: [completed('NVDA')] }));
  expect(screen.getByText('确认 AAPL')).toBeInTheDocument();
  expect(screen.queryByText('确认 OLD')).not.toBeInTheDocument();
  expect(post).toHaveBeenCalledTimes(1);
});

it('refreshes confirmed data without reopening an operation panel the user closed', async () => {
  render(<PlatformAssistantActions />);
  publish([proposal('NVDA')], 'closed-confirmation');
  let release!: (result: any) => void; post.mockImplementation(() => new Promise(resolve => { release = resolve; }));
  const refreshed = jest.fn(); window.addEventListener(WATCHLIST_UPDATED_EVENT, refreshed);
  try {
    act(() => fireEvent.click(screen.getByRole('button', { name: '确认执行' })));
    act(() => fireEvent.click(screen.getByRole('button', { name: '关闭操作结果' })));
    await act(async () => release({ platform_actions: [completed('NVDA')] }));
    expect(screen.queryByText('确认 NVDA')).not.toBeInTheDocument();
    expect(refreshed).toHaveBeenCalledTimes(1);
  } finally { window.removeEventListener(WATCHLIST_UPDATED_EVENT, refreshed); }
});

it('does not submit the same proposal twice on rapid repeated clicks', async () => {
  render(<PlatformAssistantActions />);
  publish([proposal('NVDA')], 'double-click');
  let release!: (result: any) => void; post.mockImplementation(() => new Promise(resolve => { release = resolve; }));
  const button = screen.getByRole('button', { name: '确认执行' });
  act(() => { fireEvent.click(button); fireEvent.click(button); });
  expect(post).toHaveBeenCalledTimes(1);
  await act(async () => release({ platform_actions: [completed('NVDA')] }));
  expect(screen.getByText('已完成')).toBeInTheDocument();
});

it('suppresses both the confirmation receipt and watchlist event when its account changes in flight', async () => {
  render(<PlatformAssistantActions />);
  publish([proposal('NVDA')], 'confirm-owner');
  let release!: (result: any) => void; post.mockImplementation(() => new Promise(resolve => { release = resolve; }));
  const refreshed = jest.fn(); window.addEventListener(WATCHLIST_UPDATED_EVENT, refreshed);
  try {
    act(() => fireEvent.click(screen.getByRole('button', { name: '确认执行' })));
    act(() => authenticateSession(account('B'), 'token-b', 'https://example.test'));
    await act(async () => release({ platform_actions: [completed('NVDA')] }));
    expect(screen.queryByText('确认 NVDA')).not.toBeInTheDocument(); expect(refreshed).not.toHaveBeenCalled();
  } finally { window.removeEventListener(WATCHLIST_UPDATED_EVENT, refreshed); }
});

it('does not display the old account confirmation failure after a switch', async () => {
  render(<PlatformAssistantActions />);
  publish([proposal('NVDA')], 'old-confirm-error');
  let reject!: (error: Error) => void; post.mockImplementation(() => new Promise((_, fail) => { reject = fail; }));
  act(() => fireEvent.click(screen.getByRole('button', { name: '确认执行' })));
  act(() => authenticateSession(account('B'), 'token-b', 'https://example.test'));
  await act(async () => reject(new Error('A 的确认失败细节')));
  expect(screen.queryByText('A 的确认失败细节')).not.toBeInTheDocument();
});

it('refreshes the canonical watchlist after a confirmed write without saving the server result again', async () => {
  jest.useFakeTimers();
  fetchList.mockResolvedValueOnce({ symbols: ['OLD'], names: {}, empty: false });
  const { result } = renderHook(() => useAccountWatchlist('A', defaults, names));
  render(<PlatformAssistantActions />); await settle();
  expect(result.current.watchlist).toEqual(['OLD']);
  publish([proposal('NVDA')], 'confirmed-refresh');
  fetchList.mockResolvedValueOnce({ symbols: ['OLD', 'NVDA'], names: { NVDA: '英伟达' }, empty: false });
  post.mockResolvedValue({ platform_actions: [completed('NVDA')] });
  await act(async () => fireEvent.click(screen.getByRole('button', { name: '确认执行' }))); await settle();
  expect(result.current.watchlist).toEqual(['OLD', 'NVDA']); expect(result.current.names.NVDA).toBe('英伟达');
  act(() => jest.advanceTimersByTime(1000)); await settle();
  expect(fetchList).toHaveBeenCalledTimes(2); expect(saveList).not.toHaveBeenCalled();
});

it('cancels a pending local autosave before loading an assistant watchlist update', async () => {
  jest.useFakeTimers();
  fetchList.mockResolvedValueOnce({ symbols: ['OLD'], names: {}, empty: false });
  const { result } = renderHook(() => useAccountWatchlist('A', defaults, names)); await settle();
  act(() => result.current.setWatchlist(['OLD', 'LOCAL']));
  act(() => jest.advanceTimersByTime(500));
  fetchList.mockResolvedValueOnce({ symbols: ['OLD', 'NVDA'], names: {}, empty: false });
  publish([completed('NVDA')], 'cancel-autosave'); await settle();
  act(() => jest.advanceTimersByTime(1000)); await settle();
  expect(result.current.watchlist).toEqual(['OLD', 'NVDA']); expect(saveList).not.toHaveBeenCalled();
});

it('does not refresh watchlists for failed writes or successful reads', () => {
  const refreshed = jest.fn(); window.addEventListener(WATCHLIST_UPDATED_EVENT, refreshed);
  try {
    publish([{ ...completed('NVDA'), status: 'failed', error: '未写入' }], 'failed-write');
    publish([{ ...completed('AAPL'), method: 'GET' }], 'successful-read');
    expect(refreshed).not.toHaveBeenCalled();
  } finally { window.removeEventListener(WATCHLIST_UPDATED_EVENT, refreshed); }
});

it('labels atomic watchlist changes and investment research inputs in readable Chinese', () => {
  render(<PlatformAssistantActions />);
  publish([{ ...proposal('NVDA'), arguments: { body: {
    delta: { added: ['NVDA'], removed: ['OLD'], names: { NVDA: '英伟达' } },
    tool: '标的识别', input: { identifier: '英伟达' },
  } } }], 'readable-new-fields');
  expect(screen.getByText('本次变更 · 添加标的')).toBeInTheDocument();
  expect(screen.getByText('本次变更 · 移除标的')).toBeInTheDocument();
  expect(screen.getByText('研究工具')).toBeInTheDocument();
  expect(screen.getByText('输入内容 · 标的名称或代码')).toBeInTheDocument();
  expect(screen.queryByText(/delta|added|removed|identifier/)).not.toBeInTheDocument();
  expect(post).not.toHaveBeenCalled();
});
