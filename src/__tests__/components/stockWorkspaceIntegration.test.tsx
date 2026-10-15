import React from 'react';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import FinancialTerminal from '../../components/FinancialTerminal';
import { AuthProvider } from '../../context/AuthContext';
import { apiGet, apiPost } from '../../services/apiClient';
import type { AuthUser } from '../../services/authService';
import { authenticateSession, getAuthSnapshot, invalidateAuthSession } from '../../state/authSession';

// Keep the terminal, account watchlist and stock workspace real. Only replace
// remote I/O and the independent research renderer; this tests navigation and
// watchlist ownership without requesting market data or model work.
jest.mock('../../services/apiClient', () => ({
  apiGet: jest.fn(), apiPost: jest.fn(), apiDelete: jest.fn(),
  getApiBaseUrls: () => ['https://example.test'],
  getActiveApiBaseUrl: () => 'https://example.test',
  getAuthenticationApiBaseUrl: () => 'https://example.test',
  sessionOwnsRequest: () => true,
  formatErrorMessage: (error: any) => error?.message || 'Request failed',
  DF_WEB_TOKEN: 'test-web',
}));
jest.mock('../../services/eventService', () => ({
  ...jest.requireActual('../../services/eventService'),
  createRealtimeMessageStream: jest.fn(() => ({ close: jest.fn() })),
}));
jest.mock('../../components/StockIntelligencePanel', () => ({
  __esModule: true,
  default: ({ name, symbol }: { name: string; symbol: string }) =>
    <section aria-label={`${name}个股研究台`}>研究 {symbol}</section>,
}));

const watchlist = {
  symbols: ['002594', '600519'], names: { '002594': '比亚迪', '600519': '贵州茅台' }, empty: false,
};
const account: AuthUser = {
  id: 'stock-workspace-user', username: 'stock-workspace-user', email: null,
  role: 'viewer', is_active: true, created_at: '',
  membership: { tier: 'premium', expires_at: '2027-01-01', days_left: 90 },
};
const originalScrollIntoView = HTMLElement.prototype.scrollIntoView;

beforeAll(() => { HTMLElement.prototype.scrollIntoView = jest.fn(); });
afterAll(() => { HTMLElement.prototype.scrollIntoView = originalScrollIntoView; });
beforeEach(() => {
  jest.useFakeTimers();
  jest.clearAllMocks();
  invalidateAuthSession();
  localStorage.clear();
  localStorage.setItem('bbt_acct_hint_v1', '1');
  window.history.replaceState({}, '', '/');
  (apiGet as jest.Mock).mockImplementation(async (path: string) => {
    if (path === '/api/auth/me') return getAuthSnapshot().account;
    if (path === '/api/me/watchlist') return { ...watchlist, symbols: [...watchlist.symbols], names: { ...watchlist.names } };
    return {
      items: [], messages: [], quotes: [], categories: [], memories: [], markets: [], themes: [],
      symbols: [], names: {}, empty: false, has_more: false,
      available: {}, code: '', unread: 0, exists: false,
    };
  });
  (apiPost as jest.Mock).mockResolvedValue({});
  authenticateSession(account, 'stock-workspace-test', 'https://example.test');
});
afterEach(() => {
  cleanup();
  invalidateAuthSession();
  jest.clearAllTimers();
  jest.useRealTimers();
});

const sidebar = () => within(screen.getByRole('complementary', { name: '主导航' }));
const root = () => document.querySelector('.bbt')!;
const watchlistButtons = () => Array.from(document.querySelectorAll<HTMLButtonElement>('.bbt-watchlist-panel .bbt-qrow-select'));
const saves = () => (apiPost as jest.Mock).mock.calls.filter(([path]) => path === '/api/me/watchlist');

async function click(target: HTMLElement) {
  await act(async () => { fireEvent.click(target); });
}
async function openStock() {
  await act(async () => { render(<AuthProvider><FinancialTerminal /></AuthProvider>); });
  await click(sidebar().getByRole('button', { name: /我的股票/ }));
  expect(watchlistButtons().map(button => button.textContent)).toEqual(['002594 比亚迪', '600519 贵州茅台']);
  await click(screen.getByRole('button', { name: '查看 比亚迪（002594）行情' }));
  expect(root()).toHaveClass('bbt-stock-focused');
  expect(screen.getByRole('navigation', { name: '个股研究导航' })).toBeInTheDocument();
  expect(screen.getByRole('region', { name: '比亚迪个股研究台' })).toBeInTheDocument();
}

it('returns from focused research with the account watchlist intact', async () => {
  await openStock();
  await click(screen.getByRole('button', { name: /返回自选/ }));
  expect(root()).not.toHaveClass('bbt-stock-focused');
  expect(screen.queryByRole('navigation', { name: '个股研究导航' })).not.toBeInTheDocument();
  expect(screen.queryByRole('region', { name: '比亚迪个股研究台' })).not.toBeInTheDocument();
  expect(watchlistButtons().map(button => button.textContent)).toEqual(['002594 比亚迪', '600519 贵州茅台']);
  await act(async () => { jest.advanceTimersByTime(1000); });
  expect(saves()).toEqual([]);
});

it.each([
  ['市场快讯', 'bbt-side-market'], ['深度文章', 'bbt-side-articles'],
])('leaves stock research when explicitly navigating to %s', async (label, className) => {
  await openStock();
  await click(sidebar().getByRole('button', { name: new RegExp(label) }));
  expect(root()).toHaveClass(className);
  expect(root()).not.toHaveClass('bbt-stock-focused');
  expect(screen.queryByRole('navigation', { name: '个股研究导航' })).not.toBeInTheDocument();
  expect(screen.queryByRole('region', { name: '比亚迪个股研究台' })).not.toBeInTheDocument();
  expect(document.querySelector('.bbt-stock-workspace')).not.toBeInTheDocument();
});

it('returns to the existing watchlist to research another stock without changing it', async () => {
  await openStock();
  expect(screen.queryByRole('combobox', { name: '切换研究股票' })).not.toBeInTheDocument();
  await click(screen.getByRole('button', { name: /返回自选/ }));
  await click(screen.getByRole('button', { name: '查看 贵州茅台（600519）行情' }));
  expect(screen.getByRole('region', { name: '贵州茅台个股研究台' })).toBeInTheDocument();
  expect(screen.queryByRole('region', { name: '比亚迪个股研究台' })).not.toBeInTheDocument();
  await act(async () => { jest.advanceTimersByTime(1000); });
  expect(watchlistButtons().map(button => button.textContent)).toEqual(['002594 比亚迪', '600519 贵州茅台']);
  expect(saves()).toEqual([]);
});

it('opens a usable AI workspace from the command bar without leaving stock research mounted', async () => {
  await openStock();
  await click(screen.getByRole('button', { name: '✨ 问 AI' }));
  expect(root()).toHaveClass('bbt-side-ai');
  expect(root()).not.toHaveClass('bbt-stock-focused');
  expect(screen.queryByRole('navigation', { name: '个股研究导航' })).not.toBeInTheDocument();
  expect(document.querySelector('.bbt-stock-workspace')).not.toBeInTheDocument();
  const workspace = within(screen.getByRole('region', { name: 'AI 对话工作区' }));
  const input = workspace.getByRole('textbox', { name: '向 AI 投研助手提问' });
  expect(input).toBeEnabled();
  await act(async () => { fireEvent.change(input, { target: { value: '比较这两家公司的盈利质量' } }); });
  expect(input).toHaveValue('比较这两家公司的盈利质量');
  expect(workspace.getByRole('button', { name: '发送问题' })).toBeEnabled();
  expect(saves()).toEqual([]);
});
