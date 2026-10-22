import React from 'react';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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
