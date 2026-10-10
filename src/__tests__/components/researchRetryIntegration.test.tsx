import React from 'react';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import FinancialTerminal from '../../components/FinancialTerminal';
import { AuthProvider } from '../../context/AuthContext';
import { apiGet, apiPost } from '../../services/apiClient';
import { generateResearchDeepDraftSmart } from '../../services/researchService';
import type { AuthUser } from '../../services/authService';
import { authenticateSession, getAuthSnapshot, invalidateAuthSession } from '../../state/authSession';

// Keep the terminal, account store, hooks and report entry real. Replace only
// remote I/O; unrelated market/support requests return empty server fixtures.
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
jest.mock('../../services/researchService', () => ({
  ...jest.requireActual('../../services/researchService'),
  generateResearchDeepDraftSmart: jest.fn(),
}));

const research = generateResearchDeepDraftSmart as jest.MockedFunction<typeof generateResearchDeepDraftSmart>;
const report = {
  id: 'report-fixture', file_id: 'same-report', filename: 'fixture.pdf',
  title: '研报断流集成回归', date: '2026-10-10', created_at: '2026-10-10T09:00:00Z',
  instruments: [], preview_url: '',
};
const account = (id: string): AuthUser => ({
  id, username: id, email: null, role: 'viewer', is_active: true, created_at: '',
  membership: { tier: 'premium', expires_at: '2027-01-01', days_left: 90 },
});
const originalScrollIntoView = HTMLElement.prototype.scrollIntoView;

beforeAll(() => { HTMLElement.prototype.scrollIntoView = jest.fn(); });
afterAll(() => { HTMLElement.prototype.scrollIntoView = originalScrollIntoView; });
beforeEach(() => {
  jest.useFakeTimers();
  jest.clearAllMocks();
  research.mockReset();
  invalidateAuthSession();
  localStorage.clear();
  localStorage.setItem('df_onboarded_v1', '1');
  localStorage.setItem('bbt_acct_hint_v1', '1');
  window.history.replaceState({}, '', '/?tab=research');
  (apiGet as jest.Mock).mockImplementation(async (path: string) => {
    if (path === '/api/auth/me') return getAuthSnapshot().account;
    if (path === '/api/research/wire') return { items: [report] };
    return {
      items: [], messages: [], quotes: [], categories: [], memories: [], markets: [], themes: [],
      symbols: [], names: {}, empty: false, has_more: false,
      available: {}, code: '', unread: 0, exists: false,
    };
  });
  (apiPost as jest.Mock).mockResolvedValue({});
  authenticateSession(account('A'), 'test-a', 'https://example.test');
});
afterEach(() => {
  cleanup();
  invalidateAuthSession();
  jest.clearAllTimers();
  jest.useRealTimers();
});

async function openReport() {
  let view!: ReturnType<typeof render>;
  await act(async () => { view = render(<AuthProvider><FinancialTerminal /></AuthProvider>); });
  expect(screen.getByText(report.title)).toBeInTheDocument();
  expect(apiGet).toHaveBeenCalledWith('/api/research/wire', expect.any(Object));
  await clickReport();
  return view;
}

async function clickReport() {
  await act(async () => { fireEvent.click(screen.getAllByRole('button', { name: 'AI解析' })[0]); });
}

function pendingResearch() {
  research.mockImplementationOnce((_payload, _stage, _quick, signal) => new Promise((_resolve, reject) => {
    signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true });
  }));
}

function expectNoCompactRequest() {
  expect((apiPost as jest.Mock).mock.calls.some(([path]) => path === '/api/research/vision-analyze')).toBe(false);
}

it('keeps a delivered quick preview after a broken stream without another paid request', async () => {
  research.mockImplementationOnce(async (_payload, _stage, onQuick) => {
    onQuick?.({ one_liner: '已交付的速览结论', summary: '速览内容仍可阅读', quick: true });
    // Deliberately omit streamDeliveredResult: the component must also guard
    // the actual quick callback it has already delivered to its task.
    throw new TypeError('Failed to fetch');
  });
  await openReport();
  expect(screen.queryByRole('dialog', { name: 'AI 深度稿' })).not.toBeInTheDocument();
  expect(screen.queryByText(/已交付的速览结论/)).not.toBeInTheDocument();
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: /解读失败：研报断流集成回归/ })); });
  expect(screen.getByText(/已交付的速览结论/)).toBeInTheDocument();
  expect(screen.getByText('速览内容仍可阅读')).toBeInTheDocument();
  await act(async () => { jest.advanceTimersByTime(18_000); });
  expect(research).toHaveBeenCalledTimes(1);
  expectNoCompactRequest();
});

it('does not let account A\'s delayed retry replay or cancel account B\'s same report', async () => {
  research.mockRejectedValueOnce(new TypeError('Failed to fetch'));
  await openReport();
  expect(screen.getByRole('button', { name: /网络波动，6 秒后自动重试（1\/2）/ })).toBeInTheDocument();
  expect(screen.queryByRole('dialog', { name: 'AI 深度稿' })).not.toBeInTheDocument();
  expect(research).toHaveBeenCalledTimes(1);
  pendingResearch();
  await act(async () => { authenticateSession(account('B'), 'test-b', 'https://example.test'); });
  await clickReport();
  expect(research).toHaveBeenCalledTimes(2);
  const newSignal = research.mock.calls[1][3]!;
  expect(newSignal.aborted).toBe(false);
  await act(async () => { jest.advanceTimersByTime(6_000); });
  expect(research).toHaveBeenCalledTimes(2);
  expect(newSignal.aborted).toBe(false);
  expectNoCompactRequest();
});

it('aborts the report controller when the actual terminal unmounts', async () => {
  pendingResearch();
  const view = await openReport();
  const signal = research.mock.calls[0][3]!;
  expect(signal.aborted).toBe(false);
  await act(async () => { view.unmount(); });
  expect(signal.aborted).toBe(true);
  await act(async () => { jest.advanceTimersByTime(12_000); });
  expect(research).toHaveBeenCalledTimes(1);
  expectNoCompactRequest();
});

it('keeps the real report entry usable while account watchlist hydration is pending or empty', async () => {
  const serve = (apiGet as jest.Mock).getMockImplementation()!;
  let hydrate!: (value: { symbols: string[]; names: {}; empty: boolean }) => void;
  const waiting = new Promise(resolve => { hydrate = resolve; });
  (apiGet as jest.Mock).mockImplementation((path: string, ...args: any[]) =>
    path === '/api/me/watchlist' ? waiting : serve(path, ...args));
  pendingResearch();
  const view = await openReport();
  expect(research).toHaveBeenCalledTimes(1);
  await act(async () => { hydrate({ symbols: [], names: {}, empty: false }); });
  expect(research).toHaveBeenCalledTimes(1);
  expect(screen.getAllByText(report.title).length).toBeGreaterThan(0);
  await act(async () => { view.unmount(); });
  expect(research.mock.calls[0][3]!.aborted).toBe(true);
});

it('keeps an anonymous empty watchlist terminal interactive across rerenders', async () => {
  invalidateAuthSession();
  localStorage.setItem('bbt.watchlist', '[]');
  localStorage.setItem('bbt.names', '{}');
  pendingResearch();
  const view = await openReport();
  expect(getAuthSnapshot().account).toBeNull();
  expect(research).toHaveBeenCalledTimes(1);
  await act(async () => { view.rerender(<AuthProvider><FinancialTerminal /></AuthProvider>); });
  // Clicking the actual report again resumes the existing task instead of
  // creating another request; empty quote state must not cause render loops.
  await clickReport();
  expect(research).toHaveBeenCalledTimes(1);
  expect(screen.getByText('✦ 正在后台解读中，完成后提醒你')).toBeInTheDocument();
  expect(apiGet).not.toHaveBeenCalledWith('/api/market/quotes', expect.anything());
  await act(async () => { view.unmount(); });
  expect(research.mock.calls[0][3]!.aborted).toBe(true);
});
