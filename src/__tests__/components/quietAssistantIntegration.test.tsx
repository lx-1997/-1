import React from 'react';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import FinancialTerminal from '../../components/FinancialTerminal';
import { AuthProvider } from '../../context/AuthContext';
import { apiGet, apiPost } from '../../services/apiClient';
import { createRealtimeMessageStream, type RealtimeMessageRecord } from '../../services/eventService';
import { runToolResearch, runToolResearchStream } from '../../services/agentService';
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
  runToolResearch: jest.fn(), runToolResearchStream: jest.fn(() => jest.fn()),
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
  (runToolResearchStream as jest.Mock).mockImplementation(() => jest.fn());
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

async function deliver(id: string, overrides: Partial<RealtimeMessageRecord> = {}) {
  const message: RealtimeMessageRecord = {
    id, title: `财经消息 ${id}`, content: '测试事件内容', topic: '快讯', severity: 'warning',
    created_at: new Date().toISOString(), tags: [], metadata: {}, ...overrides,
  };
  await act(async () => {
    stream.mock.calls[stream.mock.calls.length - 1][0].onMessage(message);
  });
  return message;
}

const popup = () => document.querySelector('.bbt-news-popup');
const moreButton = () => screen.getAllByRole('button', { name: /^更多/ })[0];
async function openPopupPreferences() {
  await act(async () => { fireEvent.click(moreButton()); });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: /弹窗提醒/ })); });
}
async function chooseMode(name: '全部' | '仅重要' | '关闭') {
  await act(async () => {
    fireEvent.click(within(screen.getByRole('group', { name: '弹窗提醒级别' }))
      .getByRole('button', { name }));
  });
}
async function closeMore() {
  await act(async () => { fireEvent.click(moreButton()); });
}

it('ignores the old automatic all preference while still accepting fresh feed messages', async () => {
  localStorage.setItem('bbt.news_popup_mode.v1', JSON.stringify('all'));
  localStorage.setItem('bbt.news_popup_topics.v1', JSON.stringify(['快讯', '文章', '研报', '机构纪要']));
  await mountTerminal();
  await deliver('new-article', { topic: '文章', severity: 'info' });
  await deliver('urgent-news', { severity: 'critical' });
  expect(popup()).not.toBeInTheDocument();
  await openPopupPreferences();
  expect(within(screen.getByRole('group', { name: '弹窗提醒级别' }))
    .getByRole('button', { name: '关闭' })).toHaveAttribute('aria-pressed', 'true');
  expect((apiPost as jest.Mock).mock.calls.some(([path]) => /ai-analyze|vision-analyze|agent/.test(path))).toBe(false);
});

it('shows reminders only after an explicit choice and restores that v2 choice on remount', async () => {
  const view = await mountTerminal();
  await openPopupPreferences();
  await chooseMode('仅重要');
  expect(localStorage.getItem('bbt.news_popup_mode.v2')).toBe(JSON.stringify('important'));
  await closeMore();
  const chosen = await deliver('chosen-important');
  expect(popup()).toHaveTextContent(chosen.title);
  await act(async () => { view.unmount(); });
  await mountTerminal();
  await deliver('restored-important');
  expect(popup()).toHaveTextContent('财经消息 restored-important');
});

it('clears the current queue when reminders are turned off and keeps later messages quiet', async () => {
  localStorage.setItem('bbt.news_popup_mode.v2', JSON.stringify('all'));
  await mountTerminal();
  await deliver('first');
  await deliver('queued');
  expect(popup()).toHaveTextContent('另有 1 条');
  await openPopupPreferences();
  await chooseMode('关闭');
  expect(popup()).not.toBeInTheDocument();
  await closeMore();
  await deliver('after-off', { severity: 'critical' });
  expect(popup()).not.toBeInTheDocument();
  expect(localStorage.getItem('bbt.news_popup_mode.v2')).toBe(JSON.stringify('off'));
});

it('does not interrupt typing or the complete AI conversation even when reminders are enabled', async () => {
  localStorage.setItem('bbt.news_popup_mode.v2', JSON.stringify('all'));
  await mountTerminal();
  await openPopupPreferences();
  const input = screen.getByRole('textbox', { name: '添加弹窗关键词' });
  input.focus();
  await deliver('while-typing');
  expect(popup()).not.toBeInTheDocument();
  input.blur();
  await closeMore();
  await act(async () => {
    fireEvent.click(within(screen.getByRole('complementary', { name: '主导航' }))
      .getByRole('button', { name: /AI 对话/ }));
  });
  await deliver('during-ai');
  expect(screen.getByRole('region', { name: 'AI 对话工作区' })).toBeInTheDocument();
  expect(popup()).not.toBeInTheDocument();
});

it('leaves a first visit quiet after eight seconds but keeps the manual tour available', async () => {
  expect(localStorage.getItem('df_onboarded_v1')).toBeNull();
  await mountTerminal();
  await act(async () => { jest.advanceTimersByTime(9000); });
  expect(screen.queryByRole('dialog', { name: /第 1 步/ })).not.toBeInTheDocument();
  await act(async () => { fireEvent.click(moreButton()); });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '❔ 新手引导' })); });
  expect(screen.getByRole('dialog', { name: /第 1 步/ })).toBeInTheDocument();
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '跳过引导' })); });
  expect(screen.queryByRole('dialog', { name: /第 1 步/ })).not.toBeInTheDocument();
});

it('names the floating assistant clearly and lets Enter and Space expand and collapse it', async () => {
  await mountTerminal();
  const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime });
  const fab = screen.getByRole('button', { name: 'AI 助手' });
  expect(fab).toHaveTextContent('AI 助手');
  expect(fab).toHaveAttribute('aria-expanded', 'false');
  expect(fab).toHaveAttribute('aria-controls', 'bbt-ai-dock-panel');
  fab.focus();
  await act(async () => { await user.keyboard('{Enter}'); });
  expect(fab).toHaveAttribute('aria-expanded', 'true');
  expect(screen.getByRole('dialog', { name: 'AI 助手' })).toHaveAttribute('id', 'bbt-ai-dock-panel');
  await act(async () => { await user.keyboard(' '); });
  expect(fab).toHaveAttribute('aria-expanded', 'false');
  expect(screen.queryByRole('dialog', { name: 'AI 助手' })).not.toBeInTheDocument();
});

it('uses the compact AI navigation entry to expand, collapse and enter a full conversation without sending', async () => {
  await mountTerminal();
  const entry = within(screen.getByRole('navigation', { name: '移动端内容导航' })).getByRole('button', { name: /^AI$/ });
  expect(entry).toHaveAttribute('aria-haspopup', 'dialog');
  expect(entry).toHaveAttribute('aria-expanded', 'false');
  await act(async () => { fireEvent.click(entry); });
  expect(screen.getByRole('dialog', { name: 'AI 助手' })).toBeInTheDocument();
  await act(async () => { fireEvent.click(entry); });
  expect(screen.queryByRole('dialog', { name: 'AI 助手' })).not.toBeInTheDocument();
  await act(async () => { fireEvent.click(entry); });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: /开始新对话/ })); });
  expect(screen.getByRole('region', { name: 'AI 对话工作区' })).toBeInTheDocument();
  expect(screen.queryByRole('dialog', { name: 'AI 助手' })).not.toBeInTheDocument();
  expect((apiPost as jest.Mock).mock.calls.some(([path]) => /ai-analyze|vision-analyze|tool-research/.test(path))).toBe(false);
});

// jsdom does not expose PointerEvent in all supported versions. MouseEvent
// carries the real coordinates; these events still reach React's pointer hooks.
function pointer(target: HTMLElement, type: string, x: number, y: number) {
  const event = new MouseEvent(type, { bubbles: true, clientX: x, clientY: y });
  Object.defineProperty(event, 'pointerId', { value: 1 });
  fireEvent(target, event);
}

it('does not open the assistant from the compatibility click following a drag', async () => {
  await mountTerminal();
  const fab = screen.getByRole('button', { name: 'AI 助手' });
  await act(async () => {
    pointer(fab, 'pointerdown', 100, 100);
    pointer(fab, 'pointermove', 50, 60);
    pointer(fab, 'pointerup', 50, 60);
    fireEvent.click(fab, { detail: 1 });
  });
  expect(localStorage.getItem('bbt_ai_dock_pos')).not.toBeNull();
  expect(fab).toHaveAttribute('aria-expanded', 'false');
  expect(screen.queryByRole('dialog', { name: 'AI 助手' })).not.toBeInTheDocument();
});

it('toggles exactly once for a pointer tap and preserves the expanded state during a drag', async () => {
  await mountTerminal();
  const fab = screen.getByRole('button', { name: 'AI 助手' });
  await act(async () => {
    pointer(fab, 'pointerdown', 100, 100);
    pointer(fab, 'pointerup', 100, 100);
    fireEvent.click(fab, { detail: 1 });
  });
  expect(fab).toHaveAttribute('aria-expanded', 'true');
  await act(async () => {
    pointer(fab, 'pointerdown', 100, 100);
    pointer(fab, 'pointermove', 50, 60);
    pointer(fab, 'pointerup', 50, 60);
    fireEvent.click(fab, { detail: 1 });
  });
  expect(fab).toHaveAttribute('aria-expanded', 'true');
  expect(screen.getByRole('dialog', { name: 'AI 助手' })).toBeInTheDocument();
});

it('closes the floating home with Escape and restores focus to its launcher', async () => {
  await mountTerminal();
  const fab = screen.getByRole('button', { name: 'AI 助手' });
  await act(async () => { fireEvent.click(fab); });
  expect(screen.getByRole('dialog', { name: 'AI 助手' })).toBeInTheDocument();
  await act(async () => { fireEvent.keyDown(document, { key: 'Escape' }); });
  expect(screen.queryByRole('dialog', { name: 'AI 助手' })).not.toBeInTheDocument();
  expect(fab).toHaveFocus();
});

it('prepares a market research draft from the launcher without submitting an AI request', async () => {
  await mountTerminal();
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'AI 助手' })); });
  const writesBefore = (apiPost as jest.Mock).mock.calls.filter(([path]) => /ai|research/.test(path));
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: /梳理市场线索/ })); });
  expect(screen.queryByRole('dialog', { name: 'AI 助手' })).not.toBeInTheDocument();
  expect(screen.getByRole('textbox', { name: '向 AI 投研助手提问' })).toHaveValue('帮我梳理今天市场的重要变化与需要继续核实的线索。');
  expect((apiPost as jest.Mock).mock.calls.filter(([path]) => /ai|research/.test(path))).toEqual(writesBefore);
});


it.each(['error', 'eof'])('does not silently restart an executed quick question after %s', async failure => {
  await mountTerminal();
  await act(async () => { fireEvent.click(within(screen.getByRole('complementary', { name: '主导航' })).getByRole('button', { name: /AI 对话/ })); });
  await act(async () => { fireEvent.change(screen.getByRole('textbox', { name: '向 AI 投研助手提问' }), { target: { value: '微软最近有哪些重要变化和风险？请简洁回答。' } }); });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '发送问题' })); });
  const callbacks = (runToolResearchStream as jest.Mock).mock.calls[0][1];
  await act(async () => {
    if (failure === 'error') callbacks.onError('本轮研究等待超时', 408);
    callbacks.onDone();
  });
  expect(runToolResearch).not.toHaveBeenCalled();
  expect(screen.getByRole('textbox', { name: '向 AI 投研助手提问' })).toHaveValue('微软最近有哪些重要变化和风险？请简洁回答。');
  expect(screen.getByRole('button', { name: '发送问题' })).toBeEnabled();
});

it('exposes both successful and unavailable source receipts while the answer is generating', async () => {
  await mountTerminal();
  await act(async () => { fireEvent.click(within(screen.getByRole('complementary', { name: '主导航' })).getByRole('button', { name: /AI 对话/ })); });
  await act(async () => { fireEvent.change(screen.getByRole('textbox', { name: '向 AI 投研助手提问' }), { target: { value: '核对微软最近变化' } }); });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '发送问题' })); });
  const callbacks = (runToolResearchStream as jest.Mock).mock.calls[0][1];
  await act(async () => {
    callbacks.onTool({ tool: 'get_stock_news' }, 'start');
    callbacks.onTool({ tool: 'search_our_content' }, 'start');
    callbacks.onTool({ tool: 'get_stock_news', ok: false, summary: '该新闻源本轮不可用' }, 'result');
    callbacks.onTool({ tool: 'search_our_content', ok: true, summary: '找到两条相关站内资料' }, 'result');
  });
  expect(screen.getByText('执行过程 · 2/2 项已返回')).toBeInTheDocument();
  expect(screen.getByText('未能取得资料')).toBeInTheDocument();
  expect(screen.getByText('该新闻源本轮不可用')).toBeInTheDocument();
  expect(screen.getByText('找到两条相关站内资料')).toBeInTheDocument();
});
