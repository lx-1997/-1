import React from 'react';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import FinancialTerminal from '../../components/FinancialTerminal';
import { AuthProvider } from '../../context/AuthContext';
import { apiGet, apiPost } from '../../services/apiClient';
import { generateResearchDeepDraftSmart } from '../../services/researchService';
import { authenticateSession, getAuthSnapshot, invalidateAuthSession } from '../../state/authSession';
import { renderAiInterpretationImages } from '../../utils/aiInterpretationExport';
import type { AuthUser } from '../../services/authService';

jest.mock('../../services/apiClient', () => ({
  apiGet: jest.fn(), apiPost: jest.fn(), apiDelete: jest.fn(),
  getApiBaseUrls: () => ['https://example.test'], getActiveApiBaseUrl: () => 'https://example.test',
  getAuthenticationApiBaseUrl: () => 'https://example.test', sessionOwnsRequest: () => true,
  formatErrorMessage: (error: any) => error?.message || 'Request failed', DF_WEB_TOKEN: 'test-web',
}));
jest.mock('../../services/eventService', () => ({
  ...jest.requireActual('../../services/eventService'), createRealtimeMessageStream: jest.fn(() => ({ close: jest.fn() })),
}));
jest.mock('../../services/researchService', () => ({ ...jest.requireActual('../../services/researchService'), generateResearchDeepDraftSmart: jest.fn() }));
jest.mock('../../utils/aiInterpretationExport', () => ({ ...jest.requireActual('../../utils/aiInterpretationExport'), renderAiInterpretationImages: jest.fn() }));

const research = generateResearchDeepDraftSmart as jest.Mock;
const report = { id: 'copy-report', file_id: 'copy-report', filename: 'copy.pdf', title: '完整解读复制回归', date: '2026-10-12', instruments: [], preview_url: '' };
const article = { id: 'copy-news', title: '新闻完整文字复制', topic: '文章', severity: 'info', created_at: '2026-10-12T09:00:00Z', content: '原始新闻正文', url: '', tags: [], metadata: {} };
const draft = {
  title: report.title,
  sections: Array.from({ length: 17 }, (_, i) => ({ title: `第${i + 1}章`, paragraphs: ['正文'.repeat(1100) + `正文尾部${i + 1}`] })),
  tables: [{ title: '完整表格', columns: ['指标'], rows: Array.from({ length: 29 }, (_, i) => [`表格第${i + 1}行`]) }],
  watchlist: [{ title: '最后一个跟踪项', trigger: '最终验证条件' }], risks: ['完整风险尾部'],
};
const account = (id: string): AuthUser => ({ id, username: id, email: null, role: 'viewer', is_active: true, created_at: '', membership: { tier: 'premium', expires_at: '2027-01-01', days_left: 90 } });
const originalClipboard = navigator.clipboard;
const originalSecure = window.isSecureContext;
const originalScroll = HTMLElement.prototype.scrollIntoView;
const originalExec = document.execCommand;
let writeText: jest.Mock;

beforeEach(() => {
  jest.useFakeTimers(); jest.clearAllMocks(); invalidateAuthSession(); localStorage.clear();
  localStorage.setItem('df_onboarded_v1', '1'); localStorage.setItem('bbt_acct_hint_v1', '1');
  window.history.replaceState({}, '', '/?tab=research');
  HTMLElement.prototype.scrollIntoView = jest.fn();
  writeText = jest.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
  Object.defineProperty(window, 'isSecureContext', { configurable: true, value: true });
  (apiGet as jest.Mock).mockImplementation(async (path: string, options?: any) => {
    if (path === '/api/auth/me') return getAuthSnapshot().account;
    if (path === '/api/research/wire') return { items: [report] };
    if (path === '/api/realtime/messages') return { messages: options?.params?.topic === '文章' ? [article] : [] };
    return { items: [], messages: [], quotes: [], categories: [], memories: [], markets: [], themes: [], symbols: [], names: {}, empty: false, has_more: false, available: {}, code: '', unread: 0, exists: false };
  });
  (apiPost as jest.Mock).mockResolvedValue({}); research.mockResolvedValue(draft);
  authenticateSession(account('A'), 'test-a', 'https://example.test');
});
afterEach(() => {
  cleanup(); invalidateAuthSession(); jest.clearAllTimers(); jest.useRealTimers();
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: originalClipboard });
  Object.defineProperty(window, 'isSecureContext', { configurable: true, value: originalSecure });
  HTMLElement.prototype.scrollIntoView = originalScroll; document.execCommand = originalExec;
});
async function openReport() {
  await act(async () => { render(<AuthProvider><FinancialTerminal /></AuthProvider>); });
  await act(async () => { fireEvent.click(screen.getAllByRole('button', { name: 'AI解析' })[0]); });
  if (!screen.queryByRole('dialog', { name: 'AI 深度稿' })) {
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /解读完成.*完整解读复制回归/ })); });
  }
  return screen.getByRole('dialog', { name: 'AI 深度稿' });
}

it('copies all report chapters, long-paragraph tails, table rows and tracking from the real reader', async () => {
  const reader = await openReport();
  await act(async () => { fireEvent.click(within(reader).getByRole('button', { name: '复制文字' })); });
  const copied = writeText.mock.calls[0][0];
  expect(copied).toContain('第17章'); expect(copied).toContain('正文尾部17');
  expect(copied).toContain('表格第29行'); expect(copied).toContain('最终验证条件');
  expect(copied).toContain('完整风险尾部'); expect(research).toHaveBeenCalledTimes(1);
});

it('uses that same full reader content for image generation and clears a pending export on account change', async () => {
  let finish!: (value: unknown[]) => void;
  (renderAiInterpretationImages as jest.Mock).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  const reader = await openReport();
  await act(async () => { fireEvent.click(within(reader).getByRole('button', { name: '复制完整图片' })); });
  expect((renderAiInterpretationImages as jest.Mock).mock.calls[0][0]).toContain('表格第29行');
  expect(screen.getByRole('dialog', { name: 'AI 解读完整图片' })).toBeInTheDocument();
  await act(async () => { authenticateSession(account('B'), 'test-b', 'https://example.test'); });
  await act(async () => { finish([]); });
  expect(screen.queryByRole('dialog', { name: 'AI 解读完整图片' })).not.toBeInTheDocument();
  expect(writeText).not.toHaveBeenCalled(); expect(research).toHaveBeenCalledTimes(1);
});

it('copies news detail through the fallback and does not claim success when both clipboard paths fail', async () => {
  (apiPost as jest.Mock).mockImplementation(async (path: string) => path === '/api/news/ai-analyze'
    ? { title: article.title, one_liner: '新闻结论', summary: '新闻详情尾部', logic_lines: [{ title: '完整逻辑', evidence: '明确事实' }], confidence: 0.8 } : {});
  await act(async () => { render(<AuthProvider><FinancialTerminal /></AuthProvider>); });
  await act(async () => { fireEvent.click(within(screen.getByRole('complementary', { name: '主导航' })).getByRole('button', { name: /深度文章/ })); });
  const row = screen.getByRole('article', { name: `资讯：${article.title}` });
  await act(async () => { fireEvent.click(within(row).getByRole('button', { name: 'AI 解读' })); });
  writeText.mockRejectedValue(new Error('clipboard denied')); document.execCommand = jest.fn().mockReturnValue(false);
  const reader = screen.getByRole('dialog', { name: 'AI 解读' });
  await act(async () => { fireEvent.click(within(reader).getByRole('button', { name: '复制文字' })); });
  expect(writeText.mock.calls[0][0]).toContain('新闻详情尾部');
  expect(document.execCommand).toHaveBeenCalledWith('copy');
  expect((within(reader).getByLabelText('手动复制 AI 解读文字') as HTMLTextAreaElement).value).toContain('明确事实');
  expect(within(reader).queryByText('文字已复制')).not.toBeInTheDocument();
});

it('closes only the image preview when Escape is pressed above the real reader', async () => {
  (renderAiInterpretationImages as jest.Mock).mockImplementationOnce(() => new Promise(() => {}));
  const reader = await openReport();
  await act(async () => { fireEvent.click(within(reader).getByRole('button', { name: '复制完整图片' })); });
  const preview = screen.getByRole('dialog', { name: 'AI 解读完整图片' });
  await act(async () => { fireEvent.keyDown(preview, { key: 'Escape' }); });
  expect(screen.queryByRole('dialog', { name: 'AI 解读完整图片' })).not.toBeInTheDocument();
  expect(screen.getByRole('dialog', { name: 'AI 深度稿' })).toBeInTheDocument();
});

it('does not fall back to copying a closed reader when the legacy footer clipboard request rejects late', async () => {
  let reject!: (error: Error) => void;
  writeText.mockImplementationOnce(() => new Promise((_, fail) => { reject = fail; }));
  document.execCommand = jest.fn().mockReturnValue(true);
  const reader = await openReport();
  await act(async () => { fireEvent.click(within(reader).getByRole('button', { name: /复制为文字/ })); });
  await act(async () => { fireEvent.click(within(reader).getAllByRole('button', { name: '关闭' })[0]); });
  await act(async () => { reject(new Error('clipboard denied late')); });
  expect(document.execCommand).not.toHaveBeenCalled();
  expect(screen.queryByRole('dialog', { name: 'AI 深度稿' })).not.toBeInTheDocument();
});
