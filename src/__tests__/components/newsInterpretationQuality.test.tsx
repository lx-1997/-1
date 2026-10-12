import React from 'react';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import FinancialTerminal from '../../components/FinancialTerminal';
import { AuthProvider } from '../../context/AuthContext';
import { apiGet, apiPost } from '../../services/apiClient';
import type { AuthUser } from '../../services/authService';
import { authenticateSession, getAuthSnapshot, invalidateAuthSession } from '../../state/authSession';
import { accountStorageKey } from '../../utils/accountStorage';
import { newsInterpretationCacheKey } from '../../utils/aiInterpretCache';

// Exercise the actual article entry, reader, retry controls and account-scoped
// cache. Only remote requests and the live SSE connection are replaced.
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

const article = {
  id: 'news-quality-fixture', title: '部长制裁新闻解读集成回归',
  topic: '文章', severity: 'info', created_at: '2026-10-12T09:00:00Z',
  content: '中方宣布对相关部长采取制裁。措施包括禁止入境，以及冻结境内财产。',
  url: '', source_name: 'fixture', tags: [], metadata: {},
};
const good = {
  title: article.title, one_liner: '本次制裁明确包含入境限制和财产冻结两项措施。',
  summary: '措施作用于被列入制裁名单的部长，原文没有量化经济影响。',
  logic_lines: [
    { title: '入境限制', evidence: '禁止被制裁部长入境。' },
    { title: '财产冻结', evidence: '冻结被制裁部长在境内的财产。' },
  ],
  confidence: 0.8, source_note: '已读取原文全文',
};
const bad = {
  title: article.title, one_liner: '中方宣布对相关部长实施制裁。',
  summary: '具体措施和影响尚不明确。', confidence: 0, source_note: '已读取原文全文',
  logic_lines: [{
    title: '信息边界',
    evidence: '原文未形成可独立核对的分项事实。',
    chain: '原文未明确说明事实到影响的传导。',
    impact: '原文未明确提及受益或受压对象。',
    watch: '原文未给出验证条件。',
  }],
};
const account = (id: string): AuthUser => ({
  id, username: id, email: null, role: 'viewer', is_active: true, created_at: '',
  membership: { tier: 'premium', expires_at: '2027-01-01', days_left: 90 },
});
const storeKey = (id: string) => accountStorageKey('df_ai_interpret_cache_v1', id);
const cacheKey = newsInterpretationCacheKey(article.id);
const cached = (id = 'A') => JSON.parse(localStorage.getItem(storeKey(id)) || '{}');
const newsCalls = () => (apiPost as jest.Mock).mock.calls.filter(([path]) => path === '/api/news/ai-analyze');
const originalScrollIntoView = HTMLElement.prototype.scrollIntoView;

beforeAll(() => { HTMLElement.prototype.scrollIntoView = jest.fn(); });
afterAll(() => { HTMLElement.prototype.scrollIntoView = originalScrollIntoView; });
beforeEach(() => {
  jest.useFakeTimers();
  jest.clearAllMocks();
  invalidateAuthSession();
  localStorage.clear();
  localStorage.setItem('df_onboarded_v1', '1');
  localStorage.setItem('bbt_acct_hint_v1', '1');
  window.history.replaceState({}, '', '/?tab=articles');
  (apiGet as jest.Mock).mockImplementation(async (path: string, options?: { params?: { topic?: string } }) => {
    if (path === '/api/auth/me') return getAuthSnapshot().account;
    if (path === '/api/realtime/messages') return { messages: options?.params?.topic === '文章' ? [article] : [] };
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

function serveNews(...responses: unknown[]) {
  const queue = [...responses];
  (apiPost as jest.Mock).mockImplementation((path: string) => {
    if (path !== '/api/news/ai-analyze') return Promise.resolve({});
    if (!queue.length) return Promise.reject(new Error('Unexpected news request'));
    return Promise.resolve(queue.shift());
  });
}

async function mountArticles() {
  await act(async () => { render(<AuthProvider><FinancialTerminal /></AuthProvider>); });
  // The terminal currently restores the article tab from its content preference;
  // use the visible navigation entry as a user would, even with tab=articles.
  await act(async () => {
    fireEvent.click(within(screen.getByRole('complementary', { name: '主导航' }))
      .getByRole('button', { name: /深度文章/ }));
  });
  await act(async () => { jest.advanceTimersByTime(0); });
  expect(apiGet).toHaveBeenCalledWith('/api/realtime/messages', expect.objectContaining({
    params: expect.objectContaining({ topic: '文章' }),
  }));
  expect(screen.getByRole('article', { name: `资讯：${article.title}` })).toBeInTheDocument();
}

async function clickArticleAi() {
  const row = screen.getByRole('article', { name: `资讯：${article.title}` });
  await act(async () => { fireEvent.click(within(row).getByRole('button', { name: 'AI 解读' })); });
}

function reader() { return screen.getByRole('dialog', { name: 'AI 解读' }); }
async function clickReaderButton(name: string) {
  await act(async () => { fireEvent.click(within(reader()).getByRole('button', { name })); });
}

it('turns an HTTP 200 boundary card into a retryable error and renders the corrected facts on retry', async () => {
  serveNews(bad, good);
  await mountArticles();
  await clickArticleAi();
  expect(newsCalls()).toHaveLength(1);
  expect(within(reader()).getByRole('button', { name: '↻ 重试' })).toBeInTheDocument();
  expect(reader()).not.toHaveTextContent('置信 0%');
  expect(reader()).not.toHaveTextContent('信息边界');
  expect(reader()).not.toHaveTextContent('原文未形成可独立核对');
  expect(reader()).not.toHaveTextContent(bad.summary);
  expect(cached()[cacheKey]).toBeUndefined();

  await clickReaderButton('↻ 重试');
  expect(newsCalls()).toHaveLength(2);
  expect(within(reader()).getByText(good.logic_lines[0].evidence)).toBeInTheDocument();
  expect(within(reader()).getByText(good.logic_lines[1].evidence)).toBeInTheDocument();
  expect(within(reader()).queryByRole('button', { name: '↻ 重试' })).not.toBeInTheDocument();
  expect(cached()[cacheKey]?.result).toEqual(good);
});

it('regenerates a completed interpretation with refresh=true instead of reopening the memory cache', async () => {
  const regenerated = { ...good, one_liner: '重新生成后仍保留两项可核对的制裁措施。' };
  serveNews(good, regenerated);
  await mountArticles();
  await clickArticleAi();
  expect(cached()[cacheKey]?.result).toEqual(good);
  await clickReaderButton('↻ 重新解读');
  expect(newsCalls()).toHaveLength(2);
  expect(newsCalls()[1][1]).toEqual(expect.objectContaining({ message_id: article.id, refresh: true }));
  expect(within(reader()).getByText(regenerated.one_liner)).toBeInTheDocument();
  expect(cached()[cacheKey]?.result).toEqual(regenerated);
});

it('ignores a persisted result from the old unversioned news key and sends a new request', async () => {
  const old = { ...good, one_liner: '旧版本缓存不能挡住新的新闻解读。' };
  localStorage.setItem(storeKey('A'), JSON.stringify({
    [`news:${article.id}`]: { result: old, savedAt: Date.now() },
  }));
  serveNews(good);
  await mountArticles();
  await clickArticleAi();
  expect(newsCalls()).toHaveLength(1);
  expect(newsCalls()[0][1]).toEqual(expect.objectContaining({ message_id: article.id, title: article.title }));
  expect(reader()).not.toHaveTextContent(old.one_liner);
  expect(within(reader()).getByText(good.logic_lines[0].evidence)).toBeInTheDocument();
  expect(cached()[cacheKey]?.result).toEqual(good);
});

it('does not replay account A\'s delayed response into account B\'s same article or cache', async () => {
  let resolveA!: (value: unknown) => void;
  let resolveB!: (value: unknown) => void;
  const waitingA = new Promise(resolve => { resolveA = resolve; });
  const waitingB = new Promise(resolve => { resolveB = resolve; });
  const answerA = { ...good, one_liner: '账号 A 的迟到解读。' };
  const answerB = { ...good, one_liner: '账号 B 的当前解读。' };
  serveNews(waitingA, waitingB);
  await mountArticles();
  await clickArticleAi();
  expect(newsCalls()).toHaveLength(1);
  await act(async () => { authenticateSession(account('B'), 'test-b', 'https://example.test'); });
  await act(async () => { jest.advanceTimersByTime(0); });
  await clickArticleAi();
  expect(newsCalls()).toHaveLength(2);

  await act(async () => { resolveA(answerA); });
  expect(screen.queryByText(answerA.one_liner)).not.toBeInTheDocument();
  expect(cached('A')[cacheKey]).toBeUndefined();
  expect(cached('B')[cacheKey]).toBeUndefined();
  await act(async () => { resolveB(answerB); });
  expect(within(reader()).getByText(answerB.one_liner)).toBeInTheDocument();
  expect(reader()).not.toHaveTextContent(answerA.one_liner);
  expect(cached('B')[cacheKey]?.result).toEqual(answerB);
  expect(cached('A')[cacheKey]).toBeUndefined();
});
