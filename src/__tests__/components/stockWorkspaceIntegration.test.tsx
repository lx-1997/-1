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

jest.mock('../../components/TerminalAuthModal', () => ({
  __esModule: true,
  default: ({ open, onAuthed }: any) => open ? <button onClick={() => mockCompleteLogin(onAuthed)}>完成测试登录</button> : null,
}));
const mockCompleteLogin = (onAuthed: (name: string) => void) => {
  authenticateSession(account, 'stock-workspace-test', 'https://example.test');
  onAuthed(account.username);
};

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
  await click(screen.getByRole('button', { name: '问 AI' }));
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

it('carries a stock into an editable AI draft and returns without submitting or changing the watchlist', async () => {
  await openStock();
  const writes = () => (apiPost as jest.Mock).mock.calls.filter(([path]) => !/^\/api\/(activity|metrics\/)/.test(path));
  const before = writes().length;
  await click(screen.getByRole('button', { name: /带着这只股票问 AI/ }));
  const input = screen.getByRole('textbox', { name: '向 AI 投研助手提问' });
  expect((input as HTMLTextAreaElement).value).toContain('比亚迪（002594）');
  expect(screen.getByRole('navigation', { name: '当前研究上下文' })).toBeInTheDocument();
  expect(writes().length).toBe(before);
  await click(screen.getByRole('button', { name: '← 返回比亚迪研究' }));
  expect(screen.getByRole('region', { name: '比亚迪个股研究台' })).toBeInTheDocument();
  await act(async () => { jest.advanceTimersByTime(0); });
  expect(screen.getByRole('button', { name: /返回自选/ })).toHaveFocus();
  expect(saves()).toEqual([]);
});

it('preserves an existing AI draft when entering from stock research and clears the return context on logout', async () => {
  await openStock();
  await click(screen.getByRole('button', { name: '问 AI' }));
  const input = screen.getByRole('textbox', { name: '向 AI 投研助手提问' });
  await act(async () => { fireEvent.change(input, { target: { value: '保留我正在写的问题' } }); });
  await click(screen.getByRole('button', { name: '← 返回比亚迪研究' }));
  await click(screen.getByRole('button', { name: /带着这只股票问 AI/ }));
  expect(screen.getByRole('textbox', { name: '向 AI 投研助手提问' })).toHaveValue('保留我正在写的问题');
  await act(async () => { invalidateAuthSession(); });
  expect(screen.queryByRole('navigation', { name: '当前研究上下文' })).not.toBeInTheDocument();
});

async function searchApple(anonymous = false) {
  if (anonymous) { invalidateAuthSession(); localStorage.setItem('bbt.watchlist', '[]'); }
  (apiGet as jest.Mock).mockImplementation(async (path: string) => {
    if (path === '/api/auth/me') return getAuthSnapshot().account;
    if (path === '/api/me/watchlist') return { ...watchlist };
    if (path === '/api/market/search') return { candidates: [{ code: 'AAPL', symbol: 'AAPL', name: '苹果', market: 'US' }] };
    if (path === '/api/market/quotes') return { quotes: [] }; // quote outage must not block a known security
    return { items: [], messages: [], quotes: [], markets: [], themes: [], available: {}, has_more: false };
  });
  await act(async () => { render(<AuthProvider><FinancialTerminal /></AuthProvider>); });
  await click(sidebar().getByRole('button', { name: '机构纪要' }));
  await click(screen.getByRole('button', { name: '搜索股票、行业或资讯' }));
  await act(async () => { fireEvent.change(screen.getByPlaceholderText(/搜索代码或名称/), { target: { value: 'AAPL' } }); });
  await act(async () => { jest.advanceTimersByTime(250); });
}

it('adds a verified search result with a semantic button click despite a quote outage, then persists it once', async () => {
  await searchApple();
  const add = screen.getByRole('button', { name: '添加 苹果（AAPL）到自选' });
  // Keyboard activation dispatches click without mousedown.
  await click(add);
  expect(watchlistButtons().map(button => button.textContent)).toContain('AAPL 苹果');
  expect(root()).toHaveClass('bbt-side-notes');
  expect(screen.getByRole('dialog', { name: '搜索股票与添加自选' })).toHaveTextContent('已自选');
  await act(async () => { jest.advanceTimersByTime(800); });
  expect(saves()).toHaveLength(1);
  expect(saves()[0][1].symbols).toContain('AAPL');
});

it('opens a stock search result in the visible research workspace without adding it', async () => {
  await searchApple();
  const input = screen.getByPlaceholderText(/搜索代码或名称/);
  await act(async () => { fireEvent.keyDown(input, { key: 'Enter' }); });
  expect(root()).toHaveClass('bbt-side-stocks');
  expect(screen.getByRole('region', { name: '苹果个股研究台' })).toBeInTheDocument();
  await act(async () => { jest.advanceTimersByTime(1000); });
  // A read-only lookup also updates the display name; the existing hook may persist names.
  expect(watchlistButtons().map(button => button.textContent)).not.toContain('AAPL 苹果');
});


it('continues a guest add after login using the newly hydrated account list', async () => {
  await searchApple(true);
  await click(screen.getByRole('button', { name: '添加 苹果（AAPL）到自选' }));
  await click(screen.getByRole('button', { name: '完成测试登录' }));
  await act(async () => { jest.advanceTimersByTime(800); });
  expect(saves()).toHaveLength(1);
  expect(saves()[0][1].symbols).toEqual(['002594', '600519', 'AAPL']);
});
