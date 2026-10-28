import React from 'react';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { runToolResearch, runToolResearchStream } from '../../services/agentService';
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
  runToolResearch: jest.fn(),
}));
const stream = createRealtimeMessageStream as jest.MockedFunction<typeof createRealtimeMessageStream>;
const account: AuthUser = {
  id: 'quiet-user', username: 'quiet-user', email: null,
  role: 'viewer', is_active: true, created_at: '',
  membership: { tier: 'premium', expires_at: '2027-01-01', days_left: 90 },
};
const originalScrollIntoView = HTMLElement.prototype.scrollIntoView;
const originalMatchMedia = window.matchMedia;

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
    if (path === '/api/payment-config') return { enabled: true, note: '测试套餐', packages: [{ key: 'month', label: '月卡', days: 30, price: 99 }], wechat: false, alipay: false };
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
  window.matchMedia = originalMatchMedia;
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

async function openChat() {
  await mountTerminal();
  await act(async () => { fireEvent.click(within(screen.getByRole('complementary', { name: '主导航' })).getByRole('button', { name: /AI 对话/ })); });
  const workspace = screen.getByRole('region', { name: 'AI 对话工作区' });
  return { workspace, input: within(workspace).getByRole('textbox', { name: '向 AI 投研助手提问' }) };
}
async function sendQuestion(input: HTMLElement, question: string) {
  await act(async () => { fireEvent.change(input, { target: { value: question } }); });
  await act(async () => { fireEvent.keyDown(input, { key: 'Enter' }); });
  return (runToolResearchStream as jest.Mock).mock.calls.slice(-1)[0][1];
}
it('prevents duplicate Enter requests and preserves a new draft while research is running', async () => {
  const { input, workspace } = await openChat();
  await act(async () => {
    fireEvent.change(input, { target: { value: '核对公司财报' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    fireEvent.keyDown(input, { key: 'Enter' });
  });
  expect(runToolResearchStream).toHaveBeenCalledTimes(1);
  expect((runToolResearchStream as jest.Mock).mock.calls[0][0].context_hint).toContain('优先简明回答');
  await act(async () => { fireEvent.change(input, { target: { value: '下一轮尚未发送的问题' } }); });
  const callbacks = (runToolResearchStream as jest.Mock).mock.calls[0][1];
  await act(async () => { callbacks.onFinal({ ok: true, answer: '核对结果', tool_trace: [], suggestions: [] }); });
  expect(input).toHaveValue('下一轮尚未发送的问题');
  expect(within(workspace).getByText(/已保存到研究历史/)).toBeInTheDocument();
  const completeEvents = (apiPost as jest.Mock).mock.calls.filter(([path, body]) => path === '/api/activity' && body.action === 'ai_answer_complete');
  expect(completeEvents).toHaveLength(1);
  expect(JSON.stringify(completeEvents)).not.toContain('核对公司财报');
});
it('keeps a membership restriction inline and opens pricing only on the explicit action', async () => {
  const { input, workspace } = await openChat();
  const callbacks = await sendQuestion(input, '核对公司财报');
  await act(async () => { callbacks.onFinal({ ok: false, answer: '', tool_trace: [], status: 402, error: '额度已用完' }); });
  expect(input).toHaveValue('核对公司财报');
  expect(screen.queryByRole('dialog', { name: '开通会员' })).not.toBeInTheDocument();
  expect(within(workspace).getByRole('button', { name: '查看会员权益与价格' })).toBeInTheDocument();
  expect(within(workspace).queryByRole('button', { name: '重试原问题' })).not.toBeInTheDocument();
  await act(async () => { fireEvent.click(within(workspace).getByRole('button', { name: '查看会员权益与价格' })); });
  expect(screen.getByRole('dialog', { name: '开通 / 续费会员' })).toHaveTextContent('¥99');
});
it('offers a retry after a service failure and does not resubmit the edited draft', async () => {
  const { input, workspace } = await openChat();
  const callbacks = await sendQuestion(input, '原问题');
  await act(async () => { callbacks.onFinal({ ok: false, answer: '', tool_trace: [], status: 503 }); });
  await act(async () => { fireEvent.change(input, { target: { value: '修改后的草稿' } }); });
  await act(async () => { fireEvent.click(within(workspace).getByRole('button', { name: '重试原问题' })); });
  expect((runToolResearchStream as jest.Mock).mock.calls.slice(-1)[0][0].message).toBe('原问题');
});
it('ignores late tool events after stopping and after a completed answer', async () => {
  const { input, workspace } = await openChat();
  const callbacks = await sendQuestion(input, '停止测试');
  await act(async () => { fireEvent.click(within(workspace).getByRole('button', { name: '停止' })); });
  await act(async () => { callbacks.onTool({ tool: 'get_valuation', summary: '迟到工具结果', ok: true }, 'done'); });
  expect(within(workspace).queryByText(/迟到工具结果/)).not.toBeInTheDocument();
  expect(input).toHaveValue('停止测试');
  const next = await sendQuestion(input, '下一轮');
  await act(async () => { next.onFinal({ ok: true, answer: '有效结果', tool_trace: [] }); });
  await act(async () => { next.onTool({ tool: 'get_valuation', summary: '完成后迟到', ok: true }, 'done'); });
  expect(within(workspace).queryByText(/完成后迟到/)).not.toBeInTheDocument();
});
it('keeps headlines visible at desktop width and preserves article context through fallback and owned history', async () => {
  window.matchMedia = jest.fn().mockImplementation(query => ({ matches: query === '(min-width: 1366px)', media: query, addEventListener: jest.fn(), removeEventListener: jest.fn() }));
  const message: RealtimeMessageRecord = { tags: ['文章'], metadata: {}, id: 'context-article', title: '公司新增订单', content: '当前可见摘要：订单增长。—— 🔒 不应作为可见摘要发送的尾注', topic: '文章', severity: 'info', source_name: 'DAO财经', source_type: 'dao-article', url: 'https://backend.futoucaixin.cn/uploads/example.png', created_at: new Date().toISOString() };
  const fallback = (apiGet as jest.Mock).getMockImplementation()!;
  (apiGet as jest.Mock).mockImplementation((path: string) => path === '/api/realtime/messages' ? Promise.resolve({ messages: [message], has_more: false }) : path.endsWith('/original-text') ? Promise.resolve({ id: message.id, title: message.title, content: '当前可见摘要', paragraphs: ['当前可见摘要'], parser: 'fixture', truncated: false }) : fallback(path));
  await mountTerminal();
  await act(async () => { fireEvent.click(within(screen.getByRole('complementary', { name: '主导航' })).getByRole('button', { name: /深度文章/ })); });
  expect(document.querySelector('.bbt-rail')).not.toBeInTheDocument();
  await act(async () => { fireEvent.click(screen.getByText('公司新增订单', { exact: true })); });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '围绕这篇问 AI' })); });
  const workspace = screen.getByRole('region', { name: 'AI 对话工作区' });
  expect(within(workspace).getByLabelText('本次参考资料')).toHaveTextContent('公司新增订单');
  expect(runToolResearchStream).not.toHaveBeenCalled();
  const input = within(workspace).getByRole('textbox', { name: '向 AI 投研助手提问' });
  let resolveFallback!: (value: unknown) => void;
  (runToolResearch as jest.Mock).mockImplementationOnce(() => new Promise(resolve => { resolveFallback = resolve; }));
  await act(async () => { fireEvent.keyDown(input, { key: 'Enter' }); });
  const [request, callbacks] = (runToolResearchStream as jest.Mock).mock.calls[0];
  expect(request.context_hint).toContain('当前可见摘要');
  expect(request.context_hint).not.toContain('不应作为可见摘要发送');
  await act(async () => { callbacks.onFallback('断线'); });
  expect((runToolResearch as jest.Mock).mock.calls[0][5].context_hint).toBe(request.context_hint);
  await act(async () => { resolveFallback({ ok: true, answer: '文章研究结论', tool_trace: [] }); });
  await act(async () => { fireEvent.click(within(workspace).getByRole('button', { name: /新会话/ })); });
  expect(within(workspace).queryByLabelText('本次参考资料')).not.toBeInTheDocument();
  await act(async () => { fireEvent.click(within(workspace).getByRole('button', { name: /继续上次研究/ })); });
  expect(within(workspace).getByLabelText('本次参考资料')).toHaveTextContent('公司新增订单');
  expect(within(workspace).getByText('文章研究结论')).toBeInTheDocument();
});
it('can stop the deep-to-quick fallback and a late result cannot replace a new conversation', async () => {
  mockHistory(async () => ({ memories: [{ ...memory('deep-retry', account.id, '深度回退测试'), tags: ['deep'] }] }));
  await mountTerminal();
  const dock = await openDock();
  await act(async () => { fireEvent.click(within(dock).getByRole('button', { name: /深度回退测试/ })); });
  const workspace = screen.getByRole('region', { name: 'AI 对话工作区' });
  const input = within(workspace).getByRole('textbox', { name: '向 AI 投研助手提问' });
  let finishFallback!: (value: unknown) => void;
  (runToolResearch as jest.Mock).mockImplementationOnce(() => new Promise(resolve => { finishFallback = resolve; }));
  const callbacks = await sendQuestion(input, '旧深度请求');
  await act(async () => { callbacks.onError('暂时不可用', 503); });
  expect(runToolResearch).toHaveBeenCalledTimes(1);
  const signal = (runToolResearch as jest.Mock).mock.calls[0][5].signal as AbortSignal;
  await act(async () => { fireEvent.click(within(workspace).getByRole('button', { name: '停止' })); });
  expect(signal.aborted).toBe(true);
  expect(input).toHaveValue('旧深度请求');
  await act(async () => { fireEvent.click(within(workspace).getByRole('button', { name: /新会话/ })); });
  const next = await sendQuestion(input, '新问题');
  await act(async () => { finishFallback({ ok: true, answer: '迟到的旧回退', tool_trace: [] }); });
  expect(within(workspace).queryByText('迟到的旧回退')).not.toBeInTheDocument();
  expect(within(workspace).getByRole('button', { name: '发送问题' })).toBeDisabled();
  await act(async () => { next.onFinal({ ok: true, answer: '新会话的回答', tool_trace: [] }); });
  expect(within(workspace).getByText('新会话的回答')).toBeInTheDocument();
});

it('preserves the same account draft when a claimed trial changes the authentication revision', async () => {
  await act(async () => { authenticateSession({ ...account, membership: null, trial_claimable: true }, 'claim-test', 'https://example.test'); });
  const fallback = (apiPost as jest.Mock).getMockImplementation()!;
  (apiPost as jest.Mock).mockImplementation((path: string, body: unknown) => path === '/api/membership/claim-trial' ? Promise.resolve({ days: 3, membership: account.membership }) : fallback(path, body));
  const { input, workspace } = await openChat();
  const callbacks = await sendQuestion(input, '领取后继续研究这个问题');
  await act(async () => { callbacks.onFinal({ ok: false, answer: '', tool_trace: [], status: 403 }); });
  await act(async () => { fireEvent.click(within(workspace).getByRole('button', { name: '领取可用体验权益' })); });
  expect(input).toHaveValue('领取后继续研究这个问题');
  expect(getAuthSnapshot().account?.membership?.tier).toBe('premium');
});
it('closes an open global menu when entering the assistant', async () => {
  await mountTerminal();
  await act(async () => { fireEvent.click(document.querySelector('.bbt-more-btn')!); });
  expect(document.querySelector('.bbt-more-pop')).toBeInTheDocument();
  await act(async () => { fireEvent.click(within(screen.getByRole('complementary', { name: '主导航' })).getByRole('button', { name: /AI 对话/ })); });
  expect(document.querySelector('.bbt-more-pop')).not.toBeInTheDocument();
});
