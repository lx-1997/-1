import React from 'react';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { runToolResearchStream } from '../../services/agentService';
import FinancialTerminal from '../../components/FinancialTerminal';
import { AuthProvider } from '../../context/AuthContext';
import { apiGet, apiPost } from '../../services/apiClient';
import { createRealtimeMessageStream, type RealtimeMessageRecord } from '../../services/eventService';
import type { AuthUser } from '../../services/authService';
import { authenticateSession, getAuthSnapshot, invalidateAuthSession } from '../../state/authSession';

// Keep the actual terminal, preference controls, dock and onboarding. Replace
// remote I/O so these interaction regressions never request a model or network.
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

jest.mock('../../services/agentService', () => ({
  ...jest.requireActual('../../services/agentService'),
  runToolResearchStream: jest.fn(() => jest.fn()),
}));
const stream = createRealtimeMessageStream as jest.MockedFunction<typeof createRealtimeMessageStream>;
const account: AuthUser = {
  id: 'quiet-user', username: 'quiet-user', email: null,
  role: 'viewer', is_active: true, created_at: '',
  membership: { tier: 'premium', expires_at: '2027-01-01', days_left: 90 },
};
const originalScrollIntoView = HTMLElement.prototype.scrollIntoView;

beforeAll(() => { HTMLElement.prototype.scrollIntoView = jest.fn(); });
afterAll(() => { HTMLElement.prototype.scrollIntoView = originalScrollIntoView; });
beforeEach(() => {
  jest.useFakeTimers();
  jest.clearAllMocks();
  (runToolResearchStream as jest.Mock).mockReturnValue(jest.fn());
  invalidateAuthSession();
  localStorage.clear();
  localStorage.setItem('bbt_acct_hint_v1', '1');
  window.history.replaceState({}, '', '/');
  (apiGet as jest.Mock).mockImplementation(async (path: string) => {
    if (path === '/api/auth/me') return getAuthSnapshot().account;
    return {
      items: [], messages: [], quotes: [], categories: [], memories: [], markets: [], themes: [],
      symbols: [], names: {}, empty: false, has_more: false,
      available: {}, code: '', unread: 0, exists: false,
    };
  });
  (apiPost as jest.Mock).mockResolvedValue({});
  authenticateSession(account, 'quiet-test', 'https://example.test');
});
afterEach(() => {
  cleanup();
  invalidateAuthSession();
  jest.clearAllTimers();
  jest.useRealTimers();
});

async function mountTerminal() {
  let view!: ReturnType<typeof render>;
  await act(async () => { view = render(<AuthProvider><FinancialTerminal /></AuthProvider>); });
  expect(stream).toHaveBeenCalled();
  return view;
}


const memory = (id: string, owner: string, title: string) => ({
  id, owner_user_id: owner, scope: 'user', hall: 'ai_chat', title,
  content: `正文 ${title}`, tags: ['quick'], source: 'ai_chat', created_at: '2026-10-19T00:00:00Z',
});
function mockHistory(get: () => Promise<unknown>) {
  const fallback = (apiGet as jest.Mock).getMockImplementation()!;
  (apiGet as jest.Mock).mockImplementation((path: string) => path.startsWith('/api/dulus/memory?') ? get() : fallback(path));
}
async function openDock() {
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'AI 助手' })); });
  return screen.getByRole('dialog', { name: 'AI 助手' });
}
it('shows only owned titles, restores the selected answer, and has no duplicate composer', async () => {
  mockHistory(async () => ({ memories: [memory('own', account.id, '我的研究'), memory('other', 'other-user', '别人的问题')] }));
  await mountTerminal();
  const dock = await openDock();
  expect(within(dock).getByRole('button', { name: /我的研究/ })).toBeInTheDocument();
  expect(within(dock).queryByText('别人的问题')).not.toBeInTheDocument();
  expect(within(dock).queryByText('正文 我的研究')).not.toBeInTheDocument();
  expect(within(dock).queryByRole('textbox')).not.toBeInTheDocument();
  await act(async () => { fireEvent.click(within(dock).getByRole('button', { name: /我的研究/ })); });
  const workspace = screen.getByRole('region', { name: 'AI 对话工作区' });
  expect(within(workspace).getByText('正文 我的研究')).toBeInTheDocument();
  expect(within(workspace).queryByLabelText('证据驱动研究流程')).not.toBeInTheDocument();
});
it('clears the open dock and rejects old server history when switching accounts', async () => {
  let resolveOld!: (value: unknown) => void;
  const old = new Promise(resolve => { resolveOld = resolve; });
  mockHistory(() => getAuthSnapshot().account?.id === account.id ? old : Promise.resolve({ memories: [memory('new', 'B', 'B 的对话')] }));
  await mountTerminal();
  await openDock();
  await act(async () => { authenticateSession({ ...account, id: 'B', username: 'B' }, 'b-token', 'https://example.test'); });
  expect(screen.queryByRole('dialog', { name: 'AI 助手' })).not.toBeInTheDocument();
  await act(async () => { resolveOld({ memories: [memory('old', account.id, 'A 的迟到数据')] }); });
  const dock = await openDock();
  expect(within(dock).getByRole('button', { name: /B 的对话/ })).toBeInTheDocument();
  expect(within(dock).queryByText('A 的迟到数据')).not.toBeInTheDocument();
});

it('starting a new conversation ignores the cancelled deep request and its late result', async () => {
  mockHistory(async () => ({ memories: [{ ...memory('deep', account.id, '旧深度对话'), tags: ['deep'] }] }));
  await mountTerminal();
  const dock = await openDock();
  await act(async () => { fireEvent.click(within(dock).getByRole('button', { name: /旧深度对话/ })); });
  const workspace = screen.getByRole('region', { name: 'AI 对话工作区' });
  const input = within(workspace).getByRole('textbox', { name: '向 AI 投研助手提问' });
  await act(async () => { fireEvent.change(input, { target: { value: '继续核对旧数据' } }); fireEvent.keyDown(input, { key: 'Enter' }); });
  expect(runToolResearchStream).toHaveBeenCalled();
  const callbacks = (runToolResearchStream as jest.Mock).mock.calls.slice(-1)[0][1];
  await act(async () => { fireEvent.click(within(workspace).getByRole('button', { name: /新会话/ })); });
  await act(async () => { callbacks.onFinal({ ok: true, answer: '迟到正文', roundtable: { synthesis: '迟到正文' } }); });
  expect(input).toHaveValue('');
  expect(within(workspace).getByText('像问分析师一样，直接问')).toBeInTheDocument();
  expect(within(workspace).queryByText('迟到正文')).not.toBeInTheDocument();
  expect(within(workspace).queryByText(/已停止本次研究/)).not.toBeInTheDocument();
});
